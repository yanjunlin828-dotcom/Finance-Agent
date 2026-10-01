"""用于 S0 重启试验的最小 SQLite 状态仓库。"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any


class DuplicateStateError(RuntimeError):
    """同一业务 ID 被重复写入时抛出。"""


class SQLiteStateStore:
    """以唯一业务 ID 原子保存无敏感信息的小型 JSON 状态。"""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS states (
                    business_id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

    def save_once(self, business_id: str, payload: dict[str, Any]) -> None:
        """保存一条完整状态；序列化或唯一约束失败时不留下半条记录。"""

        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        try:
            with closing(self._connect()) as connection, connection:
                connection.execute(
                    "INSERT INTO states (business_id, payload_json) VALUES (?, ?)",
                    (business_id, serialized),
                )
        except sqlite3.IntegrityError as exc:
            raise DuplicateStateError(f"business_id 已存在: {business_id}") from exc

    def load(self, business_id: str) -> dict[str, Any] | None:
        """通过新连接读取状态，未找到时返回 ``None``。"""

        with closing(self._connect()) as connection, connection:
            row = connection.execute(
                "SELECT payload_json FROM states WHERE business_id = ?", (business_id,)
            ).fetchone()
        return None if row is None else json.loads(row["payload_json"])

    def count(self) -> int:
        with closing(self._connect()) as connection, connection:
            row = connection.execute("SELECT COUNT(*) AS count FROM states").fetchone()
        return int(row["count"])
