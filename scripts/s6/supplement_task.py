"""Durable B2 task CLI; human clarification is an explicit local capability."""
from __future__ import annotations
import argparse
from copy import deepcopy
from datetime import date
import importlib.util
import json
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from finresearch.contracts import EvidenceCandidate
from finresearch.contracts.research import ResearchContext
from finresearch.retrieval.corpus import file_sha256
from finresearch.storage.session_store import SessionStore, safe_task_id, Cancelled, UnsafeResume
from finresearch.workflow.session_inputs import source_inputs
from finresearch.workflow.session_executor import atomic_artifact, json_text, validate_input_lock, verify_published_output
from finresearch.workflow.supplement_session import SupplementSession, supplement_followup, task_directories
from finresearch.workflow.supplement_session_inputs import make_supplement_dependencies
from finresearch.workflow.bounded_supplement import refresh_financials


def prepare(task_id, mode, scenario="natural", baseline_run=None):
    """Freeze revalidated B1 and an explicitly labelled derived input scenario."""
    safe_task_id(task_id)
    selection_path = ROOT / "configs/s6/baseline.json"
    selection = json.loads(selection_path.read_text(encoding="utf-8")) if baseline_run is None else {"kind":"S4", "run_id":baseline_run}
    baseline_run = safe_task_id(selection["run_id"])
    if selection["kind"] == "S6":
        parent_runtime, parent_output = task_directories(ROOT, baseline_run)
        if not (parent_runtime / "session.sqlite").is_file():
            raise ValueError("当前基线任务尚未生成或不可用；不隐式回退到旧版本")
        parent_store = SessionStore(parent_runtime / "session.sqlite")
        parent_manifest = parent_store.task()["manifest"]
        validate_input_lock(ROOT, parent_manifest["input_lock"])
        baseline = verify_published_output(parent_output, parent_store)
        protocol = json.loads((ROOT / "protocols/revenue_quality_v1.json").read_text(encoding="utf-8"))
        if baseline.get("trace") != protocol["nodes"] or any(not c["evidence_ids"] for c in baseline["claims"] if c["kind"]=="DISCLOSED"):
            raise ValueError("当前B1基线流程或公司披露引用不完整")
        baseline = deepcopy(baseline)
        audit = {"kind":"S6_CURRENT_B1", "run_id":baseline_run, "published_sources_verified":True,
                 "input_lock_verified":True, "claims_retain_provenance":True}
    elif selection["kind"] == "S4":
        spec = importlib.util.spec_from_file_location("b2_baseline_audit", ROOT / "scripts/s5/run_supplement.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        audit = module.validate_baseline(baseline_run)
        baseline = deepcopy(json.loads((ROOT / "runs/s4" / baseline_run / "final_state.json").read_text(encoding="utf-8")))
    else:
        raise ValueError("未知基线类型")
    gate = json.loads((ROOT / "runs/s5/s5-gate-20261001-01/gate_report.json").read_text(encoding="utf-8"))
    if gate["decision"] != "GO" or not gate["all_checks_passed"]:
        raise ValueError("G5未通过")
    context = ResearchContext.model_validate(baseline["context"]).model_copy(update={"run_id": task_id})
    corpus, data, rows, evidence, checks, locks = source_inputs(ROOT, context)
    if baseline["observations"] != [o.model_dump(mode="json") for o in rows]:
        raise ValueError("B1财务事实与来源不一致")
    baseline["evidence"] = list({**{e.evidence_id: e.model_dump(mode="json") for e in evidence},
                                 **{e["evidence_id"]: e for e in baseline["evidence"]}}.values())
    fault = {"type": "NONE"}
    if scenario == "missing_prior_revenue":
        target = next(o for o in baseline["observations"] if o["company_id"] == "002371.SZ" and o["fiscal_year"] == 2023 and o["metric_id"] == "revenue")
        baseline["observations"] = [o for o in baseline["observations"] if o["observation_id"] != target["observation_id"]]
        baseline = refresh_financials(baseline, context)
        fault = {"type": "EXPLICIT_FAULT_INJECTION", "removed_observation_id": target["observation_id"], "note": "只改变派生输入视图，原来源/B1不改，不算自然缺失"}
    policy = json.loads((ROOT / "configs/s6/runtime.json").read_text(encoding="utf-8"))
    policy.update(workflow="B2", execution_mode=mode, baseline_run=baseline_run, baseline_kind=selection["kind"], scenario=scenario)
    if scenario == "waiting_input":
        policy["initial_gaps"] = [{"gap_id": "s6-explicit-source-request", "company_id": context.company_ids[0],
            "gap_type": "SOURCE_REQUEST", "severity": "CRITICAL", "description": "故障实验：当前快照缺独立资料，需要人类选择下一步",
            "closing_condition": "NEW_SNAPSHOT_OR_USER_INPUT"}]
        fault = {"type": "EXPLICIT_FAULT_INJECTION", "note": "等待态流程实验，不声称存在真实更正版"}
    return context, baseline, policy, audit, fault


def create(task_id, mode, scenario="natural", baseline_run=None, hook=None, fault_lab=None):
    runtime, output = task_directories(ROOT, task_id)
    context, baseline, policy, audit, fault = prepare(task_id, mode, scenario, baseline_run)
    if fault_lab:
        policy["fault_lab"] = fault_lab
    if (runtime / "session.sqlite").exists() or output.exists():
        raise FileExistsError("任务ID已使用，须恢复或新ID")
    runtime.mkdir(parents=True, exist_ok=False)
    for name, payload in (("baseline.json", baseline), ("policy.json", policy), ("baseline_revalidation.json", audit), ("fault_injection.json", fault)):
        atomic_artifact(runtime / name, json_text(payload))
    store = SessionStore(runtime / "session.sqlite")
    deps, corpus, locks, guard, model, checks = make_supplement_dependencies(ROOT, context, store, policy, baseline)
    atomic_artifact(runtime / "source_checks.json", json_text(checks))
    if mode == "LIVE":
        atomic_artifact(runtime / "model_config.json", json_text(model))
    for path in [*runtime.glob("*.json"), Path(__file__), ROOT / "scripts/s6/supplement_fault_worker.py", ROOT / "scripts/s5/run_supplement.py", ROOT / "scripts/s4/finalize_g4.py", ROOT / "scripts/s4/run_research.py", ROOT / "runs/s5/s5-gate-20261001-01/gate_report.json"]:
        locks[path.relative_to(ROOT).as_posix()] = file_sha256(path)
    if baseline_run is None:
        path = ROOT / "configs/s6/baseline.json"
        locks[path.relative_to(ROOT).as_posix()] = file_sha256(path)
    ex = SupplementSession(ROOT, task_id, deps, corpus.documents, baseline, hook=hook)
    ex.create(context, locks, policy)
    return ex, guard


def reopen(task_id, hook=None, *, require_model=True):
    safe_task_id(task_id)
    runtime, _ = task_directories(ROOT, task_id)
    if not (runtime / "session.sqlite").is_file():
        raise ValueError("任务不存在")
    store = SessionStore(runtime / "session.sqlite")
    manifest = store.task()["manifest"]
    validate_input_lock(ROOT, manifest["input_lock"])
    context = ResearchContext.model_validate(manifest["context"])
    baseline = json.loads((runtime / "baseline.json").read_text(encoding="utf-8"))
    capability_policy = dict(manifest["policy"])
    if not require_model:
        capability_policy["execution_mode"] = "RULES"
    deps, corpus, _, guard, _, _ = make_supplement_dependencies(ROOT, context, store, capability_policy, baseline)
    return SupplementSession(ROOT, task_id, deps, corpus.documents, baseline, hook=hook), guard


def main():
    parser = argparse.ArgumentParser(description="S6 B2持久补查、澄清、取消、追问和来源复核记忆")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("create", "resume", "status", "cancel", "clarify", "followup", "history", "remember", "memory-read"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--task-id", required=True)
        if name == "create":
            cmd.add_argument("--mode", choices=["RULES", "LIVE"], required=True)
            cmd.add_argument("--scenario", choices=["natural", "missing_prior_revenue", "waiting_input"], default="natural")
        if name == "clarify":
            cmd.add_argument("--request-id", required=True)
            cmd.add_argument("--gap-id", required=True)
            cmd.add_argument("--decision", choices=["continue_with_limitations", "new_snapshot_required"], required=True)
        if name == "followup":
            cmd.add_argument("--question", required=True)
            cmd.add_argument("--turn-id")
            cmd.add_argument("--topic", choices=["revenue", "cash_flow", "receivables", "all"])
            cmd.add_argument("--companies", nargs="+")
            cmd.add_argument("--as-of", type=date.fromisoformat)
            cmd.add_argument("--snapshot")
    args = parser.parse_args()
    task_id = safe_task_id(args.task_id)
    if args.command == "create":
        ex, guard = create(task_id, args.mode, args.scenario)
    else:
        runtime, _ = task_directories(ROOT, task_id)
        path = runtime / "session.sqlite"
        if not path.is_file():
            raise ValueError("任务不存在")
        store = SessionStore(path)
        task = store.task()
        if args.command == "status":
            print(json.dumps({"task_id": task_id, "status": task["status"], "cancel_requested": task["cancel_requested"], "workflow": task["manifest"]["policy"].get("workflow")}, ensure_ascii=False))
            return
        if args.command == "cancel":
            print(json.dumps({"requested": store.cancel()}))
            return
        ex, guard = reopen(task_id, require_model=args.command == "resume")
        ctx = ResearchContext.model_validate(task["manifest"]["context"])
        if args.command == "clarify":
            print(json.dumps(ex.clarify(args.request_id, args.gap_id, args.decision), ensure_ascii=False))
            return
        if args.command == "history":
            print(json.dumps({"turns": store.read_turns(ctx), "clarifications": ex.pending_clarifications()}, ensure_ascii=False))
            return
        if args.command in {"remember", "memory-read"}:
            _, _, rows, _, _, _ = source_inputs(ROOT, ctx)
            verified = [o.model_dump(mode="json") for o in rows]
            memory = SessionStore(ROOT / "storage/s6/verified_memory.sqlite")
            if args.command == "remember":
                state = verify_published_output(ex.output, store)
                answer = {"inserted": memory.promote_facts(ctx, state["observations"], verified)}
            else:
                answer = {"facts": memory.read_facts(ctx, verified)}
            print(json.dumps(answer, ensure_ascii=False))
            return
        if args.command == "followup":
            verify_published_output(ex.output, store)
            capsule = json.loads((ex.output / "context_capsule.json").read_text(encoding="utf-8"))
            changes = {name: getattr(args, arg) for name, arg in (("company_ids", "companies"), ("as_of_date", "as_of"), ("corpus_snapshot_id", "snapshot")) if getattr(args, arg) is not None}
            requested = ResearchContext.model_validate(ctx.model_dump(mode="python") | changes)
            answer = supplement_followup(capsule, requested, args.question, args.topic)
            turn = args.turn_id or "turn-" + uuid.uuid4().hex
            store.save_turn(turn, args.question, requested, answer)
            print(json.dumps({"turn_id": turn, **answer}, ensure_ascii=False))
            return
    try:
        state = ex.run()
        print(json.dumps({"task_id": task_id, "status": state.get("execution_status", state.get("status")),
            "model_calls": guard.total_calls if guard else 0, "estimated_cost_usd": str(guard.reserved_cost) if guard else "0",
            "workflow": "B2", "execution_mode": ex.store.task()["manifest"]["policy"]["execution_mode"]}, ensure_ascii=False))
    except (Cancelled, UnsafeResume):
        print(json.dumps({"task_id": task_id, "status": ex.store.task()["status"]}))
        raise SystemExit(2) from None


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    try:
        main()
    except UnsafeResume:
        # Input integrity can fail while rebuilding capabilities, before run().
        if "--task-id" in sys.argv:
            task_id = safe_task_id(sys.argv[sys.argv.index("--task-id") + 1])
            path = ROOT / "storage/s6/tasks" / task_id / "session.sqlite"
            if path.exists():
                store = SessionStore(path)
                store.set_status("BLOCKED")
                store.event({"event": "BLOCKED", "reason": "INPUT_INTEGRITY_FAILURE"})
        print(json.dumps({"status": "BLOCKED", "error_type": "UnsafeResume"}))
        raise SystemExit(2) from None
