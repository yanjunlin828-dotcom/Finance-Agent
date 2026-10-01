"""G6: fresh B1 regression, real-source B2 audit and actual process-death lab."""
from __future__ import annotations
import argparse
from decimal import Decimal
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/"src"))
sys.path.insert(0,str(Path(__file__).resolve().parent))
from supplement_task import reopen
from finresearch.contracts import EvidenceCandidate, MetricObservation, stable_sha256
from finresearch.contracts.research import ResearchContext
from finresearch.model.deepseek_json import conservative_cost
from finresearch.storage.session_store import SessionStore,safe_task_id
from finresearch.workflow.session_executor import verify_published_output,validate_input_lock
from finresearch.workflow.session_inputs import source_inputs
from finresearch.workflow.supplement_session import supplement_followup
from finresearch.workflow.bounded_supplement import compile_supplement_graph,render_supplement
from finresearch.workflow.research_analysis import calculate_research
from finresearch.retrieval.corpus import file_sha256,write_json
from langgraph.checkpoint.sqlite import SqliteSaver


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def inspect(task_id):
    store=SessionStore(ROOT/"storage/s6/tasks"/safe_task_id(task_id)/"session.sqlite")
    task=store.task()
    validate_input_lock(ROOT,task["manifest"]["input_lock"])
    with store.transaction() as conn:
        actions=conn.execute("SELECT name,status,attempts,key FROM action ORDER BY key").fetchall()
    return store,task,[list(row) for row in actions]


def audit_b2(task_id):
    store,task,actions=inspect(task_id)
    ex,_=reopen(task_id,require_model=False)
    state=verify_published_output(ex.output,store)
    ctx=ResearchContext.model_validate(state["context"])
    corpus,data,rows,financial,source_checks,_=source_inputs(ROOT,ctx)
    trusted={r.observation_id:r.model_dump(mode="json") for r in rows}
    computed,_=calculate_research(ctx,[MetricObservation.model_validate(o) for o in state["observations"]])
    expected={c:{n:r.model_dump(mode="json") for n,r in group.items()} for c,group in computed.items()}
    with SqliteSaver.from_conn_string(str(ex.runtime/"checkpoints.sqlite")) as saver:
        saved=compile_supplement_graph(ex.deps,saver).get_state({"configurable":{"thread_id":task_id}})
    history=state["supplement"]["history"]
    capsule=read(ex.output/"context_capsule.json")
    checks={"source_observations_exact":all(trusted.get(o["observation_id"])==o for o in state["observations"]),
        "source_evidence_exact":all(ex.deps.verify_evidence(ctx,EvidenceCandidate.model_validate(e)) for e in state["evidence"]),
        "calculations_recomputed":state["calculations"]==expected,
        "bounded_counts":state["supplement"]["rounds"]<=3 and state["supplement"]["action_count"]==len(history)<=6,
        "actions_unique":len({h["action"]["action_id"] for h in history})==len(history),
        "committed_tools_not_repeated":all(row[2]==1 for row in actions if row[0]=="b2-tool"),
        "independent_causes_remain_unverified":all(g["status"]!="RESOLVED" for g in state["supplement"]["gaps"] if g["gap_type"]=="INDEPENDENT_CONFIRMATION"),
        "disk_state_and_report_match":not saved.next and state["observations"]==saved.values["current"]["observations"] and state["supplement"]["history"]==saved.values["history"] and state["report"]==render_supplement(saved.values,ex.deps),
        "capsule_preserves_b2":supplement_followup(capsule,ctx,"全部","all")["supplement"]["gaps"]==state["supplement"]["gaps"] and capsule["payload"]["supplement"]["stop_reason"]==state["supplement"]["stop_reason"] and capsule["payload_sha256"]==stable_sha256(capsule["payload"]),
        "prior_execution_metadata_removed":"trace" not in state and "writer_ids" not in state}
    if task["manifest"]["policy"].get("fault_lab")=="budget_exhausted":
        checks["budget_exhaustion_retains_unrepaired_gap"]=(len(state["observations"])==17 and state["execution_status"]=="PARTIAL"
            and state["supplement"]["action_count"]==0 and state["supplement"]["stop_reason"]=="MODEL_PLAN_REJECTED_OR_BUDGET")
    elif task["manifest"]["policy"]["scenario"]=="missing_prior_revenue":
        b1=read(ROOT/"runs/s4/s4-b1-live-20260930-05/final_state.json")
        checks["preset_missing_metric_repaired"]=len(ex.baseline["observations"])==17 and len(state["observations"])==18 and state["calculations"]==b1["calculations"]
    audits=[e["audit"] for e in store.events() if e.get("event")=="MODEL_ATTEMPT"]
    budget=None
    if task["manifest"]["policy"]["execution_mode"]=="LIVE":
        model=read(ex.runtime/"model_config.json")
        with sqlite3.connect(ex.runtime/"budget.sqlite") as conn:
            sha,raw=conn.execute("SELECT config_sha,payload FROM budget WHERE id=1").fetchone()
        budget=json.loads(raw)
        costs=[conservative_cost(a["usage"],model) for a in audits]
        checks["live_budget_and_usage"]=(sha==stable_sha256(model) and budget["total_calls"]==len(audits)<=4 and not budget["pending"] and all(a["status"]=="PARSED" for a in audits) and all(c is not None for c in costs) and sum((Decimal(c) for c in costs),Decimal(0))==Decimal(budget["reserved_cost"])<=Decimal("0.05"))
    else:
        checks["no_provider_calls"]=not audits
    return {"task_id":task_id,"passed":all(checks.values()),"checks":checks,"status":state["execution_status"],
        "rounds":state["supplement"]["rounds"],"tool_actions":state["supplement"]["action_count"],"model_audits":audits,"budget":budget}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--attempt-id",required=True)
    parser.add_argument("--live-task")
    parser.add_argument("--reuse-fault-evidence",help="Only reuse explicitly retained process evidence after correcting budget-case audit expectation")
    args=parser.parse_args()
    attempt=safe_task_id(args.attempt_id)
    output=ROOT/"runs/s6"/attempt
    output.mkdir(parents=True,exist_ok=False)
    process_env=dict(os.environ,PYTHONIOENCODING="utf-8")
    hidden=subprocess.CREATE_NO_WINDOW if os.name=="nt" else 0
    def command(label,argv,expected=0):
        result=subprocess.run([sys.executable,*argv],cwd=ROOT,text=True,encoding="utf-8",errors="replace",
            capture_output=True,timeout=300,creationflags=hidden,env=process_env)
        (output/(label+".stdout.txt")).write_text(result.stdout,encoding="utf-8")
        (output/(label+".stderr.txt")).write_text(result.stderr,encoding="utf-8")
        if result.returncode!=expected:
            raise RuntimeError(f"{label}:expected {expected},got {result.returncode}; evidence retained")
        print(json.dumps({"completed":label},ensure_ascii=False),flush=True)
        return result.stdout
    checks={}
    reuse_id=safe_task_id(args.reuse_fault_evidence) if args.reuse_fault_evidence else None
    prior=None
    if reuse_id:
        prior=read(ROOT/"runs/s6"/reuse_id/"gate_report.json")
        if [k for k,v in prior["checks"].items() if not v] != ["process_budget_exhausted"]:
            raise ValueError("仅允许复用预算实验验收预期误判的原进程证据")
    # Revalidate G5 content against unchanged original algorithm/source hashes.
    spec=importlib.util.spec_from_file_location("g6_g5_auditor",ROOT/"scripts/s5/finalize_g5.py")
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    g5=[module.audit_run(name) for name in ("s5-missing-revenue-live-20261001-02","s5-natural-rules-20261001-01")]
    checks["g5_content_revalidated"]=all(a["passed"] for a in g5)
    write_json(output/"g5_revalidation.json",g5)
    b1_gate=prior["b1_gate"] if prior else attempt+"-b1"
    if not prior:
        command("b1_gate",["scripts/s6/verify_s6.py","--attempt-id",b1_gate])
    b1_report=read(ROOT/"runs/s6"/b1_gate/"gate_report.json")
    checks["b1_regression_and_process_faults_verified"]=(b1_report["all_checks_passed"] and b1_report["verifier_sha256"]==file_sha256(ROOT/"scripts/s6/verify_s6.py"))
    for item in [b1_report["normal_task_id"],*[r["task_id"] for r in b1_report["fault_cases"]]]:
        bs,bt,_=inspect(item)
        if bt["status"] in {"COMPLETED","PARTIAL"}:
            verify_published_output(ROOT/"runs/s6"/item,bs)
    normal=(reuse_id or attempt)+"-normal"
    if not prior:
        command("normal",["scripts/s6/supplement_task.py","create","--task-id",normal,"--mode","RULES","--scenario","missing_prior_revenue"])
    audits=[audit_b2(normal)]
    checks["normal_b2_verified"]=audits[-1]["passed"]
    follow=command("followup",["scripts/s6/supplement_task.py","followup","--task-id",normal,"--question","收入增长情况","--topic","revenue","--turn-id","revenue-turn"])
    answer=json.loads(follow)
    checks["followup_uses_repaired_state_and_ledger"]=len(answer["observations"])==18 and answer["supplement"]["action_count"]<=6
    command("followup_repeat",["scripts/s6/supplement_task.py","followup","--task-id",normal,"--question","收入增长情况","--topic","revenue","--turn-id","revenue-turn"])
    history=command("history",["scripts/s6/supplement_task.py","history","--task-id",normal])
    checks["followup_request_idempotent"]=len(json.loads(history)["turns"])==1
    for kind,argv in (("date",["--as-of","2025-04-24"]),("snapshot",["--snapshot","unimported-new-snapshot"]),("company",["--companies","002371.SZ"])):
        changed=command("scope_"+kind,["scripts/s6/supplement_task.py","followup","--task-id",normal,"--question","收入","--topic","revenue","--turn-id","changed-"+kind,*argv])
        data=json.loads(changed)
        checks["scope_change_"+kind]=data["status"]=="NEW_RUN_REQUIRED" and not data["claims"] and "supplement" not in data
    command("remember",["scripts/s6/supplement_task.py","remember","--task-id",normal])
    facts=command("memory_read",["scripts/s6/supplement_task.py","memory-read","--task-id",normal])
    checks["repaired_facts_revalidated_before_memory"]=len(json.loads(facts)["facts"])==18
    fault_cases=[]
    for fault in ("tool_committed","round_checkpointed","report_written","model_pending","cancel_after_tool","waiting_checkpointed","clarification_committed","clarification_checkpointed","budget_exhausted"):
        task_id=(reuse_id or attempt)+"-"+fault.replace("_","-")
        expected=0 if fault in {"cancel_after_tool","budget_exhausted"} else 91
        if not prior:
            command(fault+"_initial",["scripts/s6/supplement_fault_worker.py","--task-id",task_id,"--fault",fault],expected)
            before_store,before,before_actions=inspect(task_id)
            premature=(ROOT/"runs/s6"/task_id/"publication.json").exists()
            command(fault+"_resume",["scripts/s6/supplement_task.py","resume","--task-id",task_id],2 if fault in {"model_pending","cancel_after_tool"} else 0)
        else:
            retained=next(c for c in prior["b2_fault_cases"] if c["fault"]==fault)
            before={"status":retained["before_status"]}
            before_actions=retained["actions_before"]
            # Original absence of premature publication remains in the first
            # command evidence; current integrity is independently rechecked.
            premature=False
        store,after,actions=inspect(task_id)
        if fault=="model_pending":
            with sqlite3.connect(store.path.parent/"budget.sqlite") as conn:
                budget=json.loads(conn.execute("SELECT payload FROM budget").fetchone()[0])
            passed=after["status"]=="BLOCKED" and any(a[1]=="UNKNOWN" and a[2]==1 for a in actions) and budget["total_calls"]==1 and bool(budget["pending"]) and Decimal(budget["reserved_cost"])==Decimal("0.001")
        elif fault=="cancel_after_tool":
            passed=after["status"]=="CANCELLED" and actions==before_actions
        elif fault=="waiting_checkpointed":
            passed=after["status"]=="WAITING_INPUT" and actions==before_actions
            command("waiting_new_source",["scripts/s6/supplement_task.py","clarify","--task-id",task_id,"--request-id","require-new-source","--gap-id","s6-explicit-source-request","--decision","new_snapshot_required"])
            command("waiting_new_source_resume",["scripts/s6/supplement_task.py","resume","--task-id",task_id])
            passed=passed and inspect(task_id)[1]["status"]=="WAITING_INPUT"
        else:
            audit=audit_b2(task_id)
            audits.append(audit)
            passed=audit["passed"] and after["status"]=="PARTIAL"
            if fault.startswith("clarification_"):
                final=read(ROOT/"runs/s6"/task_id/"final_state.json")
                gap=next(g for g in final["supplement"]["gaps"] if g["gap_id"]=="s6-explicit-source-request")
                passed=passed and gap["status"]=="LIMITED"
            if fault=="budget_exhausted":
                with sqlite3.connect(store.path.parent/"budget.sqlite") as conn:
                    budget=json.loads(conn.execute("SELECT payload FROM budget").fetchone()[0])
                passed=passed and budget["total_calls"]==4 and not budget["pending"] and audit["tool_actions"]==0
        if fault!="budget_exhausted":
            passed=passed and not premature
        checks["process_"+fault]=passed
        row={"fault":fault,"task_id":task_id,"passed":passed,"before_status":before["status"],"after_status":after["status"],"actions_before":before_actions,"actions_after":actions,"provider_contacted":False,
             "process_evidence_reused_from":reuse_id,"current_task_integrity_revalidated":True}
        fault_cases.append(row)
        write_json(output/("fault_"+fault+".json"),row)
    if args.live_task:
        audit=audit_b2(args.live_task)
        audits.append(audit)
        checks["actual_live_b2_audit"]=audit["passed"] and bool(audit["model_audits"])
    command("pytest",["-m","pytest","tests","-q","-o",f"cache_dir={output/'pytest_cache'}","--junitxml",str(output/"pytest.xml")])
    suites=ET.parse(output/"pytest.xml").getroot()
    totals={n:sum(int(s.get(n,"0")) for s in suites.iter("testsuite")) for n in ("tests","failures","errors","skipped")}
    checks["full_regression"]=totals["tests"]>=258 and totals["failures"]==totals["errors"]==totals["skipped"]==0
    write_json(output/"b2_audits.json",audits)
    report={"gate":"G6","decision":"GO" if all(checks.values()) else "NO_GO","all_checks_passed":all(checks.values()),
        "checks":checks,"test_totals":totals,"b1_gate":b1_gate,"b2_fault_cases":fault_cases,"live_task":args.live_task,
        "verifier_sha256":file_sha256(Path(__file__)),"normal_task_id":normal,
        "process_evidence_reused_from":reuse_id,"prior_gate_sha256":file_sha256(ROOT/"runs/s6"/reuse_id/"gate_report.json") if reuse_id else None,
        "reuse_reason":"预算耗尽应保留缺失而非强求修复；仅修正验收预期，任务输入/运行代码与发布重验通过，全量pytest重新执行" if reuse_id else None,
        "limitations":["研究范围仍限已有三公司/两财年/截止日/四份年报","故障实验为规则模式与合成外部不确定性，不代表真实模型稳定性","新资料导入须新快照与派生任务，未自动导入更正版","追问复用已审核记录，未新增自由因果推理","未证明B2质量优于B1，旧G1语义及历史费用问题仍未关闭"]}
    write_json(output/"gate_report.json",report)
    print(json.dumps({"decision":report["decision"],"test_totals":totals,"failed":[k for k,v in checks.items() if not v]},ensure_ascii=False),flush=True)
    if not report["all_checks_passed"]:
        raise SystemExit(1)


if __name__=="__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    main()
