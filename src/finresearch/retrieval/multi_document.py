"""Four comparable retrieval paths over one filtered, verified corpus."""
from __future__ import annotations
from copy import deepcopy
import hashlib
import math
import re
import time
from typing import TYPE_CHECKING

import jieba
from rank_bm25 import BM25Okapi

from finresearch.contracts.retrieval import MultiDocumentResult, RetrievalChunk, RetrievalHit, RetrievalQuery
from .corpus import ResearchCorpus
from .keyword import KeywordEvidenceRetriever
if TYPE_CHECKING:
    from .vector_index import VectorIndex


def reciprocal_rank_fusion(rankings: dict[str, list[tuple[str, float]]], k: int = 60) -> tuple[list[tuple[str, float]], dict[str, dict[str, int]]]:
    """Fuse ranks only; duplicate IDs within one channel contribute once."""
    if k <= 0:
        raise ValueError("RRF k必须为正")
    scores: dict[str, float] = {}
    origins: dict[str, dict[str, int]] = {}
    for name, rows in rankings.items():
        seen = set()
        for ordinal, (chunk_id, _) in enumerate(rows, 1):
            if chunk_id in seen:
                continue
            seen.add(chunk_id)
            scores[chunk_id] = scores.get(chunk_id, 0) + 1 / (k + ordinal)
            origins.setdefault(chunk_id, {})[name] = ordinal
    return sorted(scores.items(), key=lambda row: (-row[1], row[0])), origins


class MultiDocumentRetriever:
    def __init__(self, corpus: ResearchCorpus, chunks: list[RetrievalChunk], config: dict, keyword_config: dict, vector_index: VectorIndex | None = None) -> None:
        self.corpus = corpus
        self.chunks = chunks
        self.by_id = {c.chunk_id: c for c in chunks}
        if len(self.by_id) != len(chunks):
            raise ValueError("检索块ID重复")
        self.config = config
        self.keyword_config = keyword_config
        self.vector_index = vector_index
        self.tokenizer = jieba.Tokenizer()
        for term in config["custom_terms"]:
            self.tokenizer.add_word(term)
        self.chunk_statement: dict[str, str | None] = {}
        heading = re.compile(r"(?:^|\n)(?:\d+[、. ]*)?((?:合并|母公司)(?:资产负债表|利润表|现金流量表))(?:\n|$)")
        # Derive table provenance from standalone headings, never from labels
        # or evaluation page numbers. A heading may begin near a page's bottom.
        for document_id in sorted(corpus.documents):
            current = None
            for page in sorted((p for p in corpus.pages.values() if p.document_id == document_id), key=lambda p: p.pdf_page):
                headings = list(heading.finditer(page.normalized_text))
                for c in (c for c in chunks if c.parent_page_id == page.page_id):
                    preceding = [h for h in headings if h.start(1) < c.character_end]
                    self.chunk_statement[c.chunk_id] = preceding[-1].group(1) if preceding else current
                if headings:
                    current = headings[-1].group(1)
        self.tokens = {c.chunk_id: self._tokens(c.text) for c in chunks}
        for chunk in chunks:
            page = corpus.pages[chunk.parent_page_id]
            record = corpus.documents[page.document_id]
            if (chunk.document_id != page.document_id or chunk.company_id != record.ts_code
                or chunk.reporting_year != int(record.reporting_period) or chunk.published_on != record.published_on
                or chunk.source_sha256 != record.sha256 or chunk.pdf_page != page.pdf_page
                or chunk.document_type != record.document_type or chunk.document_status != "READY"
                or chunk.corpus_snapshot_id != corpus.snapshot_id):
                raise ValueError("检索块元数据与权威页及文档不一致")
            if page.normalized_text[chunk.character_start:chunk.character_end] != chunk.text:
                raise ValueError("检索块跨度与父页不一致")
            if (not 0 <= chunk.character_start < chunk.character_end <= len(page.normalized_text)
                or hashlib.sha256(chunk.text.encode()).hexdigest() != chunk.text_sha256
                or chunk.line_start != page.normalized_text.count("\n", 0, chunk.character_start) + 1
                or chunk.line_end != page.normalized_text.count("\n", 0, chunk.character_end - 1) + 1
                or chunk.chunker_version != config["chunker_version"]):
                raise ValueError("检索块哈希、行范围或版本与父页不一致")
        self._bm25_cache: dict[tuple[str, ...], tuple[list[RetrievalChunk], BM25Okapi]] = {}

    def _tokens(self, text: str) -> list[str]:
        if self.config.get("join_wrapped_metric_labels"):
            for phrase in self.config["custom_terms"]:
                pattern = r"\s*".join(re.escape(char) for char in phrase)
                text = re.sub(pattern, lambda _: phrase, text)
        return [token for token in self.tokenizer.cut(text.lower()) if re.search(r"[\w\u4e00-\u9fff]", token)]

    def _sparse(self, query: RetrievalQuery, eligible: list[str]) -> list[tuple[str, float]]:
        requested_table = next((name for name in ("合并资产负债表", "合并现金流量表", "合并利润表") if name in query.question), None)
        table_filter = requested_table if self.config.get("statement_scoped_sparse") else None
        key = (*eligible, table_filter)
        if key not in self._bm25_cache:
            subset = [c for c in self.chunks if c.document_id in eligible and (table_filter is None or self.chunk_statement[c.chunk_id] == table_filter)]
            if not subset:
                return []
            self._bm25_cache[key] = (subset, BM25Okapi([self.tokens[c.chunk_id] or ["__empty__"] for c in subset]))
        subset, scorer = self._bm25_cache[key]
        expanded = query.question
        for term, alternatives in self.config["query_expansions"].items():
            if term in query.question:
                expanded += " " + " ".join(alternatives)
        scores = scorer.get_scores(self._tokens(expanded))
        return sorted([(c.chunk_id, float(score)) for c, score in zip(subset, scores) if score > 0 and math.isfinite(score)], key=lambda row: (-row[1], row[0]))[:self.config["candidate_k"]]

    def _keyword(self, query: RetrievalQuery, eligible: list[str]) -> list[tuple[str, float]]:
        config = deepcopy(self.keyword_config)
        config["top_k_pages"] = self.config["candidate_k"]
        for rule in config["rules"]:
            rule["context_terms"] = [str(query.reporting_year) if t == "2024" else t for t in rule["context_terms"]]
        rows = []
        for document_id in eligible:
            pages = [p for p in self.corpus.pages.values() if p.document_id == document_id]
            result = KeywordEvidenceRetriever(config).retrieve("s3-query", query.question, pages)
            for score in result.page_scores[:self.config["candidate_k"]]:
                if score.score > 0:
                    chunk = next(c for c in self.chunks if c.parent_page_id == score.page_id)
                    rows.append((chunk.chunk_id, score.score))
        return sorted(rows, key=lambda row: (-row[1], row[0]))[:self.config["candidate_k"]]

    def retrieve(self, query: RetrievalQuery, method: str) -> MultiDocumentResult:
        """Filter first, rank child chunks, then return TopK unique parent pages.

        Financial pages lacking a unit/header get the preceding page as labeled
        context. Adjacent pages never affect ranking metrics and pass the same
        source/date filter. Evidence is canonical corpus text, not index payload.
        """
        started = time.perf_counter()
        if method not in {"keyword", "bm25", "vector", "hybrid"}:
            raise ValueError("未知检索方法")
        eligible = self.corpus.eligible_documents(query)
        rows = []
        origins: dict[str, dict[str, int]] = {}
        if eligible:
            if method == "keyword":
                rows = self._keyword(query, eligible)
            elif method == "bm25":
                rows = self._sparse(query, eligible)
            else:
                if self.vector_index is None:
                    raise ValueError("向量索引尚未接入")
                vectors = self.vector_index.search(query, set(eligible), self.config["candidate_k"])
                if method == "vector":
                    rows = vectors
                else:
                    rows, origins = reciprocal_rank_fusion({"bm25": self._sparse(query, eligible), "vector": vectors}, self.config["rrf_k"])
        hits = []
        seen_pages = set()
        for ordinal, (chunk_id, score) in enumerate(rows, 1):
            chunk = self.by_id[chunk_id]
            if chunk.document_id not in eligible:
                raise ValueError("结果越过统一过滤")
            if chunk.parent_page_id in seen_pages:
                continue
            seen_pages.add(chunk.parent_page_id)
            page = self.corpus.pages[chunk.parent_page_id]
            adjacent = []
            # A preceding page can hold a split table heading. Do not infer scope
            # from this heuristic; financial extraction still verifies its table.
            if "单位:" not in page.normalized_text and any(t in page.normalized_text for t in ("应收账款", "经营活动产生的现金流", "量净额")):
                neighbors = [p for p in self.corpus.pages.values() if p.document_id == chunk.document_id and p.pdf_page == chunk.pdf_page - 1]
                adjacent = [{"page_id": p.page_id, "document_id": p.document_id, "pdf_page": p.pdf_page, "text": p.normalized_text} for p in neighbors]
            hits.append(RetrievalHit(rank=len(hits) + 1, score=score, chunk=chunk, parent_text=page.normalized_text,
                                     adjacent_context=adjacent, contributing_ranks=origins.get(chunk_id, {method: ordinal}),
                                     matched_terms=sorted(set(self._tokens(query.question)) & set(self.tokens[chunk_id]))))
            if len(hits) == query.top_k:
                break
        return MultiDocumentResult(query=query, method=method, status="FOUND" if hits else "NO_EVIDENCE" if eligible else "NO_ELIGIBLE_DOCUMENT",
                                   eligible_documents=eligible, candidate_chunk_ids=[row[0] for row in rows], hits=hits,
                                   elapsed_ms=(time.perf_counter() - started) * 1000)
