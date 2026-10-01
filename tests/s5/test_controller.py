from datetime import date
import pytest
from finresearch.contracts.supplement import SupplementGap, ActionPlan
from finresearch.workflow.supplement_controller import identify_gaps, candidate_actions, approve_plan
from .helpers import setup


def missing_gap(ctx):
    return SupplementGap(gap_id="missing-revenue", company_id=ctx.company_ids[0], gap_type="MISSING_METRIC", severity="CRITICAL",
                         metric_id="revenue", fiscal_year=2023, description="缺上期收入", closing_condition="VERIFIED_METRIC")


def test_missing_metric_precedes_explanatory_search():
    baseline, ctx, _, _, _ = setup(remove_metric=True)
    gaps = identify_gaps(baseline, ctx)
    candidates = candidate_actions(gaps, ctx, set())
    assert candidates[0].tool == "QUERY_METRIC"
    selected = approve_plan(ActionPlan(selected_action_ids=[candidates[0].action_id]), candidates, gaps, ctx, set(), 1, 2)
    assert selected[0].arguments == {"metric_id": "revenue", "fiscal_year": 2023}
    explanation = next(a for a in candidates if a.tool == "SEARCH_DISCLOSURE")
    reversed_model_order = ActionPlan(selected_action_ids=[explanation.action_id, candidates[0].action_id])
    assert approve_plan(reversed_model_order, candidates, gaps, ctx, set(), 2, 2)[0].tool == "QUERY_METRIC"


@pytest.mark.parametrize("selection", [["fake"], ["__duplicate__"], ["__over_budget__"]])
def test_invalid_duplicate_or_over_budget_model_ids_rejected(selection):
    _, ctx, _, _, _ = setup()
    gaps = [missing_gap(ctx)]
    actions = candidate_actions(gaps, ctx, set())
    ids = selection if selection == ["fake"] else [actions[0].action_id] * 2
    with pytest.raises(ValueError):
        approve_plan(ActionPlan(selected_action_ids=ids), actions, gaps, ctx, set(), 1, 2)


@pytest.mark.parametrize("change", [
    {"company_id": "000001.SZ"}, {"as_of_date": date(2030, 1, 1)},
    {"corpus_snapshot_id": "evil"}, {"arguments": {"file": "../../secret"}}, {"tool": "REQUEST_CLARIFICATION"},
])
def test_mutated_scope_or_arguments_rejected(change):
    _, ctx, _, _, _ = setup()
    gaps = [missing_gap(ctx)]
    action = candidate_actions(gaps, ctx, set())[0]
    with pytest.raises(ValueError):
        approve_plan(ActionPlan(selected_action_ids=[action.action_id]), [action.model_copy(update=change)], gaps, ctx, set(), 2, 2)


def test_query_deduplication_not_affected_by_reason_wording():
    _, ctx, _, _, _ = setup()
    gap = missing_gap(ctx)
    actions = candidate_actions([gap, gap.model_copy(update={"gap_id": "other", "description": "同义缺口"})], ctx, set())
    assert len(actions) == 1
    assert not candidate_actions([gap], ctx, {actions[0].action_id.removeprefix("action-")})


def test_independent_causality_cannot_be_resolved_by_source_tools():
    _, ctx, _, _, _ = setup()
    with pytest.raises(ValueError):
        SupplementGap(gap_id="cause", company_id=ctx.company_ids[0], gap_type="INDEPENDENT_CONFIRMATION", severity="MATERIAL",
                      description="证明原因", closing_condition="INDEPENDENT_REVIEW", status="RESOLVED")
    baseline, ctx, _, _, _ = setup()
    gaps = identify_gaps(baseline, ctx)
    limited = [g for g in gaps if g.gap_type in {"INVALID_CALCULATION", "INDEPENDENT_CONFIRMATION"}]
    assert limited and all(g.status == "LIMITED" for g in limited)
    assert not candidate_actions(limited, ctx, set())
