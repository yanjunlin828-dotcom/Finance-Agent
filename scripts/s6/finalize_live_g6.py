"""Bind unchanged full G6 fault evidence to independently audited live recovery."""
from __future__ import annotations
import argparse
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
from verify_s6_b2 import ROOT,read,inspect,audit_b2
from finresearch.storage.session_store import safe_task_id
from finresearch.workflow.session_executor import verify_published_output
from finresearch.retrieval.corpus import file_sha256,write_json
import json


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--attempt-id",required=True)
    parser.add_argument("--base-gate",required=True)
    parser.add_argument("--live-task",required=True)
    args=parser.parse_args()
    name=safe_task_id(args.attempt_id)
    base_id=safe_task_id(args.base_gate)
    live_id=safe_task_id(args.live_task)
    output=ROOT/"runs/s6"/name
    output.mkdir(parents=True,exist_ok=False)
    base_path=ROOT/"runs/s6"/base_id/"gate_report.json"
    base=read(base_path)
    checks={"full_gate_passed":base["decision"]=="GO" and base["all_checks_passed"],
        "full_gate_verifier_unchanged":base["verifier_sha256"]==file_sha256(ROOT/"scripts/s6/verify_s6_b2.py")}
    # Do not merely copy historical GO: reopen every cited task's input locks
    # and verify publication files for completed tasks under current sources.
    b1=read(ROOT/"runs/s6"/base["b1_gate"]/"gate_report.json")
    task_ids=[base["normal_task_id"],b1["normal_task_id"],*[c["task_id"] for c in base["b2_fault_cases"]],*[c["task_id"] for c in b1["fault_cases"]]]
    revalidated=[]
    for task_id in task_ids:
        store,task,actions=inspect(task_id)
        if task["status"] in {"COMPLETED","PARTIAL"}:
            verify_published_output(ROOT/"runs/s6"/task_id,store)
        revalidated.append({"task_id":task_id,"status":task["status"],"input_lock_valid":True})
    checks["cited_tasks_revalidated"]=len(revalidated)==len(task_ids)
    audit=audit_b2(live_id)
    checks["live_source_and_budget_audit"]=audit["passed"] and len(audit["model_audits"])==4
    lab=read(ROOT/"runs/s6"/live_id/"recovery_lab.json")
    store,task,actions=inspect(live_id)
    checks["live_committed_plan_recovered_once"]=(lab["mode"]=="LIVE" and lab["fault_point"]=="action:b2-planner:COMMITTED"
        and lab["model_calls_before_exit"]==1 and lab["exit_code"]==91
        and task["manifest"]["policy"]["execution_mode"]=="LIVE"
        and all(a[1]=="DONE" and a[2]==1 for a in actions))
    # A finished resume must verify publication, without new provider calls.
    from supplement_task import reopen
    ex,guard=reopen(live_id)
    before=guard.total_calls
    ex.run()
    checks["finished_live_resume_adds_no_calls"]=before==guard.total_calls==4
    write_json(output/"live_audit.json",audit)
    write_json(output/"revalidated_tasks.json",revalidated)
    report={"gate":"G6","decision":"GO" if all(checks.values()) else "NO_GO","all_checks_passed":all(checks.values()),
        "checks":checks,"base_gate":base_id,"base_gate_sha256":file_sha256(base_path),
        "test_totals":base["test_totals"],"test_evidence_reused_from_unchanged_base_gate":True,
        "live_task":live_id,"live_model_calls":audit["budget"]["total_calls"],"live_estimated_cost_usd":audit["budget"]["reserved_cost"],
        "live_recovery_lab_sha256":file_sha256(ROOT/"runs/s6"/live_id/"recovery_lab.json"),
        "b1_process_faults":len(b1["fault_cases"]),"b2_process_faults":len(base["b2_fault_cases"]),
        "b1_legacy_scope_field":b1["full_g6_decision"],
        "b1_scope_note":"兼容B1验证器的NOT_READY_G5_PENDING字段只描述旧验证器范围，不裁定当前G5或完整G6；本报告结合G5复验和B2故障证据裁定完整G6",
        "verifier_sha256":file_sha256(Path(__file__)),"limitations":base["limitations"]}
    write_json(output/"gate_report.json",report)
    print(json.dumps({"decision":report["decision"],"checks":checks,"test_totals":base["test_totals"]},ensure_ascii=False))
    if not report["all_checks_passed"]:
        raise SystemExit(1)


if __name__=="__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    main()
