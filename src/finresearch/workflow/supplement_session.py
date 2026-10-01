"""S6 durable adapter for the bounded B2 graph and explicit human clarification."""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver
from finresearch.contracts import stable_sha256
from finresearch.contracts.research import ResearchContext
from finresearch.contracts.supplement import ActionPlan, ToolResult, SupplementGap, SupplementAction
from finresearch.storage.session_store import SessionStore, Cancelled, UnsafeResume, safe_task_id, task_lock
from .bounded_supplement import SupplementDependencies, compile_supplement_graph, render_supplement
from .session_executor import atomic_artifact, json_text, validate_input_lock, verify_published_output
from .session_context import pack_context, answer_followup, ContextTooLarge
from finresearch.retrieval.corpus import file_sha256

OLD_EXECUTION_FIELDS = {"report", "validation", "execution_status", "trace", "writer_ids"}


def task_directories(root: Path, task_id: str) -> tuple[Path, Path]:
    """Resolve application-owned task directories; links cannot escape root."""
    root = root.resolve()
    task_id = safe_task_id(task_id)
    runtime = root / "storage/s6/tasks" / task_id
    output = root / "runs/s6" / task_id
    if any(not path.resolve().is_relative_to(root) for path in (runtime, output)):
        raise ValueError("任务目录越出项目")
    return runtime, output


def fact_view(baseline: dict, context: ResearchContext) -> dict:
    """Retain research records, strip prior execution metadata and bind this run."""
    return {k: v for k, v in baseline.items() if k not in OLD_EXECUTION_FIELDS} | {
        "context": context.model_dump(mode="json")}


def supplement_capsule(state: dict, context: ResearchContext, documents: dict, maximum_bytes: int) -> dict:
    """Losslessly retain B2 stopping conditions as well as source financial facts."""
    capsule = pack_context(state, context, documents, maximum_bytes)
    ledger = dict(state["supplement"])
    # Successful tool records already occur verbatim in observations/evidence.
    # Keep result locators, notes and full action parameters without duplicating
    # entire source windows. Rejected/unmerged records remain in the audit file.
    history = []
    for entry in ledger["history"]:
        compact = dict(entry)
        if "action" in compact:
            action = dict(compact["action"])
            if (action["as_of_date"] != context.as_of_date.isoformat()
                    or action["corpus_snapshot_id"] != context.corpus_snapshot_id):
                raise ValueError("补查行动与上下文范围不一致")
            # Shared scope is stored once in payload.context, never inferred.
            action.pop("as_of_date")
            action.pop("corpus_snapshot_id")
            compact["action"] = action
        if "result" in compact:
            result = dict(compact["result"])
            result["observation_ids"] = [o["observation_id"] for o in result.pop("observations", [])]
            result["evidence_ids"] = [e["evidence_id"] for e in result.pop("evidence", [])]
            compact["result"] = result
        history.append(compact)
    ledger["history"] = history
    # Schema defaults carry no additional fact. Omit only defaults that can be
    # exactly reconstructed; retain all text, limits, counterevidence and IDs.
    # Full original records remain immutable in final_state.json.
    ledger["gaps"] = [SupplementGap.model_validate(g).model_dump(mode="json", exclude_defaults=True)
                      for g in ledger["gaps"]]
    ledger["gap_encoding"] = "SUPPLEMENT_GAP_V1_DEFAULTS_OMITTED"
    ledger["action_encoding"] = "SHARED_CUTOFF_AND_SNAPSHOT_FROM_CONTEXT_V1"
    ledger["audit_artifact"] = "final_state.json#/supplement"
    capsule["payload"]["supplement"] = ledger
    size = len(json.dumps(capsule["payload"], ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    if size > maximum_bytes:
        raise ContextTooLarge(f"CONTEXT_TOO_LARGE:{size}>{maximum_bytes}")
    capsule.update(payload_sha256=stable_sha256(capsule["payload"]), utf8_bytes=size,
                   compression="LOSSLESS_SOURCE_RECORDS_WITH_AUDIT_REFERENCES", serialization="COMPACT_JSON_UTF8")
    return capsule


def supplement_followup(capsule: dict, requested: ResearchContext, question: str, topic: str | None = None) -> dict:
    """Same-scope follow-ups retain unresolved B2 gaps and bounded-stop metadata."""
    answer = answer_followup(capsule, requested, question, topic)
    if answer["status"] != "NEW_RUN_REQUIRED":
        ledger = dict(capsule["payload"]["supplement"])
        if ledger.get("gap_encoding") == "SUPPLEMENT_GAP_V1_DEFAULTS_OMITTED":
            ledger["gaps"] = [SupplementGap.model_validate(g).model_dump(mode="json") for g in ledger["gaps"]]
        if ledger.get("action_encoding") == "SHARED_CUTOFF_AND_SNAPSHOT_FROM_CONTEXT_V1":
            bound_context = capsule["payload"]["context"]
            ledger["history"] = [{**entry, "action":{**entry["action"],
                "as_of_date":bound_context["as_of_date"],"corpus_snapshot_id":bound_context["corpus_snapshot_id"]}}
                for entry in ledger["history"]]
        answer["supplement"] = ledger
    return answer


class SupplementSession:
    """One frozen task, journal and budget; graph replay never resets loop counts.

    Application supplies verified baseline, source tools and optional live model.
    Hook is for local fault injection, not model-controlled instructions. Waiting
    does not publish a completed result. Human continuation changes only a Gap
    limitation; changed sources always require a derived task and new snapshot.
    """
    def __init__(self, root: Path, task_id: str, deps: SupplementDependencies, documents: dict, baseline: dict, *, hook=None):
        self.root = root.resolve()
        self.task_id = safe_task_id(task_id)
        self.runtime, self.output = task_directories(self.root, self.task_id)
        self.store = SessionStore(self.runtime / "session.sqlite")
        self.deps, self.documents, self.baseline = deps, documents, baseline
        self.hook = hook or (lambda point: None)
        with self.store.transaction() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS clarification (id TEXT PRIMARY KEY, payload TEXT NOT NULL, digest TEXT NOT NULL)")

    def create(self, context: ResearchContext, locks: dict, policy: dict) -> None:
        if context.run_id != self.task_id:
            raise ValueError("任务ID不一致")
        validate_input_lock(self.root, locks)
        self.store.create({"schema_version": 1, "context": context.model_dump(mode="json"),
                           "input_lock": locks, "policy": policy,
                           "baseline_sha256": stable_sha256(self.baseline),
                           "supplement_policy_sha256": stable_sha256(self.deps.policy)})

    def after_commit(self, name: str) -> None:
        self.hook("action:" + name + ":COMMITTED")

    def wrapped_dependencies(self) -> SupplementDependencies:
        def select(ctx, round_number, candidates, cap):
            args = {"round": round_number, "candidates": [a.model_dump(mode="json") for a in candidates], "cap": cap}
            raw = self.store.call("b2-planner", args,
                lambda: self.deps.select_actions(ctx, round_number, candidates, cap).model_dump(mode="json"),
                external_model=(self.store.task()["manifest"]["policy"]["execution_mode"] == "LIVE"
                                or self.store.task()["manifest"]["policy"].get("fault_lab") == "model_pending"),
                after_commit=self.after_commit)
            return ActionPlan.model_validate(raw)

        def tool(ctx, action):
            raw = self.store.call("b2-tool", action.model_dump(mode="json"),
                lambda: ToolResult.model_validate(self.deps.execute_tool(ctx, action)).model_dump(mode="json"),
                after_commit=self.after_commit)
            return ToolResult.model_validate(raw)

        def quotes(ctx, candidates):
            raw = self.store.call("b2-quotes", {"evidence": [e.model_dump(mode="json") for e in candidates]},
                lambda: [c.model_dump(mode="json") for c in self.deps.select_quotes(ctx, candidates)],
                external_model=self.store.task()["manifest"]["policy"]["execution_mode"] == "LIVE",
                after_commit=self.after_commit)
            from finresearch.contracts.research import ResearchClaim
            return [ResearchClaim.model_validate(c) for c in raw]

        def event(event):
            self.store.check_cancelled()
            self.store.event(event)
            self.hook("event:" + event["event"])
            self.deps.event_sink(event)

        return replace(self.deps, select_actions=select, execute_tool=tool, select_quotes=quotes, event_sink=event)

    def pending_clarifications(self) -> list[dict]:
        with self.store.transaction() as conn:
            rows = conn.execute("SELECT payload,digest FROM clarification ORDER BY rowid").fetchall()
        payloads = []
        for raw, digest in rows:
            payload = json.loads(raw)
            if stable_sha256(payload) != digest:
                raise UnsafeResume("澄清记录被修改")
            payloads.append(payload)
        return payloads

    def clarify(self, request_id: str, gap_id: str, decision: str) -> dict:
        """Persist explicit local human intent; no numbers or source facts change.

        Exact repeated request is idempotent even after completion. The caller
        must use the dedicated human CLI; the model has no such capability.
        """
        safe_task_id(request_id)
        if decision not in {"continue_with_limitations", "new_snapshot_required"}:
            raise ValueError("澄清只能保留局限继续，或要求新快照")
        with task_lock(self.runtime / "executor.lock"):
            task = self.store.task()
            validate_input_lock(self.root, task["manifest"]["input_lock"])
            for prior in self.pending_clarifications():
                if prior["request_id"] == request_id:
                    if prior["gap_id"] != gap_id or prior["decision"] != decision:
                        raise ValueError("相同澄清ID内容冲突")
                    return {"accepted": True, "idempotent": True, "decision": decision}
            self.store.check_cancelled()
            if task["status"] != "WAITING_INPUT":
                raise ValueError("任务未等待澄清")
            with SqliteSaver.from_conn_string(str(self.runtime / "checkpoints.sqlite")) as saver:
                graph = compile_supplement_graph(self.deps, saver)
                saved = graph.get_state({"configurable": {"thread_id": self.task_id}})
                target = next((g for g in saved.values["gaps"] if g["gap_id"] == gap_id), None)
                if not target or target["status"] != "WAITING_INPUT":
                    raise ValueError("缺口未等待澄清或不属于任务")
                payload = {"request_id": request_id, "gap_id": gap_id, "decision": decision,
                           "checkpoint_sha256": stable_sha256(saved.values),
                           "authority": "HUMAN_INTENT_NOT_SOURCE_FACT"}
            with self.store.transaction() as conn:
                conn.execute("INSERT INTO clarification VALUES(?,?,?)",
                             (request_id, json.dumps(payload, ensure_ascii=False), stable_sha256(payload)))
            self.store.event({"event": "HUMAN_CLARIFICATION_RECORDED", **payload})
            self.hook("clarification:COMMITTED")
            return {"accepted": True, "decision": decision,
                    "status": "NEW_RUN_REQUIRED" if decision == "new_snapshot_required" else "READY_TO_RESUME"}

    def apply_clarification(self, graph, cfg: dict, state: dict) -> dict:
        if state.get("status") != "WAITING_INPUT":
            return state
        for payload in self.pending_clarifications():
            if payload["decision"] != "continue_with_limitations" or payload["checkpoint_sha256"] != stable_sha256(state):
                continue
            gaps = list(state["gaps"])
            for index, raw in enumerate(gaps):
                if raw["gap_id"] == payload["gap_id"] and raw["status"] == "WAITING_INPUT":
                    gaps[index] = SupplementGap.model_validate(raw).model_copy(update={
                        "status": "LIMITED", "resolution_note": "用户选择保留局限继续；没有补齐来源或认证因果；澄清ID：" + payload["request_id"]}).model_dump(mode="json")
                    graph.update_state(cfg, {"gaps": gaps, "status": "RUNNING", "stop_reason": "", "selected": []}, as_node="execute_actions")
                    self.hook("clarification:CHECKPOINTED")
                    return graph.get_state(cfg).values
        return state

    def run(self) -> dict:
        """Drive persistent B2 graph, checking waiting/cancel after every round."""
        with task_lock(self.runtime / "executor.lock"):
            task = self.store.task()
            manifest = task["manifest"]
            ctx = ResearchContext.model_validate(manifest["context"])
            try:
                validate_input_lock(self.root, manifest["input_lock"])
                if (stable_sha256(self.baseline) != manifest["baseline_sha256"]
                        or stable_sha256(self.deps.policy) != manifest["supplement_policy_sha256"]
                        or manifest["policy"].get("workflow") != "B2"):
                    raise UnsafeResume("补查输入或策略已变化")
                self.store.check_cancelled()
                if task["status"] in {"COMPLETED", "PARTIAL"}:
                    return verify_published_output(self.output, self.store)
                if task["status"] == "BLOCKED":
                    raise UnsafeResume("结果不确定任务不能自动重试")
                cfg = {"configurable": {"thread_id": self.task_id}}
                with SqliteSaver.from_conn_string(str(self.runtime / "checkpoints.sqlite")) as saver:
                    graph = compile_supplement_graph(self.wrapped_dependencies(), saver)
                    while True:
                        saved = graph.get_state(cfg)
                        state = saved.values
                        if state and state["context"] != ctx.model_dump(mode="json"):
                            raise UnsafeResume("检查点范围已变化")
                        state = self.apply_clarification(graph, cfg, state)
                        saved = graph.get_state(cfg)
                        self.store.check_cancelled()
                        if state.get("status") == "WAITING_INPUT":
                            self.store.set_status("WAITING_INPUT")
                            waiting = {"context": ctx.model_dump(mode="json"), "status": "WAITING_INPUT",
                                       "checkpoint_sha256": stable_sha256(state), "gaps": state["gaps"],
                                       "rounds": state["rounds"], "action_count": state["action_count"]}
                            atomic_artifact(self.runtime / "waiting" / (stable_sha256(waiting) + ".json"), json_text(waiting))
                            self.hook("waiting:CHECKPOINTED")
                            return waiting
                        if state and not saved.next:
                            break
                        self.store.set_status("RUNNING")
                        initial = {"context": ctx.model_dump(mode="json"), "baseline": fact_view(self.baseline, ctx)} if not state else None
                        state = graph.invoke(initial, cfg, interrupt_after=["execute_actions"])
                        self.hook("round:CHECKPOINTED")
                self.store.check_cancelled()
                if state.get("validation", {}).get("status") != "PASS" or state["report"] != render_supplement(state, self.deps):
                    raise ValueError("补查报告未通过最终核验")
                final = fact_view(state["current"], ctx)
                ledger = {k: state[k] for k in ("gaps", "history", "rounds", "action_count", "no_progress_rounds", "seen", "added_evidence_ids", "stop_reason")}
                final.update(report=state["report"], validation=state["validation"], execution_status=state["status"], supplement=ledger)
                capsule = supplement_capsule(final, ctx, self.documents, manifest["policy"]["maximum_context_utf8_bytes"])
                self.hook("publish:BEFORE")
                atomic_artifact(self.output / "report.md", final["report"])
                self.hook("publish:REPORT_WRITTEN")
                atomic_artifact(self.output / "final_state.json", json_text(final))
                atomic_artifact(self.output / "context_capsule.json", json_text(capsule))
                publication = {"schema_version": 1, "status": final["execution_status"], "execution_mode": manifest["policy"]["execution_mode"],
                    "scope": "S6_B2_BOUNDED_SUPPLEMENT", "files": {name: file_sha256(self.output / name) for name in ("report.md", "final_state.json", "context_capsule.json")}}
                self.store.check_cancelled()
                atomic_artifact(self.output / "publication.json", json_text(publication))
                self.hook("publish:MANIFEST_WRITTEN")
                self.store.set_status(final["execution_status"])
                self.store.event({"event": "PUBLISHED", "status": final["execution_status"]})
                return final
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
                raise
