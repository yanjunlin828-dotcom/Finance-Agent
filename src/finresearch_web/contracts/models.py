"""W0 JSON contracts, without research execution, source writes or HTTP handlers.

Inputs are JSON-compatible display records from an explicitly labelled adapter.
Outputs retain source identities, Decimal strings and independent UI/runtime states.
Core financial types are reused, not recalculated. Historical timestamps may be
unknown; a projection timestamp must never replace a source occurrence timestamp.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import PurePosixPath
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator, field_validator

from finresearch.contracts.evidence import EvidenceCandidate
from finresearch.contracts.metrics import CalculationResult, MetricObservation
from finresearch.contracts.research import CompanyId, ResearchClaim, ResearchContext, ResearchGap
from finresearch.contracts.supplement import SupplementAction, SupplementGap, ToolResult

SCHEMA_VERSION = "1.0.0"
TaskStatus = Literal["CREATED", "RUNNING", "COMPLETED", "PARTIAL", "WAITING_INPUT", "CANCELLED", "FAILED", "BLOCKED"]
StopReason = Literal["ROUND_LIMIT", "ACTION_BUDGET", "NO_PROGRESS", "NO_ACTIONABLE_GAPS", "ALL_RESOLVED", "MODEL_PLAN_REJECTED_OR_BUDGET", "TOOL_FAILURE", "QUOTE_REVIEW_FAILED", "NEEDS_INPUT", "REQUIRES_NEW_SNAPSHOT"]
FollowupStatus = Literal["ANSWERED_FROM_REVIEWED_RECORDS", "NEEDS_CLARIFICATION", "INSUFFICIENT_EVIDENCE", "NEW_RUN_REQUIRED"]
Origin = Literal["CURRENT_EXECUTION", "HISTORICAL_VIEW", "RESULT_REUSE", "MODEL_REPLAY", "FAULT_DEMO", "WEB_FIXTURE"]
Operation = Literal["VIEW_REPORT", "VIEW_STEPS", "VIEW_EVIDENCE", "FOLLOWUP", "CANCEL", "CLARIFY", "RESUME", "NEW_TASK"]
ErrorCode = Literal["REQUEST_INVALID", "SCOPE_NOT_SUPPORTED", "IDEMPOTENCY_CONFLICT", "TASK_NOT_FOUND", "TASK_BUSY", "UNSUPPORTED_CONTRACT_VERSION", "SOURCE_INTEGRITY_FAILED", "PUBLICATION_INCOMPLETE", "OUTPUT_UNAVAILABLE", "INPUT_LOCK_CHANGED", "EXTERNAL_RESULT_UNKNOWN", "INVALID_CLARIFICATION", "NEW_RUN_REQUIRED", "BUDGET_LIMIT", "CONNECTION_LOST", "DEPENDENCY_NOT_AVAILABLE"]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
SafeId = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,99}$")]
DecimalString = Annotated[str, Field(pattern=r"^-?\d+(?:\.\d+)?$")]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("*")
    @classmethod
    def utc_timestamps(cls, value):
        if isinstance(value, datetime) and (value.tzinfo is None or value.utcoffset() != timedelta(0)):
            raise ValueError("timestamps require an explicit UTC offset")
        return value


class ArtifactRef(Contract):
    """Trusted export locator relative to APP_ROOT, never a client filesystem path."""
    path: str
    sha256: Digest
    pointer: str = ""

    @field_validator("path")
    @classmethod
    def relative_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if not value or path.is_absolute() or ":" in value or "\\" in value or ".." in path.parts:
            raise ValueError("artifact locator must remain relative to APP_ROOT")
        return value


class ProvenanceView(Contract):
    kind: Literal["SYNTHETIC", "DERIVED_HISTORICAL", "LIVE_PROJECTION"]
    label: str = Field(min_length=1)
    source_artifacts: list[ArtifactRef] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    source_revalidation: Literal["NOT_CHECKED", "VERIFIED", "UNAVAILABLE", "FAILED"] = "NOT_CHECKED"

    @model_validator(mode="after")
    def historical_has_source(self):
        if self.kind == "DERIVED_HISTORICAL" and not self.source_artifacts:
            raise ValueError("historical projection requires source digests")
        return self


class ActionCapability(Contract):
    operation: Operation
    enabled: bool
    reason: str | None = None
    authority: Literal["READ_ONLY", "SIMULATED", "SERVER"]

    @model_validator(mode="after")
    def disabled_reason(self):
        if not self.enabled and not self.reason:
            raise ValueError("disabled action needs a readable reason")
        return self


class BaselineView(Contract):
    task_id: SafeId
    origin: Literal["RESULT_REUSE"] = "RESULT_REUSE"
    label: str
    final_state_sha256: Digest | None = None


class TaskView(Contract):
    task_id: SafeId
    question: str = Field(min_length=1, max_length=2000)
    context: ResearchContext
    workflow: Literal["B1", "B2"]
    execution_mode: Literal["LIVE", "REPLAY", "RULES"]
    origin: Origin
    task_status: TaskStatus
    access_mode: Literal["MOCK", "HISTORY_READ_ONLY", "LIVE_CONTROL"]
    company_labels: dict[str, str] = Field(default_factory=dict)
    baseline: BaselineView | None = None
    created_at: datetime | None = None
    available_actions: list[ActionCapability] = Field(default_factory=list)

    @model_validator(mode="after")
    def identity_and_mode(self):
        if self.context.run_id != self.task_id:
            raise ValueError("task and scope identities disagree")
        if (self.workflow == "B1" and self.execution_mode == "RULES") or (self.workflow == "B2" and self.execution_mode == "REPLAY"):
            raise ValueError("unsupported workflow/mode combination")
        if (self.workflow == "B2") != (self.baseline is not None):
            raise ValueError("B2 requires a baseline; B1 must not invent one")
        if self.access_mode == "MOCK" and self.origin != "WEB_FIXTURE":
            raise ValueError("mock task must visibly carry WEB_FIXTURE origin")
        if self.access_mode == "HISTORY_READ_ONLY" and self.origin != "HISTORICAL_VIEW":
            raise ValueError("history adapter must identify historical browsing")
        if self.origin == "HISTORICAL_VIEW" and self.access_mode != "HISTORY_READ_ONLY":
            raise ValueError("historical browsing cannot escalate into a control adapter")
        if len({a.operation for a in self.available_actions}) != len(self.available_actions):
            raise ValueError("duplicate action capability")
        if not set(self.company_labels) <= set(self.context.company_ids):
            raise ValueError("company label map exceeds frozen scope")
        return self


class RuntimeView(Contract):
    queue_state: Literal["NOT_APPLICABLE", "QUEUED", "DISPATCHED", "UNKNOWN"] = "UNKNOWN"
    worker_state: Literal["ACTIVE", "STOPPED", "UNKNOWN"] = "UNKNOWN"
    connection_state: Literal["CONNECTED", "RECONNECTING", "DISCONNECTED", "NOT_APPLICABLE"]
    cancel_requested: bool = False
    event_cursor: int = Field(default=0, ge=0)
    event_stream_id: str | None = None
    current_step_id: str | None = None
    resume_readiness: Literal["UNKNOWN", "CONFIRMED_SAFE", "UNSAFE"] = "UNKNOWN"
    clarification_status: Literal["NONE", "READY_TO_RESUME", "NEW_RUN_REQUIRED"] = "NONE"
    waiting_gap_ids: list[str] = Field(default_factory=list)
    checkpoint_sha256: Digest | None = None


class PublishedFile(Contract):
    name: Literal["report.md", "final_state.json", "context_capsule.json"]
    sha256: Digest
    digest_matches: bool


class PublicationView(Contract):
    verification: Literal["NOT_PUBLISHED", "PENDING", "VERIFIED", "SIMULATED_VALID", "INVALID", "UNSUPPORTED"]
    result_available: bool = False
    files: list[PublishedFile] = Field(default_factory=list)
    publication_status: Literal["COMPLETED", "PARTIAL"] | None = None
    business_validation: Literal["PASS", "FAIL", "UNKNOWN"] = "UNKNOWN"
    scope_matches: bool | None = None
    reasons: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def verified_bundle(self):
        if self.verification == "VERIFIED":
            if {f.name for f in self.files} != {"report.md", "final_state.json", "context_capsule.json"} or len(self.files) != 3:
                raise ValueError("verified publication requires exactly three files")
            if not all(f.digest_matches for f in self.files) or self.business_validation != "PASS" or self.scope_matches is not True:
                raise ValueError("publication integrity and business gates must pass")
        if self.result_available and self.verification not in {"VERIFIED", "SIMULATED_VALID"}:
            raise ValueError("unverified output cannot be a formal result")
        if self.result_available and self.publication_status is None:
            raise ValueError("available result needs a published outcome")
        return self


class StepView(Contract):
    step_id: str
    node_id: str
    instance_id: str
    identity_kind: Literal["EXECUTION", "SOURCE_RECORD", "SYNTHETIC"]
    origin: Origin
    round: int | None = Field(default=None, ge=1)
    attempt: int | None = Field(default=None, ge=1)
    lifecycle: Literal["NOT_STARTED", "RUNNING", "FUNCTION_RETURNED", "FAILED", "REUSED", "UNKNOWN"]
    acceptance: Literal["UNKNOWN", "CHECKPOINT_CONFIRMED", "PUBLISHED"] = "UNKNOWN"
    occurred_at: datetime | None = None
    output_ids: list[str] = Field(default_factory=list)
    source: ArtifactRef | None = None


class OutputView(Contract):
    output_id: str
    instance_id: str | None = None
    round: int | None = Field(default=None, ge=1)
    kind: Literal["FACTS", "CALCULATIONS", "CLAIMS", "EVIDENCE", "GAPS", "REPORT"]
    origin: Origin
    availability: Literal["AVAILABLE", "NOT_SAVED", "PENDING_VERIFICATION", "INVALID"]
    depends_on: list[str] = Field(default_factory=list)
    produced_at: datetime | None = None
    source: ArtifactRef | None = None
    note: str | None = None


class DisplayEvent(Contract):
    """Source-associated events; journal commit is not a business acceptance."""
    task_id: SafeId
    seq: int = Field(ge=1)
    occurred_at: datetime | None = None
    type: Literal["TASK_STATUS", "NODE_START", "NODE_RETURNED", "NODE_FAILED", "CALL_COMMITTED", "CHECKPOINT_ACCEPTED", "OUTPUT_AVAILABLE", "REPORT_VERIFIED", "CANCEL_REQUESTED", "WAITING_INPUT", "CONNECTION_STATE"]
    origin: Origin
    instance_id: str | None = None
    step_id: str | None = None
    action_id: str | None = None
    journal_key: str | None = None
    round: int | None = Field(default=None, ge=1)
    attempt: int | None = Field(default=None, ge=1)
    output_ids: list[str] = Field(default_factory=list)
    source_event_ids: list[int] = Field(default_factory=list)
    event_stream_id: str | None = None
    source: ArtifactRef | None = None

    @model_validator(mode="after")
    def committed_call_is_not_output(self):
        if self.type == "CALL_COMMITTED" and self.output_ids:
            raise ValueError("journal commit cannot announce business-accepted output")
        return self


class SupplementHistoryView(Contract):
    action: SupplementAction
    result: ToolResult | None = None
    changed_gap_status: Literal["OPEN", "RESOLVED", "LIMITED", "WAITING_INPUT", "FAILED"] | None = None
    failure_type: str | None = None

    @model_validator(mode="after")
    def action_matches_result(self):
        if self.result and self.result.action_id != self.action.action_id:
            raise ValueError("history action/result identities disagree")
        if self.result is None and self.failure_type is None:
            raise ValueError("history needs a result or an explicit failure")
        return self


class SupplementView(Contract):
    rounds: int = Field(ge=0)
    action_count: int = Field(ge=0)
    no_progress_rounds: int = Field(ge=0)
    stop_reason: StopReason | None = None
    gaps: list[SupplementGap] = Field(default_factory=list)
    history: list[SupplementHistoryView] = Field(default_factory=list)


class EvidenceView(Contract):
    record: EvidenceCandidate
    printed_page: str | None = None
    printed_page_source: ArtifactRef | None = None

    @model_validator(mode="after")
    def page_label_source(self):
        if self.printed_page is not None and self.printed_page_source is None:
            raise ValueError("printed page cannot be guessed from physical page")
        return self


class DependencyIssue(Contract):
    owner_id: str
    target_id: str
    reason: Literal["MISSING_RECORD", "MISSING_DIRECT_REFERENCE", "AMBIGUOUS_REFERENCE"]


class DocumentView(Contract):
    """Registered identity and optional metadata; never a native path or guessed page."""
    document_id: str
    title: str | None = None
    company_id: CompanyId
    document_sha256: Digest | None = None
    published_on: date | None = None
    page_count: int | None = Field(default=None, ge=1)
    pdf_access: Literal["NOT_CONNECTED", "AVAILABLE", "UNAVAILABLE"] = "NOT_CONNECTED"
    metadata_source: ArtifactRef | None = None


class ResearchView(Contract):
    observations: list[MetricObservation] = Field(default_factory=list)
    calculations: list[CalculationResult] = Field(default_factory=list)
    calculation_slots: dict[str, dict[str, str]] = Field(default_factory=dict)
    claims: list[ResearchClaim] = Field(default_factory=list)
    gaps: list[ResearchGap] = Field(default_factory=list)
    evidence: list[EvidenceView] = Field(default_factory=list)
    documents: list[DocumentView] = Field(default_factory=list)
    supplement: SupplementView | None = None
    report_markdown: str | None = None
    dependency_issues: list[DependencyIssue] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def decimal_wire_strings(cls, value):
        def inspect(node):
            if isinstance(node, dict):
                if any(node.get(k) is not None and not isinstance(node[k], str) for k in ("raw_value", "standard_value")):
                    raise ValueError("financial observations must use Decimal JSON strings")
                if "calculation_id" in node and node.get("value") is not None and not isinstance(node["value"], str):
                    raise ValueError("financial calculations must use Decimal JSON strings")
                for child in node.values():
                    inspect(child)
            elif isinstance(node, list):
                for child in node:
                    inspect(child)
        inspect(value)
        return value

    @model_validator(mode="after")
    def finite_and_unique(self):
        for rows, key in [(self.observations, "observation_id"), (self.calculations, "calculation_id"), (self.claims, "claim_id")]:
            if len({getattr(row, key) for row in rows}) != len(rows):
                raise ValueError("duplicate research object identity")
        if len({e.record.evidence_id for e in self.evidence}) != len(self.evidence):
            raise ValueError("duplicate evidence identity")
        for row in self.observations:
            if any(v is not None and not v.is_finite() for v in (row.raw_value, row.standard_value)):
                raise ValueError("financial values must be finite")
        if any(c.value is not None and not c.value.is_finite() for c in self.calculations):
            raise ValueError("calculation values must be finite")
        return self


class BudgetView(Contract):
    currency: Literal["USD"] = "USD"
    estimate_kind: Literal["USAGE_ESTIMATE_NOT_INVOICE"] = "USAGE_ESTIMATE_NOT_INVOICE"
    settled_estimate: DecimalString | None = None
    pending_reservation: DecimalString | None = None
    baseline_historical_estimate: DecimalString | None = None
    total_calls: int | None = Field(default=None, ge=0)
    source: ArtifactRef | None = None
    note: str = "未提供金额表示未知，不能显示为零；组合总预算尚未建立。"

    @model_validator(mode="after")
    def nonnegative(self):
        if any(v is not None and Decimal(v) < 0 for v in (self.settled_estimate, self.pending_reservation, self.baseline_historical_estimate)):
            raise ValueError("cost estimate cannot be negative")
        return self


class UIState(Contract):
    panel: Literal["QUESTION", "PROCESS", "REPORT"]
    selected_step_id: str | None = None
    selected_round: int | None = Field(default=None, ge=1)
    follow_progress: bool = True
    reader_open: bool = False
    selected_evidence_id: str | None = None
    text_selection_active: bool = False
    editing_followup: bool = False
    manual_browsing: bool = False
    anchor: str | None = None
    scroll_y: int = Field(default=0, ge=0)
    reduced_motion: bool = False


class TaskSnapshot(Contract):
    schema_version: Literal["1.0.0"] = SCHEMA_VERSION
    task: TaskView
    runtime: RuntimeView
    publication: PublicationView
    provenance: ProvenanceView
    steps: list[StepView] = Field(default_factory=list)
    outputs: list[OutputView] = Field(default_factory=list)
    research: ResearchView = Field(default_factory=ResearchView)
    budget: BudgetView = Field(default_factory=BudgetView)
    ui: UIState

    @model_validator(mode="after")
    def display_safety(self):
        t, p, r = self.task, self.publication, self.runtime
        if r.event_cursor and not r.event_stream_id:
            raise ValueError("nonzero cursor requires an identified event stream")
        if (t.access_mode == "MOCK") != (self.provenance.kind == "SYNTHETIC"):
            raise ValueError("mock provenance cannot impersonate real history/live state")
        if (t.access_mode == "HISTORY_READ_ONLY") != (self.provenance.kind == "DERIVED_HISTORICAL"):
            raise ValueError("historical provenance cannot impersonate a live control projection")
        if p.verification == "SIMULATED_VALID" and t.access_mode != "MOCK":
            raise ValueError("simulated publication is only legal in labelled mock mode")
        if p.verification == "VERIFIED" and t.access_mode == "MOCK":
            raise ValueError("synthetic state cannot claim verified real publication")
        if p.result_available:
            if t.task_status not in {"COMPLETED", "PARTIAL"} or r.cancel_requested or p.publication_status != t.task_status:
                raise ValueError("report requires a matching terminal outcome and no cancellation")
            if self.research.report_markdown is None:
                raise ValueError("available report requires readable content")
        companies, years = set(t.context.company_ids), set(t.context.fiscal_years)
        for observation in self.research.observations:
            if observation.company_id not in companies or observation.fiscal_year not in years or observation.document_published_on > t.context.as_of_date:
                raise ValueError("observation exceeds frozen research scope")
        if any(c.company_id not in companies for c in self.research.claims) or any(e.record.company_id not in companies for e in self.research.evidence) or any(d.company_id not in companies for d in self.research.documents):
            raise ValueError("claim/evidence exceeds frozen company scope")
        calculation_map = {c.calculation_id: c for c in self.research.calculations}
        for company, slots in self.research.calculation_slots.items():
            if company not in companies or any(cid not in calculation_map or calculation_map[cid].company_id not in {None, company} for cid in slots.values()):
                raise ValueError("calculation role mapping is missing or crosses company scope")
        if t.workflow == "B1" and self.research.supplement is not None:
            raise ValueError("B1 cannot claim a B2 supplement ledger")
        if t.workflow == "B2" and p.result_available and self.research.supplement is None:
            raise ValueError("published B2 needs its supplement ledger")
        if t.task_status == "WAITING_INPUT" and (not r.waiting_gap_ids or not r.checkpoint_sha256):
            raise ValueError("waiting requires actual gap and checkpoint identities")
        if r.waiting_gap_ids and self.research.supplement is not None:
            waiting = {g.gap_id for g in self.research.supplement.gaps if g.status == "WAITING_INPUT"}
            if not set(r.waiting_gap_ids) <= waiting:
                raise ValueError("waiting gaps must be present in the checkpoint view")
        for key in ("step_id", "instance_id"):
            if len({getattr(s, key) for s in self.steps}) != len(self.steps):
                raise ValueError("step identities cannot overwrite another attempt/round")
        output_map = {o.output_id: o for o in self.outputs}
        if len(output_map) != len(self.outputs):
            raise ValueError("duplicate output identity")
        for step in self.steps:
            for oid in step.output_ids:
                output = output_map.get(oid)
                if output is None or output.instance_id != step.instance_id:
                    raise ValueError("step output association is missing or mismatched")
                if output.availability == "AVAILABLE" and step.acceptance == "UNKNOWN":
                    raise ValueError("function return alone cannot expose accepted output")
        writes = {"CANCEL", "CLARIFY", "RESUME", "FOLLOWUP", "NEW_TASK"}
        for action in t.available_actions:
            if not action.enabled:
                continue
            if action.operation in writes and action.authority != ("SIMULATED" if t.access_mode == "MOCK" else "SERVER"):
                raise ValueError("write capability authority disagrees with adapter mode")
            if t.access_mode == "HISTORY_READ_ONLY" and action.operation in writes:
                raise ValueError("historical fixtures cannot enable mutations")
            if action.operation == "VIEW_REPORT" and not p.result_available:
                raise ValueError("report action requires verified publication")
            if action.operation == "FOLLOWUP" and (not p.result_available or r.cancel_requested):
                raise ValueError("followup requires a published result")
            if action.operation == "CANCEL" and (t.task_status in {"COMPLETED", "PARTIAL", "CANCELLED"} or r.cancel_requested):
                raise ValueError("cancel capability disagrees with core status")
            if action.operation == "CLARIFY" and (t.task_status != "WAITING_INPUT" or t.workflow != "B2" or r.cancel_requested):
                raise ValueError("clarify requires a waiting B2 checkpoint")
            if action.operation == "RESUME":
                if r.resume_readiness != "CONFIRMED_SAFE" or r.worker_state != "STOPPED" or r.cancel_requested or t.task_status not in {"CREATED", "RUNNING", "FAILED", "WAITING_INPUT"}:
                    raise ValueError("resume requires explicit server-confirmed safety")
                if t.task_status == "WAITING_INPUT" and r.clarification_status != "READY_TO_RESUME":
                    raise ValueError("waiting cannot resume without a recorded decision")
        return self


class CreateTaskRequest(Contract):
    schema_version: Literal["1.0.0"] = SCHEMA_VERSION
    request_id: SafeId
    question: str = Field(min_length=1, max_length=2000)
    workflow: Literal["B1", "B2"]
    execution_mode: Literal["LIVE", "REPLAY", "RULES"]
    company_ids: list[CompanyId] = Field(min_length=1)
    fiscal_years: tuple[int, int]
    as_of_date: date
    corpus_snapshot_id: str
    baseline_task_id: SafeId | None = None

    @model_validator(mode="after")
    def supported_request(self):
        if not self.question.strip():
            raise ValueError("question cannot be blank")
        if (self.workflow == "B1" and self.execution_mode == "RULES") or (self.workflow == "B2" and self.execution_mode == "REPLAY"):
            raise ValueError("unsupported workflow/mode")
        if (self.workflow == "B2") != (self.baseline_task_id is not None):
            raise ValueError("baseline required only for B2")
        if len(set(self.company_ids)) != len(self.company_ids) or self.fiscal_years[1] != self.fiscal_years[0] + 1:
            raise ValueError("companies must be unique and fiscal years adjacent")
        return self


class ControlRequest(Contract):
    request_id: SafeId
    expected_cursor: int = Field(ge=0)


class ClarificationRequest(ControlRequest):
    gap_id: str
    checkpoint_sha256: Digest
    decision: Literal["continue_with_limitations", "new_snapshot_required"]


class FollowupRequest(Contract):
    request_id: SafeId
    question: str = Field(min_length=1, max_length=2000)
    topic: Literal["revenue", "cash_flow", "receivables", "all"] | None = None

    @field_validator("question")
    @classmethod
    def nonblank_question(cls, value):
        if not value.strip():
            raise ValueError("question cannot be blank")
        return value


class FollowupView(Contract):
    status: FollowupStatus
    message: str
    claim_ids: list[str] = Field(default_factory=list)


class ErrorView(Contract):
    code: ErrorCode
    message: str
    request_id: SafeId | None = None
    recovery: Literal["REFRESH_READ", "CORRECT_INPUT", "NEW_TASK", "MANUAL_REVIEW", "NONE"]

    @model_validator(mode="after")
    def no_blind_retry(self):
        if self.code in {"EXTERNAL_RESULT_UNKNOWN", "INPUT_LOCK_CHANGED", "SOURCE_INTEGRITY_FAILED"} and self.recovery == "REFRESH_READ":
            raise ValueError("unsafe operation cannot advertise retry as recovery")
        return self


class FrameExpectation(Contract):
    suggested_panel: Literal["QUESTION", "PROCESS", "REPORT"]
    auto_enter_report: bool
    result_available: bool
    enabled_operations: list[Operation]


class FixtureFrame(Contract):
    name: str
    snapshot: TaskSnapshot
    events: list[DisplayEvent] = Field(default_factory=list)
    expected: FrameExpectation

    @model_validator(mode="after")
    def ordered_associated_events(self):
        seq = [e.seq for e in self.events]
        if seq != sorted(set(seq)) or any(e.task_id != self.snapshot.task.task_id for e in self.events):
            raise ValueError("events must be ordered, unique and task-associated")
        if seq and seq[-1] > self.snapshot.runtime.event_cursor:
            raise ValueError("events cannot exceed snapshot cursor")
        if any(e.event_stream_id != self.snapshot.runtime.event_stream_id for e in self.events):
            raise ValueError("event stream differs from consistent snapshot stream")
        available = {o.output_id for o in self.snapshot.outputs if o.availability == "AVAILABLE"}
        if any(e.type == "OUTPUT_AVAILABLE" and (not e.output_ids or not set(e.output_ids) <= available) for e in self.events):
            raise ValueError("output event needs a saved available output association")
        return self


class ScenarioFixture(Contract):
    schema_version: Literal["1.0.0"] = SCHEMA_VERSION
    scenario_id: SafeId
    title: str
    description: str
    synthetic_execution: bool
    frames: list[FixtureFrame] = Field(min_length=1)
    followups: list[FollowupView] = Field(default_factory=list)
    covered_codes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def labelled_execution(self):
        if len({f.name for f in self.frames}) != len(self.frames):
            raise ValueError("frame names must be unique")
        if any((f.snapshot.task.access_mode == "MOCK") != self.synthetic_execution for f in self.frames):
            raise ValueError("scenario synthetic marker must match every frame")
        return self
