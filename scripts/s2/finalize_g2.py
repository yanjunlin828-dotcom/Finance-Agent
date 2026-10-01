"""从S2真实产物计算G2门禁。"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from finresearch.verification.financial_artifacts import verify_s2_artifacts, file_sha, s2_source_files
from run_boundary_checks import collect_boundary_checks
from finresearch.finance import build_definition_index  # noqa: E402
from finresearch.gates import evaluate_s2_gate  # noqa: E402


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def gate(gate_id: str, passed: bool, evidence: Any) -> dict[str, Any]:
    return {"gate_id": gate_id, "status": "PASS" if passed else "FAIL", "evidence": evidence}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--observation-attempt", default="s2-observations-20260922-02")
    parser.add_argument("--comparison-attempt", default="s2-comparison-20260922-03")
    parser.add_argument("--boundary-attempt", default="s2-boundary-20260922-02")
    args = parser.parse_args()
    output_dir = PROJECT_ROOT / "runs/s2" / args.attempt_id
    output_dir.mkdir(parents=True, exist_ok=False)
    observation_attempt = args.observation_attempt
    comparison_attempt = args.comparison_attempt
    boundary_attempt = args.boundary_attempt
    observation_dir = PROJECT_ROOT / "runs/s2" / observation_attempt
    comparison_dir = PROJECT_ROOT / "runs/s2" / comparison_attempt
    boundary_dir = PROJECT_ROOT / "runs/s2" / boundary_attempt

    dictionary = read_json(PROJECT_ROOT / "configs/s2/metric_dictionary.json")
    definitions = build_definition_index(dictionary)
    observations = read_jsonl(observation_dir / "observations.jsonl")
    source_validation = read_json(observation_dir / "source_validation.json")
    comparison = read_json(comparison_dir / "comparison.json")
    summary = read_json(comparison_dir / "summary.json")
    replay = read_json(comparison_dir / "replay.json")
    boundary = read_json(boundary_dir / "boundary_checks.json")
    fresh_boundaries = collect_boundary_checks()
    fresh_by_name = {item["name"]: item for item in fresh_boundaries}
    boundary_by_name = {item["name"]: {**item, "passed": bool(item["passed"] and fresh_by_name.get(item["name"], {}).get("passed") and item["actual"] == fresh_by_name[item["name"]]["actual"])} for item in boundary["checks"]}
    verified = verify_s2_artifacts(PROJECT_ROOT, observation_dir, comparison_dir)
    corrections = read_json(PROJECT_ROOT / "storage/s2/manual_corrections.json")

    expected_values = {
        ("revenue", 2023): Decimal("22079458092.37"),
        ("revenue", 2024): Decimal("29838069162.26"),
        ("operating_cash_flow_net", 2023): Decimal("2365007734.54"),
        ("operating_cash_flow_net", 2024): Decimal("1573165054.27"),
        ("accounts_receivable", 2023): Decimal("3767378764.55"),
        ("accounts_receivable", 2024): Decimal("6044952733.01"),
    }
    actual_values = {(item["metric_id"], item["fiscal_year"]): Decimal(item["standard_value"]) for item in observations}
    calculations = comparison["calculations"]
    all_calculations_traceable = all(
        item["status"] == "VALID" and item["input_ids"] and item["formula_version"] == "1.1.0"
        for item in calculations.values()
    )
    required_artifacts = [
        observation_dir / "observations.jsonl", observation_dir / "source_validation.json", observation_dir / "inputs.lock.json",
        comparison_dir / "comparison.json", comparison_dir / "comparison_table.csv", comparison_dir / "comparison.md",
        comparison_dir / "replay.json", comparison_dir / "summary.json", comparison_dir / "inputs.lock.json",
        boundary_dir / "boundary_checks.json",
    ]
    credential = os.environ.get("DEEPSEEK_API_KEY", "")
    suspicious = re.compile(r"(?i)(authorization\s*[:=]\s*bearer|sk-[a-z0-9]{16,}|api[_-]?key\s*[:=]\s*[^\[])")
    leak_files: list[str] = []
    for path in required_artifacts:
        text = path.read_text(encoding="utf-8", errors="ignore")
        if (credential and credential in text) or suspicious.search(text):
            leak_files.append(str(path.relative_to(PROJECT_ROOT)).replace("\\", "/"))

    checks = [
        gate(
            "G2-M1",
            len(definitions) == 3
            and len(dictionary["formulas"]) == 4
            and all(item.metric_kind in {"FLOW", "STOCK"} and item.currency == "CNY" and item.statement_scope == "CONSOLIDATED" for item in definitions.values()),
            {"metric_ids": sorted(definitions), "formula_ids": [item["formula_id"] for item in dictionary["formulas"]]},
        ),
        gate(
            "G2-M2",
            len(observations) == 6 and source_validation["all_passed"] and verified["source_verified"] and actual_values == expected_values,
            {"observation_count": len(observations), "source_validation": source_validation["all_passed"], "values": {f"{key[0]}:{key[1]}": str(value) for key, value in actual_values.items()}},
        ),
        gate(
            "G2-U1",
            all(boundary_by_name[name]["passed"] for name in ["ten_thousand_yuan_exact", "hundred_million_yuan_exact", "unknown_unit_rejected", "missing_not_zero"]),
            {name: boundary_by_name[name]["actual"] for name in ["ten_thousand_yuan_exact", "hundred_million_yuan_exact", "unknown_unit_rejected", "missing_not_zero"]},
        ),
        gate("G2-C1", len(calculations) == 8 and all_calculations_traceable and verified["calculations_recomputed"] and verified["database_replayed"], {"calculation_count": len(calculations), "independent_revalidation": verified}),
        gate(
            "G2-C2",
            all(boundary_by_name[name]["passed"] for name in ["positive_growth", "decline_growth", "zero_denominator", "negative_base", "missing_input"]),
            {name: boundary_by_name[name]["actual"] for name in ["positive_growth", "decline_growth", "zero_denominator", "negative_base", "missing_input"]},
        ),
        gate(
            "G2-C3",
            all(boundary_by_name[name]["passed"] for name in ["currency_conflict", "scope_conflict", "period_conflict", "stock_flow_period_conflict"]),
            {name: boundary_by_name[name]["actual"] for name in ["currency_conflict", "scope_conflict", "period_conflict", "stock_flow_period_conflict"]},
        ),
        gate("G2-P1", boundary_by_name["synthetic_correction_pit"]["passed"], boundary_by_name["synthetic_correction_pit"]["actual"]),
        gate("G2-S1", boundary_by_name["atomic_batch_rollback"]["passed"] and verified["database_replayed"], {"atomic": boundary_by_name["atomic_batch_rollback"]["actual"], "database_counts": summary["database_counts"]}),
        gate(
            "G2-R1",
            verified["calculations_recomputed"] and verified["database_replayed"] and len(comparison["comparison_rows"]) == 5,
            {"comparison_rows": len(comparison["comparison_rows"]), "descriptive_flags": comparison["descriptive_flags"], "scope_limit": comparison["scope_limit"]},
        ),
        gate(
            "G2-O1",
            all(path.is_file() and path.stat().st_size > 0 for path in required_artifacts) and not leak_files and corrections["records"] == [],
            {"required_artifact_count": len(required_artifacts), "sensitive_scan_matches": leak_files, "manual_correction_count": len(corrections["records"])},
        ),
        gate(
            "G2-H1",
            (PROJECT_ROOT / "docs/stages/s2/handoff_to_s3_s4.md").is_file(),
            {"handoff": "docs/stages/s2/handoff_to_s3_s4.md"},
        ),
    ]
    report = evaluate_s2_gate(args.attempt_id, checks)
    (output_dir / "gate_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output_dir / "inputs.lock.json").write_text(
        json.dumps(
            {
                "files": [{"path": p.relative_to(PROJECT_ROOT).as_posix(), "sha256": file_sha(p)} for p in [*required_artifacts, comparison_dir / "metric_store.sqlite3", *s2_source_files(PROJECT_ROOT), Path(__file__), Path(__file__).with_name("run_boundary_checks.py")]],
                "observation_attempt": observation_attempt,
                "comparison_attempt": comparison_attempt,
                "boundary_attempt": boundary_attempt,
                "limitations": [
                    "真实范围仅一家公司、一份年报、两个比较期间",
                    "PIT更正链为合成测试，不代表真实更正公告已穷尽",
                    "2023比较值为SOURCE_CHECKED",
                ],
            },
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"attempt_id": args.attempt_id, "decision": report["overall_decision"], "gate_count": len(checks)}, ensure_ascii=False))
    return 0 if report["overall_decision"] == "GO" else 1


if __name__ == "__main__":
    raise SystemExit(main())
