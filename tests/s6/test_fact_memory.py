from datetime import date
import pytest
from finresearch.storage.session_store import UnsafeResume
from .helpers import create_executor


def test_explicit_source_verified_memory_idempotent_and_cutoff_filtered(tmp_path):
    ex, ctx, _ = create_executor(tmp_path)
    rows = [o.model_dump(mode="json") for o in ex.deps.load_financials(ctx)]
    assert ex.store.promote_facts(ctx, rows, rows) == 18
    assert ex.store.promote_facts(ctx, rows, rows) == 0
    assert sorted(ex.store.read_facts(ctx, rows), key=lambda r: r["observation_id"]) == sorted(rows, key=lambda r: r["observation_id"])
    early = ctx.model_copy(update={"as_of_date": date(2025, 4, 24)})
    assert all(r["document_published_on"] <= "2025-04-24" for r in ex.store.read_facts(early, rows))
    different = ctx.model_copy(update={"corpus_snapshot_id": "another-snapshot"})
    assert not ex.store.read_facts(different, rows)
    subset = ctx.model_copy(update={"company_ids": ["002371.SZ"]})
    assert all(r["company_id"] == "002371.SZ" for r in ex.store.read_facts(subset, rows))


@pytest.mark.parametrize("mutation", [{"standard_value": "999"}, {"review_status": "DRAFT"}, {"company_id": "000001.SZ"}])
def test_modified_or_unreviewed_records_cannot_become_facts(tmp_path, mutation):
    ex, ctx, _ = create_executor(tmp_path)
    rows = [o.model_dump(mode="json") for o in ex.deps.load_financials(ctx)]
    candidates = [dict(rows[0]) | mutation]
    with pytest.raises(ValueError):
        ex.store.promote_facts(ctx, candidates, rows)
    assert not ex.store.read_facts(ctx, rows)


def test_report_and_hypothesis_are_not_fact_records(tmp_path):
    ex, ctx, _ = create_executor(tmp_path)
    with pytest.raises(ValueError):
        ex.store.promote_facts(ctx, [{"text": "模型认为增长可靠", "kind": "INFERENCE"}], [])


def test_stale_facts_not_reused_and_corruption_is_explicit(tmp_path):
    ex, ctx, _ = create_executor(tmp_path)
    rows = [o.model_dump(mode="json") for o in ex.deps.load_financials(ctx)]
    ex.store.promote_facts(ctx, rows, rows)
    assert not ex.store.read_facts(ctx, [])
    with ex.store.transaction() as conn:
        conn.execute("UPDATE fact SET payload='{}'")
    with pytest.raises(UnsafeResume):
        ex.store.read_facts(ctx, rows)

