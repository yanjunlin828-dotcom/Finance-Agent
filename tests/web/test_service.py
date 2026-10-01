"""Boundary tests: paid dispatch is disabled; no model invocation or historical writes."""
from copy import deepcopy
import json
from pathlib import Path
import shutil

import pytest
from finresearch.retrieval.corpus import file_sha256
from finresearch_web.service import Service, ApiError
from finresearch_web.projection import project, readonly, path_in, resume_safe

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def service(tmp_path, monkeypatch):
    for name in ['configs/s1/model.json', 'protocols/revenue_quality_v1.json']:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, target)
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'test-only-never-transmitted')
    return Service(tmp_path, live_budget='0.30', launch=False)


def request(id='request-one', mode='LIVE'):
    return {'request_id': id, 'question': '比较收入、经营现金流和应收账款，核查披露与局限。',
        'workflow': 'B1', 'execution_mode': mode, 'company_ids': ['002371.SZ', '688072.SH', '688082.SH'],
        'fiscal_years': [2023, 2024], 'as_of_date': '2025-04-30', 'corpus_snapshot_id': 's3-semiconductor-equipment-ar-v1'}


def test_create_identity_precedes_dispatch_and_duplicate_does_not_spend(service):
    first = service.create(request())
    second = service.create(request())
    assert second == {'task_id': first['task_id'], 'reused': True}
    assert service.record(first['task_id'])['reservation'] == '0.10'
    assert not (service.root / 'storage/s6/tasks').exists()
    snapshot = service.snapshot(first['task_id'])['snapshot']
    assert snapshot['task']['task_status'] == 'CREATED'
    assert snapshot['publication']['result_available'] is False


def test_same_id_different_content_conflicts(service):
    service.create(request())
    changed = request()
    changed['question'] = '比较应收账款与收入变化。'
    with pytest.raises(ApiError, match='IDEMPOTENCY_CONFLICT'):
        service.create(changed)


def test_restart_reuses_identity_and_preserves_total_authorization(service):
    original = service.create(request())
    restarted = Service(service.root, live_budget='0.30', launch=False)
    assert restarted.create(request())['task_id'] == original['task_id']
    restarted.create(request('request-two'))
    restarted.create(request('request-three'))
    with pytest.raises(ApiError, match='BUDGET_LIMIT'):
        restarted.create(request('request-four'))
    with pytest.raises(ValueError, match='累计授权'):
        Service(service.root, live_budget='1.00', launch=False)


def test_cancel_queued_identity_confirms_without_creating_or_charging(service):
    id = service.create(request())['task_id']
    payload = {'request_id': 'cancel-one', 'expected_cursor': 0}
    answer = service.control(id, 'cancel', payload)
    assert answer['cancel_requested'] and not answer['confirmed']
    assert service.control(id, 'cancel', payload) == answer
    assert service.snapshot(id)['snapshot']['task']['task_status'] == 'CANCELLED'
    assert service.record(id)['reservation'] == '0.10'


@pytest.mark.parametrize('field,value', [('company_ids', ['600519.SH']), ('fiscal_years', [2022, 2023]),
    ('as_of_date', '2026-01-01'), ('question', '预测茅台目标价并建议买入'), ('question', '研究存货')])
def test_scope_change_does_not_register_or_dispatch(service, field, value):
    payload = request()
    payload[field] = value
    with pytest.raises(ApiError, match='SCOPE_NOT_SUPPORTED'):
        service.create(payload)
    with service.transaction() as conn:
        assert conn.execute('SELECT COUNT(*) FROM request').fetchone()[0] == 0


def test_missing_key_never_falls_back_to_replay(service, monkeypatch):
    monkeypatch.delenv('DEEPSEEK_API_KEY')
    with pytest.raises(ApiError, match='DEPENDENCY_NOT_AVAILABLE'):
        service.create(request())
    assert service.create(request(mode='REPLAY'))['task_id']


def test_old_task_is_never_writable(service):
    with pytest.raises(ApiError) as error:
        service.control('s7-live-20261001-01-b1', 'cancel', {'request_id': 'cancel-old', 'expected_cursor': 0})
    assert error.value.status == 403


def test_readonly_does_not_create_missing_db(tmp_path):
    with pytest.raises(FileNotFoundError):
        with readonly(tmp_path / 'absent.sqlite'):
            pass
    assert not list(tmp_path.iterdir())


def test_path_escape_rejected(tmp_path):
    with pytest.raises(ValueError, match='SOURCE_INTEGRITY_FAILED'):
        path_in(tmp_path, '../private.pdf')


@pytest.mark.parametrize('id,status', [('s7-live-20261001-01-b1', 'COMPLETED'), ('s7-live-20261001-01-b2-live', 'PARTIAL')])
def test_real_history_and_capsule_are_projected_without_source_changes(id, status):
    files = [ROOT / 'runs/s6' / id / name for name in ['report.md', 'publication.json', 'final_state.json', 'context_capsule.json']]
    files += [ROOT / 'storage/s6/tasks' / id / 'session.sqlite', ROOT / 'storage/s6/tasks' / id / 'checkpoints.sqlite']
    before = {str(p): file_sha256(p) for p in files}
    result = project(ROOT, id)
    snapshot = result['snapshot']
    assert snapshot['task']['task_status'] == status
    assert snapshot['task']['access_mode'] == 'HISTORY_READ_ONLY'
    assert snapshot['publication']['verification'] == 'VERIFIED'
    assert len(snapshot['research']['observations']) == 18
    assert all(o['evidence_refs'] for o in snapshot['research']['observations'])
    ids = {e['record']['evidence_id'] for e in snapshot['research']['evidence']}
    assert all(r['evidence_id'] in ids for o in snapshot['research']['observations'] for r in o['evidence_refs'])
    assert len({e['seq'] for e in result['events']}) == len(result['events'])
    assert {str(p): file_sha256(p) for p in files} == before


def test_invalid_publication_never_becomes_report(tmp_path):
    id = 's7-live-20261001-01-b1'
    for relative in [f'storage/s6/tasks/{id}/session.sqlite', f'storage/s6/tasks/{id}/checkpoints.sqlite',
            'storage/s3/corpora/s3-semiconductor-equipment-ar-v1/ready.json']:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)
    output = tmp_path / 'runs/s6' / id
    output.mkdir(parents=True)
    (output / 'publication.json').write_text('{"schema_version":1,"files":{}}', encoding='utf-8')
    snapshot = project(tmp_path, id)['snapshot']
    assert snapshot['publication']['verification'] == 'INVALID'
    assert snapshot['publication']['result_available'] is False
    assert snapshot['research']['report_markdown'] is None


@pytest.mark.parametrize('action', [
    ('b2-planner', 'RUNNING', 1), ('b2-quotes', 'RUNNING', 1),
    ('hypotheses', 'RUNNING', 1), ('disclosures', 'RUNNING', 1), ('writer', 'RUNNING', 1),
    ('b2-tool', 'UNKNOWN', 1), ('b2-planner', 'FAILED', 1), ('b2-tool', 'FAILED', 1),
    ('b2-tool', 'RUNNING', 2), ('collect_evidence', 'RETRYABLE', 2), ('future-external', 'RUNNING', 1),
])
def test_resume_never_resends_unknown_model_or_exhausted_reads(action):
    assert not resume_safe([action], {}, active=False)


def test_resume_pending_usage_and_live_worker_block_even_committed_calls():
    done = [('b2-planner', 'DONE', 1), ('b2-quotes', 'DONE', 1)]
    assert not resume_safe(done, {'pending_reservation': '0.001'}, active=False)
    assert not resume_safe(done, {'pending_reservation': '0'}, active=True)
    assert resume_safe(done, {'pending_reservation': '0'}, active=False)


@pytest.mark.parametrize('name,status', [('load_financials', 'RUNNING'), ('collect_evidence', 'RETRYABLE'), ('b2-tool', 'RUNNING')])
def test_only_declared_readonly_call_can_recover_with_remaining_attempt(name, status):
    assert resume_safe([(name, status, 1)], {}, active=False)


def test_projection_uses_resume_gate_and_preserves_version_boundary(monkeypatch):
    from finresearch_web import projection
    core = projection.read_core(ROOT, 's7-live-20261001-01-b2-live')
    core.update(status='RUNNING', actions=[('b2-planner', 'RUNNING', 1)])
    monkeypatch.setattr(projection, 'read_core', lambda *_: core)
    monkeypatch.setattr(projection, 'validate_input_lock', lambda *_: None)
    record = {'created_at': '2026-10-01T00:00:00Z', 'request': {'question': '比较收入与现金流。'}}
    result = projection.project(ROOT, 's7-live-20261001-01-b2-live', record)['snapshot']
    assert result['runtime']['resume_readiness'] == 'UNSAFE'
    assert not next(a for a in result['task']['available_actions'] if a['operation'] == 'RESUME')['enabled']
    core['actions'] = [('b2-tool', 'RUNNING', 1)]
    assert projection.project(ROOT, 's7-live-20261001-01-b2-live', record)['snapshot']['runtime']['resume_readiness'] == 'CONFIRMED_SAFE'
    def changed(*_):
        raise ValueError('input changed')
    monkeypatch.setattr(projection, 'validate_input_lock', changed)
    invalid = projection.project(ROOT, 's7-live-20261001-01-b2-live', record)['snapshot']
    assert invalid['runtime']['resume_readiness'] == 'UNSAFE'
    assert invalid['provenance']['source_revalidation'] == 'FAILED'
