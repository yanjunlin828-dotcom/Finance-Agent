from datetime import date
import json
from pathlib import Path
import pytest
from finresearch.contracts.research import ResearchContext
from finresearch.finance.research_tables import extract_annual_observations
from finresearch.retrieval.corpus import ResearchCorpus
from finresearch.workflow.research_analysis import calculate_research, detect_phenomena, calculation_claims

ROOT = Path(__file__).resolve().parents[2]


def context():
    return ResearchContext(run_id="test-s4", company_ids=["002371.SZ", "688072.SH", "688082.SH"], fiscal_years=(2023, 2024),
        as_of_date=date(2025, 4, 30), corpus_snapshot_id="s3-semiconductor-equipment-ar-v1", protocol_id="revenue_quality_v1", protocol_version="1.0.0")


@pytest.fixture(scope="module")
def financials():
    def read(path):
        return json.loads((ROOT / path).read_text(encoding="utf-8"))
    corpus = ResearchCorpus(ROOT, read("storage/s3/corpora/s3-semiconductor-equipment-ar-v1/ready.json"))
    mapping = read("configs/s4/financial_source_map.json")
    dictionary = read("configs/s2/metric_dictionary.json")
    return corpus, mapping, dictionary


def test_three_company_source_tables_and_negative_signs(financials):
    corpus, mapping, dictionary = financials
    observations, evidence, checks = extract_annual_observations(corpus, mapping, dictionary, context().as_of_date)
    assert len(observations) == 18
    assert all(c["passed"] for c in checks)
    values = {(o.company_id, o.metric_id, o.fiscal_year): str(o.standard_value) for o in observations}
    assert values[("688072.SH", "operating_cash_flow_net", 2024)] == "-282525331.06"
    assert values[("688082.SH", "operating_cash_flow_net", 2023)] == "-426963656.49"
    assert values[("688082.SH", "revenue", 2024)] == "5617740375.66"
    rows, gaps = calculate_research(context(), observations)
    assert sum(map(len, rows.values())) == 24
    assert rows["688072.SH"]["operating_cash_flow_net_growth"].status == "NEGATIVE_BASE"
    assert rows["688082.SH"]["operating_cash_flow_net_growth"].status == "NEGATIVE_BASE"
    assert rows["002371.SZ"]["revenue_ocf_growth_gap_pp"].status == "VALID"
    phenomena = detect_phenomena(rows, context().fiscal_years)
    assert any(p.kind == "CASH_REVENUE_DIVERGENCE" and p.company_id == "002371.SZ" for p in phenomena)
    assert any(p.kind == "NEGATIVE_OCF_RATIO" and p.company_id == "688072.SH" for p in phenomena)
    assert not any("造假" in c.text or "风险高" in c.text for c in calculation_claims(rows))
    assert len(gaps) == 4


def test_financial_disclosure_cutoff_does_not_backfill(financials):
    corpus, mapping, dictionary = financials
    observations, _, checks = extract_annual_observations(corpus, mapping, dictionary, date(2025, 2, 27))
    assert len(observations) == 6
    assert {o.company_id for o in observations} == {"688082.SH"}
    assert sum(c.get("status") == "NOT_AVAILABLE_AS_OF" for c in checks) == 2


def test_source_header_change_requires_review(financials):
    corpus, mapping, dictionary = financials
    altered = json.loads(json.dumps(mapping))
    altered["documents"][0]["rows"][0]["header_excerpts"] = ["不存在的单位与年度"]
    with pytest.raises(ValueError, match="表头"):
        extract_annual_observations(corpus, altered, dictionary, context().as_of_date)


def test_future_or_conflicting_observation_stops_analysis(financials):
    observations, _, _ = extract_annual_observations(*financials, context().as_of_date)
    with pytest.raises(ValueError, match="截止日"):
        calculate_research(context().model_copy(update={"as_of_date": date(2024, 1, 1)}), observations)
    with pytest.raises(ValueError, match="冲突"):
        calculate_research(context(), observations + [observations[0].model_copy(update={"document_id": "different"})])


def test_synthetic_divergence_does_not_become_causal_or_fraud_claim(financials):
    """Fictional values only exercise analysis; never enter the source corpus."""
    from decimal import Decimal
    observations,_,_=extract_annual_observations(*financials,context().as_of_date)
    synthetic=[]
    amounts={"revenue":{2023:100,2024:150},"operating_cash_flow_net":{2023:20,2024:10},"accounts_receivable":{2023:10,2024:30}}
    for o in observations:
        if o.company_id!="002371.SZ":
            continue
        amount=Decimal(amounts[o.metric_id][o.fiscal_year])
        synthetic.append(o.model_copy(update={"raw_value_text":str(amount),"raw_value":amount,"standard_value":amount}))
    ctx=context().model_copy(update={"company_ids":["002371.SZ"]})
    rows,gaps=calculate_research(ctx,synthetic)
    phenomena=detect_phenomena(rows,ctx.fiscal_years)
    assert {p.kind for p in phenomena} >= {"CASH_REVENUE_DIVERGENCE","RECEIVABLE_GROWTH_EXCEEDS_REVENUE","RECEIVABLE_RATIO_INCREASE"}
    assert not gaps
    assert all("造假" not in c.text and "导致" not in c.text for c in calculation_claims(rows))
