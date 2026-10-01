"""使用Decimal完成金额单位标准化并构造稳定观察ID。"""

from __future__ import annotations

from decimal import Decimal

from finresearch.contracts import MetricDefinition, MetricObservation, MetricObservationSeed, stable_sha256


def normalize_amount(value: Decimal, raw_unit: str, definition: MetricDefinition) -> Decimal:
    """把允许的原始金额单位精确换算到指标标准单位。"""

    factor = definition.allowed_raw_units.get(raw_unit)
    if factor is None:
        raise ValueError(f"指标{definition.metric_id}不允许原始单位: {raw_unit}")
    return value * factor


def build_observation(seed: MetricObservationSeed, definition: MetricDefinition) -> MetricObservation:
    """核对指标口径并为种子生成标准值、内容指纹和稳定ID。"""

    # model_copy does not revalidate; never let it bypass raw amount binding.
    seed = MetricObservationSeed.model_validate(seed.model_dump())
    problems: list[str] = []
    if seed.metric_id != definition.metric_id:
        problems.append("metric_id与指标定义不一致")
    if seed.period_kind != definition.metric_kind:
        problems.append("FLOW/STOCK类型与指标定义不一致")
    if seed.currency != definition.currency:
        problems.append("币种与指标定义不一致")
    if seed.statement_scope != definition.statement_scope:
        problems.append("报表范围与指标定义不一致")
    if seed.measurement_basis != definition.measurement_basis:
        problems.append("计量基础与指标定义不一致")
    if problems:
        raise ValueError("；".join(problems))
    standard_value = None
    standard_unit = None
    if seed.value_status == "OBSERVED":
        assert seed.raw_value is not None and seed.raw_unit is not None
        standard_value = normalize_amount(seed.raw_value, seed.raw_unit, definition)
        standard_unit = definition.canonical_unit
    seed_payload = seed.model_dump(mode="json")
    content_sha = stable_sha256(seed_payload)
    period_key = (
        f"{seed.period_start.isoformat()}_{seed.period_end.isoformat()}"
        if seed.period_kind == "FLOW"
        else seed.observed_at.isoformat()  # type: ignore[union-attr]
    )
    observation_id = (
        f"obs-{seed.company_id}-{seed.metric_id}-{period_key}-v{seed.observation_version}-{content_sha[:12]}"
    )
    return MetricObservation(
        **seed.model_dump(),
        observation_id=observation_id,
        standard_value=standard_value,
        standard_unit=standard_unit,
        content_sha256=content_sha,
    )
