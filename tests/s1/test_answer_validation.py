from __future__ import annotations

from finresearch.contracts import EvidenceCandidate
from finresearch.contracts.model_output import EvidenceAnswer
from finresearch.verification import normalize_numeric_token, validate_evidence_answer


def candidate() -> EvidenceCandidate:
    return EvidenceCandidate(
        evidence_id="doc:p0095:l1-3:1234567890abcdef",
        document_id="doc-001",
        page_id="doc:p0095:1234567890abcdef",
        company_id="002371.SZ",
        pdf_page=95,
        line_start=1,
        line_end=3,
        text="合并利润表\n单位:元\n营业收入 29,838,069,162.26",
        matched_terms=["合并利润表", "营业收入"],
        retrieval_score=20.0,
        retrieval_rank=1,
        evidence_sha256="c" * 64,
    )


def test_numeric_answer_with_valid_reference_passes() -> None:
    answer = EvidenceAnswer.model_validate(
        {
            "status": "ANSWERABLE",
            "answer": "29,838,069,162.26元",
            "evidence_refs": [
                {"evidence_id": candidate().evidence_id, "document_id": "doc-001", "pdf_page": 95}
            ],
            "period": "FY2024",
            "unit": "CNY yuan",
        }
    )
    result = validate_evidence_answer(
        answer,
        [candidate()],
        expected_company_id="002371.SZ",
        expected_period="FY2024",
        expected_unit="CNY yuan",
    )
    assert result.validation_status == "PASS"
    assert result.errors == []


def test_fabricated_reference_and_number_fail() -> None:
    answer = EvidenceAnswer.model_validate(
        {
            "status": "ANSWERABLE",
            "answer": "999.00元",
            "evidence_refs": [
                {"evidence_id": "unknown-evidence", "document_id": "doc-001", "pdf_page": 95}
            ],
            "period": "FY2024",
            "unit": "CNY yuan",
        }
    )
    result = validate_evidence_answer(
        answer,
        [candidate()],
        expected_company_id="002371.SZ",
        expected_period="FY2024",
        expected_unit="CNY yuan",
    )
    assert result.validation_status == "FAIL"
    assert any("不属于" in error for error in result.errors)
    assert any("999.00" in error for error in result.errors)


def test_normalize_numeric_token_removes_thousands_separator() -> None:
    assert normalize_numeric_token("29,838,069,162.26") == "29838069162.26"
