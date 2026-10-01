"""根据真实S0证据计算G0门禁，禁止用静态PASS替代检查。"""

from __future__ import annotations

from typing import Any


def evaluate_s0_gate(
    *,
    attempt_id: str,
    generated_at: str,
    probe_results: list[dict[str, Any]],
    manifest: list[dict[str, Any]],
    metric_dictionary: dict[str, Any],
    cases: list[dict[str, Any]],
    split_policy: dict[str, Any],
    environment: dict[str, Any],
    handoff_ready: bool,
) -> dict[str, Any]:
    """计算G0检查与阶段裁决。

    该函数不读取文件、不调用模型，也不修改复核状态。调用方必须传入本次锁定的
    真实输入。任何探针失败都会得到NO_GO；本地项通过但外部项缺失时为LOCAL_ONLY。
    """

    probes = {result["probe_id"]: result for result in probe_results}
    question_cases = [case for case in cases if case.get("case_id", "").startswith("S0-Q")]
    boundary_cases = [case for case in cases if case.get("case_id", "").startswith("S0-B")]

    metric_statuses = {
        item.get("metric_id"): item.get("definition_status")
        for item in metric_dictionary.get("metrics", []) + metric_dictionary.get("unresolved_metrics", [])
    }
    referenced_metric_ids = {
        fact["metric_id"]
        for case in question_cases
        for fact in case.get("expected_facts", [])
        if "metric_id" in fact
    }
    allowed_definition_statuses = {"PROPOSED", "CONFIRMED", "UNRESOLVED"}
    c2_ok = referenced_metric_ids.issubset(metric_statuses) and all(
        status in allowed_definition_statuses for status in metric_statuses.values()
    )

    d2_ok = bool(manifest) and all(
        row.get("published_on")
        and row.get("date_evidence_ref")
        and row.get("date_status") == "OFFICIAL_EXCHANGE_SOURCE_CHECKED"
        and row.get("version_status")
        for row in manifest
    )
    e2_ok = (
        len(boundary_cases) >= 6
        and split_policy.get("development_policy", {}).get("grouping_key") == "fact_group_id"
        and bool(split_policy.get("future_holdout", {}).get("reserved_company_ids"))
    )

    if len(question_cases) != 5:
        e1_status = f"FAIL_EXPECTED_5_CASES_FOUND_{len(question_cases)}"
    elif all(case.get("review_status") == "HUMAN_VERIFIED" for case in question_cases):
        e1_status = "PASS"
    else:
        e1_status = "BLOCKED_HUMAN_VERIFICATION_REQUIRED"

    checks = {
        "G0-D1": _probe_status(probes, "T01"),
        "G0-D2": "PASS_WITH_LIMITATION_BINARY_IDENTITY_NOT_PROVEN" if d2_ok else "FAIL",
        "G0-D3": _probe_status(probes, "T02"),
        "G0-C1": _probe_status(probes, "T03"),
        "G0-C2": "PASS" if c2_ok else "FAIL",
        "G0-E1": e1_status,
        "G0-E2": "PASS" if e2_ok else "FAIL",
        "G0-L1": "PASS" if environment.get("isolated_environment") else "PARTIAL_NO_ISOLATED_LOCKED_ENVIRONMENT",
        "G0-L2": _probe_status(probes, "T04"),
        "G0-M1": _probe_status(probes, "T05"),
        "G0-M2": _combined_probe_status(probes, ("T06", "T07")),
        "G0-M3": _probe_status(probes, "T08"),
        "G0-H1": "NOT_READY",
    }

    prerequisites_for_handoff = all(
        _is_pass(checks[key])
        for key in (
            "G0-D1",
            "G0-D2",
            "G0-D3",
            "G0-C1",
            "G0-C2",
            "G0-E1",
            "G0-E2",
            "G0-L1",
            "G0-L2",
            "G0-M1",
            "G0-M2",
            "G0-M3",
        )
    )
    if handoff_ready and prerequisites_for_handoff:
        checks["G0-H1"] = "PASS"

    if any(value.startswith("FAIL") for value in checks.values()):
        overall = "NO_GO"
    elif all(_is_pass(value) for value in checks.values()):
        overall = "GO"
    else:
        overall = "LOCAL_ONLY"

    open_items = [key for key, value in checks.items() if not _is_pass(value)]
    return {
        "attempt_id": attempt_id,
        "generated_at": generated_at,
        "overall_decision": overall,
        "checks": checks,
        "evidence_summary": {
            "probe_statuses": {key: value.get("status") for key, value in probes.items()},
            "question_case_count": len(question_cases),
            "human_verified_question_count": sum(
                case.get("review_status") == "HUMAN_VERIFIED" for case in question_cases
            ),
            "boundary_case_count": len(boundary_cases),
            "referenced_metric_ids": sorted(referenced_metric_ids),
            "isolated_environment": bool(environment.get("isolated_environment")),
        },
        "open_items": open_items,
        "reason": _reason(overall, open_items),
    }


def _probe_status(probes: dict[str, dict[str, Any]], probe_id: str) -> str:
    result = probes.get(probe_id)
    if result is None:
        return "NOT_RUN"
    return "PASS" if result.get("status") == "PASS" else "FAIL"


def _combined_probe_status(probes: dict[str, dict[str, Any]], probe_ids: tuple[str, ...]) -> str:
    statuses = [_probe_status(probes, probe_id) for probe_id in probe_ids]
    if any(status == "FAIL" for status in statuses):
        return "FAIL"
    if all(status == "PASS" for status in statuses):
        return "PASS"
    return "NOT_RUN"


def _is_pass(status: str) -> bool:
    return status == "PASS" or status.startswith("PASS_WITH_LIMITATION")


def _reason(overall: str, open_items: list[str]) -> str:
    if overall == "GO":
        return "所有G0门禁均由输入证据计算为通过"
    if overall == "NO_GO":
        return f"存在失败门禁：{', '.join(open_items)}"
    return f"本地工作可继续，但以下门禁尚未通过：{', '.join(open_items)}"
