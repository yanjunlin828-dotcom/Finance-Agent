"""Read-only history/reviewer audits and immutable evaluation manifests."""
from __future__ import annotations
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

from finresearch.contracts import stable_sha256
from finresearch.retrieval.corpus import file_sha256
from finresearch.workflow.session_executor import atomic_artifact, json_text


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value):
    """Publish a new immutable file; conflicting prior content is rejected."""
    atomic_artifact(path, json_text(value))


def freeze(root: Path, folder: Path, source_locks: dict, contract: dict, mode: str) -> dict:
    """Lock evaluator, labels, production/source/model versions before execution."""
    locks = dict(source_locks)
    for pattern in ("src/finresearch_evals/*.py", "scripts/s7/*.py", "configs/s7/*.json", "evals/s7/*.json"):
        locks.update({p.relative_to(root).as_posix():file_sha256(p) for p in root.glob(pattern)})
    for path in (root/"configs/s6/baseline.json",root/"scripts/s6/supplement_task.py"):
        if path.is_file():locks[path.relative_to(root).as_posix()]=file_sha256(path)
    manifest = {"version":1,"created_at":datetime.now(timezone.utc).isoformat(),"mode":mode,
        "dataset_role":contract["dataset_role"],"contract":contract,"locks":locks,
        "gold_visible_to_model":False,"independent_holdout":False,
        "fact_groups":[f"{c}:{m}:2023-2024" for c in contract["scope"]["company_ids"]
                       for m in ("revenue","operating_cash_flow_net","accounts_receivable")],
        "semantic_groups":[f"{c}:annual-disclosure-2024" for c in contract["scope"]["company_ids"]]}
    manifest["manifest_sha256"] = stable_sha256(manifest)
    write_json(folder/"evaluation.lock.json",manifest)
    return manifest


def validate_freeze(root: Path, manifest: dict) -> None:
    """Fail on changed files or contract, including private scoring labels."""
    content = {k:v for k,v in manifest.items() if k!="manifest_sha256"}
    if stable_sha256(content)!=manifest["manifest_sha256"]:
        raise ValueError("evaluation manifest changed")
    for relative,digest in manifest["locks"].items():
        path=(root/relative).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file() or file_sha256(path)!=digest:
            raise ValueError("evaluation source drift")


def historical_s1(root: Path) -> dict:
    """Preserve reopened G1 status; unknown historical billing stays unknown."""
    gate_path=root/"runs/s1/s1-repair-revalidation-20260930-04/gate_report.json"
    gate=read_json(gate_path)
    checks={r["gate_id"]:r for r in gate["checks"]}
    return {"source_gate":gate_path.relative_to(root).as_posix(),"source_sha256":file_sha256(gate_path),
        "decision":gate["overall_decision"],"answer_review":checks.get("G1-A1"),
        "billing_checks":[r for r in gate["checks"] if r["gate_id"]=="G1-O2"],
        "resolution":"NOT_CLOSED_BY_S7_AUTOMATION",
        "billing_note":"历史失败调用无完整usage的费用不能由文件缺失推断为零；保持未知，不声称已完整审计。"}


def task_budget(path: Path) -> dict:
    """Read persistent conservative cost, including pending provider reservations."""
    if not path.is_file():
        return {"calls":0,"estimated_cost_usd":"0","pending":{},"provider_contacted":False}
    with sqlite3.connect(f"file:{path.as_posix()}?mode=ro",uri=True) as conn:
        row=conn.execute("SELECT payload FROM budget WHERE id=1").fetchone()
    data=json.loads(row[0])
    return {"calls":data["total_calls"],"estimated_cost_usd":data["reserved_cost"],"pending":data["pending"],
        "provider_contacted":bool(data["total_calls"]),"billing_basis":"CONSERVATIVE_CONFIG_ESTIMATE_NOT_SUPPLIER_INVOICE"}


def reviewer_packet(scores: dict, s1_audit: dict) -> dict:
    """Bind every review item to exact output/source bytes; leave decisions empty."""
    items=[]
    for branch,score in scores.items():
        for item in score.get("semantic_review",{}).get("items",[]):
            bound={"branch":branch,**item}
            bound["item_sha256"]=stable_sha256({k:v for k,v in bound.items() if k not in {"decision","reviewer_type"}})
            items.append(bound)
    return {"version":1,"status":"PENDING_INDEPENDENT_HUMAN_REVIEW","items":items,
        "questions":["原文是否回答研究问题？","支持/削弱关系是否恰当？","是否遗漏限定或反证？","公司解释是否被写成确认因果？"],
        "s1_reopened_audit":s1_audit,"reviewer":None,
        "instructions":"独立人类填写来源绑定的逐项判断和理由。本AI/模型初审不能标为独立人工批准。"}


def validate_human_review(packet: dict, submitted: dict) -> bool:
    """Accept only complete, signed independent-human declarations, not AI scores.

    This validates a declared review contract, not the biological identity or
    independence of the signatory. Such trust is external to this local tool.
    """
    reviewer=submitted.get("reviewer") or {}
    if reviewer.get("type")!="INDEPENDENT_HUMAN" or not reviewer.get("name") or not reviewer.get("reviewed_at"):
        return False
    known={(r["branch"],r["item_id"]):r for r in packet["items"]}
    seen=set()
    for row in submitted.get("items",[]):
        key=row.get("branch"),row.get("item_id")
        if key in seen or key not in known or row.get("item_sha256")!=known[key]["item_sha256"]:
            return False
        if row.get("decision") not in {"SUPPORTED_WITH_LIMITS","REJECTED","NOT_ENOUGH_EVIDENCE"} or not row.get("reason"):
            return False
        seen.add(key)
    return bool(known) and seen==set(known)
