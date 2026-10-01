"""生成S1不依赖模型的边界与完整性检查证据。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from pydantic import ValidationError  # noqa: E402

from finresearch.contracts import (  # noqa: E402
    DocumentManifestRecord,
    EvidenceAnswer,
    ResearchRequest,
    ToolRequest,
)
from finresearch.ingestion import PDFPageIngestor, load_verified_pages  # noqa: E402
from finresearch.retrieval import KeywordEvidenceRetriever  # noqa: E402
from finresearch.verification import validate_evidence_answer  # noqa: E402
from finresearch.workflow import PreflightFailure, prepare_single_document_request  # noqa: E402


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def check(name: str, passed: bool, actual: Any) -> dict[str, Any]:
    return {"name": name, "passed": passed, "actual": actual}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--attempt-id", required=True)
    args = parser.parse_args()
    output_dir = PROJECT_ROOT / "runs/s1" / args.attempt_id
    output_dir.mkdir(parents=True, exist_ok=False)

    scope = read_json(PROJECT_ROOT / "configs/s0/scope.json")
    base = read_json(PROJECT_ROOT / "configs/s0/request_examples.json")["valid"][0]
    manifest = read_jsonl(PROJECT_ROOT / "storage/s0/document_manifest.jsonl")
    registry = read_json(PROJECT_ROOT / "storage/s1/snapshot_registry.json")
    retrieval_config = read_json(PROJECT_ROOT / "configs/s1/retrieval_terms.json")
    snapshot = next(s for s in registry["snapshots"] if s["snapshot_id"] == registry["active_snapshot_id"])
    pages_path = PROJECT_ROOT / snapshot["pages_path"]
    document = next(m for m in manifest if m["document_id"] == snapshot["document_id"])
    document_id = document["document_id"]
    retriever = KeywordEvidenceRetriever(retrieval_config)
    results: list[dict[str, Any]] = []

    for case_name, patch, expected_code in [
        ("date_before_disclosure", {"as_of_date": "2025-04-24"}, "NOT_AVAILABLE_AS_OF"),
        ("company_mismatch", {"company_ids": ["688082.SH"]}, "OUT_OF_SCOPE"),
    ]:
        load_count = 0

        def loader() -> list[Any]:
            nonlocal load_count
            load_count += 1
            return []

        try:
            prepare_single_document_request(
                ResearchRequest.model_validate({**base, **patch}),
                scope=scope,
                manifest_records=manifest,
                allowed_document_id=document_id,
                snapshot_record=snapshot,
                page_loader=loader,
                retriever=retriever,
            )
            actual_code = "NO_ERROR"
        except PreflightFailure as exc:
            actual_code = exc.code
        results.append(
            check(
                case_name,
                actual_code == expected_code and load_count == 0,
                {"code": actual_code, "page_load_count": load_count, "model_call_count": 0},
            )
        )

    no_match_load_count = 0

    def no_match_loader() -> list[Any]:
        nonlocal no_match_load_count
        no_match_load_count += 1
        return load_verified_pages(PROJECT_ROOT, snapshot, document)

    try:
        prepare_single_document_request(
            ResearchRequest.model_validate(
                {**base, "request_id": "s1-no-match", "question": "董事长的出生月份是什么？"}
            ),
            scope=scope,
            manifest_records=manifest,
            allowed_document_id=document_id,
            snapshot_record=snapshot,
            page_loader=no_match_loader,
            retriever=retriever,
        )
        no_match_code = "NO_ERROR"
    except PreflightFailure as exc:
        no_match_code = exc.code
    results.append(
        check(
            "no_matching_rule_stops_before_model",
            no_match_code == "INSUFFICIENT_EVIDENCE",
            {"code": no_match_code, "page_load_count": no_match_load_count, "model_call_count": 0},
        )
    )

    offline = read_json(PROJECT_ROOT / "runs/s1/s1-offline-20260922-02/retrieval_results.json")
    known = offline["cases"][0]["retrieval"]["evidence_candidates"]
    from finresearch.contracts import EvidenceCandidate  # local import keeps CLI startup clear

    candidates = [EvidenceCandidate.model_validate(item) for item in known]
    forged = EvidenceAnswer.model_validate(
        {
            "status": "ANSWERABLE",
            "answer": "营业收入为29,838,069,162.26元。",
            "evidence_refs": [
                {"evidence_id": "forged-evidence-id", "document_id": document_id, "pdf_page": 95}
            ],
            "period": "FY2024",
            "unit": "CNY yuan",
        }
    )
    forged_validation = validate_evidence_answer(
        forged, candidates, expected_company_id="002371.SZ", expected_period="FY2024", expected_unit="CNY yuan"
    )
    results.append(check("forged_reference_rejected", forged_validation.validation_status == "FAIL", forged_validation.model_dump(mode="json")))

    try:
        ToolRequest.model_validate({"tool_name": "read_any_path", "arguments": {"document_id": document_id}})
        unknown_tool_rejected = False
    except ValidationError:
        unknown_tool_rejected = True
    results.append(check("unknown_tool_and_path_rejected", unknown_tool_rejected, {"model_supplied_path_supported": False}))

    try:
        DocumentManifestRecord.model_validate({**manifest[0], "local_path": "../../outside.pdf"})
        traversal_rejected = False
    except ValidationError:
        traversal_rejected = True
    results.append(check("manifest_path_traversal_rejected", traversal_rejected, "../../outside.pdf"))

    bad_manifest = {**manifest[0], "sha256": "0" * 64}
    with TemporaryDirectory() as temporary:
        try:
            PDFPageIngestor(PROJECT_ROOT, Path(temporary)).import_document(bad_manifest)
            hash_rejected = False
        except ValueError as exc:
            hash_rejected = "SHA-256" in str(exc)
    results.append(check("document_hash_mismatch_rejected", hash_rejected, "rejected_before_extraction"))

    ingestor = PDFPageIngestor(PROJECT_ROOT, PROJECT_ROOT / "storage/s1/snapshots")
    first = ingestor.import_document(manifest[0])
    second = ingestor.import_document(manifest[0])
    results.append(
        check(
            "snapshot_import_idempotent",
            first.reused_existing and second.reused_existing and first.snapshot_id == second.snapshot_id and first.pages_sha256 == second.pages_sha256,
            {"snapshot_id": second.snapshot_id, "pages_sha256": second.pages_sha256, "page_count": second.page_count},
        )
    )
    payload = {"attempt_id": args.attempt_id, "all_passed": all(item["passed"] for item in results), "checks": results}
    (output_dir / "boundary_checks.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if payload["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
