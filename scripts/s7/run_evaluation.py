"""Execute S7 frozen regression; no parent quant strategy/test-set access."""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
from finresearch_evals.runner import run_suite


def main():
    parser=argparse.ArgumentParser(description="S7限定开发评测，OFFLINE不冒充LIVE，保留全部失败")
    parser.add_argument('--run-id',required=True)
    parser.add_argument('--mode',choices=['OFFLINE','LIVE'],required=True)
    args=parser.parse_args()
    summary=run_suite(ROOT,args.run_id,args.mode,notify=lambda item:print(json.dumps(item,ensure_ascii=False),flush=True))
    print(json.dumps({k:summary[k] for k in ('measurement_gate','full_release_gate','product_decision','new_model_calls_total','new_estimated_cost_usd_total')},ensure_ascii=False))


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    main()
