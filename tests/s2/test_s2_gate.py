from finresearch.gates.s2_gate import REQUIRED_G2_IDS, evaluate_s2_gate


def test_g2_requires_every_hard_gate() -> None:
    rows = [{"gate_id": item, "status": "PASS", "evidence": {}} for item in sorted(REQUIRED_G2_IDS)]
    assert evaluate_s2_gate("g2-test", rows)["overall_decision"] == "GO"
    assert evaluate_s2_gate("g2-test", rows[:-1])["overall_decision"] == "NO_GO"
