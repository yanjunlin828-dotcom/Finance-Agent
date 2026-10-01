"""Process-death lab on verified sources. REPLAY only, never a live quality result."""
from __future__ import annotations
import argparse
from dataclasses import replace
from datetime import date
import json
import os
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from finresearch.contracts import stable_sha256
from finresearch.contracts.research import ResearchContext
from finresearch.retrieval.corpus import file_sha256
from finresearch.storage.session_store import SessionStore, safe_task_id, Cancelled
from finresearch.workflow.session_executor import SessionExecutor
from finresearch.workflow.session_inputs import make_dependencies


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--fault", required=True, choices=["hypotheses_committed", "report_written", "retrieval_start", "model_pending", "cancel_after_model"])
    args = parser.parse_args()
    task_id = safe_task_id(args.task_id)
    protocol = json.loads((ROOT / "protocols/revenue_quality_v1.json").read_text(encoding="utf-8"))
    ctx = ResearchContext(run_id=task_id, company_ids=["002371.SZ", "688072.SH", "688082.SH"], fiscal_years=(2023, 2024),
                          as_of_date=date(2025, 4, 30), corpus_snapshot_id="s3-semiconductor-equipment-ar-v1",
                          protocol_id="revenue_quality_v1", protocol_version="1.0.0", protocol_config_sha256=stable_sha256(protocol))
    policy = json.loads((ROOT / "configs/s6/runtime.json").read_text(encoding="utf-8"))
    policy.update(execution_mode="REPLAY", replay_path="runs/s4/s4-b1-live-20260930-05/final_state.json", fault_lab=args.fault)
    store = SessionStore(ROOT / "storage/s6/tasks" / task_id / "session.sqlite")
    deps, corpus, locks, _ = make_dependencies(ROOT, ctx, store, policy, ROOT / policy["replay_path"])
    for path in (Path(__file__), ROOT / "scripts/s6/task.py"):
        locks[path.relative_to(ROOT).as_posix()] = file_sha256(path)
    if args.fault == "model_pending":
        # Persisted RUNNING operation with no response: deliberately uncertain.
        # No provider is contacted. This tests safety, not provider billing.
        deps = replace(deps, propose_hypotheses=lambda *a: os._exit(91))
    ex = SessionExecutor(ROOT, task_id, deps, corpus.documents)
    ex.create(ctx, locks, policy)
    points = {"hypotheses_committed": "action:hypotheses:COMMITTED", "report_written": "publish:REPORT_WRITTEN",
              "retrieval_start": "node:collect_fixed_evidence:START", "cancel_after_model": "action:hypotheses:COMMITTED"}
    def hook(point):
        if point == points.get(args.fault):
            if args.fault == "cancel_after_model":
                store.cancel()
            else:
                os._exit(91)
    ex.hook = hook
    try:
        ex.run()
    except Cancelled:
        print(json.dumps({"status": "CANCELLED", "execution_mode": "REPLAY"}))


if __name__ == "__main__":
    main()
