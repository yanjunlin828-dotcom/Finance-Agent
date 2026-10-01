"""对模型答案执行确定性引用、数字与范围检查。"""

from __future__ import annotations

import re
import unicodedata
from typing import Iterable

from finresearch.contracts import AnswerValidation, EvidenceCandidate
from finresearch.contracts.model_output import EvidenceAnswer

_NUMBER_PATTERN = re.compile(r"(?<![A-Za-z0-9.,])[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:%|个百分点)?(?![0-9.])")


def normalize_numeric_token(value: str) -> str:
    return unicodedata.normalize("NFKC", value).replace(",", "").replace("−", "-").lstrip("+")


def _numeric_tokens(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", text).replace("−", "-")
    tokens = []
    for match in _NUMBER_PATTERN.finditer(normalized):
        token = normalize_numeric_token(match.group(0))
        # Explicit narrative decreases denote negative percentage changes.
        prefix = normalized[max(0, match.start() - 5):match.start()]
        if token.endswith("%") and not token.startswith("-") and re.search(r"(?:下降|减少|降低)(?:了|约)?\s*$", prefix):
            token = "-" + token
        tokens.append(token)
    return tokens


def validate_evidence_answer(
    answer: EvidenceAnswer,
    candidates: Iterable[EvidenceCandidate],
    *,
    expected_company_id: str,
    expected_period: str | None = None,
    expected_unit: str | None = None,
) -> AnswerValidation:
    """校验答案只能引用当前候选，并检查可确定验证的数字与范围。"""

    candidate_list = list(candidates)
    candidate_by_id = {candidate.evidence_id: candidate for candidate in candidate_list}
    errors: list[str] = []
    warnings: list[str] = []
    reference_checks: list[dict[str, object]] = []
    numeric_checks: list[dict[str, object]] = []
    scope_checks: list[dict[str, object]] = []

    for reference in answer.evidence_refs:
        candidate = candidate_by_id.get(reference.evidence_id)
        known = candidate is not None
        metadata_matches = bool(
            candidate
            and candidate.document_id == reference.document_id
            and candidate.pdf_page == reference.pdf_page
        )
        reference_checks.append(
            {
                "evidence_id": reference.evidence_id,
                "known": known,
                "metadata_matches": metadata_matches,
            }
        )
        if not known:
            errors.append(f"引用不属于本次候选集合: {reference.evidence_id}")
        elif not metadata_matches:
            errors.append(f"引用元数据与证据不一致: {reference.evidence_id}")

    if answer.status == "ANSWERABLE" and not answer.evidence_refs:
        errors.append("ANSWERABLE没有引用证据")
    if answer.status == "INSUFFICIENT_EVIDENCE" and answer.evidence_refs:
        warnings.append("证据不足状态仍附带引用；引用只能说明已检查片段，不能支持确定答案")

    cited = [candidate_by_id[ref.evidence_id] for ref in answer.evidence_refs if ref.evidence_id in candidate_by_id]
    cited_text = "\n".join(candidate.text for candidate in cited)
    cited_tokens = set(_numeric_tokens(cited_text))
    if answer.answer:
        for token in _numeric_tokens(answer.answer):
            supported = token in cited_tokens
            numeric_checks.append({"token": token, "supported_by_cited_evidence": supported})
            if not supported:
                errors.append(f"回答数字未出现在引用证据中: {token}")

    company_matches = all(candidate.company_id == expected_company_id for candidate in cited)
    scope_checks.append(
        {"name": "company_id", "expected": expected_company_id, "passed": company_matches}
    )
    if cited and not company_matches:
        errors.append("引用证据公司与请求公司不一致")

    if expected_period is not None:
        period_matches = answer.period == expected_period
        scope_checks.append(
            {"name": "period", "expected": expected_period, "actual": answer.period, "passed": period_matches}
        )
        if not period_matches:
            errors.append("回答期间与预期期间不一致")
    if expected_unit is not None:
        unit_matches = answer.unit == expected_unit
        scope_checks.append(
            {"name": "unit", "expected": expected_unit, "actual": answer.unit, "passed": unit_matches}
        )
        if not unit_matches:
            errors.append("回答单位与预期单位不一致")

    if answer.status == "INSUFFICIENT_EVIDENCE" and not answer.missing_information:
        errors.append("证据不足状态没有说明缺少的信息")
    if answer.status == "ANSWERABLE" and not answer.answer:
        errors.append("有答案状态缺少答案文本")
    if answer.status == "ANSWERABLE":
        warnings.append("程序仅检查引用、完整数字与口径字段；不证明事实归属、解释语义或因果关系")

    if errors:
        status = "FAIL"
    elif answer.status == "ANSWERABLE" and (not numeric_checks or expected_unit is None):
        status = "NEEDS_REVIEW"
        warnings.append("文字回答须独立语义复核，合法数字或年份不能证明解释与因果")
    else:
        status = "PASS"
    return AnswerValidation(
        validation_status=status,
        reference_checks=reference_checks,
        numeric_checks=numeric_checks,
        scope_checks=scope_checks,
        unsupported_claims=[],
        errors=errors,
        warnings=warnings,
    )
