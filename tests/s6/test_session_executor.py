from dataclasses import replace
import json
import pytest

from finresearch.workflow.session_executor import SessionExecutor
from finresearch.storage.session_store import Cancelled, UnsafeResume
from .helpers import create_executor


class SimulatedProcessDeath(BaseException):
    """Bypass exception handlers, like process termination; DB writes remain."""


@pytest.mark.parametrize("point", ["node:calculate:END", "action:hypotheses:COMMITTED",
                                   "action:collect_evidence:COMMITTED", "action:disclosures:COMMITTED",
                                   "action:writer:COMMITTED", "publish:REPORT_WRITTEN", "publish:MANIFEST_WRITTEN"])
def test_resume_after_failure_replays_committed_results_once(tmp_path, point):
    counts = {}
    def crash(at):
        if at == point:
            raise SimulatedProcessDeath()
    ex, _, _ = create_executor(tmp_path, hook=crash, counts=counts)
    with pytest.raises(SimulatedProcessDeath):
        ex.run()
    reopened = SessionExecutor(tmp_path, ex.task_id, ex.deps, {})
    state = reopened.run()
    assert state["trace"] == ex.deps.protocol["nodes"]
    assert state["execution_status"] == "COMPLETED" and state["validation"]["status"] == "PASS"
    assert all(count == 1 for count in counts.values())
    assert reopened.verify_publication() == state
    repeated = reopened.run()
    assert repeated == state and all(count == 1 for count in counts.values())


def test_pending_model_is_blocked_on_resume(tmp_path):
    ex, _, counts = create_executor(tmp_path)
    original = ex.deps.propose_hypotheses
    def model(*args):
        original(*args)
        raise SimulatedProcessDeath()
    ex.deps = replace(ex.deps, propose_hypotheses=model)
    with pytest.raises(SimulatedProcessDeath):
        ex.run()
    reopened = SessionExecutor(tmp_path, ex.task_id, replace(ex.deps, propose_hypotheses=original), {})
    with pytest.raises(UnsafeResume):
        reopened.run()
    assert reopened.store.task()["status"] == "BLOCKED"
    assert sum(counts.values()) == 1
    assert not (reopened.output / "publication.json").exists()


@pytest.mark.parametrize("point", ["node:calculate:END", "action:hypotheses:COMMITTED", "publish:BEFORE"])
def test_cancel_prevents_later_calls_and_completion(tmp_path, point):
    ex, _, counts = create_executor(tmp_path)
    ex.hook = lambda at: ex.store.cancel() if at == point else None
    with pytest.raises(Cancelled):
        ex.run()
    before = dict(counts)
    assert ex.store.task()["status"] == "CANCELLED"
    assert not (ex.output / "publication.json").exists()
    with pytest.raises(Cancelled):
        SessionExecutor(tmp_path, ex.task_id, ex.deps, {}).run()
    assert counts == before


def test_cancel_during_call_preserves_result_and_stops_next_company(tmp_path):
    ex, _, counts = create_executor(tmp_path)
    original = ex.deps.propose_hypotheses
    def model(*args):
        result = original(*args)
        ex.store.cancel()
        return result
    ex.deps = replace(ex.deps, propose_hypotheses=model)
    with pytest.raises(Cancelled):
        ex.run()
    assert sum(counts.values()) == 1
    with ex.store.transaction() as conn:
        assert conn.execute("SELECT status FROM action WHERE name='hypotheses'").fetchone()[0] == "DONE"


def test_version_change_is_safe_failure_and_does_not_repeat_calls(tmp_path):
    ex, _, counts = create_executor(tmp_path, hook=lambda p: (_ for _ in ()).throw(SimulatedProcessDeath()) if p == "node:calculate:END" else None)
    with pytest.raises(SimulatedProcessDeath):
        ex.run()
    (tmp_path / "locked.json").write_text('{"changed":true}', encoding="utf-8")
    with pytest.raises(UnsafeResume, match="版本"):
        SessionExecutor(tmp_path, ex.task_id, ex.deps, {}).run()
    assert not counts and ex.store.task()["status"] == "BLOCKED"


def test_partial_model_failure_remains_partial(tmp_path):
    ex, _, _ = create_executor(tmp_path)
    ex.deps = replace(ex.deps, propose_hypotheses=lambda *args: (_ for _ in ()).throw(RuntimeError("provider failed")))
    state = ex.run()
    assert state["execution_status"] == "PARTIAL"
    assert ex.store.task()["status"] == "PARTIAL"
    assert state["validation"]["status"] == "PASS"


def test_report_corruption_and_context_checkpoint_change_refused(tmp_path):
    ex, _, _ = create_executor(tmp_path)
    ex.run()
    (ex.output / "report.md").write_text("fabricated", encoding="utf-8")
    with pytest.raises(UnsafeResume, match="产物"):
        ex.run()
    assert ex.store.task()["status"] == "BLOCKED"


def test_failed_publication_keeps_no_false_completion_and_can_recover(tmp_path):
    ex, _, counts = create_executor(tmp_path)
    def failure(point):
        if point == "publish:REPORT_WRITTEN":
            raise OSError("disk failure")
    ex.hook = failure
    with pytest.raises(OSError):
        ex.run()
    assert ex.store.task()["status"] == "FAILED"
    assert not (ex.output / "publication.json").exists()
    reopened = SessionExecutor(tmp_path, ex.task_id, ex.deps, {})
    reopened.run()
    assert all(n == 1 for n in counts.values())

