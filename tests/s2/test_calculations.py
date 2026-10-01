from decimal import Decimal

from finresearch.contracts import MetricDefinition, MetricObservationSeed
from finresearch.finance import (
    build_observation,
    calculate_flow_ratio,
    calculate_growth,
    calculate_growth_gap,
    calculate_stock_to_flow,
)


def make_observation(value: str | None, year: int, *, metric: str = "revenue", kind: str = "FLOW", status: str = "OBSERVED"):
    definition = MetricDefinition.model_validate(
        {
            "metric_id": metric, "name_zh": "测试指标", "definition": "用于确定性计算测试的指标",
            "metric_kind": kind, "currency": "CNY", "canonical_unit": "CNY_YUAN", "allowed_raw_units": {"CNY_YUAN": "1"},
            "statement_scope": "CONSOLIDATED", "measurement_basis": "REPORTED_AMOUNT",
            "allowed_formula_ids": ["period_growth_v1"], "prohibited_equivalences": [], "version": "1.0.0", "status": "ACTIVE",
        }
    )
    is_flow = kind == "FLOW"
    seed = MetricObservationSeed.model_validate(
        {
            "metric_id": metric, "company_id": "002371.SZ", "fiscal_year": year, "period_kind": kind,
            "period_start": f"{year}-01-01" if is_flow else None, "period_end": f"{year}-12-31" if is_flow else None,
            "observed_at": None if is_flow else f"{year}-12-31", "raw_value_text": value, "raw_value": value,
            "raw_unit": "CNY_YUAN" if value is not None else None, "currency": "CNY", "statement_scope": "CONSOLIDATED",
            "measurement_basis": "REPORTED_AMOUNT", "value_status": status, "document_id": "doc-test",
            "document_sha256": "b" * 64, "document_published_on": "2025-04-25",
            "evidence_refs": [{"evidence_id": "evidence-test-001", "pdf_page": 1, "source_label": "测试"}] if value is not None else [],
            "extraction_method": "TABLE_EXTRACTED", "review_status": "DRAFT", "review_note": "测试",
            "observation_version": 1, "supersedes_observation_id": None,
        }
    )
    return build_observation(seed, definition)


def test_growth_normal_and_decline() -> None:
    assert calculate_growth(make_observation("120", 2024), make_observation("100", 2023)).value == Decimal("0.2")
    assert calculate_growth(make_observation("80", 2024), make_observation("100", 2023)).value == Decimal("-0.2")


def test_growth_boundaries_have_no_fake_value() -> None:
    zero = calculate_growth(make_observation("10", 2024), make_observation("0", 2023))
    negative = calculate_growth(make_observation("10", 2024), make_observation("-2", 2023))
    missing = calculate_growth(make_observation("10", 2024), make_observation(None, 2023, status="MISSING"))
    assert (zero.status, zero.value) == ("ZERO_DENOMINATOR", None)
    assert (negative.status, negative.value) == ("NEGATIVE_BASE", None)
    assert (missing.status, missing.value) == ("MISSING_INPUT", None)


def test_flow_ratio_requires_same_period() -> None:
    result = calculate_flow_ratio(make_observation("20", 2024, metric="ocf"), make_observation("100", 2023))
    assert result.status == "INCOMPARABLE"
    assert result.value is None


def test_flow_ratio_rejects_currency_and_scope_conflicts() -> None:
    numerator = make_observation("20", 2024, metric="ocf")
    denominator = make_observation("100", 2024)
    assert calculate_flow_ratio(numerator.model_copy(update={"currency": "USD"}), denominator).status == "INCOMPARABLE"
    assert calculate_flow_ratio(numerator.model_copy(update={"statement_scope": "PARENT_COMPANY"}), denominator).status == "INCOMPARABLE"


def test_stock_to_flow_and_growth_gap() -> None:
    stock = make_observation("25", 2024, metric="ar", kind="STOCK")
    flow = make_observation("100", 2024)
    ratio = calculate_stock_to_flow(stock, flow)
    revenue_growth = calculate_growth(make_observation("120", 2024), make_observation("100", 2023))
    ocf_growth = calculate_growth(make_observation("90", 2024, metric="ocf"), make_observation("100", 2023, metric="ocf"))
    gap = calculate_growth_gap(revenue_growth, ocf_growth)
    assert ratio.value == Decimal("0.25")
    assert gap.value == Decimal("30.0")
