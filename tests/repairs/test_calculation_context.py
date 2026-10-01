import importlib.util
from pathlib import Path
from datetime import date
import pytest
from finresearch.finance import calculate_growth, calculate_flow_ratio, calculate_growth_gap
from finresearch.storage import MetricStore, MetricSourceConflictError

ROOT = Path(__file__).resolve().parents[2]
def module(relative):
    spec = importlib.util.spec_from_file_location("repair_fixture", ROOT / relative)
    item = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(item)
    return item
observation = module("tests/s2/test_calculations.py").make_observation
stored = module("tests/s2/test_metric_store.py").make_observation

def test_half_year_cannot_be_labeled_annual_growth():
    current = observation("120", 2024).model_copy(update={"period_start": date(2024, 7, 1)})
    result = calculate_growth(current, observation("100", 2023))
    assert result.status == "INCOMPARABLE" and result.value is None

def test_ratio_cannot_be_used_as_growth_gap():
    ratio = calculate_flow_ratio(observation("20", 2024, metric="ocf"), observation("100", 2024))
    assert calculate_growth_gap(ratio, ratio).status == "INCOMPARABLE"

@pytest.mark.parametrize("field,value", [("company_id", "688082.SH"), ("comparison_period", (2022, 2023)), ("currency", "USD"), ("statement_scope", "PARENT_COMPANY")])
def test_growth_gap_must_share_comparison_context(field, value):
    growth = calculate_growth(observation("120", 2024), observation("100", 2023))
    assert calculate_growth_gap(growth, growth.model_copy(update={field: value})).status == "INCOMPARABLE"

def test_new_formula_contract_describes_percentage_points():
    growth = calculate_growth(observation("120", 2024), observation("100", 2023))
    decline = calculate_growth(observation("90", 2024, metric="ocf"), observation("100", 2023, metric="ocf"))
    result = calculate_growth_gap(growth, decline)
    assert result.value == 30 and result.expression == "(growth_a - growth_b) * 100"
    assert result.formula_version == "1.1.0"

def test_unlinked_sources_raise_conflict_but_snapshot_can_exclude_other_source(tmp_path):
    store = MetricStore(tmp_path / "test.sqlite3")
    old = stored("100", "2025-01-01")
    other = stored("1", "2025-01-01", document_id="independent-doc")
    store.save_observations([old, other])
    with pytest.raises(MetricSourceConflictError):
        store.query_observation_as_of("002371.SZ", "revenue", 2024, date(2025, 2, 1), allowed_document_ids={old.document_id, other.document_id})
    assert store.query_observation_as_of("002371.SZ", "revenue", 2024, date(2025, 2, 1), allowed_document_ids={old.document_id}).standard_value == 100
    assert store.query_observation_as_of("002371.SZ", "revenue", 2024, date(2025, 2, 1), allowed_document_ids=set()) is None

@pytest.mark.parametrize("field,value", [("company_id", "688082.SH"), ("document_published_on", date(2024, 12, 1)), ("measurement_basis", "OTHER")])
def test_invalid_correction_chain_rolls_back_whole_batch(tmp_path, field, value):
    store = MetricStore(tmp_path / "test.sqlite3")
    old = stored("100", "2025-01-01")
    new = stored("110", "2025-02-01", version=2, supersedes=old.observation_id, document_id="doc-v2")
    with pytest.raises(MetricSourceConflictError):
        store.save_observations([old, new.model_copy(update={field: value})])
    assert store.counts()["metric_observations"] == 0
