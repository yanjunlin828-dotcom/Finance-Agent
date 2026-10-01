"""S1页面、检索证据与回答校验契约。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DocumentPage(BaseModel):
    """从已登记PDF派生的一页可追溯文本。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    page_id: str = Field(min_length=12, max_length=220)
    document_id: str = Field(min_length=3, max_length=150)
    company_id: str = Field(pattern=r"^\d{6}\.(SZ|SH|BJ)$")
    pdf_page: int = Field(gt=0)
    printed_page: str | None = Field(default=None, max_length=30)
    raw_text: str
    normalized_text: str
    text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    parser_name: Literal["pdfplumber"]
    parser_version: str = Field(min_length=3)
    extraction_status: Literal["TEXT_AVAILABLE", "EMPTY", "FAILED"]

    @model_validator(mode="after")
    def validate_text_status(self) -> "DocumentPage":
        if self.extraction_status == "TEXT_AVAILABLE" and not self.normalized_text:
            raise ValueError("TEXT_AVAILABLE页面必须包含规范化文本")
        if self.extraction_status == "EMPTY" and self.normalized_text:
            raise ValueError("EMPTY页面不得包含规范化文本")
        return self


class PageScore(BaseModel):
    """可解释的页级检索得分。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    page_id: str
    document_id: str
    pdf_page: int = Field(gt=0)
    score: float
    matched_terms: list[str]
    score_components: dict[str, float]


class EvidenceCandidate(BaseModel):
    """可由稳定ID回到具体页面和行区间的证据窗口。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_id: str = Field(min_length=12, max_length=240)
    document_id: str
    page_id: str
    company_id: str = Field(pattern=r"^\d{6}\.(SZ|SH|BJ)$")
    pdf_page: int = Field(gt=0)
    line_start: int = Field(gt=0)
    line_end: int = Field(gt=0)
    text: str = Field(min_length=1)
    matched_terms: list[str]
    retrieval_score: float
    retrieval_rank: int = Field(gt=0)
    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_line_range(self) -> "EvidenceCandidate":
        if self.line_end < self.line_start:
            raise ValueError("line_end不能早于line_start")
        return self


class RetrievalResult(BaseModel):
    """一次确定性单文档检索的完整结果。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    request_id: str
    question: str
    document_id: str
    rule_id: str | None
    required_terms: list[str]
    optional_terms: list[str]
    context_terms: list[str]
    page_scores: list[PageScore]
    evidence_candidates: list[EvidenceCandidate]
    status: Literal["FOUND", "NO_MATCHING_RULE", "NO_EVIDENCE"]


class AnswerValidation(BaseModel):
    """程序对模型答案的引用、数值和范围检查。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    validation_status: Literal["PASS", "FAIL", "NEEDS_REVIEW"]
    reference_checks: list[dict[str, object]]
    numeric_checks: list[dict[str, object]]
    scope_checks: list[dict[str, object]]
    unsupported_claims: list[str]
    errors: list[str]
    warnings: list[str]

