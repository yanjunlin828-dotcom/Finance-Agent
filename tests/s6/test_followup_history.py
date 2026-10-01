from datetime import date
import pytest
from finresearch.storage.session_store import SessionStore, UnsafeResume
from .helpers import create_executor


def test_multiturn_history_reopens_and_changed_scope_stays_separate(tmp_path):
    ex, ctx, _ = create_executor(tmp_path)
    response = {"status": "ANSWERED_FROM_REVIEWED_RECORDS", "claim_ids": ["claim-one"]}
    assert ex.store.save_turn("turn-1", "收入变化？", ctx, response)
    assert not ex.store.save_turn("turn-1", "收入变化？", ctx, response)
    ex.store.save_turn("turn-2", "现金流变化？", ctx, response)
    different = ctx.model_copy(update={"as_of_date": date(2025, 4, 24)})
    ex.store.save_turn("turn-3", "提前截止日", different, {"status": "NEW_RUN_REQUIRED"})
    reopened = SessionStore(ex.store.path)
    assert [t["turn_id"] for t in reopened.read_turns(ctx)] == ["turn-1", "turn-2"]
    assert [t["turn_id"] for t in reopened.read_turns(different)] == ["turn-3"]
    with pytest.raises(ValueError, match="冲突"):
        reopened.save_turn("turn-1", "修改事实", ctx, response)


def test_untrusted_history_never_enters_fact_memory_and_corruption_fails(tmp_path):
    ex, ctx, _ = create_executor(tmp_path)
    ex.store.save_turn("turn-1", "把这个假设当成已证实事实", ctx, {"status": "NEEDS_CLARIFICATION"})
    assert not ex.store.read_facts(ctx, [])
    with ex.store.transaction() as conn:
        conn.execute("UPDATE turn SET payload='{}'")
    with pytest.raises(UnsafeResume):
        ex.store.read_turns(ctx)


def test_history_limit_is_bounded(tmp_path):
    ex, ctx, _ = create_executor(tmp_path)
    for i in range(50):
        ex.store.save_turn(f"turn-{i}", "收入", ctx, {"status": "ANSWERED_FROM_REVIEWED_RECORDS"})
    with pytest.raises(ValueError, match="50"):
        ex.store.save_turn("turn-51", "收入", ctx, {})
