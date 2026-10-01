from finresearch.retrieval.evaluation import score_case, aggregate_scores
from finresearch.contracts.retrieval import MultiDocumentResult
from test_retrieval_contract import query


def test_missing_results_keep_all_necessary_pages_in_denominator():
    case = {"case_id": "split-table", "document_id": "doc-test", "necessary_pages": [{"pdf_page": 1, "excerpts": ["表头"]}, {"pdf_page": 2, "excerpts": ["数值"]}], "answer_pages": [2]}
    result = MultiDocumentResult(query=query(), method="bm25", status="NO_EVIDENCE", eligible_documents=["doc-test"], candidate_chunk_ids=[], hits=[], elapsed_ms=1)
    scored = score_case(case, result)
    assert scored["recall_at_5"] == 0
    assert not scored["expanded_complete"]
    assert scored["missing_necessary_pages"] == [1, 2]
    assert aggregate_scores([scored])["case_count"] == 1
