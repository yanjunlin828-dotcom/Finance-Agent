"""Offline S6 fixtures with real financial inputs and explicitly synthetic model calls."""
from datetime import date
import json
from pathlib import Path
from finresearch.contracts import MetricObservation, stable_sha256
from finresearch.contracts.research import ResearchContext, HypothesisBatch, DisclosureBatch, WriterPlan
from finresearch.workflow.fixed_research import ResearchDependencies

ROOT = Path(__file__).resolve().parents[2]


def setup(task_id="s6-test", counts=None):
    protocol = json.loads((ROOT / "protocols/revenue_quality_v1.json").read_text(encoding="utf-8"))
    rows = json.loads((ROOT / "runs/s4/s4-financial-inputs-20260930-01/observations.json").read_text(encoding="utf-8"))
    rows = [MetricObservation.model_validate(o) for o in rows]
    ctx = ResearchContext(run_id=task_id, company_ids=["002371.SZ", "688072.SH", "688082.SH"], fiscal_years=(2023, 2024),
                          as_of_date=date(2025, 4, 30), corpus_snapshot_id="s3-semiconductor-equipment-ar-v1",
                          protocol_id="revenue_quality_v1", protocol_version="1.0.0", protocol_config_sha256=stable_sha256(protocol))
    counts = counts if counts is not None else {}
    def count(name):
        counts[name] = counts.get(name, 0) + 1
    def hypotheses(ctx, company, phenomena):
        count("hypotheses:" + company)
        return HypothesisBatch(hypotheses=[{"company_id": company, "category": "UNRESOLVED_CAUSE",
                                           "phenomenon_ids": [phenomena[0].phenomenon_id], "rationale": "现象不能证明原因",
                                           "support_needed": ["INDEPENDENT_CONFIRMATION"], "weakening_evidence_needed": ["TIMING_DETAIL"]}])
    def collect(ctx, company):
        count("collect:" + company)
        return []
    def disclose(ctx, company, evidence):
        count("disclosures:" + company)
        return DisclosureBatch(company_id=company, selected=[])
    def writer(ctx, claims):
        count("writer")
        return WriterPlan(ordered_claim_ids=[c.claim_id for c in claims if c.status == "APPROVED"])
    deps = ResearchDependencies(protocol, lambda ctx: rows, hypotheses, collect, disclose, writer)
    return deps, ctx, counts


def create_executor(tmp_path, task_id="s6-test", hook=None, counts=None):
    from finresearch.workflow.session_executor import SessionExecutor
    deps, ctx, counts = setup(task_id, counts)
    anchor = tmp_path / "locked.json"
    anchor.write_text("{}", encoding="utf-8")
    from finresearch.retrieval.corpus import file_sha256
    ex = SessionExecutor(tmp_path, task_id, deps, {}, hook=hook)
    ex.create(ctx, {"locked.json": file_sha256(anchor)}, {"schema_version": 1, "maximum_context_utf8_bytes": 100000, "execution_mode": "OFFLINE_TEST"})
    return ex, ctx, counts
