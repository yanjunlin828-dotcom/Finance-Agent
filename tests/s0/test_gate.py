from __future__ import annotations

from copy import deepcopy

from finresearch.gates import evaluate_s0_gate


def base_inputs() -> dict:
    results = [{"probe_id": probe_id, "status": "PASS"} for probe_id in ("T01", "T02", "T03", "T04")]
    cases = [
        {
            "case_id": f"S0-Q{i}",
            "review_status": "SOURCE_CHECKED",
            "expected_facts": [{"metric_id": "revenue"}] if i == 1 else [],
        }
        for i in range(1, 6)
    ] + [{"case_id": f"S0-B{i}", "review_status": "SOURCE_CHECKED"} for i in range(1, 7)]
    return {
        "attempt_id": "test-attempt",
        "generated_at": "2026-09-22T00:00:00+08:00",
        "probe_results": results,
        "manifest": [
            {
                "published_on": "2025-04-25",
                "date_evidence_ref": "https://example.com/official.pdf",
                "date_status": "OFFICIAL_EXCHANGE_SOURCE_CHECKED",
                "version_status": "CHECKED",
            }
        ],
        "metric_dictionary": {
            "metrics": [{"metric_id": "revenue", "definition_status": "CONFIRMED"}],
            "unresolved_metrics": [],
        },
        "cases": cases,
        "split_policy": {
            "development_policy": {"grouping_key": "fact_group_id"},
            "future_holdout": {"reserved_company_ids": ["688012.SH"]},
        },
        "environment": {"isolated_environment": False},
        "handoff_ready": False,
    }


def test_local_gate_is_computed_as_local_only() -> None:
    report = evaluate_s0_gate(**base_inputs())
    assert report["overall_decision"] == "LOCAL_ONLY"
    assert report["checks"]["G0-E1"] == "BLOCKED_HUMAN_VERIFICATION_REQUIRED"
    assert report["checks"]["G0-M1"] == "NOT_RUN"
    assert report["evidence_summary"]["boundary_case_count"] == 6


def test_missing_boundary_case_causes_no_go() -> None:
    payload = base_inputs()
    payload["cases"] = payload["cases"][:-1]
    report = evaluate_s0_gate(**payload)
    assert report["checks"]["G0-E2"] == "FAIL"
    assert report["overall_decision"] == "NO_GO"


def test_all_evidence_can_reach_go_without_static_override() -> None:
    payload = deepcopy(base_inputs())
    for case in payload["cases"][:5]:
        case["review_status"] = "HUMAN_VERIFIED"
    payload["environment"]["isolated_environment"] = True
    payload["probe_results"].extend(
        {"probe_id": probe_id, "status": "PASS"} for probe_id in ("T05", "T06", "T07", "T08")
    )
    payload["handoff_ready"] = True
    report = evaluate_s0_gate(**payload)
    assert report["overall_decision"] == "GO"
    assert all(value == "PASS" or value.startswith("PASS_WITH_LIMITATION") for value in report["checks"].values())
