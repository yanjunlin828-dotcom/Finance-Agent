"""把六条真实观察写入SQLite，执行确定性计算并导出可复算比较表。"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from finresearch.contracts import CalculationResult, MetricObservation  # noqa: E402
from finresearch.finance import (  # noqa: E402
    build_definition_index,
    calculate_flow_ratio,
    calculate_growth,
    calculate_growth_gap,
    calculate_stock_to_flow,
)
from finresearch.finance.s2_protocol import calculate_s2_comparison
from finresearch.verification.financial_artifacts import validate_observation_artifacts, s2_source_files
from finresearch.storage import MetricStore  # noqa: E402

TIMEZONE = ZoneInfo("Asia/Shanghai")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_observations(path: Path) -> list[MetricObservation]:
    return [MetricObservation.model_validate_json(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


def percent_text(value: Decimal | None, *, unit: str = "RATIO") -> str:
    if value is None:
        return "N/A"
    shown = value if unit == "PERCENTAGE_POINT" else value * Decimal("100")
    suffix = "个百分点" if unit == "PERCENTAGE_POINT" else "%"
    return f"{shown.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)}{suffix}"


def amount_text(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):,.2f}元"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--observation-attempt", default="s2-observations-20260922-02")
    args = parser.parse_args()
    output_dir = PROJECT_ROOT / "runs/s2" / args.attempt_id
    output_dir.mkdir(parents=True, exist_ok=False)
    observations_path = PROJECT_ROOT / "runs/s2" / args.observation_attempt / "observations.jsonl"
    dictionary_path = PROJECT_ROOT / "configs/s2/metric_dictionary.json"
    correction_path = PROJECT_ROOT / "storage/s2/manual_corrections.json"
    definitions = build_definition_index(read_json(dictionary_path))
    observations = validate_observation_artifacts(PROJECT_ROOT, observations_path)
    if len(observations) != 6:
        raise ValueError("S2首版必须恰好读取六条已验证观察")

    database_path = output_dir / "metric_store.sqlite3"
    store = MetricStore(database_path)
    store.save_definitions(definitions.values())
    store.save_observations(observations)
    as_of = date(2025, 4, 25)
    # Lock queries to the same registered source as the S2 observation builder.
    manifest_path = PROJECT_ROOT / "storage/s0/document_manifest.jsonl"
    allowed_ids = {json.loads(line)["document_id"] for line in manifest_path.read_text(encoding="utf-8").splitlines() if line.strip()}
    selected: dict[tuple[str, int], MetricObservation] = {}
    for metric_id in definitions:
        for year in (2023, 2024):
            item = store.query_observation_as_of("002371.SZ", metric_id, year, as_of, allowed_document_ids=allowed_ids)
            if item is None:
                raise ValueError(f"截止日缺少观察: {metric_id}/{year}")
            selected[(metric_id, year)] = item

    calculations = calculate_s2_comparison(selected)
    if any(item.status != "VALID" for item in calculations.values()):
        raise ValueError("真实比较表存在非VALID计算，停止导出")
    store.save_calculations(calculations.values())

    comparison_rows = [
        {
            "item_id": "revenue", "name": "营业收入", "2023": decimal_text(selected[("revenue", 2023)].standard_value),
            "2024": decimal_text(selected[("revenue", 2024)].standard_value), "change": decimal_text(calculations["revenue_growth_2024"].value),
            "display_change": percent_text(calculations["revenue_growth_2024"].value), "formula_id": calculations["revenue_growth_2024"].formula_id,
            "input_ids": calculations["revenue_growth_2024"].input_ids, "unit": "CNY_YUAN",
        },
        {
            "item_id": "operating_cash_flow_net", "name": "经营活动产生的现金流量净额",
            "2023": decimal_text(selected[("operating_cash_flow_net", 2023)].standard_value),
            "2024": decimal_text(selected[("operating_cash_flow_net", 2024)].standard_value),
            "change": decimal_text(calculations["ocf_growth_2024"].value), "display_change": percent_text(calculations["ocf_growth_2024"].value),
            "formula_id": calculations["ocf_growth_2024"].formula_id, "input_ids": calculations["ocf_growth_2024"].input_ids, "unit": "CNY_YUAN",
        },
        {
            "item_id": "accounts_receivable", "name": "应收账款列示账面价值",
            "2023": decimal_text(selected[("accounts_receivable", 2023)].standard_value),
            "2024": decimal_text(selected[("accounts_receivable", 2024)].standard_value),
            "change": decimal_text(calculations["ar_growth_2024"].value), "display_change": percent_text(calculations["ar_growth_2024"].value),
            "formula_id": calculations["ar_growth_2024"].formula_id, "input_ids": calculations["ar_growth_2024"].input_ids, "unit": "CNY_YUAN",
        },
        {
            "item_id": "ocf_to_revenue", "name": "经营现金流净额/营业收入",
            "2023": decimal_text(calculations["ocf_to_revenue_2023"].value), "2024": decimal_text(calculations["ocf_to_revenue_2024"].value),
            "change": None, "display_change": "描述性比率，不作风险评级", "formula_id": "flow_to_flow_ratio_v1",
            "input_ids": sorted(set(calculations["ocf_to_revenue_2023"].input_ids + calculations["ocf_to_revenue_2024"].input_ids)), "unit": "RATIO",
        },
        {
            "item_id": "ar_to_revenue", "name": "期末应收账款/年度营业收入",
            "2023": decimal_text(calculations["ar_to_revenue_2023"].value), "2024": decimal_text(calculations["ar_to_revenue_2024"].value),
            "change": None, "display_change": "不是应收账款周转率", "formula_id": "stock_to_flow_ratio_v1",
            "input_ids": sorted(set(calculations["ar_to_revenue_2023"].input_ids + calculations["ar_to_revenue_2024"].input_ids)), "unit": "RATIO",
        },
    ]
    payload = {
        "schema_version": "1.0.0",
        "attempt_id": args.attempt_id,
        "company_id": "002371.SZ",
        "as_of_date": as_of.isoformat(),
        "scope_limit": "ONE_COMPANY_ONE_ANNUAL_REPORT_TWO_COMPARATIVE_PERIODS",
        "observations": [item.model_dump(mode="json") for item in observations],
        "calculations": {key: value.model_dump(mode="json") for key, value in calculations.items()},
        "comparison_rows": comparison_rows,
        "descriptive_flags": [
            {
                "name": "revenue_ocf_growth_divergence",
                "value_percentage_points": decimal_text(calculations["revenue_minus_ocf_growth_gap"].value),
                "meaning": "收入增长率与经营现金流净额增长率的算术差，仅用于标记需要解释的现象，不构成风险评级。",
            }
        ],
    }
    (output_dir / "comparison.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with (output_dir / "comparison_table.csv").open("w", encoding="utf-8-sig", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=["item_id", "name", "2023", "2024", "change", "display_change", "formula_id", "input_ids", "unit"])
        writer.writeheader()
        for item in comparison_rows:
            writer.writerow({**item, "input_ids": "|".join(item["input_ids"])})

    revenue_2023 = selected[("revenue", 2023)].standard_value
    revenue_2024 = selected[("revenue", 2024)].standard_value
    ocf_2023 = selected[("operating_cash_flow_net", 2023)].standard_value
    ocf_2024 = selected[("operating_cash_flow_net", 2024)].standard_value
    ar_2023 = selected[("accounts_receivable", 2023)].standard_value
    ar_2024 = selected[("accounts_receivable", 2024)].standard_value
    assert all(item is not None for item in [revenue_2023, revenue_2024, ocf_2023, ocf_2024, ar_2023, ar_2024])
    markdown = f"""# 北方华创2023—2024财务事实比较（S2窄范围）

截止日：2025-04-25。数据来自同一份2024年年度报告的合并报表；2023数据是比较列，当前并未声明已覆盖2023年原始年报版本。

| 项目 | 2023 | 2024 | 变化 |
|---|---:|---:|---:|
| 营业收入 | {amount_text(revenue_2023)} | {amount_text(revenue_2024)} | {percent_text(calculations['revenue_growth_2024'].value)} |
| 经营活动产生的现金流量净额 | {amount_text(ocf_2023)} | {amount_text(ocf_2024)} | {percent_text(calculations['ocf_growth_2024'].value)} |
| 应收账款列示账面价值 | {amount_text(ar_2023)} | {amount_text(ar_2024)} | {percent_text(calculations['ar_growth_2024'].value)} |
| 经营现金流净额/营业收入 | {percent_text(calculations['ocf_to_revenue_2023'].value)} | {percent_text(calculations['ocf_to_revenue_2024'].value)} | 描述性比率 |
| 期末应收账款/年度营业收入 | {percent_text(calculations['ar_to_revenue_2023'].value)} | {percent_text(calculations['ar_to_revenue_2024'].value)} | 不是周转率 |

收入增长率与经营现金流净额增长率相差 **{percent_text(calculations['revenue_minus_ocf_growth_gap'].value, unit='PERCENTAGE_POINT')}**。这只标记收入与现金流变化不同步的现象；原因分析必须回到披露和后续证据，不能从该差值直接推出经营风险或投资结论。

每个计算的完整精度、公式ID、输入观察ID、证据ID和校验状态保存在`comparison.json`。
"""
    (output_dir / "comparison.md").write_text(markdown, encoding="utf-8")

    reopened = MetricStore(database_path)
    reloaded_selected = {
        (metric_id, year): reopened.query_observation_as_of("002371.SZ", metric_id, year, as_of, allowed_document_ids=allowed_ids)
        for metric_id in definitions
        for year in (2023, 2024)
    }
    if any(item is None for item in reloaded_selected.values()):
        raise ValueError("数据库重开后缺少PIT观察")
    replay_calculations = calculate_s2_comparison(reloaded_selected)
    replay = {
        "database_counts": reopened.counts(),
        "observation_payloads_reloaded": len(reopened.load_all_observations()) == 6,
        "calculation_payloads_reloaded": len(reopened.load_all_calculations()) == len(calculations),
        "selected_result_hashes_match": all(
            replay_calculations[key].result_sha256 == calculations[key].result_sha256 for key in replay_calculations
        ),
    }
    replay["passed"] = all(value for key, value in replay.items() if key != "database_counts")
    (output_dir / "replay.json").write_text(json.dumps(replay, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output_dir / "inputs.lock.json").write_text(
        json.dumps(
            {
                "attempt_id": args.attempt_id,
                "created_at": datetime.now(TIMEZONE).isoformat(),
                "observation_attempt": args.observation_attempt,
                "files": [
                    {"path": str(path.relative_to(PROJECT_ROOT)).replace("\\", "/"), "sha256": file_hash(path)}
                    for path in [observations_path, observations_path.parent / "inputs.lock.json", dictionary_path, correction_path, manifest_path,
                                 *s2_source_files(PROJECT_ROOT), Path(__file__)]
                ],
            },
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )
    summary = {
        "attempt_id": args.attempt_id,
        "observation_count": len(observations),
        "calculation_count": len(calculations),
        "all_calculations_valid": all(item.status == "VALID" for item in calculations.values()),
        "replay_passed": replay["passed"],
        "database_counts": reopened.counts(),
    }
    (output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if summary["all_calculations_valid"] and summary["replay_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
