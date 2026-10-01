"""Pure W0 presentation rules; no task mutations or permission authority.

Inputs are validated snapshots; outputs are UI decisions/capability proposals.
Resume safety must be explicitly supplied by a future trusted coordinator. An
HTTP handler must repeat its own authorization and current-state checks.
"""
from __future__ import annotations

from .models import ActionCapability, Operation, TaskSnapshot

OPERATIONS: tuple[Operation, ...] = ("VIEW_REPORT", "VIEW_STEPS", "VIEW_EVIDENCE", "FOLLOWUP", "CANCEL", "CLARIFY", "RESUME", "NEW_TASK")


def can_auto_enter_report(snapshot: TaskSnapshot) -> bool:
    """Decide automatic report navigation, retaining any ongoing reading/editing."""
    ui = snapshot.ui
    return bool(snapshot.publication.result_available and ui.panel == "PROCESS" and ui.follow_progress
                and not (ui.reader_open or ui.text_selection_active or ui.editing_followup or ui.manual_browsing))


def suggested_panel(snapshot: TaskSnapshot) -> str:
    """Suggest an initial panel; historical reading does not replay execution."""
    if snapshot.task.origin == "HISTORICAL_VIEW" and snapshot.publication.result_available:
        return "REPORT"
    if snapshot.ui.panel == "REPORT" and not snapshot.publication.result_available:
        return "PROCESS"
    return "REPORT" if can_auto_enter_report(snapshot) else snapshot.ui.panel


def action_capabilities(snapshot: TaskSnapshot) -> list[ActionCapability]:
    """Conservative display capabilities, independent of animation or connectivity."""
    task, runtime, publication = snapshot.task, snapshot.runtime, snapshot.publication
    controls = task.access_mode != "HISTORY_READ_ONLY"
    reachable = runtime.connection_state in {"CONNECTED", "NOT_APPLICABLE"}
    writes_allowed = controls and reachable
    report = publication.result_available
    waiting = task.task_status == "WAITING_INPUT" and task.workflow == "B2"
    resume = (writes_allowed and runtime.resume_readiness == "CONFIRMED_SAFE" and runtime.worker_state == "STOPPED" and not runtime.cancel_requested
              and task.task_status in {"CREATED", "RUNNING", "FAILED", "WAITING_INPUT"}
              and (not waiting or runtime.clarification_status == "READY_TO_RESUME"))
    enabled = {
        "VIEW_REPORT": report,
        "VIEW_STEPS": bool(snapshot.steps or snapshot.research.supplement),
        "VIEW_EVIDENCE": bool(snapshot.research.evidence),
        "FOLLOWUP": writes_allowed and report,
        "CANCEL": writes_allowed and task.task_status not in {"COMPLETED", "PARTIAL", "CANCELLED"} and not runtime.cancel_requested,
        "CLARIFY": writes_allowed and waiting and not runtime.cancel_requested and runtime.clarification_status == "NONE",
        "RESUME": resume,
        "NEW_TASK": controls and reachable,
    }
    reasons = {
        "VIEW_REPORT": "正式报告尚未通过发布核验。",
        "VIEW_STEPS": "当前记录没有可用的独立步骤或补查历史。",
        "VIEW_EVIDENCE": "当前记录没有可读取的原文。",
        "FOLLOWUP": "追问需要已发布结果和可写入的当前会话。",
        "CANCEL": "当前终态或已受理取消不需要重复请求。",
        "CLARIFY": "仅当前等待检查点可接收新的澄清决定。",
        "RESUME": "恢复需要协调层明确核验安全；等待任务还需要已记录决定。",
        "NEW_TASK": "当前为只读历史或连接尚未恢复。",
    }
    authority = "SIMULATED" if task.access_mode == "MOCK" else "SERVER"
    return [ActionCapability(operation=op, enabled=enabled[op], authority="READ_ONLY" if op.startswith("VIEW_") else authority,
                             reason=None if enabled[op] else ("当前为只读历史，不执行写操作。" if not controls and not op.startswith("VIEW_")
                                                            else "连接尚未恢复，先核对任务状态。" if not reachable and not op.startswith("VIEW_")
                                                            else reasons[op])) for op in OPERATIONS]
