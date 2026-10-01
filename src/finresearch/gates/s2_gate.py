"""S2门禁的纯函数裁决。"""

from __future__ import annotations

from typing import Any

REQUIRED_G2_IDS = {
    "G2-M1", "G2-M2", "G2-U1", "G2-C1", "G2-C2", "G2-C3",
    "G2-P1", "G2-S1", "G2-R1", "G2-O1", "G2-H1",
}


def evaluate_s2_gate(attempt_id: str, checks: list[dict[str, Any]]) -> dict[str, Any]:
    ids = [item["gate_id"] for item in checks]
    missing = sorted(REQUIRED_G2_IDS - set(ids))
    duplicates = sorted({item for item in ids if ids.count(item) > 1})
    passed = not missing and not duplicates and all(item["status"] == "PASS" for item in checks)
    return {
        "schema_version": "1.0.0",
        "attempt_id": attempt_id,
        "gate": "G2",
        "scope": "ONE_COMPANY_ONE_ANNUAL_REPORT_TWO_COMPARATIVE_PERIODS",
        "overall_decision": "GO" if passed else "NO_GO",
        "missing_gate_ids": missing,
        "duplicate_gate_ids": duplicates,
        "checks": checks,
    }
