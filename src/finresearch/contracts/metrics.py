"""S2财务指标、观察值和计算结果契约。"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from .numeric import parse_amount_text


class MetricDefinition(BaseModel):
    """版本化指标定义；金融口径必须先于数值入库。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    metric_id: str = Field(pattern=r"^[a-z][a-z0-9_]+$")
    name_zh: str = Field(min_length=2)
    definition: str = Field(min_length=5)
    metric_kind: Literal["FLOW", "STOCK"]
    currency: Literal["CNY", "USD", "HKD"]
    canonical_unit: str = Field(pattern=r"^[A-Z][A-Z0-9_]+$")
    allowed_raw_units: dict[str, Decimal]
    statement_scope: Literal["CONSOLIDATED", "PARENT_COMPANY"]
    measurement_basis: str = Field(min_length=3)
    allowed_formula_ids: list[str]
    prohibited_equivalences: list[str]
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    status: Literal["ACTIVE", "RETIRED"]

    @model_validator(mode="after")
    def validate_units(self) -> "MetricDefinition":
        if self.canonical_unit not in self.allowed_raw_units:
            raise ValueError("canonical_unit必须出现在allowed_raw_units")
        if any(factor <= 0 for factor in self.allowed_raw_units.values()):
            raise ValueError("单位换算因子必须为正数")
        return self


class MetricEvidenceRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_id: str = Field(min_length=12)
    pdf_page: int = Field(gt=0)
    source_label: str = Field(min_length=2)


class MetricObservationSeed(BaseModel):
    """人工或来源复核后的观察种子，稳定ID和标准值由程序生成。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    metric_id: str
    company_id: str = Field(pattern=r"^\d{6}\.(SZ|SH|BJ)$")
    fiscal_year: int = Field(ge=2000, le=2100)
    period_kind: Literal["FLOW", "STOCK"]
    period_start: date | None
    period_end: date | None
    observed_at: date | None
    raw_value_text: str | None
    raw_value: Decimal | None
    raw_unit: str | None
    currency: Literal["CNY", "USD", "HKD"]
    statement_scope: Literal["CONSOLIDATED", "PARENT_COMPANY"]
    measurement_basis: str
    value_status: Literal["OBSERVED", "MISSING", "NOT_APPLICABLE", "REVIEW_REQUIRED"]
    document_id: str
    document_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    document_published_on: date
    evidence_refs: list[MetricEvidenceRef]
    extraction_method: Literal["MANUAL_CURATED_FROM_S1_EVIDENCE", "TABLE_EXTRACTED"]
    review_status: Literal["HUMAN_VERIFIED", "SOURCE_CHECKED", "DRAFT"]
    review_note: str
    observation_version: int = Field(gt=0)
    supersedes_observation_id: str | None

    @model_validator(mode="after")
    def validate_period_and_value(self) -> "MetricObservationSeed":
        if self.period_kind == "FLOW":
            if self.period_start is None or self.period_end is None or self.observed_at is not None:
                raise ValueError("FLOW必须提供period_start/period_end且不得提供observed_at")
            if self.period_end < self.period_start:
                raise ValueError("period_end不能早于period_start")
        else:
            if self.observed_at is None or self.period_start is not None or self.period_end is not None:
                raise ValueError("STOCK必须提供observed_at且不得提供期间起止日")
        if self.value_status == "OBSERVED":
            if self.raw_value is None or self.raw_value_text is None or self.raw_unit is None:
                raise ValueError("OBSERVED必须保留原值、原始文本和单位")
            if not self.evidence_refs:
                raise ValueError("OBSERVED必须包含证据引用")
            if not self.raw_value.is_finite() or parse_amount_text(self.raw_value_text) != self.raw_value:
                raise ValueError("原始金额文本与raw_value不一致")
        elif self.raw_value is not None or self.raw_value_text is not None:
            raise ValueError("非OBSERVED状态不得附带数值")
        return self


class MetricObservation(MetricObservationSeed):
    """完成单位标准化、可不可变存储的财务观察。"""

    observation_id: str = Field(min_length=16, max_length=220)
    standard_value: Decimal | None
    standard_unit: str | None
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_standard_value(self) -> "MetricObservation":
        if self.value_status == "OBSERVED":
            if self.standard_value is None or self.standard_unit is None:
                raise ValueError("OBSERVED必须有标准化值和单位")
        elif self.standard_value is not None or self.standard_unit is not None:
            raise ValueError("非OBSERVED不得伪造标准化值")
        return self


class ComparabilityCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    passed: bool
    expected: str | None = None
    actual: str | None = None


class CalculationResult(BaseModel):
    """确定性公式输出；无效状态不得携带数值。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    calculation_id: str
    formula_id: Literal[
        "period_growth_v1", "flow_to_flow_ratio_v1", "stock_to_flow_ratio_v1", "growth_gap_pp_v1"
    ]
    formula_version: Literal["1.0.0", "1.1.0"]
    # Legacy records remain readable but lack a usable calculation context.
    company_id: str | None = None
    comparison_period: tuple[int, int] | None = None
    currency: str | None = None
    statement_scope: str | None = None
    expression: str
    input_ids: list[str] = Field(min_length=1)
    status: Literal["VALID", "MISSING_INPUT", "ZERO_DENOMINATOR", "NEGATIVE_BASE", "INCOMPARABLE"]
    value: Decimal | None
    output_unit: Literal["RATIO", "PERCENTAGE_POINT"]
    comparability_checks: list[ComparabilityCheck]
    errors: list[str]
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_status_value(self) -> "CalculationResult":
        if self.status == "VALID" and self.value is None:
            raise ValueError("VALID计算必须包含结果值")
        if self.status != "VALID" and self.value is not None:
            raise ValueError("非VALID计算不得包含伪结果")
        if self.status == "VALID" and self.errors:
            raise ValueError("VALID计算不得包含错误")
        return self


def stable_sha256(payload: object) -> str:
    """对JSON兼容对象生成稳定SHA-256，Decimal由调用方转为字符串。"""

    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
