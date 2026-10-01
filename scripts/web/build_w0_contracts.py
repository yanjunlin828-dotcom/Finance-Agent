"""Export W0 schemas and labelled fixtures; read historical sources without execution.

Run with the existing finresearch-agent Python. The only write targets are web/
contracts and web/fixtures. --check validates deterministic exports without writes.
No SessionStore/executor/model is instantiated. Historical gates certify published
bytes and scope consistency, not current source semantics or resumption safety.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from typing import get_args

APP_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(APP_ROOT / "src"))

from finresearch.contracts.metrics import stable_sha256
from finresearch.contracts.supplement import Tool, GapType, SupplementGap, ToolResult
from finresearch_web.contracts import models as m
from finresearch_web.contracts.rules import action_capabilities, can_auto_enter_report, suggested_panel
from finresearch_web.contracts import vocabulary as v

HISTORY_TASKS = ("s6-live-20260930-02", "s6-b2-live-20261001-03")
COMPANY_LABELS = {"002371.SZ":"北方华创", "688072.SH":"拓荆科技", "688082.SH":"盛美上海"}


def dumps(payload) -> str:
    """Canonical export bytes; no generated timestamp masquerades as source time."""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def artifact(path: Path, pointer: str = "") -> dict:
    resolved = path.resolve()
    if not resolved.is_relative_to(APP_ROOT) or not resolved.is_file():
        raise ValueError("fixture source outside APP_ROOT or missing")
    return {"path": resolved.relative_to(APP_ROOT).as_posix(), "sha256": digest(resolved), "pointer": pointer}


def history(task_id: str) -> dict:
    """Copy one verified published record using a consistent read-only DB transaction."""
    output = APP_ROOT / "runs/s6" / task_id
    runtime = APP_ROOT / "storage/s6/tasks" / task_id
    sources = [output / name for name in ("publication.json", "report.md", "final_state.json", "context_capsule.json")]
    sources += [runtime / "session.sqlite"]
    sources += [p for p in [runtime / "session.sqlite-wal", runtime / "baseline.json", runtime / "policy.json"] if p.is_file()]
    before = {p: digest(p) for p in sources}
    publication = json.loads(sources[0].read_text(encoding="utf-8"))
    state = json.loads((output / "final_state.json").read_text(encoding="utf-8"))
    required = {"report.md", "final_state.json", "context_capsule.json"}
    if publication["schema_version"] != 1 or set(publication["files"]) != required:
        raise ValueError("historical publication schema/files disagree")
    if any(digest(output / name) != sha for name, sha in publication["files"].items()):
        raise ValueError("historical published bytes do not match")
    db = runtime / "session.sqlite"
    with sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True) as conn:
        conn.execute("BEGIN")
        if conn.execute("PRAGMA user_version").fetchone()[0] != 1:
            raise ValueError("unsupported historical task schema")
        raw_manifest, manifest_sha, status, cancel = conn.execute("SELECT manifest,digest,status,cancel FROM task WHERE id=1").fetchone()
        manifest = json.loads(raw_manifest)
        cursor = conn.execute("SELECT COALESCE(MAX(id),0) FROM event").fetchone()[0]
    if (stable_sha256(manifest) != manifest_sha or manifest["schema_version"] != 1 or cancel
            or status not in {"COMPLETED", "PARTIAL"} or publication["status"] != status
            or state["execution_status"] != status or state["validation"]["status"] != "PASS"
            or state["context"] != manifest["context"]):
        raise ValueError("historical DB/publication/business gates disagree")
    if any(digest(p) != sha for p, sha in before.items()):
        raise ValueError("historical input changed during export; retry read after reconciliation")
    grouped = state["calculations"]
    calculations = [row for company in grouped.values() for row in company.values()]
    research = {"observations": state["observations"], "calculations": calculations,
                "calculation_slots": {company: {role: row["calculation_id"] for role, row in slots.items()} for company, slots in grouped.items()},
                "claims": state["claims"], "gaps": state["gaps"], "evidence": [{"record": e} for e in state["evidence"]],
                "report_markdown": state["report"], "dependency_issues": []}
    for claim in state["claims"]:
        if claim["kind"] == "DISCLOSED" and not claim["evidence_ids"]:
            research["dependency_issues"].append({"owner_id": claim["claim_id"], "target_id": "direct-evidence-reference",
                                                   "reason": "MISSING_DIRECT_REFERENCE"})
    documents={}
    for evidence in state["evidence"]:
        observation=next((o for o in state["observations"] if o["document_id"]==evidence["document_id"]),None)
        documents[evidence["document_id"]]={"document_id":evidence["document_id"],"company_id":evidence["company_id"],
                                             "document_sha256":observation["document_sha256"] if observation else None,
                                             "published_on":observation["document_published_on"] if observation else None,
                                             "metadata_source":artifact(output/"final_state.json","/observations") if observation else None}
    research["documents"]=list(documents.values())
    baseline = None
    if "supplement" in state:
        ledger = state["supplement"]
        research["supplement"] = {k: ledger[k] for k in ("rounds", "action_count", "no_progress_rounds", "stop_reason", "gaps")}
        research["supplement"]["history"] = [{"action": h["action"], "result": h.get("result"),
                                               "changed_gap_status": h.get("changed_gap_status"), "failure_type": h.get("exception_type")}
                                              for h in ledger["history"]]
        baseline_id = manifest["policy"]["baseline_run"]
        baseline = {"task_id": baseline_id, "label": "复用已有S4研究；本次只记录B2补查"}
    data = {"schema_version": m.SCHEMA_VERSION,
            "task": {"task_id": task_id, "question": "比较三家半导体设备公司的收入、经营现金流与应收账款，并区分事实、披露和未决解释。",
                     "context": state["context"], "workflow": "B2" if baseline else "B1", "execution_mode": manifest["policy"]["execution_mode"],
                     "origin": "HISTORICAL_VIEW", "task_status": status, "access_mode": "HISTORY_READ_ONLY", "baseline": baseline,
                     "company_labels":{code:COMPANY_LABELS[code] for code in state["context"]["company_ids"]}},
            "runtime": {"connection_state": "NOT_APPLICABLE", "event_cursor": cursor, "event_stream_id":"core-session:"+task_id+":schema1"},
            "publication": {"verification": "VERIFIED", "result_available": True, "publication_status": status,
                            "business_validation": "PASS", "scope_matches": True,
                            "files": [{"name": name, "sha256": sha, "digest_matches": True} for name, sha in publication["files"].items()]},
            "provenance": {"kind": "DERIVED_HISTORICAL", "label": "历史资料只读派生样本；未运行研究",
                           "source_artifacts": [artifact(p) for p in sources], "source_revalidation": "NOT_CHECKED",
                           "notes": ["已核验历史发布字节、数据库终态及范围；未重新认证解释语义或当前输入锁。",
                                     "created_at、独立步骤输出和印刷页未知时保留null，不从投影时间或最终报告反推。"]},
            "research": research, "ui": {"panel": "REPORT", "follow_progress": False}}
    data["outputs"] = [{"output_id": task_id + ":published:report", "kind": "REPORT", "origin": "HISTORICAL_VIEW", "availability": "AVAILABLE",
                        "source": artifact(output / "report.md"), "note": "正式发布报告；不是重建的节点中间输出。"}]
    return normalize(data)


def normalize(data: dict) -> dict:
    """Validate before and after conservative display capabilities are attached."""
    snapshot = m.TaskSnapshot.model_validate(data)
    data = snapshot.model_dump(mode="json")
    data["task"]["available_actions"] = [a.model_dump(mode="json") for a in action_capabilities(snapshot)]
    return m.TaskSnapshot.model_validate(data).model_dump(mode="json")


def base(scenario: str, context: dict, *, workflow="B1", status="RUNNING") -> dict:
    task_id = "fixture-" + scenario
    return {"task": {"task_id": task_id, "question": "【页面样本】比较收入、经营现金流和应收账款，核查披露与局限。",
                     "context": {**context, "run_id": task_id}, "workflow": workflow, "execution_mode": "REPLAY" if workflow == "B1" else "RULES",
                     "task_status": status, "origin": "WEB_FIXTURE", "access_mode": "MOCK", "company_labels":{code:COMPANY_LABELS[code] for code in context["company_ids"]},
                     "baseline": {"task_id": "fixture-baseline", "label": "【合成过程】复用已有基线，不重播B1"} if workflow == "B2" else None},
            "runtime": {"connection_state": "CONNECTED", "event_stream_id":"fixture:"+scenario+":v1"}, "publication": {"verification": "NOT_PUBLISHED"},
            "provenance": {"kind": "SYNTHETIC", "label": "合成页面场景，非真实研究执行", "notes": ["数据模型与状态规则用于W1验收；操作只在样本内生效。"]},
            "ui": {"panel": "PROCESS"}}


def mock_report(data: dict, content: dict | None = None):
    status = data["task"]["task_status"]
    data["publication"] = {"verification": "SIMULATED_VALID", "result_available": True, "publication_status": status}
    data["research"] = deepcopy(content) if content else {}
    data["research"].setdefault("report_markdown", "# 合成页面报告\n\n此内容仅验证报告布局与状态，没有执行真实研究。")


def waiting_gap(status="WAITING_INPUT") -> dict:
    return {"gap_id": "fixture-source-gap", "company_id": "002371.SZ", "gap_type": "SOURCE_REQUEST", "severity": "MATERIAL",
            "description": "【合成等待场景】当前资料不足，需要明确保留局限或另建新快照。",
            "closing_condition": "NEW_SNAPSHOT_OR_USER_INPUT", "status": status}


def ledger(data: dict, *, status="OPEN", reason=None, rounds=1, actions=1):
    data.setdefault("research", {})["supplement"] = {"rounds": rounds, "action_count": actions, "no_progress_rounds": 0,
                                                     "stop_reason": reason, "gaps": [waiting_gap(status)], "history": []}


def waiting(data: dict, reason="NEEDS_INPUT"):
    data["task"]["task_status"] = "WAITING_INPUT"
    ledger(data, status="WAITING_INPUT", reason=reason)
    data["runtime"].update(waiting_gap_ids=["fixture-source-gap"], checkpoint_sha256=stable_sha256({"fixture": data["task"]["task_id"], "waiting": reason}))


def frame(name: str, data: dict, event_type: str | None = None) -> dict:
    data = normalize(data)
    snapshot = m.TaskSnapshot.model_validate(data)
    events = []
    if event_type:
        data["runtime"]["event_cursor"] = max(data["runtime"]["event_cursor"], 1)
        events = [{"task_id": data["task"]["task_id"], "seq": data["runtime"]["event_cursor"], "event_stream_id": data["runtime"]["event_stream_id"], "type": event_type, "origin": "WEB_FIXTURE"}]
    return {"name": name, "snapshot": data, "events": events,
            "expected": {"suggested_panel": suggested_panel(snapshot), "auto_enter_report": can_auto_enter_report(snapshot),
                         "result_available": snapshot.publication.result_available,
                         "enabled_operations": [a.operation for a in snapshot.task.available_actions if a.enabled]}}


def scenario(sid: str, title: str, frames: list[dict], *, codes=(), synthetic=True, followups=()) -> dict:
    return m.ScenarioFixture.model_validate({"scenario_id": sid, "title": title, "description": "合成执行仅用于页面验收。" if synthetic else "只读复制已核验历史发布，不执行Agent。",
                                            "synthetic_execution": synthetic, "frames": frames, "covered_codes": list(codes), "followups": list(followups)}).model_dump(mode="json")


def exports() -> dict[str, str]:
    b1, b2 = (history(tid) for tid in HISTORY_TASKS)
    context = b1["task"]["context"]
    content = b1["research"]
    fixtures = {}

    def add(sid, title, frames, **kwargs):
        fixtures[sid] = scenario(sid, title, frames, **kwargs)

    for sid, data in [("history-b1", b1), ("history-b2", b2)]:
        add(sid, "只读历史：" + data["task"]["workflow"], [frame("published", data)], synthetic=False)
    for sid, terminal in [("b1-normal", "COMPLETED"), ("b1-model-partial", "PARTIAL")]:
        created = base(sid, context, status="CREATED"); created["ui"]["panel"] = "QUESTION"
        running = base(sid, context)
        running["steps"] = [{"step_id": "station-1", "node_id": "propose_hypotheses" if terminal == "PARTIAL" else "validate_context", "instance_id": sid + ":instance-1", "identity_kind": "SYNTHETIC", "origin": "WEB_FIXTURE", "lifecycle": "FUNCTION_RETURNED"}]
        done = base(sid, context, status=terminal); mock_report(done, content)
        done["provenance"]["source_artifacts"] = b1["provenance"]["source_artifacts"]
        done["provenance"]["notes"].append("报告内容复制历史记录，进度/终态为合成；不是一次新的发布核验。")
        if terminal == "PARTIAL":
            done["research"]["gaps"].append({"gap_id": "fixture-model-failure", "company_id": "002371.SZ", "gap_type": "MODEL_FAILURE", "description": "【合成场景】候选解释调用失败，已有事实仍可形成带局限报告。", "affected_ids": [], "suggested_next_step": "保留局限并核对调用记录。", "status": "UNRESOLVED"})
        add(sid, "B1正常推进" if terminal == "COMPLETED" else "B1模型局部失败后带局限发布", [frame("created", created), frame("function-returned-not-checkpointed", running, "NODE_RETURNED"), frame("published-sample", done, "REPORT_VERIFIED")], codes=[terminal])

    rounds = []
    for n in (1, 2, 3):
        data = base("b2-rounds", context, workflow="B2")
        ledger(data, rounds=n, actions=n*2)
        data["research"]["supplement"]["history"] = b2["research"]["supplement"]["history"][:n*2]
        data["provenance"]["source_artifacts"] = b2["provenance"]["source_artifacts"]
        data["provenance"]["notes"].append("动作内容取自历史，本场景的轮次编排为合成，不重建历史节点事件。")
        data["steps"] = [{"step_id": "baseline", "node_id": "baseline", "instance_id": "fixture-baseline:reuse", "identity_kind": "SYNTHETIC", "origin": "RESULT_REUSE", "lifecycle": "REUSED"},
                         {"step_id": "round-"+str(n), "node_id": "execute_actions", "instance_id": "fixture-b2-rounds:round-"+str(n), "identity_kind": "SYNTHETIC", "origin": "WEB_FIXTURE", "round": n, "attempt": 1, "lifecycle": "FUNCTION_RETURNED", "acceptance": "CHECKPOINT_CONFIRMED"}]
        rounds.append(frame("round-"+str(n), data, "CHECKPOINT_ACCEPTED"))
    add("b2-rounds", "历史基线与独立补查轮次", rounds)

    wait = base("b2-wait-continue", context, workflow="B2"); waiting(wait)
    ready = deepcopy(wait); ready["runtime"].update(clarification_status="READY_TO_RESUME", resume_readiness="CONFIRMED_SAFE", worker_state="STOPPED")
    resumed = deepcopy(ready); resumed["task"]["task_status"] = "RUNNING"
    resumed["runtime"].update(waiting_gap_ids=[], clarification_status="NONE", resume_readiness="UNKNOWN")
    ledger(resumed, status="LIMITED", rounds=1, actions=1)
    add("b2-wait-continue", "等待→记录保留局限决定→恢复", [frame("waiting", wait, "WAITING_INPUT"), frame("decision-recorded-not-resumed", ready), frame("resumed-gap-still-limited", resumed)])
    new = base("b2-new-snapshot", context, workflow="B2"); waiting(new, "REQUIRES_NEW_SNAPSHOT")
    decided = deepcopy(new); decided["runtime"]["clarification_status"] = "NEW_RUN_REQUIRED"
    add("b2-new-snapshot", "要求新快照后旧任务仍等待", [frame("waiting", new), frame("new-run-required-still-waiting", decided)], codes=["NEW_RUN_REQUIRED"])

    cancel = base("cancel-late-result", context)
    cancelling = deepcopy(cancel); cancelling["runtime"]["cancel_requested"] = True
    cancelled = deepcopy(cancelling); cancelled["task"]["task_status"] = "CANCELLED"
    add("cancel-late-result", "请求取消→晚返回→边界确认", [frame("running", cancel), frame("cancel-requested", cancelling, "CANCEL_REQUESTED"), frame("cancelled", cancelled)], codes=["CANCELLED"])
    blocked = base("unknown-model", context, status="BLOCKED")
    blocked["runtime"]["resume_readiness"] = "UNSAFE"
    blocked["budget"] = {"pending_reservation": "0.0015", "total_calls": 1, "note": "合成未知调用：保留预留，不是供应商账单。"}
    add("unknown-model", "模型结果未知，阻塞且保留预算", [frame("blocked", blocked)], codes=["BLOCKED", "EXTERNAL_RESULT_UNKNOWN"])
    failed = base("hard-failure", context, status="FAILED"); failed["runtime"]["resume_readiness"] = "UNSAFE"
    add("hard-failure", "不可信工具结果硬失败", [frame("failed", failed)], codes=["FAILED"])
    incomplete = base("publication-incomplete", context, status="COMPLETED")
    incomplete["publication"] = {"verification": "INVALID", "reasons": ["仅有report.md，正式三文件与业务状态尚未核验。"]}
    add("publication-incomplete", "文件出现不等于正式完成", [frame("not-available", incomplete)], codes=["PUBLICATION_INCOMPLETE"])
    connected = base("reconnect", context); disconnected = deepcopy(connected); disconnected["runtime"]["connection_state"] = "DISCONNECTED"
    restored = deepcopy(connected); restored["runtime"]["event_cursor"] = 2
    add("reconnect", "断连与恢复保持同任务", [frame("connected", connected), frame("disconnected-not-failed", disconnected), frame("reconciled", restored, "CONNECTION_STATE")], codes=["CONNECTION_LOST"])
    reading = base("reading-ready", context, status="COMPLETED"); mock_report(reading, content)
    reading["ui"].update(reader_open=True, selected_evidence_id=content["evidence"][0]["record"]["evidence_id"])
    add("reading-ready", "阅读原文时报告就绪不跳走", [frame("report-ready-reader-held", reading)])
    rapid = base("rapid-completion", context, status="COMPLETED"); mock_report(rapid)
    rapid_created=base("rapid-completion", context, status="CREATED"); rapid_created["ui"]["panel"]="QUESTION"
    add("rapid-completion", "快速完成只收敛至最新视图", [frame("created-no-fake-running", rapid_created), frame("latest-state", rapid, "REPORT_VERIFIED")])

    extreme = base("content-extremes", context, status="PARTIAL"); mock_report(extreme, content)
    extreme["task"]["question"] = ("【排版合成样本】" + "比较三家公司在固定财年范围内的收入、经营现金流和期末应收账款，保留原文、单位、否定及未决条件。"*60)[:2000]
    extreme["task"]["company_labels"]["002371.SZ"]="【合成长名称排版】北方华创科技集团股份有限公司（页面布局测试名称，不改变公司身份与研究范围）"
    extreme["provenance"]["source_artifacts"] = b1["provenance"]["source_artifacts"]
    for status in ["MISSING_INPUT", "ZERO_DENOMINATOR", "NEGATIVE_BASE", "INCOMPARABLE"]:
        row = deepcopy(content["calculations"][0]); row.update(calculation_id="fixture-calc-"+status, status=status, value=None, errors=["【合成显示边界】"+status], input_ids=["fixture-missing-input"])
        row["input_sha256"]=stable_sha256({"fixture_input":"fixture-missing-input"})
        row["result_sha256"]=stable_sha256({k:value for k,value in row.items() if k!="result_sha256"})
        extreme["research"]["calculations"].append(row)
        extreme["research"]["dependency_issues"].append({"owner_id": row["calculation_id"], "target_id": "fixture-missing-input", "reason": "MISSING_RECORD"})
    for status in ["MISSING", "NOT_APPLICABLE", "REVIEW_REQUIRED"]:
        row=deepcopy(content["observations"][0]); row.update(observation_id="fixture-observation-"+status, value_status=status, raw_value_text=None, raw_value=None, raw_unit=None, standard_value=None, standard_unit=None, evidence_refs=[], review_status="DRAFT", review_note="合成显示边界，未复核为权威财务事实。")
        row["content_sha256"]=stable_sha256({k:value for k,value in row.items() if k!="content_sha256"})
        extreme["research"]["observations"].append(row)
    extreme["provenance"]["notes"].append("fixture-* 数值/计算对象仅为无值显示边界，不是来源验证事实；长问题仅测试排版。")
    add("content-extremes", "长中文、完整原文、无效计算及缺引用", [frame("long-content", extreme)], codes=[*v.CALCULATION_LABELS, *v.OBSERVATION_LABELS])
    followups = [{"status": status, "message": label} for status, label in v.FOLLOWUP_LABELS.items()]
    add("followup-results", "四种追问结果独立于任务终态", [frame("published-task-unchanged", rapid)], followups=followups, codes=v.FOLLOWUP_LABELS)

    for reason in get_args(m.StopReason):
        sid="b2-stop-"+reason.lower().replace("_", "-")
        data=base(sid,context,workflow="B2",status="COMPLETED" if reason=="ALL_RESOLVED" else "PARTIAL")
        if reason in {"NEEDS_INPUT", "REQUIRES_NEW_SNAPSHOT"}:
            waiting(data, reason)
        else:
            mock_report(data); ledger(data, reason=reason, status="FAILED" if reason=="TOOL_FAILURE" else "LIMITED", rounds=3 if reason=="ROUND_LIMIT" else 1)
            if reason=="ALL_RESOLVED": data["research"]["supplement"]["gaps"]=[]
        add(sid, v.STOP_LABELS[reason], [frame("stop",data)], codes=[reason])
    tool_frames=[]
    for status in get_args(ToolResult.model_fields["status"].annotation):
        data=base("tool-results",context,workflow="B2")
        if status in {"NEEDS_INPUT","REQUIRES_NEW_SNAPSHOT"}: waiting(data,status)
        else: ledger(data,status="LIMITED")
        gap=data["research"]["supplement"]["gaps"][0]
        tool="REQUEST_CLARIFICATION"; arguments={"gap_type":"SOURCE_REQUEST"}
        result={"action_id":"fixture-action-"+status,"status":status,"note":v.TOOL_RESULT_LABELS[status]}
        if status=="FOUND":
            tool="QUERY_METRIC"; arguments={"metric_id":"revenue","fiscal_year":2023}
            gap.update(gap_type="MISSING_METRIC",metric_id="revenue",fiscal_year=2023,closing_condition="VERIFIED_METRIC",status="RESOLVED")
            row=next(o for o in content["observations"] if o["company_id"]=="002371.SZ" and o["metric_id"]=="revenue" and o["fiscal_year"]==2023)
            result["observations"]=[row]
            data["provenance"]["source_artifacts"]=b1["provenance"]["source_artifacts"]
        elif status=="NO_EVIDENCE":
            tool="SEARCH_DISCLOSURE"; arguments={"question":"现金流变动说明","reporting_year":2024,"need":"TIMING_DETAIL"}
            gap.update(gap_type="DISCLOSURE_CONTEXT",need="TIMING_DETAIL",closing_condition="VERIFIED_SOURCE_WINDOW")
        elif status in {"REVIEW_REQUIRED","REQUIRES_NEW_SNAPSHOT"}:
            tool="REVIEW_TABLE"; arguments={"gap_type":"TABLE_REVIEW"}; gap.update(gap_type="TABLE_REVIEW")
            if status=="REVIEW_REQUIRED":
                result["note"]="合成合同边界：REVIEW_REQUIRED已定义，当前本地工具不承诺直接产出此结果。"
        action={"action_id":result["action_id"],"gap_id":gap["gap_id"],"tool":tool,"company_id":"002371.SZ","as_of_date":context["as_of_date"],"corpus_snapshot_id":context["corpus_snapshot_id"],"arguments":arguments,"reason":"合成工具结果显示测试，不是真实调用。"}
        data["research"]["supplement"]["history"]=[{"action":action,"result":result,"changed_gap_status":gap["status"]}]
        tool_frames.append(frame(status.lower(),data))
    add("tool-results","五种工具结果（合成合同边界）",tool_frames,codes=v.TOOL_RESULT_LABELS)

    result={}
    for model in [m.TaskSnapshot,m.ScenarioFixture,m.CreateTaskRequest,m.ControlRequest,m.ClarificationRequest,m.FollowupRequest,m.ErrorView]:
        schema=model.model_json_schema(mode="serialization")
        schema["$schema"]="https://json-schema.org/draft/2020-12/schema"
        schema["$id"]="urn:finresearch:web:"+model.__name__+":"+m.SCHEMA_VERSION
        result["web/contracts/"+model.__name__+".schema.json"]=dumps(schema)
    vocabulary={"schema_version":m.SCHEMA_VERSION,"task_status":v.TASK_LABELS,"stop_reason":v.STOP_LABELS,"tool_result":v.TOOL_RESULT_LABELS,"followup":v.FOLLOWUP_LABELS,"observation_status":v.OBSERVATION_LABELS,"calculation_status":v.CALCULATION_LABELS,"operations":v.OPERATION_LABELS,"gap_status_labels":v.GAP_LABELS,
                "tools":list(get_args(Tool)),"gap_types":list(get_args(GapType)),"gap_status":list(get_args(SupplementGap.model_fields['status'].annotation)),"closing_conditions":list(get_args(SupplementGap.model_fields['closing_condition'].annotation)),
                "b1_stations":[{"node_id":n,"label":l,"group":g} for n,l,g in v.B1_STATIONS],"b2_stations":[{"node_id":n,"label":l} for n,l in v.B2_STATIONS]}
    result["web/contracts/vocabulary.json"]=dumps(vocabulary)
    result["web/contracts/capabilities.sample.json"]=dumps({"schema_version":m.SCHEMA_VERSION,"is_sample":True,"http_service":"NOT_IMPLEMENTED",
        "scope":{"company_labels":COMPANY_LABELS,"company_ids":context["company_ids"],"fiscal_years":context["fiscal_years"],"as_of_date":context["as_of_date"],"corpus_snapshot_id":context["corpus_snapshot_id"],"metric_ids":["revenue","operating_cash_flow_net","accounts_receivable"]},
        "cli_combinations":[{"workflow":workflow,"execution_mode":mode} for workflow,mode in [("B1","LIVE"),("B1","REPLAY"),("B2","LIVE"),("B2","RULES")]],
        "pending_capabilities":["HTTP_ASYNC_COORDINATOR","READ_ONLY_API","SAME_RUN_B1_TO_B2","NEW_SNAPSHOT_INGESTION","INDEPENDENT_CAUSAL_REVIEW"],
        "source_artifacts":b1["provenance"]["source_artifacts"],"notes":["公司简称沿用第二版范围说明；实际代码、年份、截止日与快照取自历史context；样本不是在线capabilities接口。"]})
    for sid,payload in fixtures.items(): result["web/fixtures/"+sid+".json"]=dumps(payload)
    manifest={"schema_version":m.SCHEMA_VERSION,"purpose":"W0 labelled display samples; no HTTP or research execution", "fixtures":[{"scenario_id":sid,"path":sid+".json","synthetic_execution":f["synthetic_execution"],"title":f["title"],"sha256":hashlib.sha256(result["web/fixtures/"+sid+".json"].encode('utf-8')).hexdigest()} for sid,f in fixtures.items()]}
    result["web/fixtures/manifest.json"]=dumps(manifest)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check",action="store_true",help="verify deterministic exports without writing")
    args=parser.parse_args()
    generated=exports()
    errors=[]
    for relative,content in generated.items():
        path=(APP_ROOT/relative).resolve()
        if not path.is_relative_to(APP_ROOT/"web"):
            raise ValueError("export path escaped web")
        if args.check:
            if not path.is_file() or path.read_bytes()!=content.encode('utf-8'): errors.append(relative)
        else:
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(content.encode('utf-8'))
    if errors:
        print("stale/missing W0 exports: "+", ".join(errors)); return 1
    print(f"W0 {'verified' if args.check else 'exported'}: {len(generated)} files; no research or source writes")
    return 0


if __name__=="__main__":
    raise SystemExit(main())
