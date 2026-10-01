from datetime import date
import pytest
from finresearch.contracts import EvidenceCandidate
from finresearch.contracts.research import HypothesisBatch, DisclosureBatch, Phenomenon, ResearchClaim, ResearchContext, WriterPlan
from finresearch.workflow.research_claims import review_hypotheses, review_disclosures, validate_writer_plan, render_report, validate_report


def ctx():
    return ResearchContext(run_id="test", company_ids=["002371.SZ"], fiscal_years=(2023,2024), as_of_date=date(2025,4,30), corpus_snapshot_id="corpus", protocol_id="revenue_quality_v1", protocol_version="1.0.0")


def test_model_strong_causal_rationale_is_not_published():
    batch = HypothesisBatch.model_validate({"hypotheses": [{"company_id": "002371.SZ", "category": "SETTLEMENT_TIMING", "phenomenon_ids": ["phen-1"],
        "rationale": "必然造假且100%是客户拖欠导致", "support_needed": ["TIMING_DETAIL"], "weakening_evidence_needed": ["INDEPENDENT_CONFIRMATION"]}]})
    phenomena = [Phenomenon(phenomenon_id="phen-1", company_id="002371.SZ", kind="CASH_REVENUE_DIVERGENCE", calculation_ids=["calc-1"], description="收入增现金流降")]
    _, claims, gaps = review_hypotheses(batch, "002371.SZ", phenomena)
    assert "尚未独立证实" in claims[0].text
    assert "造假" not in claims[0].text and "100%" not in claims[0].text
    assert claims[0].calculation_ids == ["calc-1"]
    assert gaps[0].status == "UNRESOLVED"
    with pytest.raises(ValueError):
        review_hypotheses(batch, "688072.SH", phenomena)


def test_company_explanation_stays_an_attributed_exact_quote():
    evidence = EvidenceCandidate(evidence_id="doc-source:evidence1", document_id="doc-source", page_id="doc-source:page1", company_id="002371.SZ",
        pdf_page=1, line_start=1, line_end=1, text="公司说明采购商品支付的货款增加。", matched_terms=[], retrieval_score=0, retrieval_rank=1, evidence_sha256="a"*64)
    batch = DisclosureBatch(company_id="002371.SZ", selected=[{"evidence_id": evidence.evidence_id,"exact_quote":"采购商品支付的货款增加"}])
    claims, gaps = review_disclosures(batch, "002371.SZ", {evidence.evidence_id:evidence})
    assert claims[0].kind == "DISCLOSED"
    assert claims[0].text.startswith("公司披露")
    bad = DisclosureBatch(company_id="002371.SZ", selected=[{"evidence_id":evidence.evidence_id,"exact_quote":"已经独立证实是唯一原因"}])
    claims, gaps = review_disclosures(bad, "002371.SZ", {evidence.evidence_id:evidence})
    assert not claims and any(g.gap_type=="CLAIM_REJECTED" for g in gaps)


def test_writer_cannot_add_omit_or_duplicate_facts():
    claim = ResearchClaim(claim_id="claim-1",company_id="002371.SZ",kind="COMPUTED",text="收入增长率为20.00%。",calculation_ids=["calc-1"],status="APPROVED")
    for ids in (["claim-1","invented"],["invented"],["claim-1","claim-1"]):
        with pytest.raises(ValueError):
            validate_writer_plan(WriterPlan(ordered_claim_ids=ids),[claim])
    ids=validate_writer_plan(WriterPlan(ordered_claim_ids=["claim-1"]),[claim])
    report=render_report(ctx(),[claim],[],ids,execution_status="COMPLETED")
    assert validate_report(report,ctx(),[claim],[],ids,"COMPLETED")["status"]=="PASS"
    assert validate_report(report.replace("20.00%","200.00%"),ctx(),[claim],[],ids,"COMPLETED")["status"]=="FAIL"


def test_associating_hypotheses_preserves_disclosed_and_other_dependencies():
    from finresearch.workflow.research_claims import attach_hypothesis_disclosures
    disclosed=ResearchClaim(claim_id="quote",company_id="002371.SZ",kind="DISCLOSED",text="公司披露：“采购付款增加”",
        evidence_ids=["source-quote"],status="APPROVED")
    computed=ResearchClaim(claim_id="number",company_id="002371.SZ",kind="COMPUTED",text="收入增长",
        observation_ids=["obs"],calculation_ids=["calc"],evidence_ids=["financial-source"],status="APPROVED")
    unresolved=ResearchClaim(claim_id="gap",company_id="002371.SZ",kind="UNRESOLVED",text="仍需核验",
        evidence_ids=["counter-source"],status="APPROVED")
    result=attach_hypothesis_disclosures([disclosed,computed,unresolved],[],{})
    assert [r.model_dump() for r in result]==[r.model_dump() for r in (disclosed,computed,unresolved)]
