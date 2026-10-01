from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest
from pydantic import ValidationError

from finresearch.contracts import (
    ResearchRequest,
    ScopeViolation,
    select_available_documents,
    validate_request_against_scope,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def load_scope() -> dict:
    return json.loads((PROJECT_ROOT / "configs/s0/scope.json").read_text(encoding="utf-8"))


def load_manifest() -> list[dict]:
    path = PROJECT_ROOT / "storage/s0/document_manifest.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def make_request(as_of_date: str = "2025-04-25", company_id: str = "002371.SZ") -> ResearchRequest:
    return ResearchRequest.model_validate(
        {
            "request_id": "test-request",
            "topic_id": "revenue_quality_v0",
            "company_ids": [company_id],
            "reporting_years": [2024],
            "as_of_date": as_of_date,
            "cutoff_policy": "ASIA_SHANGHAI_END_OF_DAY",
            "timezone": "Asia/Shanghai",
            "document_snapshot_id": "s0-naura-2024-v1",
            "question": "2024年度营业收入是多少？",
            "output_language": "zh-CN",
            "budget_profile_id": "s0-local-only",
        }
    )


def test_valid_request_roundtrip_and_timestamp() -> None:
    request = make_request()
    recovered = ResearchRequest.model_validate_json(request.model_dump_json())
    assert recovered == request
    assert request.as_of_timestamp == pd.Timestamp("2025-04-25 23:59:59.999999999", tz="Asia/Shanghai")


@pytest.mark.parametrize(
    ("as_of_date", "expected_count"),
    [("2025-04-24", 0), ("2025-04-25", 1), ("2025-04-26", 1)],
)
def test_date_boundary_uses_published_date(as_of_date: str, expected_count: int) -> None:
    assert len(
        select_available_documents(
            make_request(as_of_date), load_manifest(), {"cninfo-002371-2024-ar-1223309278"}
        )
    ) == expected_count


def test_unknown_published_date_is_not_available() -> None:
    manifest = load_manifest()
    manifest[0]["published_on"] = None
    manifest[0]["time_precision"] = "UNKNOWN"
    manifest[0]["date_status"] = "UNKNOWN"
    manifest[0]["date_evidence_ref"] = None
    assert select_available_documents(
        make_request(), manifest, {"cninfo-002371-2024-ar-1223309278"}
    ) == []


def test_snapshot_rejects_document_not_listed_in_scope() -> None:
    manifest = load_manifest()
    manifest[0]["document_id"] = "unregistered-document-with-same-snapshot-id"
    assert select_available_documents(
        make_request(), manifest, {"cninfo-002371-2024-ar-1223309278"}
    ) == []


def test_ambiguous_company_name_is_rejected() -> None:
    with pytest.raises(ValidationError, match="交易所后缀"):
        make_request(company_id="北方华创")


def test_out_of_scope_company_is_rejected() -> None:
    with pytest.raises(ScopeViolation, match="先行范围"):
        validate_request_against_scope(make_request(company_id="688082.SH"), load_scope())


def test_extra_field_is_rejected() -> None:
    payload = make_request().model_dump(mode="json")
    payload["invented_scope"] = "should fail"
    with pytest.raises(ValidationError):
        ResearchRequest.model_validate(payload)
