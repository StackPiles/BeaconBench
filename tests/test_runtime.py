"""End-to-end fixtures for the real runtime; no external model requests."""
import asyncio
import copy
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx

from beaconbench.assets import install_diagnostic, install_pack, load_pack
from beaconbench.config import DEFAULT_PRESET, DEFAULT_RUN_SETTINGS, validate_model, validate_settings
from beaconbench.runner import Worker, launch, stop
from beaconbench.qualification import qualify_executor
from beaconbench.server import LocalServer
from beaconbench.store import Store
from beaconbench.transport import TransportFailure, chat
from beaconbench.util import BenchError, atomic_json, process_alive, read_json, trial_seed, write_once


class FixtureHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        value = json.dumps({'data': [{'id': 'fixture-model'}]}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(value)))
        self.end_headers()
        self.wfile.write(value)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.server.requests.append(body)
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.end_headers()
        frames = [{'choices': [{'index': 0, 'delta': {'role': 'assistant'}}]},
                  {'choices': [{'index': 0, 'delta': {'reasoning_content': '考えます。'}}]},
                  {'choices': [{'index': 0, 'delta': {'content': '42'}}]}]
        if self.server.mode == 'complete':
            frames.extend([{'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]},
                           {'choices': [], 'usage': {'prompt_tokens': 10, 'completion_tokens': 4, 'total_tokens': 14,
                                                    'prompt_tokens_details': {'cached_tokens': 2},
                                                    'completion_tokens_details': {'reasoning_tokens': 2}}}])
        raw = ''.join('data: ' + json.dumps(row, ensure_ascii=False) + '\r\n\r\n' for row in frames).encode('utf-8')
        if self.server.mode == 'complete':
            raw += b'data: [DONE]\n\n'
        try:
            for index in range(0, len(raw), 17):
                self.wfile.write(raw[index:index + 17])
                self.wfile.flush()
                time.sleep(.001)
            if self.server.mode == 'hold':
                for _ in range(50):
                    time.sleep(.1)
                    self.wfile.write(b': heartbeat\n\n')
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass


class Runtime(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.endpoint = ThreadingHTTPServer(('127.0.0.1', 0), FixtureHandler)
        self.endpoint.daemon_threads = True
        self.endpoint.mode = 'complete'
        self.endpoint.requests = []
        self.thread = threading.Thread(target=self.endpoint.serve_forever, daemon=True)
        self.thread.start()
        os.environ['BEACON_TEST_API_KEY'] = 'fixture-private-credential'
        self.store = Store(self.root / 'data')
        self.model = {'id': 'fixture', 'name': 'Fixture model', 'api_model': 'fixture-model',
                      'base_url': 'http://127.0.0.1:' + str(self.endpoint.server_port) + '/v1',
                      'api_key_env': 'BEACON_TEST_API_KEY', 'context_tokens': 4096, 'output_tokens': 2048,
                      'adapter': 'vllm', 'engine': {'reasoning_effort': 'xhigh'}, 'revision': 'fixture-v1',
                      'cache_policy': 'shared-service-uncontrolled'}
        self.store.registry.save('models', self.model)
        install_diagnostic(self.store.root)
        self.preset = {**copy.deepcopy(DEFAULT_PRESET), 'context_tokens': 4096, 'output_tokens': 2048}

    def tearDown(self):
        self.endpoint.shutdown()
        self.endpoint.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()
        os.environ.pop('BEACON_TEST_API_KEY', None)

    def plan(self, trials=1, suite_id='connection-check-ja'):
        settings = copy.deepcopy(DEFAULT_RUN_SETTINGS)
        settings['trials_per_case'] = trials
        return self.store.plan({'model_ids': ['fixture'], 'presets': [self.preset], 'suite_id': suite_id, 'run_settings': settings})

    def run_plan(self, plan):
        identity = self.store.create_run(plan)
        asyncio.run(Worker(self.store, identity).run())
        return identity

    def test_real_pipeline_records_reasoning_usage_and_grade_without_credentials(self):
        identity = self.run_plan(self.plan())
        self.assertEqual(self.store.status(identity)['phase'], 'completed')
        result = read_json(self.store.run_path(identity) / 'summary.json')
        self.assertTrue(result['rows'][0]['grade']['passed'])
        usage = result['rows'][0]['trial']['role_totals']['target']
        self.assertEqual(usage['total_tokens'], 14)
        self.assertEqual(usage['reasoning_tokens'], 2)
        self.assertEqual(usage['cached_prompt_tokens'], 2)
        self.assertEqual(usage['requests'], 1)
        for file in self.store.run_path(identity).rglob('*'):
            if file.is_file():
                self.assertNotIn(b'fixture-private-credential', file.read_bytes())
        self.assertTrue(any(path.read_text(encoding='utf-8') == '考えます。' for path in self.store.run_path(identity).rglob('reasoning.txt')))

    def test_more_trials_reuse_the_existing_seed_prefix_and_do_not_regenerate(self):
        first = self.run_plan(self.plan(1))
        second = self.run_plan(self.plan(3))
        self.assertEqual(len(self.endpoint.requests), 3)
        self.assertEqual(self.store.status(second)['reused'], 1)
        self.assertEqual(self.store.status(second)['scored'], 3)
        original = read_json(self.store.run_path(first) / 'summary.json')['rows'][0]
        reused = read_json(self.store.run_path(second) / 'summary.json')['rows'][0]
        self.assertEqual(original['generation_fingerprint'], reused['generation_fingerprint'])
        self.assertEqual(original['trial']['seed'], reused['trial']['seed'])

    def test_expanding_suite_keeps_existing_case_revision_seed_and_answer(self):
        first_plan = self.plan()
        self.run_plan(first_plan)
        pack = load_pack(self.store.root, self.store.registry.get('suites', 'connection-check-ja'))
        first_revision = pack['cases'][0]['generation_revision']
        pack['id'] = 'expanded-control'
        pack['source']['receipts'] = [{'retrieved_at': 'new-retrieval', 'case_count': 2}]
        additional = copy.deepcopy(pack['cases'][0])
        additional['id'] = 'extra-control'
        pack['cases'].append(additional)
        install_pack(self.store.root, pack)
        expanded = load_pack(self.store.root, self.store.registry.get('suites', pack['id']))
        self.assertEqual(expanded['cases'][0]['generation_revision'], first_revision)
        identity = self.run_plan(self.plan(suite_id=pack['id']))
        self.assertEqual(self.store.status(identity)['phase'], 'completed')
        self.assertEqual(self.store.status(identity)['reused'], 1)
        self.assertEqual(len(self.endpoint.requests), 2)

    def test_regrading_changed_policy_reuses_answer_but_not_old_judgment(self):
        first = self.run_plan(self.plan())
        suite = self.store.registry.get('suites', 'connection-check-ja')
        pack = load_pack(self.store.root, suite)
        pack['cases'][0]['grader']['expected'] = '43'
        install_pack(self.store.root, pack)
        second = self.run_plan(self.plan())
        self.assertEqual(len(self.endpoint.requests), 1)
        self.assertTrue(read_json(self.store.run_path(first) / 'summary.json')['rows'][0]['grade']['passed'])
        self.assertFalse(read_json(self.store.run_path(second) / 'summary.json')['rows'][0]['grade']['passed'])

    def test_cut_stream_is_not_reusable_and_resume_preserves_the_partial_attempt(self):
        self.endpoint.mode = 'truncated'
        identity = self.run_plan(self.plan())
        root = self.store.run_path(identity)
        self.assertEqual(self.store.status(identity)['phase'], 'infrastructure_error')
        self.assertFalse(list(root.rglob('complete.json')))
        partial = next(root.rglob('response.sse'))
        original = partial.read_bytes()
        self.endpoint.mode = 'complete'
        asyncio.run(Worker(self.store, identity).run())
        self.assertEqual(self.store.status(identity)['phase'], 'completed')
        self.assertEqual(partial.read_bytes(), original)
        self.assertEqual(len(list(root.rglob('response.sse'))), 2)

    def test_stop_cancels_a_waiting_stream_and_keeps_partial_evidence(self):
        self.endpoint.mode = 'hold'
        identity = self.store.create_run(self.plan())
        worker = Worker(self.store, identity)
        async def scenario():
            job = asyncio.create_task(worker.run())
            async def started_stream():
                while not list(worker.root.rglob('response.sse')):
                    await asyncio.sleep(.01)
            await asyncio.wait_for(started_stream(), timeout=5)
            atomic_json(worker.root / 'stop.json', {'requested': True})
            await asyncio.wait_for(job, timeout=3)
        asyncio.run(scenario())
        self.assertEqual(self.store.status(identity)['phase'], 'interrupted')
        self.assertFalse(list(worker.root.rglob('complete.json')))
        self.assertTrue(list(worker.root.rglob('response.sse')))

    def test_corrupt_saved_answer_is_rejected_without_new_generation(self):
        first = self.run_plan(self.plan())
        answer = next(self.store.run_path(first).rglob('final-answer.txt'))
        answer.write_text('changed', encoding='utf-8')
        second = self.run_plan(self.plan())
        self.assertEqual(self.store.status(second)['phase'], 'infrastructure_error')
        self.assertEqual(len(self.endpoint.requests), 1)

    def test_immutable_writer_and_plan_snapshot(self):
        plan = self.plan()
        self.preset['temperature'] = .1
        self.model['engine']['reasoning_effort'] = 'low'
        saved = self.store.get_plan(plan['plan_id'])
        self.assertEqual(saved['combinations'][0]['preset']['temperature'], 1)
        self.assertEqual(saved['combinations'][0]['model']['engine']['reasoning_effort'], 'xhigh')
        path = self.root / 'immutable.json'
        write_once(path, {'answer': 1})
        write_once(path, {'answer': 1})
        with self.assertRaises(BenchError):
            write_once(path, {'answer': 2})

    def test_ui_and_cli_resolver_match_and_cross_origin_posts_are_denied(self):
        server = LocalServer('127.0.0.1', 0, self.store)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = 'http://127.0.0.1:' + str(server.server_port)
        try:
            with httpx.Client(trust_env=False) as browser:
                bootstrap = browser.get(base + '/api/bootstrap').json()
                body = {'model_ids': ['fixture'], 'presets': [self.preset], 'suite_id': 'connection-check-ja', 'run_settings': DEFAULT_RUN_SETTINGS}
                self.assertEqual(browser.post(base + '/api/plans', json=body).status_code, 403)
                headers = {'X-Beacon-Token': bootstrap['csrf_token']}
                response = browser.post(base + '/api/plans', json=body, headers=headers)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()['fingerprint'], self.store.plan(body)['fingerprint'])
                headers['Origin'] = 'https://outside.example'
                self.assertEqual(browser.post(base + '/api/plans', json=body, headers=headers).status_code, 403)
                self.assertEqual(browser.get(base + '/api/bootstrap', headers={'Host': 'outside.example'}).status_code, 403)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_run_settings_reject_invalid_values_and_external_endpoints(self):
        for count in [0, -1, 1.5, True, None]:
            with self.assertRaises(BenchError):
                validate_settings({**copy.deepcopy(DEFAULT_RUN_SETTINGS), 'trials_per_case': count})
        with self.assertRaises(BenchError):
            validate_model({**self.model, 'base_url': 'https://api.example.com/v1'})
        with self.assertRaises(BenchError):
            validate_model({**self.model, 'base_url': '/'.join(('http:', '', 'user:credential@localhost', 'v1'))})

    def test_native_executor_requires_paired_controls_and_preserves_grade_evidence(self):
        from beaconbench.util import file_sha
        script = self.root / 'fixture_grader.py'
        script.write_text('import json,sys\np=json.load(sys.stdin)\nprint(json.dumps({"status":"scored","passed":p["answer"].strip()=="42","metric_unit":"binary"}))\n', encoding='utf-8')
        executor = {'id': 'fixture-native', 'name': 'Fixture native', 'command': [sys.executable, str(script)],
                    'lock_files': [{'path': str(script), 'sha256': file_sha(script)}], 'qualified': False}
        self.store.registry.save('executors', executor)
        grader = {'kind': 'command', 'executor_id': executor['id'], 'policy': 'fixture-v1'}
        pack = load_pack(self.store.root, self.store.registry.get('suites', 'connection-check-ja'))
        pack.update(id='native-fixture', track='development')
        pack['cases'][0]['grader'] = grader
        install_pack(self.store.root, pack)
        unavailable = next(row for row in self.store.available_suites() if row['id'] == pack['id'])
        self.assertFalse(unavailable['execution_ready'])
        with self.assertRaises(BenchError):
            self.plan(suite_id=pack['id'])
        payload = {'case_id': pack['cases'][0]['id'], 'grader': grader}
        controls = [{'expected_passed': expected, 'payload': {**payload, 'answer': answer}}
                    for answer, expected in [('42', True), ('41', False)]]
        proof = asyncio.run(qualify_executor(self.store, executor['id'], controls))
        self.assertTrue(proof['qualified'])
        self.assertTrue(next(row for row in self.store.available_suites() if row['id'] == pack['id'])['execution_ready'])
        identity = self.run_plan(self.plan(suite_id=pack['id']))
        summary = read_json(self.store.run_path(identity) / 'summary.json')
        self.assertTrue(summary['rows'][0]['grade']['passed'])
        self.assertTrue(list(self.store.run_path(identity).rglob('stdout.txt')))
        script.write_text('changed', encoding='utf-8')
        with self.assertRaises(BenchError):
            self.plan(suite_id=pack['id'])
        self.assertFalse(next(row for row in self.store.available_suites() if row['id'] == pack['id'])['execution_ready'])

    def test_dead_worker_lease_is_archived_but_live_worker_is_never_displaced(self):
        plan = self.plan()
        first = self.store.create_run(plan)
        self.store.reserve(first, plan)
        atomic_json(self.store.run_path(first) / 'worker.json', {'pid': os.getpid()})
        with self.assertRaises(BenchError):
            self.store.reserve('other-run', plan)
        state = read_json(self.store.run_path(first) / 'status.json')
        atomic_json(self.store.run_path(first) / 'status.json', {**state, 'phase': 'interrupted'})
        atomic_json(self.store.run_path(first) / 'worker.json', {'pid': 0})
        self.store.reserve(first, plan)
        self.assertEqual(len(list((self.store.root / 'leases/recovered').glob('*.json'))), 1)
        self.store.release(first, plan)

    def test_separate_worker_uses_frozen_source_and_resumes_a_cut_stream(self):
        self.endpoint.mode = 'truncated'
        plan = self.plan()
        identity = launch(self.store, plan_id=plan['plan_id'])

        def finished():
            deadline = time.monotonic() + 12
            while time.monotonic() < deadline:
                state = self.store.status(identity)
                worker_path = self.store.run_path(identity) / 'worker.json'
                exited = worker_path.exists() and not process_alive(read_json(worker_path).get('pid', 0))
                if state['phase'] not in {'queued', 'preflight', 'generating', 'scoring'} and exited:
                    return state
                time.sleep(.025)
            stop(self.store, identity)
            self.fail('fixture worker did not finish')

        self.assertEqual(finished()['phase'], 'infrastructure_error')
        worker = read_json(self.store.run_path(identity) / 'worker.json')
        expected = self.store.root / 'runtime-cache' / plan['runtime_revision'] / 'beaconbench'
        self.assertEqual(Path(worker['runtime_location']), expected)
        original = next(self.store.run_path(identity).rglob('response.sse')).read_bytes()
        self.endpoint.mode = 'complete'
        # The parent application is not the code source used by the child.
        from unittest.mock import patch
        with patch('beaconbench.store.runtime_revision', return_value='updated-parent-runtime'):
            launch(self.store, resume_id=identity)
            self.assertEqual(finished()['phase'], 'completed')
        self.assertEqual(len(self.endpoint.requests), 2)
        self.assertTrue(any(path.read_bytes() == original for path in self.store.run_path(identity).rglob('response.sse')))

    def test_hard_exit_recovery_preserves_raw_and_keeps_unreported_usage_unknown(self):
        from beaconbench.recovery import recover_requests, unclosed_measurement
        from beaconbench.runner import request_totals
        from beaconbench.util import append_json
        identity = self.store.create_run(self.plan())
        run = self.store.run_path(identity)
        call = run / 'trials/config/case/0/attempts/0/calls/000000'
        write_once(call / 'request.json', {'role': 'target', 'body': {'messages': []}})
        raw = b'data: partial\n\n'
        (call / 'response.sse').write_bytes(raw)
        unknown = unclosed_measurement(call)
        self.assertIsNone(unknown['times']['request_wall_seconds'])
        self.assertIsNone(request_totals([unknown])['target']['total_tokens'])
        append_json(call / 'chunks.jsonl', {'elapsed_seconds': 1.2, 'data': {'choices': []}})
        self.assertEqual(recover_requests(run), 1)
        recovered = read_json(call / 'measurement.json')
        self.assertEqual((call / 'response.sse').read_bytes(), raw)
        self.assertFalse(recovered['completed'])
        self.assertEqual(recovered['termination'], 'process_failure')
        self.assertTrue(recovered['times']['request_wall_censored'])
        self.assertEqual(recovered['times']['request_wall_seconds'], 1.2)
        self.assertIsNone(request_totals([recovered])['target']['completion_tokens'])
        self.assertEqual(recover_requests(run), 0)


if __name__ == '__main__':
    unittest.main()
