"""在独立进程中读取S0 SQLite状态，用于验证重启后恢复。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from finresearch.storage import SQLiteStateStore  # noqa: E402


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit("usage: read_state.py <database_path> <business_id>")
    payload = SQLiteStateStore(Path(sys.argv[1])).load(sys.argv[2])
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0 if payload is not None else 2


if __name__ == "__main__":
    raise SystemExit(main())
