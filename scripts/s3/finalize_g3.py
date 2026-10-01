"""Recompute G3 on current sources and real persistent indexes."""
from __future__ import annotations
import argparse
from datetime import date
from decimal import Decimal
import json
import os
from pathlib import Path
import subprocess
import sys
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT / "src"))
from finresearch.contracts.retrieval import RetrievalQuery
from finresearch.retrieval.corpus import ResearchCorpus,chunk_pages,file_sha256,write_json
from finresearch.retrieval.vector_index import LocalEmbedding,VectorIndex
from finresearch.retrieval.multi_document import MultiDocumentRetriever,reciprocal_rank_fusion
from finresearch.retrieval.evaluation import score_case,aggregate_scores
from finresearch.finance.research_tables import extract_annual_observations
from finresearch.verification.financial_artifacts import verify_s2_artifacts


def read(path):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--attempt-id",required=True)
    parser.add_argument("--evaluation-attempt",required=True)
    args=parser.parse_args()
    if not all(s.replace("-","").isalnum() for s in (args.attempt_id,args.evaluation_attempt)):
        raise ValueError("不安全的运行ID")
    run=ROOT / "runs/s3" / args.attempt_id
    run.mkdir(parents=True,exist_ok=False)
    config=read("configs/s3/retrieval.json")
    ranking=read("configs/s3/ranking_v2.json")
    corpus_path=ROOT / "storage/s3/corpora" / config["corpus_snapshot_id"] / "ready.json"
    corpus=ResearchCorpus(ROOT,json.loads(corpus_path.read_text(encoding="utf-8")))
    labels=read("evals/dev/s3_cases.json")
    cases=labels["cases"]
    chunks=chunk_pages(corpus,config)
    embedding=LocalEmbedding(ROOT,config)
    evaluation=ROOT / "runs/s3" / args.evaluation_attempt
    lock=json.loads((evaluation / "inputs.lock.json").read_text(encoding="utf-8"))
    locked_current=(lock["corpus_sha256"]==file_sha256(corpus_path)
        and lock["labels_sha256"]==file_sha256(ROOT / "evals/dev/s3_cases.json")
        and lock["retrieval_config_sha256"]==file_sha256(ROOT / "configs/s3/ranking_v2.json")
        and all(file_sha256(ROOT / name)==sha for name,sha in lock["source_files"].items()))
    outputs={}
    boundaries=[]
    for name in ("bge-v1-attempt01","bge-rebuild01"):
        index=VectorIndex(ROOT / "indexes/s3" / name,embedding,chunks,config)
        try:
            engine=MultiDocumentRetriever(corpus,chunks,ranking,read("configs/s1/retrieval_terms.json"),index)
            scores={}
            ranks={}
            for method in ("keyword","bm25","vector","hybrid"):
                rows=[]
                for case in cases:
                    query=RetrievalQuery.model_validate({k:case[k] for k in ("question","company_id","reporting_year","as_of_date","corpus_snapshot_id")})
                    result=engine.retrieve(query,method)
                    rows.append(score_case(case,result))
                    ranks[f"{method}/{case['case_id']}"]=[h.chunk.parent_page_id for h in result.hits]
                scores[method]=aggregate_scores(rows)
                for company,year,cutoff in (("000001.SZ",2024,"2025-04-30"),("002371.SZ",2023,"2025-04-30"),("002371.SZ",2024,"2025-04-24")):
                    q=RetrievalQuery(company_id=company,reporting_year=year,as_of_date=cutoff,question="营业收入是多少",corpus_snapshot_id=corpus.snapshot_id)
                    r=engine.retrieve(q,method)
                    boundaries.append({"index":name,"method":method,"company":company,"year":year,"cutoff":cutoff,"passed":r.status=="NO_ELIGIBLE_DOCUMENT" and not r.hits})
                try:
                    engine.retrieve(query.model_copy(update={"corpus_snapshot_id":"unregistered"}),method)
                    rejected=False
                except ValueError:
                    rejected=True
                boundaries.append({"index":name,"method":method,"boundary":"WRONG_SNAPSHOT","passed":rejected})
            outputs[name]={"metrics":scores,"ranks":ranks}
        finally:
            index.close()
    for status in ("EXTRACTED","FAILED"):
        altered=json.loads(corpus_path.read_text(encoding="utf-8"))
        altered["documents"][0]["status"]=status
        try:
            ResearchCorpus(ROOT,altered)
            rejected=False
        except ValueError:
            rejected=True
        boundaries.append({"boundary":f"NON_READY_{status}","passed":rejected})
    expected=read(f"runs/s3/{args.evaluation_attempt}/metrics.json")
    names=("case_count","recall_at_5","hit_at_5","mrr_at_5","primary_complete_at_5","expanded_complete_at_5","filter_violation_count")
    fresh_metrics_match=all(outputs["bge-v1-attempt01"]["metrics"][m][k]==expected[m][k] for m in expected for k in names)
    rebuild=read(f"storage/s3/corpora/{corpus.snapshot_id}/rebuild01/rebuild_checks.json")
    rebuild_ok=(all(x["pages_match"] for x in rebuild["documents"]) and rebuild["same_chunks"] and rebuild["same_vectors"]
        and rebuild["source_corpus_sha256"]==file_sha256(corpus_path)
        and outputs["bge-v1-attempt01"]["ranks"]==outputs["bge-rebuild01"]["ranks"])
    observations,_,_=extract_annual_observations(corpus,read("configs/s4/financial_source_map.json"),read("configs/s2/metric_dictionary.json"),date(2025,4,30))
    b0=ROOT / "runs/s3/s3-b0-live-20260930-01"
    b0_numeric=[]
    for company in sorted({o.company_id for o in observations}):
        response=json.loads((b0 / "cases" / f"S3-{company}-2024-REVENUE" / "model_response.json").read_text(encoding="utf-8"))["answer"]
        wanted=[o for o in observations if o.company_id==company and o.metric_id=="revenue"]
        text=response["answer"]
        b0_numeric.append({"company":company,"passed":all(f"{o.standard_value:,.2f}" in text and str(o.fiscal_year) in text for o in wanted),
            "review_scope":"Exact amounts + year mentions verified; ordinal annual-column semantics manually reviewed in execution record, not generic NLI."})
    audits=[json.loads(line) for line in (b0 / "model_attempts.jsonl").read_text(encoding="utf-8").splitlines()]
    b0_summary=json.loads((b0 / "summary.json").read_text(encoding="utf-8"))
    b0_ok=(len(audits)==6==b0_summary["call_count"] and all(a["usage"]["total_tokens"] and a["status"]=="PARSED" for a in audits)
        and sum(Decimal(a["estimated_cost_usd"]) for a in audits)==Decimal(b0_summary["accounted_cost_usd"]) and all(x["passed"] for x in b0_numeric))
    foundation=verify_s2_artifacts(ROOT,ROOT / "runs/s2/s2-repair-observations-20260930-04",ROOT / "runs/s2/s2-repair-comparison-20260930-03")
    test=subprocess.run([sys.executable,"-m","pytest","tests","-q"],cwd=ROOT,capture_output=True,text=True,encoding="utf-8",errors="replace")
    (run / "pytest.txt").write_text(test.stdout+test.stderr,encoding="utf-8")
    dependency=subprocess.run([sys.executable,"-m","pip","check"],cwd=ROOT,capture_output=True,text=True,encoding="utf-8",errors="replace")
    (run / "pip_check.txt").write_text(dependency.stdout+dependency.stderr,encoding="utf-8")
    asset_paths=[corpus_path,ROOT / "evals/dev/s3_cases.json",ROOT / "configs/s3/retrieval.json",ROOT / "configs/s3/ranking_v2.json",ROOT / "environment/requirements.lock.txt"]
    for entry in corpus.registry["documents"]:
        asset_paths += [ROOT / entry["manifest"]["local_path"],ROOT / entry["snapshot"]["pages_path"],ROOT / entry["snapshot"]["snapshot_manifest_path"]]
    sources={p.relative_to(ROOT).as_posix():file_sha256(p) for folder in ("retrieval","contracts","ingestion") for p in sorted((ROOT / "src/finresearch" / folder).glob("*.py"))}
    write_json(run / "inputs.lock.json",{"assets":{p.relative_to(ROOT).as_posix():file_sha256(p) for p in asset_paths},"source_files":sources,
        "runner_sha256":file_sha256(Path(__file__)),"model_lock":embedding.model_lock})
    actual_credential=os.environ.get("DEEPSEEK_API_KEY","")
    scans=[p for p in b0.rglob("*") if p.is_file() and p.suffix in {".json",".jsonl"}]
    leaks=[p.relative_to(ROOT).as_posix() for p in scans if actual_credential and actual_credential in p.read_text(encoding="utf-8")]
    best=outputs["bge-v1-attempt01"]["metrics"]["bm25"]
    labels_ok=all(all(all(excerpt in next(p.normalized_text for p in corpus.pages.values() if p.document_id==c["document_id"] and p.pdf_page==page["pdf_page"])
        for excerpt in page["excerpts"]) for page in c["necessary_pages"]) for c in cases)
    handoff="\n".join(["# S3向S4交接", "", f"语料：{corpus.snapshot_id}；4份年报、997页、{len(chunks)}个精确跨度子块。",
        "默认检索：configs/s3/ranking_v2.json中的BM25。RRF和纯向量保留为对照，重排序关闭。",
        "S4必须锁定公司/年度/截止日/语料快照；使用configs/s4/financial_source_map.json校验的三项合并指标。",
        "开发20题的BM25召回1.0、MRR约0.829；只描述当前开发结果，不宣称盲测准确率。",
        "公司解释只作为公司披露；候选解释保留支持需求和削弱条件，不因检索命中解除独立验证缺口。",
        "G1旧自由文本与历史费用仍NO_GO；不得继承旧语义通过。S4采用批准Claim+受控渲染并单独验收。",
        "本交接只有同目录gate_report.json裁决GO且所有输入锁匹配时才能启用；无OCR、自动采集、自主补查。", ""])
    (run / "handoff_to_s4.md").write_text(handoff,encoding="utf-8",newline="\n")
    tests_ok=test.returncode==0 and dependency.returncode==0
    checks={"G3-C1":len(corpus.documents)==4 and len({r.ts_code for r in corpus.documents.values()})==3,
        "G3-I1":rebuild_ok,"G3-F1":all(b["passed"] for b in boundaries) and all(v["filter_violation_count"]==0 for v in outputs["bge-v1-attempt01"]["metrics"].values()),
        "G3-K1":bool(chunks) and max(embedding.token_lengths([c.text for c in chunks]))<=512,
        "G3-S1":bool(ranking["custom_terms"]) and ranking["tokenizer_version"] and locked_current,
        "G3-V1":bool(embedding.model_lock["files"]),"G3-H1":tests_ok and reciprocal_rank_fusion({"a":[("x",10),("x",999)]})[0]==[("x",1/61)],
        "G3-E1":len(cases)>=12 and labels_ok and all(c["source_review_status"]=="SOURCE_TEXT_CHECKED" for c in cases),
        "G3-E2":best["recall_at_5"]>=.85 and best["mrr_at_5"]>=.65 and best["expanded_complete_at_5"]>=.85,
        "G3-O1":locked_current and fresh_metrics_match and b0_ok and not leaks and tests_ok,
        "G3-R1":not ranking.get("reranker_enabled",False),"G3-HO":all(foundation[k] for k in ("source_verified","calculations_recomputed","database_replayed"))}
    checks={k:bool(v) for k,v in checks.items()}
    write_json(run / "boundary_checks.json",boundaries)
    write_json(run / "recomputed_retrieval.json",outputs)
    write_json(run / "b0_review.json",{"numeric":b0_numeric,"complete_call_audit":b0_ok,"textual_cases":"NEEDS_REVIEW; no causal certification"})
    write_json(run / "foundation_replay.json",foundation)
    write_json(run / "gate_report.json",{"gate":"G3","decision":"GO" if all(checks.values()) else "NO_GO","all_checks_passed":all(checks.values()),"checks":checks,
        "default_method":"bm25","scope":"Four official annual reports; three companies; 2023/2024 document years; 20 development questions, not holdout or exhaustive disclosure coverage.",
        "corpus_sha256":file_sha256(corpus_path),"ranking_config_sha256":file_sha256(ROOT / "configs/s3/ranking_v2.json"),"retrieval_source_files":sources,
        "upstream_g1":"REOPENED_NO_GO for legacy free-text semantic/cost audit; G3 certifies retrieval only and does not inherit it.",
        "limitations":["开发集调优后表现，不是泛化准确率","解释问答保留NEEDS_REVIEW","无重排序/OCR/自动采集","S4受控Claim仍需单独验收"]})
    print(json.dumps({"decision":"GO" if all(checks.values()) else "NO_GO","failed":[k for k,v in checks.items() if not v]}),flush=True)


if __name__=="__main__":
    main()
