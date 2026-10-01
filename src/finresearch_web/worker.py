"""One independent, OS-locked worker reusing the existing B1/B2 executors."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path

from finresearch.contracts import stable_sha256
from finresearch.contracts.research import ResearchContext
from finresearch.retrieval.corpus import file_sha256
from finresearch.storage.session_store import SessionStore, task_lock, Cancelled, UnsafeResume
from finresearch.workflow.session_inputs import make_dependencies, source_inputs, checked_path
from finresearch.workflow.session_executor import SessionExecutor, atomic_artifact, json_text, validate_input_lock, verify_published_output
from finresearch.workflow.supplement_session import SupplementSession
from finresearch.workflow.supplement_session_inputs import make_supplement_dependencies
from finresearch.workflow.supplement_controller import action_fingerprint
from .projection import read_core, checkpoint


def context_for(root: Path, task_id: str, request: dict) -> ResearchContext:
    """Bind only server-approved scope and the current protocol hash."""
    protocol = json.loads((root / 'protocols/revenue_quality_v1.json').read_text(encoding='utf-8'))
    return ResearchContext(run_id=task_id, company_ids=request['company_ids'], fiscal_years=request['fiscal_years'],
        as_of_date=request['as_of_date'], corpus_snapshot_id=request['corpus_snapshot_id'], protocol_id=protocol['protocol_id'],
        protocol_version=protocol['version'], protocol_config_sha256=stable_sha256(protocol))


def construct(root: Path, task_id: str, request: dict, *, resume=False, require_model=True):
    """Assemble verified dependencies without changing research rules or old task locks."""
    runtime = root / 'storage/s6/tasks' / task_id
    if resume:
        store = SessionStore(runtime / 'session.sqlite')
        manifest = store.task()['manifest']
        validate_input_lock(root, manifest['input_lock'])
        context, policy = ResearchContext.model_validate(manifest['context']), manifest['policy']
    else:
        for name in ('runs/s3/s3-gate-20260930-01/gate_report.json', 'runs/s4/s4-gate-20260930-01/gate_report.json'):
            gate = json.loads((root / name).read_text(encoding='utf-8'))
            if gate.get('decision') != 'GO' or not gate.get('all_checks_passed'):
                raise ValueError('上游限定工程门禁未通过')
        context = context_for(root, task_id, request)
        policy = json.loads((root / 'configs/s6/runtime.json').read_text(encoding='utf-8'))
        policy.update(execution_mode=request['execution_mode'], workflow=request['workflow'])
        if runtime.exists() or (root / 'runs/s6' / task_id).exists():
            raise FileExistsError('身份已存在，拒绝重复创建')
        runtime.mkdir(parents=True)
        store = SessionStore(runtime / 'session.sqlite')
    if request['workflow'] == 'B1':
        # REPLAY is an explicitly selected server capability, never a LIVE fallback.
        replay = checked_path(root, request['replay_path'], 'runs/s4') if request.get('replay_path') else None
        policy['replay_path'] = request.get('replay_path')
        deps, corpus, locks, _ = make_dependencies(root, context, store, policy, replay)
        executor = SessionExecutor(root, task_id, deps, corpus.documents)
    else:
        if resume:
            baseline = json.loads((runtime / 'baseline.json').read_text(encoding='utf-8'))
        else:
            parent = request['baseline_task_id']
            parent_core = read_core(root, parent)
            class ReadOnlyParent:
                def task(self):
                    return {'manifest': parent_core['manifest'], 'status': parent_core['status'], 'cancel_requested': parent_core['cancel']}
            parent_store = ReadOnlyParent()
            parent_manifest = parent_core['manifest']
            validate_input_lock(root, parent_manifest['input_lock'])
            baseline = deepcopy(verify_published_output(root / 'runs/s6' / parent, parent_store))
            if 'supplement' in baseline:
                raise ValueError('B2只能绑定B1父任务')
            if {k: v for k, v in baseline['context'].items() if k != 'run_id'} != {k: v for k, v in context.model_dump(mode='json').items() if k != 'run_id'}:
                raise ValueError('父B1与补查范围不一致')
            protocol = json.loads((root / 'protocols/revenue_quality_v1.json').read_text(encoding='utf-8'))
            if baseline.get('trace') != protocol['nodes'] or any(not c['evidence_ids'] for c in baseline['claims'] if c['kind'] == 'DISCLOSED'):
                raise ValueError('父B1流程或披露来源不完整')
            gate = json.loads((root / 'runs/s5/s5-gate-20261001-01/gate_report.json').read_text(encoding='utf-8'))
            if gate.get('decision') != 'GO' or not gate.get('all_checks_passed'):
                raise ValueError('G5未通过')
            _, _, rows, evidence, _, _ = source_inputs(root, context)
            if baseline['observations'] != [o.model_dump(mode='json') for o in rows]:
                raise ValueError('父B1财务输入不匹配')
            baseline['evidence'] = list({e['evidence_id']: e for e in [*[e.model_dump(mode='json') for e in evidence], *baseline['evidence']]}.values())
            policy.update(baseline_run=parent, baseline_kind='S6', scenario='natural')
            for name, payload in [('baseline.json', baseline), ('policy.json', policy),
                    ('baseline_revalidation.json', {'kind': 'S6_CURRENT_B1', 'run_id': parent, 'published_sources_verified': True}),
                    ('fault_injection.json', {'type': 'NONE'})]:
                atomic_artifact(runtime / name, json_text(payload))
        capability_policy = policy if require_model else {**policy, 'execution_mode': 'RULES'}
        deps, corpus, locks, _, model, checks = make_supplement_dependencies(root, context, store, capability_policy, baseline)
        # Preserve real parent retrieval fingerprints instead of reading the CLI's default parent.
        seen = set(deps.initial_seen)
        for event in read_core(root, policy['baseline_run'])['events']:
            payload = event['payload']
            if payload.get('event') == 'RETRIEVAL':
                q = payload['query']
                seen.add(action_fingerprint('SEARCH_DISCLOSURE', q['company_id'], context,
                    {'question': q['question'], 'reporting_year': q['reporting_year'], 'need': 'EXPLANATION'}))
        deps = replace(deps, initial_seen=seen)
        def observed(name, callback):
            def invoke(*args):
                round_number = args[1] if name == 'plan_actions' else checkpoint(root, task_id).get('rounds')
                round_number = round_number if isinstance(round_number, int) and round_number > 0 else None
                event = {'node': name, 'round': round_number, 'origin': 'WEB_WORKER_OBSERVATION'}
                store.event({**event, 'event': 'START'})
                try:
                    result = callback(*args)
                    store.event({**event, 'event': 'END'})
                    return result
                except Exception:
                    store.event({**event, 'event': 'FAILED'})
                    raise
            return invoke
        # Observe actual callback execution; END still precedes graph checkpoint acceptance.
        deps = replace(deps, select_actions=observed('plan_actions', deps.select_actions),
            execute_tool=observed('execute_actions', deps.execute_tool),
            select_quotes=observed('write_supplement_report', deps.select_quotes))
        if not resume:
            atomic_artifact(runtime / 'source_checks.json', json_text(checks))
            if request['execution_mode'] == 'LIVE':
                atomic_artifact(runtime / 'model_config.json', json_text(model))
            locks.update({p.relative_to(root).as_posix(): file_sha256(p) for p in runtime.glob('*.json')})
            for name in ('publication.json', 'final_state.json', 'context_capsule.json', 'report.md'):
                p = root / 'runs/s6' / request['baseline_task_id'] / name
                locks[p.relative_to(root).as_posix()] = file_sha256(p)
        executor = SupplementSession(root, task_id, deps, corpus.documents, baseline)
    if not resume:
        # Freeze integration source as well; a later service change cannot silently resume this worker.
        locks.update({p.relative_to(root).as_posix(): file_sha256(p) for p in (root / 'src/finresearch_web').rglob('*.py')})
        executor.create(context, locks, policy)
    return executor


def execute(root: Path, service, task_id: str, resume=False) -> None:
    """Hold dispatch lock until process exit; task locks and provider journals remain authoritative."""
    row = service.record(task_id)
    with task_lock(root / 'storage/web/worker-locks' / (task_id + '.lock')):
        try:
            executor = construct(root, task_id, row['request'], resume=resume)
            if service.record(task_id)['cancel']:
                executor.store.cancel()
            executor.run()
            service.finished(task_id, 'STOPPED')
        except (Cancelled, UnsafeResume):
            service.finished(task_id, 'STOPPED')
        except Exception as exc:
            service.finished(task_id, 'STOPPED', type(exc).__name__)
            # Construction can fail before a task row exists; coordinator error remains visible.
            path = root / 'storage/s6/tasks' / task_id / 'session.sqlite'
            if path.is_file():
                try:
                    store = SessionStore(path)
                    store.task()
                    if store.task()['status'] not in {'COMPLETED', 'PARTIAL', 'BLOCKED', 'CANCELLED'}:
                        store.set_status('FAILED')
                except Exception:
                    pass
