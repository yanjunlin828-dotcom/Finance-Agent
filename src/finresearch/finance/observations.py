"""从版本化字典和人工复核种子构造、验证真实财务观察。"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from finresearch.contracts import (
    EvidenceCandidate,
    MetricDefinition,
    MetricObservation,
    MetricObservationSeed,
    DocumentManifestRecord,
)
from finresearch.contracts.numeric import parse_amount_text, amount_occurs_in_source

from .units import build_observation


def build_definition_index(payload: dict) -> dict[str, MetricDefinition]:
    definitions = [MetricDefinition.model_validate(item) for item in payload["definitions"]]
    index = {item.metric_id: item for item in definitions}
    if len(index) != len(definitions):
        raise ValueError("指标字典metric_id重复")
    return index


def build_evidence_index(candidates: Iterable[EvidenceCandidate]) -> dict[str, EvidenceCandidate]:
    index: dict[str, EvidenceCandidate] = {}
    for candidate in candidates:
        existing = index.get(candidate.evidence_id)
        if existing is not None and existing != candidate:
            raise ValueError(f"同一evidence_id对应不同内容: {candidate.evidence_id}")
        index[candidate.evidence_id] = candidate
    return index


def validate_seed_source(
    seed: MetricObservationSeed,
    definition: MetricDefinition,
    evidence_index: dict[str, EvidenceCandidate],
    *,
    manifest: DocumentManifestRecord,
) -> list[dict[str, object]]:
    """验证种子的文档身份、证据元数据和原始数值文本。"""

    checks: list[dict[str, object]] = []
    checks.append({"name": "document_id", "passed": seed.document_id == manifest.document_id})
    checks.append({"name": "document_sha256", "passed": seed.document_sha256 == manifest.sha256})
    checks.append({"name": "document_company", "passed": seed.company_id == manifest.ts_code})
    # Availability is a property of the registered disclosure, never user input.
    checks.append({"name": "document_published_on", "passed": manifest.published_on is not None and seed.document_published_on == manifest.published_on})
    checks.append({"name": "reporting_year", "passed": seed.fiscal_year <= int(manifest.reporting_period)})
    checks.append({"name": "metric_kind", "passed": seed.period_kind == definition.metric_kind})
    checks.append({"name": "statement_scope", "passed": seed.statement_scope == definition.statement_scope})
    checks.append({"name": "measurement_basis", "passed": seed.measurement_basis == definition.measurement_basis})
    evidence_texts: list[str] = []
    for reference in seed.evidence_refs:
        candidate = evidence_index.get(reference.evidence_id)
        checks.append(
            {
                "name": f"evidence_known:{reference.evidence_id}",
                "passed": candidate is not None,
            }
        )
        metadata_matches = bool(
            candidate
            and candidate.document_id == seed.document_id
            and candidate.pdf_page == reference.pdf_page
            and candidate.company_id == seed.company_id
        )
        checks.append(
            {
                "name": f"evidence_metadata:{reference.evidence_id}",
                "passed": metadata_matches,
            }
        )
        if candidate:
            evidence_texts.append(candidate.text)
    if seed.value_status == "OBSERVED":
        try:
            matches = parse_amount_text(seed.raw_value_text or "") == seed.raw_value
        except ValueError:
            matches = False
        checks.append({"name": "raw_value_matches_text", "passed": matches})
        checks.append(
            {
                "name": "raw_value_text_present",
                "passed": bool(seed.raw_value_text and any(amount_occurs_in_source(seed.raw_value_text, text) for text in evidence_texts)),
            }
        )
    return checks


def build_validated_observations(
    seeds: Iterable[MetricObservationSeed],
    definitions: dict[str, MetricDefinition],
    evidence_index: dict[str, EvidenceCandidate],
    *,
    manifest: DocumentManifestRecord,
) -> tuple[list[MetricObservation], list[dict[str, object]]]:
    observations: list[MetricObservation] = []
    reports: list[dict[str, object]] = []
    for seed in seeds:
        definition = definitions.get(seed.metric_id)
        if definition is None:
            raise ValueError(f"种子引用未知指标: {seed.metric_id}")
        checks = validate_seed_source(
            seed,
            definition,
            evidence_index,
            manifest=manifest,
        )
        passed = all(bool(item["passed"]) for item in checks)
        reports.append(
            {
                "metric_id": seed.metric_id,
                "fiscal_year": seed.fiscal_year,
                "review_status": seed.review_status,
                "passed": passed,
                "checks": checks,
            }
        )
        if not passed:
            continue
        observations.append(build_observation(seed, definition))
    return observations, reports


def read_evidence_jsonl(paths: Iterable[Path]) -> list[EvidenceCandidate]:
    candidates: list[EvidenceCandidate] = []
    for path in paths:
        candidates.extend(
            EvidenceCandidate.model_validate_json(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    return candidates
