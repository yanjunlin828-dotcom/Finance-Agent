"""Response contract for a one-shot B0; gold labels never enter model input."""
from typing import Literal
from decimal import Decimal
from pydantic import BaseModel, ConfigDict, Field


class BaselineAmount(BaseModel):
    model_config = ConfigDict(extra="forbid")
    metric_id: Literal["revenue", "operating_cash_flow_net", "accounts_receivable"]
    fiscal_year: Literal[2023, 2024]
    value: Decimal
    unit: Literal["CNY_YUAN"]
    evidence_id: str


class BaselineCalculation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["VALID", "NEGATIVE_BASE", "ZERO_DENOMINATOR", "INCOMPARABLE", "MISSING_INPUT"]
    value: Decimal | None
    output_unit: Literal["RATIO", "PERCENTAGE_POINT"]


class BaselineQuote(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evidence_id: str
    exact_quote: str


class BaselineAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    observations: list[BaselineAmount] = Field(max_length=6)
    calculations: dict[str, BaselineCalculation]
    disclosures: list[BaselineQuote] = Field(max_length=3)
    missing_information: list[str] = Field(max_length=8)
