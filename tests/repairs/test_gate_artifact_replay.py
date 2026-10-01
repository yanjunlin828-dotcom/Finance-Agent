"""Exercise the actual S2 CLI in isolation, then tamper with submitted artifacts."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def run(root, script, *args):
    result = subprocess.run([sys.executable, str(root / script), *args], cwd=root,
                            capture_output=True, text=True, encoding="utf-8", errors="replace",
                            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
    return result


@pytest.fixture(scope="module")
def isolated_run(tmp_path_factory):
    root = tmp_path_factory.mktemp("s2-protocol-replay")
    for folder in ("src", "configs", "scripts/s2"):
        shutil.copytree(ROOT / folder, root / folder, ignore=shutil.ignore_patterns("__pycache__"))
    files = ["storage/s0/document_manifest.jsonl", "storage/s2/metric_observation_seeds.jsonl", "storage/s2/manual_corrections.json", "storage/s1/snapshot_registry.json", "docs/stages/s2/handoff_to_s3_s4.md"]
    manifest = json.loads((ROOT / files[0]).read_text(encoding="utf-8").splitlines()[0])
    registry = json.loads((ROOT / "storage/s1/snapshot_registry.json").read_text(encoding="utf-8"))
    snapshot = next(s for s in registry["snapshots"] if s["snapshot_id"] == registry["active_snapshot_id"])
    files += [manifest["local_path"], snapshot["pages_path"], snapshot["snapshot_manifest_path"]]
    files += ["runs/s1/s1-live-q1-20260922-01/cases/S0-Q1-REVENUE/evidence.jsonl", "runs/s1/s1-live-rest-20260922-01/cases/S0-Q2-OCF/evidence.jsonl", "runs/s1/s1-live-retry-20260922-02/cases/S0-Q3-AR/evidence.jsonl"]
    for file in files:
        target = root / file
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / file, target)
    for script, args in [("build_observations.py", ["--attempt-id", "obs"]),
                         ("build_comparison.py", ["--attempt-id", "comparison", "--observation-attempt", "obs"]),
                         ("run_boundary_checks.py", ["--attempt-id", "boundary"])]:
        result = run(root, "scripts/s2/" + script, *args)
        assert result.returncode == 0, result.stdout + result.stderr
    return root


def finalize(root, identifier):
    result = run(root, "scripts/s2/finalize_g2.py", "--attempt-id", identifier, "--observation-attempt", "obs", "--comparison-attempt", "comparison", "--boundary-attempt", "boundary")
    report = json.loads((root / "runs/s2" / identifier / "gate_report.json").read_text(encoding="utf-8"))
    return result, report


def test_actual_gate_accepts_current_valid_recomputed_run(isolated_run):
    result, report = finalize(isolated_run, "gate-valid")
    assert result.returncode == 0, result.stdout + result.stderr
    assert report["overall_decision"] == "GO"


@pytest.mark.parametrize("field", ["value", "input_sha256", "formula_version"])
def test_saved_pass_and_replay_flags_cannot_hide_tampered_calculation(isolated_run, field):
    path = isolated_run / "runs/s2/comparison/comparison.json"
    before = path.read_bytes()
    try:
        payload = json.loads(before)
        payload["calculations"]["revenue_growth_2024"][field] = "999" if field == "value" else "tampered"
        path.write_text(json.dumps(payload), encoding="utf-8")
        result, report = finalize(isolated_run, "gate-altered-" + field)
        assert result.returncode == 1
        assert report["overall_decision"] == "NO_GO"
        assert next(c for c in report["checks"] if c["gate_id"] == "G2-C1")["status"] == "FAIL"
    finally:
        path.write_bytes(before)


def test_disclosure_or_standard_value_tampering_rejected_even_with_saved_source_pass(isolated_run):
    path = isolated_run / "runs/s2/obs/observations.jsonl"
    before = path.read_bytes()
    try:
        records = [json.loads(line) for line in before.decode().splitlines()]
        records[0]["document_published_on"] = "2024-01-01"
        records[0]["standard_value"] = "1"
        path.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
        result, report = finalize(isolated_run, "gate-altered-source")
        assert result.returncode == 1 and report["overall_decision"] == "NO_GO"
        assert next(c for c in report["checks"] if c["gate_id"] == "G2-M2")["status"] == "FAIL"
    finally:
        path.write_bytes(before)


def test_code_version_change_invalidates_locked_run(isolated_run):
    path = isolated_run / "src/finresearch/finance/calculations.py"
    before = path.read_bytes()
    try:
        path.write_bytes(before + b"\n# different implementation version\n")
        result, report = finalize(isolated_run, "gate-code-changed")
        assert result.returncode == 1 and report["overall_decision"] == "NO_GO"
    finally:
        path.write_bytes(before)


def test_unrelated_later_stage_code_does_not_invalidate_narrow_s2_replay(isolated_run):
    path = isolated_run / "src/finresearch/contracts/unrelated_future_stage.py"
    path.write_text("FUTURE_STAGE_VERSION = 'changed'\n", encoding="utf-8")
    result, report = finalize(isolated_run, "gate-unrelated-extension")
    assert result.returncode == 0 and report["overall_decision"] == "GO"
