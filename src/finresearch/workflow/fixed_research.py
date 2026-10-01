"""S4 explicit LangGraph path over independently testable business functions."""
from __future__ import annotations
from dataclasses import dataclass, field
import time
from typing import Callable, TypedDict

from langgraph.graph import StateGraph, START, END

from finresearch.contracts import CalculationResult, MetricObservation, EvidenceCandidate, stable_sha256
from finresearch.contracts.research import (ResearchContext, Phenomenon, HypothesisBatch, DisclosureBatch,
                                           ResearchClaim, ResearchGap, WriterPlan)
from .research_analysis import calculate_research, detect_phenomena, calculation_claims
from .research_claims import review_hypotheses, review_disclosures, attach_hypothesis_disclosures, validate_writer_plan, render_report, validate_report


class ResearchState(TypedDict, total=False):
    context: dict
    observations: list[dict]
    calculations: dict
    phenomena: list[dict]
    hypotheses: list[dict]
    evidence: list[dict]
    disclosures: list[dict]
    claims: list[dict]
    gaps: list[dict]
    trace: list[str]
    execution_status: str
    writer_ids: list[str]
    report: str
    validation: dict


@dataclass
class ResearchDependencies:
    protocol: dict
    load_financials: Callable
    propose_hypotheses: Callable
    collect_fixed_evidence: Callable
    select_disclosures: Callable
    plan_writer: Callable
    event_sink: Callable = lambda event: None
    financial_evidence: list = field(default_factory=list)
    source_links: dict = field(default_factory=dict)


def compile_research_graph(deps: ResearchDependencies, checkpointer=None):
    """Compile the fixed path; model errors produce explicit partial artifacts.

    The provider timeout bounds external calls. Local soft timeout is checked
    after a node returns; it does not promise to forcibly kill blocking I/O.
    Checkpoints persist JSON state between steps, not arbitrary process objects.
    """
    def context(state):
        return ResearchContext.model_validate(state["context"])

    def gap(company, node, exc):
        return ResearchGap(gap_id=f"gap-{company}-{node}", company_id=company, gap_type="MODEL_FAILURE",
            description=f"{node}未完成，错误类型{type(exc).__name__}", suggested_next_step="核对失败审计，在预算内新建运行；S4不自主重试").model_dump(mode="json")

    def validate_context(state):
        ctx = context(state)
        if ctx.protocol_id != deps.protocol["protocol_id"] or ctx.protocol_version != deps.protocol["version"] or list(ctx.fiscal_years) != deps.protocol["fiscal_years"]:
            raise ValueError("请求协议或期间与冻结协议不一致")
        if ctx.protocol_config_sha256 != stable_sha256(deps.protocol):
            raise ValueError("请求未锁定当前协议配置指纹")
        return {"gaps": [], "trace": [], "execution_status": "COMPLETED"}

    def load_financials(state):
        observations = deps.load_financials(context(state))
        return {"observations": [o.model_dump(mode="json") for o in observations]}

    def calculate(state):
        rows, gaps = calculate_research(context(state), [MetricObservation.model_validate(o) for o in state["observations"]])
        return {"calculations": {company: {name: calc.model_dump(mode="json") for name, calc in group.items()} for company, group in rows.items()},
                "gaps": state["gaps"] + [g.model_dump(mode="json") for g in gaps]}

    def parse_calculations(state):
        return {company: {name: CalculationResult.model_validate(c) for name, c in rows.items()} for company, rows in state["calculations"].items()}

    def detect(state):
        return {"phenomena": [p.model_dump(mode="json") for p in detect_phenomena(parse_calculations(state), context(state).fiscal_years)]}

    def propose(state):
        ctx = context(state)
        phenomena = [Phenomenon.model_validate(p) for p in state["phenomena"]]
        batches = []
        gaps = list(state["gaps"])
        partial = state["execution_status"]
        for company in ctx.company_ids:
            selected = [p for p in phenomena if p.company_id == company]
            if not selected:
                continue
            try:
                batch = deps.propose_hypotheses(ctx, company, selected)
                review_hypotheses(batch, company, selected)
                batches.append({"company_id": company, "batch": batch.model_dump(mode="json")})
            except (RuntimeError, ValueError) as exc:
                gaps.append(gap(company, "propose_hypotheses", exc))
                partial = "PARTIAL"
        return {"hypotheses": batches, "gaps": gaps, "execution_status": partial}

    def collect(state):
        ctx = context(state)
        evidence = []
        disclosures = []
        gaps = list(state["gaps"])
        partial = state["execution_status"]
        for company in ctx.company_ids:
            candidates = deps.collect_fixed_evidence(ctx, company)
            if any(e.company_id != company for e in candidates):
                raise ValueError("固定证据查询返回了其他公司")
            evidence.extend(candidates)
            try:
                batch = deps.select_disclosures(ctx, company, candidates)
                disclosures.append(batch.model_dump(mode="json"))
            except (RuntimeError, ValueError) as exc:
                gaps.append(gap(company, "select_disclosures", exc))
                partial = "PARTIAL"
        return {"evidence": [e.model_dump(mode="json") for e in evidence], "disclosures": disclosures,
                "gaps": gaps, "execution_status": partial}

    def review(state):
        ctx = context(state)
        claims = calculation_claims(parse_calculations(state))
        gaps = [ResearchGap.model_validate(g) for g in state["gaps"]]
        phenomena = [Phenomenon.model_validate(p) for p in state["phenomena"]]
        for item in state["hypotheses"]:
            _, reviewed, missing = review_hypotheses(HypothesisBatch.model_validate(item["batch"]), item["company_id"], phenomena)
            claims.extend(reviewed)
            gaps.extend(missing)
        evidence = {e["evidence_id"]: EvidenceCandidate.model_validate(e) for e in state["evidence"]}
        for item in state["disclosures"]:
            batch = DisclosureBatch.model_validate(item)
            reviewed, missing = review_disclosures(batch, batch.company_id, evidence)
            claims.extend(reviewed)
            gaps.extend(missing)
        claims = attach_hypothesis_disclosures(claims, [DisclosureBatch.model_validate(i) for i in state["disclosures"]], evidence)
        for g in list(gaps):
            claims.append(ResearchClaim(claim_id=f"claim-{g.gap_id}", company_id=g.company_id, kind="UNRESOLVED",
                text=g.description, status="APPROVED", limitations=[g.suggested_next_step]))
        return {"claims": [c.model_dump(mode="json") for c in claims], "gaps": [g.model_dump(mode="json") for g in gaps]}

    def write(state):
        ctx = context(state)
        claims = [ResearchClaim.model_validate(c) for c in state["claims"]]
        gaps = [ResearchGap.model_validate(g) for g in state["gaps"]]
        status = state["execution_status"]
        try:
            plan = deps.plan_writer(ctx, claims)
            ids = validate_writer_plan(plan, claims)
        except (RuntimeError, ValueError) as exc:
            g = ResearchGap.model_validate(gap(ctx.company_ids[0], "write_report", exc))
            gaps.append(g)
            status = "PARTIAL"
            # A failed model writer still yields an explicitly partial factual
            # report. This is a transparent fallback, never a live-writer pass.
            ids = [c.claim_id for c in claims if c.status == "APPROVED"]
        report = render_report(ctx, claims, gaps, ids, execution_status=status, **report_inputs(state))
        return {"writer_ids": ids, "report": report, "gaps": [g.model_dump(mode="json") for g in gaps], "execution_status": status}

    def validate(state):
        return {"validation": validate_report(state["report"], context(state), [ResearchClaim.model_validate(c) for c in state["claims"]],
                    [ResearchGap.model_validate(g) for g in state["gaps"]], state["writer_ids"], state["execution_status"], **report_inputs(state))}

    def report_inputs(state):
        evidence = {e.evidence_id: e for e in deps.financial_evidence}
        evidence.update({e["evidence_id"]: EvidenceCandidate.model_validate(e) for e in state["evidence"]})
        return dict(observations=[MetricObservation.model_validate(o) for o in state["observations"]],
            calculations=parse_calculations(state), evidence=list(evidence.values()), source_links=deps.source_links)

    functions = dict(validate_context=validate_context, load_financials=load_financials, calculate=calculate,
                     detect_phenomena=detect, propose_hypotheses=propose, collect_fixed_evidence=collect,
                     review_claims=review, write_report=write, validate_report=validate)
    if set(functions) != set(deps.protocol["nodes"]):
        raise ValueError("节点实现与协议不同")
    def instrument(name, fn):
        def run(state):
            started = time.perf_counter()
            deps.event_sink({"node": name, "event": "START"})
            try:
                update = fn(state)
                elapsed = time.perf_counter() - started
                if elapsed > deps.protocol["node_soft_timeout_seconds"]:
                    raise TimeoutError("节点超过soft timeout，未宣称强制中断")
                update["trace"] = state.get("trace", []) + [name]
                deps.event_sink({"node": name, "event": "END", "elapsed_seconds": elapsed})
                return update
            except Exception as exc:
                deps.event_sink({"node": name, "event": "FAILED", "exception_type": type(exc).__name__})
                raise
        return run
    builder = StateGraph(ResearchState)
    previous = START
    for name in deps.protocol["nodes"]:
        builder.add_node(name, instrument(name, functions[name]))
        builder.add_edge(previous, name)
        previous = name
    builder.add_edge(previous, END)
    return builder.compile(checkpointer=checkpointer)
