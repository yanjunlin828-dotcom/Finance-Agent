"""Real executor state transitions behind web controls; synthetic tools, zero provider calls."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import shutil
import sys

import pytest
from finresearch.contracts.supplement import SupplementGap, ToolResult
from finresearch.retrieval.corpus import file_sha256
from finresearch.workflow.session_inputs import source_inputs
from finresearch.workflow.supplement_session import SupplementSession
from finresearch_web.service import Service, ApiError
from finresearch_web.projection import project
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from s5.helpers import setup, ROOT


@pytest.fixture
def waiting(tmp_path, monkeypatch):
    gap = SupplementGap(gap_id='web-waiting-source', company_id='002371.SZ', gap_type='SOURCE_REQUEST',
        severity='CRITICAL', description='需要新资料', closing_condition='NEW_SNAPSHOT_OR_USER_INPUT')
    baseline, ctx, deps, calls, _ = setup(gaps=[gap])
    corpus, _, _, evidence, _, _ = source_inputs(ROOT, ctx)
    baseline['evidence'] = list({e['evidence_id']: e for e in [*[e.model_dump(mode='json') for e in evidence], *baseline['evidence']]}.values())
    def tool(c, action):
        calls.append(action)
        return ToolResult(action_id=action.action_id, status='NEEDS_INPUT' if action.gap_id == 'web-waiting-source' else 'NO_EVIDENCE',
                          note='仅测试工具：缺资料')
    deps = replace(deps, execute_tool=tool)
    ctx = ctx.model_copy(update={'run_id': 'web-waiting-fixture'})
    for name in ['configs/s1/model.json', 'protocols/revenue_quality_v1.json',
                 'storage/s3/corpora/s3-semiconductor-equipment-ar-v1/ready.json']:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, target)
    anchor = tmp_path / 'anchor.json'
    anchor.write_text('{}', encoding='utf-8')
    executor = SupplementSession(tmp_path, ctx.run_id, deps, corpus.documents, baseline)
    executor.create(ctx, {'anchor.json': file_sha256(anchor)}, {'schema_version': 1, 'workflow': 'B2',
        'execution_mode': 'RULES', 'baseline_run': 'fixture-parent', 'maximum_context_utf8_bytes': 200000})
    executor.run()
    service = Service(tmp_path, live_budget='0', launch=False)
    payload = {'question': '核查收入与现金流。', 'workflow': 'B2', 'execution_mode': 'RULES',
        'baseline_task_id': 'fixture-parent', **{k: ctx.model_dump(mode='json')[k] for k in
            ['company_ids', 'fiscal_years', 'as_of_date', 'corpus_snapshot_id']}}
    # Register a test-owned task only in the temporary coordinator, not real history.
    with service.transaction() as conn:
        conn.execute('INSERT INTO request VALUES(?,?,?,?,?,?,?,?,?)',
            ('waiting-fixture', 'test-digest', ctx.run_id, json.dumps(payload), '2026-10-01T00:00:00Z', 'STOPPED', 0, None, '0'))
    from finresearch_web import worker
    def construct(*args, **kwargs):
        assert kwargs == {'resume': True, 'require_model': False}
        return executor
    monkeypatch.setattr(worker, 'construct', construct)
    return service, executor, calls


def decide(service, executor, decision='continue_with_limitations'):
    snapshot = service.snapshot(executor.task_id)['snapshot']
    return {'request_id': 'human-one', 'expected_cursor': snapshot['runtime']['event_cursor'],
        'checkpoint_sha256': snapshot['runtime']['checkpoint_sha256'], 'gap_id': 'web-waiting-source', 'decision': decision}


def test_checkpoint_decision_is_persisted_but_never_auto_resumes(waiting):
    service, executor, calls = waiting
    before = service.snapshot(executor.task_id)['snapshot']
    assert before['task']['task_status'] == 'WAITING_INPUT'
    assert not next(a for a in before['task']['available_actions'] if a['operation'] == 'RESUME')['enabled']
    payload = decide(service, executor)
    answer = service.control(executor.task_id, 'clarify', payload)
    assert service.control(executor.task_id, 'clarify', payload) == answer
    after = service.snapshot(executor.task_id)['snapshot']
    assert after['task']['task_status'] == 'WAITING_INPUT'
    assert after['runtime']['clarification_status'] == 'READY_TO_RESUME'
    assert next(a for a in after['task']['available_actions'] if a['operation'] == 'RESUME')['enabled']
    assert not after['publication']['result_available'] and len(calls) == 1
    dispatched = []
    service.dispatch = lambda task_id, **kw: dispatched.append((task_id, kw))
    resume = {'request_id': 'resume-one', 'expected_cursor': after['runtime']['event_cursor']}
    service.control(executor.task_id, 'resume', resume)
    service.control(executor.task_id, 'resume', resume)
    assert dispatched == [(executor.task_id, {'resume': True})]
    final = executor.run()
    assert final['execution_status'] == 'PARTIAL'
    assert next(g for g in final['supplement']['gaps'] if g['gap_id'] == 'web-waiting-source')['status'] == 'LIMITED'
    assert service.snapshot(executor.task_id)['snapshot']['publication']['verification'] == 'VERIFIED'


@pytest.mark.parametrize('change', ['checkpoint_sha256', 'gap_id', 'expected_cursor'])
def test_foreign_or_stale_decision_is_rejected_before_core_write(waiting, change):
    service, executor, _ = waiting
    payload = decide(service, executor)
    payload[change] = '0' * 64 if change == 'checkpoint_sha256' else 'another-gap' if change == 'gap_id' else 999
    with pytest.raises(ApiError):
        service.control(executor.task_id, 'clarify', payload)
    assert not executor.pending_clarifications()


def test_new_snapshot_does_not_resume_or_publish_old_task(waiting):
    service, executor, calls = waiting
    service.control(executor.task_id, 'clarify', decide(service, executor, 'new_snapshot_required'))
    snapshot = service.snapshot(executor.task_id)['snapshot']
    assert snapshot['runtime']['clarification_status'] == 'NEW_RUN_REQUIRED'
    assert not next(a for a in snapshot['task']['available_actions'] if a['operation'] == 'RESUME')['enabled']
    assert snapshot['task']['task_status'] == 'WAITING_INPUT' and len(calls) == 1


def test_stopped_waiting_cancel_confirms_without_another_tool_or_publication(waiting):
    service, executor, calls = waiting
    cursor = service.snapshot(executor.task_id)['snapshot']['runtime']['event_cursor']
    service.control(executor.task_id, 'cancel', {'request_id': 'cancel-wait', 'expected_cursor': cursor})
    snapshot = service.snapshot(executor.task_id)['snapshot']
    assert snapshot['task']['task_status'] == 'CANCELLED' and snapshot['runtime']['cancel_requested']
    assert not snapshot['publication']['result_available'] and len(calls) == 1


def test_followup_is_saved_once_and_does_not_change_core_terminal(waiting):
    service, executor, calls = waiting
    service.control(executor.task_id, 'clarify', decide(service, executor))
    executor.run()
    count = len(calls)
    payload = {'request_id': 'turn-one', 'question': '收入增长情况', 'topic': 'revenue'}
    answer = service.control(executor.task_id, 'followup', payload)
    assert answer['claims']
    assert service.control(executor.task_id, 'followup', payload) == answer
    with executor.store.transaction() as conn:
        assert conn.execute('SELECT COUNT(*) FROM turn').fetchone()[0] == 1
    assert len(calls) == count and executor.store.task()['status'] == 'PARTIAL'


def test_task_bound_pdf_matches_registered_source_without_initializing_service():
    service = Service.__new__(Service)
    service.root = ROOT
    service.snapshot = lambda id: project(ROOT, id)
    id = 's7-live-20261001-01-b1'
    doc = service.snapshot(id)['snapshot']['research']['documents'][0]
    assert file_sha256(service.pdf(id, doc['document_id'])) == doc['document_sha256']
    with pytest.raises(ApiError):
        service.pdf(id, 'unregistered-document')
