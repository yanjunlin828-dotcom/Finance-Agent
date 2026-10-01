"""Recheck retained process evidence after correcting the lossless codec auditor.

No process or supplier call is replayed. Only the capsule equality check changes;
all original fault outcomes, actions, source hashes and regression stay bound.
"""
import argparse,json,sys
from copy import deepcopy
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'));sys.path.insert(0,str(ROOT/'scripts/s6'))
from verify_s6_b2 import audit_b2,inspect
from finresearch.storage.session_store import safe_task_id
from finresearch.retrieval.corpus import file_sha256
from finresearch_evals.audit import read_json,write_json

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--original-id',required=True);parser.add_argument('--new-id',required=True)
    args=parser.parse_args()
    original=ROOT/'runs/s7'/safe_task_id(args.original_id)
    old=read_json(original/'gate_report.json')
    for relative,digest in old['current_core_hashes'].items():
        if file_sha256(ROOT/relative)!=digest:raise ValueError('tested core changed; cannot reuse process evidence')
    output=ROOT/'runs/s7'/safe_task_id(args.new_id);output.mkdir(parents=True,exist_ok=False)
    result=deepcopy(old)
    for row in result['b2_faults']:
        fault=row['fault']
        store,task,actions=inspect(row['task_id'])
        if actions!=row['actions_after'] or task['status']!=row['after_status']:
            raise ValueError('process evidence state drift')
        if fault in {'model_pending','cancel_after_tool','waiting_checkpointed'}:
            if not old['checks'][fault]:raise ValueError('non-capsule check failed; cannot promote')
            continue
        retained=read_json(original/('audit_'+fault+'.json'))
        if any(not v for k,v in retained['checks'].items() if k!='capsule_preserves_b2'):
            raise ValueError('other original check failed; cannot promote')
        audit=audit_b2(row['task_id']);write_json(output/('audit_'+fault+'.json'),audit)
        passed=audit['passed'] and task['status']=='PARTIAL'
        if fault.startswith('clarification_'):
            state=read_json(ROOT/'runs/s6'/row['task_id']/'final_state.json')
            passed=passed and next(g for g in state['supplement']['gaps'] if g['gap_id']=='s6-explicit-source-request')['status']=='LIMITED'
        result['checks'][fault]=passed;row['passed']=passed
        print(json.dumps({'rechecked':fault,'passed':passed}),flush=True)
    result['all_checks_passed']=all(result['checks'].values())
    result['decision']='GO' if result['all_checks_passed'] else 'NO_GO'
    result['reused_process_evidence_from']=args.original_id
    result['correction']='Only compare fully decoded gap records, not encoded omission of schema defaults; no change to frozen measurement'
    result['evidence_hashes']={p.relative_to(ROOT).as_posix():file_sha256(p) for p in original.glob('*') if p.is_file()}
    result['audit_verifier_sha256']=file_sha256(ROOT/'scripts/s6/verify_s6_b2.py')
    result['revalidation_verifier_sha256']=file_sha256(Path(__file__))
    write_json(output/'gate_report.json',result)
    print(json.dumps({'decision':result['decision'],'test_totals':result['test_totals']}),flush=True)
    if not result['all_checks_passed']:raise SystemExit(1)

if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8');main()
