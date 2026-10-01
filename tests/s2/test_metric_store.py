from datetime import date

import pytest

from finresearch.contracts import MetricDefinition, MetricObservationSeed
from finresearch.finance import build_observation
from finresearch.storage import DuplicateMetricRecordError, MetricStore


def make_definition() -> MetricDefinition:
    return MetricDefinition.model_validate(
        {
            "metric_id": "revenue", "name_zh": "营业收入", "definition": "合并利润表营业收入金额",
            "metric_kind": "FLOW", "currency": "CNY", "canonical_unit": "CNY_YUAN", "allowed_raw_units": {"CNY_YUAN": "1"},
            "statement_scope": "CONSOLIDATED", "measurement_basis": "REPORTED", "allowed_formula_ids": ["period_growth_v1"],
            "prohibited_equivalences": [], "version": "1.0.0", "status": "ACTIVE",
        }
    )


def make_observation(value: str, published_on: str, version: int = 1, supersedes: str | None = None, document_id: str = "doc-v1"):
    seed = MetricObservationSeed.model_validate(
        {
            "metric_id": "revenue", "company_id": "002371.SZ", "fiscal_year": 2024, "period_kind": "FLOW",
            "period_start": "2024-01-01", "period_end": "2024-12-31", "observed_at": None,
            "raw_value_text": value, "raw_value": value, "raw_unit": "CNY_YUAN", "currency": "CNY",
            "statement_scope": "CONSOLIDATED", "measurement_basis": "REPORTED", "value_status": "OBSERVED",
            "document_id": document_id, "document_sha256": ("a" if version == 1 else "b") * 64,
            "document_published_on": published_on,
            "evidence_refs": [{"evidence_id": f"evidence-version-{version}", "pdf_page": 1, "source_label": "收入"}],
            "extraction_method": "TABLE_EXTRACTED", "review_status": "DRAFT", "review_note": "synthetic PIT",
            "observation_version": version, "supersedes_observation_id": supersedes,
        }
    )
    return build_observation(seed, make_definition())


def test_batch_insert_is_atomic_on_duplicate(tmp_path) -> None:
    store = MetricStore(tmp_path / "metrics.sqlite3")
    observation = make_observation("100", "2025-01-01")
    with pytest.raises(DuplicateMetricRecordError):
        store.save_observations([observation, observation])
    assert store.counts()["metric_observations"] == 0


def test_point_in_time_query_does_not_backfill_correction(tmp_path) -> None:
    store = MetricStore(tmp_path / "metrics.sqlite3")
    old = make_observation("100", "2025-01-01")
    new = make_observation("110", "2025-02-01", version=2, supersedes=old.observation_id, document_id="doc-v2")
    store.save_observations([old, new])
    allowed = {old.document_id, new.document_id}
    assert store.query_observation_as_of("002371.SZ", "revenue", 2024, date(2024, 12, 31), allowed_document_ids=allowed) is None
    assert store.query_observation_as_of("002371.SZ", "revenue", 2024, date(2025, 1, 15), allowed_document_ids=allowed).standard_value == old.standard_value
    assert store.query_observation_as_of("002371.SZ", "revenue", 2024, date(2025, 2, 15), allowed_document_ids=allowed).standard_value == new.standard_value


def test_reopen_database_preserves_decimal_payload(tmp_path) -> None:
    path = tmp_path / "metrics.sqlite3"
    observation = make_observation("123.45", "2025-01-01")
    MetricStore(path).save_observations([observation])
    loaded = MetricStore(path).load_all_observations()[0]
    assert loaded.standard_value == observation.standard_value
    assert loaded.content_sha256 == observation.content_sha256
