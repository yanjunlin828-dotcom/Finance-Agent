"""S4 fixed research objects: facts, calculations, hypotheses and gaps."""
from __future__ import annotations
from datetime import date
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

CompanyId = Annotated[str, Field(pattern=r"^\d{6}\.(SZ|SH|BJ)$")]
Need = Literal["SALES_COLLECTION_DETAIL", "PURCHASE_AND_PAYMENTS", "RECEIVABLE_AGING", "CUSTOMER_MIX", "TIMING_DETAIL", "INDEPENDENT_CONFIRMATION"]
Category = Literal["SCALE_EXPANSION", "SETTLEMENT_TIMING", "CUSTOMER_MIX", "UNRESOLVED_CAUSE"]


class ResearchContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    run_id: str = Field(pattern=r"^[a-zA-Z0-9_-]+$")
    company_ids: list[CompanyId] = Field(min_length=1)
    fiscal_years: tuple[int, int]
    as_of_date: date
    corpus_snapshot_id: str
    protocol_id: Literal["revenue_quality_v1"]
    protocol_version: Literal["1.0.0"]
    protocol_config_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def scope_consistency(self):
        if len(set(self.company_ids)) != len(self.company_ids):
            raise ValueError("公司不得重复")
        if self.fiscal_years[1] != self.fiscal_years[0] + 1:
            raise ValueError("首版只支持相邻完整财年")
        return self


class Phenomenon(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    phenomenon_id: str
    company_id: CompanyId
    kind: Literal["REVENUE_GROWTH", "CASH_REVENUE_DIVERGENCE", "RECEIVABLE_GROWTH_EXCEEDS_REVENUE", "NEGATIVE_OCF_RATIO", "OCF_RATIO_DECLINE", "RECEIVABLE_RATIO_INCREASE"]
    calculation_ids: list[str] = Field(min_length=1)
    description: str


class HypothesisCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    company_id: CompanyId
    category: Category
    phenomenon_ids: list[str] = Field(min_length=1, max_length=6)
    rationale: str = Field(min_length=3, max_length=600)
    support_needed: list[Need] = Field(min_length=1, max_length=6)
    weakening_evidence_needed: list[Need] = Field(min_length=1, max_length=6)


class HypothesisBatch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    hypotheses: list[HypothesisCandidate] = Field(min_length=1, max_length=3)


class DisclosureSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    evidence_id: str
    exact_quote: str = Field(min_length=5, max_length=350)
    related_hypothesis_categories: list[Category] = Field(default_factory=list, max_length=3)
    relation: Literal["SUPPORTING_DISCLOSURE", "WEAKENING_DISCLOSURE", "CONTEXT"] = "CONTEXT"


class DisclosureBatch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    company_id: CompanyId
    selected: list[DisclosureSelection] = Field(max_length=3)
    missing_information: list[str] = Field(default_factory=list, max_length=6)


class ResearchClaim(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    claim_id: str
    company_id: CompanyId
    kind: Literal["COMPUTED", "DISCLOSED", "INFERENCE", "UNRESOLVED"]
    text: str
    calculation_ids: list[str] = Field(default_factory=list)
    observation_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    hypothesis_category: Category | None = None
    status: Literal["APPROVED", "REJECTED", "REVIEW_REQUIRED"]
    limitations: list[str] = Field(default_factory=list)


class ResearchGap(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    gap_id: str
    company_id: CompanyId
    gap_type: Literal["MISSING_METRIC", "INCOMPARABLE_CALCULATION", "MISSING_EXPLANATION", "MISSING_COUNTEREVIDENCE", "CLAIM_REJECTED", "MODEL_FAILURE"]
    description: str
    affected_ids: list[str] = Field(default_factory=list)
    suggested_next_step: str
    status: Literal["UNRESOLVED"] = "UNRESOLVED"


class WriterPlan(BaseModel):
    """The model may order approved claims; it cannot author new fact text."""
    model_config = ConfigDict(extra="forbid", frozen=True)
    ordered_claim_ids: list[str] = Field(min_length=1)
