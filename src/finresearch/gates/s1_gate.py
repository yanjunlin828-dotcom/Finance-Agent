"""S1门禁的纯函数裁决。"""

from __future__ import annotations

from typing import Any

REQUIRED_G1_IDS = {
    "G1-D1", "G1-D2", "G1-R1", "G1-R2", "G1-A1", "G1-A2", "G1-A3",
    "G1-N1", "G1-N2", "G1-N3", "G1-O1", "G1-O2", "G1-H1",
}


def evaluate_s1_gate(attempt_id: str, checks: list[dict[str, Any]]) -> dict[str, Any]:
    """要求所有硬门禁存在且通过；不允许用平均分掩盖边界失败。"""

    ids = {item["gate_id"] for item in checks}
    missing = sorted(REQUIRED_G1_IDS - ids)
    duplicates = sorted({item for item in ids if sum(row["gate_id"] == item for row in checks) > 1})
    all_passed = not missing and not duplicates and all(item["status"] == "PASS" for item in checks)
    return {
        "schema_version": "1.0.0",
        "attempt_id": attempt_id,
        "gate": "G1",
        "overall_decision": "GO" if all_passed else "NO_GO",
        "missing_gate_ids": missing,
        "duplicate_gate_ids": duplicates,
        "checks": checks,
    }
