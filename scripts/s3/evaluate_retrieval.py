"""Run the four methods against frozen labels, retaining every failure."""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from finresearch.contracts.retrieval import RetrievalQuery
from finresearch.retrieval.corpus import ResearchCorpus, chunk_pages, file_sha256, write_json
from finresearch.retrieval.vector_index import LocalEmbedding, VectorIndex
from finresearch.retrieval.multi_document import MultiDocumentRetriever
from finresearch.retrieval.evaluation import score_case, aggregate_scores


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--index-name", required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--ranking-config", default="configs/s3/retrieval.json")
    args = parser.parse_args()
    if not all(s.replace("-", "").isalnum() for s in (args.index_name, args.attempt_id)):
        raise ValueError("运行路径参数不安全")
    config_path = (ROOT / args.ranking_config).resolve()
    if not config_path.is_relative_to(ROOT / "configs/s3"):
        raise ValueError("排序配置必须位于configs/s3")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    index_config = json.loads((ROOT / "configs/s3/retrieval.json").read_text(encoding="utf-8"))
    for key in ("corpus_snapshot_id", "max_chunk_characters", "chunker_version", "overlap_lines", "embedding_model", "embedding_dimension", "distance"):
        if config[key] != index_config[key]:
            raise ValueError("排序实验不能改变索引构建合同；结构变化必须重建索引")
    corpus_path = ROOT / "storage/s3/corpora" / config["corpus_snapshot_id"] / "ready.json"
    corpus = ResearchCorpus(ROOT, json.loads(corpus_path.read_text(encoding="utf-8")))
    chunks = chunk_pages(corpus, config)
    labels_path = ROOT / "evals/dev/s3_cases.json"
    cases = json.loads(labels_path.read_text(encoding="utf-8"))["cases"]
    run = ROOT / "runs/s3" / args.attempt_id
    run.mkdir(parents=True, exist_ok=False)
    write_json(run / "inputs.lock.json", {"corpus_sha256": file_sha256(corpus_path), "labels_sha256": file_sha256(labels_path),
        "retrieval_config_sha256": file_sha256(config_path), "keyword_config_sha256": file_sha256(ROOT / "configs/s1/retrieval_terms.json"),
        "source_files": {p.relative_to(ROOT).as_posix(): file_sha256(p) for p in sorted((ROOT / "src/finresearch/retrieval").glob("*.py"))},
        "index_name": args.index_name, "index_lock_sha256": file_sha256(ROOT / "indexes/s3" / args.index_name / "index.lock.json")})
    index = VectorIndex(ROOT / "indexes/s3" / args.index_name, LocalEmbedding(ROOT, index_config), chunks, index_config)
    try:
        engine = MultiDocumentRetriever(corpus, chunks, config, json.loads((ROOT / "configs/s1/retrieval_terms.json").read_text(encoding="utf-8")), index)
        summary = {}
        all_rows = []
        for method in ("keyword", "bm25", "vector", "hybrid"):
            rows = []
            results = []
            for case in cases:
                query = RetrievalQuery.model_validate({k: case[k] for k in ("question", "company_id", "reporting_year", "as_of_date", "corpus_snapshot_id")})
                result = engine.retrieve(query, method)
                score = score_case(case, result)
                candidate_pages = {engine.by_id[i].pdf_page for i in result.candidate_chunk_ids if engine.by_id[i].document_id == case["document_id"]}
                score["error_type"] = None if score["expanded_complete"] else "RANKING_MISS" if set(case["answer_pages"]) & candidate_pages else "RECALL_MISS"
                rows.append(score)
                all_rows.append({"method": method, **score})
                results.append({"case_id": case["case_id"], "result": result.model_dump(mode="json")})
            write_json(run / f"{method}_results.json", results)
            summary[method] = aggregate_scores(rows)
            print(json.dumps({"method": method, **summary[method]}), flush=True)
        write_json(run / "scores.json", all_rows)
        write_json(run / "metrics.json", summary)
        write_json(run / "summary.json", {"status": "EVALUATED_NOT_GATED", "case_count": len(cases), "methods": list(summary),
            "index_size_bytes": sum(p.stat().st_size for p in (ROOT / "indexes/s3" / args.index_name).rglob("*") if p.is_file())})
    finally:
        index.close()


if __name__ == "__main__":
    main()
