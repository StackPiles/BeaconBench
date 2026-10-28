"""Run a pinned native CLI behind loopback model endpoints and stdio RPC.

This runs in the native environment (possibly reached by SSH), never on a
relay host as a substitute for a grading sandbox. Native keys are ephemeral proxy
tokens. Real target/helper API credentials stay in the controller process.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
import signal
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from string import Template

from .adapters.native import WIRE, locked_files, require_locked
from .util import BenchError, atomic_json, canonical, file_sha, inside, read_json, utc, write_once


class RPC:
    def __init__(self, incoming, outgoing):
        self.incoming, self.outgoing = incoming, outgoing
        self.writer = threading.Lock()
        self.pending = {}
        self.lock = threading.Lock()
        self.closed = threading.Event()
        self.failure = None
        threading.Thread(target=self.read, daemon=True).start()

    def send(self, value):
        with self.writer:
            self.outgoing.write(canonical(value).decode() + '\n')
            self.outgoing.flush()

    def read(self):
        try:
            for line in self.incoming:
                value = json.loads(line)
                if value.get('type') != 'model_response':
                    raise BenchError('Unexpected controller response')
                with self.lock:
                    waiting = self.pending.get(value.get('id'))
                    if waiting is None or waiting['event'].is_set():
                        raise BenchError('Unknown or repeated response ID')
                    waiting['response'] = value['response']
                    waiting['event'].set()
        except Exception as error:
            self.failure = type(error).__name__
        finally:
            self.closed.set()

    def model(self, role, body):
        identity = uuid.uuid4().hex
        waiting = {'event': threading.Event()}
        with self.lock:
            self.pending[identity] = waiting
        try:
            self.send({'type': 'model_request', 'id': identity, 'role': role, 'body': body})
            while not waiting['event'].wait(.2):
                if self.closed.is_set():
                    raise BenchError('Controller disconnected')
            return waiting['response']
        finally:
            with self.lock:
                self.pending.pop(identity, None)


class ModelHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, code, value):
        payload = canonical(value)
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def role(self):
        pieces = self.path.strip('/').split('/')
        if len(pieces) < 3 or pieces[0] not in self.server.roles or pieces[1] != 'v1':
            raise BenchError('Unknown model proxy route')
        return pieces[0]

    def do_GET(self):
        try:
            role = self.role()
            if not self.path.endswith('/models'):
                raise BenchError('Unknown model route')
            self.reply(200, {'object': 'list', 'data': [{'id': self.server.aliases.get(role, role), 'object': 'model'}]})
        except BenchError:
            self.reply(400, {'error': {'message': 'Unknown proxy route'}})

    def do_POST(self):
        acquired = False
        try:
            role = self.role()
            if self.path != f'/{role}/v1/chat/completions' or self.headers.get('Authorization') != 'Bearer ' + self.server.token:
                raise BenchError('Unknown or unauthorized proxy route')
            acquired = self.server.single_flight.acquire(blocking=False)
            if not acquired:
                self.server.rpc.failure = 'overlapping-native-model-requests'
                raise BenchError('Native retries/concurrency must be disabled for a one-case trial')
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            if body.get('stream'):
                raise BenchError('The native SDK receives buffered JSON; the origin transport records SSE')
            value = self.server.rpc.model(role, body)
            self.reply(200, value)
        except Exception:
            self.server.rpc.failure = self.server.rpc.failure or 'native-model-proxy-error'
            # Fatal 400 avoids a native SDK silently retrying an accepted call.
            try:
                self.reply(400, {'error': {'message': 'Measured model bridge failed; inspect retained evidence'}})
            except OSError:
                pass
        finally:
            if acquired:
                self.server.single_flight.release()


def extract_record(value, spec):
    if spec.get('filter'):
        if not isinstance(value, list):
            raise BenchError('Native event selection requires a list')
        value = [row for row in value if all(row.get(key) == item for key, item in spec['filter'].items())]
    for key in spec.get('pointer', []):
        value = value[key]
    if spec.get('id_field'):
        candidates = value if isinstance(value, list) else [value]
        matches = [row for row in candidates if str(row.get(spec['id_field'])) == str(spec['expected_id'])]
        if len(matches) != 1:
            raise BenchError('Native output has missing/duplicate case results')
        value = matches[0]
    for key in spec.get('after_select', []):
        value = value[key]
    if not isinstance(value, dict):
        raise BenchError('Native grading record is not an object')
    return value


def result_file(root, spec):
    paths = sorted(root.glob(spec['glob'])) if spec.get('glob') else [inside(root, spec['path'])]
    if len(paths) != 1:
        raise BenchError('Expected exactly one native result file; do not select the newest or best result')
    path = inside(root, paths[0])
    value = ([json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
             if spec.get('format') == 'jsonl' else read_json(path))
    return path, value


def render(value, substitutions):
    """Substitute JSON values without losing numeric capacity types."""
    if isinstance(value, dict): return {key: render(item, substitutions) for key, item in value.items()}
    if isinstance(value, list): return [render(item, substitutions) for item in value]
    if isinstance(value, str):
        if value.startswith('${') and value.endswith('}') and value[2:-1] in substitutions:
            return substitutions[value[2:-1]]
        return Template(value).substitute(substitutions)
    return value


def transfer_artifacts(rpc, root, paths):
    manifest = {}
    for index, path in enumerate(sorted(set(paths))):
        path = inside(root, path)
        relative = path.relative_to(root).as_posix(); key = 'artifact-' + str(index)
        entry = {'path': relative, 'sha256': file_sha(path), 'bytes': path.stat().st_size}
        rpc.send({'type': 'artifact_start', 'id': key, **entry})
        offset = 0
        with path.open('rb') as source:
            while chunk := source.read(192 * 1024):
                rpc.send({'type': 'artifact_chunk', 'id': key, 'offset': offset, 'data': base64.b64encode(chunk).decode('ascii')})
                offset += len(chunk)
        rpc.send({'type': 'artifact_end', 'id': key})
        manifest[key] = entry
    return manifest


def run(config, initial):
    locked_files(config)
    require_locked(config, [item['source'] for item in config.get('templates', [])])
    if initial.get('protocol') != WIRE or initial.get('type') != 'start':
        raise BenchError('Unsupported native wire protocol')
    if config.get('platform') == 'linux' and sys.platform != 'linux':
        raise BenchError('This native family requires its dedicated Linux environment')
    roles = set(initial['roles'])
    if not set(config.get('required_roles', [])) <= roles:
        raise BenchError('Required measuring roles are missing')
    case = initial['payload']
    root = Path(config['work_root']).resolve() / ('trial-' + uuid.uuid4().hex)
    root.mkdir(parents=True, exist_ok=False)
    write_once(root / 'input.json', initial)
    rpc = RPC(sys.stdin, sys.stdout)
    server = ThreadingHTTPServer(('127.0.0.1', 0), ModelHandler)
    server.daemon_threads = True
    server.rpc, server.roles, server.token = rpc, roles, secrets.token_urlsafe(32)
    server.single_flight = threading.Lock()
    server.aliases = config.get('aliases', {})
    threading.Thread(target=server.serve_forever, daemon=True).start()
    generation = case['generation']
    substitutions = {**config.get('variables', {}), 'WORK_ROOT': str(root), 'CASE_ID': case['case_id'], 'NATIVE_ID': generation['native_id'],
                     'DOMAIN': generation.get('domain', ''), 'SEED': str(initial['seed']), 'LANGUAGE': 'ja',
                     'PROXY_KEY': server.token, 'SOURCE_ROOT': str(Path(config['source_root']).resolve())}
    for role in roles:
        substitutions[role.upper() + '_BASE_URL'] = f'http://127.0.0.1:{server.server_port}/{role}/v1'
        substitutions[role.upper() + '_MODEL'] = server.aliases.get(role, role)
        for key, value in initial.get('model_capacities', {}).get(role, {}).items():
            substitutions[role.upper() + '_' + key.upper()] = value
    for destination, value in config.get('inline_files', {}).items():
        path = inside(root, destination)
        write_once(path, render(value, substitutions)); path.chmod(0o600)
    for item in config.get('templates', []):
        destination = inside(root, item['destination'])
        destination.parent.mkdir(parents=True, exist_ok=True)
        text = Path(item['source']).read_text(encoding='utf-8')
        destination.write_text(Template(text).substitute(substitutions), encoding='utf-8')
        destination.chmod(0o600)
    argv = [Template(part).substitute(substitutions) for part in config['native_command']]
    environment = dict(os.environ)
    environment.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', TOKENIZERS_PARALLELISM='false')
    environment.update(render(config.get('environment', {}), substitutions))
    write_once(root / 'invocation.json', {'utc': utc(), 'argv': ['<ephemeral-proxy-token>' if part == server.token else part for part in argv],
                                         'config_fingerprint': {asset['path']: asset['sha256'] for asset in config['lock_files']}})
    process = None
    started = time.monotonic()
    def interrupted(signum, frame):
        rpc.closed.set()
        raise InterruptedError('Native trial interrupted')
    previous = {sig: signal.signal(sig, interrupted) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {'start_new_session': True}
        with (root / 'native.stdout.txt').open('wb') as output, (root / 'native.stderr.txt').open('wb') as errors:
            process = subprocess.Popen(argv, cwd=Template(config.get('native_cwd', '${SOURCE_ROOT}')).substitute(substitutions),
                                       env=environment, stdin=subprocess.DEVNULL, stdout=output, stderr=errors, **options)
            while process.poll() is None:
                if rpc.closed.is_set() or rpc.failure:
                    raise BenchError('Controller disconnected or model-call coverage failed')
                time.sleep(.1)
        if process.returncode or rpc.failure:
            raise BenchError('Native command failed; raw logs retained')
        result_spec = config['result']
        result_path, value = result_file(root, render(result_spec, substitutions))
        native = extract_record(value, {**result_spec, 'expected_id': generation['native_id']})
        artifact_paths = [result_path, root / 'native.stdout.txt', root / 'native.stderr.txt']
        for pattern in config.get('artifact_globs', []): artifact_paths.extend(path for path in root.glob(pattern) if path.is_file())
        manifest = transfer_artifacts(rpc, root, artifact_paths)
        # A native pipeline can include conversion/judging. Its total duration
        # is not silently presented as target task-completion time.
        result = {'protocol': WIRE, 'type': 'result', 'case_id': case['case_id'], 'answer': json.dumps(native, ensure_ascii=False),
                  'native_id': generation['native_id'], 'family': generation['family'], 'grade_record': native,
                  'task_wall_seconds': None, 'native_pipeline_seconds': time.monotonic() - started,
                  'termination': 'native_process_completed', 'coverage': {'all_model_calls_via_rpc': True,
                     'task_completion_boundary': 'unavailable unless the source wrapper provides a boundary event'},
                  'checkpoint': {'path': str(root), 'result_sha256': file_sha(result_path)}}
        result['artifact_manifest'] = manifest
        write_once(root / 'result.json', result)
        rpc.send(result)
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait()
        server.shutdown(); server.server_close()
        write_once(root / 'execution.json', {'exit_code': process.returncode if process else None,
                                             'pipeline_seconds': time.monotonic() - started, 'rpc_failure': rpc.failure})
        for sig, previous_handler in previous.items():
            signal.signal(sig, previous_handler)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, required=True)
    args = parser.parse_args()
    try:
        run(read_json(args.config), json.loads(sys.stdin.readline()))
    except Exception as error:
        print(type(error).__name__ + ': ' + str(error), file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
