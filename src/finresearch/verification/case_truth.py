"""用版本化人工真值要求检查S1演示答案。"""

from __future__ import annotations

from typing import Any

from finresearch.contracts import AnswerValidation, EvidenceAnswer

from .answer import normalize_numeric_token, _numeric_tokens


def validate_case_truth(
    answer: EvidenceAnswer,
    deterministic_validation: AnswerValidation,
    requirements: dict[str, Any],
) -> dict[str, Any]:
    """检查状态、必要页、真值数字和关键语义词组，返回逐项可审计结果。"""

    checks: list[dict[str, Any]] = []
    checks.append(
        {
            "name": "deterministic_validation",
            "passed": deterministic_validation.validation_status in {"PASS", "NEEDS_REVIEW"},
            "actual": deterministic_validation.validation_status,
        }
    )
    checks.append(
        {
            "name": "expected_status",
            "passed": answer.status == requirements["expected_status"],
            "actual": answer.status,
        }
    )
    checks.append(
        {
            "name": "expected_period",
            "passed": answer.period == requirements.get("expected_period"),
            "actual": answer.period,
        }
    )
    checks.append(
        {
            "name": "expected_unit",
            "passed": answer.unit == requirements.get("expected_unit"),
            "actual": answer.unit,
        }
    )
    cited_pages = {reference.pdf_page for reference in answer.evidence_refs}
    for page in requirements.get("required_pages", []):
        checks.append(
            {"name": f"required_page_{page}", "passed": page in cited_pages, "actual": sorted(cited_pages)}
        )
    answer_text = answer.answer or ""
    answer_tokens = set(_numeric_tokens(answer_text))
    for token in requirements.get("required_numeric_tokens", []):
        normalized = normalize_numeric_token(token)
        checks.append(
            {
                "name": f"required_numeric_{token}",
                "passed": normalized in answer_tokens or (normalized.endswith("%") and "-" + normalized in answer_tokens),
                "actual": answer_text,
            }
        )
    for index, alternatives in enumerate(requirements.get("required_term_groups", []), start=1):
        checks.append(
            {
                "name": f"required_term_group_{index}",
                "passed": any(term in answer_text for term in alternatives),
                "actual": alternatives,
            }
        )
    forbidden = requirements.get("forbidden_claims", [])
    for index, claim in enumerate(forbidden, start=1):
        checks.append(
            {"name": f"forbidden_claim_{index}", "passed": claim not in answer_text, "actual": claim}
        )
    mechanical_passed = all(check["passed"] for check in checks)
    needs_review = deterministic_validation.validation_status == "NEEDS_REVIEW"
    # Development keywords are not a human or semantic verification result.
    return {"passed": mechanical_passed and not needs_review,
            "mechanical_checks_passed": mechanical_passed,
            "review_status": "NEEDS_REVIEW" if needs_review else deterministic_validation.validation_status,
            "checks": checks}
