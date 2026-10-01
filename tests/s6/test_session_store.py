import json
import sqlite3
import pytest
from finresearch.storage.session_store import SessionStore, TransientReadError, UnsafeResume, safe_task_id, task_lock
from .helpers import create_executor


def test_persistent_retry_is_bounded_and_success_is_reused(tmp_path):
    ex, _, _ = create_executor(tmp_path)
    calls = []
    def read():
        calls.append(1)
        if len(calls) == 1:
            raise TransientReadError("temporary")
        return {"result": "ok"}
    assert ex.store.call("read", {}, read) == {"result": "ok"}
    reopened = SessionStore(ex.store.path)
    assert reopened.call("read", {}, lambda: pytest.fail("must not repeat")) == {"result": "ok"}
    assert len(calls) == 2


def test_permanent_and_exhausted_errors_do_not_retry_after_restart(tmp_path):
    ex, _, _ = create_executor(tmp_path)
    calls = []
    def failing():
        calls.append(1)
        raise TransientReadError("temporary")
    with pytest.raises(TransientReadError):
        ex.store.call("read", {}, failing)
    assert len(calls) == 2
    with pytest.raises(RuntimeError):
        SessionStore(ex.store.path).call("read", {}, failing)
    assert len(calls) == 2
    with pytest.raises(ValueError):
        ex.store.call("permanent", {}, lambda: (_ for _ in ()).throw(ValueError("bad")))
    with pytest.raises(RuntimeError):
        ex.store.call("permanent", {}, lambda: pytest.fail("not retryable"))


def test_uncertain_provider_call_is_blocked_not_charged_again(tmp_path):
    ex, _, _ = create_executor(tmp_path)
    key = ex.store.action_key("model", {})
    with ex.store.transaction() as conn:
        conn.execute("INSERT INTO action(key,name,status,attempts) VALUES(?,?,'RUNNING',1)", (key, "model"))
    with pytest.raises(UnsafeResume, match="费用"):
        ex.store.call("model", {}, lambda: pytest.fail("must not call provider"), external_model=True)
    with ex.store.transaction() as conn:
        assert conn.execute("SELECT status FROM action WHERE key=?", (key,)).fetchone()[0] == "UNKNOWN"


def test_model_error_is_one_attempt_even_if_transient(tmp_path):
    ex, _, _ = create_executor(tmp_path)
    calls = []
    def failing():
        calls.append(1)
        raise TransientReadError("model timeout")
    with pytest.raises(TransientReadError):
        ex.store.call("model", {}, failing, external_model=True)
    assert len(calls) == 1


def test_action_cache_corruption_rejected(tmp_path):
    ex, _, _ = create_executor(tmp_path)
    ex.store.call("read", {}, lambda: {"value": "1"})
    with ex.store.transaction() as conn:
        conn.execute("UPDATE action SET payload='{}'")
    with pytest.raises(UnsafeResume):
        ex.store.call("read", {}, lambda: pytest.fail("corruption cannot be retried"))


@pytest.mark.parametrize("task_id", ["../secret", "a/b", "a\\b", "", "..", "x" * 101])
def test_task_path_injection_rejected(task_id):
    with pytest.raises(ValueError):
        safe_task_id(task_id)


def test_lock_prevents_two_workers_and_releases_after_exit(tmp_path):
    path = tmp_path / "task.lock"
    with task_lock(path):
        with pytest.raises(RuntimeError, match="执行器"):
            with task_lock(path):
                pytest.fail("second worker entered")
    with task_lock(path):
        pass


def test_unknown_database_schema_refused(tmp_path):
    path = tmp_path / "unknown.sqlite"
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA user_version=999")
    with pytest.raises(UnsafeResume, match="schema"):
        SessionStore(path)
