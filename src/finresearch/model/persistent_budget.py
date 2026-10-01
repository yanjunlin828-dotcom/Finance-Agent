"""S4 per-run budgets survive restarts and concurrent local attempts."""
from __future__ import annotations
from collections import Counter
from contextlib import closing
from decimal import Decimal
import json
from pathlib import Path
import sqlite3

from finresearch.contracts import stable_sha256
from .probe_guard import LiveProbeGuard


class PersistentBudgetGuard(LiveProbeGuard):
    """Use one SQLite transaction for every reservation or settlement.

    Unknown usage keeps its reservation. This records conservatively estimated
    costs, not the supplier's actual billing ledger. Credentials are never stored.
    """
    def __init__(self, config: dict, path: Path) -> None:
        super().__init__(config)
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.config_sha = stable_sha256(config)
        with closing(sqlite3.connect(path)) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS budget (id INTEGER PRIMARY KEY CHECK(id=1), config_sha TEXT NOT NULL, payload TEXT NOT NULL)")
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT config_sha,payload FROM budget WHERE id=1").fetchone()
            if row is None:
                conn.execute("INSERT INTO budget VALUES (1, ?, ?)", (self.config_sha, json.dumps(self._payload())))
                conn.commit()
            else:
                if row[0] != self.config_sha:
                    raise ValueError("预算配置不能在同一run内变更")
                self._restore(json.loads(row[1]))

    def _payload(self):
        return {"total_calls": self.total_calls, "calls_by_probe": dict(self.calls_by_probe),
                "reserved_cost": str(self.reserved_cost), "pending": {str(k): str(v) for k, v in self._pending.items()}}

    def _restore(self, payload):
        self.total_calls = payload["total_calls"]
        self.calls_by_probe = Counter(payload["calls_by_probe"])
        self.reserved_cost = Decimal(payload["reserved_cost"])
        self._pending = {int(k): Decimal(v) for k, v in payload["pending"].items()}

    def _transaction(self, callback):
        with closing(sqlite3.connect(self.path, timeout=10)) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT config_sha,payload FROM budget WHERE id=1").fetchone()
            if row[0] != self.config_sha:
                raise ValueError("预算配置已改变")
            self._restore(json.loads(row[1]))
            try:
                result = callback()
            except Exception:
                # Preserve a provider overrun recorded by settle_call. A failed
                # reservation has no side effect; it writes the unchanged state.
                conn.execute("UPDATE budget SET payload=? WHERE id=1", (json.dumps(self._payload()),))
                conn.commit()
                raise
            conn.execute("UPDATE budget SET payload=? WHERE id=1", (json.dumps(self._payload()),))
            conn.commit()
            return result

    def reserve_call(self, probe_id: str, estimated_cost: Decimal):
        return self._transaction(lambda: super(PersistentBudgetGuard, self).reserve_call(probe_id, estimated_cost))

    def settle_call(self, reservation: dict, actual_cost: Decimal | None):
        return self._transaction(lambda: super(PersistentBudgetGuard, self).settle_call(reservation, actual_cost))
