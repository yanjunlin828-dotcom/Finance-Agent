from decimal import Decimal
import json
from pathlib import Path

from finresearch.contracts import EvidenceCandidate, MetricObservationSeed, DocumentManifestRecord
from finresearch.finance import build_definition_index, build_evidence_index, build_validated_observations


def test_source_value_must_appear_in_registered_evidence() -> None:
    definitions = build_definition_index(
        {
            "definitions": [
                {
                    "metric_id": "revenue", "name_zh": "营业收入", "definition": "合并利润表营业收入",
                    "metric_kind": "FLOW", "currency": "CNY", "canonical_unit": "CNY_YUAN", "allowed_raw_units": {"CNY_YUAN": "1"},
                    "statement_scope": "CONSOLIDATED", "measurement_basis": "REPORTED", "allowed_formula_ids": [],
                    "prohibited_equivalences": [], "version": "1.0.0", "status": "ACTIVE",
                }
            ]
        }
    )
    seed = MetricObservationSeed.model_validate(
        {
            "metric_id": "revenue", "company_id": "002371.SZ", "fiscal_year": 2024, "period_kind": "FLOW",
            "period_start": "2024-01-01", "period_end": "2024-12-31", "observed_at": None,
            "raw_value_text": "123.45", "raw_value": "123.45", "raw_unit": "CNY_YUAN", "currency": "CNY",
            "statement_scope": "CONSOLIDATED", "measurement_basis": "REPORTED", "value_status": "OBSERVED",
            "document_id": "doc-1", "document_sha256": "a" * 64, "document_published_on": "2025-01-01",
            "evidence_refs": [{"evidence_id": "doc-1:p0001:test", "pdf_page": 1, "source_label": "收入"}],
            "extraction_method": "TABLE_EXTRACTED", "review_status": "DRAFT", "review_note": "test",
            "observation_version": 1, "supersedes_observation_id": None,
        }
    )
    candidate = EvidenceCandidate.model_validate(
        {
            "evidence_id": "doc-1:p0001:test", "document_id": "doc-1", "page_id": "doc-1:p0001:hash",
            "company_id": "002371.SZ", "pdf_page": 1, "line_start": 1, "line_end": 1, "text": "营业收入 999.99",
            "matched_terms": ["营业收入"], "retrieval_score": 1, "retrieval_rank": 1, "evidence_sha256": "b" * 64,
        }
    )
    observations, reports = build_validated_observations(
        [seed], definitions, build_evidence_index([candidate]),
        manifest=DocumentManifestRecord.model_validate({
            **json.loads((Path(__file__).resolve().parents[2] / "storage/s0/document_manifest.jsonl").read_text(encoding="utf-8").splitlines()[0]),
            "document_id": "doc-1", "sha256": "a" * 64, "published_on": "2025-01-01",
        })
    )
    assert observations == []
    assert reports[0]["passed"] is False
