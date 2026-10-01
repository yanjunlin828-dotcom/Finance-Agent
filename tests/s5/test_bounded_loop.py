import pytest
from finresearch.contracts.supplement import SupplementGap, ToolResult, ActionPlan
from finresearch.workflow.bounded_supplement import compile_supplement_graph, render_supplement
from .helpers import setup


def invoke(baseline, ctx, deps):
    return compile_supplement_graph(deps).invoke({"context": ctx.model_dump(mode="json"), "baseline": baseline})


def test_real_source_fixture_missing_previous_revenue_is_repaired_and_recomputed():
    baseline, ctx, deps, calls, trusted = setup(remove_metric=True)
    state = invoke(baseline, ctx, deps)
    gaps = [g for g in state["gaps"] if g["gap_type"] == "MISSING_METRIC"]
    assert gaps and all(g["status"] == "RESOLVED" for g in gaps)
    assert sorted(state["current"]["observations"], key=lambda o: o["observation_id"]) == sorted(trusted, key=lambda o: o["observation_id"])
    assert len(state["current"]["calculations"]["002371.SZ"]) == 8
    assert calls[0].tool == "QUERY_METRIC" and state["status"] == "PARTIAL"
    assert len(baseline["observations"]) == 17
    original, _, _, _, _ = setup()
    assert {c["claim_id"] for c in state["current"]["claims"] if c["kind"] == "INFERENCE"} == {c["claim_id"] for c in original["claims"] if c["kind"] == "INFERENCE"}
    known_claims = {c["claim_id"] for c in state["current"]["claims"]}
    assert all(set(g["affected_ids"]).issubset(known_claims) for g in state["current"]["gaps"] if g["gap_type"] == "MISSING_COUNTEREVIDENCE")
    assert render_supplement(state, deps) == state["report"]


def test_complete_inputs_do_not_call_planner_or_tools():
    baseline, ctx, deps, calls, _ = setup(gaps=[], planner=lambda *a: pytest.fail("no unnecessary LLM calls"))
    state = invoke(baseline, ctx, deps)
    assert state["status"] == "PARTIAL" and state["action_count"] == 0 and not calls


@pytest.mark.parametrize("kind,expected_tool", [("TABLE_REVIEW", "REVIEW_TABLE"), ("SOURCE_VERSION", "VERIFY_VERSION"),
                                              ("SOURCE_REQUEST", "REQUEST_CLARIFICATION"), ("MODEL_FAILURE", "REQUEST_CLARIFICATION")])
def test_new_source_or_ambiguous_scope_requires_input(kind, expected_tool):
    baseline, ctx, deps, calls, _ = setup()
    deps.initial_gaps = [SupplementGap(gap_id="input-gap", company_id=ctx.company_ids[0], gap_type=kind, severity="CRITICAL",
                                      description="需要复核", closing_condition="NEW_SNAPSHOT_OR_USER_INPUT")]
    deps.execute_tool = lambda c, a: ToolResult(action_id=a.action_id, status="NEEDS_INPUT", note="不能扩大快照")
    state = invoke(baseline, ctx, deps)
    assert state["status"] == "WAITING_INPUT" and state["history"][0]["action"]["tool"] == expected_tool
    assert state["current"]["observations"] == baseline["observations"]


def test_no_evidence_stops_with_bounded_actions_and_never_means_no_disclosure():
    baseline, ctx, deps, calls, _ = setup()
    state = invoke(baseline, ctx, deps)
    assert state["rounds"] <= 3 and len(calls) <= 6 and state["status"] == "PARTIAL"
    assert len({a.action_id for a in calls}) == len(calls)
    assert any("未召回不等于未披露" in (g["resolution_note"] or "") for g in state["gaps"])


@pytest.mark.parametrize("limit_name,limit,reason", [("maximum_rounds", 1, "ROUND_LIMIT"), ("maximum_actions", 1, "ACTION_BUDGET"),
                                                     ("maximum_no_progress_rounds", 1, "NO_PROGRESS")])
def test_budget_and_no_progress_boundaries(limit_name, limit, reason):
    baseline, ctx, deps, calls, _ = setup()
    deps.policy = deps.policy | {limit_name: limit}
    state = invoke(baseline, ctx, deps)
    assert state["status"] == "PARTIAL" and state["stop_reason"] == reason


def test_model_proposes_unknown_action_and_path_stops_without_tool_call():
    baseline, ctx, deps, calls, _ = setup(planner=lambda *a: ActionPlan(selected_action_ids=["read-api-key"]))
    state = invoke(baseline, ctx, deps)
    assert state["status"] == "PARTIAL" and not calls


def test_tool_failure_preserves_existing_results_and_is_not_success():
    baseline, ctx, deps, _, _ = setup()
    deps.execute_tool = lambda *a: (_ for _ in ()).throw(RuntimeError("tool unavailable"))
    state = invoke(baseline, ctx, deps)
    assert state["status"] == "PARTIAL" and state["stop_reason"] == "TOOL_FAILURE"
    assert state["current"]["observations"] == baseline["observations"]


@pytest.mark.parametrize("mutation", [{"company_id": "000001.SZ"}, {"standard_value": "999"}, {"document_published_on": "2030-01-01"}])
def test_untrusted_or_future_observation_never_enters_state(mutation):
    baseline, ctx, deps, _, trusted = setup(remove_metric=True)
    target = next(o for o in trusted if o["company_id"] == "002371.SZ" and o["metric_id"] == "revenue" and o["fiscal_year"] == 2023)
    deps.execute_tool = lambda c, a: ToolResult(action_id=a.action_id, status="FOUND", observations=[target | mutation], note="malicious")
    with pytest.raises(ValueError):
        invoke(baseline, ctx, deps)
    assert len(baseline["observations"]) == 17


def test_wrong_action_id_and_unverified_evidence_are_hard_failures():
    baseline, ctx, deps, _, _ = setup()
    deps.execute_tool = lambda c, a: ToolResult(action_id="wrong", status="FOUND", note="wrong identity")
    with pytest.raises(ValueError):
        invoke(baseline, ctx, deps)

