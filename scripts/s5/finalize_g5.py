"""G5 content replay, behavior regression and honest B1/B2 comparison."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import xml.etree.ElementTree as ET
from decimal import Decimal
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from langgraph.checkpoint.sqlite import SqliteSaver
from finresearch.contracts import stable_sha256, EvidenceCandidate
from finresearch.contracts.research import ResearchContext, ResearchClaim
from finresearch.contracts.supplement import ActionPlan, ToolResult
from finresearch.model.deepseek_json import conservative_cost
from finresearch.retrieval.corpus import write_json, file_sha256, chunk_pages
from finresearch.retrieval.multi_document import MultiDocumentRetriever
from finresearch.storage.session_store import SessionStore, safe_task_id
from finresearch.workflow.session_executor import validate_input_lock
from finresearch.workflow.session_inputs import source_inputs
from finresearch.workflow.supplement_controller import action_fingerprint
from finresearch.workflow.supplement_tools import SupplementTools
from finresearch.workflow.bounded_supplement import SupplementDependencies, compile_supplement_graph, render_supplement


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def audit_run(name):
    safe_task_id(name)
    run = ROOT / "runs/s5" / name
    state = read(run / "final_state.json")
    baseline = read(run / "baseline.json")
    ctx = ResearchContext.model_validate(read(run / "context.json"))
    validate_input_lock(ROOT, read(run / "inputs.lock.json"))
    corpus, data, rows, evidence, source_checks, _ = source_inputs(ROOT, ctx)
    engine = MultiDocumentRetriever(corpus, chunk_pages(corpus, data["ranking"]), data["ranking"], data["keyword"])
    tools = SupplementTools(corpus, rows, evidence, engine, [EvidenceCandidate.model_validate(e) for e in baseline["evidence"]])
    known_rows = {r.observation_id: r.model_dump(mode="json") for r in rows}
    all_final_rows = state["current"]["observations"]
    checks = {"source_observations_exact": all(known_rows.get(r["observation_id"]) == r for r in all_final_rows),
              "bounded_rounds_actions": state["rounds"] <= 3 and state["action_count"] <= 6,
              "history_count": len(state["history"]) == state["action_count"],
              "causal_gaps_not_closed": all(g["status"] != "RESOLVED" for g in state["gaps"] if g["gap_type"] == "INDEPENDENT_CONFIRMATION")}
    for raw in state["current"]["evidence"]:
        if not tools.verify_evidence(ctx, EvidenceCandidate.model_validate(raw)):
            raise ValueError("最终原文范围不合法")
    actions = {h["action"]["action_id"]: h for h in state["history"]}
    checks["unique_actions"] = len(actions) == len(state["history"])
    store = SessionStore(ROOT / "storage/s5/tasks" / name / "session.sqlite")
    plans = {}
    policy = read(run / "policy.json")
    with SqliteSaver.from_conn_string(str(store.path.parent / "checkpoints.sqlite")) as saver:
        graph = compile_supplement_graph(SupplementDependencies(policy,None,None,None,None,None,{},set()), saver)
        cfg = {"configurable": {"thread_id": name}}
        # Graph node structure is independent of callbacks; policy checked below.
        restored = graph.get_state(cfg)
        checks["disk_state_matches"] = restored.values == state and not restored.next
        for snap in graph.get_state_history(cfg):
            if "execute_actions" in snap.next:
                plans[snap.values["rounds"]] = [a["action_id"] for a in snap.values["selected"]]
    task = store.task()
    if policy != read(ROOT / "configs/s5/controller.json"):
        raise ValueError("运行控制策略与冻结版本不一致")
    seen = set()
    baseline_name = baseline["context"]["run_id"]
    for path in sorted((ROOT / "runs/s4" / baseline_name / "queries").glob("*.json")):
        query = read(path)["query"]
        seen.add(action_fingerprint("SEARCH_DISCLOSURE", query["company_id"], ctx,
                                   {"question":query["question"],"reporting_year":query["reporting_year"],"need":"EXPLANATION"}))
    selected_claims = [ResearchClaim.model_validate(c) for c in state["current"]["claims"] if c["claim_id"].startswith("claim-s5-")]
    deps = SupplementDependencies(policy,
        lambda c,r,candidates,cap: ActionPlan(selected_action_ids=plans[r]),
        lambda c,a: ToolResult.model_validate(actions[a.action_id]["result"]),
        tools.verify_observation, tools.verify_evidence, lambda c,e:selected_claims,
        {d:r.source_url for d,r in corpus.documents.items()}, seen)
    replay = compile_supplement_graph(deps).invoke({"context":ctx.model_dump(mode="json"),"baseline":baseline})
    checks["controller_and_result_replay"] = replay == state
    checks["report_exact_replay"] = (run / "report.md").read_text(encoding="utf-8") == render_supplement(state,deps) == state["report"]
    summary = read(run / "summary.json")
    if summary["mode"] == "LIVE":
        config = read(run / "model_config.json")
        audits = [e["audit"] for e in store.events() if e.get("event") == "MODEL_ATTEMPT"]
        with sqlite3.connect(store.path.parent / "budget.sqlite") as conn:
            sha, raw = conn.execute("SELECT config_sha,payload FROM budget WHERE id=1").fetchone()
        budget = json.loads(raw)
        costs = [conservative_cost(a["usage"], config) for a in audits]
        checks["live_budget_audit"] = (sha == stable_sha256(config) and len(audits) == summary["model_calls"] == budget["total_calls"]
            and len(audits) <= 4 and all(a["status"] == "PARSED" for a in audits) and all(c is not None for c in costs)
            and Decimal(summary["estimated_cost_usd"]) == Decimal(budget["reserved_cost"]) == sum((Decimal(c) for c in costs),Decimal(0))
            and Decimal(budget["reserved_cost"]) <= Decimal("0.05") and not budget["pending"])
    else:
        checks["zero_hidden_model_calls"] = not any(e.get("event") == "MODEL_ATTEMPT" for e in store.events()) and summary["model_calls"] == 0
    return {"run_id":name,"checks":checks,"passed":all(checks.values()),"summary":summary,
            "metrics": {"claims_before":len(baseline["claims"]),"claims_after":len(state["current"]["claims"]),
                        "observations_before":len(baseline["observations"]),"observations_after":len(all_final_rows)},
            "scope": "SOURCE_WINDOW_AVAILABILITY_NOT_SEMANTIC_OR_CAUSAL_ACCURACY"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--attempt-id",required=True)
    parser.add_argument("--missing-run",required=True)
    parser.add_argument("--natural-run",required=True)
    args = parser.parse_args()
    name = safe_task_id(args.attempt_id)
    folder = ROOT / "runs/s5" / name
    folder.mkdir(parents=True,exist_ok=False)
    audits = [audit_run(args.missing_run),audit_run(args.natural_run)]
    write_json(folder / "run_audits.json",audits)
    expected = read(ROOT / "runs/s4/s4-b1-live-20260930-05/calculations.json")
    missing = read(ROOT / "runs/s5" / args.missing_run / "final_state.json")
    natural = read(ROOT / "runs/s5" / args.natural_run / "final_state.json")
    checks = {"actual_content_audits":all(a["passed"] for a in audits),
              "preset_numeric_gap_repaired":len(missing["baseline"]["observations"])==17 and len(missing["current"]["observations"])==18
                  and any(g["gap_type"]=="MISSING_METRIC" and g["status"]=="RESOLVED" for g in missing["gaps"]),
              "repaired_calculations_match_b1":missing["current"]["calculations"]==expected,
              "natural_numbers_unchanged":natural["current"]["calculations"]==expected and natural["current"]["observations"]==natural["baseline"]["observations"],
              "unverified_causes_remain_partial":missing["status"]==natural["status"]=="PARTIAL"}
    env = dict(os.environ)
    env["PYTHONIOENCODING"]="utf-8"
    result = subprocess.run([sys.executable,"-m","pytest","tests","-q","-o",f"cache_dir={folder / 'pytest_cache'}","--junitxml",str(folder / "pytest.xml")],
        cwd=ROOT,env=env,capture_output=True,text=True,encoding="utf-8",errors="replace",timeout=120,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name=="nt" else 0)
    (folder / "pytest.txt").write_text(result.stdout+result.stderr,encoding="utf-8")
    xml = ET.parse(folder / "pytest.xml").getroot()
    totals = {key:sum(int(s.get(key,"0")) for s in xml.iter("testsuite")) for key in ("tests","failures","errors","skipped")}
    labels = [t.get("name","") for t in xml.iter("testcase")]
    cases = read(ROOT / "evals/dev/s5_decision_cases.json")["cases"]
    checks["decision_cases_all_exercised"] = all(any(label.startswith(case["test"]) for label in labels) for case in cases)
    checks["full_regression"] = result.returncode==0 and totals["tests"]>=240 and totals["failures"]==totals["errors"]==totals["skipped"]==0
    write_json(folder / "gate_report.json",{"gate":"G5","decision":"GO" if all(checks.values()) else "NO_GO",
        "all_checks_passed":all(checks.values()),"checks":checks,"test_totals":totals,"development_decision_cases":len(cases),
        "runs":[args.missing_run,args.natural_run],"verifier_sha256":file_sha256(Path(__file__)),
        "limitations":["数值故障场景是显式输入视图故障，不是自然缺失率","新增原文窗口不等于语义支持或因果认证",
                       "未证明B2整体质量优于B1，默认仍保留B1","S6循环中断恢复/取消/WAITING_INPUT尚需接入后单独验收","旧G1语义/历史费用问题未关闭"]})
    print(json.dumps({"decision":"GO" if all(checks.values()) else "NO_GO","tests":totals,"failed":[k for k,v in checks.items() if not v]}))
    if not all(checks.values()):
        raise SystemExit(1)


if __name__=="__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    main()
