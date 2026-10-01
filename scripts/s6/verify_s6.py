"""S6 B1 release evidence: real-source replay, subprocess faults and regression."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import xml.etree.ElementTree as ET
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from finresearch.contracts import stable_sha256
from finresearch.retrieval.corpus import file_sha256, write_json
from finresearch.storage.session_store import safe_task_id, SessionStore
from finresearch.workflow.session_executor import validate_input_lock, verify_published_output


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--live-task", help="已完成的可选实时演示；不由验收器发起收费调用")
    args = parser.parse_args()
    attempt = safe_task_id(args.attempt_id)
    output = ROOT / "runs/s6" / attempt
    output.mkdir(parents=True, exist_ok=False)
    hidden = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    process_env = dict(os.environ)
    process_env["PYTHONIOENCODING"] = "utf-8"
    def command(label, arguments, expected):
        result = subprocess.run([sys.executable, *arguments], cwd=ROOT, text=True, encoding="utf-8",
                                errors="replace", capture_output=True, timeout=120, creationflags=hidden, env=process_env)
        (output / f"{label}.stdout.txt").write_text(result.stdout, encoding="utf-8")
        (output / f"{label}.stderr.txt").write_text(result.stderr, encoding="utf-8")
        if result.returncode != expected:
            raise RuntimeError(f"{label}:expected {expected},got {result.returncode}; evidence retained")
        return result.stdout
    def read_task(task_id):
        store = SessionStore(ROOT / "storage/s6/tasks" / task_id / "session.sqlite")
        task = store.task()
        validate_input_lock(ROOT, task["manifest"]["input_lock"])
        with store.transaction() as conn:
            actions = conn.execute("SELECT name,status,attempts,key FROM action ORDER BY key").fetchall()
        return store, task, actions

    checks = {}
    cases = []
    normal = attempt + "-normal"
    command("normal", ["scripts/s6/task.py", "create", "--task-id", normal, "--mode", "REPLAY", "--replay-run", "runs/s4/s4-b1-live-20260930-05/final_state.json"], 0)
    store, task, actions = read_task(normal)
    state = verify_published_output(ROOT / "runs/s6" / normal, store)
    checks["normal_real_source_replay"] = state["trace"] == json.loads((ROOT / "protocols/revenue_quality_v1.json").read_text(encoding="utf-8"))["nodes"]
    checks["financial_results_match_g4"] = state["calculations"] == json.loads((ROOT / "runs/s4/s4-b1-live-20260930-05/calculations.json").read_text(encoding="utf-8"))
    checks["normal_replay_did_not_call_provider"] = not any(e.get("event") == "MODEL_ATTEMPT" for e in store.events())
    command("same_scope_followup", ["scripts/s6/task.py", "followup", "--task-id", normal, "--question", "经营现金流如何变化？", "--turn-id", "same-scope"], 0)
    changed = command("changed_scope_followup", ["scripts/s6/task.py", "followup", "--task-id", normal, "--question", "收入变化？", "--as-of", "2025-04-24", "--turn-id", "new-scope"], 0)
    checks["scope_change_requires_new_research"] = json.loads(changed)["status"] == "NEW_RUN_REQUIRED" and not json.loads(changed)["claims"]
    command("remember", ["scripts/s6/task.py", "remember", "--task-id", normal], 0)
    reread = command("memory_read", ["scripts/s6/task.py", "memory-read", "--task-id", normal], 0)
    checks["verified_facts_reopened"] = len(json.loads(reread)["facts"]) == 18

    for fault in ("hypotheses_committed", "report_written", "retrieval_start", "model_pending", "cancel_after_model"):
        task_id = attempt + "-" + fault.replace("_", "-")
        expected = 0 if fault == "cancel_after_model" else 91
        command(fault + "_initial", ["scripts/s6/fault_worker.py", "--task-id", task_id, "--fault", fault], expected)
        before_store, before_task, before_actions = read_task(task_id)
        premature = (ROOT / "runs/s6" / task_id / "publication.json").exists()
        command(fault + "_resume", ["scripts/s6/task.py", "resume", "--task-id", task_id], 2 if fault in {"model_pending", "cancel_after_model"} else 0)
        after_store, after_task, after_actions = read_task(task_id)
        if fault == "model_pending":
            passed = after_task["status"] == "BLOCKED" and any(row[1] == "UNKNOWN" for row in after_actions) and before_actions[0][2] == 1
        elif fault == "cancel_after_model":
            passed = after_task["status"] == "CANCELLED" and after_actions == before_actions
        else:
            resumed = verify_published_output(ROOT / "runs/s6" / task_id, after_store)
            passed = after_task["status"] == "COMPLETED" and all(row[2] == 1 for row in after_actions) and resumed["calculations"] == state["calculations"]
        passed = passed and not premature
        checks["process_fault_" + fault] = passed
        cases.append({"fault": fault, "task_id": task_id, "before_status": before_task["status"],
                      "after_status": after_task["status"], "actions_before": before_actions,
                      "actions_after": after_actions, "passed": passed, "execution_mode": "REPLAY"})
        write_json(output / "fault_matrix.partial.json" if not (output / "fault_matrix.partial.json").exists() else output / f"fault_{fault}.json", cases[-1])

    command("pytest", ["-m", "pytest", "tests", "-q", "-o", f"cache_dir={output / 'pytest_cache'}", "--junitxml", str(output / "pytest.xml")], 0)
    suites = ET.parse(output / "pytest.xml").getroot()
    totals = {name: sum(int(s.get(name, "0")) for s in suites.iter("testsuite")) for name in ("tests", "failures", "errors", "skipped")}
    checks["full_regression"] = totals["tests"] >= 204 and totals["failures"] == 0 and totals["errors"] == 0 and totals["skipped"] == 0
    if args.live_task:
        live_id = safe_task_id(args.live_task)
        live_store, live_task, _ = read_task(live_id)
        live = verify_published_output(ROOT / "runs/s6" / live_id, live_store)
        audits = [e["audit"] for e in live_store.events() if e.get("event") == "MODEL_ATTEMPT"]
        checks["optional_live_report"] = live_task["manifest"]["policy"]["execution_mode"] == "LIVE" and live["validation"]["status"] == "PASS" and bool(audits)
        write_json(output / "live_audit.json", {"task_id": live_id, "status": live_task["status"], "attempts": audits,
                                                "causal_semantics": "NOT_INDEPENDENTLY_CERTIFIED"})
    report = {"gate": "G6_B1", "decision": "GO_B1_ONLY" if all(checks.values()) else "NO_GO",
              "full_g6_decision": "NOT_READY_G5_PENDING", "checks": checks, "all_checks_passed": all(checks.values()),
              "test_totals": totals, "fault_cases": cases, "normal_task_id": normal,
              "optional_live_task": args.live_task, "verifier_sha256": file_sha256(Path(__file__)),
              "limitations": ["S5/G5尚未实现，未验证B2循环恢复", "追问只复用已审记录，不进行新的自由因果分析",
                              "单机单用户，每任务串行", "取消是协作式，不强制杀死阻塞调用", "供应商结果未知时阻塞，不承诺外部exactly-once",
                              "回放不计入实时模型稳定性/延迟/成本", "旧G1自由解释语义/历史费用问题仍未关闭"]}
    write_json(output / "gate_report.json", report)
    write_json(output / "inputs.lock.json", {"files": {p.relative_to(ROOT).as_posix(): file_sha256(p) for p in
                                                       sorted((ROOT / "src/finresearch").rglob("*.py"))},
                                            "scripts": {p.relative_to(ROOT).as_posix(): file_sha256(p) for p in (ROOT / "scripts/s6").glob("*.py")},
                                            "tests": {p.relative_to(ROOT).as_posix(): file_sha256(p) for p in (ROOT / "tests/s6").glob("*.py")}})
    print(json.dumps({"decision": report["decision"], "full_g6": report["full_g6_decision"], "tests": totals}, ensure_ascii=False))
    if not all(checks.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
