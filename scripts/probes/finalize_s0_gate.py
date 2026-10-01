"""在交接文档完成后，重新计算指定attempt的S0最终门禁。"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from finresearch.gates import evaluate_s0_gate  # noqa: E402

TIMEZONE = ZoneInfo("Asia/Shanghai")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--attempt-id", required=True)
    args = parser.parse_args()
    output_dir = PROJECT_ROOT / "runs" / "s0" / args.attempt_id
    handoff_path = PROJECT_ROOT / "docs" / "stages" / "s0" / "handoff_to_s1.md"
    handoff = handoff_path.read_text(encoding="utf-8")
    handoff_ready = "状态：`READY`" in handoff and "## S1首条纵向任务" in handoff
    if not handoff_ready:
        raise RuntimeError("S1交接文档尚未达到READY状态")

    probe_results = read_json(output_dir / "probe_results.json")["results"]
    report = evaluate_s0_gate(
        attempt_id=args.attempt_id,
        generated_at=datetime.now(TIMEZONE).isoformat(),
        probe_results=probe_results,
        manifest=read_jsonl(PROJECT_ROOT / "storage" / "s0" / "document_manifest.jsonl"),
        metric_dictionary=read_json(PROJECT_ROOT / "configs" / "s0" / "metric_dictionary.json"),
        cases=read_jsonl(PROJECT_ROOT / "evals" / "dev" / "s0_cases.jsonl"),
        split_policy=read_json(PROJECT_ROOT / "evals" / "split_policy.json"),
        environment=read_json(output_dir / "environment.json"),
        handoff_ready=True,
    )
    (output_dir / "gate_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    with (output_dir / "events.jsonl").open("a", encoding="utf-8") as event_file:
        event_file.write(
            json.dumps(
                {
                    "time": report["generated_at"],
                    "event": "gate_finalized",
                    "overall_decision": report["overall_decision"],
                    "handoff_ready": True,
                },
                ensure_ascii=False,
            )
            + "\n"
        )
    print(json.dumps({"attempt_id": args.attempt_id, "overall_decision": report["overall_decision"]}, ensure_ascii=False))
    return 0 if report["overall_decision"] in {"GO", "GO_SCOPED"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
