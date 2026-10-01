import importlib.util
from pathlib import Path
import sys
import pytest
from finresearch.contracts import EvidenceCandidate

spec=importlib.util.spec_from_file_location("s4_test_runner",Path(__file__).resolve().parents[2]/"scripts/s4/run_research.py")
runner=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=runner
spec.loader.exec_module(runner)


def evidence(text):
    import hashlib
    return EvidenceCandidate(evidence_id="doc-test:evidence-id",document_id="doc-test",page_id="doc-test:page-id",company_id="002371.SZ",pdf_page=1,
        line_start=1,line_end=len(text.splitlines()),text=text,matched_terms=[],retrieval_score=0,retrieval_rank=1,evidence_sha256=hashlib.sha256(text.encode()).hexdigest())


def test_quotes_do_not_cut_long_sentences_or_drop_checkbox_negation():
    e=evidence("此前表格\n(4)经营现金流减少，采购付款增加。\n公司是否持续亏损\n□是 ☑否。\n"+"这是超长句"*100+"。\n本期未发生异常变化。")
    options=runner.quote_options([e])
    assert options
    assert all(5<=len(o["exact_quote"])<=250 and o["exact_quote"] in e.text for o in options)
    assert not any("公司是否" in o["exact_quote"] or "超长句" in o["exact_quote"] for o in options)
    assert any("采购付款增加。" in o["exact_quote"] for o in options)
    assert any(o["exact_quote"]=="本期未发生异常变化。" for o in options)


def test_unknown_or_duplicate_quote_selection_rejected():
    e=evidence("公司说明采购付款增加。")
    options=runner.quote_options([e])
    choice={"quote_id":options[0]["quote_id"]}
    valid=runner.QuoteChoiceBatch(company_id=e.company_id,selected=[choice])
    assert runner.bind_quote_choices(valid,[e],options,e.company_id).selected[0].exact_quote==e.text
    for selected in ([{"quote_id":"invented"}],[choice,choice]):
        with pytest.raises(ValueError):
            runner.bind_quote_choices(runner.QuoteChoiceBatch(company_id=e.company_id,selected=selected),[e],options,e.company_id)
