"""生成S2公式、单位、存储和PIT边界的机器验收证据。"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from pydantic import ValidationError  # noqa: E402

from finresearch.contracts import MetricDefinition, MetricObservationSeed  # noqa: E402
from finresearch.finance import (  # noqa: E402
    build_observation,
    calculate_flow_ratio,
    calculate_growth,
    calculate_stock_to_flow,
    normalize_amount,
)
from finresearch.storage import DuplicateMetricRecordError, MetricStore  # noqa: E402


def definition(metric: str = "revenue", kind: str = "FLOW", currency: str = "CNY") -> MetricDefinition:
    return MetricDefinition.model_validate(
        {
            "metric_id": metric, "name_zh": "边界指标", "definition": "用于S2边界验收的合成指标",
            "metric_kind": kind, "currency": currency, "canonical_unit": f"{currency}_YUAN",
            "allowed_raw_units": {f"{currency}_YUAN": "1", f"{currency}_TEN_THOUSAND_YUAN": "10000", f"{currency}_HUNDRED_MILLION_YUAN": "100000000"},
            "statement_scope": "CONSOLIDATED", "measurement_basis": "REPORTED_AMOUNT",
            "allowed_formula_ids": ["period_growth_v1"], "prohibited_equivalences": [],
            "version": "1.0.0", "status": "ACTIVE",
        }
    )


def observation(
    value: str | None,
    year: int,
    *,
    metric: str = "revenue",
    kind: str = "FLOW",
    published_on: str = "2025-01-01",
    version: int = 1,
    supersedes: str | None = None,
    document_id: str = "synthetic-v1",
    status: str = "OBSERVED",
):
    is_flow = kind == "FLOW"
    seed = MetricObservationSeed.model_validate(
        {
            "metric_id": metric, "company_id": "002371.SZ", "fiscal_year": year, "period_kind": kind,
            "period_start": f"{year}-01-01" if is_flow else None, "period_end": f"{year}-12-31" if is_flow else None,
            "observed_at": None if is_flow else f"{year}-12-31", "raw_value_text": value, "raw_value": value,
            "raw_unit": "CNY_YUAN" if value is not None else None, "currency": "CNY", "statement_scope": "CONSOLIDATED",
            "measurement_basis": "REPORTED_AMOUNT", "value_status": status, "document_id": document_id,
            "document_sha256": ("a" if version == 1 else "b") * 64, "document_published_on": published_on,
            "evidence_refs": [{"evidence_id": f"synthetic-evidence-{version}", "pdf_page": 1, "source_label": "合成边界"}] if value is not None else [],
            "extraction_method": "TABLE_EXTRACTED", "review_status": "DRAFT", "review_note": "只用于机制测试，不代表真实更正公告",
            "observation_version": version, "supersedes_observation_id": supersedes,
        }
    )
    return build_observation(seed, definition(metric, kind))


def result(name: str, passed: bool, actual: Any) -> dict[str, Any]:
    return {"name": name, "passed": passed, "actual": actual}


def collect_boundary_checks() -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []

    unit_definition = definition()
    checks.append(result("ten_thousand_yuan_exact", normalize_amount(Decimal("1.2345"), "CNY_TEN_THOUSAND_YUAN", unit_definition) == Decimal("12345.0000"), "1.2345万元=12345元"))
    checks.append(result("hundred_million_yuan_exact", normalize_amount(Decimal("1.23456789"), "CNY_HUNDRED_MILLION_YUAN", unit_definition) == Decimal("123456789.00000000"), "1.23456789亿元=123456789元"))
    try:
        normalize_amount(Decimal("1"), "USD", unit_definition)
        unknown_unit = False
    except ValueError:
        unknown_unit = True
    checks.append(result("unknown_unit_rejected", unknown_unit, "USD"))

    positive = calculate_growth(observation("120", 2024), observation("100", 2023))
    decline = calculate_growth(observation("80", 2024), observation("100", 2023))
    zero = calculate_growth(observation("10", 2024), observation("0", 2023))
    negative = calculate_growth(observation("10", 2024), observation("-2", 2023))
    missing = calculate_growth(observation("10", 2024), observation(None, 2023, status="MISSING"))
    checks.extend(
        [
            result("positive_growth", positive.status == "VALID" and positive.value == Decimal("0.2"), positive.model_dump(mode="json")),
            result("decline_growth", decline.status == "VALID" and decline.value == Decimal("-0.2"), decline.model_dump(mode="json")),
            result("zero_denominator", zero.status == "ZERO_DENOMINATOR" and zero.value is None, zero.model_dump(mode="json")),
            result("negative_base", negative.status == "NEGATIVE_BASE" and negative.value is None, negative.model_dump(mode="json")),
            result("missing_input", missing.status == "MISSING_INPUT" and missing.value is None, missing.model_dump(mode="json")),
        ]
    )
    numerator = observation("20", 2024, metric="ocf")
    denominator = observation("100", 2024)
    currency_conflict = calculate_flow_ratio(numerator.model_copy(update={"currency": "USD"}), denominator)
    scope_conflict = calculate_flow_ratio(numerator.model_copy(update={"statement_scope": "PARENT_COMPANY"}), denominator)
    period_conflict = calculate_flow_ratio(numerator, observation("100", 2023))
    stock_mismatch = calculate_stock_to_flow(observation("25", 2023, metric="ar", kind="STOCK"), denominator)
    checks.extend(
        [
            result("currency_conflict", currency_conflict.status == "INCOMPARABLE" and currency_conflict.value is None, currency_conflict.model_dump(mode="json")),
            result("scope_conflict", scope_conflict.status == "INCOMPARABLE" and scope_conflict.value is None, scope_conflict.model_dump(mode="json")),
            result("period_conflict", period_conflict.status == "INCOMPARABLE" and period_conflict.value is None, period_conflict.model_dump(mode="json")),
            result("stock_flow_period_conflict", stock_mismatch.status == "INCOMPARABLE" and stock_mismatch.value is None, stock_mismatch.model_dump(mode="json")),
        ]
    )
    try:
        MetricObservationSeed.model_validate(
            {**observation("0", 2024).model_dump(exclude={"observation_id", "standard_value", "standard_unit", "content_sha256"}), "value_status": "MISSING"}
        )
        missing_zero_rejected = False
    except ValidationError:
        missing_zero_rejected = True
    checks.append(result("missing_not_zero", missing_zero_rejected, "MISSING with raw 0 rejected"))

    with TemporaryDirectory() as temporary:
        database_path = Path(temporary) / "boundary.sqlite3"
        store = MetricStore(database_path)
        duplicate = observation("100", 2024)
        try:
            store.save_observations([duplicate, duplicate])
            atomic_rollback = False
        except DuplicateMetricRecordError:
            atomic_rollback = store.counts()["metric_observations"] == 0
        old = observation("100", 2024, published_on="2025-01-01")
        new = observation("110", 2024, published_on="2025-02-01", version=2, supersedes=old.observation_id, document_id="synthetic-v2")
        store.save_observations([old, new])
        allowed = {old.document_id, new.document_id}
        before = store.query_observation_as_of("002371.SZ", "revenue", 2024, date(2024, 12, 31), allowed_document_ids=allowed)
        between = store.query_observation_as_of("002371.SZ", "revenue", 2024, date(2025, 1, 15), allowed_document_ids=allowed)
        after = store.query_observation_as_of("002371.SZ", "revenue", 2024, date(2025, 2, 15), allowed_document_ids=allowed)
        pit_passed = before is None and between is not None and after is not None and between.standard_value == Decimal("100") and after.standard_value == Decimal("110")
    checks.append(result("atomic_batch_rollback", atomic_rollback, "duplicate batch left zero rows"))
    checks.append(result("synthetic_correction_pit", pit_passed, {"before": None, "between": "100", "after": "110", "limitation": "synthetic version chain only"}))

    return checks


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--attempt-id", required=True)
    args = parser.parse_args()
    output_dir = PROJECT_ROOT / "runs/s2" / args.attempt_id
    output_dir.mkdir(parents=True, exist_ok=False)
    checks = collect_boundary_checks()

    payload = {"attempt_id": args.attempt_id, "all_passed": all(item["passed"] for item in checks), "checks": checks}
    (output_dir / "boundary_checks.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"attempt_id": args.attempt_id, "check_count": len(checks), "all_passed": payload["all_passed"]}, ensure_ascii=False))
    return 0 if payload["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
