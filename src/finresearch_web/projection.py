"""Read-only task projection; source time, numeric strings and publication gates stay intact."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import sqlite3

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from finresearch.contracts import stable_sha256
from finresearch.retrieval.corpus import file_sha256
from finresearch.storage.session_store import safe_task_id, UnsafeResume
from finresearch.workflow.session_executor import validate_input_lock
from .contracts.models import TaskSnapshot, DisplayEvent
from .contracts.rules import action_capabilities

LABELS = {'002371.SZ': '北方华创', '688072.SH': '拓荆科技', '688082.SH': '盛美上海'}


def path_in(root: Path, relative: str) -> Path:
    """Resolve server-owned locators; never permit symlink escape."""
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError('SOURCE_INTEGRITY_FAILED')
    return path


@contextmanager
def readonly(path: Path):
    """Read a pre-existing SQLite file in a consistent transaction, without schema setup."""
    if not path.is_file():
        raise FileNotFoundError('TASK_NOT_FOUND')
    conn = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=10)
    try:
        conn.execute('PRAGMA query_only=ON')
        conn.execute('BEGIN')
        yield conn
    finally:
        conn.rollback()
        conn.close()


def read_core(root: Path, task_id: str) -> dict:
    """Return manifest, status, actions and source event IDs from one DB snapshot."""
    db = path_in(root, f'storage/s6/tasks/{safe_task_id(task_id)}/session.sqlite')
    with readonly(db) as conn:
        if conn.execute('PRAGMA user_version').fetchone()[0] != 1:
            raise ValueError('UNSUPPORTED_CONTRACT_VERSION')
        row = conn.execute('SELECT manifest,digest,status,cancel FROM task WHERE id=1').fetchone()
        if not row:
            raise ValueError('TASK_NOT_FOUND')
        manifest = json.loads(row[0])
        if stable_sha256(manifest) != row[1] or manifest.get('schema_version') != 1 or manifest['context']['run_id'] != task_id:
            raise ValueError('SOURCE_INTEGRITY_FAILED')
        events = [{'id': n, 'time': time, 'payload': json.loads(raw)} for n, time, raw in
                  conn.execute('SELECT id,created_at,payload FROM event ORDER BY id')]
        actions = []
        for name, status, attempts, payload, digest in conn.execute('SELECT name,status,attempts,payload,digest FROM action'):
            if status == 'DONE' and (payload is None or stable_sha256(json.loads(payload)) != digest):
                raise ValueError('SOURCE_INTEGRITY_FAILED')
            actions.append((name, status, attempts))
        clarifications = []
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='clarification'").fetchone():
            for raw, digest in conn.execute('SELECT payload,digest FROM clarification ORDER BY rowid'):
                payload = json.loads(raw)
                if stable_sha256(payload) != digest:
                    raise ValueError('SOURCE_INTEGRITY_FAILED')
                clarifications.append(payload)
    return {'manifest': manifest, 'status': row[2], 'cancel': bool(row[3]), 'events': events,
            'cursor': events[-1]['id'] if events else 0, 'actions': actions, 'clarifications': clarifications}


def checkpoint(root: Path, task_id: str) -> dict:
    """Decode the pinned SQLite checkpoint format without invoking graph/setup or merging pending writes."""
    path = path_in(root, f'storage/s6/tasks/{task_id}/checkpoints.sqlite')
    if not path.is_file():
        return {}
    with readonly(path) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='checkpoints'").fetchone():
            return {}
        row = conn.execute("SELECT type,checkpoint FROM checkpoints WHERE thread_id=? AND checkpoint_ns='' ORDER BY checkpoint_id DESC LIMIT 1", (task_id,)).fetchone()
    if not row:
        return {}
    # Only the locally pinned serializer and existing application-owned DB are accepted.
    value = JsonPlusSerializer(pickle_fallback=False).loads_typed((row[0], row[1]))
    return {k: v for k, v in value['channel_values'].items() if not k.startswith('__') and not k.startswith('branch:')}


def publication(root: Path, task_id: str, core: dict) -> tuple[dict, dict, list]:
    """Require every published digest, business PASS, frozen scope and DB terminal; cancel wins."""
    output = f'runs/s6/{task_id}'
    meta = path_in(root, output + '/publication.json')
    if not meta.is_file():
        return {'verification': 'NOT_PUBLISHED'}, {}, []
    refs = []
    try:
        published = json.loads(meta.read_text(encoding='utf-8'))
        names = {'report.md', 'final_state.json', 'context_capsule.json'}
        if published.get('schema_version') != 1 or set(published['files']) != names:
            raise ValueError()
        for name, digest in published['files'].items():
            p = path_in(root, output + '/' + name)
            if not p.is_file() or file_sha256(p) != digest:
                raise ValueError()
            refs.append({'path': f'{output}/{name}', 'sha256': digest})
        state = json.loads(path_in(root, output + '/final_state.json').read_text(encoding='utf-8'))
        capsule = json.loads(path_in(root, output + '/context_capsule.json').read_text(encoding='utf-8'))
        if (core['cancel'] or core['status'] not in {'COMPLETED', 'PARTIAL'} or
                published['status'] != core['status'] or state['execution_status'] != core['status'] or
                state['context'] != core['manifest']['context'] or state['validation']['status'] != 'PASS' or
                capsule['payload']['context'] != state['context'] or
                stable_sha256(capsule['payload']) != capsule['payload_sha256'] or
                path_in(root, output + '/report.md').read_text(encoding='utf-8') != state['report']):
            raise ValueError()
        # Finance page windows live in the lossless capsule, not always final_state.
        state['evidence'] = list({e['evidence_id']: e for e in [*capsule['payload']['evidence'], *state.get('evidence', [])]}.values())
        return {'verification': 'VERIFIED', 'result_available': True, 'publication_status': core['status'],
                'business_validation': 'PASS', 'scope_matches': True,
                'files': [{'name': name, 'sha256': digest, 'digest_matches': True} for name, digest in published['files'].items()]}, state, refs
    except (ValueError, KeyError, OSError):
        return {'verification': 'INVALID', 'reasons': ['发布文件、摘要、范围、业务校验或终态不一致；不开放报告。']}, {}, refs


def research_view(state: dict, workflow: str) -> dict:
    """Project accepted source objects without frontend math or invented references."""
    body = state.get('current', state) if workflow == 'B2' else state
    grouped = body.get('calculations', {})
    result = {k: body.get(k, []) for k in ('observations', 'claims', 'gaps')}
    result.update(calculations=[r for slots in grouped.values() for r in slots.values()],
                  calculation_slots={c: {role: r['calculation_id'] for role, r in slots.items()} for c, slots in grouped.items()},
                  evidence=[{'record': e} for e in body.get('evidence', [])], report_markdown=state.get('report'))
    ledger = state.get('supplement') or (state if workflow == 'B2' and 'rounds' in state else None)
    if ledger is not None:
        result['supplement'] = {k: ledger.get(k, 0) for k in ('rounds', 'action_count', 'no_progress_rounds')}
        result['supplement'].update(stop_reason=ledger.get('stop_reason') or None, gaps=ledger.get('gaps', []),
            history=[{'action': h['action'], 'result': h.get('result'), 'changed_gap_status': h.get('changed_gap_status'),
                      'failure_type': h.get('exception_type')} for h in ledger.get('history', [])])
        result['gaps'] = body.get('gaps', [])
    return result


def budget_view(root: Path, task_id: str) -> dict:
    """Read settled estimate and unknown reservations; never instantiate/reset a budget guard."""
    path = path_in(root, f'storage/s6/tasks/{task_id}/budget.sqlite')
    if not path.is_file():
        return {}
    with readonly(path) as conn:
        row = conn.execute('SELECT payload FROM budget WHERE id=1').fetchone()
    value = json.loads(row[0])
    pending = sum((Decimal(v) for v in value['pending'].values()), Decimal(0))
    return {'settled_estimate': str(Decimal(value['reserved_cost']) - pending), 'pending_reservation': str(pending),
            'total_calls': value['total_calls'], 'note': '保守估算，不是供应商账单；未知 usage 保留预留。'}


def document_registry(root: Path, context: dict) -> dict:
    """Read only documents bound to the frozen snapshot, companies and disclosure cutoff."""
    ready = path_in(root, f"storage/s3/corpora/{context['corpus_snapshot_id']}/ready.json")
    data = json.loads(ready.read_text(encoding='utf-8'))
    return {d['manifest']['document_id']: d for d in data['documents'] if
            d['manifest']['ts_code'] in context['company_ids'] and d['manifest']['published_on'] <= context['as_of_date']}


def resume_safe(actions: list[tuple[str, str, int]], budget: dict, *, active: bool) -> bool:
    """Mirror the journal boundary: only known read-only calls may retry, at most twice.

    A stopped process does not prove that an external model never executed.
    Pending cost and unknown/failed actions remain unsafe; no automatic retry.
    """
    if active or Decimal(budget.get('pending_reservation', '0')) > 0:
        return False
    readonly_names = {'load_financials', 'collect_evidence', 'b2-tool'}
    return all(status == 'DONE' or name in readonly_names and status in {'RUNNING', 'RETRYABLE'} and attempts < 2
               for name, status, attempts in actions)


def project(root: Path, task_id: str, record: dict | None = None, *, active=False) -> dict:
    """Read source snapshots and checkpoint data; retry if the core event boundary moves."""
    root = root.resolve()
    for _ in range(4):
        core = read_core(root, task_id)
        saved = checkpoint(root, task_id)
        pub, final, refs = publication(root, task_id, core)
        after = read_core(root, task_id)
        if (core['cursor'], core['status'], core['cancel']) == (after['cursor'], after['status'], after['cancel']):
            break
    else:
        raise ValueError('DEPENDENCY_NOT_AVAILABLE')
    manifest = core['manifest']
    workflow = manifest['policy'].get('workflow', 'B1')
    owned = record is not None
    origin = 'CURRENT_EXECUTION' if owned else 'HISTORICAL_VIEW'
    if owned and manifest['policy']['execution_mode'] == 'REPLAY':
        origin = 'MODEL_REPLAY'
    context = manifest['context']
    if saved and saved.get('context') != context:
        raise ValueError('SOURCE_INTEGRITY_FAILED')
    state = final or saved
    research = research_view(state, workflow)
    if not pub.get('result_available'):
        research['report_markdown'] = None
    registered = document_registry(root, context)
    research['documents'] = [{'document_id': id, 'company_id': d['manifest']['ts_code'], 'title': d['manifest']['title'],
        'published_on': d['manifest']['published_on'], 'document_sha256': d['manifest']['sha256'],
        'page_count': d['manifest']['page_count'], 'pdf_access': 'AVAILABLE'} for id, d in registered.items()]
    accepted = state.get('trace', [])
    steps = []
    events = []
    current = None
    for event in core['events']:
        p, seq = event['payload'], event['id']
        kind = {'START': 'NODE_START', 'END': 'NODE_RETURNED', 'ACTION_COMMITTED': 'CALL_COMMITTED',
                'CANCEL_REQUESTED': 'CANCEL_REQUESTED', 'WAITING_INPUT': 'WAITING_INPUT'}.get(p.get('event'), 'TASK_STATUS')
        node = p.get('node')
        if node and p.get('event') == 'START':
            sid = f'{task_id}:node:{seq}'
            steps.append({'step_id': sid, 'instance_id': sid, 'node_id': node, 'identity_kind': 'EXECUTION',
                          'origin': origin, 'round': p.get('round'), 'lifecycle': 'RUNNING', 'occurred_at': event['time']})
            current = sid
        if node and p.get('event') in {'END', 'FAILED'}:
            for step in reversed(steps):
                if step['node_id'] == node:
                    step['lifecycle'] = 'FAILED' if p['event'] == 'FAILED' else 'FUNCTION_RETURNED'
                    step['acceptance'] = 'PUBLISHED' if final else 'CHECKPOINT_CONFIRMED' if node in accepted else 'UNKNOWN'
                    break
        events.append(DisplayEvent(task_id=task_id, seq=seq, type=kind, origin=origin, occurred_at=event['time'],
                                   source_event_ids=[seq], event_stream_id=f'core-session:{task_id}:schema1').model_dump(mode='json'))
    waiting = saved if core['status'] == 'WAITING_INPUT' else {}
    gaps = [g['gap_id'] for g in waiting.get('gaps', []) if g['status'] == 'WAITING_INPUT']
    checkpoint_sha = stable_sha256(waiting) if gaps else None
    clarification = 'NONE'
    for item in core['clarifications']:
        if item['checkpoint_sha256'] == checkpoint_sha and item['gap_id'] in gaps:
            clarification = 'READY_TO_RESUME' if item['decision'] == 'continue_with_limitations' else 'NEW_RUN_REQUIRED'
    safe = False
    version_ok = True
    budget = budget_view(root, task_id)
    try:
        validate_input_lock(root, manifest['input_lock'])
        safe = resume_safe(core['actions'], budget, active=active)
    except (ValueError, RuntimeError, UnsafeResume):
        version_ok = False
    runtime = {'connection_state': 'CONNECTED', 'event_cursor': core['cursor'], 'event_stream_id': f'core-session:{task_id}:schema1',
        'worker_state': 'ACTIVE' if active else 'STOPPED', 'queue_state': 'DISPATCHED' if active else 'NOT_APPLICABLE',
        'cancel_requested': core['cancel'], 'current_step_id': current if core['status'] == 'RUNNING' else None,
        'resume_readiness': 'CONFIRMED_SAFE' if owned and safe else 'UNSAFE' if owned else 'UNKNOWN',
        'waiting_gap_ids': gaps, 'checkpoint_sha256': checkpoint_sha, 'clarification_status': clarification}
    baseline = None
    if workflow == 'B2':
        parent = manifest['policy']['baseline_run']
        baseline = {'task_id': parent, 'label': '复用已发布 B1；本任务只执行有界补查'}
        budget['baseline_historical_estimate'] = budget_view(root, parent).get('settled_estimate')
    data = {'task': {'task_id': task_id, 'question': record['request']['question'] if record else '比较收入、经营现金流和应收账款，核查披露与局限。',
        'context': context, 'workflow': workflow, 'execution_mode': manifest['policy']['execution_mode'], 'origin': origin,
        'task_status': core['status'], 'access_mode': 'LIVE_CONTROL' if owned else 'HISTORY_READ_ONLY',
        'baseline': baseline, 'company_labels': {id: LABELS.get(id, id) for id in context['company_ids']},
        'created_at': record['created_at'] if record else None}, 'runtime': runtime, 'publication': pub,
        'research': research, 'budget': budget, 'steps': steps,
        'provenance': {'kind': 'LIVE_PROJECTION' if owned else 'DERIVED_HISTORICAL', 'label': '真实任务即时读取' if owned else '真实历史记录只读浏览',
            'source_artifacts': refs or [{'path': f'storage/s6/tasks/{task_id}/session.sqlite', 'sha256': file_sha256(path_in(root, f'storage/s6/tasks/{task_id}/session.sqlite'))}],
            'source_revalidation': 'VERIFIED' if version_ok else 'FAILED',
            'notes': ['已发布记录的完整性与当前版本可恢复性分别核验；没有独立语义认证。', '缺独立输出、逐动作轮次和印刷页码保持缺失。']},
        'ui': {'panel': 'PROCESS' if owned else 'REPORT', 'follow_progress': owned}}
    value = TaskSnapshot.model_validate(data)
    data['task']['available_actions'] = [a.model_dump(mode='json') for a in action_capabilities(value)]
    return {'snapshot': TaskSnapshot.model_validate(data).model_dump(mode='json'), 'events': events}
