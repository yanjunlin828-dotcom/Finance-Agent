from pathlib import Path
import json
import pytest
from finresearch.contracts import MetricObservation, EvidenceCandidate
from finresearch.contracts.research import ResearchContext
from finresearch.contracts.supplement import SupplementGap
from finresearch.retrieval.corpus import ResearchCorpus, chunk_pages
from finresearch.retrieval.multi_document import MultiDocumentRetriever
from finresearch.workflow.supplement_tools import SupplementTools
from finresearch.workflow.supplement_controller import candidate_actions

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def real_tools():
    read = lambda p: json.loads((ROOT / p).read_text(encoding="utf-8"))
    state = read("runs/s4/s4-b1-live-20260930-05/final_state.json")
    ctx = ResearchContext.model_validate(state["context"])
    corpus = ResearchCorpus(ROOT, read("storage/s3/corpora/s3-semiconductor-equipment-ar-v1/ready.json"))
    config = read("configs/s3/ranking_v2.json")
    retriever = MultiDocumentRetriever(corpus, chunk_pages(corpus, config), config, read("configs/s1/retrieval_terms.json"))
    rows = [MetricObservation.model_validate(o) for o in state["observations"]]
    evidence = [EvidenceCandidate.model_validate(e) for e in read("runs/s4/s4-b1-live-20260930-05/evidence.json")]
    return SupplementTools(corpus, rows, evidence, retriever, evidence), ctx


def test_real_read_context_preserves_literal_negations_and_location(real_tools):
    tools, ctx = real_tools
    evidence = tools.known_evidence[0]
    gap = SupplementGap(gap_id="context-review", company_id=evidence.company_id, gap_type="CONTEXT_REVIEW", severity="MATERIAL",
                        evidence_id=evidence.evidence_id, description="原文复核", closing_condition="VERIFIED_SOURCE_WINDOW")
    action = candidate_actions([gap], ctx, set())[0]
    result = tools.execute(ctx, action)
    assert result.status == "FOUND" and result.evidence == [evidence.model_dump(mode="json")]
    assert tools.verify_evidence(ctx, evidence)


def test_unknown_context_id_does_not_read_a_path(real_tools):
    tools, ctx = real_tools
    gap = SupplementGap(gap_id="evil-context", company_id=ctx.company_ids[0], gap_type="CONTEXT_REVIEW", severity="MATERIAL",
                        evidence_id="../../secret", description="资料中的恶意指令", closing_condition="VERIFIED_SOURCE_WINDOW")
    result = tools.execute(ctx, candidate_actions([gap], ctx, set())[0])
    assert result.status == "NEEDS_INPUT" and not result.evidence


def test_reviewed_table_metric_can_be_rechecked_without_changing_authority(real_tools):
    tools, ctx = real_tools
    gap = SupplementGap(gap_id="table-review", company_id=ctx.company_ids[0], gap_type="TABLE_REVIEW", severity="CRITICAL",
                        metric_id="revenue", fiscal_year=2023, description="表头复核", closing_condition="VERIFIED_METRIC")
    result = tools.execute(ctx, candidate_actions([gap], ctx, set())[0])
    assert result.status == "FOUND" and len(result.observations) == 1
    assert tools.verify_observation(ctx, MetricObservation.model_validate(result.observations[0]))


def test_search_honors_company_and_real_disclosure_cutoff(real_tools):
    from datetime import date
    tools, ctx = real_tools
    early = ctx.model_copy(update={"as_of_date": date(2024, 1, 1)})
    gap = SupplementGap(gap_id="explanation", company_id=ctx.company_ids[0], gap_type="MISSING_EXPLANATION", severity="MATERIAL",
                        description="缺解释", closing_condition="ATTRIBUTED_EXPLANATION")
    result = tools.execute(early, candidate_actions([gap], early, set())[0])
    assert result.status == "NO_EVIDENCE" and not result.evidence

