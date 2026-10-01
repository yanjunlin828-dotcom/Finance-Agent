"""Actual loopback HTTP tests; dispatch and provider calls disabled."""
from functools import partial
from http.server import ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import shutil
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from finresearch_web.service import Service

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('web_app_http', ROOT / 'scripts/web/serve_app.py')
http_app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(http_app)


@pytest.fixture
def server(tmp_path, monkeypatch):
    for name in ['configs/s1/model.json', 'protocols/revenue_quality_v1.json']:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, target)
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'test-not-a-real-key')
    service = Service(tmp_path, live_budget='0.30', launch=False)
    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(http_app.AppHandler, service=service))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{server.server_port}', service
    server.shutdown()
    server.server_close()
    thread.join()


def send(base, path, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    request = Request(base + path, data=data, headers={'Content-Type': 'application/json', **(headers or {})})
    try:
        response = urlopen(request, timeout=10)
    except HTTPError as exc:
        response = exc
    return response.code, json.loads(response.read())


def payload():
    return {'request_id': 'http-create', 'question': '比较收入与现金流和应收账款。', 'workflow': 'B1',
        'execution_mode': 'LIVE', 'company_ids': ['002371.SZ', '688072.SH', '688082.SH'],
        'fiscal_years': [2023, 2024], 'as_of_date': '2025-04-30', 'corpus_snapshot_id': 's3-semiconductor-equipment-ar-v1'}


def test_write_requires_token_and_rejects_external_origin(server):
    base, service = server
    assert send(base, '/api/tasks', payload())[0] == 403
    assert send(base, '/api/tasks', payload(), {'X-FinResearch-Token': service.token, 'Origin': 'https://external.example'})[0] == 403
    with service.transaction() as conn:
        assert conn.execute('SELECT COUNT(*) FROM request').fetchone()[0] == 0


def test_dns_rebinding_host_rejected(server):
    base, _ = server
    assert send(base, '/api/bootstrap', headers={'Host': 'external.example'})[0] == 403


def test_ack_is_persisted_and_snapshot_does_not_fake_running(server):
    base, service = server
    _, bootstrap = send(base, '/api/bootstrap')
    code, created = send(base, '/api/tasks', payload(), {'X-FinResearch-Token': bootstrap['csrf_token']})
    assert code == 202
    _, s = send(base, '/api/tasks/' + created['task_id'])
    assert s['snapshot']['task']['task_status'] == 'CREATED'
    assert not s['snapshot']['publication']['result_available']
    _, again = send(base, '/api/tasks', payload(), {'X-FinResearch-Token': bootstrap['csrf_token']})
    assert again['task_id'] == created['task_id'] and again['reused']


def test_future_or_foreign_cursor_is_rejected(server):
    base, service = server
    _, created = send(base, '/api/tasks', payload(), {'X-FinResearch-Token': service.token})
    assert send(base, '/api/tasks/' + created['task_id'] + '/events?after=999')[0] == 422
    _, value = send(base, '/api/tasks/' + created['task_id'] + '/events?after=0')
    assert value['cursor'] == 0 and value['events'] == []


def test_arbitrary_pdf_path_and_unknown_task_are_refused(server):
    base, _ = server
    assert send(base, '/api/tasks/no-such-task')[0] == 404
    assert send(base, '/api/tasks/no-such-task/documents/../../private.pdf')[0] == 422
