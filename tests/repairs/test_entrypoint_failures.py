"""Check failure accounting through CLI orchestration using fake local transport."""
import importlib.util
import json
import shutil
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from finresearch.model import DeepSeekJsonClient, LiveProbeGuard, ModelCallFailure
from finresearch.contracts import ConnectivityProbeOutput
from finresearch.ingestion import load_verified_pages
from finresearch.storage import SQLiteStateStore, DuplicateStateError
from test_model_failure_audit import FakeModel, response

ROOT = Path(__file__).resolve().parents[2]


def module(path, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def test_sqlite_connections_are_closed_on_success_and_duplicate(monkeypatch, tmp_path):
    connections = []
    original = SQLiteStateStore._connect
    def tracked(owner):
        connection = original(owner)
        connections.append(connection)
        return connection
    monkeypatch.setattr(SQLiteStateStore, "_connect", tracked)
    store = SQLiteStateStore(tmp_path / "state.sqlite3")
    store.save_once("one", {"amount": 1})
    assert store.load("one") == {"amount": 1}
    assert store.count() == 1
    with pytest.raises(DuplicateStateError):
        store.save_once("one", {"amount": 2})
    assert len(connections) == 5
    for connection in connections:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            connection.execute("SELECT 1")


def test_s1_batch_persists_failed_retries_and_continues_next_case(monkeypatch, tmp_path):
    entry = module("scripts/s1/run_answer_batch.py", "batch_entry")
    root = tmp_path / "project"
    for folder in ("configs", "evals", "src"):
        shutil.copytree(ROOT / folder, root / folder, ignore=shutil.ignore_patterns("__pycache__"))
    for relative in ("storage/s0/document_manifest.jsonl", "storage/s1/snapshot_registry.json"):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, path)
    registry = json.loads((root / "storage/s1/snapshot_registry.json").read_text(encoding="utf-8"))
    snapshot = next(s for s in registry["snapshots"] if s["snapshot_id"] == registry["active_snapshot_id"])
    pages = root / snapshot["pages_path"]
    pages.parent.mkdir(parents=True, exist_ok=True)
    pages.write_text("offline stub; page loader never reached", encoding="utf-8")
    config_path = root / "configs/s1/model.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config.update(maximum_input_tokens=100_000, currency_limit="1.00")
    config_path.write_text(json.dumps(config), encoding="utf-8")
    fake = FakeModel([response("bad1"), response("bad2"), response('{"status":"INSUFFICIENT_EVIDENCE","missing_information":["not supplied"]}')])
    monkeypatch.setattr("finresearch.model.deepseek_json.ChatOpenAI", lambda **kwargs: fake)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "offline-only")
    monkeypatch.setattr(entry, "PROJECT_ROOT", root)
    script_copy = root / "scripts/s1/run_answer_batch.py"
    script_copy.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / "scripts/s1/run_answer_batch.py", script_copy)
    monkeypatch.setattr(entry, "__file__", str(script_copy))
    retrieval = SimpleNamespace(evidence_candidates=[], model_dump=lambda **kwargs: {"status": "FOUND"})
    monkeypatch.setattr(entry, "prepare_single_document_request", lambda *args, **kwargs: SimpleNamespace(retrieval=retrieval))
    monkeypatch.setattr(sys, "argv", ["batch", "--attempt-id", "batch-fake", "--case-id", "S0-Q1-REVENUE", "--case-id", "S0-Q2-OCF"])
    assert entry.main() == 1
    output = root / "runs/s1/batch-fake"
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    audits = [json.loads(line) for line in (output / "model_attempts.jsonl").read_text(encoding="utf-8").splitlines()]
    assert summary["case_count"] == 2 and summary["total_calls"] == fake.calls == len(audits) == 3
    assert summary["failed_attempt_count"] == 2
    assert summary["unknown_cost_attempt_count"] == 0
    assert summary["estimated_cost_usd"] == "0.00016200"
    assert summary["cases"][0]["estimated_cost_usd"] == "0.00010800"
    assert summary["cases"][0]["call_count"] == 2
    assert (output / "cases/S0-Q1-REVENUE/failure.json").is_file()
    assert (output / "cases/S0-Q2-OCF/model_response.json").is_file()


def test_s0_failed_structure_preserves_every_attempt_usage(monkeypatch):
    entry = module("scripts/probes/run_live_probes.py", "s0_entry")
    config = json.loads((ROOT / "configs/s0/model_probe.json").read_text(encoding="utf-8"))
    guard = entry.LiveProbeGuard(config)
    fake = FakeModel([response("bad1"), response("bad2")])
    monkeypatch.setattr("finresearch.model.deepseek_json.ChatOpenAI", lambda **kwargs: fake)
    client = DeepSeekJsonClient(config, "offline-only", guard)
    def action():
        client.invoke_json("T05", entry.ConnectivityProbeOutput, "test", "test")
    result = entry.execute_probe("T05", "test", [], "test", config, guard, client, action, [])
    assert result["status"] == "FAIL" and result["call_count"] == 2
    assert result["usage"]["total_tokens"] == 240
    assert result["cost_amount"] == "0.00010800"
    assert len(result["observed_result"]["model_attempts"]) == 2


def test_g1_rechecks_old_explanations_without_changing_history():
    entry = module("scripts/s1/finalize_g1.py", "g1_entry")
    manifest = [json.loads(line) for line in (ROOT / "storage/s0/document_manifest.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    registry = json.loads((ROOT / "storage/s1/snapshot_registry.json").read_text(encoding="utf-8"))
    snapshot = next(s for s in registry["snapshots"] if s["snapshot_id"] == registry["active_snapshot_id"])
    pages = load_verified_pages(ROOT, snapshot, manifest[0])
    scope = json.loads((ROOT / "configs/s0/scope.json").read_text(encoding="utf-8"))
    requirements = json.loads((ROOT / "evals/dev/s1_answer_requirements.json").read_text(encoding="utf-8"))["cases"]
    cases = {c["case_id"]: c for c in [json.loads(line) for line in (ROOT / "evals/dev/s0_cases.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]}
    identifier = "S0-Q5-OCF-EXPLANATION"
    result = entry.revalidate_case(ROOT / "runs/s1" / entry.LIVE_SOURCES[identifier] / "cases" / identifier, identifier, pages, manifest, scope, requirements, cases)
    assert result["deterministic"]["validation_status"] == "NEEDS_REVIEW"
    assert not result["case_truth"]["passed"]


def test_g1_audit_file_presence_does_not_prove_complete_accounting(monkeypatch, tmp_path):
    entry = module("scripts/s1/finalize_g1.py", "g1_cost_entry")
    config = json.loads((ROOT / "configs/s0/model_probe.json").read_text(encoding="utf-8"))
    guard = LiveProbeGuard(config)
    fake = FakeModel([response("bad1"), response("bad2")])
    monkeypatch.setattr("finresearch.model.deepseek_json.ChatOpenAI", lambda **kwargs: fake)
    client = DeepSeekJsonClient(config, "offline-only", guard)
    with pytest.raises(ModelCallFailure, match="INVALID_STRUCTURED_OUTPUT"):
        client.invoke_json("T05", ConnectivityProbeOutput, "test", "test")
    path = tmp_path / "model_attempts.jsonl"
    path.write_text("\n".join(json.dumps(a) for a in client.attempt_audits), encoding="utf-8")
    assert entry.reconcile_call_audits(tmp_path, 2, "0.00010800", config)
    assert not entry.reconcile_call_audits(tmp_path, 1, "0.00005400", config)
    assert not entry.reconcile_call_audits(tmp_path, 2, "0", config)
    client.attempt_audits[0]["estimated_cost_usd"] = "0"
    path.write_text("\n".join(json.dumps(a) for a in client.attempt_audits), encoding="utf-8")
    assert not entry.reconcile_call_audits(tmp_path, 2, "0.00005400", config)
