"""S6 transactional task control, result journal and explicit verified facts."""
from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import re
import sqlite3
from typing import Callable, Iterator

from finresearch.contracts import MetricObservation, stable_sha256
from finresearch.contracts.research import ResearchContext
from finresearch.workflow.session_context import eligible_observations, scope_key

SCHEMA_VERSION = 1


class Cancelled(Exception):
    """Cooperative cancellation, deliberately not swallowed by S4 fallbacks."""


class UnsafeResume(Exception):
    """An uncertain external effect requires explicit human review."""


class TransientReadError(Exception):
    """Only adapters for read-only operations may classify an error as retryable."""


def safe_task_id(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}", value):
        raise ValueError("不安全任务ID")
    return value


@contextmanager
def task_lock(path: Path) -> Iterator[None]:
    """One OS-held lock; process exit releases it, with no lease expiry races."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        if path.stat().st_size == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        import os
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise RuntimeError("任务已有执行器，拒绝并发运行") from None
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


class SessionStore:
    """Per-task durable state; supplied paths are trusted application paths.

    Every mutating method uses its own transaction. Task and action payloads
    have content digests; these detect corruption, not malicious administrators
    replacing both trusted data and digests. Secrets and exception text are not
    recorded. Long-term fact writes are an explicit non-model capability.
    """
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.transaction() as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, SCHEMA_VERSION):
                raise UnsafeResume("业务数据库schema不兼容")
            if version == 0 and conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
                raise UnsafeResume("未知数据库不自动迁移")
            conn.execute("CREATE TABLE IF NOT EXISTS task (id INTEGER PRIMARY KEY CHECK(id=1), manifest TEXT NOT NULL, digest TEXT NOT NULL, status TEXT NOT NULL, cancel INTEGER NOT NULL DEFAULT 0)")
            conn.execute("CREATE TABLE IF NOT EXISTS action (key TEXT PRIMARY KEY, name TEXT NOT NULL, status TEXT NOT NULL, attempts INTEGER NOT NULL, payload TEXT, digest TEXT, failure_type TEXT)")
            conn.execute("CREATE TABLE IF NOT EXISTS event (id INTEGER PRIMARY KEY, created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')), payload TEXT NOT NULL)")
            conn.execute("CREATE TABLE IF NOT EXISTS fact (snapshot TEXT NOT NULL, id TEXT NOT NULL, payload TEXT NOT NULL, digest TEXT NOT NULL, PRIMARY KEY(snapshot,id))")
            conn.execute("CREATE TABLE IF NOT EXISTS turn (id TEXT PRIMARY KEY, scope TEXT NOT NULL, payload TEXT NOT NULL, digest TEXT NOT NULL)")
            conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    @contextmanager
    def transaction(self):
        conn = sqlite3.connect(self.path, timeout=10)
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def create(self, manifest: dict):
        safe_task_id(manifest["context"]["run_id"])
        with self.transaction() as conn:
            if conn.execute("SELECT 1 FROM task").fetchone():
                raise FileExistsError("任务已存在，须用恢复入口")
            conn.execute("INSERT INTO task(id,manifest,digest,status) VALUES(1,?,?,?)",
                         (json.dumps(manifest, ensure_ascii=False), stable_sha256(manifest), "CREATED"))

    def task(self) -> dict:
        with self.transaction() as conn:
            row = conn.execute("SELECT manifest,digest,status,cancel FROM task WHERE id=1").fetchone()
        if row is None:
            raise ValueError("任务不存在")
        manifest = json.loads(row[0])
        if stable_sha256(manifest) != row[1] or manifest["schema_version"] != SCHEMA_VERSION:
            raise UnsafeResume("任务清单指纹或schema不兼容")
        return {"manifest": manifest, "status": row[2], "cancel_requested": bool(row[3])}

    def set_status(self, status: str):
        if status not in {"CREATED", "RUNNING", "COMPLETED", "PARTIAL", "WAITING_INPUT", "CANCELLED", "FAILED", "BLOCKED"}:
            raise ValueError("未知状态")
        with self.transaction() as conn:
            row = conn.execute("SELECT cancel FROM task WHERE id=1").fetchone()
            if not row:
                raise ValueError("任务不存在")
            # Cancellation winning the final-publication race is authoritative.
            if row[0] and status in {"COMPLETED", "PARTIAL"}:
                raise Cancelled("任务已请求取消")
            conn.execute("UPDATE task SET status=? WHERE id=1", (status,))

    def cancel(self):
        with self.transaction() as conn:
            row = conn.execute("SELECT status FROM task WHERE id=1").fetchone()
            if row is None:
                raise ValueError("任务不存在")
            if row[0] in {"COMPLETED", "PARTIAL", "CANCELLED"}:
                return False
            conn.execute("UPDATE task SET cancel=1 WHERE id=1")
        self.event({"event": "CANCEL_REQUESTED"})
        return True

    def check_cancelled(self):
        if self.task()["cancel_requested"]:
            raise Cancelled("在可控边界停止")

    def event(self, payload: dict):
        with self.transaction() as conn:
            conn.execute("INSERT INTO event(payload) VALUES(?)", (json.dumps(payload, ensure_ascii=False),))

    def events(self) -> list[dict]:
        with self.transaction() as conn:
            return [json.loads(row[0]) for row in conn.execute("SELECT payload FROM event ORDER BY id")]

    def action_key(self, name: str, arguments: dict) -> str:
        manifest = self.task()["manifest"]
        return stable_sha256({"scope": scope_key(ResearchContext.model_validate(manifest["context"])),
                              "inputs": manifest["input_lock"], "policy": manifest["policy"],
                              "name": name, "arguments": arguments})

    def completed_result(self, name: str, arguments: dict):
        key = self.action_key(name, arguments)
        with self.transaction() as conn:
            row = conn.execute("SELECT status,payload,digest FROM action WHERE key=?", (key,)).fetchone()
        if row is None or row[0] != "DONE":
            return None
        payload = json.loads(row[1])
        if stable_sha256(payload) != row[2]:
            raise UnsafeResume("动作结果缓存被修改")
        return payload

    def call(self, name: str, arguments: dict, callback: Callable,
             *, external_model: bool = False, maximum_attempts: int = 2,
             after_commit: Callable | None = None):
        """Journal JSON outputs; replay committed success before node checkpoint.

        Models get one attempt. In-flight model calls after process death are
        UNKNOWN, never automatically sent again. Only TransientReadError from
        a declared read-only adapter is retried, with persisted attempt limits.
        Caller holds the task OS lock for the whole execution.
        """
        if not 1 <= maximum_attempts <= 2:
            raise ValueError("只读动作最多尝试两次")
        limit = 1 if external_model else maximum_attempts
        key = self.action_key(name, arguments)
        while True:
            self.check_cancelled()
            with self.transaction() as conn:
                row = conn.execute("SELECT status,attempts,payload,digest,failure_type FROM action WHERE key=?", (key,)).fetchone()
                if row and row[0] == "DONE":
                    payload = json.loads(row[2])
                    if stable_sha256(payload) != row[3]:
                        raise UnsafeResume("动作结果缓存被修改")
                    return payload
                if row and (row[0] == "UNKNOWN" or external_model and row[0] == "RUNNING"):
                    conn.execute("UPDATE action SET status='UNKNOWN' WHERE key=?", (key,))
                    uncertain = True
                else:
                    uncertain = False
                    attempts = row[1] if row else 0
                    if row and row[0] == "FAILED":
                        raise RuntimeError("已记录动作失败，不自动重发模型或非暂时错误")
                    if attempts >= limit:
                        raise RuntimeError("动作持久化尝试次数耗尽")
                    conn.execute("INSERT INTO action(key,name,status,attempts) VALUES(?,?,'RUNNING',1) ON CONFLICT(key) DO UPDATE SET status='RUNNING',attempts=action.attempts+1",
                                 (key, name))
            if uncertain:
                self.event({"event": "EXTERNAL_RESULT_UNKNOWN", "action": name, "key": key})
                raise UnsafeResume("模型外部执行/费用可能已发生，禁止自动重发")
            self.event({"event": "ACTION_START", "action": name, "key": key})
            try:
                payload = callback()
                serialized = json.dumps(payload, ensure_ascii=False, allow_nan=False)
            except Exception as exc:
                retry = isinstance(exc, TransientReadError) and not external_model
                with self.transaction() as conn:
                    used = conn.execute("SELECT attempts FROM action WHERE key=?", (key,)).fetchone()[0]
                    conn.execute("UPDATE action SET status=?,failure_type=? WHERE key=?",
                                 ("RETRYABLE" if retry and used < limit else "FAILED", type(exc).__name__, key))
                self.event({"event": "ACTION_FAILED", "action": name, "exception_type": type(exc).__name__, "retryable": retry and used < limit})
                if retry and used < limit:
                    continue
                raise
            with self.transaction() as conn:
                conn.execute("UPDATE action SET status='DONE',payload=?,digest=? WHERE key=?",
                             (serialized, stable_sha256(payload), key))
            self.event({"event": "ACTION_COMMITTED", "action": name, "key": key})
            if after_commit:
                after_commit(name)
            self.check_cancelled()
            return payload

    def promote_facts(self, context: ResearchContext, candidates: list[dict], verified: list[dict]) -> int:
        """Explicit local command only; compare against freshly rebuilt sources.

        Full batch commits atomically. Conflicting source records remain an
        error, never an implicit override. Models cannot invoke this method.
        """
        trusted = {r["observation_id"]: r for r in eligible_observations(verified, context)}
        normalized = [MetricObservation.model_validate(r).model_dump(mode="json") for r in candidates]
        if len({r["observation_id"] for r in normalized}) != len(normalized):
            raise ValueError("事实批次ID重复")
        if any(r != trusted.get(r["observation_id"]) or r["review_status"] not in {"SOURCE_CHECKED", "HUMAN_VERIFIED"}
               or r["value_status"] != "OBSERVED" for r in normalized):
            raise ValueError("事实未独立复核或超出范围；拒绝写入")
        inserted = 0
        with self.transaction() as conn:
            for payload in normalized:
                key = (context.corpus_snapshot_id, payload["observation_id"])
                digest = stable_sha256(payload)
                row = conn.execute("SELECT digest FROM fact WHERE snapshot=? AND id=?", key).fetchone()
                if row and row[0] != digest:
                    raise ValueError("长期事实冲突，不能覆盖")
                if not row:
                    conn.execute("INSERT INTO fact VALUES(?,?,?,?)", (*key, json.dumps(payload, ensure_ascii=False), digest))
                    inserted += 1
        return inserted

    def read_facts(self, context: ResearchContext, verified: list[dict]) -> list[dict]:
        """Only exact matches to current independently checked sources are reusable."""
        trusted = {r["observation_id"]: r for r in eligible_observations(verified, context)}
        with self.transaction() as conn:
            rows = conn.execute("SELECT payload,digest FROM fact WHERE snapshot=? ORDER BY id", (context.corpus_snapshot_id,)).fetchall()
        result = []
        for raw, digest in rows:
            payload = json.loads(raw)
            if stable_sha256(payload) != digest:
                raise UnsafeResume("长期事实存储被修改")
            if trusted.get(payload["observation_id"]) == payload:
                result.append(payload)
        return result

    def save_turn(self, turn_id: str, question: str, context: ResearchContext, answer: dict):
        """Persist typed follow-up history; messages never become source facts.

        The client request ID makes a repeated request idempotent. A changed
        question/answer under the same ID is a conflict. Per-task history is
        bounded to fifty turns; it is not automatically fed to the model.
        """
        safe_task_id(turn_id)
        if not question.strip() or len(question) > 2000:
            raise ValueError("追问不能为空或超过2000字符")
        payload = {"question": question, "context": context.model_dump(mode="json"), "answer": answer,
                   "authority": "CONVERSATION_NOT_FINANCIAL_FACT"}
        digest = stable_sha256(payload)
        with self.transaction() as conn:
            row = conn.execute("SELECT digest FROM turn WHERE id=?", (turn_id,)).fetchone()
            if row:
                if row[0] != digest:
                    raise ValueError("相同追问ID的内容发生冲突")
                return False
            if conn.execute("SELECT COUNT(*) FROM turn").fetchone()[0] >= 50:
                raise ValueError("追问历史已达50轮上限，请新建任务")
            conn.execute("INSERT INTO turn VALUES(?,?,?,?)", (turn_id, scope_key(context), json.dumps(payload, ensure_ascii=False), digest))
        return True

    def read_turns(self, context: ResearchContext):
        with self.transaction() as conn:
            rows = conn.execute("SELECT id,payload,digest FROM turn WHERE scope=? ORDER BY rowid", (scope_key(context),)).fetchall()
        result = []
        for turn_id, raw, digest in rows:
            payload = json.loads(raw)
            if stable_sha256(payload) != digest:
                raise UnsafeResume("追问历史被修改")
            result.append({"turn_id": turn_id, **payload})
        return result
