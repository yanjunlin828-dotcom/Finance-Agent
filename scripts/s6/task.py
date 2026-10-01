"""S6 local single-user CLI. Every model replay is explicitly labelled."""
from __future__ import annotations
import argparse
from datetime import date
import json
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from finresearch.contracts import stable_sha256
from finresearch.contracts.research import ResearchContext
from finresearch.retrieval.corpus import file_sha256
from finresearch.storage.session_store import SessionStore, safe_task_id, Cancelled, UnsafeResume
from finresearch.workflow.session_executor import SessionExecutor, validate_input_lock, verify_published_output
from finresearch.workflow.session_inputs import make_dependencies, source_inputs, checked_path
from finresearch.workflow.session_context import answer_followup


def load_store(task_id):
    task_id = safe_task_id(task_id)
    path = ROOT / "storage/s6/tasks" / task_id / "session.sqlite"
    if not path.is_file() or not path.resolve().is_relative_to(ROOT.resolve()):
        raise ValueError("任务不存在或越出项目")
    return SessionStore(path)


def main():
    parser = argparse.ArgumentParser(description="S6可靠任务和受限追问；不实现S5自主补查")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create")
    create.add_argument("--task-id", required=True)
    create.add_argument("--mode", required=True, choices=["LIVE", "REPLAY"])
    create.add_argument("--replay-run", help="项目内S4 final_state.json路径；仅REPLAY使用")
    create.add_argument("--companies", nargs="+", default=["002371.SZ", "688072.SH", "688082.SH"])
    create.add_argument("--as-of", type=date.fromisoformat, default=date(2025, 4, 30))
    create.add_argument("--snapshot", default="s3-semiconductor-equipment-ar-v1")
    create.add_argument("--parent-task", help="范围变更时的来源任务，仅作追溯，不复用旧结论")
    for command in ("resume", "cancel", "status", "followup", "history", "remember", "memory-read"):
        cmd = sub.add_parser(command)
        cmd.add_argument("--task-id", required=True)
        if command == "followup":
            cmd.add_argument("--question", required=True)
            cmd.add_argument("--turn-id", help="重复请求使用相同ID；不提供时产生新ID")
            cmd.add_argument("--topic", choices=["revenue", "cash_flow", "receivables", "all"])
            cmd.add_argument("--companies", nargs="+")
            cmd.add_argument("--as-of", type=date.fromisoformat)
            cmd.add_argument("--snapshot")
    args = parser.parse_args()
    task_id = safe_task_id(args.task_id)
    if args.command == "create":
        # Explicit upstream gate check; old G1 semantic NO_GO remains independent.
        for name in ("runs/s3/s3-gate-20260930-01/gate_report.json", "runs/s4/s4-gate-20260930-01/gate_report.json"):
            gate = json.loads((ROOT / name).read_text(encoding="utf-8"))
            if gate.get("decision") != "GO" or not gate.get("all_checks_passed"):
                raise ValueError("上游限定门禁没有通过")
        protocol = json.loads((ROOT / "protocols/revenue_quality_v1.json").read_text(encoding="utf-8"))
        ctx = ResearchContext(run_id=task_id, company_ids=args.companies, fiscal_years=tuple(protocol["fiscal_years"]),
                              as_of_date=args.as_of, corpus_snapshot_id=args.snapshot, protocol_id=protocol["protocol_id"],
                              protocol_version=protocol["version"], protocol_config_sha256=stable_sha256(protocol))
        policy = json.loads((ROOT / "configs/s6/runtime.json").read_text(encoding="utf-8"))
        policy["execution_mode"] = args.mode
        if args.mode == "REPLAY" and not args.replay_run or args.mode == "LIVE" and args.replay_run:
            raise ValueError("REPLAY须提供回放运行；LIVE不能提供回放输出")
        policy["replay_path"] = args.replay_run
        if args.parent_task:
            parent = load_store(args.parent_task).task()
            policy["parent_task_id"] = args.parent_task
            policy["parent_scope"] = parent["manifest"]["context"]
        store = SessionStore(ROOT / "storage/s6/tasks" / task_id / "session.sqlite")
        replay = checked_path(ROOT, args.replay_run, "runs/s4") if args.replay_run else None
        deps, corpus, locks, guard = make_dependencies(ROOT, ctx, store, policy, replay)
        locks[Path(__file__).relative_to(ROOT).as_posix()] = file_sha256(Path(__file__))
        ex = SessionExecutor(ROOT, task_id, deps, corpus.documents)
        ex.create(ctx, locks, policy)
    else:
        store = load_store(task_id)
        task = store.task()
        manifest = task["manifest"]
        ctx = ResearchContext.model_validate(manifest["context"])
        if args.command == "status":
            print(json.dumps({"task_id": task_id, "status": task["status"], "cancel_requested": task["cancel_requested"],
                              "execution_mode": manifest["policy"]["execution_mode"]}, ensure_ascii=False))
            return
        if args.command == "cancel":
            print(json.dumps({"requested": store.cancel(), "boundary": "COOPERATIVE"}))
            return
        validate_input_lock(ROOT, manifest["input_lock"])
        policy = manifest["policy"]
        if args.command == "history":
            print(json.dumps({"turns": store.read_turns(ctx)}, ensure_ascii=False))
            return
        if args.command in {"remember", "memory-read"}:
            _, _, rows, _, _, _ = source_inputs(ROOT, ctx)
            verified = [o.model_dump(mode="json") for o in rows]
            memory = SessionStore(ROOT / "storage/s6/verified_memory.sqlite")
            if args.command == "remember":
                if task["status"] not in {"COMPLETED", "PARTIAL"}:
                    raise ValueError("任务未完成，不能提升事实记忆")
                state = verify_published_output(ROOT / "runs/s6" / task_id, store)
                result = {"inserted": memory.promote_facts(ctx, state["observations"], verified)}
            else:
                result = {"facts": memory.read_facts(ctx, verified)}
            print(json.dumps(result, ensure_ascii=False))
            return
        if args.command == "followup":
            if task["status"] not in {"COMPLETED", "PARTIAL"}:
                raise ValueError("任务尚无可用报告")
            verify_published_output(ROOT / "runs/s6" / task_id, store)
            capsule = json.loads((ROOT / "runs/s6" / task_id / "context_capsule.json").read_text(encoding="utf-8"))
            changes = {name: getattr(args, arg) for name, arg in (("company_ids", "companies"), ("as_of_date", "as_of"), ("corpus_snapshot_id", "snapshot")) if getattr(args, arg) is not None}
            requested = ResearchContext.model_validate(ctx.model_dump(mode="python") | changes)
            answer = answer_followup(capsule, requested, args.question, args.topic)
            turn_id = args.turn_id or "turn-" + uuid.uuid4().hex
            store.save_turn(turn_id, args.question, requested, answer)
            print(json.dumps({"turn_id": turn_id, **answer}, ensure_ascii=False))
            return
        replay = checked_path(ROOT, policy["replay_path"], "runs/s4") if policy.get("replay_path") else None
        deps, corpus, locks, guard = make_dependencies(ROOT, ctx, store, policy, replay)
        ex = SessionExecutor(ROOT, task_id, deps, corpus.documents)
    try:
        state = ex.run()
        print(json.dumps({"task_id": task_id, "status": state["execution_status"], "validation": state["validation"]["status"],
                          "execution_mode": policy["execution_mode"], "model_calls": guard.total_calls if guard else 0,
                          "accounted_cost_usd": str(guard.reserved_cost) if guard else "0",
                          "scope": "B1_ONLY_S5_PENDING"}, ensure_ascii=False))
    except (Cancelled, UnsafeResume) as exc:
        print(json.dumps({"task_id": task_id, "status": ex.store.task()["status"], "error_type": type(exc).__name__}))
        raise SystemExit(2) from None


if __name__ == "__main__":
    # Preserve PDF glyphs in Windows redirected output; never strip source text.
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    main()
