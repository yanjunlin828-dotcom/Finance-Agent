from __future__ import annotations

from finresearch.contracts import DocumentPage, EvidenceCandidate


def test_document_page_roundtrip() -> None:
    page = DocumentPage(
        page_id="doc:p0001:1234567890abcdef",
        document_id="doc-001",
        company_id="002371.SZ",
        pdf_page=1,
        printed_page=None,
        raw_text="营业收入 100.00",
        normalized_text="营业收入 100.00",
        text_sha256="a" * 64,
        parser_name="pdfplumber",
        parser_version="0.11.10",
        extraction_status="TEXT_AVAILABLE",
    )
    assert DocumentPage.model_validate_json(page.model_dump_json()) == page


def test_evidence_candidate_rejects_reverse_line_range() -> None:
    try:
        EvidenceCandidate(
            evidence_id="doc:p0001:l10-2:1234567890abcdef",
            document_id="doc-001",
            page_id="doc:p0001:1234567890abcdef",
            company_id="002371.SZ",
            pdf_page=1,
            line_start=10,
            line_end=2,
            text="营业收入 100.00",
            matched_terms=["营业收入"],
            retrieval_score=1.0,
            retrieval_rank=1,
            evidence_sha256="b" * 64,
        )
    except ValueError as exc:
        assert "line_end" in str(exc)
    else:
        raise AssertionError("反向行区间应被拒绝")
