import importlib.util
from pathlib import Path
import pytest
from finresearch.contracts import EvidenceAnswer
from finresearch.verification import validate_evidence_answer, validate_case_truth
from finresearch.retrieval import KeywordEvidenceRetriever
from finresearch.ingestion import normalize_page_text
from finresearch.contracts import DocumentPage
import hashlib
import json

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("fixture_answer", ROOT / "tests/s1/test_answer_validation.py")
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)

def answer(text, candidate, unit="percent"):
    return EvidenceAnswer(status="ANSWERABLE", answer=text, evidence_refs=[
        {"evidence_id": candidate.evidence_id, "document_id": candidate.document_id, "pdf_page": candidate.pdf_page}], period="FY2024", unit=unit)

@pytest.mark.parametrize("source,text", [("变动 -33.48%", "增长33.48%"), ("金额12345.67", "金额2345.67"), ("变化33.48%", "变化33.48个百分点")])
def test_complete_numeric_tokens_reject_sign_substring_and_percentage_points(source, text):
    candidate = fixture.candidate().model_copy(update={"text": source})
    result = validate_evidence_answer(answer(text, candidate), [candidate], expected_company_id="002371.SZ", expected_unit="percent")
    assert result.validation_status == "FAIL"

def test_explicit_negative_narrative_matches_negative_percent():
    candidate = fixture.candidate().model_copy(update={"text": "同比变动 -33.48%"})
    result = validate_evidence_answer(answer("同比下降33.48%", candidate), [candidate], expected_company_id="002371.SZ", expected_unit="percent")
    assert result.validation_status == "PASS"

def test_explanation_with_legitimate_year_never_mechanically_gets_semantic_pass():
    candidate = fixture.candidate().model_copy(update={"text": "2024年度采购商品支付的货款增加"})
    model_answer = answer("公司披露2024年度采购商品支付的货款增加。这一原因已获独立验证且为唯一原因，不能排除其他因素这一说法不成立。", candidate, None)
    validation = validate_evidence_answer(model_answer, [candidate], expected_company_id="002371.SZ")
    truth = validate_case_truth(model_answer, validation, {"expected_status": "ANSWERABLE", "expected_period": "FY2024", "expected_unit": None,
        "required_term_groups": [["公司披露"], ["不能"], ["独立验证"], ["唯一原因"]]})
    assert validation.validation_status == "NEEDS_REVIEW" and truth["passed"] is False

def test_long_line_is_explicit_failure_instead_of_silent_financial_truncation():
    config = json.loads((ROOT / "configs/s1/retrieval_terms.json").read_text(encoding="utf-8"))
    config["maximum_window_characters"] = 34
    text = "合并利润表\n单位:元\n营业收入 " + "x" * 80 + " 12345.67"
    digest = hashlib.sha256(text.encode()).hexdigest()
    page = DocumentPage(page_id="doc:p0001:"+digest[:16], document_id="doc", company_id="002371.SZ", pdf_page=1, raw_text=text,
        normalized_text=normalize_page_text(text), text_sha256=digest, parser_name="pdfplumber", parser_version="test", extraction_status="TEXT_AVAILABLE")
    with pytest.raises(ValueError, match="不能安全截断"):
        KeywordEvidenceRetriever(config).retrieve("test", "2024年度营业收入是多少？", [page])

def test_split_windows_preserve_exact_line_locator_and_complete_numeric_row():
    config = json.loads((ROOT / "configs/s1/retrieval_terms.json").read_text(encoding="utf-8"))
    config["maximum_window_characters"] = 38
    text = "合并利润表\n单位:元\n营业收入 " + "x" * 10 + "\n营业收入 12345.67\n营业收入 23456.78"
    digest = hashlib.sha256(text.encode()).hexdigest()
    page = DocumentPage(page_id="doc:p0001:"+digest[:16], document_id="doc", company_id="002371.SZ", pdf_page=1, raw_text=text,
        normalized_text=normalize_page_text(text), text_sha256=digest, parser_name="pdfplumber", parser_version="test", extraction_status="TEXT_AVAILABLE")
    windows = KeywordEvidenceRetriever(config).retrieve("test", "2024年度营业收入是多少？", [page]).evidence_candidates
    assert len(windows) >= 2
    for window in windows:
        assert window.text == "\n".join(page.normalized_text.splitlines()[window.line_start-1:window.line_end])
        assert len(window.text) <= 38
    assert any("营业收入 23456.78" in w.text for w in windows)
