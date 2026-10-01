"""S6 durable adapter around the frozen S4 graph; no research rules duplicated."""
from __future__ import annotations

from dataclasses import replace
import json
import logging
import os
from pathlib import Path
import uuid

from langgraph.checkpoint.sqlite import SqliteSaver
from finresearch.contracts import EvidenceCandidate, MetricObservation, stable_sha256
from finresearch.contracts.research import ResearchContext, HypothesisBatch, DisclosureBatch, WriterPlan
from finresearch.retrieval.corpus import file_sha256
from finresearch.storage.session_store import SessionStore, Cancelled, UnsafeResume, safe_task_id, task_lock
from .fixed_research import ResearchDependencies, compile_research_graph
from .session_context import pack_context, eligible_observations

LOG = logging.getLogger(__name__)


def atomic_artifact(path: Path, content: str):
    """Publish whole bytes once; equal replay is allowed, conflict never overwritten."""
    data = content.encode("utf-8")
    if path.exists():
        if path.read_bytes() != data:
            raise FileExistsError("已有产物内容冲突，拒绝覆盖")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        # Only the task lock holder can publish; no other writer is authorized.
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def json_text(payload):
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"


def validate_input_lock(root: Path, locks: dict):
    """Reject changed source, configuration, model/dependency versions or snapshots."""
    if not locks:
        raise UnsafeResume("运行缺少输入锁")
    for name, digest in locks.items():
        path = (root / name).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file() or file_sha256(path) != digest:
            raise UnsafeResume("输入/代码版本已改变，须新建研究")


def verify_published_output(output: Path, store: SessionStore) -> dict:
    """Completion requires matching DB status and every published file digest."""
    publication = json.loads((output / "publication.json").read_text(encoding="utf-8"))
    if set(publication["files"]) != {"report.md", "final_state.json", "context_capsule.json"}:
        raise UnsafeResume("发布清单缺少必需产物")
    for name, digest in publication["files"].items():
        path = (output / name).resolve()
        if not path.is_relative_to(output.resolve()) or file_sha256(path) != digest:
            raise UnsafeResume("已发布产物不完整或被修改")
    state = json.loads((output / "final_state.json").read_text(encoding="utf-8"))
    task = store.task()
    if (publication["status"] != task["status"] or task["cancel_requested"]
            or state["validation"]["status"] != "PASS"
            or state["execution_status"] != publication["status"]
            or state["context"] != task["manifest"]["context"]):
        raise UnsafeResume("完成清单与业务状态不一致")
    return state


class SessionExecutor:
    """Single-process execution with persistent graph and external-action journal.

    Dependencies are application-controlled capabilities. CLI freezes inputs and
    constructs these from verified source artifacts; arbitrary document/model
    text cannot supply functions or file paths. A pending provider operation is
    blocked rather than replayed. Hook is local fault-injection only.
    """
    def __init__(self, root: Path, task_id: str, deps: ResearchDependencies, documents: dict,
                 *, hook=None):
        self.root = root.resolve()
        self.task_id = safe_task_id(task_id)
        self.runtime = self.root / "storage/s6/tasks" / self.task_id
        self.output = self.root / "runs/s6" / self.task_id
        for path in (self.runtime, self.output):
            if not path.resolve().is_relative_to(self.root):
                raise ValueError("任务路径越出项目")
        self.store = SessionStore(self.runtime / "session.sqlite")
        self.deps = deps
        self.documents = documents
        self.hook = hook or (lambda point: None)

    def create(self, context: ResearchContext, input_lock: dict, policy: dict):
        if context.run_id != self.task_id:
            raise ValueError("运行ID与任务ID不一致")
        validate_input_lock(self.root, input_lock)
        self.store.create({"schema_version": 1, "context": context.model_dump(mode="json"),
                           "input_lock": input_lock, "policy": policy})

    def wrapped_dependencies(self):
        def load(ctx):
            def action():
                rows = [o.model_dump(mode="json") for o in self.deps.load_financials(ctx)]
                if eligible_observations(rows, ctx) != rows:
                    raise ValueError("财务适配器返回范围外记录")
                return rows
            raw = self.store.call("load_financials", {}, action, after_commit=self.after_commit)
            return [MetricObservation.model_validate(o) for o in raw]

        def hypotheses(ctx, company, phenomena):
            args = {"company": company, "phenomena": [p.model_dump(mode="json") for p in phenomena]}
            raw = self.store.call("hypotheses", args,
                                  lambda: self.deps.propose_hypotheses(ctx, company, phenomena).model_dump(mode="json"),
                                  external_model=True, after_commit=self.after_commit)
            return HypothesisBatch.model_validate(raw)

        def collect(ctx, company):
            raw = self.store.call("collect_evidence", {"company": company},
                                  lambda: [e.model_dump(mode="json") for e in self.deps.collect_fixed_evidence(ctx, company)],
                                  after_commit=self.after_commit)
            return [EvidenceCandidate.model_validate(e) for e in raw]

        def disclosures(ctx, company, candidates):
            args = {"company": company, "evidence": [e.model_dump(mode="json") for e in candidates]}
            raw = self.store.call("disclosures", args,
                                  lambda: self.deps.select_disclosures(ctx, company, candidates).model_dump(mode="json"),
                                  external_model=True, after_commit=self.after_commit)
            return DisclosureBatch.model_validate(raw)

        def writer(ctx, claims):
            args = {"claims": [c.model_dump(mode="json") for c in claims]}
            raw = self.store.call("writer", args, lambda: self.deps.plan_writer(ctx, claims).model_dump(mode="json"),
                                  external_model=True, after_commit=self.after_commit)
            return WriterPlan.model_validate(raw)

        def event_sink(event):
            self.store.event(event)
            self.hook("node:" + event["node"] + ":" + event["event"])
            self.store.check_cancelled()
            self.deps.event_sink(event)

        return replace(self.deps, load_financials=load, propose_hypotheses=hypotheses,
                       collect_fixed_evidence=collect, select_disclosures=disclosures,
                       plan_writer=writer, event_sink=event_sink)

    def after_commit(self, name):
        self.hook("action:" + name + ":COMMITTED")

    def verify_publication(self):
        return verify_published_output(self.output, self.store)

    def run(self) -> dict:
        """Create graph state once, then resume invoke(None) from disk checkpoints."""
        with task_lock(self.runtime / "executor.lock"):
            task = self.store.task()
            manifest = task["manifest"]
            ctx = ResearchContext.model_validate(manifest["context"])
            try:
                validate_input_lock(self.root, manifest["input_lock"])
                if (ctx.protocol_config_sha256 != stable_sha256(self.deps.protocol)
                        or manifest["policy"].get("schema_version") != 1):
                    raise UnsafeResume("协议或运行策略不兼容")
                self.store.check_cancelled()
                if task["status"] in {"COMPLETED", "PARTIAL"}:
                    return self.verify_publication()
                if task["status"] == "BLOCKED":
                    raise UnsafeResume("任务已阻塞，不能自动消除外部调用不确定性")
                self.store.set_status("RUNNING")
                cfg = {"configurable": {"thread_id": self.task_id}}
                with SqliteSaver.from_conn_string(str(self.runtime / "checkpoints.sqlite")) as saver:
                    graph = compile_research_graph(self.wrapped_dependencies(), saver)
                    saved = graph.get_state(cfg)
                    if saved.values:
                        if saved.values.get("context") != ctx.model_dump(mode="json"):
                            raise UnsafeResume("检查点范围与任务清单不一致")
                        state = graph.invoke(None, cfg) if saved.next else saved.values
                    else:
                        state = graph.invoke({"context": ctx.model_dump(mode="json")}, cfg)
                self.store.check_cancelled()
                if state.get("validation", {}).get("status") != "PASS":
                    raise ValueError("报告尚未通过最终检查")
                merged = dict(state)
                evidence = {e.evidence_id: e.model_dump(mode="json") for e in self.deps.financial_evidence}
                evidence.update({e["evidence_id"]: e for e in state.get("evidence", [])})
                merged["evidence"] = list(evidence.values())
                capsule = pack_context(merged, ctx, self.documents,
                                       manifest["policy"]["maximum_context_utf8_bytes"])
                self.hook("publish:BEFORE")
                atomic_artifact(self.output / "report.md", state["report"])
                self.hook("publish:REPORT_WRITTEN")
                atomic_artifact(self.output / "final_state.json", json_text(state))
                atomic_artifact(self.output / "context_capsule.json", json_text(capsule))
                publication = {"schema_version": 1, "status": state["execution_status"],
                               "files": {name: file_sha256(self.output / name) for name in
                                         ("report.md", "final_state.json", "context_capsule.json")},
                               "execution_mode": manifest["policy"]["execution_mode"],
                               "scope": "S6_B1_ONLY_G5_NOT_IMPLEMENTED"}
                self.store.check_cancelled()
                atomic_artifact(self.output / "publication.json", json_text(publication))
                self.hook("publish:MANIFEST_WRITTEN")
                self.store.set_status(state["execution_status"])
                self.store.event({"event": "PUBLISHED", "status": state["execution_status"]})
                return state
            except Cancelled:
                self.store.set_status("CANCELLED")
                self.store.event({"event": "CANCELLED"})
                raise
            except UnsafeResume:
                self.store.set_status("BLOCKED")
                self.store.event({"event": "BLOCKED", "reason": "UNSAFE_RESUME_OR_INTEGRITY_FAILURE"})
                raise
            except Exception as exc:
                self.store.set_status("FAILED")
                self.store.event({"event": "FAILED", "exception_type": type(exc).__name__})
                LOG.error("Task %s failed: %s", self.task_id, type(exc).__name__)
                raise
