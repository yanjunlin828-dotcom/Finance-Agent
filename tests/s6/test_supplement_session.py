"""B2 state/intent recovery invariants, with real financial source records."""
from dataclasses import replace
import json
import pytest

from finresearch.contracts import stable_sha256
from finresearch.contracts.supplement import SupplementGap, ToolResult
from finresearch.retrieval.corpus import file_sha256
from finresearch.storage.session_store import Cancelled, UnsafeResume
from finresearch.workflow.supplement_session import SupplementSession, supplement_followup, fact_view, task_directories
from finresearch.workflow.session_inputs import source_inputs
from s5.helpers import setup, ROOT


class ProcessDeath(BaseException):
    pass


@pytest.fixture(scope="module")
def sources():
    _, ctx, _, _, _ = setup()
    corpus, _, _, financial, _, _ = source_inputs(ROOT, ctx)
    return corpus, financial


def create(tmp_path, sources, *, waiting=False, mode="RULES", hook=None):
    gaps = [SupplementGap(gap_id="waiting-source-request", company_id="002371.SZ", gap_type="SOURCE_REQUEST",
        severity="CRITICAL", description="需要补充资料", closing_condition="NEW_SNAPSHOT_OR_USER_INPUT")] if waiting else None
    def result(c, a):
        if waiting and a.gap_id == "waiting-source-request":
            return ToolResult(action_id=a.action_id, status="NEEDS_INPUT", note="缺独立资料")
        if a.tool == "QUERY_METRIC":
            row = next(r for r in trusted if r["company_id"] == a.company_id and r["metric_id"] == a.arguments["metric_id"] and r["fiscal_year"] == a.arguments["fiscal_year"])
            return ToolResult(action_id=a.action_id, status="FOUND", observations=[row], note="来源核验")
        return ToolResult(action_id=a.action_id, status="NO_EVIDENCE", note="暂无资料")
    baseline, ctx, deps, calls, trusted = setup(remove_metric=not waiting, gaps=gaps)
    original = deps.execute_tool
    if waiting:
        deps = replace(deps, execute_tool=lambda c,a:(calls.append(a),result(c,a))[1])
    corpus, evidence = sources
    baseline["evidence"] = list({**{e.evidence_id:e.model_dump(mode="json") for e in evidence}, **{e["evidence_id"]:e for e in baseline["evidence"]}}.values())
    ctx = ctx.model_copy(update={"run_id":"s6-b2-test"})
    anchor = tmp_path / "anchor.json"
    anchor.write_text("{}", encoding="utf-8")
    ex = SupplementSession(tmp_path, ctx.run_id, deps, corpus.documents, baseline, hook=hook)
    ex.create(ctx, {"anchor.json":file_sha256(anchor)}, {"schema_version":1,"workflow":"B2", "execution_mode":mode,"maximum_context_utf8_bytes":200000})
    return ex, ctx, calls


def reopen(ex, hook=None):
    return SupplementSession(ex.root, ex.task_id, ex.deps, ex.documents, ex.baseline, hook=hook)


@pytest.mark.parametrize("point", ["action:b2-planner:COMMITTED", "action:b2-tool:COMMITTED", "round:CHECKPOINTED", "publish:REPORT_WRITTEN", "publish:MANIFEST_WRITTEN"])
def test_b2_recovery_preserves_counts_and_does_not_repeat_committed_tools(tmp_path, sources, point):
    def hook(at):
        if at == point:
            raise ProcessDeath()
    ex, ctx, calls = create(tmp_path,sources,hook=hook)
    with pytest.raises(ProcessDeath):
        ex.run()
    state = reopen(ex).run()
    assert len(state["observations"]) == 18
    assert len({a.action_id for a in calls}) == len(calls) == state["supplement"]["action_count"]
    assert state["supplement"]["rounds"] <= 3
    assert state["supplement"]["action_count"] <= 6
    assert state["execution_status"] == "PARTIAL"
    assert "trace" not in state and "writer_ids" not in state
    count = len(calls)
    assert reopen(ex).run() == state and len(calls) == count


def test_waiting_without_human_input_never_continues_or_publishes(tmp_path,sources):
    ex,ctx,calls=create(tmp_path,sources,waiting=True)
    state=ex.run()
    assert state["status"]=="WAITING_INPUT" and state["action_count"]==1
    assert not (ex.output/"publication.json").exists()
    assert reopen(ex).run()==state and len(calls)==1


@pytest.mark.parametrize("point",["clarification:COMMITTED","clarification:CHECKPOINTED"])
def test_human_intent_survives_death_and_does_not_close_source_gap(tmp_path,sources,point):
    ex,ctx,calls=create(tmp_path,sources,waiting=True)
    ex.run()
    def hook(at):
        if at==point:
            raise ProcessDeath()
    changed=reopen(ex,hook)
    if point=="clarification:COMMITTED":
        with pytest.raises(ProcessDeath):
            changed.clarify("human-01","waiting-source-request","continue_with_limitations")
    else:
        changed.clarify("human-01","waiting-source-request","continue_with_limitations")
        with pytest.raises(ProcessDeath):
            changed.run()
    final=reopen(ex).run()
    gap=next(g for g in final["supplement"]["gaps"] if g["gap_id"]=="waiting-source-request")
    assert gap["status"]=="LIMITED" and final["execution_status"]=="PARTIAL"
    assert len(calls)==final["supplement"]["action_count"] <= 6
    assert len({a.action_id for a in calls})==len(calls)
    assert reopen(ex).clarify("human-01","waiting-source-request","continue_with_limitations")["idempotent"]
    with pytest.raises(ValueError,match="冲突"):
        reopen(ex).clarify("human-01","waiting-source-request","new_snapshot_required")


def test_new_snapshot_request_keeps_waiting_and_old_sources(tmp_path,sources):
    ex,ctx,calls=create(tmp_path,sources,waiting=True)
    first=ex.run()
    response=ex.clarify("new-source","waiting-source-request","new_snapshot_required")
    assert response["status"]=="NEW_RUN_REQUIRED"
    assert reopen(ex).run()==first and len(calls)==1
    assert not(ex.output/"publication.json").exists()


@pytest.mark.parametrize("point",["action:b2-planner:COMMITTED","action:b2-tool:COMMITTED","round:CHECKPOINTED","publish:BEFORE"])
def test_cancel_b2_stops_next_action_and_never_publishes(tmp_path,sources,point):
    ex,ctx,calls=create(tmp_path,sources)
    ex.hook=lambda at:ex.store.cancel() if at==point else None
    with pytest.raises(Cancelled):
        ex.run()
    count=len(calls)
    with pytest.raises(Cancelled):
        reopen(ex).run()
    assert len(calls)==count and ex.store.task()["status"]=="CANCELLED"
    assert not(ex.output/"publication.json").exists()


def test_unknown_model_result_does_not_reissue(tmp_path,sources):
    ex,ctx,calls=create(tmp_path,sources,mode="LIVE")
    attempted=[]
    def model(*args):
        attempted.append(1)
        raise ProcessDeath()
    ex.deps=replace(ex.deps,select_actions=model)
    with pytest.raises(ProcessDeath):
        ex.run()
    with pytest.raises(UnsafeResume):
        reopen(ex).run()
    assert len(attempted)==1 and not calls and ex.store.task()["status"]=="BLOCKED"


def test_b2_changed_inputs_block_before_more_calls(tmp_path,sources):
    ex,ctx,calls=create(tmp_path,sources,hook=lambda at:(_ for _ in ()).throw(ProcessDeath()) if at=="round:CHECKPOINTED" else None)
    with pytest.raises(ProcessDeath):
        ex.run()
    count=len(calls)
    (tmp_path/"anchor.json").write_text('{"new":true}',encoding="utf-8")
    with pytest.raises(UnsafeResume):
        reopen(ex).run()
    assert len(calls)==count and ex.store.task()["status"]=="BLOCKED"


def test_b2_followup_contains_new_observations_and_stop_ledger(tmp_path,sources):
    ex,ctx,calls=create(tmp_path,sources)
    final=ex.run()
    capsule=json.loads((ex.output/"context_capsule.json").read_text(encoding="utf-8"))
    answer=supplement_followup(capsule,ctx,"收入增长情况", "revenue")
    assert len(answer["observations"])==18
    assert answer["supplement"]["gaps"]==final["supplement"]["gaps"]
    assert answer["supplement"]["stop_reason"]==final["supplement"]["stop_reason"]
    assert all("observations" not in h.get("result",{}) for h in answer["supplement"]["history"])
    shifted=ctx.model_copy(update={"corpus_snapshot_id":"new-snapshot"})
    answer=supplement_followup(capsule,shifted,"收入", "revenue")
    assert answer["status"]=="NEW_RUN_REQUIRED" and not answer["claims"] and "supplement" not in answer


def test_b2_capsule_tamper_is_rejected(tmp_path,sources):
    ex,ctx,calls=create(tmp_path,sources)
    ex.run()
    capsule=json.loads((ex.output/"context_capsule.json").read_text(encoding="utf-8"))
    capsule["payload"]["supplement"]["stop_reason"]="ALL_CERTIFIED"
    with pytest.raises(ValueError,match="摘要"):
        supplement_followup(capsule,ctx,"收入","revenue")


def test_invalid_clarification_cannot_modify_nonwaiting_gap(tmp_path,sources):
    ex,ctx,calls=create(tmp_path,sources,waiting=True)
    ex.run()
    with pytest.raises(ValueError,match="缺口"):
        ex.clarify("attack-01","other-gap","continue_with_limitations")
    assert ex.store.task()["status"]=="WAITING_INPUT" and not ex.pending_clarifications()


def test_resolved_directory_escape_rejected_before_database_write(tmp_path, monkeypatch):
    from pathlib import Path
    original = Path.resolve
    def resolved(path, *args, **kwargs):
        actual = original(path, *args, **kwargs)
        if "tasks" in path.parts:
            return tmp_path.parent / "outside-task"
        return actual
    monkeypatch.setattr(Path, "resolve", resolved)
    with pytest.raises(ValueError, match="越出"):
        task_directories(tmp_path, "safe-looking-id")
    assert not (tmp_path / "storage").exists()
