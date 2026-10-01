"""Read-only final completion audit; does not perform paid model calls."""
import argparse
import ast
import importlib.util
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT / "src"))
from finresearch.retrieval.corpus import file_sha256,write_json,ResearchCorpus,chunk_pages
from finresearch.retrieval.vector_index import LocalEmbedding,VectorIndex


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--attempt-id",required=True)
    args=parser.parse_args()
    if not args.attempt_id.replace("-","").isalnum():raise ValueError("不安全ID")
    folder=ROOT / "runs/s4" / args.attempt_id
    folder.mkdir(parents=True,exist_ok=False)
    def read(path):return json.loads((ROOT / path).read_text(encoding="utf-8"))
    g3=read("runs/s3/s3-gate-20260930-01/gate_report.json")
    g4=read("runs/s4/s4-gate-20260930-01/gate_report.json")
    lock=read("runs/s3/s3-gate-20260930-01/inputs.lock.json")
    checks={"g3_decision":g3["decision"]=="GO" and all(g3["checks"].values()),
        "g4_decision":g4["decision"]=="GO" and g4["all_checks_passed"],
        "current_g3_inputs":all(file_sha256(ROOT / name)==sha for group in ("assets","source_files") for name,sha in lock[group].items()),
        "current_g4_verifier":file_sha256(ROOT / "scripts/s4/finalize_g4.py")==g4["verifier_sha256"]}
    config=read("configs/s3/retrieval.json")
    corpus=ResearchCorpus(ROOT,read(f"storage/s3/corpora/{config['corpus_snapshot_id']}/ready.json"))
    chunks=chunk_pages(corpus,config)
    embedding=LocalEmbedding(ROOT,config)
    for name in ("bge-v1-attempt01","bge-rebuild01"):
        index=VectorIndex(ROOT / "indexes/s3" / name,embedding,chunks,config)
        try:checks[f"current_index_{name}"]=index.lock["chunk_count"]==3096
        finally:index.close()
    spec=importlib.util.spec_from_file_location("s4_completion_verifier",ROOT / "scripts/s4/finalize_g4.py")
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    actual=[module.audit_run(name) for name in g4["run_ids"]]
    checks["current_s4_content"]=all(not row["errors"] for row in actual)
    required={"S3.1":["05_S3_详细设计与AI执行计划.md","07_S3_S4_实施顺序与验收清单.md","environment/requirements.lock.txt"],
        "S3.2":["storage/s3/document_manifest.jsonl",f"storage/s3/corpora/{config['corpus_snapshot_id']}/ready.json"],
        "S3.3":["src/finresearch/contracts/retrieval.py","src/finresearch/retrieval/corpus.py","tests/s3/test_retrieval_contract.py"],
        "S3.4":["configs/s3/ranking_v2.json","src/finresearch/retrieval/multi_document.py"],
        "S3.5":["src/finresearch/retrieval/vector_index.py","tests/s3/test_vector_persistence.py"],
        "S3.6":["tests/s3/test_retrieval_contract.py","src/finresearch/retrieval/multi_document.py"],
        "S3.7":["evals/dev/s3_cases.json","runs/s3/s3-eval-20260930-04/metrics.json","runs/s3/s3-b0-live-20260930-01/model_attempts.jsonl"],
        "S3.8":["runs/s3/s3-gate-20260930-01/gate_report.json","runs/s3/s3-gate-20260930-01/handoff_to_s4.md"],
        "S4.1":["08_S4_详细设计与AI执行计划.md","protocols/revenue_quality_v1.json","src/finresearch/contracts/research.py"],
        "S4.2":["configs/s4/financial_source_map.json","src/finresearch/finance/research_tables.py","src/finresearch/workflow/research_analysis.py"],
        "S4.3":["runs/s4/s4-b1-live-20260930-05/hypotheses.json"],
        "S4.4":["runs/s4/s4-b1-live-20260930-05/evidence.json","runs/s4/s4-b1-live-20260930-05/queries"],
        "S4.5":["src/finresearch/workflow/research_claims.py","runs/s4/s4-b1-live-20260930-05/claims.json","runs/s4/s4-b1-live-20260930-05/gaps.json"],
        "S4.6":["runs/s4/s4-b1-live-20260930-05/report.md","tests/s4/test_claims_and_writer.py","tests/s4/test_quote_selection.py"],
        "S4.7":["src/finresearch/workflow/fixed_research.py","src/finresearch/model/persistent_budget.py","runs/s4/s4-b1-live-20260930-05/checkpoint_reopen.json"],
        "G4_HANDOFF":["runs/s4/s4-gate-20260930-01/gate_report.json","docs/stages/s4/execution_record.md","docs/stages/s4/handoff_to_s5.md","09_S3_S4_完成日志与逐项验收.md"]}
    checks["explicit_deliverables_present"]=all((ROOT / p).exists() for paths in required.values() for p in paths)
    checks["documentation_stage_ledger"]="| S4 | PASSED / GO" in (ROOT / "01_分阶段实施路线与验收计划.md").read_text(encoding="utf-8")
    python_files=[p for base in ("src","scripts","tests") for p in (ROOT / base).rglob("*.py")]
    whitespace=[]
    for path in python_files:
        source=path.read_text(encoding="utf-8-sig")
        ast.parse(source,filename=str(path))
        whitespace.extend(f"{path.relative_to(ROOT)}:{i}" for i,line in enumerate(source.splitlines(),1) if line.rstrip()!=line)
    checks["source_syntax_and_whitespace"]=not whitespace
    write_json(folder / "completion_audit.json",{"objective":"按计划高质量实现S3和S4，完成当前冻结范围的验收与交接", "passed":all(checks.values()),
        "checks":checks,"requirements_evidence":required,"s4_content_rechecks":actual,"parsed_python_files":len(python_files),"whitespace_errors":whitespace,
        "scope_limits":["三个公司两个财年三项指标","仅批准受控报告，不批准旧G1自由文本语义","S5-S8尚未实现","未运行中证500测试集"],
        "offline_test_evidence":"runs/s4/s4-gate-20260930-01/pytest.txt: 156 passed","verifier_sha256":file_sha256(Path(__file__))})
    print(json.dumps({"passed":all(checks.values()),"failed_checks":[k for k,v in checks.items() if not v]}),flush=True)


if __name__=="__main__":main()
