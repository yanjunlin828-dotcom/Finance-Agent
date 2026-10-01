"""执行S0的T01—T04本地技术探针并保存不可混淆的真实结果。"""

from __future__ import annotations

import argparse
import hashlib
import json
import locale
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import pandas as pd  # noqa: E402
import pdfplumber  # noqa: E402
import pydantic  # noqa: E402
from pydantic import ValidationError  # noqa: E402

from finresearch.contracts import (  # noqa: E402
    DocumentManifestRecord,
    ResearchRequest,
    ScopeViolation,
    select_available_documents,
    validate_request_against_scope,
)
from finresearch.storage import DuplicateStateError, SQLiteStateStore  # noqa: E402
from finresearch.gates import evaluate_s0_gate  # noqa: E402

TIMEZONE = ZoneInfo("Asia/Shanghai")


def now_iso() -> str:
    return datetime.now(TIMEZONE).isoformat()


def canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def pdf_page_count(pdf_path: Path) -> int:
    executable = shutil.which("pdfinfo")
    if executable is None:
        raise RuntimeError("未找到pdfinfo")
    completed = subprocess.run([executable, str(pdf_path)], capture_output=True, check=True)
    # Windows版pdfinfo会按本机代码页输出部分元数据。页数字段是ASCII，
    # 因此使用系统首选编码并允许替换无关字符，避免元数据解码失败遮蔽页数检查。
    output = completed.stdout.decode(locale.getpreferredencoding(False), errors="replace")
    for line in output.splitlines():
        match = re.match(r"^Pages:\s+(\d+)\s*$", line)
        if match is not None:
            return int(match.group(1))
    raise RuntimeError("pdfinfo输出中没有页数")


def probe_t01(manifest: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    validated_records = [DocumentManifestRecord.model_validate(record) for record in manifest]
    document_ids = [record.document_id for record in validated_records]
    checks.append(
        {
            "name": "manifest_document_ids_unique",
            "passed": len(document_ids) == len(set(document_ids)),
            "actual": document_ids,
        }
    )
    for record in manifest:
        path = PROJECT_ROOT / record["local_path"]
        actual_hash = file_hash(path)
        actual_pages = pdf_page_count(path)
        actual_size = path.stat().st_size
        checks.extend(
            [
                {"name": "file_exists", "passed": path.is_file(), "actual": str(path)},
                {"name": "pdf_magic", "passed": path.read_bytes()[:5] == b"%PDF-", "actual": "%PDF-"},
                {"name": "sha256", "passed": actual_hash == record["sha256"], "actual": actual_hash},
                {"name": "size_bytes", "passed": actual_size == record["size_bytes"], "actual": actual_size},
                {"name": "page_count", "passed": actual_pages == record["page_count"], "actual": actual_pages},
                {
                    "name": "source_and_date_evidence_present",
                    "passed": bool(record.get("source_url") and record.get("published_on") and record.get("date_evidence_ref")),
                    "actual": {"published_on": record.get("published_on"), "source_url": record.get("source_url")},
                },
                {
                    "name": "repeat_hash_is_stable",
                    "passed": file_hash(path) == actual_hash,
                    "actual": actual_hash,
                },
            ]
        )
        with tempfile.TemporaryDirectory(prefix="finresearch-s0-") as temporary_directory:
            tampered_path = Path(temporary_directory) / "tampered.pdf"
            shutil.copyfile(path, tampered_path)
            with tampered_path.open("ab") as target:
                target.write(b"S0_TAMPER_TEST")
            tampered_hash = file_hash(tampered_path)
            checks.append(
                {
                    "name": "tampered_copy_is_detected",
                    "passed": tampered_hash != record["sha256"],
                    "actual": {"expected": record["sha256"], "tampered": tampered_hash},
                }
            )
    return checks, {"document_count": len(manifest)}


def probe_t02(manifest: list[dict[str, Any]], output_dir: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    pdf_path = PROJECT_ROOT / manifest[0]["local_path"]
    expectations = {
        16: ["报告期内公司从事的主要业务", "电子工艺装备", "电子元器件"],
        31: ["经营活动产生的现金流量净额", "采购商品支付的货款增加"],
        90: ["合并资产负债表", "单位：元"],
        91: ["应收账款", "6,044,952,733.01"],
        95: ["合并利润表", "29,838,069,162.26"],
        99: ["合并现金流量表", "1,573,165,054.27"],
    }
    extracted_dir = output_dir / "extracted_pages"
    extracted_dir.mkdir(parents=True, exist_ok=True)
    checks: list[dict[str, Any]] = []
    page_outputs: list[dict[str, Any]] = []
    with pdfplumber.open(pdf_path) as pdf:
        checks.append(
            {
                "name": "pdfplumber_page_count",
                "passed": len(pdf.pages) == manifest[0]["page_count"],
                "actual": len(pdf.pages),
            }
        )
        for page, markers in expectations.items():
            text_path = extracted_dir / f"page_{page:03d}.txt"
            # PDF页码从1开始，pdfplumber页数组从0开始。
            content = pdf.pages[page - 1].extract_text(layout=True) or ""
            text_path.write_text(content, encoding="utf-8")
            marker_results = {marker: marker in content for marker in markers}
            checks.append(
                {
                    "name": f"page_{page}_expected_markers",
                    "passed": all(marker_results.values()),
                    "actual": marker_results,
                }
            )
            page_outputs.append(
                {"pdf_page": page, "text_path": str(text_path.relative_to(PROJECT_ROOT)), "sha256": file_hash(text_path)}
            )
    return checks, {
        "parser": "pdfplumber",
        "parser_version": pdfplumber.__version__,
        "page_numbering": "PDF_ONE_BASED_TO_PDFPLUMBER_ZERO_BASED",
        "pages": page_outputs,
    }


def make_request(as_of_date: str = "2025-04-25", company_id: str = "002371.SZ") -> ResearchRequest:
    return ResearchRequest.model_validate(
        {
            "request_id": f"probe-{as_of_date}-{company_id.replace('.', '-')}",
            "topic_id": "revenue_quality_v0",
            "company_ids": [company_id],
            "reporting_years": [2024],
            "as_of_date": as_of_date,
            "cutoff_policy": "ASIA_SHANGHAI_END_OF_DAY",
            "timezone": "Asia/Shanghai",
            "document_snapshot_id": "s0-naura-2024-v1",
            "question": "北方华创2024年度合并营业收入是多少？",
            "output_language": "zh-CN",
            "budget_profile_id": "s0-local-only",
        }
    )


def probe_t03(
    scope: dict[str, Any], manifest: list[dict[str, Any]], request_examples: dict[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    allowed_document_ids = set(scope["document_snapshot"]["document_ids"])
    valid = ResearchRequest.model_validate(request_examples["valid"][0])
    validate_request_against_scope(valid, scope)
    roundtrip = ResearchRequest.model_validate_json(valid.model_dump_json())
    checks.append({"name": "valid_roundtrip", "passed": roundtrip == valid, "actual": valid.model_dump(mode="json")})
    checks.append(
        {
            "name": "runtime_timestamp_is_tz_aware_eod",
            "passed": valid.as_of_timestamp == pd.Timestamp("2025-04-25 23:59:59.999999999", tz="Asia/Shanghai"),
            "actual": str(valid.as_of_timestamp),
        }
    )
    boundary_counts: dict[str, int] = {}
    for label, date_text in [("before", "2025-04-24"), ("on", "2025-04-25"), ("after", "2025-04-26")]:
        count = len(select_available_documents(make_request(date_text), manifest, allowed_document_ids))
        boundary_counts[label] = count
    checks.append(
        {
            "name": "date_boundaries",
            "passed": boundary_counts == {"before": 0, "on": 1, "after": 1},
            "actual": boundary_counts,
        }
    )
    unknown_date = deepcopy(manifest[0])
    unknown_date["published_on"] = None
    unknown_date["time_precision"] = "UNKNOWN"
    unknown_date["date_status"] = "UNKNOWN"
    unknown_date["date_evidence_ref"] = None
    checks.append(
        {
            "name": "unknown_published_on_is_unavailable",
            "passed": select_available_documents(valid, [unknown_date], allowed_document_ids) == [],
            "actual": "excluded",
        }
    )
    injected = deepcopy(manifest[0])
    injected["document_id"] = "unregistered-document-with-same-snapshot-id"
    checks.append(
        {
            "name": "snapshot_rejects_unregistered_document_id",
            "passed": select_available_documents(valid, [injected], allowed_document_ids) == [],
            "actual": injected["document_id"],
        }
    )
    try:
        make_request(company_id="北方华创")
        ambiguous_rejected = False
    except ValidationError:
        ambiguous_rejected = True
    checks.append({"name": "ambiguous_company_rejected", "passed": ambiguous_rejected, "actual": ambiguous_rejected})
    try:
        validate_request_against_scope(make_request(company_id="688082.SH"), scope)
        out_of_scope_rejected = False
    except ScopeViolation:
        out_of_scope_rejected = True
    checks.append({"name": "out_of_scope_company_rejected", "passed": out_of_scope_rejected, "actual": out_of_scope_rejected})
    invalid_case_results: dict[str, bool] = {}
    base_payload = deepcopy(request_examples["valid"][0])
    for case in request_examples["invalid"]:
        payload = deepcopy(base_payload)
        patch = case["payload_patch"]
        for key in patch.get("remove", []):
            payload.pop(key, None)
        payload.update(patch.get("replace", {}))
        try:
            candidate = ResearchRequest.model_validate(payload)
            validate_request_against_scope(candidate, scope)
            rejected_with_expected_reason = False
            observed_error = None
        except (ValidationError, ScopeViolation) as exc:
            observed_error = str(exc)
            rejected_with_expected_reason = case["expected_error"] in observed_error
        invalid_case_results[case["case_id"]] = rejected_with_expected_reason
        checks.append(
            {
                "name": f"invalid_example_{case['case_id']}",
                "passed": rejected_with_expected_reason,
                "actual": observed_error,
            }
        )
    return checks, {
        "valid_request_id": valid.request_id,
        "boundary_available_counts": boundary_counts,
        "invalid_examples": invalid_case_results,
    }


def probe_t04(output_dir: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    database_path = output_dir / "s0_state.sqlite3"
    if database_path.exists():
        database_path.unlink()
    store = SQLiteStateStore(database_path)
    payload = {"request_id": "s0-valid-001", "status": "SAVED", "evidence_ids": ["page-95-revenue"]}
    store.save_once("s0-valid-001", payload)
    count_after_first = store.count()
    try:
        store.save_once("s0-valid-001", payload)
        duplicate_rejected = False
    except DuplicateStateError:
        duplicate_rejected = True
    count_after_duplicate = store.count()
    try:
        store.save_once("serialization-failure", {"not_json": {1, 2, 3}})
        serialization_failed = False
    except TypeError:
        serialization_failed = True
    count_after_failure = store.count()
    child_env = os.environ.copy()
    child_env["PYTHONPATH"] = str(PROJECT_ROOT / "src")
    completed = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "scripts" / "probes" / "read_state.py"), str(database_path), "s0-valid-001"],
        capture_output=True,
        text=True,
        check=True,
        env=child_env,
        encoding="utf-8",
    )
    recovered = json.loads(completed.stdout)
    checks = [
        {"name": "first_write_complete", "passed": count_after_first == 1, "actual": count_after_first},
        {"name": "duplicate_rejected", "passed": duplicate_rejected and count_after_duplicate == 1, "actual": count_after_duplicate},
        {"name": "failed_write_is_atomic", "passed": serialization_failed and count_after_failure == 1, "actual": count_after_failure},
        {"name": "separate_process_recovery", "passed": recovered == payload, "actual": recovered},
    ]
    return checks, {"database_path": str(database_path.relative_to(PROJECT_ROOT)), "record_count": store.count()}


def run_probe(
    probe_id: str,
    attempt_id: str,
    input_refs: list[str],
    config_hash: str,
    action: Callable[[], tuple[list[dict[str, Any]], dict[str, Any]]],
) -> dict[str, Any]:
    started_at = now_iso()
    try:
        checks, observed = action()
        status = "PASS" if all(check["passed"] for check in checks) else "FAIL"
        error_code = None
    except Exception as exc:  # 探针必须保存失败，而不是只留下异常栈
        checks = []
        observed = {"exception_type": type(exc).__name__, "message": str(exc)}
        status = "FAIL"
        error_code = type(exc).__name__
    return {
        "probe_id": probe_id,
        "attempt_id": attempt_id,
        "mode": "LOCAL",
        "started_at": started_at,
        "finished_at": now_iso(),
        "input_refs": input_refs,
        "config_hash": config_hash,
        "status": status,
        "observed_result": observed,
        "checks": checks,
        "error_code": error_code,
        "call_count": 0,
        "usage": null_usage(),
        "cost_status": "NOT_APPLICABLE_LOCAL",
        "cost_amount": None,
        "limitations": [],
    }


def null_usage() -> dict[str, Any]:
    return {"model_input_tokens": None, "model_output_tokens": None}


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--attempt-id", default="s0-local-20260922-01")
    arguments = parser.parse_args()
    attempt_id = arguments.attempt_id
    output_dir = PROJECT_ROOT / "runs" / "s0" / attempt_id
    output_dir.mkdir(parents=True, exist_ok=False)

    scope_path = PROJECT_ROOT / "configs" / "s0" / "scope.json"
    metric_path = PROJECT_ROOT / "configs" / "s0" / "metric_dictionary.json"
    model_probe_path = PROJECT_ROOT / "configs" / "s0" / "model_probe.json"
    protocol_path = PROJECT_ROOT / "protocols" / "revenue_quality_v0.json"
    request_examples_path = PROJECT_ROOT / "configs" / "s0" / "request_examples.json"
    cases_path = PROJECT_ROOT / "evals" / "dev" / "s0_cases.jsonl"
    split_policy_path = PROJECT_ROOT / "evals" / "split_policy.json"
    manifest_path = PROJECT_ROOT / "storage" / "s0" / "document_manifest.jsonl"
    scope = read_json(scope_path)
    metric_dictionary = read_json(metric_path)
    model_probe = read_json(model_probe_path)
    protocol = read_json(protocol_path)
    request_examples = read_json(request_examples_path)
    cases = read_jsonl(cases_path)
    split_policy = read_json(split_policy_path)
    manifest = read_jsonl(manifest_path)
    config_bundle = {
        "scope": scope,
        "metric_dictionary": metric_dictionary,
        "model_probe": model_probe,
        "protocol": protocol,
        "request_examples": request_examples,
        "cases": cases,
        "split_policy": split_policy,
    }
    config_hash = canonical_hash(config_bundle)

    inputs_lock = {
        "attempt_id": attempt_id,
        "created_at": now_iso(),
        "config_hash": config_hash,
        "files": [
            {"path": str(path.relative_to(PROJECT_ROOT)), "sha256": file_hash(path)}
            for path in [
                scope_path,
                metric_path,
                model_probe_path,
                protocol_path,
                request_examples_path,
                cases_path,
                split_policy_path,
                manifest_path,
            ]
        ],
        "documents": [{"document_id": row["document_id"], "sha256": row["sha256"]} for row in manifest],
    }
    write_json(output_dir / "inputs.lock.json", inputs_lock)
    conda_environment = os.environ.get("CONDA_DEFAULT_ENV")
    prefix_name = Path(sys.prefix).name
    expected_environment = "finresearch-agent"
    isolated_environment = expected_environment in {conda_environment, prefix_name}
    environment = {
        "captured_at": now_iso(),
        "python": sys.version,
        "python_executable": sys.executable,
        "python_prefix": sys.prefix,
        "platform": platform.platform(),
        "conda_environment": conda_environment,
        "detected_environment_name": conda_environment or prefix_name,
        "expected_environment_name": expected_environment,
        "pydantic": pydantic.__version__,
        "pandas": pd.__version__,
        "pdfplumber": pdfplumber.__version__,
        "pdfinfo": shutil.which("pdfinfo"),
        "pdftotext": shutil.which("pdftotext"),
        "isolated_environment": isolated_environment,
        "memory": "UNKNOWN_PERMISSION_DENIED_DURING_S0_1",
    }
    write_json(output_dir / "environment.json", environment)

    results = [
        run_probe("T01", attempt_id, ["storage/s0/document_manifest.jsonl"], config_hash, lambda: probe_t01(manifest)),
        run_probe("T02", attempt_id, [manifest[0]["document_id"]], config_hash, lambda: probe_t02(manifest, output_dir)),
        run_probe(
            "T03",
            attempt_id,
            ["configs/s0/scope.json", "configs/s0/request_examples.json"],
            config_hash,
            lambda: probe_t03(scope, manifest, request_examples),
        ),
        run_probe("T04", attempt_id, ["small_non_sensitive_state"], config_hash, lambda: probe_t04(output_dir)),
    ]
    write_json(output_dir / "probe_results.json", {"attempt_id": attempt_id, "results": results})
    event_lines = [
        json.dumps(
            {"time": result["finished_at"], "event": "probe_finished", "probe_id": result["probe_id"], "status": result["status"]},
            ensure_ascii=False,
        )
        for result in results
    ]
    (output_dir / "events.jsonl").write_text("\n".join(event_lines) + "\n", encoding="utf-8")

    local_pass = all(result["status"] == "PASS" for result in results)
    gate_report = evaluate_s0_gate(
        attempt_id=attempt_id,
        generated_at=now_iso(),
        probe_results=results,
        manifest=manifest,
        metric_dictionary=metric_dictionary,
        cases=cases,
        split_policy=split_policy,
        environment=environment,
        handoff_ready=False,
    )
    write_json(output_dir / "gate_report.json", gate_report)
    print(json.dumps({"attempt_id": attempt_id, "overall_decision": gate_report["overall_decision"], "probe_statuses": {r["probe_id"]: r["status"] for r in results}}, ensure_ascii=False))
    return 0 if local_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
