from finresearch.contracts import AnswerValidation, EvidenceAnswer
from finresearch.verification import validate_case_truth


def test_truth_validation_requires_expected_page_and_value() -> None:
    answer = EvidenceAnswer.model_validate(
        {
            "status": "ANSWERABLE",
            "answer": "2024年度合并营业收入为29,838,069,162.26元。",
            "period": "FY2024",
            "unit": "CNY yuan",
            "evidence_refs": [
                {"evidence_id": "known-evidence", "document_id": "doc-1", "pdf_page": 95}
            ],
        }
    )
    validation = AnswerValidation(
        validation_status="PASS",
        reference_checks=[],
        numeric_checks=[],
        scope_checks=[],
        unsupported_claims=[],
        errors=[],
        warnings=[],
    )
    result = validate_case_truth(
        answer,
        validation,
        {
            "expected_status": "ANSWERABLE",
            "expected_period": "FY2024",
            "expected_unit": "CNY yuan",
            "required_pages": [95],
            "required_numeric_tokens": ["29838069162.26"],
            "required_term_groups": [],
        },
    )
    assert result["passed"] is True
