"""在真实逐页快照上评测S1五题关键词检索，不调用模型。"""

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

from finresearch.ingestion import load_verified_pages  # noqa: E402
from finresearch.retrieval import KeywordEvidenceRetriever  # noqa: E402

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


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--attempt-id", required=True)
    args = parser.parse_args()
    output_dir = PROJECT_ROOT / "runs" / "s1" / args.attempt_id
    output_dir.mkdir(parents=True, exist_ok=False)

    config_path = PROJECT_ROOT / "configs" / "s1" / "retrieval_terms.json"
    registry_path = PROJECT_ROOT / "storage" / "s1" / "snapshot_registry.json"
    cases_path = PROJECT_ROOT / "evals" / "dev" / "s0_cases.jsonl"
    config = read_json(config_path)
    registry = read_json(registry_path)
    snapshot = next(s for s in registry["snapshots"] if s["snapshot_id"] == registry["active_snapshot_id"])
    pages_path = PROJECT_ROOT / snapshot["pages_path"]
    manifest = [json.loads(line) for line in (PROJECT_ROOT / "storage/s0/document_manifest.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    document = next(m for m in manifest if m["document_id"] == snapshot["document_id"])
    pages = load_verified_pages(PROJECT_ROOT, snapshot, document)
    cases = [row for row in read_jsonl(cases_path) if row["case_id"].startswith("S0-Q")]
    retriever = KeywordEvidenceRetriever(config)
    case_results: list[dict[str, Any]] = []
    all_evidence: dict[str, dict[str, Any]] = {}
    for case in cases:
        result = retriever.retrieve(case["case_id"], case["question"], pages)
        top_pages = [item.pdf_page for item in result.page_scores if item.score > 0][: config["top_k_pages"]]
        expected_pages = sorted({locator["pdf_page"] for locator in case.get("evidence_locators", [])})
        missing_pages = [page for page in expected_pages if page not in top_pages]
        evidence_pages = sorted({item.pdf_page for item in result.evidence_candidates})
        passed = result.status == "FOUND" and not missing_pages and all(
            page in evidence_pages for page in expected_pages
        )
        case_results.append(
            {
                "case_id": case["case_id"],
                "question": case["question"],
                "rule_id": result.rule_id,
                "status": result.status,
                "expected_pages": expected_pages,
                "top_pages": top_pages,
                "evidence_pages": evidence_pages,
                "missing_expected_pages": missing_pages,
                "passed": passed,
                "retrieval": result.model_dump(mode="json"),
            }
        )
        for evidence in result.evidence_candidates:
            all_evidence[evidence.evidence_id] = evidence.model_dump(mode="json")

    inputs_lock = {
        "attempt_id": args.attempt_id,
        "created_at": datetime.now(TIMEZONE).isoformat(),
        "files": [
            {"path": str(path.relative_to(PROJECT_ROOT)).replace("\\", "/"), "sha256": file_hash(path)}
            for path in [config_path, registry_path, cases_path, pages_path, PROJECT_ROOT / "storage/s0/document_manifest.jsonl", PROJECT_ROOT / snapshot["snapshot_manifest_path"], PROJECT_ROOT / document["local_path"], *sorted((PROJECT_ROOT / "src/finresearch").rglob("*.py")), Path(__file__)]
        ],
        "snapshot_id": snapshot["snapshot_id"],
        "document_id": snapshot["document_id"],
    }
    metrics = {
        "case_count": len(case_results),
        "top_k": config["top_k_pages"],
        "necessary_page_recall_at_k": {
            "numerator": sum(
                len(result["expected_pages"]) - len(result["missing_expected_pages"]) for result in case_results
            ),
            "denominator": sum(len(result["expected_pages"]) for result in case_results),
        },
        "all_cases_passed": all(result["passed"] for result in case_results),
    }
    write_json(output_dir / "inputs.lock.json", inputs_lock)
    write_json(output_dir / "retrieval_results.json", {"attempt_id": args.attempt_id, "cases": case_results})
    write_json(output_dir / "metrics.json", metrics)
    with (output_dir / "evidence.jsonl").open("w", encoding="utf-8", newline="\n") as target:
        for evidence in all_evidence.values():
            target.write(json.dumps(evidence, ensure_ascii=False) + "\n")
    (output_dir / "events.jsonl").write_text(
        json.dumps(
            {
                "time": datetime.now(TIMEZONE).isoformat(),
                "event": "offline_retrieval_finished",
                "all_cases_passed": metrics["all_cases_passed"],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"attempt_id": args.attempt_id, "metrics": metrics}, ensure_ascii=False))
    return 0 if metrics["all_cases_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
