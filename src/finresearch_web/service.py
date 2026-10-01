"""Local single-user coordinator: durable identities, bounded grants and explicit controls."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import threading
import uuid

from finresearch.contracts import stable_sha256
from finresearch.contracts.research import ResearchContext
from finresearch.storage.session_store import SessionStore, task_lock, safe_task_id
from finresearch.workflow.session_context import answer_followup
from finresearch.workflow.supplement_session import supplement_followup
from .contracts.models import CreateTaskRequest, ControlRequest, ClarificationRequest, FollowupRequest, TaskSnapshot
from .contracts.rules import action_capabilities
from .projection import project, read_core, path_in, document_registry, file_sha256, budget_view, publication


class ApiError(Exception):
    def __init__(self, code: str, message: str, status=409):
        self.code, self.message, self.status = code, message, status
        super().__init__(code)


class Service:
    """Own only newly registered web tasks; existing Agent history is never mutated."""
    def __init__(self, root: Path, *, live_budget='0', launch=True):
        self.root = root.resolve()
        self.db = path_in(self.root, 'storage/web/coordinator.sqlite')
        self.db.parent.mkdir(parents=True, exist_ok=True)
        self.launch = launch
        self.children = {}
        self.dispatch_lock = threading.RLock()
        self.token = uuid.uuid4().hex + uuid.uuid4().hex
        self.live_budget = Decimal(live_budget)
        if not self.live_budget.is_finite() or self.live_budget < 0:
            raise ValueError('预算必须为非负有限数')
        with self.transaction() as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS config(id INTEGER PRIMARY KEY, grant TEXT NOT NULL)')
            conn.execute('CREATE TABLE IF NOT EXISTS request(request_id TEXT PRIMARY KEY,digest TEXT NOT NULL,task_id TEXT UNIQUE NOT NULL,payload TEXT NOT NULL,created_at TEXT NOT NULL,phase TEXT NOT NULL,cancel INTEGER NOT NULL DEFAULT 0,error TEXT,reservation TEXT NOT NULL)')
            conn.execute('CREATE TABLE IF NOT EXISTS operation(id TEXT PRIMARY KEY,digest TEXT NOT NULL,result TEXT)')
            row = conn.execute('SELECT grant FROM config WHERE id=1').fetchone()
            if row and Decimal(row[0]) != self.live_budget:
                raise ValueError('同一服务账本的累计授权不自动修改；必须明确规划新的费用授权')
            conn.execute('INSERT OR IGNORE INTO config VALUES(1,?)', (str(self.live_budget),))

    @contextmanager
    def transaction(self):
        conn = sqlite3.connect(self.db, timeout=15)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute('BEGIN IMMEDIATE')
            yield conn
            conn.commit()
        finally:
            conn.close()

    def record(self, task_id: str) -> dict | None:
        safe_task_id(task_id)
        with self.transaction() as conn:
            row = conn.execute('SELECT * FROM request WHERE task_id=?', (task_id,)).fetchone()
        return {**dict(row), 'request': json.loads(row['payload'])} if row else None

    def active(self, task_id: str) -> bool:
        child = self.children.get(task_id)
        if child is not None and child.poll() is None:
            return True
        lock = path_in(self.root, f'storage/web/worker-locks/{task_id}.lock')
        if not lock.exists():
            return False
        try:
            with task_lock(lock):
                return False
        except RuntimeError:
            return True

    def finished(self, task_id, phase, error=None):
        with self.transaction() as conn:
            conn.execute('UPDATE request SET phase=?,error=? WHERE task_id=?', (phase, error, task_id))

    def allocation(self, row: sqlite3.Row) -> Decimal:
        """Charge a verified terminal task by settled estimate; retain every uncertain cap.

        Reads only application-owned records. The original task reservation and
        core budget are never rewritten. In-flight, failed, cancelled, malformed
        or unknown usage keeps its complete authorization allocation.
        """
        cap = Decimal(row['reservation'])
        if not cap or row['phase'] != 'STOPPED' or row['error'] or row['cancel'] or self.active(row['task_id']):
            return cap
        try:
            core = read_core(self.root, row['task_id'])
            verified, _, _ = publication(self.root, row['task_id'], core)
            if not verified.get('result_available') or any(status != 'DONE' for _, status, _ in core['actions']):
                return cap
            budget = budget_view(self.root, row['task_id'])
            if Decimal(budget['pending_reservation']) != 0:
                return cap
            audits = [e['payload']['audit'] for e in core['events'] if e['payload'].get('event') == 'MODEL_ATTEMPT']
            if not audits or len(audits) != budget['total_calls'] or any(a['status'] != 'PARSED' or
                    any(not isinstance(a['usage'].get(k), int) or a['usage'][k] < 0 for k in
                        ('model_input_tokens', 'model_output_tokens')) for a in audits):
                return cap
            settled = Decimal(budget['settled_estimate'])
            costs = [Decimal(a['estimated_cost_usd']) for a in audits]
            if (not settled.is_finite() or not 0 <= settled <= cap or
                    any(not v.is_finite() or v < 0 for v in costs) or sum(costs, Decimal(0)) != settled):
                return cap
            return settled
        except (ValueError, KeyError, TypeError, OSError, sqlite3.Error, ArithmeticError):
            return cap

    def allocations(self, conn) -> tuple[Decimal, Decimal]:
        """Return current committed cost/caps and original allocations in one coordinator transaction."""
        rows = list(conn.execute('SELECT * FROM request'))
        return (sum((self.allocation(r) for r in rows), Decimal(0)),
                sum((Decimal(r['reservation']) for r in rows), Decimal(0)))

    def dispatch(self, task_id, *, resume=False):
        with self.dispatch_lock:
            if self.active(task_id):
                raise ApiError('TASK_BUSY', '同一任务已有执行器。')
            self.finished(task_id, 'DISPATCHED')
            if not self.launch:
                return
            args = [sys.executable, '-B', str(self.root / 'scripts/web/run_worker.py'), '--task', task_id,
                    '--live-budget', str(self.live_budget)]
            if resume:
                args.append('--resume')
            output = path_in(self.root, f'storage/web/logs/{task_id}.log')
            output.parent.mkdir(parents=True, exist_ok=True)
            try:
                with output.open('ab') as log:
                    self.children[task_id] = subprocess.Popen(args, cwd=self.root, stdout=log, stderr=log,
                        stdin=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            except OSError:
                self.finished(task_id, 'STOPPED', 'WorkerLaunchFailed')
                raise ApiError('DEPENDENCY_NOT_AVAILABLE', '执行器启动失败；身份已保存，请检查后恢复。', 503)

    def create(self, raw: dict) -> dict:
        request = CreateTaskRequest.model_validate(raw)
        value = request.model_dump(mode='json')
        # Fixed research protocol is explicit; question text cannot silently expand data/finance scope.
        if (set(request.company_ids) != {'002371.SZ', '688072.SH', '688082.SH'} or request.fiscal_years != (2023, 2024)
                or request.as_of_date.isoformat() != '2025-04-30' or request.corpus_snapshot_id != 's3-semiconductor-equipment-ar-v1'
                or not any(w in request.question for w in ('收入', '现金流', '应收', '财务', '披露'))
                or re.search(r'买入|卖出|目标价|预测股价|茅台|存货|202[0126789]', request.question)):
            raise ApiError('SCOPE_NOT_SUPPORTED', '仅支持已准备三公司、2023/2024收入质量专题；超范围需先准备资料。', 422)
        if request.execution_mode == 'REPLAY':
            # Server-selected source; clients cannot inject a replay path.
            value['replay_path'] = 'runs/s4/s4-b1-live-20260930-05/final_state.json'
        digest = stable_sha256(value)
        reservation = Decimal('0.10' if request.workflow == 'B1' else '0.05') if request.execution_mode == 'LIVE' else Decimal(0)
        with self.transaction() as conn:
            prior = conn.execute('SELECT digest,task_id FROM request WHERE request_id=?', (request.request_id,)).fetchone()
            if prior:
                if prior['digest'] != digest:
                    raise ApiError('IDEMPOTENCY_CONFLICT', '相同请求 ID 的内容不同，拒绝重复创建。')
                return {'task_id': prior['task_id'], 'reused': True}
            model = json.loads((self.root / 'configs/s1/model.json').read_text(encoding='utf-8'))
            if reservation and not os.environ.get(model['credential_environment_variable']):
                raise ApiError('DEPENDENCY_NOT_AVAILABLE', '服务端缺少模型凭证，不切换演示或回放。', 503)
            used, _ = self.allocations(conn)
            if used + reservation > self.live_budget:
                raise ApiError('BUDGET_LIMIT', '本轮累计授权额度不足，未创建或调用模型。', 422)
            if request.workflow == 'B2':
                parent = project(self.root, request.baseline_task_id)['snapshot']
                if not parent['publication']['result_available'] or parent['task']['workflow'] != 'B1':
                    raise ApiError('PUBLICATION_INCOMPLETE', '所选 B1 没有核验通过的可用发布。')
                if parent['provenance']['source_revalidation'] != 'VERIFIED':
                    raise ApiError('INPUT_LOCK_CHANGED', '父 B1 当前来源或版本不兼容，需要新研究。')
            task_id = 'web-' + request.workflow.lower() + '-' + uuid.uuid4().hex
            conn.execute('INSERT INTO request(request_id,digest,task_id,payload,created_at,phase,reservation) VALUES(?,?,?,?,?,?,?)',
                (request.request_id, digest, task_id, json.dumps(value, ensure_ascii=False), datetime.now(timezone.utc).isoformat(), 'REGISTERED', str(reservation)))
        self.dispatch(task_id)
        return {'task_id': task_id, 'reused': False}

    def snapshot(self, task_id: str) -> dict:
        row = self.record(task_id)
        db = path_in(self.root, f'storage/s6/tasks/{task_id}/session.sqlite')
        if db.is_file():
            try:
                return project(self.root, task_id, row, active=self.active(task_id) if row else False)
            except ValueError as exc:
                if str(exc) != 'TASK_NOT_FOUND' or not row:
                    raise
        if not row:
            raise ApiError('TASK_NOT_FOUND', '任务不存在。', 404)
        from .worker import context_for
        context = context_for(self.root, task_id, row['request']).model_dump(mode='json')
        started = self.active(task_id)
        status = 'CANCELLED' if row['phase'] == 'CANCELLED' else 'FAILED' if row['error'] else 'CREATED'
        data = {'task': {'task_id': task_id, 'question': row['request']['question'], 'context': context,
                'workflow': row['request']['workflow'], 'execution_mode': row['request']['execution_mode'],
                'origin': 'MODEL_REPLAY' if row['request']['execution_mode'] == 'REPLAY' else 'CURRENT_EXECUTION', 'access_mode': 'LIVE_CONTROL',
                'task_status': status, 'created_at': row['created_at'],
                'company_labels': {'002371.SZ': '北方华创', '688072.SH': '拓荆科技', '688082.SH': '盛美上海'},
                'baseline': {'task_id': row['request']['baseline_task_id'], 'label': '复用所选B1'} if row['request']['workflow'] == 'B2' else None},
            'runtime': {'connection_state': 'CONNECTED', 'worker_state': 'ACTIVE' if started else 'STOPPED', 'queue_state': 'DISPATCHED' if started else 'QUEUED',
                'cancel_requested': bool(row['cancel']), 'event_stream_id': f'core-session:{task_id}:schema1',
                'resume_readiness': 'CONFIRMED_SAFE' if not started and not db.exists() and not row['error'] else 'UNSAFE'},
            'publication': {'verification': 'NOT_PUBLISHED'}, 'provenance': {'kind': 'LIVE_PROJECTION', 'label': '请求已持久登记，尚未完成执行准备',
                'notes': ['没有核心终态，不伪造 RUNNING。' + ('执行准备失败：' + row['error'] if row['error'] else '')]},
            'ui': {'panel': 'PROCESS'}}
        snapshot = TaskSnapshot.model_validate(data)
        data['task']['available_actions'] = [a.model_dump(mode='json') for a in action_capabilities(snapshot)]
        return {'snapshot': TaskSnapshot.model_validate(data).model_dump(mode='json'), 'events': []}

    def history(self) -> list:
        defaults = ['s7-live-20261001-01-b1', 's7-live-20261001-01-b2-live', 's7-live-20261001-01-b2-rules']
        with self.transaction() as conn:
            owned = [r[0] for r in conn.execute('SELECT task_id FROM request ORDER BY created_at DESC')]
        rows = []
        for id in [*owned, *defaults]:
            try:
                s = self.snapshot(id)['snapshot']
                rows.append({'task_id': id, 'workflow': s['task']['workflow'], 'status': s['task']['task_status'],
                    'execution_mode': s['task']['execution_mode'], 'report_available': s['publication']['result_available'],
                    'question': s['task']['question'], 'source_revalidation': s['provenance']['source_revalidation']})
            except (ValueError, FileNotFoundError, ApiError, sqlite3.Error):
                rows.append({'task_id': id, 'workflow': None, 'status': 'SOURCE_UNAVAILABLE', 'report_available': False})
        return rows

    def bootstrap(self):
        model = json.loads((self.root / 'configs/s1/model.json').read_text(encoding='utf-8'))
        with self.transaction() as conn:
            reserved, original = self.allocations(conn)
        return {'csrf_token': self.token, 'tasks': self.history(), 'default_task_id': 's7-live-20261001-01-b1',
            'model': model['model'], 'live_enabled': bool(os.environ.get(model['credential_environment_variable'])) and reserved < self.live_budget,
            'model_ready': bool(os.environ.get(model['credential_environment_variable'])),
            'task_caps_usd': {'B1': '0.10', 'B2': '0.05'},
            'authorized_total_usd': str(self.live_budget), 'reserved_authorizations_usd': str(reserved),
            'original_task_reservations_usd': str(original), 'remaining_authorized_usd': str(max(Decimal(0), self.live_budget - reserved)),
            'scope': {'company_ids': ['002371.SZ', '688072.SH', '688082.SH'], 'fiscal_years': [2023, 2024],
                      'as_of_date': '2025-04-30', 'corpus_snapshot_id': 's3-semiconductor-equipment-ar-v1'}}

    def control(self, task_id: str, kind: str, raw: dict) -> dict:
        row = self.record(task_id)
        if not row:
            raise ApiError('REQUEST_INVALID', '已有历史只读，不能控制历史任务。', 403)
        model = {'cancel': ControlRequest, 'resume': ControlRequest, 'clarify': ClarificationRequest, 'followup': FollowupRequest}[kind]
        request = model.model_validate(raw)
        key = f'{task_id}:{kind}:{request.request_id}'
        digest = stable_sha256(request.model_dump(mode='json'))
        # Serialize each user operation, including its durable acknowledgement.
        with task_lock(path_in(self.root, 'storage/web/control.lock')):
            with self.transaction() as conn:
                prior = conn.execute('SELECT digest,result FROM operation WHERE id=?', (key,)).fetchone()
                if prior:
                    if prior['digest'] != digest:
                        raise ApiError('IDEMPOTENCY_CONFLICT', '操作请求 ID 内容冲突。')
                    if prior['result']:
                        return json.loads(prior['result'])
            s = self.snapshot(task_id)['snapshot']
            op = kind.upper() if kind != 'clarify' else 'CLARIFY'
            enabled = any(a['operation'] == op and a['enabled'] for a in s['task']['available_actions'])
            if not enabled:
                raise ApiError('TASK_BUSY', '当前状态不允许此操作；请读取最新任务状态。')
            if kind != 'followup' and request.expected_cursor != s['runtime']['event_cursor']:
                raise ApiError('REQUEST_INVALID', '状态已更新，请核对后再操作。')
            if kind == 'resume':
                # Commit acknowledgement before dispatch; a repeated POST never spawns again.
                answer = {'accepted': True, 'task_id': task_id}
                with self.transaction() as conn:
                    conn.execute('INSERT INTO operation VALUES(?,?,?)', (key, digest, json.dumps(answer)))
                self.dispatch(task_id, resume=path_in(self.root, f'storage/s6/tasks/{task_id}/session.sqlite').is_file())
                return answer
            if kind == 'cancel':
                with self.transaction() as conn:
                    conn.execute('UPDATE request SET cancel=1 WHERE task_id=?', (task_id,))
                db = path_in(self.root, f'storage/s6/tasks/{task_id}/session.sqlite')
                if db.is_file():
                    SessionStore(db).cancel()
                    if not self.active(task_id):
                        with task_lock(db.parent / 'executor.lock'):
                            store = SessionStore(db)
                            store.set_status('CANCELLED')
                            store.event({'event': 'CANCELLED', 'reason': 'STOPPED_WORKER_CANCEL_BOUNDARY'})
                elif not self.active(task_id):
                    self.finished(task_id, 'CANCELLED')
                answer = {'accepted': True, 'cancel_requested': True, 'confirmed': False}
            elif kind == 'clarify':
                if request.checkpoint_sha256 != s['runtime']['checkpoint_sha256'] or request.gap_id not in s['runtime']['waiting_gap_ids']:
                    raise ApiError('INVALID_CLARIFICATION', '决定对应的缺口或检查点已变化。')
                from .worker import construct
                executor = construct(self.root, task_id, row['request'], resume=True, require_model=False)
                answer = executor.clarify(request.request_id, request.gap_id, request.decision)
            else:
                runtime = path_in(self.root, f'storage/s6/tasks/{task_id}/session.sqlite')
                store = SessionStore(runtime)
                capsule = json.loads(path_in(self.root, f'runs/s6/{task_id}/context_capsule.json').read_text(encoding='utf-8'))
                ctx = ResearchContext.model_validate(s['task']['context'])
                function = supplement_followup if s['task']['workflow'] == 'B2' else answer_followup
                answer = function(capsule, ctx, request.question, request.topic)
                store.save_turn(request.request_id, request.question, ctx, answer)
                answer = {**answer, 'message': '复用已审核记录，未新增 AI 调用或改变研究终态。'}
            with self.transaction() as conn:
                conn.execute('INSERT INTO operation VALUES(?,?,?)', (key, digest, json.dumps(answer, ensure_ascii=False)))
            return answer

    def pdf(self, task_id: str, document_id: str) -> Path:
        s = self.snapshot(task_id)['snapshot']
        if document_id not in {d['document_id'] for d in s['research']['documents']}:
            raise ApiError('REQUEST_INVALID', '文档不属于此任务资料范围。', 403)
        core = read_core(self.root, task_id)
        ready = f"storage/s3/corpora/{s['task']['context']['corpus_snapshot_id']}/ready.json"
        if core['manifest']['input_lock'].get(ready) != file_sha256(path_in(self.root, ready)):
            raise ApiError('SOURCE_INTEGRITY_FAILED', '原任务绑定的资料目录已变化，拒绝打开。')
        entry = document_registry(self.root, s['task']['context'])[document_id]['manifest']
        path = path_in(self.root, entry['local_path'])
        if path.suffix.lower() != '.pdf' or file_sha256(path) != entry['sha256']:
            raise ApiError('SOURCE_INTEGRITY_FAILED', 'PDF 摘要不匹配，拒绝打开。')
        return path
