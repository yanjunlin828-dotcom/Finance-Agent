"""S5 gap-driven actions are typed data, never executable model instructions."""
from __future__ import annotations
from datetime import date
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .research import CompanyId, Need

Tool = Literal["QUERY_METRIC", "SEARCH_DISCLOSURE", "READ_CONTEXT", "REVIEW_TABLE", "VERIFY_VERSION", "REQUEST_CLARIFICATION"]
GapType = Literal["MISSING_METRIC", "DISCLOSURE_CONTEXT", "CONTEXT_REVIEW", "MISSING_EXPLANATION", "INDEPENDENT_CONFIRMATION", "INVALID_CALCULATION", "TABLE_REVIEW", "SOURCE_VERSION", "MODEL_FAILURE", "SOURCE_REQUEST"]


class SupplementGap(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    gap_id: str = Field(min_length=3)
    company_id: CompanyId
    gap_type: GapType
    severity: Literal["CRITICAL", "MATERIAL", "CONTEXT"]
    description: str
    affected_claim_ids: list[str] = Field(default_factory=list)
    metric_id: Literal["revenue", "operating_cash_flow_net", "accounts_receivable"] | None = None
    fiscal_year: int | None = None
    need: Need | None = None
    evidence_id: str | None = None
    closing_condition: Literal["VERIFIED_METRIC", "VERIFIED_SOURCE_WINDOW", "ATTRIBUTED_EXPLANATION", "INDEPENDENT_REVIEW", "NEW_SNAPSHOT_OR_USER_INPUT", "NOT_REPAIRABLE_BY_SEARCH"]
    status: Literal["OPEN", "RESOLVED", "LIMITED", "WAITING_INPUT", "FAILED"] = "OPEN"
    attempted_action_ids: list[str] = Field(default_factory=list)
    resolution_note: str | None = None

    @model_validator(mode="after")
    def require_parameters(self):
        if self.gap_type == "MISSING_METRIC" and (self.metric_id is None or self.fiscal_year is None):
            raise ValueError("缺指标必须指定指标和财年")
        if self.gap_type == "DISCLOSURE_CONTEXT" and self.need is None:
            raise ValueError("资料缺口必须指定Need")
        if self.gap_type == "DISCLOSURE_CONTEXT" and self.need == "INDEPENDENT_CONFIRMATION":
            raise ValueError("独立核验不能伪装成检索上下文需求")
        if self.gap_type == "CONTEXT_REVIEW" and not self.evidence_id:
            raise ValueError("上下文复核必须引用已登记证据ID")
        if self.gap_type == "INDEPENDENT_CONFIRMATION" and self.status == "RESOLVED":
            raise ValueError("当前工具不能关闭独立因果核验缺口")
        return self


class SupplementAction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    action_id: str
    gap_id: str
    tool: Tool
    company_id: CompanyId
    as_of_date: date
    corpus_snapshot_id: str
    arguments: dict
    reason: str


class ActionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    selected_action_ids: list[str] = Field(min_length=1, max_length=2)


class ToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    action_id: str
    status: Literal["FOUND", "NO_EVIDENCE", "REVIEW_REQUIRED", "REQUIRES_NEW_SNAPSHOT", "NEEDS_INPUT"]
    observations: list[dict] = Field(default_factory=list, max_length=1)
    evidence: list[dict] = Field(default_factory=list, max_length=4)
    note: str


class SupplementQuotes(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    selected_quote_ids: list[str] = Field(default_factory=list, max_length=6)
