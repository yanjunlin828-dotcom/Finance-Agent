"""Read-only S7 post-run usage reconciliation against frozen model assumptions."""
import argparse,json,sqlite3,sys
from decimal import Decimal
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
from finresearch.contracts import stable_sha256
from finresearch.storage.session_store import safe_task_id
from finresearch.model.deepseek_json import conservative_cost
from finresearch_evals.audit import read_json,write_json,validate_freeze
from finresearch_evals.gates import verify_artifact_set
from finresearch.retrieval.corpus import file_sha256

def readonly_rows(path,query):
    with sqlite3.connect(f'file:{path.as_posix()}?mode=ro',uri=True) as conn:return conn.execute(query).fetchall()

def main():
    p=argparse.ArgumentParser();p.add_argument('--suite-id',required=True);p.add_argument('--audit-id',required=True);a=p.parse_args()
    suite_id=safe_task_id(a.suite_id);suite=ROOT/'runs/s7'/suite_id
    validate_freeze(ROOT,read_json(suite/'evaluation.lock.json'))
    verify_artifact_set(suite,read_json(suite/'publication.json'))
    summary=read_json(suite/'summary.json');base=read_json(ROOT/'configs/s1/model.json');controller=read_json(ROOT/'configs/s5/controller.json')
    companies=read_json(suite/'evaluation.lock.json')['contract']['scope']['company_ids']
    rows=[]
    for branch in ('B0','B1','B2_LIVE'):
        config=dict(base)
        if branch=='B0':
            folder=suite/'b0';config=read_json(folder/'model_config.json')
            audits=[json.loads(line) for line in (folder/'model_attempts.jsonl').read_text(encoding='utf-8').splitlines()]
        else:
            folder=ROOT/'storage/s6/tasks'/summary['outcomes'][branch]['task_id']
            events=[json.loads(r[0]) for r in readonly_rows(folder/'session.sqlite','SELECT payload FROM event ORDER BY id')]
            audits=[e['audit'] for e in events if e.get('event')=='MODEL_ATTEMPT']
            if branch=='B1':
                config.update(maximum_total_calls=7,maximum_attempts_per_probe=1,maximum_input_tokens=32000,maximum_output_tokens=3500,
                    currency_limit='0.10',online_probe_ids=[f's6-{c}-{t}' for c in companies for t in ('hypotheses','disclosures')]+['s6-writer'])
            else:
                config.update(maximum_total_calls=controller['maximum_model_calls'],maximum_attempts_per_probe=1,maximum_input_tokens=32000,
                    maximum_output_tokens=1500,currency_limit=controller['maximum_cost_usd'],
                    online_probe_ids=[f'b2-plan-{i}' for i in range(1,controller['maximum_rounds']+1)]+['b2-quotes'])
        config_sha,raw=readonly_rows(folder/'budget.sqlite','SELECT config_sha,payload FROM budget WHERE id=1')[0]
        budget=json.loads(raw);costs=[conservative_cost(x.get('usage'),config) for x in audits]
        amount=sum((Decimal(x) for x in costs if x is not None),Decimal(0))
        checks={'config_exact':config_sha==stable_sha256(config),'call_count_exact':len(audits)==budget['total_calls']==summary['outcomes'][branch]['calls'],
            'all_usage_recorded':all(x is not None for x in costs),'all_calls_parsed':all(x.get('status')=='PARSED' for x in audits),
            'pending_empty':not budget['pending'],'cost_exact':str(amount)==budget['reserved_cost']==summary['outcomes'][branch]['estimated_cost_usd']}
        rows.append({'branch':branch,'checks':checks,'passed':all(checks.values()),'calls':len(audits),
            'reconciled_estimated_cost_usd':str(amount),'budget_sha256':file_sha256(folder/'budget.sqlite')})
    report={'suite_id':suite_id,'all_checks_passed':all(r['passed'] for r in rows),'branches':rows,'supplier_invoice_verified':False,
        'historical_s1_closed':False,'note':'本轮usage与持久预算对账，不声称供应商账单或历史费用完整',
        'source_summary_sha256':file_sha256(suite/'summary.json'),'audit_script_sha256':file_sha256(Path(__file__))}
    output=ROOT/'runs/s7'/safe_task_id(a.audit_id);output.mkdir(parents=True,exist_ok=False)
    write_json(output/'usage_audit.json',report)
    write_json(output/'publication.json',{'files':{'usage_audit.json':file_sha256(output/'usage_audit.json')}})
    print(json.dumps({'all_checks_passed':report['all_checks_passed'],'branches':rows}),flush=True)
    if not report['all_checks_passed']:raise SystemExit(1)

if __name__=='__main__':
    main()
