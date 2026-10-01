from __future__ import annotations

import json
from pathlib import Path

from finresearch.contracts import DocumentPage
from finresearch.retrieval import KeywordEvidenceRetriever

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def make_page(page: int, text: str) -> DocumentPage:
    return DocumentPage(
        page_id=f"doc:p{page:04d}:1234567890abcdef",
        document_id="doc-001",
        company_id="002371.SZ",
        pdf_page=page,
        printed_page=None,
        raw_text=text,
        normalized_text=text,
        text_sha256=f"{page:064x}"[-64:],
        parser_name="pdfplumber",
        parser_version="0.11.10",
        extraction_status="TEXT_AVAILABLE",
    )


def retriever() -> KeywordEvidenceRetriever:
    config = json.loads((PROJECT_ROOT / "configs/s1/retrieval_terms.json").read_text(encoding="utf-8"))
    return KeywordEvidenceRetriever(config)


def test_revenue_question_ranks_consolidated_income_statement_first() -> None:
    pages = [
        make_page(1, "目录 营业收入"),
        make_page(95, "合并利润表\n单位:元\n营业收入 29,838,069,162.26\n2024年度"),
        make_page(101, "母公司利润表\n营业收入 20.00"),
    ]
    result = retriever().retrieve("r1", "2024年度合并营业收入是多少？", pages)
    assert result.status == "FOUND"
    assert result.rule_id == "revenue_numeric"
    assert result.page_scores[0].pdf_page == 95
    assert result.evidence_candidates[0].pdf_page == 95
    assert "29,838,069,162.26" in result.evidence_candidates[0].text


def test_explanation_rule_wins_when_question_asks_why_cash_flow_declined() -> None:
    pages = [
        make_page(31, "现金流\n经营活动产生的现金流量净额同比下降\n采购商品支付的货款增加\n变动说明"),
        make_page(99, "合并现金流量表\n经营活动产生的现金流量净额 100.00"),
    ]
    result = retriever().retrieve("r2", "公司如何解释经营活动现金流量净额下降？", pages)
    assert result.rule_id == "operating_cash_flow_explanation"
    assert result.page_scores[0].pdf_page == 31


def test_unknown_question_returns_no_matching_rule_without_evidence() -> None:
    result = retriever().retrieve("r3", "董事长出生于哪一年？", [make_page(1, "没有相关内容")])
    assert result.status == "NO_MATCHING_RULE"
    assert result.evidence_candidates == []


def test_evidence_ids_are_stable() -> None:
    pages = [make_page(95, "合并利润表\n单位:元\n营业收入 100.00")]
    first = retriever().retrieve("r4", "营业收入是多少？", pages)
    second = retriever().retrieve("r4", "营业收入是多少？", pages)
    assert [item.evidence_id for item in first.evidence_candidates] == [
        item.evidence_id for item in second.evidence_candidates
    ]
