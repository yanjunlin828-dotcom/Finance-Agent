"""Bounded hypothesis/quote review and a Writer that cannot add facts."""
from __future__ import annotations
from finresearch.contracts.research import HypothesisBatch, DisclosureBatch, ResearchClaim, ResearchGap, WriterPlan, Phenomenon, ResearchContext

_CATEGORY = {"SCALE_EXPANSION": "经营规模扩张", "SETTLEMENT_TIMING": "结算回款时点差异", "CUSTOMER_MIX": "客户结构变化", "UNRESOLVED_CAUSE": "原因未明"}


def review_hypotheses(batch: HypothesisBatch, company: str, phenomena: list[Phenomenon]):
    """Untrusted rationales remain candidates; only qualified templates publish."""
    valid_ids = {p.phenomenon_id for p in phenomena if p.company_id == company}
    approved = []
    claims = []
    gaps = []
    seen = set()
    for candidate in batch.hypotheses:
        if candidate.company_id != company or not set(candidate.phenomenon_ids) <= valid_ids:
            raise ValueError("候选解释使用了其他公司或未知现象")
        if candidate.category in seen:
            raise ValueError("同公司候选类别重复")
        seen.add(candidate.category)
        approved.append(candidate)
        claim_id = f"claim-hyp-{company}-{candidate.category}"
        text = ("现有资料尚不足以确认上述现象的主要原因。" if candidate.category == "UNRESOLVED_CAUSE" else
                f"上述现象可能与{_CATEGORY[candidate.category]}相容，但尚未独立证实，不能据此确认主要原因。")
        claims.append(ResearchClaim(claim_id=claim_id, company_id=company, kind="INFERENCE", text=text,
            calculation_ids=sorted({cid for p in phenomena if p.phenomenon_id in candidate.phenomenon_ids for cid in p.calculation_ids}),
            hypothesis_category=candidate.category, status="APPROVED",
            limitations=["候选解释，不是已验证因果", f"支持需求: {candidate.support_needed}", f"削弱条件需检查: {candidate.weakening_evidence_needed}"]))
        gaps.append(ResearchGap(gap_id=f"gap-{claim_id}", company_id=company, gap_type="MISSING_COUNTEREVIDENCE",
            description="候选解释的支持需求及削弱条件尚未完成独立核验", affected_ids=[claim_id],
            suggested_next_step="S5按缺口补证据；S4保留未决，不自动搜索"))
    return approved, claims, gaps


def review_disclosures(batch: DisclosureBatch, company: str, evidence: dict):
    """Only exact substrings from this company's eligible evidence become facts.

    Quoting company text proves disclosure, not truth of its causal explanation.
    A fabricated quote or evidence ID is rejected with an explicit gap.
    """
    if batch.company_id != company:
        raise ValueError("披露选择公司错误")
    claims = []
    gaps = []
    for index, item in enumerate(batch.selected):
        candidate = evidence.get(item.evidence_id)
        claim_id = f"claim-disclosed-{company}-{index}"
        if candidate is None or candidate.company_id != company or item.exact_quote not in candidate.text:
            gaps.append(ResearchGap(gap_id=f"gap-{claim_id}", company_id=company, gap_type="CLAIM_REJECTED",
                description="模型选择的引用或原句无法在本次证据中核验", affected_ids=[claim_id], suggested_next_step="读取原文重新核对；不使用该句"))
            continue
        claims.append(ResearchClaim(claim_id=claim_id, company_id=company, kind="DISCLOSED", text=f"公司披露：“{item.exact_quote}”",
            evidence_ids=[item.evidence_id], status="APPROVED", limitations=["公司原文的解释，不构成独立验证的因果结论"]))
    if not claims:
        gaps.append(ResearchGap(gap_id=f"gap-{company}-explanation", company_id=company, gap_type="MISSING_EXPLANATION",
            description="未得到可核对的公司披露解释", suggested_next_step="在合法范围内补查解释性披露"))
    return claims, gaps


def attach_hypothesis_disclosures(claims, batches, evidence):
    """Link exact quotes to candidate explanations without certifying causality.

    Relation labels are model assessments pending semantic review. They do not
    discharge independent-confirmation gaps or alter a calculated fact.
    """
    updated = []
    for claim in claims:
        # Only candidate explanations acquire model-assessed relation links.
        # A disclosed fact already owns its exact source; retain that provenance
        # and all other non-inference dependencies during the association pass.
        if claim.kind != "INFERENCE":
            updated.append(claim)
            continue
        links = []
        for batch in batches:
            if batch.company_id != claim.company_id or claim.kind != "INFERENCE":
                continue
            for item in batch.selected:
                source = evidence.get(item.evidence_id)
                if (claim.hypothesis_category in item.related_hypothesis_categories and source is not None
                    and source.company_id == claim.company_id and item.exact_quote in source.text):
                    links.append((item.evidence_id, item.relation))
        updated.append(claim.model_copy(update={"evidence_ids": sorted({i for i, _ in links}),
            "limitations": claim.limitations + ([f"模型标记相关披露({', '.join(sorted({r for _, r in links}))})，仅核验原句存在，语义关联待复核"] if links else [])}))
    return updated


def validate_writer_plan(plan: WriterPlan, claims: list[ResearchClaim]):
    known = {c.claim_id for c in claims if c.status == "APPROVED"}
    ids = plan.ordered_claim_ids
    if len(ids) != len(set(ids)) or set(ids) != known:
        raise ValueError("Writer必须且只能组织全部已批准Claim，不得新增、重复或遗漏")
    return ids


def render_report(context: ResearchContext, claims: list[ResearchClaim], gaps: list[ResearchGap], ordered_ids: list[str], *, execution_status: str,
                  observations=(), calculations=None, evidence=(), source_links=None) -> str:
    """Exact approved text is rendered by program; no open-ended model prose."""
    by_id = {c.claim_id: c for c in claims}
    if set(ordered_ids) != {c.claim_id for c in claims if c.status == "APPROVED"} or len(ordered_ids) != len(set(ordered_ids)):
        raise ValueError("报告Claim集合与批准集合不同")
    lines = ["# 半导体设备公司收入增长质量研究", "", f"研究截止日：{context.as_of_date}；比较财年：{context.fiscal_years[0]}、{context.fiscal_years[1]}。",
             f"资料快照：{context.corpus_snapshot_id}；协议：{context.protocol_id}/{context.protocol_version}；执行状态：{execution_status}。", "",
             "范围为三项合并财务指标和公开年报披露。比较列按本次年报披露日可用，不回填到历史。", ""]
    if observations:
        labels = {"revenue": "营业收入", "operating_cash_flow_net": "经营现金流净额", "accounts_receivable": "期末应收账款账面价值"}
        lines += ["## 财务比较表", "", "金额单位为人民币元，合并口径。未作综合风险排名。", "",
                  "| 公司 | 财年 | 指标 | 金额/状态 | 观察ID |", "|---|---|---|---:|---|"]
        for o in sorted(observations, key=lambda o: (o.company_id, o.fiscal_year, o.metric_id)):
            value = f"{o.standard_value:,.2f}" if o.value_status == "OBSERVED" and o.standard_value is not None else o.value_status
            lines.append(f"| {o.company_id} | {o.fiscal_year} | {labels[o.metric_id]} | {value} | {o.observation_id} |")
        lines.append("")
    for company in context.company_ids:
        lines += [f"## {company}", ""]
        for cid in ordered_ids:
            c = by_id[cid]
            if c.company_id != company:
                continue
            refs = c.calculation_ids + c.evidence_ids + c.observation_ids
            lines += [f"- [{c.kind}] {c.text} 来源ID：{', '.join(refs) if refs else '受限候选解释'}。", ""]
            if c.limitations:
                lines += ["  限定：" + "；".join(c.limitations), ""]
    lines += ["## 未决问题与资料限制", ""]
    for gap in gaps:
        lines += [f"- {gap.company_id}／{gap.gap_type}：{gap.description}。下一步：{gap.suggested_next_step}。", ""]
    if calculations:
        lines += ["## 计算与来源追踪", "", "| 公司 | 计算名称 | 状态 | 计算ID | 输入ID |", "|---|---|---|---|---|"]
        for company, rows in calculations.items():
            for name, calc in rows.items():
                lines.append(f"| {company} | {name} | {calc.status} | {calc.calculation_id} | {', '.join(calc.input_ids)} |")
        lines.append("")
    if evidence:
        lines += ["## 原文定位", "", "完整原文、金额观察及公式分别保存在 evidence.json、observations.json、calculations.json 中。", ""]
        for e in sorted(evidence, key=lambda e: (e.document_id, e.pdf_page, e.line_start)):
            link = (source_links or {}).get(e.document_id)
            location = f"PDF第{e.pdf_page}页，行{e.line_start}—{e.line_end}"
            source = f"[{location}]({link}#page={e.pdf_page})" if link else location
            lines += [f"- {e.evidence_id}：{e.company_id}／{source}。", ""]
    lines += ["资料仅来自锁定的公司年报，未完成完整更正公告搜索或外部独立验证。检索效果来自开发集，不代表盲测表现。", ""]
    return "\n".join(lines)


def validate_report(report: str, context: ResearchContext, claims: list[ResearchClaim], gaps: list[ResearchGap], ordered_ids: list[str], execution_status: str,
                    **render_inputs) -> dict:
    """Reject any text inserted after review by re-rendering authoritative claims."""
    expected = render_report(context, claims, gaps, ordered_ids, execution_status=execution_status, **render_inputs)
    errors = []
    if report != expected:
        errors.append("报告包含未通过批准对象渲染的改动")
    if any(c.company_id not in context.company_ids for c in claims):
        errors.append("报告Claim公司超出研究范围")
    return {"status": "PASS" if not errors else "FAIL", "errors": errors,
            "limitations": ["此校验绑定已审查对象与最终文本，不声称是通用语义或因果验证"]}
