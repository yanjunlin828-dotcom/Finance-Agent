"""Bind S7 measurement and fresh core requalification, retaining release limits."""
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
from finresearch_evals.gates import bind_gate

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    for name in ('suite-id','repair-id','gate-id'):parser.add_argument('--'+name,required=True)
    args=parser.parse_args()
    result=bind_gate(ROOT,args.suite_id,args.repair_id,args.gate_id)
    print(json.dumps({k:result[k] for k in ('decision','engineering_gate','measurement_gate','full_release_gate')}))
