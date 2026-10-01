"""Loopback FinResearch service and frontend; model secrets stay in the worker environment."""
from __future__ import annotations

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
from pathlib import Path
import re
import sqlite3
import sys
from urllib.parse import urlparse, parse_qs, unquote

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from pydantic import ValidationError
from finresearch.storage.session_store import task_lock
from finresearch_web.service import Service, ApiError


class AppHandler(SimpleHTTPRequestHandler):
    extensions_map = {**SimpleHTTPRequestHandler.extensions_map, '.mjs': 'text/javascript'}

    def __init__(self, *args, service, **kwargs):
        self.service = service
        super().__init__(*args, directory=str(ROOT / 'web'), **kwargs)

    def origin_allowed(self, write=False):
        port = self.server.server_port
        hosts = {f'127.0.0.1:{port}', f'localhost:{port}'}
        origin = self.headers.get('Origin')
        return self.headers.get('Host') in hosts and (origin is None or origin in {f'http://{host}' for host in hosts}) and (
            not write or self.headers.get('X-FinResearch-Token') == self.service.token)

    def translate_path(self, path):
        resolved = Path(super().translate_path(path)).resolve()
        return str(resolved) if resolved.is_relative_to((ROOT / 'web').resolve()) else str(ROOT / 'web/__not_found__')

    def end_headers(self):
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'")
        super().end_headers()

    def json_response(self, status, payload):
        data = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def failure(self, exc):
        if isinstance(exc, ApiError):
            code, status, message = exc.code, exc.status, exc.message
        elif isinstance(exc, ValidationError):
            code, status, message = 'REQUEST_INVALID', 422, '请求字段、版本或类型不受支持。'
        elif isinstance(exc, FileNotFoundError):
            code, status, message = 'TASK_NOT_FOUND', 404, '来源任务或必要文件不存在。'
        elif isinstance(exc, sqlite3.Error):
            code, status, message = 'DEPENDENCY_NOT_AVAILABLE', 503, '数据读取暂不可用，请重新读取状态。'
        else:
            allowed = {'SOURCE_INTEGRITY_FAILED', 'UNSUPPORTED_CONTRACT_VERSION', 'TASK_NOT_FOUND', 'DEPENDENCY_NOT_AVAILABLE'}
            code = str(exc) if str(exc) in allowed else 'SOURCE_INTEGRITY_FAILED'
            status, message = 409, '任务或来源未通过读取核验；未继续执行模型。'
        logging.warning('API rejected: %s (%s)', code, type(exc).__name__)
        self.json_response(status, {'code': code, 'message': message})

    def do_GET(self):
        if not self.origin_allowed():
            return self.json_response(403, {'code': 'REQUEST_INVALID', 'message': '仅允许本机同源访问。'})
        path = unquote(urlparse(self.path).path)
        if not path.startswith('/api/'):
            return super().do_GET()
        try:
            if path == '/api/bootstrap':
                return self.json_response(200, self.service.bootstrap())
            if path == '/api/tasks':
                return self.json_response(200, {'tasks': self.service.history()})
            match = re.fullmatch(r'/api/tasks/([A-Za-z0-9_-]+)/?(.*)', path)
            if not match:
                raise ApiError('TASK_NOT_FOUND', '接口不存在。', 404)
            task, endpoint = match.groups()
            if endpoint.startswith('documents/'):
                document = endpoint[len('documents/'):]
                if '/' in document:
                    raise ApiError('REQUEST_INVALID', '文档 ID 不合法。', 422)
                pdf = self.service.pdf(task, document)
                data = pdf.read_bytes()
                self.send_response(200)
                self.send_header('Content-Type', 'application/pdf')
                self.send_header('Content-Length', str(len(data)))
                self.send_header('Content-Disposition', 'inline; filename="annual-report.pdf"')
                self.end_headers()
                return self.wfile.write(data)
            value = self.service.snapshot(task)
            if endpoint == 'events':
                params = parse_qs(urlparse(self.path).query)
                cursor = int(params.get('after', ['0'])[0])
                if cursor < 0 or cursor > value['snapshot']['runtime']['event_cursor']:
                    raise ApiError('REQUEST_INVALID', '游标不属于当前任务事件流。', 422)
                events = [e for e in value['events'] if e['seq'] > cursor][:200]
                return self.json_response(200, {'events': events, 'cursor': events[-1]['seq'] if events else cursor,
                    'head_cursor': value['snapshot']['runtime']['event_cursor'], 'event_stream_id': value['snapshot']['runtime']['event_stream_id']})
            if endpoint not in {'', 'snapshot'}:
                raise ApiError('OUTPUT_UNAVAILABLE', '该独立输出尚未提供。', 404)
            return self.json_response(200, value)
        except Exception as exc:
            self.failure(exc)

    def do_POST(self):
        if not self.origin_allowed(write=True):
            return self.json_response(403, {'code': 'REQUEST_INVALID', 'message': '写操作要求本机同源与有效会话 token。'})
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 20000 or self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                raise ApiError('REQUEST_INVALID', '请求必须为有限长度 JSON。', 422)
            raw = json.loads(self.rfile.read(length))
            path = urlparse(self.path).path
            if path == '/api/tasks':
                return self.json_response(202, self.service.create(raw))
            match = re.fullmatch(r'/api/tasks/([A-Za-z0-9_-]+)/(cancel|resume|clarify|followup)', path)
            if not match:
                raise ApiError('TASK_NOT_FOUND', '接口不存在。', 404)
            task, operation = match.groups()
            return self.json_response(200, self.service.control(task, operation, raw))
        except Exception as exc:
            self.failure(exc)

    def log_message(self, format, *args):
        logging.info(format, *args)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8766)
    parser.add_argument('--live-budget', default='0', help='Persistent cumulative grant; zero disables paid creation')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    # One coordinator per ledger, even when another process tries a different port.
    with task_lock(ROOT / 'storage/web/server.lock'):
        service = Service(ROOT, live_budget=args.live_budget)
        server = ThreadingHTTPServer(('127.0.0.1', args.port), partial(AppHandler, service=service))
        logging.info('FinResearch service: http://127.0.0.1:%s', server.server_port)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()


if __name__ == '__main__':
    main()
