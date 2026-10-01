"""Real-source B2 process-exit lab. Synthetic faults never contact a provider."""
from dataclasses import replace
from decimal import Decimal
import argparse
import os
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from supplement_task import create, reopen, ROOT
from finresearch.model.persistent_budget import PersistentBudgetGuard
from finresearch.storage.session_store import Cancelled
from finresearch.workflow.supplement_session_inputs import make_supplement_dependencies


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--task-id",required=True)
    parser.add_argument("--fault",required=True,choices=["tool_committed","round_checkpointed","report_written","model_pending","cancel_after_tool","waiting_checkpointed","clarification_committed","clarification_checkpointed","budget_exhausted"])
    args=parser.parse_args()
    waiting=args.fault in {"waiting_checkpointed","clarification_committed","clarification_checkpointed"}
    ex,guard=create(args.task_id,"RULES","waiting_input" if waiting else "missing_prior_revenue",fault_lab=args.fault)
    points={"tool_committed":"action:b2-tool:COMMITTED","round_checkpointed":"round:CHECKPOINTED",
            "report_written":"publish:REPORT_WRITTEN","cancel_after_tool":"action:b2-tool:COMMITTED",
            "waiting_checkpointed":"waiting:CHECKPOINTED","clarification_committed":"clarification:COMMITTED",
            "clarification_checkpointed":"clarification:CHECKPOINTED"}
    def hook(point):
        if point==points.get(args.fault):
            if args.fault=="cancel_after_tool":
                ex.store.cancel()
            else:
                os._exit(91)
    ex.hook=hook
    if args.fault in {"model_pending","budget_exhausted"}:
        from finresearch.contracts.research import ResearchContext
        task=ex.store.task()
        _,_,_,_,model,_=make_supplement_dependencies(ROOT,ResearchContext.model_validate(task["manifest"]["context"]),ex.store,task["manifest"]["policy"],ex.baseline)
        guard=PersistentBudgetGuard(model,ex.runtime/"budget.sqlite")
        if args.fault=="model_pending":
            def pending(*a):
                guard.reserve_call("b2-plan-1",Decimal("0.001"))
                os._exit(91)
            ex.deps=replace(ex.deps,select_actions=pending)
        else:
            for probe in model["online_probe_ids"]:
                reservation=guard.reserve_call(probe,Decimal("0.001"))
                guard.settle_call(reservation,Decimal("0.001"))
            ex.deps=replace(ex.deps,select_actions=lambda *a:guard.reserve_call("b2-plan-1",Decimal("0.001")))
    try:
        ex.run()
        if args.fault in {"clarification_committed","clarification_checkpointed"}:
            ex.clarify("human-resume-01","s6-explicit-source-request","continue_with_limitations")
            ex.run()
    except Cancelled:
        pass


if __name__=="__main__":
    main()
