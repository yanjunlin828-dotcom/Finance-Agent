"""Audit two actual S4 runs against current sources, arithmetic and checkpoints."""
from __future__ import annotations
import argparse
from decimal import Decimal
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT / "src"))
from langgraph.checkpoint.sqlite import SqliteSaver
from finresearch.contracts import MetricObservation,CalculationResult,EvidenceCandidate,stable_sha256
from finresearch.contracts.research import ResearchContext,ResearchClaim,ResearchGap,HypothesisBatch,DisclosureBatch
from finresearch.finance.research_tables import extract_annual_observations
from finresearch.ingestion.pdf_pages import validate_evidence_pages
from finresearch.model.deepseek_json import conservative_cost
from finresearch.retrieval.corpus import ResearchCorpus,file_sha256,write_json
from finresearch.workflow.research_analysis import calculate_research,detect_phenomena,calculation_claims
from finresearch.workflow.research_claims import review_hypotheses,review_disclosures,attach_hypothesis_disclosures,validate_report
from finresearch.workflow.fixed_research import ResearchDependencies,compile_research_graph


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def audit_run(name):
    folder=ROOT / "runs/s4" / name
    state=read(folder / "final_state.json")
    context=ResearchContext.model_validate(read(folder / "context.json"))
    lock=read(folder / "inputs.lock.json")
    config=read(folder / "model_config.json")
    protocol=read(ROOT / "protocols/revenue_quality_v1.json")
    errors=[]
    checks={}
    checks["current_input_lock"]=(all(file_sha256(ROOT / item["path"])==item["sha256"] for item in lock["inputs"].values())
        and all(file_sha256(ROOT / path)==digest for path,digest in lock["source_files"].items())
        and file_sha256(ROOT / "scripts/s4/run_research.py")==lock["runner_sha256"]
        and file_sha256(folder / "model_config.json")==lock["model_config_sha256"]
        and file_sha256(ROOT / "environment/requirements.lock.txt")==lock["dependency_lock_sha256"])
    corpus=ResearchCorpus(ROOT,read(ROOT / lock["inputs"]["corpus"]["path"]))
    fresh,evidence,source_checks=extract_annual_observations(corpus,read(ROOT / lock["inputs"]["mapping"]["path"]),read(ROOT / lock["inputs"]["dictionary"]["path"]),context.as_of_date)
    observations=[MetricObservation.model_validate(o) for o in read(folder / "observations.json")]
    checks["financial_source_replay"]=(observations==fresh and len(fresh)==18 and all(c["passed"] for c in source_checks))
    calculated,financial_gaps=calculate_research(context,fresh)
    serialized={c:{n:v.model_dump(mode="json") for n,v in rows.items()} for c,rows in calculated.items()}
    checks["registered_calculation_replay"]=serialized==read(folder / "calculations.json")==state["calculations"]
    # Independent arithmetic: do not call the registered formula functions here.
    amounts={(o.company_id,o.metric_id,o.fiscal_year):o.standard_value for o in fresh}
    direct=[]
    for company,rows in calculated.items():
        expected={}
        for metric in ("revenue","operating_cash_flow_net","accounts_receivable"):
            previous=amounts[(company,metric,2023)]
            current=amounts[(company,metric,2024)]
            expected[f"{metric}_growth"]=("NEGATIVE_BASE",None) if previous<0 else ("VALID",(current-previous)/previous)
        for year in (2023,2024):
            expected[f"ocf_revenue_ratio_{year}"]=("VALID",amounts[(company,"operating_cash_flow_net",year)]/amounts[(company,"revenue",year)])
            expected[f"ar_revenue_ratio_{year}"]=("VALID",amounts[(company,"accounts_receivable",year)]/amounts[(company,"revenue",year)])
        rev,cash=expected["revenue_growth"],expected["operating_cash_flow_net_growth"]
        expected["revenue_ocf_growth_gap_pp"]=("VALID",(rev[1]-cash[1])*100) if rev[0]==cash[0]=="VALID" else ("INCOMPARABLE",None)
        direct.extend(rows[n].status==status and rows[n].value==value for n,(status,value) in expected.items())
    checks["independent_decimal_math"]=len(direct)==24 and all(direct)
    phenomena=detect_phenomena(calculated,context.fiscal_years)
    checks["phenomenon_replay"]=[p.model_dump(mode="json") for p in phenomena]==read(folder / "phenomena.json")
    all_evidence=[EvidenceCandidate.model_validate(e) for e in read(folder / "evidence.json")]
    validate_evidence_pages(all_evidence,list(corpus.pages.values()))
    checks["eligible_evidence"]=all(corpus.documents[e.document_id].ts_code==e.company_id and corpus.documents[e.document_id].published_on<=context.as_of_date for e in all_evidence)
    approved=calculation_claims(calculated)
    gaps=list(financial_gaps)
    for item in read(folder / "hypotheses.json"):
        _,claims,missing=review_hypotheses(HypothesisBatch.model_validate(item["batch"]),item["company_id"],phenomena)
        approved.extend(claims);gaps.extend(missing)
    by_evidence={e.evidence_id:e for e in all_evidence}
    batches=[DisclosureBatch.model_validate(b) for b in read(folder / "disclosures.json")]
    for batch in batches:
        claims,missing=review_disclosures(batch,batch.company_id,by_evidence)
        approved.extend(claims);gaps.extend(missing)
    approved=attach_hypothesis_disclosures(approved,batches,by_evidence)
    for gap in gaps:
        approved.append(ResearchClaim(claim_id=f"claim-{gap.gap_id}",company_id=gap.company_id,kind="UNRESOLVED",text=gap.description,status="APPROVED",limitations=[gap.suggested_next_step]))
    checks["claim_replay"]=[c.model_dump(mode="json") for c in approved]==read(folder / "claims.json")==state["claims"]
    checks["gap_replay"]=[g.model_dump(mode="json") for g in gaps]==read(folder / "gaps.json")==state["gaps"]
    checks["bounded_attributed_disclosures"]=(len(batches)==3 and all(b.selected for b in batches)
        and all(q.exact_quote.endswith(tuple("。！？")) and len(q.exact_quote)<=250 for b in batches for q in b.selected)
        and all(c.text.startswith("公司披露：") for c in approved if c.kind=="DISCLOSED"))
    report=(folder / "report.md").read_text(encoding="utf-8")
    validated=validate_report(report,context,approved,gaps,state["writer_ids"],state["execution_status"],observations=fresh,calculations=calculated,evidence=all_evidence,source_links={d:r.source_url for d,r in corpus.documents.items()})
    checks["report_replay"]=validated["status"]=="PASS" and report==state["report"]
    checks["fixed_path"]=state["trace"]==protocol["nodes"] and context.protocol_config_sha256==stable_sha256(protocol)
    deps=ResearchDependencies(protocol,lambda c:[],lambda *a:None,lambda *a:[],lambda *a:None,lambda *a:None)
    runtime=ROOT / "storage/s4/runtime" / name
    with SqliteSaver.from_conn_string(str(runtime / "checkpoints.sqlite")) as saver:
        graph=compile_research_graph(deps,saver)
        cfg={"configurable":{"thread_id":name}}
        saved=graph.get_state(cfg)
        history=list(graph.get_state_history(cfg))
        checks["checkpoint_reopened"]=saved.values==state and not saved.next and len(history)>=len(protocol["nodes"])
    audits=[json.loads(line) for line in (folder / "model_attempts.jsonl").read_text(encoding="utf-8").splitlines()]
    costs=[conservative_cost(a["usage"],config) for a in audits]
    checks["live_model_and_cost_audit"]=(len(audits)==7 and all(a["status"]=="PARSED" and a["content"] for a in audits)
        and all(c is not None and Decimal(c)==Decimal(a["estimated_cost_usd"]) for c,a in zip(costs,audits))
        and [a["budget_reservation"]["total_calls"] for a in audits]==list(range(1,8)))
    with sqlite3.connect(runtime / "budget.sqlite") as conn:
        config_sha,payload=conn.execute("SELECT config_sha,payload FROM budget WHERE id=1").fetchone()
        budget=json.loads(payload)
    checks["persisted_budget"]=(config_sha==stable_sha256(config) and budget["total_calls"]==7 and not budget["pending"]
        and Decimal(budget["reserved_cost"])==sum((Decimal(c) for c in costs if c is not None),Decimal(0))
        and Decimal(budget["reserved_cost"])<=Decimal(config["currency_limit"]))
    with sqlite3.connect(runtime / "metrics.sqlite") as conn:
        stored_obs={r[0] for r in conn.execute("SELECT payload_json FROM metric_observations")}
        stored_calcs={r[0] for r in conn.execute("SELECT payload_json FROM calculation_results")}
    checks["financial_database_reopened"]=(stored_obs=={o.model_dump_json() for o in fresh} and stored_calcs=={c.model_dump_json() for rows in calculated.values() for c in rows.values()})
    checks["actual_success"]=(state["execution_status"]=="COMPLETED" and read(folder / "summary.json")["status"]=="COMPLETED" and not any(g.gap_type=="MODEL_FAILURE" for g in gaps))
    actual_key=os.environ.get("DEEPSEEK_API_KEY","")
    checks["no_credentials"]=(not actual_key or not any(actual_key in p.read_text(encoding="utf-8") for p in folder.rglob("*") if p.is_file() and p.suffix in {".json",".jsonl",".md"}))
    for key,passed in checks.items():
        if not passed:errors.append(key)
    return {"run_id":name,"checks":checks,"errors":errors,"trace":state["trace"],"calculations":serialized,
        "cost_usd":budget["reserved_cost"],"claim_count":len(approved),"gap_count":len(gaps)}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--attempt-id",required=True)
    parser.add_argument("--live-attempts",nargs=2,required=True)
    args=parser.parse_args()
    if not all(s.replace("-","").isalnum() for s in [args.attempt_id,*args.live_attempts]):raise ValueError("不安全ID")
    folder=ROOT / "runs/s4" / args.attempt_id
    folder.mkdir(parents=True,exist_ok=False)
    audits=[audit_run(name) for name in args.live_attempts]
    same=(audits[0]["trace"]==audits[1]["trace"] and audits[0]["calculations"]==audits[1]["calculations"])
    test=subprocess.run([sys.executable,"-m","pytest","tests","-q"],cwd=ROOT,capture_output=True,text=True,encoding="utf-8",errors="replace")
    (folder / "pytest.txt").write_text(test.stdout+test.stderr,encoding="utf-8")
    passed=all(not a["errors"] for a in audits) and same and test.returncode==0
    write_json(folder / "run_audits.json",audits)
    write_json(folder / "gate_report.json",{"gate":"G4","decision":"GO" if passed else "NO_GO","all_checks_passed":passed,
        "run_ids":args.live_attempts,"same_required_path_and_financial_results":same,"offline_tests_passed":test.returncode==0,
        "scope":"3 companies; 2023/2024; 3 consolidated metrics; fixed queries; typed candidates; literal quotes; approved-ID writer; basic durable state.",
        "semantic_approval_policy":"Computed facts independently recalculated; disclosures only certify exact source attribution; inference templates explicitly qualified, relation labels await review and independent causal confirmation remains unresolved.",
        "limitations":["不批准旧G1自由文本解释或历史费用完整性","无通用语义或因果认证","开发材料范围有限","S5自主补查与S6完整恢复/取消尚未实施"],
        "accounted_successful_run_cost_usd":str(sum((Decimal(a["cost_usd"]) for a in audits),Decimal(0))),
        "failed_runs_retained":["s4-b1-live-20260930-01"],"verifier_sha256":file_sha256(Path(__file__))})
    print(json.dumps({"decision":"GO" if passed else "NO_GO","run_errors":{a["run_id"]:a["errors"] for a in audits}}),flush=True)


if __name__=="__main__":main()
