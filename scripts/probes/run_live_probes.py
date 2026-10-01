"""执行S0的T05—T08真实模型探针，并与同一attempt的本地探针合并。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import pdfplumber  # noqa: E402
from langchain_core.messages import HumanMessage, SystemMessage  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from finresearch.contracts import (  # noqa: E402
    ConnectivityProbeOutput,
    EvidenceAnswer,
    ToolRequest,
)
from finresearch.gates import evaluate_s0_gate  # noqa: E402
from finresearch.model import DeepSeekJsonClient, LiveProbeGuard, redact_sensitive  # noqa: E402
from finresearch.tools import RegisteredDocumentTools  # noqa: E402

TIMEZONE = ZoneInfo("Asia/Shanghai")
RESERVATION_PER_CALL_USD = Decimal("0.01")


def now_iso() -> str:
    return datetime.now(TIMEZONE).isoformat()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def model_message_record(message: Any) -> dict[str, Any]:
    """保留可审计输出与用量，不保存凭证或供应商内部推理。"""

    return redact_sensitive(
        {
            "content": message.content,
            "tool_calls": getattr(message, "tool_calls", []),
            "usage_metadata": getattr(message, "usage_metadata", None),
            "response_metadata": getattr(message, "response_metadata", {}),
        }
    )


def usage_from_message(message: Any) -> dict[str, int | None]:
    usage = getattr(message, "usage_metadata", None) or {}
    return {
        "model_input_tokens": usage.get("input_tokens"),
        "model_output_tokens": usage.get("output_tokens"),
        "total_tokens": usage.get("total_tokens"),
    }


def conservative_peak_cost_usd(usage: dict[str, int | None], config: dict[str, Any]) -> str | None:
    input_tokens = usage.get("model_input_tokens")
    output_tokens = usage.get("model_output_tokens")
    if input_tokens is None or output_tokens is None:
        return None
    pricing = config["pricing_assumption"]
    input_rate = Decimal(pricing["input_usd_per_million_tokens"])
    output_rate = Decimal(pricing["output_usd_per_million_tokens"])
    cost = Decimal(input_tokens) * input_rate / Decimal(1_000_000)
    cost += Decimal(output_tokens) * output_rate / Decimal(1_000_000)
    return str(cost.quantize(Decimal("0.00000001")))


def json_schema_instruction(model_type: type[BaseModel]) -> str:
    schema = json.dumps(model_type.model_json_schema(), ensure_ascii=False)
    return (
        "只输出一个JSON对象，不要Markdown、解释或额外字段。"
        f"JSON必须满足以下schema：{schema}"
    )



def execute_probe(
    probe_id: str,
    attempt_id: str,
    input_refs: list[str],
    config_hash: str,
    config: dict[str, Any],
    guard: LiveProbeGuard,
    client: DeepSeekJsonClient,
    action: Callable[[], tuple[list[dict[str, Any]], dict[str, Any], Any]],
    limitations: list[str],
) -> dict[str, Any]:
    started_at = now_iso()
    started_clock = time.perf_counter()
    start_calls = guard.total_calls
    start_audits = len(client.attempt_audits)
    try:
        checks, observed, audit = action()
        status = "PASS" if all(check["passed"] for check in checks) else "FAIL"
        error_code = None
    except Exception as exc:
        checks = []
        observed = {"exception_type": type(exc).__name__}
        status = "FAIL"
        error_code = getattr(exc, "code", type(exc).__name__)
    audits = client.attempt_audits[start_audits:]
    observed["model_attempts"] = audits
    call_count = guard.total_calls - start_calls
    usage_keys = ("model_input_tokens", "model_output_tokens", "total_tokens")
    usage = {key: sum(a["usage"][key] for a in audits)
             if audits and all(a["usage"][key] is not None for a in audits) else None for key in usage_keys}
    costs = [Decimal(a["estimated_cost_usd"]) for a in audits if a["estimated_cost_usd"] is not None]
    cost = str(sum(costs)) if audits and len(costs) == len(audits) else None
    observed["known_cost_usd"] = str(sum(costs))
    observed["unknown_cost_attempt_count"] = len(audits) - len(costs)

    return {
        "probe_id": probe_id,
        "attempt_id": attempt_id,
        "mode": "LIVE",
        "started_at": started_at,
        "finished_at": now_iso(),
        "latency_seconds": round(time.perf_counter() - started_clock, 3),
        "input_refs": input_refs,
        "config_hash": config_hash,
        "status": status,
        "observed_result": observed,
        "checks": checks,
        "error_code": error_code,
        "call_count": call_count,
        "usage": usage,
        "cost_status": "ESTIMATED_USD_CONSERVATIVE_PEAK" if cost is not None else "UNKNOWN_NO_USAGE",
        "cost_amount": cost,
        "limitations": limitations,
    }


def load_minimum_evidence(pdf_path: Path) -> tuple[str, str]:
    with pdfplumber.open(pdf_path) as pdf:
        page_95 = pdf.pages[94].extract_text(layout=True) or ""
        page_16 = pdf.pages[15].extract_text(layout=True) or ""
    revenue_lines = [line.strip() for line in page_95.splitlines() if "单位：元" in line or "营业收入" in line]
    business_lines = [
        line.strip()
        for line in page_16.splitlines()
        if "北方华创专注于半导体基础产品" in line or "艺装备包括半导体装备" in line
    ]
    return "\n".join(revenue_lines[:3]), "\n".join(business_lines[:2])


def main() -> int:
    argument_parser = argparse.ArgumentParser()
    argument_parser.add_argument("--attempt-id", required=True)
    args = argument_parser.parse_args()
    attempt_id = args.attempt_id
    output_dir = PROJECT_ROOT / "runs" / "s0" / attempt_id
    if output_dir.exists():
        raise FileExistsError(f"attempt目录已存在: {output_dir}")

    # 先用同一解释器执行T01—T04，随后在同一attempt内追加在线结果。
    subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "scripts" / "probes" / "run_local_probes.py"), "--attempt-id", attempt_id],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    config = read_json(PROJECT_ROOT / "configs" / "s0" / "model_probe.json")
    credential_name = config["credential_environment_variable"]
    credential = os.environ.get(credential_name)
    if not credential:
        raise RuntimeError(f"{credential_name}不存在；没有发起模型调用")
    guard = LiveProbeGuard(config)
    guard.assert_ready()
    inputs_lock = read_json(output_dir / "inputs.lock.json")
    config_hash = inputs_lock["config_hash"]
    manifest = read_jsonl(PROJECT_ROOT / "storage" / "s0" / "document_manifest.jsonl")
    document_id = manifest[0]["document_id"]
    pdf_path = PROJECT_ROOT / manifest[0]["local_path"]
    revenue_evidence, insufficient_evidence = load_minimum_evidence(pdf_path)

    def persist_audit(audit: dict[str, Any]) -> None:
        with (output_dir / "model_attempts.jsonl").open("a", encoding="utf-8") as target:
            target.write(json.dumps(audit, ensure_ascii=False) + "\n")
    client = DeepSeekJsonClient(config, credential, guard, audit_sink=persist_audit)

    nonce = "s0-live-t05-20260922"

    def t05() -> tuple[list[dict[str, Any]], dict[str, Any], Any]:
        parsed, response = client.invoke_json(
            "T05",
            ConnectivityProbeOutput,
            "你正在执行无敏感信息的接口连通测试。",
            f"返回status=OK、nonce={nonce}，并用一句中文说明连通成功。",
        )
        return [
            {"name": "schema_valid", "passed": isinstance(parsed, ConnectivityProbeOutput), "actual": parsed.model_dump()},
            {"name": "nonce_roundtrip", "passed": parsed.nonce == nonce, "actual": parsed.nonce},
        ], {"parsed": parsed.model_dump(), "request_contains_document_text": False}, response

    evidence_id = "naura-2024-p95-revenue"

    def t06() -> tuple[list[dict[str, Any]], dict[str, Any], Any]:
        user_text = (
            "问题：北方华创2024年度合并营业收入是多少？\n"
            f"证据ID：{evidence_id}\n文档ID：{document_id}\nPDF页码：95\n"
            f"公开年报最小证据片段：\n{revenue_evidence}\n"
            "只能依据该片段回答；period写FY2024，unit写CNY yuan。"
        )
        parsed, response = client.invoke_json("T06", EvidenceAnswer, "你是受证据约束的财务事实读取器。", user_text)
        refs = {(ref.evidence_id, ref.document_id, ref.pdf_page) for ref in parsed.evidence_refs}
        return [
            {"name": "answerable", "passed": parsed.status == "ANSWERABLE", "actual": parsed.status},
            {"name": "value_exact", "passed": bool(parsed.answer and "29,838,069,162.26" in parsed.answer), "actual": parsed.answer},
            {"name": "period_and_unit", "passed": parsed.period == "FY2024" and parsed.unit == "CNY yuan", "actual": {"period": parsed.period, "unit": parsed.unit}},
            {"name": "evidence_exact", "passed": (evidence_id, document_id, 95) in refs, "actual": sorted(refs)},
        ], {
            "parsed": parsed.model_dump(),
            "external_excerpt_sha256": sha256_text(revenue_evidence),
            "external_excerpt_characters": len(revenue_evidence),
        }, response

    def t07() -> tuple[list[dict[str, Any]], dict[str, Any], Any]:
        user_text = (
            "问题：北方华创2024年度合并营业收入是多少？\n"
            f"证据ID：naura-2024-p16-business\n文档ID：{document_id}\nPDF页码：16\n"
            f"公开年报最小证据片段：\n{insufficient_evidence}\n"
            "该片段若没有营业收入数值，必须返回INSUFFICIENT_EVIDENCE，并说明缺少营业收入数值及其报表证据。"
        )
        parsed, response = client.invoke_json("T07", EvidenceAnswer, "你是受证据约束的财务事实读取器，不得用常识补齐缺失数字。", user_text)
        return [
            {"name": "insufficient_status", "passed": parsed.status == "INSUFFICIENT_EVIDENCE", "actual": parsed.status},
            {"name": "no_answer", "passed": parsed.answer is None, "actual": parsed.answer},
            {"name": "missing_information_present", "passed": bool(parsed.missing_information), "actual": parsed.missing_information},
            {"name": "no_fabricated_reference", "passed": not parsed.evidence_refs, "actual": [ref.model_dump() for ref in parsed.evidence_refs]},
        ], {
            "parsed": parsed.model_dump(),
            "external_excerpt_sha256": sha256_text(insufficient_evidence),
            "external_excerpt_characters": len(insufficient_evidence),
        }, response

    tools = RegisteredDocumentTools(PROJECT_ROOT, manifest)
    tool_schema = {
        "type": "function",
        "function": {
            "name": "get_document_page_count",
            "description": "返回已登记文档的PDF页数。只能传入document_id，不能传路径。",
            "parameters": {
                "type": "object",
                "properties": {"document_id": {"type": "string"}},
                "required": ["document_id"],
                "additionalProperties": False,
            },
            "strict": True,
        },
    }

    def t08() -> tuple[list[dict[str, Any]], dict[str, Any], Any]:
        response, audit = client.invoke_messages("T08",
            [
                SystemMessage(content="必须通过给定工具获取页数，不要猜测，也不要在文本中假装调用。"),
                HumanMessage(content=f"文档{document_id}一共有多少页？"),
            ], {"tools": [tool_schema], "tool_choice": "required", "parallel_tool_calls": False}
        )
        if len(response.tool_calls) != 1:
            raise ValueError(f"预期1个工具调用，实际{len(response.tool_calls)}个")
        call = response.tool_calls[0]
        request = ToolRequest.model_validate({"tool_name": call["name"], "arguments": call["args"]})
        result = tools.get_document_page_count(**request.arguments.model_dump())
        return [
            {"name": "one_real_tool_call", "passed": len(response.tool_calls) == 1, "actual": len(response.tool_calls)},
            {"name": "tool_request_valid", "passed": request.tool_name == "get_document_page_count", "actual": request.model_dump()},
            {"name": "tool_executed", "passed": result == {"document_id": document_id, "page_count": 190}, "actual": result},
        ], {"validated_tool_request": request.model_dump(), "tool_execution_result": result}, audit

    online_results = [
        execute_probe("T05", attempt_id, ["connectivity_nonce"], config_hash, config, guard, client, t05, []),
        execute_probe(
            "T06",
            attempt_id,
            [evidence_id],
            config_hash,
            config,
            guard,
            client,
            t06,
            ["证据由程序预先提供，本试验不验证RAG检索"],
        ),
        execute_probe(
            "T07",
            attempt_id,
            ["naura-2024-p16-business"],
            config_hash,
            config,
            guard,
            client,
            t07,
            ["只验证给定片段不足时的停止行为，不证明整份文档无答案"],
        ),
        execute_probe(
            "T08",
            attempt_id,
            ["get_document_page_count", document_id],
            config_hash,
            config,
            guard,
            client,
            t08,
            ["只验证一次只读工具请求与执行，不代表完整Agent循环可靠"],
        ),
    ]

    probe_payload = read_json(output_dir / "probe_results.json")
    all_results = probe_payload["results"] + online_results
    write_json(output_dir / "probe_results.json", {"attempt_id": attempt_id, "results": all_results})
    with (output_dir / "events.jsonl").open("a", encoding="utf-8") as event_file:
        for result in online_results:
            event_file.write(
                json.dumps(
                    {
                        "time": result["finished_at"],
                        "event": "probe_finished",
                        "probe_id": result["probe_id"],
                        "status": result["status"],
                        "mode": "LIVE",
                        "call_count": result["call_count"],
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

    gate_report = evaluate_s0_gate(
        attempt_id=attempt_id,
        generated_at=now_iso(),
        probe_results=all_results,
        manifest=manifest,
        metric_dictionary=read_json(PROJECT_ROOT / "configs" / "s0" / "metric_dictionary.json"),
        cases=read_jsonl(PROJECT_ROOT / "evals" / "dev" / "s0_cases.jsonl"),
        split_policy=read_json(PROJECT_ROOT / "evals" / "split_policy.json"),
        environment=read_json(output_dir / "environment.json"),
        handoff_ready=False,
    )
    write_json(output_dir / "gate_report.json", gate_report)
    summary = {
        "attempt_id": attempt_id,
        "overall_decision": gate_report["overall_decision"],
        "probe_statuses": {result["probe_id"]: result["status"] for result in all_results},
        "live_calls": sum(result["call_count"] for result in online_results),
        "unknown_cost_attempt_count": sum(r["observed_result"]["unknown_cost_attempt_count"] for r in online_results),
        "accounted_or_reserved_cost_usd": str(guard.reserved_cost),
        "estimated_cost_usd": str(
            sum(Decimal(result["observed_result"]["known_cost_usd"]) for result in online_results)
        ),
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if all(result["status"] == "PASS" for result in all_results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
