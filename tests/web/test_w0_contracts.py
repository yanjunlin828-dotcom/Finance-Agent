"""Adversarial display contracts and source drift checks, not UI implementation tests."""
import ast
import hashlib
import importlib.util
import json
from pathlib import Path
from typing import get_args

import pytest
from pydantic import ValidationError

from finresearch.contracts.supplement import ToolResult
from finresearch_web.contracts import models as m
from finresearch_web.contracts.rules import action_capabilities, can_auto_enter_report, suggested_panel
from finresearch_web.contracts import vocabulary as v

APP_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = APP_ROOT / "web/fixtures"
MANIFEST = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))


def last_snapshot(loader, name):
    return loader(name)["frames"][-1]["snapshot"]


@pytest.mark.parametrize("entry", MANIFEST["fixtures"], ids=lambda e: e["scenario_id"])
def test_each_fixture_is_labelled_digest_bound_and_valid(entry):
    data = (FIXTURES / entry["path"]).read_bytes()
    assert hashlib.sha256(data).hexdigest() == entry["sha256"]
    fixture = m.ScenarioFixture.model_validate_json(data)
    assert fixture.synthetic_execution == entry["synthetic_execution"]
    for frame in fixture.frames:
        assert frame.expected.result_available == frame.snapshot.publication.result_available
        assert frame.expected.enabled_operations == [a.operation for a in frame.snapshot.task.available_actions if a.enabled]
    if not fixture.synthetic_execution:
        assert all(f.snapshot.provenance.source_artifacts for f in fixture.frames)


def test_core_status_and_station_vocabulary_has_not_drifted():
    tree = ast.parse((APP_ROOT / "src/finresearch/storage/session_store.py").read_text(encoding="utf-8"))
    source_statuses = next({n.value for n in node.elts} for node in ast.walk(tree)
                           if isinstance(node, ast.Set) and all(isinstance(n, ast.Constant) for n in node.elts)
                           and {"CREATED", "BLOCKED"} <= {n.value for n in node.elts})
    assert source_statuses == set(get_args(m.TaskStatus)) == set(v.TASK_LABELS)
    protocol=json.loads((APP_ROOT/"protocols/revenue_quality_v1.json").read_text(encoding="utf-8"))
    assert protocol["nodes"] == [n[0] for n in v.B1_STATIONS]
    tree=ast.parse((APP_ROOT/"src/finresearch/workflow/fixed_research.py").read_text(encoding="utf-8"))
    bindings=next({kw.arg for kw in node.value.keywords} for node in ast.walk(tree)
                  if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=="functions" for t in node.targets))
    assert bindings==set(protocol["nodes"])
    tree=ast.parse((APP_ROOT/"src/finresearch/workflow/bounded_supplement.py").read_text(encoding="utf-8"))
    registered={node.args[0].value for node in ast.walk(tree) if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute)
                and node.func.attr=="add_node" and node.args and isinstance(node.args[0],ast.Constant)}
    assert registered=={n[0] for n in v.B2_STATIONS}
    source=(APP_ROOT / "src/finresearch/workflow/bounded_supplement.py").read_text(encoding="utf-8")
    assert all(reason in source for reason in get_args(m.StopReason))
    assert set(get_args(m.StopReason)) == set(v.STOP_LABELS)
    assert set(get_args(ToolResult.model_fields["status"].annotation)) == set(v.TOOL_RESULT_LABELS)


def test_all_outcomes_have_actual_fixture_records(fixture_data):
    fixtures=[fixture_data(e["scenario_id"]) for e in MANIFEST["fixtures"]]
    statuses={frame["snapshot"]["task"]["task_status"] for f in fixtures for frame in f["frames"]}
    stops={frame["snapshot"]["research"]["supplement"]["stop_reason"] for f in fixtures for frame in f["frames"] if frame["snapshot"]["research"]["supplement"]}
    results={h["result"]["status"] for f in fixtures for frame in f["frames"] if frame["snapshot"]["research"]["supplement"] for h in frame["snapshot"]["research"]["supplement"]["history"] if h["result"]}
    followups={r["status"] for f in fixtures for r in f["followups"]}
    assert statuses == set(get_args(m.TaskStatus))
    assert set(get_args(m.StopReason)) <= stops
    assert results == set(v.TOOL_RESULT_LABELS)
    assert followups == set(get_args(m.FollowupStatus))


def test_publication_and_cancel_races_cannot_be_declared_complete(fixture_data):
    data=last_snapshot(fixture_data,"b1-normal")
    data["runtime"]["cancel_requested"]=True
    with pytest.raises(ValidationError,match="matching terminal"):
        m.TaskSnapshot.model_validate(data)
    data=last_snapshot(fixture_data,"publication-incomplete")
    assert not can_auto_enter_report(m.TaskSnapshot.model_validate(data))
    data["publication"]["result_available"]=True
    with pytest.raises(ValidationError,match="unverified"):
        m.TaskSnapshot.model_validate(data)


@pytest.mark.parametrize("mutation",["missing-file","digest-mismatch","scope-mismatch","business-fail","wrong-terminal"])
def test_historical_result_requires_every_gate(fixture_data,mutation):
    data=last_snapshot(fixture_data,"history-b1")
    p=data["publication"]
    if mutation=="missing-file": p["files"].pop()
    elif mutation=="digest-mismatch": p["files"][0]["digest_matches"]=False
    elif mutation=="scope-mismatch": p["scope_matches"]=False
    elif mutation=="business-fail": p["business_validation"]="FAIL"
    else: data["task"]["task_status"]="RUNNING"
    with pytest.raises(ValidationError): m.TaskSnapshot.model_validate(data)


def test_historical_b2_is_read_only_and_does_not_replay_b1(fixture_data):
    snapshot=m.TaskSnapshot.model_validate(last_snapshot(fixture_data,"history-b2"))
    assert snapshot.task.baseline.origin=="RESULT_REUSE"
    assert snapshot.research.supplement.rounds==3
    assert snapshot.research.supplement.action_count==6
    assert snapshot.task.task_status=="PARTIAL"
    assert snapshot.research.supplement.stop_reason=="ROUND_LIMIT"
    assert snapshot.steps==[] and snapshot.task.created_at is None
    assert snapshot.budget.settled_estimate is None
    assert suggested_panel(snapshot)=="REPORT" and not can_auto_enter_report(snapshot)
    assert {a.operation for a in action_capabilities(snapshot) if a.enabled} <= {"VIEW_REPORT","VIEW_STEPS","VIEW_EVIDENCE"}


@pytest.mark.parametrize("attack",["fake-real-source","fake-historical-write","fake-control-adapter","fake-live-publication","unknown-schema"])
def test_provenance_and_mode_cannot_be_forged(fixture_data,attack):
    data=last_snapshot(fixture_data,"history-b1")
    if attack=="fake-real-source": data["provenance"]["source_artifacts"]=[]
    elif attack=="fake-historical-write": data["task"]["available_actions"]=[{"operation":"CANCEL","enabled":True,"authority":"SERVER"}]
    elif attack=="fake-control-adapter": data["task"]["access_mode"]="LIVE_CONTROL"
    elif attack=="fake-live-publication": data["publication"]["verification"]="SIMULATED_VALID"
    else: data["schema_version"]="2.0.0"
    with pytest.raises(ValidationError): m.TaskSnapshot.model_validate(data)


def test_waiting_decision_and_resume_preserve_limits(fixture_data):
    frames=fixture_data("b2-wait-continue")["frames"]
    assert frames[0]["snapshot"]["task"]["task_status"]=="WAITING_INPUT"
    assert "CLARIFY" in frames[0]["expected"]["enabled_operations"]
    assert "RESUME" not in frames[0]["expected"]["enabled_operations"]
    assert frames[1]["snapshot"]["task"]["task_status"]=="WAITING_INPUT"
    assert "RESUME" in frames[1]["expected"]["enabled_operations"]
    assert frames[2]["snapshot"]["research"]["supplement"]["gaps"][0]["status"]=="LIMITED"
    assert len({f["snapshot"]["research"]["supplement"]["action_count"] for f in frames})==1
    new=last_snapshot(fixture_data,"b2-new-snapshot")
    assert new["task"]["task_status"]=="WAITING_INPUT"
    assert "RESUME" not in {a["operation"] for a in new["task"]["available_actions"] if a["enabled"]}


@pytest.mark.parametrize("name",["unknown-model","hard-failure","b2-new-snapshot"])
def test_unsafe_or_undecided_resume_cannot_be_enabled(fixture_data,name):
    data=last_snapshot(fixture_data,name)
    data["task"]["available_actions"]=[{"operation":"RESUME","enabled":True,"authority":"SIMULATED"}]
    with pytest.raises(ValidationError): m.TaskSnapshot.model_validate(data)


def test_active_worker_cannot_be_resumed_even_with_a_stale_safe_flag(fixture_data):
    data=fixture_data("b2-wait-continue")["frames"][1]["snapshot"]
    data["runtime"]["worker_state"]="ACTIVE"
    with pytest.raises(ValidationError,match="server-confirmed safety"): m.TaskSnapshot.model_validate(data)


def test_cancel_and_connection_have_separate_states(fixture_data):
    frames=fixture_data("cancel-late-result")["frames"]
    assert frames[1]["snapshot"]["task"]["task_status"]=="RUNNING"
    assert frames[1]["snapshot"]["runtime"]["cancel_requested"]
    assert not frames[1]["expected"]["result_available"]
    assert frames[2]["snapshot"]["task"]["task_status"]=="CANCELLED"
    disconnected=fixture_data("reconnect")["frames"][1]["snapshot"]
    assert disconnected["task"]["task_status"]=="RUNNING"
    assert disconnected["runtime"]["worker_state"]=="UNKNOWN"
    assert not any(a["enabled"] for a in disconnected["task"]["available_actions"] if a["operation"] in {"CANCEL","RESUME"})


@pytest.mark.parametrize("busy",["reader_open","text_selection_active","editing_followup","manual_browsing"])
def test_report_ready_does_not_steal_reading_or_editing(fixture_data,busy):
    data=last_snapshot(fixture_data,"rapid-completion")
    assert can_auto_enter_report(m.TaskSnapshot.model_validate(data))
    data["ui"][busy]=True
    snapshot=m.TaskSnapshot.model_validate(data)
    assert not can_auto_enter_report(snapshot)
    assert suggested_panel(snapshot)=="PROCESS"


def test_no_follow_and_reduced_motion_are_ui_only(fixture_data):
    data=last_snapshot(fixture_data,"rapid-completion")
    data["ui"]["reduced_motion"]=True
    assert can_auto_enter_report(m.TaskSnapshot.model_validate(data))
    data["ui"]["follow_progress"]=False
    snapshot=m.TaskSnapshot.model_validate(data)
    assert not can_auto_enter_report(snapshot)
    assert snapshot.task.task_status=="COMPLETED"


@pytest.mark.parametrize("attack",["floating-money","floating-ratio","invalid-with-zero","future-source","wrong-company","guessed-print-page"])
def test_financial_wire_and_evidence_boundaries(fixture_data,attack):
    data=last_snapshot(fixture_data,"history-b1")
    r=data["research"]
    if attack=="floating-money": r["observations"][0]["raw_value"]=float(r["observations"][0]["raw_value"])
    elif attack=="floating-ratio": r["calculations"][0]["value"]=0.123
    elif attack=="invalid-with-zero": r["calculations"][0].update(status="NEGATIVE_BASE",value="0")
    elif attack=="future-source": r["observations"][0]["document_published_on"]="2025-05-01"
    elif attack=="wrong-company": r["claims"][0]["company_id"]="000001.SZ"
    else: r["evidence"][0]["printed_page"]="95"
    with pytest.raises(ValidationError): m.TaskSnapshot.model_validate(data)


def test_unknown_cost_is_not_zero_and_unknown_call_keeps_reservation(fixture_data):
    data=last_snapshot(fixture_data,"unknown-model")
    snapshot=m.TaskSnapshot.model_validate(data)
    assert snapshot.budget.pending_reservation=="0.0015"
    assert snapshot.budget.settled_estimate is None
    with pytest.raises(ValidationError): m.BudgetView(settled_estimate="-0.01")
    with pytest.raises(ValidationError): m.ErrorView(code="EXTERNAL_RESULT_UNKNOWN",message="核对结果",recovery="REFRESH_READ")


def test_function_return_and_journal_commit_do_not_publish_outputs(fixture_data):
    data=fixture_data("b1-normal")["frames"][1]["snapshot"]
    step=data["steps"][0]
    step["output_ids"]=["fixture-output"]
    data["outputs"]=[{"output_id":"fixture-output","instance_id":step["instance_id"],"kind":"FACTS","origin":"WEB_FIXTURE","availability":"AVAILABLE"}]
    with pytest.raises(ValidationError,match="function return alone"): m.TaskSnapshot.model_validate(data)
    with pytest.raises(ValidationError,match="journal commit"):
        m.DisplayEvent(task_id="fixture-a",seq=1,type="CALL_COMMITTED",origin="WEB_FIXTURE",output_ids=["fixture-output"])


def test_cross_attempt_outputs_and_duplicate_events_are_rejected(fixture_data):
    data=fixture_data("b1-normal")["frames"][1]["snapshot"]
    data["steps"][0].update(acceptance="CHECKPOINT_CONFIRMED",output_ids=["fixture-output"])
    data["outputs"]=[{"output_id":"fixture-output","instance_id":"different-attempt","kind":"FACTS","origin":"WEB_FIXTURE","availability":"AVAILABLE"}]
    with pytest.raises(ValidationError,match="association"): m.TaskSnapshot.model_validate(data)
    frame=fixture_data("b1-normal")["frames"][1]
    frame["events"]*=2
    with pytest.raises(ValidationError,match="ordered, unique"): m.FixtureFrame.model_validate(frame)


def test_cross_stream_event_and_nested_floating_amount_are_rejected(fixture_data):
    frame=fixture_data("b1-normal")["frames"][1]
    frame["events"][0]["event_stream_id"]="different-stream"
    with pytest.raises(ValidationError,match="stream differs"): m.FixtureFrame.model_validate(frame)
    data=fixture_data("tool-results")["frames"][0]["snapshot"]
    data["research"]["supplement"]["history"][0]["result"]["observations"][0]["raw_value"]=0.1
    with pytest.raises(ValidationError,match="Decimal JSON strings"): m.TaskSnapshot.model_validate(data)


@pytest.mark.parametrize("workflow,mode,baseline,valid",[("B1","LIVE",None,True),("B1","REPLAY",None,True),("B2","LIVE","baseline",True),("B2","RULES","baseline",True),("B1","RULES",None,False),("B2","REPLAY","baseline",False),("B2","LIVE",None,False)])
def test_request_workflow_mode_contract(workflow,mode,baseline,valid):
    data={"request_id":"request-1","question":"比较财务记录","workflow":workflow,"execution_mode":mode,"company_ids":["002371.SZ"],"fiscal_years":[2023,2024],"as_of_date":"2025-04-30","corpus_snapshot_id":"snapshot","baseline_task_id":baseline}
    if valid: m.CreateTaskRequest.model_validate(data)
    else:
        with pytest.raises(ValidationError): m.CreateTaskRequest.model_validate(data)


def test_export_check_is_deterministic_without_touching_sources():
    path=APP_ROOT/"scripts/web/build_w0_contracts.py"
    spec=importlib.util.spec_from_file_location("w0_export",path)
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    source_paths={a["path"] for entry in MANIFEST["fixtures"] for frame in json.loads((FIXTURES/entry["path"]).read_text(encoding="utf-8"))["frames"] for a in frame["snapshot"]["provenance"]["source_artifacts"]}
    before={p:hashlib.sha256((APP_ROOT/p).read_bytes()).hexdigest() for p in source_paths}
    exports=module.exports()
    assert all((APP_ROOT/name).read_bytes()==content.encode("utf-8") for name,content in exports.items())
    assert before=={p:hashlib.sha256((APP_ROOT/p).read_bytes()).hexdigest() for p in source_paths}
    schema=json.loads(exports["web/contracts/TaskSnapshot.schema.json"])
    assert schema["additionalProperties"] is False
    assert all(item.get("type") in {"string","null"} for item in schema["$defs"]["MetricObservation"]["properties"]["raw_value"]["anyOf"])


@pytest.mark.parametrize("path",["../secret.txt","C:/secret.txt","/absolute/path","safe/../../escape","safe\\windows.txt"])
def test_artifact_locators_cannot_be_browser_supplied_paths(path):
    with pytest.raises(ValidationError): m.ArtifactRef(path=path,sha256="0"*64)
