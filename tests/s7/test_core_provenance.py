"""Full fixed-graph regression for the S7 discovered source-link defect."""
from datetime import date
from finresearch.contracts import EvidenceCandidate,stable_sha256
from finresearch.contracts.research import ResearchContext,HypothesisBatch,DisclosureBatch,WriterPlan
from finresearch.workflow.fixed_research import ResearchDependencies,compile_research_graph


def test_fixed_graph_publishes_disclosure_with_source_link():
    protocol={'protocol_id':'revenue_quality_v1','version':'1.0.0','fiscal_years':[2023,2024],
        'node_soft_timeout_seconds':180,'nodes':['validate_context','load_financials','calculate','detect_phenomena','propose_hypotheses',
        'collect_fixed_evidence','review_claims','write_report','validate_report']}
    ctx=ResearchContext(run_id='quote-regression',company_ids=['002371.SZ'],fiscal_years=(2023,2024),as_of_date=date(2025,4,30),
        corpus_snapshot_id='test',protocol_id='revenue_quality_v1',protocol_version='1.0.0',protocol_config_sha256=stable_sha256(protocol))
    ev=EvidenceCandidate(evidence_id='source:evidence1',document_id='source',page_id='source:page1',company_id='002371.SZ',pdf_page=1,
        line_start=1,line_end=1,text='采购商品支付的货款增加。',matched_terms=[],retrieval_score=1,retrieval_rank=1,evidence_sha256='a'*64)
    deps=ResearchDependencies(protocol,lambda _:[],lambda *a:None,lambda *a:[ev],
        lambda *a:DisclosureBatch(company_id='002371.SZ',selected=[{'evidence_id':ev.evidence_id,'exact_quote':ev.text}]),
        lambda ctx,claims:WriterPlan(ordered_claim_ids=[c.claim_id for c in claims if c.status=='APPROVED']))
    result=compile_research_graph(deps).invoke({'context':ctx.model_dump(mode='json')})
    quote=next(c for c in result['claims'] if c['kind']=='DISCLOSED')
    assert quote['evidence_ids']==[ev.evidence_id]
    assert ev.evidence_id in result['report']
    assert result['validation']['status']=='PASS'
