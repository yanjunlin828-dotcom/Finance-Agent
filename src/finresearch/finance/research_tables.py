"""Extract reviewed table rows into three-company annual observations.

This is a small deterministic adapter over source-checked column mappings,
not a general financial-table parser or unreviewed model extraction.
"""
from __future__ import annotations
from datetime import date
import hashlib
import re

from finresearch.contracts import EvidenceCandidate, MetricObservationSeed
from finresearch.contracts.numeric import parse_amount_text
from finresearch.finance.observations import build_definition_index, build_evidence_index, build_validated_observations
from finresearch.ingestion.pdf_pages import validate_evidence_pages
from finresearch.retrieval.corpus import ResearchCorpus

_MONEY = re.compile(r"(?<![\d.,])[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2}(?!\d)")


def page_evidence(page, *, rank: int = 1) -> EvidenceCandidate:
    """Bind an entire canonical page, including headings, to its exact lines."""
    digest = hashlib.sha256(page.normalized_text.encode("utf-8")).hexdigest()
    return EvidenceCandidate(evidence_id=f"{page.document_id}:p{page.pdf_page:04d}:e{digest[:16]}", document_id=page.document_id,
        page_id=page.page_id, company_id=page.company_id, pdf_page=page.pdf_page,
        line_start=1, line_end=len(page.normalized_text.splitlines()), text=page.normalized_text,
        matched_terms=[], retrieval_score=0, retrieval_rank=rank, evidence_sha256=digest)


def extract_annual_observations(corpus: ResearchCorpus, mapping: dict, dictionary: dict, as_of_date: date):
    """Read only mapped, eligible disclosures and verify columns/amounts.

    Source mapping fixes the annual current/prior columns after visual review.
    Disclosure dates come from the locked corpus. Missing eligible documents
    yield explicit diagnostics; ambiguous rows or changed headers are errors.
    Returns observations, their evidence, and per-row source checks.
    """
    if mapping["review_status"] != "SOURCE_TEXT_AND_VISUAL_CHECKED":
        raise ValueError("来源映射尚未验收")
    definitions = build_definition_index(dictionary)
    observations = []
    all_evidence: dict[str, EvidenceCandidate] = {}
    reports = []
    for document in mapping["documents"]:
        record = corpus.documents.get(document["document_id"])
        if record is None or record.ts_code != document["company_id"] or int(record.reporting_period) != document["reporting_year"]:
            raise ValueError("财务映射文档身份与快照不一致")
        if record.published_on is None or record.published_on > as_of_date:
            reports.append({"company_id": document["company_id"], "status": "NOT_AVAILABLE_AS_OF", "passed": False})
            continue
        pages = [p for p in corpus.pages.values() if p.document_id == record.document_id]
        by_number = {p.pdf_page: p for p in pages}
        seeds = []
        evidence = {}
        for row in document["rows"]:
            header = by_number[row["header_page"]]
            body = by_number[row["pdf_page"]]
            if not all(s in header.normalized_text for s in row["header_excerpts"]):
                raise ValueError("财务表头/单位/年度发生变化，需要重新复核")
            lines = body.normalized_text.splitlines()
            starts = [i for i, line in enumerate(lines) if line.startswith(row["row_prefix"])]
            if len(starts) != 1:
                raise ValueError("财务行无法唯一定位，需要重新提取")
            text = "\n".join(lines[starts[0]:starts[0] + row["row_line_count"]])
            cells = _MONEY.findall(text)
            if len(cells) != 2:
                raise ValueError("年度金额列不为两列，需要人工复核")
            refs = []
            for page in {header.page_id: header, body.page_id: body}.values():
                candidate = page_evidence(page)
                evidence[candidate.evidence_id] = candidate
                refs.append({"evidence_id": candidate.evidence_id, "pdf_page": candidate.pdf_page, "source_label": row["metric_id"]})
            definition = definitions[row["metric_id"]]
            for column, raw_text in enumerate(cells):
                year = document["reporting_year"] - column
                flow = definition.metric_kind == "FLOW"
                seeds.append(MetricObservationSeed(metric_id=definition.metric_id, company_id=record.ts_code, fiscal_year=year,
                    period_kind=definition.metric_kind, period_start=date(year, 1, 1) if flow else None,
                    period_end=date(year, 12, 31) if flow else None, observed_at=None if flow else date(year, 12, 31),
                    raw_value_text=raw_text, raw_value=parse_amount_text(raw_text), raw_unit="CNY_YUAN", currency="CNY",
                    statement_scope="CONSOLIDATED", measurement_basis=definition.measurement_basis, value_status="OBSERVED",
                    document_id=record.document_id, document_sha256=record.sha256, document_published_on=record.published_on,
                    evidence_refs=refs, extraction_method="TABLE_EXTRACTED", review_status="SOURCE_CHECKED",
                    review_note=f"Reviewed row mapping {mapping['mapping_version']}; current/prior column {column}; comparative figures available only on this disclosure date.",
                    observation_version=1, supersedes_observation_id=None))
        validate_evidence_pages(list(evidence.values()), pages)
        built, checks = build_validated_observations(seeds, definitions, build_evidence_index(evidence.values()), manifest=record)
        if len(built) != len(seeds) or not all(item["passed"] for item in checks):
            raise ValueError("财务来源校验失败")
        observations.extend(built)
        reports.extend({"company_id": record.ts_code, **item} for item in checks)
        all_evidence.update(evidence)
    return observations, list(all_evidence.values()), reports
