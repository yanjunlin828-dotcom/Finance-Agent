from datetime import date
import hashlib
from types import SimpleNamespace
import pytest
from pydantic import ValidationError

from finresearch.contracts import DocumentPage
from finresearch.contracts.retrieval import RetrievalQuery
from finresearch.retrieval.corpus import ResearchCorpus, chunk_pages
from finresearch.retrieval.multi_document import MultiDocumentRetriever, reciprocal_rank_fusion


def sample_corpus():
    corpus = ResearchCorpus.__new__(ResearchCorpus)
    corpus.snapshot_id = "corpus-test"
    corpus.documents = {"doc-test": SimpleNamespace(ts_code="002371.SZ", reporting_period="2024", document_type="ANNUAL_REPORT_FULL", published_on=date(2025, 4, 25), sha256="a" * 64)}
    text = "合并利润表\n单位:元\n营业收入 123.45\n" + "长行" * 700 + "\n最后一行必须保留"
    digest = hashlib.sha256(text.encode()).hexdigest()
    page = DocumentPage(page_id="doc-test:p0001:" + digest[:16], document_id="doc-test", company_id="002371.SZ", pdf_page=1,
                        raw_text=text, normalized_text=text, text_sha256=digest, parser_name="pdfplumber", parser_version="test-1", extraction_status="TEXT_AVAILABLE")
    corpus.pages = {page.page_id: page}
    return corpus


def query(**updates):
    return RetrievalQuery.model_validate(dict(question="营业收入是多少", company_id="002371.SZ", reporting_year=2024, as_of_date="2025-04-25", corpus_snapshot_id="corpus-test", **updates))


def config():
    return dict(max_chunk_characters=400, overlap_lines=2, chunker_version="exact-span-line-window-v2", custom_terms=["营业收入"], query_expansions={}, candidate_k=20, rrf_k=60)


def test_chunk_long_lines_preserve_tail_and_exact_locators():
    corpus = sample_corpus()
    chunks = chunk_pages(corpus, config())
    text = next(iter(corpus.pages.values())).normalized_text
    covered = set()
    for c in chunks:
        assert c.text == text[c.character_start:c.character_end]
        assert len(c.text) <= 400
        assert c.line_start == text[:c.character_start].count("\n") + 1
        assert c.line_end == text[:c.character_end - 1].count("\n") + 1
        covered.update(range(c.character_start, c.character_end))
    assert len(covered) == len(text)
    assert "最后一行必须保留" in chunks[-1].text
    assert chunks == chunk_pages(corpus, config())


@pytest.mark.parametrize("company,year,cutoff", [("688072.SH", 2024, "2025-04-25"), ("002371.SZ", 2023, "2025-04-25"), ("002371.SZ", 2024, "2025-04-24")])
def test_no_eligible_document_never_calls_vector(company, year, cutoff):
    corpus = sample_corpus()
    engine = MultiDocumentRetriever(corpus, chunk_pages(corpus, config()), config(), {}, None)
    q = query().model_copy(update={"company_id": company, "reporting_year": year, "as_of_date": date.fromisoformat(cutoff)})
    for method in ("keyword", "bm25", "vector", "hybrid"):
        result = engine.retrieve(q, method)
        assert result.status == "NO_ELIGIBLE_DOCUMENT"
        assert result.hits == []


def test_wrong_snapshot_and_missing_scope_rejected():
    corpus = sample_corpus()
    with pytest.raises(ValueError, match="快照"):
        corpus.eligible_documents(query().model_copy(update={"corpus_snapshot_id": "other"}))
    with pytest.raises(ValidationError):
        RetrievalQuery(question="营业收入", company_id="002371.SZ", as_of_date="2025-04-25", corpus_snapshot_id="corpus-test")


def test_bm25_returns_unique_pages_and_original_text():
    corpus = sample_corpus()
    engine = MultiDocumentRetriever(corpus, chunk_pages(corpus, config()), config(), {})
    result = engine.retrieve(query(), "bm25")
    assert len(result.hits) == 1
    assert "最后一行必须保留" in result.hits[0].parent_text


def test_rrf_ignores_incomparable_raw_scores_and_duplicates():
    first, origins = reciprocal_rank_fusion({"a": [("x", 1e6), ("y", 1)], "b": [("y", -999), ("x", -1e6)]})
    assert first[0][0] == "x"  # tie breaks by stable ID
    assert first[0][1] == first[1][1]
    assert origins["x"] == {"a": 1, "b": 2}
    assert reciprocal_rank_fusion({"a": [("x", 10), ("x", 10)]})[0] == [("x", 1 / 61)]


def test_tampered_chunk_metadata_and_duplicate_ids_rejected():
    corpus = sample_corpus()
    chunks = chunk_pages(corpus, config())
    with pytest.raises(ValueError, match="元数据"):
        MultiDocumentRetriever(corpus, [chunks[0].model_copy(update={"company_id": "688072.SH"})], config(), {})
    with pytest.raises(ValueError, match="重复"):
        MultiDocumentRetriever(corpus, [chunks[0], chunks[0]], config(), {})


def test_wrapped_labels_join_and_statement_scope_come_from_headings():
    corpus = sample_corpus()
    cfg = config() | {"join_wrapped_metric_labels": True, "statement_scoped_sparse": True}
    chunks = chunk_pages(corpus, cfg)
    engine = MultiDocumentRetriever(corpus, chunks, cfg, {})
    assert engine._tokens("营 业\n收 入") == engine._tokens("营业收入")
    assert engine.chunk_statement[chunks[0].chunk_id] == "合并利润表"
    assert engine.retrieve(query().model_copy(update={"question": "合并现金流量表 营业收入"}), "bm25").hits == []
