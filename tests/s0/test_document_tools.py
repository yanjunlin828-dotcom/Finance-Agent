from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from finresearch.tools import DocumentIntegrityError, RegisteredDocumentTools, UnknownDocumentError

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def load_manifest() -> list[dict]:
    path = PROJECT_ROOT / "storage/s0/document_manifest.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_registered_page_count_tool_verifies_identity() -> None:
    tools = RegisteredDocumentTools(PROJECT_ROOT, load_manifest())
    result = tools.get_document_page_count("cninfo-002371-2024-ar-1223309278")
    assert result == {"document_id": "cninfo-002371-2024-ar-1223309278", "page_count": 190}


def test_registered_page_count_tool_rejects_unknown_id() -> None:
    tools = RegisteredDocumentTools(PROJECT_ROOT, load_manifest())
    with pytest.raises(UnknownDocumentError):
        tools.get_document_page_count("unknown-document")


def test_registered_page_count_tool_rejects_tampered_file(tmp_path: Path) -> None:
    record = deepcopy(load_manifest()[0])
    (tmp_path / "tampered.pdf").write_bytes(b"%PDF-tampered")
    record["local_path"] = "tampered.pdf"
    tools = RegisteredDocumentTools(tmp_path, [record])
    with pytest.raises(DocumentIntegrityError, match="SHA-256"):
        tools.get_document_page_count(record["document_id"])
