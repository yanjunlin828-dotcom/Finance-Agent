from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

from finresearch.contracts import DocumentManifestRecord

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def load_record() -> dict:
    line = (PROJECT_ROOT / "storage/s0/document_manifest.jsonl").read_text(encoding="utf-8").strip()
    return json.loads(line)


def test_manifest_record_is_valid_and_keeps_three_time_semantics() -> None:
    record = DocumentManifestRecord.model_validate(load_record())
    assert str(record.published_on) == "2025-04-25"
    assert record.reporting_period == "2024"
    assert record.retrieved_at.isoformat().startswith("2026-09-22T12:55:23")


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("sha256", "abc", "64"),
        ("local_path", "../outside.pdf", "安全相对路径"),
        ("source_url", "http://example.com/report.pdf", "https"),
        ("ts_code", "002371", "交易所后缀"),
    ],
)
def test_manifest_rejects_unsafe_or_ambiguous_identity(field: str, value: str, message: str) -> None:
    payload = deepcopy(load_record())
    payload[field] = value
    with pytest.raises(ValidationError, match=message):
        DocumentManifestRecord.model_validate(payload)


def test_unknown_publication_date_cannot_look_precise() -> None:
    payload = deepcopy(load_record())
    payload["published_on"] = None
    with pytest.raises(ValidationError, match="UNKNOWN"):
        DocumentManifestRecord.model_validate(payload)
