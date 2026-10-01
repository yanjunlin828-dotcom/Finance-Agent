"""Development retrieval metrics; labels never enter production retrieval."""
from __future__ import annotations
from statistics import mean
import numpy as np

from finresearch.contracts.retrieval import MultiDocumentResult


def score_case(case: dict, result: MultiDocumentResult) -> dict:
    """Ranked parent coverage and expanded evidence completeness are separate.

    All necessary pages remain in the denominator even when no result is found.
    MRR uses labeled answer pages, rather than rewarding a header-only match.
    """
    expected = {p["pdf_page"] for p in case["necessary_pages"]}
    primary = {h.chunk.pdf_page for h in result.hits if h.chunk.document_id == case["document_id"]}
    answer_ranks = [h.rank for h in result.hits if h.chunk.document_id == case["document_id"] and h.chunk.pdf_page in case["answer_pages"]]
    returned_text: dict[int, str] = {}
    for hit in result.hits:
        if hit.chunk.document_id == case["document_id"]:
            returned_text[hit.chunk.pdf_page] = hit.parent_text
        for extra in hit.adjacent_context:
            if extra["document_id"] == case["document_id"]:
                returned_text[int(extra["pdf_page"])] = str(extra["text"])
    complete = all(all(excerpt in returned_text.get(p["pdf_page"], "") for excerpt in p["excerpts"]) for p in case["necessary_pages"])
    q = result.query
    violations = [h.chunk.chunk_id for h in result.hits if
                  h.chunk.company_id != q.company_id or h.chunk.reporting_year != q.reporting_year
                  or h.chunk.published_on > q.as_of_date or h.chunk.corpus_snapshot_id != q.corpus_snapshot_id
                  or h.chunk.document_status != "READY" or h.chunk.document_type != q.document_type]
    return {"case_id": case["case_id"], "recall_at_5": len(expected & primary) / len(expected),
            "hit_at_5": bool(answer_ranks), "mrr_at_5": 1 / min(answer_ranks) if answer_ranks else 0,
            "primary_all_necessary_pages": expected <= primary, "expanded_complete": complete,
            "filter_violations": violations, "missing_necessary_pages": sorted(expected - primary),
            "ranked_pages": [h.chunk.pdf_page for h in result.hits], "latency_ms": result.elapsed_ms}


def aggregate_scores(rows: list[dict]) -> dict:
    if not rows:
        raise ValueError("评测集不得为空")
    return {"case_count": len(rows), "recall_at_5": mean(r["recall_at_5"] for r in rows),
            "hit_at_5": mean(r["hit_at_5"] for r in rows), "mrr_at_5": mean(r["mrr_at_5"] for r in rows),
            "primary_complete_at_5": mean(r["primary_all_necessary_pages"] for r in rows),
            "expanded_complete_at_5": mean(r["expanded_complete"] for r in rows),
            "filter_violation_count": sum(len(r["filter_violations"]) for r in rows),
            "latency_p50_ms": float(np.quantile([r["latency_ms"] for r in rows], .5)),
            "latency_p95_ms": float(np.quantile([r["latency_ms"] for r in rows], .95))}
