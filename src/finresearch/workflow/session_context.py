"""S6 lossless, scoped research capsules and constrained follow-up answers."""
from __future__ import annotations

import hashlib
import json

from finresearch.contracts import EvidenceCandidate, MetricObservation, stable_sha256
from finresearch.contracts.research import ResearchContext, ResearchClaim, DisclosureBatch, HypothesisBatch, ResearchGap, Phenomenon


class ContextTooLarge(ValueError):
    """A complete source window cannot be silently shortened to fit a prompt."""


def scope_key(context: ResearchContext) -> str:
    """Identity for reuse; excludes task ID but includes all research boundaries."""
    payload = context.model_dump(mode="json", exclude={"run_id"})
    payload["company_ids"] = sorted(payload["company_ids"])
    return stable_sha256(payload)


def eligible_observations(rows: list[dict], context: ResearchContext) -> list[dict]:
    """Filter full records by company, annual period and actual disclosure date."""
    selected = []
    for row in rows:
        item = MetricObservation.model_validate(row)
        if (item.company_id in context.company_ids and item.fiscal_year in context.fiscal_years
                and item.document_published_on <= context.as_of_date):
            selected.append(item.model_dump(mode="json"))
    return selected


def pack_context(state: dict, context: ResearchContext, documents: dict,
                 maximum_bytes: int = 100000) -> dict:
    """Remove execution noise without paraphrasing financial or contrary evidence.

    Input state belongs to exactly the requested scope. Documents are verified
    corpus manifest objects, not metadata supplied by a model. Output retains
    full observations, calculations, claims, gaps and source windows. Oversize
    output fails explicitly; UTF-8 bytes are not an exact model token count.
    """
    original = ResearchContext.model_validate(state["context"])
    if scope_key(original) != scope_key(context):
        raise ValueError("范围变更需要派生研究，不能复用旧结论")
    rows = eligible_observations(state.get("observations", []), context)
    if len(rows) != len(state.get("observations", [])):
        raise ValueError("状态含有范围外财务记录")
    evidence = []
    for raw in state.get("evidence", []):
        item = EvidenceCandidate.model_validate(raw)
        record = documents.get(item.document_id)
        if (record is None or record.ts_code != item.company_id
                or item.company_id not in context.company_ids
                or record.published_on is None or record.published_on > context.as_of_date
                or int(record.reporting_period) not in context.fiscal_years
                or hashlib.sha256(item.text.encode()).hexdigest() != item.evidence_sha256):
            raise ValueError("上下文证据来源、范围或文本指纹失配")
        evidence.append(item.model_dump(mode="json"))
    for company, calculations in state.get("calculations", {}).items():
        if company not in context.company_ids:
            raise ValueError("计算含有范围外公司")
        if any(c.get("company_id") != company or not c.get("comparison_period")
               or not set(c["comparison_period"]).issubset(context.fiscal_years)
               for c in calculations.values()):
            raise ValueError("计算范围与研究请求不一致")
    claims = [ResearchClaim.model_validate(c).model_dump(mode="json") for c in state.get("claims", [])]
    if any(c["company_id"] not in context.company_ids for c in claims):
        raise ValueError("结论含有范围外公司")
    gaps = [ResearchGap.model_validate(g).model_dump(mode="json") for g in state.get("gaps", [])]
    phenomena = [Phenomenon.model_validate(p).model_dump(mode="json") for p in state.get("phenomena", [])]
    if any(item["company_id"] not in context.company_ids for item in gaps + phenomena):
        raise ValueError("缺口或现象含有范围外公司")
    for raw in state.get("disclosures", []):
        batch = DisclosureBatch.model_validate(raw)
        if batch.company_id not in context.company_ids:
            raise ValueError("披露含有范围外公司")
    for raw in state.get("hypotheses", []):
        batch = HypothesisBatch.model_validate(raw["batch"])
        if raw["company_id"] not in context.company_ids or any(h.company_id != raw["company_id"] for h in batch.hypotheses):
            raise ValueError("假设含有范围外公司")
    evidence_ids = {e["evidence_id"] for e in evidence}
    observation_ids = {o["observation_id"] for o in rows}
    calculation_ids = {c["calculation_id"] for group in state.get("calculations", {}).values() for c in group.values()}
    if any(not set(c["evidence_ids"]).issubset(evidence_ids)
           or not set(c["observation_ids"]).issubset(observation_ids)
           or not set(c["calculation_ids"]).issubset(calculation_ids) for c in claims):
        raise ValueError("结论依赖的证据或数字记录缺失")
    payload = {"schema_version": 1, "scope_sha256": scope_key(context),
               "context": context.model_dump(mode="json"), "observations": rows,
               "calculations": state.get("calculations", {}), "claims": claims,
               "gaps": gaps, "evidence": evidence, "phenomena": phenomena,
               # Relations are unverified assessments; retain weakening and limits.
               "disclosures": state.get("disclosures", []),
               "hypotheses": state.get("hypotheses", []),
               "execution_status": state.get("execution_status", "PARTIAL")}
    size = len(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    if maximum_bytes <= 0 or size > maximum_bytes:
        raise ContextTooLarge(f"CONTEXT_TOO_LARGE:{size}>{maximum_bytes}")
    return {"payload": payload, "payload_sha256": stable_sha256(payload),
            "utf8_bytes": size, "compression": "LOSSLESS_STRUCTURED_SELECTION", "serialization": "COMPACT_JSON_UTF8"}


TOPICS = {
    "revenue": ("收入", "营收", "revenue"),
    "cash_flow": ("现金流", "回款", "cash"),
    "receivables": ("应收", "账龄", "receivable"),
}


def answer_followup(capsule: dict, requested: ResearchContext, question: str,
                    topic: str | None = None) -> dict:
    """Return existing reviewed statements, never let question text alter scope.

    Same-scope output includes exact claims and all evidence/financial
    dependencies, not a new causal synthesis. Changed scope requires a new
    task, and returns only filtered observation candidates for revalidation.
    """
    if not question.strip() or len(question) > 2000:
        raise ValueError("追问不能为空或超过2000字符")
    payload = capsule["payload"]
    if capsule["payload_sha256"] != stable_sha256(payload) or payload["schema_version"] != 1:
        raise ValueError("上下文摘要被修改或版本不兼容")
    previous = ResearchContext.model_validate(payload["context"])
    if payload["scope_sha256"] != scope_key(previous):
        raise ValueError("摘要范围指纹错误")
    if scope_key(previous) != scope_key(requested):
        same_snapshot = previous.corpus_snapshot_id == requested.corpus_snapshot_id
        return {"status": "NEW_RUN_REQUIRED", "claims": [], "evidence": [],
                "observation_candidates": eligible_observations(payload["observations"], requested) if same_snapshot else [],
                "reason": "范围已变化；候选事实须重新验证并重算，不能复用旧解释"}
    if topic is None:
        found = [name for name, words in TOPICS.items() if any(w in question.lower() for w in words)]
        topic = found[0] if len(found) == 1 else None
    if topic not in {*TOPICS, "all"}:
        return {"status": "NEEDS_CLARIFICATION", "topics": [*TOPICS, "all"], "claims": [], "evidence": []}
    words = TOPICS.get(topic, ())
    claims = [c for c in payload["claims"] if c["status"] == "APPROVED"
              and (topic == "all" or any(w in c["text"].lower() for w in words))]
    if not claims:
        return {"status": "INSUFFICIENT_EVIDENCE", "claims": [], "evidence": [], "gaps": payload["gaps"]}
    return {"status": "ANSWERED_FROM_REVIEWED_RECORDS", "scope_sha256": scope_key(requested),
            "claims": claims, "observations": payload["observations"],
            "calculations": payload["calculations"], "evidence": payload["evidence"],
            "gaps": payload["gaps"], "disclosures": payload["disclosures"],
            "limitations": ["仅复用已审核记录，未生成新的因果结论", "模型关联标签仍需独立语义审核"]}
