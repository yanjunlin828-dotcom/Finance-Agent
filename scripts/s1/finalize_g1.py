"""从不可变S1运行产物计算G1门禁，不凭计划文字宣告通过。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from finresearch.gates import evaluate_s1_gate  # noqa: E402
from finresearch.contracts import EvidenceAnswer, ResearchRequest  # noqa: E402
from finresearch.contracts.research_request import validate_request_against_scope, select_available_documents  # noqa: E402
from finresearch.finance.observations import read_evidence_jsonl  # noqa: E402
from finresearch.ingestion import load_verified_pages, validate_evidence_pages  # noqa: E402
from finresearch.verification import validate_evidence_answer, validate_case_truth  # noqa: E402
from finresearch.model import conservative_cost  # noqa: E402

LIVE_SOURCES = {
    "S0-Q1-REVENUE": "s1-live-q1-20260922-01",
    "S0-Q2-OCF": "s1-live-rest-20260922-01",
    "S0-Q3-AR": "s1-live-retry-20260922-02",
    "S0-Q4-BUSINESS": "s1-live-rest-20260922-01",
    "S0-Q5-OCF-EXPLANATION": "s1-live-q5-20260922-03",
}
ALL_LIVE_ATTEMPTS = [
    "s1-live-q1-20260922-01",
    "s1-live-rest-20260922-01",
    "s1-live-retry-20260922-02",
    "s1-live-q5-20260922-03",
]


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def row(gate_id: str, passed: bool, evidence: Any) -> dict[str, Any]:
    return {"gate_id": gate_id, "status": "PASS" if passed else "FAIL", "evidence": evidence}


def reconcile_call_audits(root: Path, call_count: int, known_cost: str, config: dict) -> bool:
    """File presence alone does not prove cost completeness or recorded retries."""
    try:
        audits = read_jsonl(root / "model_attempts.jsonl")
        if len(audits) != call_count or not audits:
            return False
        if [a["budget_reservation"]["total_calls"] for a in audits] != list(range(1, call_count + 1)):
            return False
        costs = []
        for audit in audits:
            cost = conservative_cost(audit["usage"], config)
            if cost is None or audit["estimated_cost_usd"] is None or Decimal(cost) != Decimal(audit["estimated_cost_usd"]):
                return False
            costs.append(Decimal(cost))
        return sum(costs, Decimal("0")) == Decimal(known_cost)
    except (OSError, ValueError, KeyError, TypeError):
        return False


def revalidate_case(case_dir: Path, case_id: str, pages: list, manifest: list[dict], scope: dict, requirements: dict, cases: dict) -> dict:
    """Recheck the response using current rules; saved truth flags are not approval."""
    request = ResearchRequest.model_validate(read_json(case_dir / "request.json"))
    validate_request_against_scope(request, scope)
    if not select_available_documents(request, manifest, {p.document_id for p in pages}):
        raise ValueError("回答来源在请求截止日不可用")
    evidence = read_evidence_jsonl([case_dir / "evidence.jsonl"])
    validate_evidence_pages(evidence, pages)
    answer = EvidenceAnswer.model_validate(read_json(case_dir / "model_response.json")["parsed"])
    rule = requirements[case_id]
    validation = validate_evidence_answer(answer, evidence, expected_company_id=request.company_ids[0], expected_period=rule["expected_period"], expected_unit=rule["expected_unit"])
    truth = validate_case_truth(answer, validation, {**rule, "forbidden_claims": cases[case_id].get("forbidden_claims", [])})
    return {"deterministic": validation.model_dump(mode="json"), "case_truth": truth}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--offline-attempt", default="s1-offline-20260922-02")
    parser.add_argument("--boundary-attempt", default="s1-boundary-20260922-01")
    args = parser.parse_args()
    output_dir = PROJECT_ROOT / "runs/s1" / args.attempt_id
    output_dir.mkdir(parents=True, exist_ok=False)

    registry = read_json(PROJECT_ROOT / "storage/s1/snapshot_registry.json")
    snapshot = next(s for s in registry["snapshots"] if s["snapshot_id"] == registry["active_snapshot_id"])
    manifest = [json.loads(line) for line in (PROJECT_ROOT / "storage/s0/document_manifest.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    document = next(m for m in manifest if m["document_id"] == snapshot["document_id"])
    pages = load_verified_pages(PROJECT_ROOT, snapshot, document)
    scope = read_json(PROJECT_ROOT / "configs/s0/scope.json")
    requirements = read_json(PROJECT_ROOT / "evals/dev/s1_answer_requirements.json")["cases"]
    cases = {c["case_id"]: c for c in [json.loads(line) for line in (PROJECT_ROOT / "evals/dev/s0_cases.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]}
    pages_path = PROJECT_ROOT / snapshot["pages_path"]
    snapshot_manifest = read_json(PROJECT_ROOT / snapshot["snapshot_manifest_path"])
    boundary = read_json(PROJECT_ROOT / "runs/s1" / args.boundary_attempt / "boundary_checks.json")
    boundary_by_name = {item["name"]: item for item in boundary["checks"]}
    offline = read_json(PROJECT_ROOT / "runs/s1" / args.offline_attempt / "metrics.json")
    offline_details = read_json(PROJECT_ROOT / "runs/s1" / args.offline_attempt / "retrieval_results.json")
    negative = read_json(PROJECT_ROOT / "runs/s1/s1-negative-live-20260922-01/negative_live.json")

    selected_validations: dict[str, dict[str, Any]] = {}
    artifact_results: dict[str, Any] = {}
    required_case_files = {
        "request.json", "retrieval.json", "evidence.jsonl", "model_response.json",
        "validation.json", "report.md", "events.jsonl",
    }
    for case_id, attempt in LIVE_SOURCES.items():
        case_dir = PROJECT_ROOT / "runs/s1" / attempt / "cases" / case_id
        validation = revalidate_case(case_dir, case_id, pages, manifest, scope, requirements, cases)
        selected_validations[case_id] = validation
        actual_files = {item.name for item in case_dir.iterdir() if item.is_file()}
        artifact_results[case_id] = {
            "attempt": attempt,
            "missing": sorted(required_case_files - actual_files),
            "deterministic": validation["deterministic"]["validation_status"],
            "truth_passed": validation["case_truth"]["passed"],
        }

    all_calls = 0
    all_cost = Decimal("0")
    live_attempt_summaries: dict[str, Any] = {}
    for attempt in ALL_LIVE_ATTEMPTS:
        summary = read_json(PROJECT_ROOT / "runs/s1" / attempt / "summary.json")
        live_attempt_summaries[attempt] = {
            "calls": summary["total_calls"],
            "estimated_cost_usd": summary["estimated_cost_usd"],
        }
        all_calls += int(summary["total_calls"])
        all_cost += Decimal(summary["estimated_cost_usd"])
    all_calls += int(negative["call_count"])
    negative_cost = negative["audit"].get("estimated_cost_usd")
    if negative_cost:
        all_cost += Decimal(negative_cost)

    scan_roots = [PROJECT_ROOT / "runs/s1" / attempt for attempt in [*ALL_LIVE_ATTEMPTS, "s1-negative-live-20260922-01"]]
    # A legacy summary cannot prove that failed attempts and their cost were captured.
    model_config = read_json(PROJECT_ROOT / "configs/s1/model.json")
    complete_call_audits = all(reconcile_call_audits(PROJECT_ROOT / "runs/s1" / attempt,
        int(live_attempt_summaries[attempt]["calls"]), live_attempt_summaries[attempt]["estimated_cost_usd"], model_config)
        for attempt in ALL_LIVE_ATTEMPTS)
    complete_call_audits = complete_call_audits and reconcile_call_audits(
        PROJECT_ROOT / "runs/s1/s1-negative-live-20260922-01", int(negative["call_count"]),
        negative.get("estimated_cost_usd", negative_cost or "0"), model_config)
    credential = os.environ.get("DEEPSEEK_API_KEY", "")
    leak_files: list[str] = []
    suspicious = re.compile(r"(?i)(authorization\s*[:=]\s*bearer|sk-[a-z0-9]{16,}|api[_-]?key\s*[:=]\s*[^\[])")
    for root in scan_roots:
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if (credential and credential in text) or suspicious.search(text):
                leak_files.append(str(path.relative_to(PROJECT_ROOT)).replace("\\", "/"))

    retrieval_cases = offline_details["cases"]
    five_answer_pass = all(
        item["deterministic"]["validation_status"] == "PASS" and item["case_truth"]["passed"]
        for item in selected_validations.values()
    )
    checks = [
        row(
            "G1-D1",
            snapshot["page_count"] == 190
            and snapshot_manifest["page_count"] == 190
            and sha256(pages_path) == snapshot["pages_sha256"] == snapshot_manifest["pages_sha256"],
            {"snapshot_id": snapshot["snapshot_id"], "page_count": snapshot["page_count"], "pages_sha256": snapshot["pages_sha256"]},
        ),
        row("G1-D2", boundary_by_name["snapshot_import_idempotent"]["passed"], boundary_by_name["snapshot_import_idempotent"]["actual"]),
        row("G1-R1", offline["all_cases_passed"] and offline["necessary_page_recall_at_k"] == {"numerator": 6, "denominator": 6}, offline),
        row(
            "G1-R2",
            all(item["passed"] and not item["missing_expected_pages"] for item in retrieval_cases),
            {item["case_id"]: {"expected_pages": item["expected_pages"], "evidence_pages": item["evidence_pages"]} for item in retrieval_cases},
        ),
        row("G1-A1", five_answer_pass and len(selected_validations) == 5, artifact_results),
        row(
            "G1-A2",
            all(
                all(check["known"] and check["metadata_matches"] for check in item["deterministic"]["reference_checks"])
                for item in selected_validations.values()
            ),
            {case_id: item["deterministic"]["reference_checks"] for case_id, item in selected_validations.items()},
        ),
        row("G1-A3", five_answer_pass, {case_id: item["case_truth"]["passed"] for case_id, item in selected_validations.items()}),
        row(
            "G1-N1",
            boundary_by_name["no_matching_rule_stops_before_model"]["passed"] and negative["passed"],
            {"offline": boundary_by_name["no_matching_rule_stops_before_model"]["actual"], "live": negative["checks"]},
        ),
        row(
            "G1-N2",
            boundary_by_name["date_before_disclosure"]["passed"] and boundary_by_name["company_mismatch"]["passed"],
            {"date": boundary_by_name["date_before_disclosure"]["actual"], "company": boundary_by_name["company_mismatch"]["actual"]},
        ),
        row(
            "G1-N3",
            all(boundary_by_name[name]["passed"] for name in ["forged_reference_rejected", "unknown_tool_and_path_rejected", "manifest_path_traversal_rejected", "document_hash_mismatch_rejected"])
            and negative["passed"],
            {"boundary_attempt": boundary["attempt_id"], "negative_live_attempt": negative["attempt_id"]},
        ),
        row(
            "G1-O1",
            not leak_files and all(not item["missing"] for item in artifact_results.values()),
            {"case_artifacts": artifact_results, "sensitive_scan_matches": leak_files},
        ),
        row(
            "G1-O2",
            complete_call_audits and all_calls <= 10 and all_cost <= Decimal("0.10"),
            {"historical_recorded_calls": all_calls, "known_historical_estimated_cost_usd": str(all_cost), "cost_completeness_verified": complete_call_audits, "limit_usd": "0.10", "attempts": live_attempt_summaries},
        ),
        row(
            "G1-H1",
            (PROJECT_ROOT / "docs/stages/s1/handoff_to_s2_s3.md").is_file()
            and len(read_jsonl(PROJECT_ROOT / "evals/dev/s1_cases.jsonl")) == 20,
            {"handoff": "docs/stages/s1/handoff_to_s2_s3.md", "development_case_count": 20},
        ),
    ]
    report = evaluate_s1_gate(args.attempt_id, checks)
    (output_dir / "gate_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output_dir / "inputs.lock.json").write_text(
        json.dumps(
            {
                "offline_attempt": args.offline_attempt,
                "boundary_attempt": args.boundary_attempt,
                "negative_live_attempt": "s1-negative-live-20260922-01",
                "selected_live_sources": LIVE_SOURCES,
                "revalidation_mode": "OFFLINE_SAVED_MODEL_RESPONSES",
                "online_calls_this_attempt": 0,
                "files": [{"path": p.relative_to(PROJECT_ROOT).as_posix(), "sha256": sha256(p)} for p in [
                    *sorted((PROJECT_ROOT / "src/finresearch").rglob("*.py")), Path(__file__),
                    PROJECT_ROOT / "storage/s0/document_manifest.jsonl", PROJECT_ROOT / "storage/s1/snapshot_registry.json",
                    pages_path, PROJECT_ROOT / snapshot["snapshot_manifest_path"],
                    PROJECT_ROOT / "evals/dev/s1_answer_requirements.json", PROJECT_ROOT / "evals/dev/s0_cases.jsonl",
                    PROJECT_ROOT / "configs/s0/scope.json", PROJECT_ROOT / "runs/s1" / args.offline_attempt / "retrieval_results.json",
                    PROJECT_ROOT / "runs/s1" / args.boundary_attempt / "boundary_checks.json",
                    *[p for root in scan_roots for p in sorted(root.rglob("*")) if p.is_file() and p.suffix in {".json", ".jsonl"}]]],
            },
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"attempt_id": args.attempt_id, "decision": report["overall_decision"], "online_calls_this_attempt": 0, "historical_recorded_calls": all_calls, "known_historical_estimated_cost_usd": str(all_cost)}, ensure_ascii=False))
    return 0 if report["overall_decision"] == "GO" else 1


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


if __name__ == "__main__":
    raise SystemExit(main())
