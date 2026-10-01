"""从S1证据和S2种子生成经过来源校验的MetricObservation。"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from finresearch.contracts import DocumentManifestRecord, MetricObservationSeed  # noqa: E402
from finresearch.finance import (  # noqa: E402
    build_definition_index,
    build_evidence_index,
    build_validated_observations,
)
from finresearch.finance.observations import read_evidence_jsonl  # noqa: E402
from finresearch.ingestion import load_verified_pages, validate_evidence_pages  # noqa: E402
from finresearch.verification.financial_artifacts import s2_source_files  # noqa: E402

TIMEZONE = ZoneInfo("Asia/Shanghai")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--attempt-id", required=True)
    args = parser.parse_args()
    output_dir = PROJECT_ROOT / "runs/s2" / args.attempt_id
    output_dir.mkdir(parents=True, exist_ok=False)

    dictionary_path = PROJECT_ROOT / "configs/s2/metric_dictionary.json"
    seeds_path = PROJECT_ROOT / "storage/s2/metric_observation_seeds.jsonl"
    manifest_path = PROJECT_ROOT / "storage/s0/document_manifest.jsonl"
    evidence_paths = [
        PROJECT_ROOT / "runs/s1/s1-live-q1-20260922-01/cases/S0-Q1-REVENUE/evidence.jsonl",
        PROJECT_ROOT / "runs/s1/s1-live-rest-20260922-01/cases/S0-Q2-OCF/evidence.jsonl",
        PROJECT_ROOT / "runs/s1/s1-live-retry-20260922-02/cases/S0-Q3-AR/evidence.jsonl",
    ]
    definitions = build_definition_index(read_json(dictionary_path))
    seeds = [MetricObservationSeed.model_validate(item) for item in read_jsonl(seeds_path)]
    manifest = read_jsonl(manifest_path)[0]
    actual_pdf_hash = file_hash(PROJECT_ROOT / manifest["local_path"])
    if actual_pdf_hash != manifest["sha256"]:
        raise ValueError("原PDF哈希与Manifest不一致，停止构造观察")
    evidence_index = build_evidence_index(read_evidence_jsonl(evidence_paths))
    registry = read_json(PROJECT_ROOT / "storage/s1/snapshot_registry.json")
    snapshot = next(s for s in registry["snapshots"] if s["snapshot_id"] == registry["active_snapshot_id"])
    pages = load_verified_pages(PROJECT_ROOT, snapshot, manifest)
    used_ids = {ref.evidence_id for seed in seeds for ref in seed.evidence_refs}
    validate_evidence_pages([evidence_index[eid] for eid in used_ids], pages)
    observations, reports = build_validated_observations(
        seeds,
        definitions,
        evidence_index,
        manifest=DocumentManifestRecord.model_validate(manifest),
    )
    all_passed = len(observations) == len(seeds) == 6 and all(item["passed"] for item in reports)
    with (output_dir / "observations.jsonl").open("w", encoding="utf-8", newline="\n") as target:
        for observation in observations:
            target.write(observation.model_dump_json() + "\n")
    (output_dir / "source_validation.json").write_text(
        json.dumps({"all_passed": all_passed, "records": reports}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output_dir / "inputs.lock.json").write_text(
        json.dumps(
            {
                "attempt_id": args.attempt_id,
                "created_at": datetime.now(TIMEZONE).isoformat(),
                "files": [
                    {"path": str(path.relative_to(PROJECT_ROOT)).replace("\\", "/"), "sha256": file_hash(path)}
                    for path in [dictionary_path, seeds_path, manifest_path, *evidence_paths,
                                 PROJECT_ROOT / "storage/s1/snapshot_registry.json",
                                 PROJECT_ROOT / snapshot["pages_path"], PROJECT_ROOT / snapshot["snapshot_manifest_path"],
                                 PROJECT_ROOT / manifest["local_path"],
                                 *s2_source_files(PROJECT_ROOT), Path(__file__)]
                ],
                "document_sha256_verified": True,
            },
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )
    (output_dir / "events.jsonl").write_text(
        json.dumps(
            {"time": datetime.now(TIMEZONE).isoformat(), "event": "observations_built", "count": len(observations), "all_passed": all_passed},
            ensure_ascii=False,
        ) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"attempt_id": args.attempt_id, "observation_count": len(observations), "all_passed": all_passed}, ensure_ascii=False))
    return 0 if all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
