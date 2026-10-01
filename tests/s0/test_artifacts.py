from __future__ import annotations

import csv
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_json_artifacts_are_parseable() -> None:
    paths = [
        PROJECT_ROOT / "configs/s0/scope.json",
        PROJECT_ROOT / "configs/s0/model_probe.json",
        PROJECT_ROOT / "configs/s0/metric_dictionary.json",
        PROJECT_ROOT / "configs/s0/request_examples.json",
        PROJECT_ROOT / "protocols/revenue_quality_v0.json",
        PROJECT_ROOT / "evals/split_policy.json",
    ]
    for path in paths:
        assert json.loads(path.read_text(encoding="utf-8"))


def test_case_ids_are_unique_and_human_review_has_audit_record() -> None:
    path = PROJECT_ROOT / "evals/dev/s0_cases.jsonl"
    cases = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    ids = [case["case_id"] for case in cases]
    assert len(ids) == len(set(ids))
    assert sum(case["case_id"].startswith("S0-Q") for case in cases) == 5
    question_cases = [case for case in cases if case["case_id"].startswith("S0-Q")]
    assert all(case["review_status"] == "HUMAN_VERIFIED" for case in question_cases)
    assert all(case["review_record"]["reviewer_type"] == "USER_CONFIRMATION_IN_CHAT" for case in question_cases)
    assert all(case["review_record"]["human_reviewer"] == "user" for case in question_cases)


def test_coverage_does_not_overstate_target_scope() -> None:
    with (PROJECT_ROOT / "storage/s0/coverage.csv").open(encoding="utf-8", newline="") as source:
        rows = list(csv.DictReader(source))
    assert len(rows) == 9
    acquired = [row for row in rows if row["coverage_status"] == "ACQUIRED_AND_SAMPLED"]
    assert [(row["ts_code"], row["reporting_year"]) for row in acquired] == [("002371.SZ", "2024")]
