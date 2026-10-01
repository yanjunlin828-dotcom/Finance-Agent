"""Offline fault injection tests; these do not substitute for live B1 runs."""
from datetime import date
import json
from pathlib import Path
import pytest
from langgraph.checkpoint.sqlite import SqliteSaver
from finresearch.contracts import stable_sha256
from finresearch.contracts.research import ResearchContext, HypothesisBatch, DisclosureBatch, WriterPlan
from finresearch.workflow.fixed_research import ResearchDependencies, compile_research_graph

ROOT = Path(__file__).resolve().parents[2]


def setup(fail=None):
    protocol = json.loads((ROOT / "protocols/revenue_quality_v1.json").read_text(encoding="utf-8"))
    observations = json.loads((ROOT / "runs/s4/s4-financial-inputs-20260930-01/observations.json").read_text(encoding="utf-8"))
    from finresearch.contracts import MetricObservation
    rows = [MetricObservation.model_validate(o) for o in observations]
    ctx = ResearchContext(run_id="s4-offline",company_ids=["002371.SZ","688072.SH","688082.SH"],fiscal_years=(2023,2024),as_of_date=date(2025,4,30),
        corpus_snapshot_id="s3-semiconductor-equipment-ar-v1",protocol_id="revenue_quality_v1",protocol_version="1.0.0",protocol_config_sha256=stable_sha256(protocol))
    events = []
    def hypotheses(ctx,company,phenomena):
        if fail == "hypotheses":
            raise RuntimeError("simulated malformed JSON or transport failure")
        return HypothesisBatch.model_validate({"hypotheses":[{"company_id":company,"category":"UNRESOLVED_CAUSE","phenomenon_ids":[phenomena[0].phenomenon_id],
            "rationale":"现有现象不足以判断原因","support_needed":["INDEPENDENT_CONFIRMATION"],"weakening_evidence_needed":["TIMING_DETAIL"]}]})
    def disclose(ctx,company,evidence):
        return DisclosureBatch(company_id=company,selected=[])
    def writer(ctx,claims):
        if fail == "writer":
            return WriterPlan(ordered_claim_ids=["invented"])
        return WriterPlan(ordered_claim_ids=[c.claim_id for c in claims if c.status=="APPROVED"])
    deps=ResearchDependencies(protocol,lambda ctx:rows,hypotheses,lambda ctx,c:[],disclose,writer,event_sink=events.append)
    return deps,ctx,events


def test_fixed_path_repeat_and_reopen_checkpoint(tmp_path):
    deps,ctx,events=setup()
    cfg={"configurable":{"thread_id":ctx.run_id}}
    path=str(tmp_path / "checkpoints.sqlite")
    with SqliteSaver.from_conn_string(path) as saver:
        graph=compile_research_graph(deps,saver)
        state=graph.invoke({"context":ctx.model_dump(mode="json")},cfg)
        assert state["trace"]==deps.protocol["nodes"]
        assert state["validation"]["status"]=="PASS"
        assert state["execution_status"]=="COMPLETED"
        assert len(list(graph.get_state_history(cfg))) >= len(deps.protocol["nodes"])
    with SqliteSaver.from_conn_string(path) as saver:
        graph=compile_research_graph(deps,saver)
        saved=graph.get_state(cfg)
        assert saved.values==state and not saved.next
    repeated=compile_research_graph(deps).invoke({"context":ctx.model_dump(mode="json")})
    assert repeated["trace"]==state["trace"]
    assert repeated["calculations"]==state["calculations"]
    assert "财务比较表" in state["report"]


@pytest.mark.parametrize("failure",["hypotheses","writer"])
def test_model_failure_leaves_partial_report_and_explicit_gap(failure):
    deps,ctx,_=setup(failure)
    state=compile_research_graph(deps).invoke({"context":ctx.model_dump(mode="json")})
    assert state["trace"]==deps.protocol["nodes"]
    assert state["execution_status"]=="PARTIAL"
    assert any(g["gap_type"]=="MODEL_FAILURE" for g in state["gaps"])
    assert state["validation"]["status"]=="PASS"  # Correctness of partial rendering, not success.
    assert "执行状态：PARTIAL" in state["report"]


def test_unlocked_or_changed_protocol_rejected():
    deps,ctx,_=setup()
    with pytest.raises(ValueError,match="指纹"):
        compile_research_graph(deps).invoke({"context":ctx.model_copy(update={"protocol_config_sha256":None}).model_dump(mode="json")})


def test_local_soft_timeout_is_explicit_failed_node():
    deps,ctx,events=setup()
    deps.protocol=deps.protocol | {"node_soft_timeout_seconds":-1}
    ctx=ctx.model_copy(update={"protocol_config_sha256":stable_sha256(deps.protocol)})
    with pytest.raises(TimeoutError,match="soft timeout"):
        compile_research_graph(deps).invoke({"context":ctx.model_dump(mode="json")})
    assert events[-1]["event"]=="FAILED"
