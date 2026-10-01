from __future__ import annotations

from pathlib import Path

import pytest

from finresearch.storage import DuplicateStateError, SQLiteStateStore


def test_state_survives_new_store_instance(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite3"
    first = SQLiteStateStore(path)
    payload = {"status": "SAVED", "evidence_ids": ["page-95"]}
    first.save_once("request-1", payload)

    restarted = SQLiteStateStore(path)
    assert restarted.load("request-1") == payload


def test_duplicate_business_id_is_rejected_without_overwrite(tmp_path: Path) -> None:
    store = SQLiteStateStore(tmp_path / "state.sqlite3")
    store.save_once("request-1", {"version": 1})
    with pytest.raises(DuplicateStateError):
        store.save_once("request-1", {"version": 2})
    assert store.load("request-1") == {"version": 1}
    assert store.count() == 1


def test_serialization_failure_does_not_create_partial_record(tmp_path: Path) -> None:
    store = SQLiteStateStore(tmp_path / "state.sqlite3")
    with pytest.raises(TypeError):
        store.save_once("bad", {"not_json": {1, 2, 3}})
    assert store.count() == 0
