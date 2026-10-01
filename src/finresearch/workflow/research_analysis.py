"""Deterministic S4 financial analysis, without LLM-authored numbers."""
from __future__ import annotations
from decimal import Decimal
from finresearch.contracts import MetricObservation, CalculationResult
from finresearch.contracts.research import ResearchContext, Phenomenon, ResearchClaim, ResearchGap
from finresearch.finance.calculations import calculate_growth, calculate_flow_ratio, calculate_stock_to_flow, calculate_growth_gap


def calculate_research(context: ResearchContext, observations: list[MetricObservation]):
    """Eight registered annual calculations per fully covered company.

    Inputs must match the run companies, years and disclosure cutoff. Conflicting
    values cannot be silently resolved; incomplete coverage returns a gap.
    """
    calculations: dict[str, dict[str, CalculationResult]] = {}
    gaps = []
    lookup = {}
    for item in observations:
        if item.company_id not in context.company_ids or item.fiscal_year not in context.fiscal_years or item.document_published_on > context.as_of_date:
            raise ValueError("观察值超出研究范围或截止日")
        key = (item.company_id, item.fiscal_year, item.metric_id)
        if key in lookup and lookup[key] != item:
            raise ValueError("研究观察存在未解决的来源冲突")
        lookup[key] = item
    previous, current = context.fiscal_years
    for company in context.company_ids:
        required = [(company, y, metric) for y in context.fiscal_years for metric in ("revenue", "operating_cash_flow_net", "accounts_receivable")]
        missing = [key for key in required if key not in lookup]
        if missing:
            gaps.append(ResearchGap(gap_id=f"gap-{company}-metrics", company_id=company, gap_type="MISSING_METRIC",
                description=f"缺少{[(k[1], k[2]) for k in missing]}", suggested_next_step="查询指标或核对报表；不得填零"))
            calculations[company] = {}
            continue
        def obs(metric, year):
            return lookup[(company, year, metric)]
        rows = {f"{m}_growth": calculate_growth(obs(m, current), obs(m, previous)) for m in ("revenue", "operating_cash_flow_net", "accounts_receivable")}
        for y in context.fiscal_years:
            rows[f"ocf_revenue_ratio_{y}"] = calculate_flow_ratio(obs("operating_cash_flow_net", y), obs("revenue", y))
            rows[f"ar_revenue_ratio_{y}"] = calculate_stock_to_flow(obs("accounts_receivable", y), obs("revenue", y))
        rows["revenue_ocf_growth_gap_pp"] = calculate_growth_gap(rows["revenue_growth"], rows["operating_cash_flow_net_growth"])
        calculations[company] = rows
        for name, value in rows.items():
            if value.status != "VALID":
                gaps.append(ResearchGap(gap_id=f"gap-{company}-{name}", company_id=company, gap_type="INCOMPARABLE_CALCULATION",
                    description=f"{name}: {value.status}，不输出常规数值", affected_ids=[value.calculation_id],
                    suggested_next_step="保留该边界；负基数应使用原始金额与比率描述，不能强算常规增长率"))
    return calculations, gaps


def detect_phenomena(calculations: dict[str, dict[str, CalculationResult]], years: tuple[int, int]) -> list[Phenomenon]:
    """Fixed sign/direction rules over VALID results; no arbitrary risk score."""
    results = []
    for company, rows in calculations.items():
        def emit(kind, names, condition, description):
            values = [rows.get(n) for n in names]
            if all(v is not None and v.status == "VALID" and v.value is not None for v in values) and condition([v.value for v in values]):
                results.append(Phenomenon(phenomenon_id=f"phen-{company}-{kind}", company_id=company, kind=kind,
                    calculation_ids=[v.calculation_id for v in values], description=description))
        emit("REVENUE_GROWTH", ["revenue_growth"], lambda v: v[0] > 0, "营业收入同比增长")
        emit("CASH_REVENUE_DIVERGENCE", ["revenue_growth", "operating_cash_flow_net_growth"], lambda v: v[0] > 0 and v[1] < 0, "收入增长与经营现金流净额同比下降方向不同")
        emit("RECEIVABLE_GROWTH_EXCEEDS_REVENUE", ["accounts_receivable_growth", "revenue_growth"], lambda v: v[0] > v[1], "应收账款账面价值增速高于收入增速")
        emit("NEGATIVE_OCF_RATIO", [f"ocf_revenue_ratio_{years[1]}"], lambda v: v[0] < 0, "本期经营现金流净额/收入为负")
        emit("OCF_RATIO_DECLINE", [f"ocf_revenue_ratio_{years[0]}", f"ocf_revenue_ratio_{years[1]}"], lambda v: v[1] < v[0], "经营现金流净额/收入较上期下降")
        emit("RECEIVABLE_RATIO_INCREASE", [f"ar_revenue_ratio_{years[0]}", f"ar_revenue_ratio_{years[1]}"], lambda v: v[1] > v[0], "期末应收账款/年度收入较上期增加")
    return results


def calculation_claims(calculations: dict[str, dict[str, CalculationResult]]) -> list[ResearchClaim]:
    """Render validated calculations; every number comes from the formula result."""
    labels = {"revenue_growth": "营业收入增长率", "operating_cash_flow_net_growth": "经营现金流净额增长率", "accounts_receivable_growth": "应收账款账面价值增长率",
              "revenue_ocf_growth_gap_pp": "收入增长率与经营现金流增长率之差"}
    results = []
    for company, rows in calculations.items():
        for name, calc in rows.items():
            if calc.status != "VALID":
                continue
            label = labels.get(name)
            if label is None:
                label = ("经营现金流净额/年度营业收入" if name.startswith("ocf_") else "期末应收账款账面价值/年度营业收入") + f"（{name.rsplit('_', 1)[-1]}年）"
            amount = calc.value * 100 if calc.output_unit == "RATIO" else calc.value
            unit = "%" if calc.output_unit == "RATIO" else "个百分点"
            value_text = f"{amount.quantize(Decimal('0.01'))}{unit}"
            limitations = ["比率不等于因果解释或风险评级"]
            if name.startswith("ar_"):
                limitations.append("期末存量/期间流量，不是应收周转率或周转天数")
            results.append(ResearchClaim(claim_id=f"claim-{calc.calculation_id}", company_id=company, kind="COMPUTED",
                text=f"{label}为{value_text}。", calculation_ids=[calc.calculation_id], status="APPROVED", limitations=limitations))
    return results
