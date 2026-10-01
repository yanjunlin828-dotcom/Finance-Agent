"""版本化词表驱动的页级检索和证据窗口生成。"""

from __future__ import annotations

import hashlib
from typing import Any

from finresearch.contracts import DocumentPage, EvidenceCandidate, PageScore, RetrievalResult


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class KeywordEvidenceRetriever:
    """在单份文档内用可解释规则定位页面和证据窗口。"""

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self._validate_config()

    def _validate_config(self) -> None:
        rule_ids = [rule["rule_id"] for rule in self.config.get("rules", [])]
        if not rule_ids or len(rule_ids) != len(set(rule_ids)):
            raise ValueError("检索规则不能为空且rule_id必须唯一")
        if int(self.config.get("top_k_pages", 0)) <= 0:
            raise ValueError("top_k_pages必须为正数")

    def _select_rule(self, question: str) -> dict[str, Any] | None:
        eligible: list[tuple[int, int, dict[str, Any]]] = []
        for rule in self.config["rules"]:
            trigger_hits = sum(trigger in question for trigger in rule["triggers"])
            required_context = rule.get("must_also_contain_any", [])
            context_ok = not required_context or any(term in question for term in required_context)
            if trigger_hits and context_ok:
                eligible.append((int(rule.get("priority", 0)), trigger_hits, rule))
        if not eligible:
            return None
        eligible.sort(key=lambda item: (item[0], item[1], item[2]["rule_id"]), reverse=True)
        return eligible[0][2]

    def retrieve(self, request_id: str, question: str, pages: list[DocumentPage]) -> RetrievalResult:
        if not pages:
            raise ValueError("pages不能为空")
        document_ids = {page.document_id for page in pages}
        if len(document_ids) != 1:
            raise ValueError("S1检索一次只允许一份文档")
        rule = self._select_rule(question)
        if rule is None:
            return RetrievalResult(
                request_id=request_id,
                question=question,
                document_id=next(iter(document_ids)),
                rule_id=None,
                required_terms=[],
                optional_terms=[],
                context_terms=[],
                page_scores=[],
                evidence_candidates=[],
                status="NO_MATCHING_RULE",
            )

        page_scores = [self._score_page(question, page, rule) for page in pages]
        page_scores = self._apply_adjacency_boost(page_scores, pages, rule)
        page_scores.sort(key=lambda item: (-item.score, item.pdf_page))
        positive_scores = [item for item in page_scores if item.score > 0]
        top_scores = positive_scores[: int(self.config["top_k_pages"])]
        page_by_id = {page.page_id: page for page in pages}
        candidates_by_page: list[list[EvidenceCandidate]] = []
        for page_score in top_scores:
            page = page_by_id[page_score.page_id]
            page_candidates = self._windows_for_page(page, page_score, rule)
            page_candidates.sort(key=lambda item: (-item.retrieval_score, item.line_start))
            candidates_by_page.append(page_candidates)
        max_windows = int(self.config["max_evidence_windows"])
        # 先给每个Top页面一个窗口，再按得分补足，避免一个高频附注页耗尽全部窗口。
        candidates: list[EvidenceCandidate] = [items[0] for items in candidates_by_page if items]
        remaining = [item for items in candidates_by_page for item in items[1:]]
        remaining.sort(key=lambda item: (-item.retrieval_score, item.pdf_page, item.line_start))
        candidates.extend(remaining)
        ranked = [
            candidate.model_copy(update={"retrieval_rank": rank})
            for rank, candidate in enumerate(candidates[:max_windows], start=1)
        ]
        status = "FOUND" if ranked else "NO_EVIDENCE"
        return RetrievalResult(
            request_id=request_id,
            question=question,
            document_id=next(iter(document_ids)),
            rule_id=rule["rule_id"],
            required_terms=rule["required_terms"],
            optional_terms=rule["optional_terms"],
            context_terms=rule["context_terms"],
            page_scores=page_scores,
            evidence_candidates=ranked,
            status=status,
        )

    def _score_page(self, question: str, page: DocumentPage, rule: dict[str, Any]) -> PageScore:
        text = page.normalized_text
        weights = self.config["score_weights"]
        maximum_occurrences = int(self.config["maximum_occurrences_per_term"])
        components: dict[str, float] = {}
        matched_terms: list[str] = []
        for category, terms in (
            ("required", rule["required_terms"]),
            ("optional", rule["optional_terms"]),
            ("context", rule["context_terms"]),
            ("forbidden", rule["forbidden_terms"]),
        ):
            category_score = 0.0
            for term in terms:
                category_cap = maximum_occurrences if category == "required" else 1
                count = min(text.count(term), category_cap)
                if count:
                    matched_terms.append(term)
                    category_score += count * float(weights[category])
            components[category] = category_score
        question_tokens = [token for token in question.replace("？", "").replace("?", "").split() if len(token) >= 2]
        exact_score = sum(float(weights["question_exact"]) for token in question_tokens if token in text)
        components["question_exact"] = exact_score
        score = sum(components.values())
        return PageScore(
            page_id=page.page_id,
            document_id=page.document_id,
            pdf_page=page.pdf_page,
            score=score,
            matched_terms=sorted(set(matched_terms)),
            score_components=components,
        )

    def _apply_adjacency_boost(
        self, page_scores: list[PageScore], pages: list[DocumentPage], rule: dict[str, Any]
    ) -> list[PageScore]:
        bridge_terms = rule.get("adjacency_bridge_terms", [])
        if not bridge_terms:
            return page_scores
        pages_by_number = {page.pdf_page: page for page in pages}
        boosts: dict[int, float] = {}
        for page in pages:
            if not any(term in page.normalized_text for term in bridge_terms):
                continue
            for neighbor_number in (page.pdf_page - 1, page.pdf_page + 1):
                neighbor = pages_by_number.get(neighbor_number)
                if neighbor and any(term in neighbor.normalized_text for term in rule["required_terms"]):
                    boosts[page.pdf_page] = boosts.get(page.pdf_page, 0.0) + 50.0
                    boosts[neighbor_number] = boosts.get(neighbor_number, 0.0) + 50.0
        boosted: list[PageScore] = []
        for item in page_scores:
            boost = boosts.get(item.pdf_page, 0.0)
            components = dict(item.score_components)
            components["adjacency_bridge"] = boost
            boosted.append(item.model_copy(update={"score": item.score + boost, "score_components": components}))
        return boosted

    def _windows_for_page(
        self, page: DocumentPage, page_score: PageScore, rule: dict[str, Any]
    ) -> list[EvidenceCandidate]:
        lines = page.normalized_text.splitlines()
        searchable_terms = list(dict.fromkeys(rule["required_terms"] + rule["optional_terms"] + rule["context_terms"]))
        hit_indices = [
            index for index, line in enumerate(lines) if any(term in line for term in searchable_terms)
        ]
        if not hit_indices:
            return []
        radius = int(self.config["window_radius_lines"])
        spans = [(max(0, index - radius), min(len(lines) - 1, index + radius)) for index in hit_indices]
        merged: list[list[int]] = []
        for start, end in spans:
            if merged and start <= merged[-1][1] + 1:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])
        windows: list[EvidenceCandidate] = []
        maximum_characters = int(self.config["maximum_window_characters"])
        complete_spans: list[tuple[int, int]] = []
        for start, end in merged:
            cursor = start
            while cursor <= end:
                finish = cursor
                # Never truncate a financial row or claim a locator we did not read.
                while finish < end and len("\n".join(lines[cursor:finish + 2])) <= maximum_characters:
                    finish += 1
                if len(lines[cursor]) > maximum_characters:
                    raise ValueError("单行证据超过长度上限，不能安全截断")
                complete_spans.append((cursor, finish))
                cursor = finish + 1
        for start, end in complete_spans:
            text = "\n".join(lines[start : end + 1])
            matched = sorted({term for term in searchable_terms if term in text})
            if not matched:
                continue
            evidence_sha = _sha256_text(text)
            evidence_id = f"{page.document_id}:p{page.pdf_page:04d}:l{start + 1:03d}-{end + 1:03d}:{evidence_sha[:16]}"
            windows.append(
                EvidenceCandidate(
                    evidence_id=evidence_id,
                    document_id=page.document_id,
                    page_id=page.page_id,
                    company_id=page.company_id,
                    pdf_page=page.pdf_page,
                    line_start=start + 1,
                    line_end=end + 1,
                    text=text,
                    matched_terms=matched,
                    retrieval_score=page_score.score + len(matched),
                    retrieval_rank=1,
                    evidence_sha256=evidence_sha,
                )
            )
        return windows
