"""Fresh process requalification after the disclosed-source association fix."""
from decimal import Decimal
import argparse
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
sys.path.insert(0,str(ROOT/'scripts/s6'))
from finresearch_evals.audit import read_json,write_json
from finresearch.retrieval.corpus import file_sha256
from verify_s6_b2 import inspect,audit_b2


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--run-id',required=True)
    args=parser.parse_args()
    from finresearch.storage.session_store import safe_task_id
    run_id=safe_task_id(args.run_id)
    output=ROOT/'runs/s7'/run_id
    output.mkdir(parents=True,exist_ok=False)
    hidden=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
    def command(label,argv,expected=0):
        result=subprocess.run([sys.executable,*argv],cwd=ROOT,text=True,encoding='utf-8',errors='replace',capture_output=True,
            timeout=300,creationflags=hidden,env=dict(os.environ,PYTHONIOENCODING='utf-8'))
        (output/(label+'.stdout.txt')).write_text(result.stdout,encoding='utf-8')
        (output/(label+'.stderr.txt')).write_text(result.stderr,encoding='utf-8')
        if result.returncode!=expected:raise RuntimeError(f'{label}: unexpected exit; retained evidence')
        print(json.dumps({'verified':label}),flush=True)
        return result.stdout
    b1_id=run_id+'-b1'
    command('b1_process_and_regression',['scripts/s6/verify_s6.py','--attempt-id',b1_id])
    b1=read_json(ROOT/'runs/s6'/b1_id/'gate_report.json')
    checks={'b1_process_requalified':b1['all_checks_passed']}
    cases=[]
    for fault in ('tool_committed','round_checkpointed','report_written','model_pending','cancel_after_tool','waiting_checkpointed',
                  'clarification_committed','clarification_checkpointed','budget_exhausted'):
        task_id=run_id+'-'+fault.replace('_','-')
        command(fault+'_initial',['scripts/s6/supplement_fault_worker.py','--task-id',task_id,'--fault',fault],
            0 if fault in {'cancel_after_tool','budget_exhausted'} else 91)
        _,before,before_actions=inspect(task_id)
        premature=(ROOT/'runs/s6'/task_id/'publication.json').exists()
        command(fault+'_resume',['scripts/s6/supplement_task.py','resume','--task-id',task_id],
            2 if fault in {'model_pending','cancel_after_tool'} else 0)
        store,after,actions=inspect(task_id)
        if fault=='model_pending':
            with sqlite3.connect(store.path.parent/'budget.sqlite') as conn:budget=json.loads(conn.execute('SELECT payload FROM budget').fetchone()[0])
            passed=after['status']=='BLOCKED' and any(a[1]=='UNKNOWN' and a[2]==1 for a in actions) and budget['total_calls']==1 and bool(budget['pending']) and Decimal(budget['reserved_cost'])==Decimal('0.001')
        elif fault=='cancel_after_tool':
            passed=after['status']=='CANCELLED' and actions==before_actions
        elif fault=='waiting_checkpointed':
            passed=after['status']=='WAITING_INPUT' and actions==before_actions
            command('waiting_new_source',['scripts/s6/supplement_task.py','clarify','--task-id',task_id,'--request-id','new-source',
                '--gap-id','s6-explicit-source-request','--decision','new_snapshot_required'])
            command('waiting_new_source_resume',['scripts/s6/supplement_task.py','resume','--task-id',task_id])
            passed=passed and inspect(task_id)[1]['status']=='WAITING_INPUT'
        else:
            audit=audit_b2(task_id)
            write_json(output/('audit_'+fault+'.json'),audit)
            passed=audit['passed'] and after['status']=='PARTIAL'
            if fault.startswith('clarification_'):
                state=read_json(ROOT/'runs/s6'/task_id/'final_state.json')
                passed=passed and next(g for g in state['supplement']['gaps'] if g['gap_id']=='s6-explicit-source-request')['status']=='LIMITED'
        passed=passed and (not premature or fault=='budget_exhausted')
        checks[fault]=passed
        row={'fault':fault,'task_id':task_id,'passed':passed,'before_status':before['status'],'after_status':after['status'],
             'actions_before':before_actions,'actions_after':actions,'provider_contacted':False,'fresh_process_evidence':True}
        cases.append(row)
        write_json(output/('process_'+fault+'.json'),row)
    report={'gate':'S7_CORE_REPAIR_REQUALIFICATION','decision':'GO' if all(checks.values()) else 'NO_GO','checks':checks,
        'all_checks_passed':all(checks.values()),'b1_fault_count':5,'b2_fault_count':9,'test_totals':b1['test_totals'],
        'b1_gate':str((ROOT/'runs/s6'/b1_id/'gate_report.json').relative_to(ROOT)), 'b2_faults':cases,
        'current_core_hashes':{p.relative_to(ROOT).as_posix():file_sha256(p) for p in (ROOT/'src/finresearch').rglob('*.py')},
        'verifier_sha256':file_sha256(Path(__file__)), 'limitations':['恢复和来源链接工程复验，不等于独立语义或盲测放行','所有故障供应商状态均为合成，无真实模型收费']}
    write_json(output/'gate_report.json',report)
    print(json.dumps({'decision':report['decision'],'tests':report['test_totals']}),flush=True)
    if not all(checks.values()):raise SystemExit(1)


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8');sys.stderr.reconfigure(encoding='utf-8')
    main()
