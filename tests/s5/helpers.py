from copy import deepcopy
from pathlib import Path
import json
from finresearch.contracts.research import ResearchContext
from finresearch.contracts.supplement import ActionPlan, ToolResult
from finresearch.workflow.bounded_supplement import SupplementDependencies, refresh_financials

ROOT = Path(__file__).resolve().parents[2]


def setup(remove_metric=False, gaps=None, result=None, planner=None):
    baseline = json.loads((ROOT / "runs/s4/s4-b1-live-20260930-05/final_state.json").read_text(encoding="utf-8"))
    baseline["evidence"] = json.loads((ROOT / "runs/s4/s4-b1-live-20260930-05/evidence.json").read_text(encoding="utf-8"))
    ctx = ResearchContext.model_validate(baseline["context"])
    trusted = deepcopy(baseline["observations"])
    evidence = deepcopy(baseline["evidence"])
    if remove_metric:
        baseline["observations"] = [o for o in trusted if not (o["company_id"] == "002371.SZ" and o["fiscal_year"] == 2023 and o["metric_id"] == "revenue")]
        baseline = refresh_financials(baseline, ctx)
    calls = []
    def tool(context, action):
        calls.append(action)
        if result:
            return result(context, action)
        if action.tool == "QUERY_METRIC":
            row = next(o for o in trusted if o["company_id"] == action.company_id and o["metric_id"] == action.arguments["metric_id"] and o["fiscal_year"] == action.arguments["fiscal_year"])
            return ToolResult(action_id=action.action_id, status="FOUND", observations=[row], note="source-verified metric")
        return ToolResult(action_id=action.action_id, status="NO_EVIDENCE", note="当前查询无原文")
    policy = json.loads((ROOT / "configs/s5/controller.json").read_text(encoding="utf-8"))
    deps = SupplementDependencies(policy, planner or (lambda c, r, candidates, cap: ActionPlan(selected_action_ids=[a.action_id for a in candidates[:cap]])),
                                  tool, lambda ctx, row: row.model_dump(mode="json") in trusted,
                                  lambda ctx, item: item.model_dump(mode="json") in evidence,
                                  lambda ctx, evidence: [], {}, set(), initial_gaps=gaps)
    return baseline, ctx, deps, calls, trusted
