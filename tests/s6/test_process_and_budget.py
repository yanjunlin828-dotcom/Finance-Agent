import json
import os
from pathlib import Path
import subprocess
import sys
from decimal import Decimal
import pytest

from finresearch.storage.session_store import task_lock, UnsafeResume
from finresearch.model.persistent_budget import PersistentBudgetGuard
from .helpers import create_executor, ROOT


def test_os_lock_excludes_an_independent_process(tmp_path):
    path = tmp_path / "worker.lock"
    code = "from pathlib import Path; from finresearch.storage.session_store import task_lock; import sys,time\nwith task_lock(Path(sys.argv[1])):\n print('READY',flush=True)\n time.sleep(20)\n"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src")
    process = subprocess.Popen([sys.executable, "-c", code, str(path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               env=env, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    try:
        assert process.stdout.readline().decode().strip() == "READY"
        with pytest.raises(RuntimeError):
            with task_lock(path):
                pytest.fail("another process owns this task")
    finally:
        process.terminate()
        process.wait(timeout=5)
    with task_lock(path):
        pass


def test_uncertain_model_keeps_budget_reservation_after_reopen(tmp_path):
    ex, _, _ = create_executor(tmp_path)
    config = json.loads((ROOT / "configs/s1/model.json").read_text(encoding="utf-8"))
    call_id = config["online_probe_ids"][0]
    budget_path = tmp_path / "budget.sqlite"
    guard = PersistentBudgetGuard(config, budget_path)
    guard.reserve_call(call_id, Decimal("0.001"))
    key = ex.store.action_key("model", {})
    with ex.store.transaction() as conn:
        conn.execute("INSERT INTO action(key,name,status,attempts) VALUES(?,?,'RUNNING',1)", (key, "model"))
    with pytest.raises(UnsafeResume):
        ex.store.call("model", {}, lambda: pytest.fail("must not charge twice"), external_model=True)
    reopened = PersistentBudgetGuard(config, budget_path)
    assert reopened.total_calls == 1 and reopened.reserved_cost == Decimal("0.001")
    assert reopened._pending

