"""Loopback-only UI/API. Credentials stay in the controller environment."""
from __future__ import annotations

import asyncio
import json
import mimetypes
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from . import __version__
from .assets import draft_suites
from .case_review import build_review, optional
from .runner import launch, stop
from .store import Store
from .transport import probe
from .util import BenchError, atomic_json, canonical, digest, file_sha, inside, read_json, slug


def web_root() -> Path:
    bundled = Path(__file__).parent / 'web'
    return bundled if bundled.is_dir() else Path(__file__).resolve().parents[2] / 'ui/runtime'


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, host: str, port: int, store: Store):
        if host not in {'127.0.0.1', 'localhost'}:
            raise BenchError('この版の画面サーバーはlocalhostに限定しています。')
        self.store = store
        self.csrf = secrets.token_urlsafe(32)
        super().__init__((host, port), Handler)
        number = self.server_address[1]
        self.hosts = {'127.0.0.1:' + str(number), 'localhost:' + str(number)}
        self.origins = {'http://' + host for host in self.hosts}


class Handler(BaseHTTPRequestHandler):
    server: LocalServer

    def log_message(self, format, *args):
        pass  # No headers, query strings, or credential values in HTTP logs.

    def send(self, code: int, value, content_type='application/json; charset=utf-8'):
        data = canonical(value) if content_type.startswith('application/json') else value
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def allowed_host(self):
        return self.headers.get('Host', '').lower() in self.server.hosts

    def do_GET(self):
        if not self.allowed_host():
            return self.send(403, {'error': '許可されたローカルのアドレスから接続してください。'})
        path = urlsplit(self.path).path
        query = parse_qs(urlsplit(self.path).query)
        store = self.server.store
        try:
            if path == '/api/bootstrap':
                registry = store.registry.read()
                suites = store.available_suites()
                drafts = [draft for draft in draft_suites() if not any(
                    row.get('execution_ready') and row.get('track') == 'benchmark'
                    and row.get('case_count') == draft['case_count']
                    and row['id'].startswith(draft['id'].split('.')[0]) for row in suites)]
                models = []
                for model in registry['models']:
                    value = store.registry.public_model(model)
                    cached = store.root / 'connections' / (digest(model) + '.json')
                    value['connection'] = read_json(cached) if cached.exists() else {'available': None}
                    models.append(value)
                return self.send(200, {'version': __version__, 'csrf_token': self.server.csrf, 'models': models,
                                       'presets': registry['presets'], 'run_defaults': registry['run_defaults'],
                                       'suites': suites + drafts, 'runs': store.runs(), 'demo': False})
            if path == '/api/runs':
                return self.send(200, {'runs': store.runs()})
            pieces = path.strip('/').split('/')
            if len(pieces) >= 3 and pieces[:2] == ['api', 'runs']:
                identity = slug(pieces[2])
                root = store.run_path(identity)
                if len(pieces) == 3:
                    return self.send(200, store.status(identity))
                if pieces[3] == 'plan':
                    return self.send(200, store.public_plan(read_json(root / 'plan.json')))
                if pieces[3] == 'results':
                    result = read_json(root / 'summary.json') if (root / 'summary.json').exists() else {'rows': [], 'combinations': []}
                    from .saved_reviews import load, versions
                    result['evaluation_versions'] = versions(store, identity)
                    revision = query.get('evaluation_revision', ['native'])[0]
                    if revision != 'native':
                        review = load(store, identity, revision)
                        overrides = {(r['case_id'], r['combination_id'], r['trial_index']): r for r in review['rows']}
                        for row in result['rows']:
                            checked = overrides.get((row['case_id'], row['combination_id'], row['trial_index']))
                            if checked:
                                row['grade'] = checked['grade']
                        result['evaluation_notice'] = review['historical_generation_notice']
                    result['evaluation_revision'] = revision
                    return self.send(200, {**result, 'status': store.status(identity)})
                if pieces[3] == 'case':
                    plan, pack = read_json(root / 'plan.json'), read_json(root / 'pack.json')
                    combo_id = query.get('combination', [''])[0]
                    case_id = query.get('case_id', [''])[0]
                    index = int(query.get('trial', ['0'])[0])
                    if not any(row['id'] == combo_id for row in plan['combinations']) or not 0 <= index < plan['run_settings']['trials_per_case']:
                        raise BenchError('試行の指定が不正です。')
                    case = next((row for row in pack['cases'] if row['id'] == case_id), None)
                    if case is None:
                        raise BenchError('ケースが見つかりません。')
                    folder = root / 'trials' / combo_id / digest(case_id)[:20] / str(index)
                    complete = store.verify_completion(folder)
                    if not complete:
                        return self.send(200, {'case': {key: case[key] for key in ('id', 'title', 'category')}, 'state': 'pending'})
                    answer_path = inside(store.root, complete['answer_ref'])
                    attempt = answer_path.parent
                    grades = []
                    grade_calls = []
                    for marker_path in sorted(folder.glob('grades/*/complete.json')):
                        marker = read_json(marker_path)
                        result_path = inside(store.root, marker['result_ref'])
                        if file_sha(result_path) != marker['result_sha256']:
                            raise BenchError('保存済み採点のSHAが異なります。')
                        base_attempt = result_path.parent.parent if result_path.parent.name == 'evaluation-review' else result_path.parent
                        grades.append({'fingerprint': marker['grader_fingerprint'], 'result': read_json(result_path),
                                       'decisions': optional(base_attempt / 'decisions.json')})
                        grade_calls.extend(sorted((base_attempt / 'calls').glob('*')))
                        grade_calls.extend(sorted((base_attempt / 'evaluation-review/calls').glob('*')))
                    answer = answer_path.read_text(encoding='utf-8')
                    trial = read_json(inside(store.root, complete['trial_ref']))
                    revision = query.get('evaluation_revision', ['native'])[0]
                    if revision != 'native':
                        from .saved_reviews import load
                        record = load(store, identity, revision)
                        checked = next((r for r in record['rows'] if (r['case_id'], r['combination_id'], r['trial_index']) == (case_id, combo_id, index)), None)
                        if checked:
                            grade_calls.extend(sorted((inside(store.root, checked['result_ref']).parent / 'calls').glob('*')))
                            grades = [{'fingerprint': digest([revision, checked['source_result_sha256']]), 'result': checked['grade'],
                                       'decisions': grades[-1]['decisions'] if grades else {}}]
                    call_paths = sorted((attempt / 'calls').glob('*')) + grade_calls
                    if query.get('view') == ['trace']:
                        kind = query.get('kind', [''])[0]
                        if kind == 'answer':
                            return self.send(200, {'answer': answer})
                        if kind == 'grade':
                            return self.send(200, {'grades': grades})
                        call_index = int(query.get('call', ['0'])[0])
                        if kind not in {'reasoning', 'requests'} or not 0 <= call_index < len(call_paths):
                            raise BenchError('詳細情報の指定が不正です。')
                        item = call_paths[call_index]
                        if kind == 'reasoning':
                            return self.send(200, {'reasoning': optional(item / 'message.json').get('reasoning_content', '')})
                        return self.send(200, {'request': optional(item / 'request.json'), 'message': optional(item / 'message.json'),
                                               'measurement': optional(item / 'measurement.json')})
                    if query.get('view') == ['review']:
                        review = build_review(case, attempt, answer, grades, trial)
                        review['calls'] = [{'index': number, 'role': optional(item / 'measurement.json').get('role', '不明')}
                                           for number, item in enumerate(call_paths)]
                        return self.send(200, {'case': {key: case[key] for key in ('id', 'title', 'category')},
                                               'review': review,
                                               'reused_from': complete.get('reused_from'), 'state': 'generated'})
                    calls = []
                    for item in sorted((attempt / 'calls').iterdir()):
                        calls.append({'request': read_json(item / 'request.json'), 'message': read_json(item / 'message.json'),
                                      'measurement': read_json(item / 'measurement.json')})
                    return self.send(200, {'case': {key: case[key] for key in ('id', 'title', 'category')},
                                           'answer': answer, 'calls': calls,
                                           'grades': grades, 'trial': trial,
                                           'reused_from': complete.get('reused_from'), 'state': 'generated'})
            static = {'/': 'index.html', '/index.html': 'index.html', '/app.js': 'app.js', '/review.js': 'review.js', '/styles.css': 'styles.css', '/favicon.svg': 'favicon.svg'}
            if path in static:
                file = web_root() / static[path]
                mime = mimetypes.guess_type(file.name)[0] or 'application/octet-stream'
                return self.send(200, file.read_bytes(), mime + ('; charset=utf-8' if mime.startswith('text/') or mime in {'application/javascript', 'image/svg+xml'} else ''))
            self.send(404, {'error': '見つかりません。'})
        except FileNotFoundError:
            self.send(404, {'error': '保存データが見つかりません。'})
        except (BenchError, ValueError) as error:
            self.send(400, {'error': str(error) if isinstance(error, BenchError) else '指定の形式を確認してください。'})
        except Exception:
            self.send(500, {'error': '画面サーバーの処理に失敗しました。'})

    def do_POST(self):
        if not self.allowed_host() or self.headers.get('Origin') not in self.server.origins | {None}:
            return self.send(403, {'error': '同じ画面から操作してください。'})
        if not secrets.compare_digest(self.headers.get('X-Beacon-Token', ''), self.server.csrf):
            return self.send(403, {'error': '画面を再読み込みしてから操作してください。'})
        if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
            return self.send(415, {'error': 'JSON形式で指定してください。'})
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 1048576:
                return self.send(413, {'error': '設定のサイズを確認してください。'})
            value = json.loads(self.rfile.read(length))
            canonical(value)
            path = urlsplit(self.path).path
            store = self.server.store
            if path == '/api/models':
                model = store.registry.save('models', value)
                return self.send(200, store.registry.public_model(model))
            if path == '/api/presets':
                return self.send(200, store.registry.save('presets', value))
            if path == '/api/plans':
                return self.send(200, store.public_plan(store.plan(value)))
            if path == '/api/runs':
                return self.send(202, {'run_id': launch(store, plan_id=value['plan_id'])})
            pieces = path.strip('/').split('/')
            if len(pieces) == 4 and pieces[:2] == ['api', 'models'] and pieces[3] == 'probe':
                model = store.registry.get('models', slug(pieces[2]))
                evidence = asyncio.run(probe(model))
                atomic_json(store.root / 'connections' / (digest(model) + '.json'), evidence)
                return self.send(200, evidence)
            if len(pieces) == 4 and pieces[:2] == ['api', 'runs']:
                identity = slug(pieces[2])
                if pieces[3] == 'stop':
                    return self.send(202, stop(store, identity))
                if pieces[3] == 'resume':
                    return self.send(202, {'run_id': launch(store, resume_id=identity)})
            return self.send(404, {'error': '操作が見つかりません。'})
        except FileNotFoundError:
            self.send(404, {'error': '保存データが見つかりません。'})
        except (BenchError, ValueError, KeyError, TypeError) as error:
            self.send(400, {'error': str(error) if isinstance(error, BenchError) else '設定の形式を確認してください。'})
        except Exception:
            self.send(500, {'error': '操作に失敗しました。'})


def serve(store: Store, host='127.0.0.1', port=18741):
    server = LocalServer(host, port, store)
    print('BeaconBench: http://' + host + ':' + str(server.server_address[1]) + '/', flush=True)
    try:
        server.serve_forever(poll_interval=.2)
    finally:
        server.server_close()
