"""S2受控财务计算；所有异常边界返回显式状态。"""

from __future__ import annotations

from decimal import Decimal
from datetime import date
from typing import Iterable

from finresearch.contracts import CalculationResult, ComparabilityCheck, MetricObservation, stable_sha256


def _check(name: str, passed: bool, expected: object, actual: object) -> ComparabilityCheck:
    return ComparabilityCheck(name=name, passed=passed, expected=str(expected), actual=str(actual))


def _make_result(
    formula_id: str,
    expression: str,
    inputs: Iterable[MetricObservation | CalculationResult],
    status: str,
    value: Decimal | None,
    output_unit: str,
    checks: list[ComparabilityCheck],
    errors: list[str],
) -> CalculationResult:
    input_list = list(inputs)

    def input_value(item: MetricObservation | CalculationResult) -> str | None:
        value = item.value if isinstance(item, CalculationResult) else item.standard_value
        return None if value is None else str(value)

    input_payload = [
        {
            "id": getattr(item, "observation_id", getattr(item, "calculation_id", None)),
            "value": input_value(item),
            "status": item.status if isinstance(item, CalculationResult) else item.value_status,
            "content_sha256": getattr(item, "content_sha256", None),
            "result_sha256": getattr(item, "result_sha256", None),
        }
        for item in input_list
    ]
    input_sha = stable_sha256(input_payload)
    first = input_list[0]
    context = {
        "company_id": first.company_id,
        "currency": first.currency,
        "statement_scope": first.statement_scope,
        "comparison_period": list(first.comparison_period) if isinstance(first, CalculationResult) and first.comparison_period else
            [input_list[1].fiscal_year, first.fiscal_year] if formula_id == "period_growth_v1" else
            [first.fiscal_year, first.fiscal_year] if isinstance(first, MetricObservation) else None,
    }
    result_payload = {
        "formula_id": formula_id,
        "formula_version": "1.1.0",
        **context,
        "expression": expression,
        "input_sha256": input_sha,
        "status": status,
        "value": None if value is None else str(value),
        "output_unit": output_unit,
        "checks": [item.model_dump() for item in checks],
        "errors": errors,
    }
    result_sha = stable_sha256(result_payload)
    return CalculationResult(
        calculation_id=f"calc-{formula_id}-{result_sha[:16]}",
        formula_id=formula_id,
        formula_version="1.1.0",
        **context,
        expression=expression,
        input_ids=[
            getattr(item, "observation_id", getattr(item, "calculation_id", "")) for item in input_list
        ],
        status=status,
        value=value,
        output_unit=output_unit,
        comparability_checks=checks,
        errors=errors,
        input_sha256=input_sha,
        result_sha256=result_sha,
    )


def _missing(observations: list[MetricObservation]) -> bool:
    return any(item.value_status != "OBSERVED" or item.standard_value is None for item in observations)


def _annual(item: MetricObservation) -> bool:
    """The current calculation protocol supports complete calendar fiscal years."""
    if item.period_kind == "STOCK":
        return item.observed_at == date(item.fiscal_year, 12, 31)
    return (item.period_start, item.period_end) == (date(item.fiscal_year, 1, 1), date(item.fiscal_year, 12, 31))


def calculate_growth(current: MetricObservation, previous: MetricObservation) -> CalculationResult:
    """计算相邻期间增长率；负基数不输出常规百分比。"""

    checks = [
        _check("same_company", current.company_id == previous.company_id, previous.company_id, current.company_id),
        _check("same_metric", current.metric_id == previous.metric_id, previous.metric_id, current.metric_id),
        _check("same_period_kind", current.period_kind == previous.period_kind, previous.period_kind, current.period_kind),
        _check("same_currency", current.currency == previous.currency, previous.currency, current.currency),
        _check("same_scope", current.statement_scope == previous.statement_scope, previous.statement_scope, current.statement_scope),
        _check("same_measurement_basis", current.measurement_basis == previous.measurement_basis, previous.measurement_basis, current.measurement_basis),
        _check("consecutive_year", current.fiscal_year == previous.fiscal_year + 1, previous.fiscal_year + 1, current.fiscal_year),
        _check("complete_current_year", _annual(current), True, _annual(current)),
        _check("complete_previous_year", _annual(previous), True, _annual(previous)),
    ]
    inputs = [current, previous]
    if _missing(inputs):
        return _make_result("period_growth_v1", "(current - previous) / abs(previous)", inputs, "MISSING_INPUT", None, "RATIO", checks, ["至少一个输入不是已观察值"])
    if not all(item.passed for item in checks):
        return _make_result("period_growth_v1", "(current - previous) / abs(previous)", inputs, "INCOMPARABLE", None, "RATIO", checks, ["增长率输入口径不可比"])
    assert current.standard_value is not None and previous.standard_value is not None
    if previous.standard_value == 0:
        return _make_result("period_growth_v1", "(current - previous) / abs(previous)", inputs, "ZERO_DENOMINATOR", None, "RATIO", checks, ["前期值为0，不能计算常规增长率"])
    if previous.standard_value < 0:
        return _make_result("period_growth_v1", "(current - previous) / abs(previous)", inputs, "NEGATIVE_BASE", None, "RATIO", checks, ["前期值为负，常规增长率不具备直观可比性"])
    value = (current.standard_value - previous.standard_value) / abs(previous.standard_value)
    return _make_result("period_growth_v1", "(current - previous) / abs(previous)", inputs, "VALID", value, "RATIO", checks, [])


def calculate_flow_ratio(numerator: MetricObservation, denominator: MetricObservation) -> CalculationResult:
    """计算同公司、同期间、同口径的两个流量指标之比。"""

    checks = [
        _check("numerator_is_flow", numerator.period_kind == "FLOW", "FLOW", numerator.period_kind),
        _check("denominator_is_flow", denominator.period_kind == "FLOW", "FLOW", denominator.period_kind),
        _check("same_company", numerator.company_id == denominator.company_id, denominator.company_id, numerator.company_id),
        _check("same_period", (numerator.period_start, numerator.period_end) == (denominator.period_start, denominator.period_end), f"{denominator.period_start}/{denominator.period_end}", f"{numerator.period_start}/{numerator.period_end}"),
        _check("same_currency", numerator.currency == denominator.currency, denominator.currency, numerator.currency),
        _check("same_scope", numerator.statement_scope == denominator.statement_scope, denominator.statement_scope, numerator.statement_scope),
        _check("complete_years", _annual(numerator) and _annual(denominator), True, _annual(numerator) and _annual(denominator)),
    ]
    inputs = [numerator, denominator]
    if _missing(inputs):
        return _make_result("flow_to_flow_ratio_v1", "numerator / denominator", inputs, "MISSING_INPUT", None, "RATIO", checks, ["至少一个输入不是已观察值"])
    if not all(item.passed for item in checks):
        return _make_result("flow_to_flow_ratio_v1", "numerator / denominator", inputs, "INCOMPARABLE", None, "RATIO", checks, ["流量比率输入口径不可比"])
    assert numerator.standard_value is not None and denominator.standard_value is not None
    if denominator.standard_value == 0:
        return _make_result("flow_to_flow_ratio_v1", "numerator / denominator", inputs, "ZERO_DENOMINATOR", None, "RATIO", checks, ["分母为0"])
    return _make_result("flow_to_flow_ratio_v1", "numerator / denominator", inputs, "VALID", numerator.standard_value / denominator.standard_value, "RATIO", checks, [])


def calculate_stock_to_flow(stock: MetricObservation, flow: MetricObservation) -> CalculationResult:
    """计算期末存量/同期流量；该结果不是周转率。"""

    checks = [
        _check("stock_kind", stock.period_kind == "STOCK", "STOCK", stock.period_kind),
        _check("flow_kind", flow.period_kind == "FLOW", "FLOW", flow.period_kind),
        _check("same_company", stock.company_id == flow.company_id, flow.company_id, stock.company_id),
        _check("same_fiscal_year", stock.fiscal_year == flow.fiscal_year, flow.fiscal_year, stock.fiscal_year),
        _check("stock_at_flow_end", stock.observed_at == flow.period_end, flow.period_end, stock.observed_at),
        _check("same_currency", stock.currency == flow.currency, flow.currency, stock.currency),
        _check("same_scope", stock.statement_scope == flow.statement_scope, flow.statement_scope, stock.statement_scope),
        _check("complete_years", _annual(stock) and _annual(flow), True, _annual(stock) and _annual(flow)),
    ]
    inputs = [stock, flow]
    if _missing(inputs):
        return _make_result("stock_to_flow_ratio_v1", "ending_stock / period_flow", inputs, "MISSING_INPUT", None, "RATIO", checks, ["至少一个输入不是已观察值"])
    if not all(item.passed for item in checks):
        return _make_result("stock_to_flow_ratio_v1", "ending_stock / period_flow", inputs, "INCOMPARABLE", None, "RATIO", checks, ["存量/流量输入口径不可比"])
    assert stock.standard_value is not None and flow.standard_value is not None
    if flow.standard_value == 0:
        return _make_result("stock_to_flow_ratio_v1", "ending_stock / period_flow", inputs, "ZERO_DENOMINATOR", None, "RATIO", checks, ["期间流量为0"])
    return _make_result("stock_to_flow_ratio_v1", "ending_stock / period_flow", inputs, "VALID", stock.standard_value / flow.standard_value, "RATIO", checks, [])


def calculate_growth_gap(growth_a: CalculationResult, growth_b: CalculationResult) -> CalculationResult:
    """计算两个有效增长率之差；十进制比率差等于百分比点/100。"""

    checks = [
        _check("growth_a_valid", growth_a.status == "VALID", "VALID", growth_a.status),
        _check("growth_b_valid", growth_b.status == "VALID", "VALID", growth_b.status),
        _check("growth_a_ratio", growth_a.output_unit == "RATIO", "RATIO", growth_a.output_unit),
        _check("growth_b_ratio", growth_b.output_unit == "RATIO", "RATIO", growth_b.output_unit),
        _check("growth_formulas", growth_a.formula_id == growth_b.formula_id == "period_growth_v1", "period_growth_v1", f"{growth_a.formula_id}/{growth_b.formula_id}"),
        _check("same_company", growth_a.company_id is not None and growth_a.company_id == growth_b.company_id, growth_a.company_id, growth_b.company_id),
        _check("same_comparison_period", growth_a.comparison_period is not None and growth_a.comparison_period == growth_b.comparison_period, growth_a.comparison_period, growth_b.comparison_period),
        _check("same_currency", growth_a.currency is not None and growth_a.currency == growth_b.currency, growth_a.currency, growth_b.currency),
        _check("same_scope", growth_a.statement_scope is not None and growth_a.statement_scope == growth_b.statement_scope, growth_a.statement_scope, growth_b.statement_scope),
    ]
    inputs = [growth_a, growth_b]
    if not all(item.passed for item in checks) or growth_a.value is None or growth_b.value is None:
        return _make_result("growth_gap_pp_v1", "(growth_a - growth_b) * 100", inputs, "INCOMPARABLE", None, "PERCENTAGE_POINT", checks, ["两个增长结果必须均为同公司同期间的有效增长率"])
    # 输出单位为百分点，因此把比率差乘以100。
    value = (growth_a.value - growth_b.value) * Decimal("100")
    return _make_result("growth_gap_pp_v1", "(growth_a - growth_b) * 100", inputs, "VALID", value, "PERCENTAGE_POINT", checks, [])
