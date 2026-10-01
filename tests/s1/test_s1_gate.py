from finresearch.gates.s1_gate import REQUIRED_G1_IDS, evaluate_s1_gate


def test_s1_gate_requires_every_hard_check() -> None:
    checks = [{"gate_id": gate_id, "status": "PASS", "evidence": {}} for gate_id in sorted(REQUIRED_G1_IDS)]
    assert evaluate_s1_gate("g1-test", checks)["overall_decision"] == "GO"
    assert evaluate_s1_gate("g1-test", checks[:-1])["overall_decision"] == "NO_GO"
