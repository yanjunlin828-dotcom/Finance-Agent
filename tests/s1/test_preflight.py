from __future__ import annotations

import json
from pathlib import Path

import pytest

from finresearch.contracts import ResearchRequest
from finresearch.retrieval import KeywordEvidenceRetriever
from finresearch.workflow import PreflightFailure, prepare_single_document_request

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _request(base: dict, **updates: object) -> ResearchRequest:
    return ResearchRequest.model_validate({**base, **updates})


def test_date_boundary_stops_before_loading_pages() -> None:
    project_root = PROJECT_ROOT
    scope = json.loads((project_root / "configs/s0/scope.json").read_text(encoding="utf-8"))
    manifest = [json.loads(line) for line in (project_root / "storage/s0/document_manifest.jsonl").read_text(encoding="utf-8").splitlines()]
    base = json.loads((project_root / "configs/s0/request_examples.json").read_text(encoding="utf-8"))["valid"][0]
    config = json.loads((project_root / "configs/s1/retrieval_terms.json").read_text(encoding="utf-8"))
    loaded = False

    def loader():
        nonlocal loaded
        loaded = True
        return []

    with pytest.raises(PreflightFailure, match="尚不可用") as error:
        prepare_single_document_request(
            _request(base, as_of_date="2025-04-24"),
            scope=scope,
            manifest_records=manifest,
            allowed_document_id=manifest[0]["document_id"],
            snapshot_record={},
            page_loader=loader,
            retriever=KeywordEvidenceRetriever(config),
        )
    assert error.value.code == "NOT_AVAILABLE_AS_OF"
    assert loaded is False


def test_company_mismatch_stops_before_loading_pages() -> None:
    project_root = PROJECT_ROOT
    scope = json.loads((project_root / "configs/s0/scope.json").read_text(encoding="utf-8"))
    manifest = [json.loads(line) for line in (project_root / "storage/s0/document_manifest.jsonl").read_text(encoding="utf-8").splitlines()]
    base = json.loads((project_root / "configs/s0/request_examples.json").read_text(encoding="utf-8"))["valid"][0]
    config = json.loads((project_root / "configs/s1/retrieval_terms.json").read_text(encoding="utf-8"))
    loaded = False

    def loader():
        nonlocal loaded
        loaded = True
        return []

    with pytest.raises(PreflightFailure) as error:
        prepare_single_document_request(
            _request(base, company_ids=["688082.SH"]),
            scope=scope,
            manifest_records=manifest,
            allowed_document_id=manifest[0]["document_id"],
            snapshot_record={},
            page_loader=loader,
            retriever=KeywordEvidenceRetriever(config),
        )
    assert error.value.code == "OUT_OF_SCOPE"
    assert loaded is False
