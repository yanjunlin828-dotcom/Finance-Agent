from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from finresearch.contracts import MetricDefinition, MetricObservationSeed
from finresearch.finance import build_observation, normalize_amount


def definition(kind: str = "FLOW") -> MetricDefinition:
    return MetricDefinition.model_validate(
        {
            "metric_id": "test_metric",
            "name_zh": "测试指标",
            "definition": "用于契约边界测试的指标",
            "metric_kind": kind,
            "currency": "CNY",
            "canonical_unit": "CNY_YUAN",
            "allowed_raw_units": {"CNY_YUAN": "1", "CNY_TEN_THOUSAND_YUAN": "10000"},
            "statement_scope": "CONSOLIDATED",
            "measurement_basis": "REPORTED_AMOUNT",
            "allowed_formula_ids": ["period_growth_v1"],
            "prohibited_equivalences": [],
            "version": "1.0.0",
            "status": "ACTIVE",
        }
    )


def seed_payload() -> dict:
    return {
        "metric_id": "test_metric", "company_id": "002371.SZ", "fiscal_year": 2024,
        "period_kind": "FLOW", "period_start": "2024-01-01", "period_end": "2024-12-31", "observed_at": None,
        "raw_value_text": "12,345.67", "raw_value": "12345.67", "raw_unit": "CNY_YUAN", "currency": "CNY",
        "statement_scope": "CONSOLIDATED", "measurement_basis": "REPORTED_AMOUNT", "value_status": "OBSERVED",
        "document_id": "doc-test", "document_sha256": "a" * 64, "document_published_on": "2025-01-01",
        "evidence_refs": [{"evidence_id": "evidence-test-001", "pdf_page": 1, "source_label": "测试"}],
        "extraction_method": "TABLE_EXTRACTED", "review_status": "DRAFT", "review_note": "测试",
        "observation_version": 1, "supersedes_observation_id": None,
    }


def test_decimal_unit_normalization_is_exact() -> None:
    assert normalize_amount(Decimal("1.2345"), "CNY_TEN_THOUSAND_YUAN", definition()) == Decimal("12345.0000")


def test_unknown_unit_is_rejected() -> None:
    with pytest.raises(ValueError, match="不允许原始单位"):
        normalize_amount(Decimal("1"), "USD", definition())


def test_flow_and_stock_time_fields_are_mutually_exclusive() -> None:
    with pytest.raises(ValidationError, match="FLOW必须提供"):
        MetricObservationSeed.model_validate({**seed_payload(), "period_start": None, "observed_at": "2024-12-31"})


def test_missing_value_cannot_be_coerced_to_zero() -> None:
    with pytest.raises(ValidationError, match="非OBSERVED状态不得附带数值"):
        MetricObservationSeed.model_validate({**seed_payload(), "value_status": "MISSING", "raw_value": "0"})


def test_observation_id_is_stable() -> None:
    seed = MetricObservationSeed.model_validate(seed_payload())
    first = build_observation(seed, definition())
    second = build_observation(seed, definition())
    assert first.observation_id == second.observation_id
    assert first.standard_value == Decimal("12345.67")
