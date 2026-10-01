"""S5 deterministic scope/permission/duplicate checks and gap routing."""
from __future__ import annotations
from finresearch.contracts import MetricObservation, stable_sha256
from finresearch.contracts.research import ResearchContext, HypothesisBatch
from finresearch.contracts.supplement import SupplementGap, SupplementAction, ActionPlan

NEED_QUERIES = {
    "SALES_COLLECTION_DETAIL": "销售商品提供劳务收到的现金 回款 应收账款",
    "PURCHASE_AND_PAYMENTS": "购买商品接受劳务支付的现金 采购付款 经营现金流",
    "RECEIVABLE_AGING": "应收账款 账龄 坏账准备 信用风险",
    "CUSTOMER_MIX": "主要客户 客户集中度 收入结构",
    "TIMING_DETAIL": "经营现金流变动原因说明 回款 结算 付款时点",
}
GAP_TOOL = {"MISSING_METRIC": "QUERY_METRIC", "DISCLOSURE_CONTEXT": "SEARCH_DISCLOSURE",
            "CONTEXT_REVIEW": "READ_CONTEXT",
            "MISSING_EXPLANATION": "SEARCH_DISCLOSURE", "TABLE_REVIEW": "REVIEW_TABLE",
            "SOURCE_VERSION": "VERIFY_VERSION", "MODEL_FAILURE": "REQUEST_CLARIFICATION",
            "SOURCE_REQUEST": "REQUEST_CLARIFICATION"}


def identify_gaps(state: dict, context: ResearchContext) -> list[SupplementGap]:
    """Derive missing numbers from coverage, not model-written missing prose.

    Hypothesis source needs can request context, while independent-confirmation
    and invalid-math requirements remain explicitly LIMITED. Repeated needs for
    the same company merge without losing affected hypothesis Claim IDs.
    """
    rows = [MetricObservation.model_validate(o) for o in state["observations"]]
    present = {(o.company_id, o.fiscal_year, o.metric_id) for o in rows if o.value_status == "OBSERVED"}
    gaps = []
    for company in context.company_ids:
        for year in context.fiscal_years:
            for metric in ("revenue", "operating_cash_flow_net", "accounts_receivable"):
                if (company, year, metric) not in present:
                    gaps.append(SupplementGap(gap_id=f"s5-missing-{company}-{year}-{metric}", company_id=company,
                        gap_type="MISSING_METRIC", severity="CRITICAL", metric_id=metric, fiscal_year=year,
                        description="缺少可用的来源核验财务观察", closing_condition="VERIFIED_METRIC"))
    for raw in state.get("gaps", []):
        kind = raw["gap_type"]
        if kind == "MISSING_METRIC":
            continue
        if kind == "MISSING_COUNTEREVIDENCE":
            gaps.append(SupplementGap(gap_id="s5-" + raw["gap_id"], company_id=raw["company_id"],
                gap_type="INDEPENDENT_CONFIRMATION", severity="MATERIAL", description=raw["description"],
                affected_claim_ids=raw["affected_ids"], closing_condition="INDEPENDENT_REVIEW", status="LIMITED",
                resolution_note="年报披露只能提供线索，当前工具不能独立证明因果"))
        elif kind == "INCOMPARABLE_CALCULATION":
            gaps.append(SupplementGap(gap_id="s5-" + raw["gap_id"], company_id=raw["company_id"],
                gap_type="INVALID_CALCULATION", severity="MATERIAL", description=raw["description"],
                closing_condition="NOT_REPAIRABLE_BY_SEARCH", status="LIMITED", resolution_note="保留负基数/不可比边界，不搜索至强算"))
        elif kind in {"MISSING_EXPLANATION", "CLAIM_REJECTED", "MODEL_FAILURE"}:
            mapped = "MODEL_FAILURE" if kind == "MODEL_FAILURE" else "MISSING_EXPLANATION"
            gaps.append(SupplementGap(gap_id="s5-" + raw["gap_id"], company_id=raw["company_id"],
                gap_type=mapped, severity="MATERIAL", description=raw["description"], affected_claim_ids=raw["affected_ids"],
                closing_condition="NEW_SNAPSHOT_OR_USER_INPUT" if mapped == "MODEL_FAILURE" else "ATTRIBUTED_EXPLANATION"))
    needs = {}
    for raw in state.get("hypotheses", []):
        for hypothesis in HypothesisBatch.model_validate(raw["batch"]).hypotheses:
            for need in set(hypothesis.support_needed + hypothesis.weakening_evidence_needed):
                if need == "INDEPENDENT_CONFIRMATION":
                    continue
                key = (hypothesis.company_id, need)
                needs.setdefault(key, set()).add(f"claim-hyp-{hypothesis.company_id}-{hypothesis.category}")
    for (company, need), claims in sorted(needs.items()):
        gaps.append(SupplementGap(gap_id=f"s5-context-{company}-{need}", company_id=company,
            gap_type="DISCLOSURE_CONTEXT", severity="CONTEXT", description=f"定位{need}相关原文；不自动认证语义支持",
            need=need, affected_claim_ids=sorted(claims), closing_condition="VERIFIED_SOURCE_WINDOW"))
    if len({g.gap_id for g in gaps}) != len(gaps):
        raise ValueError("缺口ID重复")
    return gaps


def action_fingerprint(tool: str, company: str, context: ResearchContext, arguments: dict) -> str:
    """Deduplicate by actual tool arguments and immutable research scope."""
    return stable_sha256({"tool": tool, "company": company, "as_of": context.as_of_date,
                          "snapshot": context.corpus_snapshot_id, "years": context.fiscal_years,
                          "protocol": context.protocol_config_sha256, "arguments": arguments})


def candidate_actions(gaps: list[SupplementGap], context: ResearchContext, seen: set[str]) -> list[SupplementAction]:
    """Only program-generated capabilities enter the model's candidate list."""
    candidates = []
    rank = {"CRITICAL": 0, "MATERIAL": 1, "CONTEXT": 2}
    local_seen = set(seen)
    for gap in sorted(gaps, key=lambda g: (rank[g.severity], g.gap_id)):
        if gap.status != "OPEN" or gap.gap_type not in GAP_TOOL:
            continue
        if gap.company_id not in context.company_ids:
            raise ValueError("缺口公司越界")
        tool = GAP_TOOL[gap.gap_type]
        if tool == "QUERY_METRIC":
            if gap.fiscal_year not in context.fiscal_years:
                raise ValueError("缺口财年越界")
            arguments = {"metric_id": gap.metric_id, "fiscal_year": gap.fiscal_year}
        elif tool == "SEARCH_DISCLOSURE":
            arguments = {"question": NEED_QUERIES[gap.need] if gap.need else "经营活动产生的现金流量净额变动原因说明",
                         "reporting_year": context.fiscal_years[1], "need": gap.need or "EXPLANATION"}
        elif tool == "READ_CONTEXT":
            arguments = {"evidence_id": gap.evidence_id}
        elif tool == "REVIEW_TABLE" and gap.metric_id is not None and gap.fiscal_year is not None:
            arguments = {"metric_id": gap.metric_id, "fiscal_year": gap.fiscal_year}
        else:
            arguments = {"gap_type": gap.gap_type}
        digest = action_fingerprint(tool, gap.company_id, context, arguments)
        if digest in local_seen:
            continue
        local_seen.add(digest)
        candidates.append(SupplementAction(action_id="action-" + digest, gap_id=gap.gap_id, tool=tool,
            company_id=gap.company_id, as_of_date=context.as_of_date, corpus_snapshot_id=context.corpus_snapshot_id,
            arguments=arguments, reason=gap.description))
    return candidates


def approve_plan(plan: ActionPlan, candidates: list[SupplementAction], gaps: list[SupplementGap],
                 context: ResearchContext, seen: set[str], remaining: int, maximum_per_round: int) -> list[SupplementAction]:
    """Validate ID selection against current candidates, budget and prerequisites."""
    ids = plan.selected_action_ids
    by_id = {a.action_id: a for a in candidates}
    gap_map = {g.gap_id: g for g in gaps}
    if len(ids) != len(set(ids)) or len(ids) > min(remaining, maximum_per_round) or any(i not in by_id for i in ids):
        raise ValueError("动作ID未知、重复或超过预算")
    selected = [by_id[i] for i in ids]
    canonical = {a.action_id: a for a in candidate_actions(gaps, context, seen)}
    if any(action != canonical.get(action.action_id) for action in selected):
        raise ValueError("候选动作不是程序从当前缺口生成的规范实参")
    # A critical financial prerequisite cannot be bypassed in favor of prose.
    critical = {a.action_id for a in candidates if gap_map[a.gap_id].severity == "CRITICAL"}
    if critical and not set(ids).intersection(critical):
        raise ValueError("必须先处理关键财务缺口")
    if critical - set(ids) and any(i not in critical for i in ids):
        raise ValueError("未选完关键前提时不能绕去补查解释")
    for action in selected:
        gap = gap_map[action.gap_id]
        fingerprint = action_fingerprint(action.tool, action.company_id, context, action.arguments)
        if (gap.status != "OPEN" or action.tool != GAP_TOOL[gap.gap_type]
                or action.company_id != gap.company_id or action.as_of_date != context.as_of_date
                or action.corpus_snapshot_id != context.corpus_snapshot_id
                or action.action_id != "action-" + fingerprint or fingerprint in seen):
            raise ValueError("动作范围、依赖、权限或指纹失配")
    rank = {"CRITICAL": 0, "MATERIAL": 1, "CONTEXT": 2}
    return sorted(selected, key=lambda a: (rank[gap_map[a.gap_id].severity], a.action_id))
