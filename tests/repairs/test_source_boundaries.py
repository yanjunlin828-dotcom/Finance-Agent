"""Regression tests of actual source/availability paths, including review counterexamples."""
import hashlib
import json
from pathlib import Path
import pytest
from pydantic import ValidationError
from finresearch.contracts import DocumentManifestRecord, MetricObservationSeed, DocumentPage, ResearchRequest
from finresearch.contracts.numeric import parse_amount_text, amount_occurs_in_source
from finresearch.finance import build_definition_index, build_evidence_index, build_validated_observations
from finresearch.finance.observations import read_evidence_jsonl
from finresearch.ingestion import load_verified_pages, PDFPageIngestor, normalize_page_text
from finresearch.retrieval import KeywordEvidenceRetriever
from finresearch.workflow import prepare_single_document_request, PreflightFailure

ROOT = Path(__file__).resolve().parents[2]
def data(path):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))
def manifest():
    return DocumentManifestRecord.model_validate_json((ROOT / "storage/s0/document_manifest.jsonl").read_text(encoding="utf-8").splitlines()[0])
def seed():
    return MetricObservationSeed.model_validate_json((ROOT / "storage/s2/metric_observation_seeds.jsonl").read_text(encoding="utf-8").splitlines()[0])
def build(item):
    definitions = build_definition_index(data("configs/s2/metric_dictionary.json"))
    paths = [ROOT / "runs/s1/s1-live-q1-20260922-01/cases/S0-Q1-REVENUE/evidence.jsonl"]
    return build_validated_observations([item], definitions, build_evidence_index(read_evidence_jsonl(paths)), manifest=manifest())

def test_real_seed_still_builds_exact_amount():
    observations, reports = build(seed())
    assert reports[0]["passed"]
    assert str(observations[0].standard_value) == "29838069162.26"

@pytest.mark.parametrize("token,source", [("1", "29,838,069,162.26"), ("33.48", "-33.48"), ("2345.67", "12345.67")])
def test_source_binding_is_not_substring_matching(token, source):
    assert not amount_occurs_in_source(token, source)

def test_importer_snapshot_path_cannot_escape(tmp_path):
    record = manifest().model_dump(mode="json")
    record["snapshot_id"] = "x/../../../../outside"
    with pytest.raises(ValueError, match="超出storage_root"):
        PDFPageIngestor(ROOT, tmp_path).import_document(record)
    assert list(tmp_path.iterdir()) == []

def test_raw_text_and_value_mismatch_rejected_even_after_model_copy():
    value = seed()
    with pytest.raises(ValidationError, match="raw_value"):
        MetricObservationSeed.model_validate({**value.model_dump(), "raw_value": "1"})
    observations, reports = build(value.model_copy(update={"raw_value": parse_amount_text("1")}))
    assert not observations and not reports[0]["passed"]

@pytest.mark.parametrize("field,value", [("document_published_on", "2025-04-01"), ("company_id", "688082.SH"), ("fiscal_year", 2026)])
def test_seed_context_cannot_override_registered_disclosure(field, value):
    item = MetricObservationSeed.model_validate({**seed().model_dump(), field: value})
    observations, reports = build(item)
    assert observations == [] and not reports[0]["passed"]

@pytest.mark.parametrize("text", ["12,34.56", "NaN", "Infinity", "123元", "--12", "1e5"])
def test_ambiguous_amount_never_guessed(text):
    with pytest.raises(ValueError):
        parse_amount_text(text)

def test_full_width_and_negative_amount_preserved():
    assert str(parse_amount_text("－１２，３４５．６７")) == "-12345.67"

def test_actual_registered_snapshot_loads_complete_pages():
    registry = data("storage/s1/snapshot_registry.json")
    snapshot = registry["snapshots"][0]
    pages = load_verified_pages(ROOT, snapshot, manifest().model_dump(mode="json"))
    assert len(pages) == 190 and pages[94].pdf_page == 95

def test_preflight_rejects_review_fake_page_before_retrieval():
    registry = data("storage/s1/snapshot_registry.json")
    snapshot = registry["snapshots"][0]
    pages = load_verified_pages(ROOT, snapshot, manifest().model_dump(mode="json"))
    bad = pages[94].model_copy(update={"normalized_text": "营业收入 999,888"})
    request = ResearchRequest.model_validate(data("configs/s0/request_examples.json")["valid"][0])
    with pytest.raises(PreflightFailure) as error:
        prepare_single_document_request(request, scope=data("configs/s0/scope.json"),
            manifest_records=[manifest().model_dump(mode="json")], allowed_document_id=manifest().document_id,
            snapshot_record=snapshot, page_loader=lambda: [bad], retriever=KeywordEvidenceRetriever(data("configs/s1/retrieval_terms.json")))
    assert error.value.code == "DOCUMENT_INTEGRITY_FAILURE"

def test_import_reuse_rejects_same_bytes_changed_document_identity(tmp_path):
    record = manifest().model_dump(mode="json")
    snapshot = data("storage/s1/snapshot_registry.json")["snapshots"][0]
    cached = ROOT / snapshot["pages_path"]
    target = tmp_path / snapshot["snapshot_id"]
    target.mkdir()
    (target / "pages.jsonl").write_bytes(cached.read_bytes())
    (target / "snapshot.json").write_bytes((ROOT / snapshot["snapshot_manifest_path"]).read_bytes())
    record["document_id"] = "renamed-same-byte-document"
    with pytest.raises(RuntimeError, match="不一致"):
        PDFPageIngestor(ROOT, tmp_path).import_document(record)

def test_changed_page_file_rejected_without_replacing_registry(tmp_path):
    record = manifest().model_dump(mode="json")
    snapshot = data("storage/s1/snapshot_registry.json")["snapshots"][0]
    # Copy only the local inputs needed by the loader; original snapshot untouched.
    for relative in [record["local_path"], snapshot["pages_path"], snapshot["snapshot_manifest_path"]]:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / relative).read_bytes())
    (tmp_path / snapshot["pages_path"]).write_text("{}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="SHA-256"):
        load_verified_pages(tmp_path, snapshot, record)
