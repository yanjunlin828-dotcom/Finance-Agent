"""Revalidate financial run content rather than trusting saved PASS flags."""
import hashlib
import json
import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path

from finresearch.contracts import DocumentManifestRecord, MetricObservation, MetricObservationSeed
from finresearch.finance.observations import build_definition_index, build_evidence_index, build_validated_observations, read_evidence_jsonl
from finresearch.finance.s2_protocol import calculate_s2_comparison
from finresearch.ingestion import load_verified_pages, validate_evidence_pages


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def s2_source_files(root: Path) -> list[Path]:
    """Lock the S2 import/compute path, without importing later-stage modules.

    Any new dependency of this path must be added here. Unrelated S3/S4 code
    changes do not change the meaning of a narrow financial replay.
    """
    modules = ["__init__.py", "contracts/__init__.py", "contracts/document_manifest.py", "contracts/evidence.py",
               "contracts/model_output.py", "contracts/research_request.py", "contracts/metrics.py", "contracts/numeric.py",
               "finance/__init__.py", "finance/calculations.py", "finance/observations.py", "finance/units.py", "finance/s2_protocol.py",
               "ingestion/__init__.py", "ingestion/pdf_pages.py", "storage/__init__.py", "storage/metric_store.py", "storage/sqlite_store.py",
               "verification/__init__.py", "verification/answer.py", "verification/case_truth.py", "verification/financial_artifacts.py",
               "gates/__init__.py", "gates/s0_gate.py", "gates/s1_gate.py", "gates/s2_gate.py"]
    return [root / "src/finresearch" / name for name in modules]


def verify_lock(root: Path, path: Path, required: set[str]) -> None:
    """Verify all locked inputs, required provenance and containment, with no writes."""
    lock = json.loads(path.read_text(encoding="utf-8"))
    files = lock.get("files", [])
    names = [entry["path"] for entry in files]
    if len(names) != len(set(names)) or not required.issubset(names):
        raise ValueError("输入锁缺少来源或代码版本，或路径重复")
    for entry in files:
        target = (root / entry["path"]).resolve()
        if not target.is_relative_to(root.resolve()) or file_sha(target) != entry["sha256"]:
            raise ValueError(f"输入锁失配: {entry['path']}")


def validate_observation_artifacts(root: Path, observations_path: Path) -> list[MetricObservation]:
    """Validate source, amounts, standardization and IDs against the active snapshot."""
    registry_path = root / "storage/s1/snapshot_registry.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    snapshot = next(s for s in registry["snapshots"] if s["snapshot_id"] == registry["active_snapshot_id"])
    manifests = [DocumentManifestRecord.model_validate_json(line) for line in
                 (root / "storage/s0/document_manifest.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    manifest = next(m for m in manifests if m.document_id == snapshot["document_id"])
    pages = load_verified_pages(root, snapshot, manifest.model_dump(mode="json"))
    lock_path = observations_path.parent / "inputs.lock.json"
    required = {"configs/s2/metric_dictionary.json", "storage/s0/document_manifest.jsonl",
                "storage/s1/snapshot_registry.json", snapshot["pages_path"], snapshot["snapshot_manifest_path"], manifest.local_path,
                "scripts/s2/build_observations.py"}
    required.update(p.relative_to(root).as_posix() for p in s2_source_files(root))
    verify_lock(root, lock_path, required)
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    evidence_paths = [root / e["path"] for e in lock["files"] if e["path"].endswith("/evidence.jsonl")]
    evidence = build_evidence_index(read_evidence_jsonl(evidence_paths))
    observations = [MetricObservation.model_validate_json(line) for line in observations_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    used = {ref.evidence_id for observation in observations for ref in observation.evidence_refs}
    validate_evidence_pages([evidence[eid] for eid in used], pages)
    seeds = [MetricObservationSeed.model_validate({k: getattr(o, k) for k in MetricObservationSeed.model_fields}) for o in observations]
    definitions = build_definition_index(json.loads((root / "configs/s2/metric_dictionary.json").read_text(encoding="utf-8")))
    rebuilt, reports = build_validated_observations(seeds, definitions, evidence, manifest=manifest)
    if len(rebuilt) != len(observations) or any(not r["passed"] for r in reports) or rebuilt != observations:
        raise ValueError("观察的来源、标准值、身份或哈希与独立重建不符")
    return observations


def verify_s2_artifacts(root: Path, observation_dir: Path, comparison_dir: Path) -> dict:
    """Replay exact math and persisted payloads; restrict certification to formula 1.1.0."""
    result = {"source_verified": False, "calculations_recomputed": False, "database_replayed": False, "errors": []}
    try:
        observations = validate_observation_artifacts(root, observation_dir / "observations.jsonl")
        result["source_verified"] = True
        required = {"storage/s0/document_manifest.jsonl", "configs/s2/metric_dictionary.json", "scripts/s2/build_comparison.py",
                    (observation_dir / "observations.jsonl").relative_to(root).as_posix(),
                    (observation_dir / "inputs.lock.json").relative_to(root).as_posix()}
        required.update(p.relative_to(root).as_posix() for p in s2_source_files(root))
        verify_lock(root, comparison_dir / "inputs.lock.json", required)
        payload = json.loads((comparison_dir / "comparison.json").read_text(encoding="utf-8"))
        if payload["company_id"] != "002371.SZ" or payload["as_of_date"] != "2025-04-25":
            raise ValueError("比较报告公司或截止日与当前S2协议不符")
        if payload["observations"] != [o.model_dump(mode="json") for o in observations]:
            raise ValueError("报告内观察与来源产物不符")
        if any(o.document_published_on > date(2025, 4, 25) for o in observations):
            raise ValueError("使用了截止日之后的披露")
        selected = {(o.metric_id, o.fiscal_year): o for o in observations}
        expected = calculate_s2_comparison(selected)
        if payload["calculations"] != {k: c.model_dump(mode="json") for k, c in expected.items()}:
            raise ValueError("计算值、输入、口径、公式或哈希与重新计算不符")
        rows = {r["item_id"]: r for r in payload["comparison_rows"]}
        if len(rows) != 5 or len(payload["comparison_rows"]) != 5:
            raise ValueError("比较行缺失或重复")
        for metric, name in (("revenue", "revenue_growth_2024"), ("operating_cash_flow_net", "ocf_growth_2024"), ("accounts_receivable", "ar_growth_2024")):
            row = rows[metric]
            if any(Decimal(row[str(y)]) != selected[(metric, y)].standard_value for y in (2023, 2024)) or Decimal(row["change"]) != expected[name].value or row["unit"] != "CNY_YUAN":
                raise ValueError("比较行金额或增长值失配")
        for key in ("ocf_to_revenue", "ar_to_revenue"):
            if rows[key]["unit"] != "RATIO" or any(Decimal(rows[key][str(y)]) != expected[f"{key}_{y}"].value for y in (2023, 2024)):
                raise ValueError("比较行比率失配")
        if len(payload["descriptive_flags"]) != 1 or Decimal(payload["descriptive_flags"][0]["value_percentage_points"]) != expected["revenue_minus_ocf_growth_gap"].value:
            raise ValueError("描述性差值失配")
        result["calculations_recomputed"] = True
        # Read-only connection: a gate never creates or mutates the submitted DB.
        database_path = comparison_dir / "metric_store.sqlite3"
        connection = sqlite3.connect(database_path.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            db_observations = [MetricObservation.model_validate_json(row[0]) for row in connection.execute("SELECT payload_json FROM metric_observations")]
            db_calculations = [json.loads(row[0]) for row in connection.execute("SELECT payload_json FROM calculation_results")]
        finally:
            connection.close()
        if sorted(o.model_dump_json() for o in observations) != sorted(o.model_dump_json() for o in db_observations) or sorted(json.dumps(c.model_dump(mode="json"), sort_keys=True) for c in expected.values()) != sorted(json.dumps(c, sort_keys=True) for c in db_calculations):
            raise ValueError("数据库持久化负载与独立复算不符")
        result["database_replayed"] = True
    except (ValueError, KeyError, StopIteration, OSError, sqlite3.Error) as exc:
        result["errors"].append(str(exc))
    return result
