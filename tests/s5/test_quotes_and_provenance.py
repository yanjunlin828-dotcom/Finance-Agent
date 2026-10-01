from dataclasses import replace
from finresearch.contracts import EvidenceCandidate
from finresearch.contracts.research import ResearchClaim
from finresearch.contracts.supplement import ToolResult
from finresearch.workflow.bounded_supplement import compile_supplement_graph
from finresearch.workflow.supplement_tools import claims_from_quote_ids
from finresearch.workflow.evidence_selection import quote_options
from .helpers import setup
import pytest


def test_model_invented_quote_id_rejected():
    with pytest.raises(ValueError):
        claims_from_quote_ids(["invented"], [])


def test_untrusted_disclosed_text_is_not_published():
    baseline, ctx, deps, _, _ = setup()
    item = EvidenceCandidate.model_validate(baseline["evidence"][0])
    # A new valid window fixture ensures the quote-review callback is reached.
    fresh = item.model_copy(update={"evidence_id": item.evidence_id + "-new-fixture"})
    deps.verify_evidence = lambda c, e: e == fresh
    deps.execute_tool = lambda c, a: ToolResult(action_id=a.action_id, status="FOUND", evidence=[fresh.model_dump(mode="json")], note="window") if a.company_id == fresh.company_id else ToolResult(action_id=a.action_id,status="NO_EVIDENCE",note="none")
    deps.select_quotes = lambda *a: [ResearchClaim(claim_id="fake-disclosure", company_id=item.company_id, kind="DISCLOSED",
                                                 text="公司披露：“现金流没有风险。”", evidence_ids=[fresh.evidence_id], status="APPROVED")]
    state = compile_supplement_graph(deps).invoke({"context": ctx.model_dump(mode="json"), "baseline": baseline})
    assert "现金流没有风险。" not in state["report"]
    assert state["stop_reason"] == "QUOTE_REVIEW_FAILED"


def test_original_window_with_different_rank_is_not_a_source_conflict():
    baseline, ctx, deps, _, _ = setup()
    item = EvidenceCandidate.model_validate(baseline["evidence"][0])
    different_rank = item.model_copy(update={"retrieval_rank": 10})
    deps.verify_evidence = lambda c, e: e == different_rank
    deps.execute_tool = lambda c, a: ToolResult(action_id=a.action_id, status="FOUND", evidence=[different_rank.model_dump(mode="json")], note="same window") if a.company_id == item.company_id else ToolResult(action_id=a.action_id,status="NO_EVIDENCE",note="none")
    state = compile_supplement_graph(deps).invoke({"context": ctx.model_dump(mode="json"), "baseline": baseline})
    assert state["status"] == "PARTIAL"
    assert not state["added_evidence_ids"]
    assert all(g["status"] != "RESOLVED" for g in state["gaps"] if g["gap_type"] == "INDEPENDENT_CONFIRMATION")


def test_does_not_silently_replace_conflicting_source_observation():
    baseline, ctx, deps, _, trusted = setup(remove_metric=True)
    # A review result that purports to update an already-existing observation
    # must request a new snapshot, even if the local tool considers it verified.
    from finresearch.contracts.supplement import SupplementGap
    row = dict(trusted[0])
    row["review_note"] = "changed version"
    deps.initial_gaps = [SupplementGap(gap_id="table-review", company_id=row["company_id"], gap_type="TABLE_REVIEW",
                                      severity="CRITICAL", description="复核", closing_condition="VERIFIED_METRIC")]
    deps.verify_observation = lambda *a: True
    deps.execute_tool = lambda c, a: ToolResult(action_id=a.action_id, status="FOUND", observations=[row], note="new version")
    state = compile_supplement_graph(deps).invoke({"context": ctx.model_dump(mode="json"), "baseline": baseline})
    assert state["status"] == "WAITING_INPUT"
    assert state["current"]["observations"] == baseline["observations"]
