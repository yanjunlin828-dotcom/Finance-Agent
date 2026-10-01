"""Private worker entry; server-selected IDs only, not an HTTP request handler."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from finresearch_web.service import Service
from finresearch_web.worker import execute

if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser()
    parser.add_argument('--task', required=True)
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--live-budget', default='0')
    args = parser.parse_args()
    service = Service(ROOT, live_budget=args.live_budget, launch=False)
    execute(ROOT, service, args.task, args.resume)
