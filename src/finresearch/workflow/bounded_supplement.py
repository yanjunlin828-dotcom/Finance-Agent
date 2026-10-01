"""S5 bounded LangGraph loop over program-authorized read-only actions."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Callable, TypedDict
from langgraph.graph import StateGraph, START, END
from finresearch.contracts import MetricObservation, EvidenceCandidate
from finresearch.contracts.research import ResearchContext, ResearchClaim, ResearchGap, HypothesisBatch, DisclosureBatch
from finresearch.contracts.supplement import SupplementGap, SupplementAction, ActionPlan, ToolResult
from .supplement_controller import identify_gaps, candidate_actions, approve_plan, action_fingerprint
from .research_analysis import calculate_research, calculation_claims, detect_phenomena
from .research_claims import render_report, review_hypotheses, attach_hypothesis_disclosures


class SupplementState(TypedDict, total=False):
    context: dict
    baseline: dict
    current: dict
    gaps: list[dict]
    selected: list[dict]
    seen: list[str]
    history: list[dict]
    rounds: int
    action_count: int
    no_progress_rounds: int
    status: str
    stop_reason: str
    added_evidence_ids: list[str]
    report: str
    validation: dict


@dataclass
class SupplementDependencies:
    policy: dict
    select_actions: Callable
    execute_tool: Callable
    verify_observation: Callable
    verify_evidence: Callable
    select_quotes: Callable
    source_links: dict
    initial_seen: set[str]
    initial_gaps: list[SupplementGap] | None = None
    event_sink: Callable = lambda e: None


def refresh_financials(state: dict, context: ResearchContext) -> dict:
    """Recompute affected chains; never let a model replace numeric claims."""
    current = dict(state)
    calculations, numerical_gaps = calculate_research(context, [MetricObservation.model_validate(o) for o in current["observations"]])
    current["calculations"] = {company: {name: c.model_dump(mode="json") for name, c in rows.items()} for company, rows in calculations.items()}
    phenomena = detect_phenomena(calculations, context.fiscal_years)
    current["phenomena"] = [p.model_dump(mode="json") for p in phenomena]
    gaps = [g for g in current.get("gaps", []) if g["gap_type"] not in {"MISSING_METRIC", "INCOMPARABLE_CALCULATION", "MISSING_COUNTEREVIDENCE"}]
    gaps += [g.model_dump(mode="json") for g in numerical_gaps]
    current["gaps"] = gaps
    claims = [c for c in current.get("claims", []) if c["kind"] not in {"COMPUTED", "UNRESOLVED", "INFERENCE"}]
    valid_calcs = {c.calculation_id for rows in calculations.values() for c in rows.values()}
    claims = [c for c in claims if set(c["calculation_ids"]).issubset(valid_calcs)]
    claims += [c.model_dump(mode="json") for c in calculation_claims(calculations)]
    # Restore qualified candidates only when their original phenomenon inputs
    # exist again. Filling one metric must restore the whole affected DAG,
    # rather than leaving stale missing Claim IDs behind.
    hypothesis_claims = []
    available_phenomena = {p.phenomenon_id for p in phenomena}
    for raw in current.get("hypotheses", []):
        batch = HypothesisBatch.model_validate(raw["batch"])
        if not all(set(h.phenomenon_ids).issubset(available_phenomena) for h in batch.hypotheses):
            continue
        _, reviewed, missing = review_hypotheses(batch, raw["company_id"], phenomena)
        hypothesis_claims.extend(reviewed)
        gaps += [g.model_dump(mode="json") for g in missing]
    hypothesis_claims = attach_hypothesis_disclosures(hypothesis_claims,
        [DisclosureBatch.model_validate(d) for d in current.get("disclosures", [])],
        {e["evidence_id"]: EvidenceCandidate.model_validate(e) for e in current["evidence"]})
    claims += [c.model_dump(mode="json") for c in hypothesis_claims]
    current["gaps"] = gaps
    claims += [ResearchClaim(claim_id="claim-" + g["gap_id"], company_id=g["company_id"], kind="UNRESOLVED",
                            text=g["description"], status="APPROVED", limitations=[g["suggested_next_step"]]).model_dump(mode="json") for g in gaps]
    current["claims"] = claims
    return current


def render_supplement(state: SupplementState, deps: SupplementDependencies) -> str:
    """Render only approved source facts and the explicit action/gap ledger."""
    from finresearch.contracts import CalculationResult
    ctx = ResearchContext.model_validate(state["context"])
    current = state["current"]
    claims = [ResearchClaim.model_validate(c) for c in current["claims"]]
    calculations = {company: {name: CalculationResult.model_validate(c) for name, c in group.items()}
                    for company, group in current["calculations"].items()}
    text = render_report(ctx, claims, [ResearchGap.model_validate(g) for g in current["gaps"]],
                         [c.claim_id for c in claims if c.status == "APPROVED"], execution_status=state["status"],
                         observations=[MetricObservation.model_validate(o) for o in current["observations"]],
                         calculations=calculations, evidence=[EvidenceCandidate.model_validate(e) for e in current["evidence"]],
                         source_links=deps.source_links)
    lines = [text, "## S5补查与停止记录", "", f"终态：{state['status']}；停止原因：{state['stop_reason']}；轮数：{state['rounds']}；工具动作：{state['action_count']}。", "",
             "定位到相关原文仅证明资料可用，不构成独立因果核验。", ""]
    for gap in state["gaps"]:
        lines += [f"- {gap['gap_id']}／{gap['gap_type']}／{gap['status']}：{gap['description']}；处置：{gap.get('resolution_note') or '仍未完成'}。", ""]
    return "\n".join(lines)


def compile_supplement_graph(deps: SupplementDependencies, checkpointer=None):
    """Finite loop with explicit completion, partial, waiting and hard failures.

    All callbacks are application-controlled capabilities. Model selection is
    only ActionPlan IDs. Tool results are independently verified before they
    enter facts or source evidence. No automatic source/snapshot mutation.
    """
    policy = deps.policy
    for name in ("maximum_rounds", "maximum_actions_per_round", "maximum_actions", "maximum_no_progress_rounds"):
        if not isinstance(policy[name], int) or isinstance(policy[name], bool) or policy[name] <= 0:
            raise ValueError("循环上限必须为正整数")
    if policy["maximum_actions_per_round"] > 2:
        raise ValueError("每轮最多两个动作")

    def context(state):
        return ResearchContext.model_validate(state["context"])

    def initialize(state):
        ctx = context(state)
        baseline = state["baseline"]
        from .session_context import scope_key
        if scope_key(ResearchContext.model_validate(baseline["context"])) != scope_key(ctx):
            raise ValueError("补查与B1范围不同，须新建派生研究")
        current = dict(baseline)
        current["context"] = ctx.model_dump(mode="json")
        gaps = deps.initial_gaps if deps.initial_gaps is not None else identify_gaps(current, ctx)
        if any(g.company_id not in ctx.company_ids for g in gaps):
            raise ValueError("补查缺口公司越界")
        return {"current": current, "gaps": [g.model_dump(mode="json") for g in gaps], "seen": sorted(deps.initial_seen),
                "history": [], "rounds": 0, "action_count": 0, "no_progress_rounds": 0,
                "status": "RUNNING", "stop_reason": "", "added_evidence_ids": []}

    def plan(state):
        limits = ((state["rounds"] >= policy["maximum_rounds"], "ROUND_LIMIT"),
                  (state["action_count"] >= policy["maximum_actions"], "ACTION_BUDGET"),
                  (state["no_progress_rounds"] >= policy["maximum_no_progress_rounds"], "NO_PROGRESS"))
        for reached, reason in limits:
            if reached:
                return {"status": "PARTIAL", "stop_reason": reason, "selected": []}
        gaps = [SupplementGap.model_validate(g) for g in state["gaps"]]
        candidates = candidate_actions(gaps, context(state), set(state["seen"]))
        candidates = [a for a in candidates if a.tool in policy["allowed_tools"]]
        remaining = policy["maximum_actions"] - state["action_count"]
        if not candidates:
            unresolved = any(g.status != "RESOLVED" for g in gaps) or bool(state["current"].get("gaps"))
            return {"status": "PARTIAL" if unresolved else "COMPLETED", "stop_reason": "NO_ACTIONABLE_GAPS" if unresolved else "ALL_RESOLVED", "selected": []}
        try:
            proposal = deps.select_actions(context(state), state["rounds"] + 1, candidates, min(remaining, policy["maximum_actions_per_round"]))
            selected = approve_plan(proposal, candidates, gaps, context(state), set(state["seen"]), remaining, policy["maximum_actions_per_round"])
        except (RuntimeError, ValueError) as exc:
            deps.event_sink({"event": "PLAN_REJECTED", "round": state["rounds"] + 1, "exception_type": type(exc).__name__})
            return {"status": "PARTIAL", "stop_reason": "MODEL_PLAN_REJECTED_OR_BUDGET", "selected": []}
        return {"selected": [a.model_dump(mode="json") for a in selected], "rounds": state["rounds"] + 1}

    def execute(state):
        ctx = context(state)
        current = dict(state["current"])
        current["observations"] = list(current["observations"])
        current["evidence"] = list(current["evidence"])
        gaps = {g["gap_id"]: SupplementGap.model_validate(g) for g in state["gaps"]}
        seen = set(state["seen"])
        history = list(state["history"])
        added = set(state["added_evidence_ids"])
        progress = False
        count = state["action_count"]
        status, stop = "RUNNING", ""
        for raw in state["selected"]:
            action = SupplementAction.model_validate(raw)
            gap = gaps[action.gap_id]
            fingerprint = action_fingerprint(action.tool, action.company_id, ctx, action.arguments)
            if fingerprint in seen or count >= policy["maximum_actions"]:
                raise ValueError("执行器发现重复或超预算动作")
            seen.add(fingerprint)
            count += 1
            deps.event_sink({"event": "ACTION_START", "action": action.model_dump(mode="json")})
            try:
                result = ToolResult.model_validate(deps.execute_tool(ctx, action))
                if result.action_id != action.action_id:
                    raise ValueError("工具返回动作身份错误")
                if result.observations and action.tool not in {"QUERY_METRIC", "REVIEW_TABLE"}:
                    raise ValueError("非指标工具不能写财务观察")
                verified_rows = []
                for row in result.observations:
                    item = MetricObservation.model_validate(row)
                    if (item.company_id != action.company_id or item.fiscal_year not in ctx.fiscal_years
                            or item.document_published_on > ctx.as_of_date
                            or action.tool == "QUERY_METRIC" and (item.metric_id != action.arguments["metric_id"] or item.fiscal_year != action.arguments["fiscal_year"])
                            or not deps.verify_observation(ctx, item)):
                        raise ValueError("工具观察不符合源头或研究范围")
                    verified_rows.append(item)
                verified_evidence = []
                for raw_evidence in result.evidence:
                    evidence = EvidenceCandidate.model_validate(raw_evidence)
                    if evidence.company_id != action.company_id or not deps.verify_evidence(ctx, evidence):
                        raise ValueError("工具原文不符合源头或研究范围")
                    verified_evidence.append(evidence)
            except RuntimeError as exc:
                gap = gap.model_copy(update={"status": "FAILED", "resolution_note": "工具失败，未伪造成功"})
                gaps[gap.gap_id] = gap
                history.append({"action": raw, "status": "FAILED", "exception_type": type(exc).__name__})
                status, stop = "PARTIAL", "TOOL_FAILURE"
                break
            if result.status in {"REQUIRES_NEW_SNAPSHOT", "NEEDS_INPUT"}:
                gap = gap.model_copy(update={"status": "WAITING_INPUT", "resolution_note": result.note})
                status, stop = "WAITING_INPUT", result.status
            elif result.status == "FOUND":
                existing = {(o["company_id"], o["fiscal_year"], o["metric_id"]): o for o in current["observations"]}
                for item in verified_rows:
                    key = (item.company_id, item.fiscal_year, item.metric_id)
                    payload = item.model_dump(mode="json")
                    if key in existing and existing[key] != payload:
                        status, stop = "WAITING_INPUT", "REQUIRES_NEW_SNAPSHOT"
                        gap = gap.model_copy(update={"status": "WAITING_INPUT", "resolution_note": "原财务记录存在冲突，不覆盖旧观察"})
                        break
                    if key not in existing:
                        current["observations"].append(payload)
                        existing[key] = payload
                        progress = True
                evidence_index = {e["evidence_id"]: e for e in current["evidence"]}
                for item in verified_evidence:
                    payload = item.model_dump(mode="json")
                    # Rank/score belong to a query, not the identity of a source
                    # window. Preserve the first locator when only rank changes.
                    source_fields = lambda r: {k: v for k, v in r.items() if k not in {"retrieval_rank", "retrieval_score", "matched_terms"}}
                    if item.evidence_id in evidence_index and source_fields(evidence_index[item.evidence_id]) != source_fields(payload):
                        raise ValueError("同一证据ID存在内容冲突")
                    if item.evidence_id not in evidence_index:
                        current["evidence"].append(payload)
                        evidence_index[item.evidence_id] = payload
                        added.add(item.evidence_id)
                        progress = True
                if status == "RUNNING":
                    if gap.closing_condition == "VERIFIED_METRIC" and verified_rows:
                        gap = gap.model_copy(update={"status": "RESOLVED", "resolution_note": "来源核验观察已补齐并将重算；未修改旧权威来源"})
                        progress = True
                    elif gap.closing_condition == "VERIFIED_SOURCE_WINDOW" and verified_evidence:
                        gap = gap.model_copy(update={"status": "RESOLVED", "resolution_note": "已定位可追溯原文窗口；语义支持与因果仍未独立认证"})
                        progress = True
                    else:
                        gap = gap.model_copy(update={"status": "LIMITED", "resolution_note": "已取得资料，但关闭条件仍需要独立语义/版本审核"})
            else:
                gap = gap.model_copy(update={"status": "LIMITED", "resolution_note": result.note + "；未召回不等于未披露"})
            gap = gap.model_copy(update={"attempted_action_ids": gap.attempted_action_ids + [action.action_id]})
            gaps[gap.gap_id] = gap
            history.append({"action": raw, "result": result.model_dump(mode="json"), "changed_gap_status": gap.status})
            if status != "RUNNING":
                break
        current = refresh_financials(current, ctx)
        for fresh_gap in identify_gaps(current, ctx):
            if fresh_gap.gap_id not in gaps:
                gaps[fresh_gap.gap_id] = fresh_gap
        return {"current": current, "gaps": [g.model_dump(mode="json") for g in gaps.values()],
                "seen": sorted(seen), "history": history, "action_count": count, "added_evidence_ids": sorted(added),
                "no_progress_rounds": 0 if progress else state["no_progress_rounds"] + 1, "status": status, "stop_reason": stop}

    def finish(state):
        current = dict(state["current"])
        try:
            candidates = [EvidenceCandidate.model_validate(e) for e in current["evidence"] if e["evidence_id"] in state["added_evidence_ids"]]
            added_claims = deps.select_quotes(context(state), candidates) if candidates else []
            known_evidence = {e["evidence_id"]: EvidenceCandidate.model_validate(e) for e in current["evidence"]}
            existing_ids = {c["claim_id"] for c in current["claims"]}
            from .evidence_selection import quote_options
            allowed_quotes = {(o["evidence_id"], o["exact_quote"]) for o in quote_options(candidates)}
            if len(added_claims) > 6:
                raise ValueError("新增披露最多六条")
            for claim in added_claims:
                if claim.kind != "DISCLOSED" or claim.company_id not in context(state).company_ids or claim.status != "APPROVED":
                    raise ValueError("补查写作只能新增已核验公司披露")
                if not claim.evidence_ids or any(e not in known_evidence or known_evidence[e].company_id != claim.company_id for e in claim.evidence_ids):
                    raise ValueError("新披露证据不存在或公司错误")
                prefix, suffix = "公司披露：“", "”"
                if not claim.text.startswith(prefix) or not claim.text.endswith(suffix):
                    raise ValueError("新增披露不是受控原句模板")
                quote = claim.text[len(prefix):-len(suffix)]
                if not all((eid, quote) in allowed_quotes for eid in claim.evidence_ids):
                    raise ValueError("新增披露不是完整已核验原文句子")
                duplicate = any(c["company_id"] == claim.company_id and c["text"] == claim.text for c in current["claims"])
                if claim.claim_id not in existing_ids and not duplicate:
                    claim = claim.model_copy(update={"limitations": ["公司原文披露，不构成独立因果核验"]})
                    current["claims"] = current["claims"] + [claim.model_dump(mode="json")]
                    existing_ids.add(claim.claim_id)
        except (RuntimeError, ValueError) as exc:
            deps.event_sink({"event": "QUOTE_REVIEW_REJECTED", "exception_type": type(exc).__name__})
            state = dict(state, status="PARTIAL", stop_reason="QUOTE_REVIEW_FAILED")
        complete = dict(state, current=current)
        report = render_supplement(complete, deps)
        return {"current": current, "status": complete["status"], "stop_reason": complete["stop_reason"], "report": report,
                "validation": {"status": "PASS", "policy": "PROGRAM_RENDERED_SOURCE_FACTS_AND_GAP_LEDGER_NOT_CAUSAL_CERTIFICATION"}}

    graph = StateGraph(SupplementState)
    graph.add_node("audit_gaps", initialize)
    graph.add_node("plan_actions", plan)
    graph.add_node("execute_actions", execute)
    graph.add_node("write_supplement_report", finish)
    graph.add_edge(START, "audit_gaps")
    graph.add_edge("audit_gaps", "plan_actions")
    graph.add_conditional_edges("plan_actions", lambda s: "execute" if s["status"] == "RUNNING" else "finish",
                               {"execute": "execute_actions", "finish": "write_supplement_report"})
    graph.add_conditional_edges("execute_actions", lambda s: "plan" if s["status"] == "RUNNING" else "finish",
                               {"plan": "plan_actions", "finish": "write_supplement_report"})
    graph.add_edge("write_supplement_report", END)
    return graph.compile(checkpointer=checkpointer)
