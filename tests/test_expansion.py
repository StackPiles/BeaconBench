"""Offline acceptance for selection, role privacy, native RPC and strict grades."""
from __future__ import annotations

import asyncio
import copy
import json
import math
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from beaconbench.adapters.bindings import executor_fingerprint
from beaconbench.adapters.babel import recipe
from beaconbench.adapters.dialogue import customer_reply
from beaconbench.adapters.grades import normalize
from beaconbench.adapters.native import WIRE, execute
from beaconbench.adapters.sales import OUTPUT_CONTRACTS, PROMPT_KEYS
from beaconbench.assets import install_pack
from beaconbench.calibration import evaluate_controls, record_instrument, verify_instrument
from beaconbench.config import DEFAULT_PRESET, DEFAULT_RUN_SETTINGS, request_parameters
from beaconbench.qualification import accepted_control, complete_controls, control_expected, qualify_executor
from beaconbench.runner import Worker
from beaconbench.selection import validate_selection
from beaconbench.store import Store
from beaconbench.science_executor import scripts
from beaconbench.frontier_executor import checked_result
from beaconbench.native_bridge import completion_frames, extract_record, render, result_file, task_time
from beaconbench.native_score import grade as native_grade
from beaconbench.crm_native import extracted, run as crm_run
from beaconbench.util import BenchError, digest, file_sha, read_json, write_once


ROOT = Path(__file__).resolve().parents[1]


class SelectionAndGrading(unittest.TestCase):
    def test_native_stream_keeps_tool_indices_reasoning_and_unknown_usage(self):
        message = {'role': 'assistant', 'content': None, 'reasoning_content': 'observed reasoning',
                   'tool_calls': [{'id': 'call-a', 'type': 'function', 'function': {'name': 'execute', 'arguments': '{"content":"SELECT Id FROM Lead"}'}}]}
        frames = completion_frames({'id': 'x', 'created': 1, 'model': 'fixed', 'usage': None,
                                    'choices': [{'message': message, 'finish_reason': 'tool_calls'}]})
        self.assertEqual(len(frames), 2)
        self.assertEqual(frames[0]['choices'][0]['delta']['tool_calls'][0]['index'], 0)
        self.assertEqual(frames[0]['choices'][0]['delta']['reasoning_content'], 'observed reasoning')
        self.assertFalse(any('usage' in frame for frame in frames))
        self.assertEqual(frames[-1]['choices'][0]['finish_reason'], 'tool_calls')

    def test_native_configuration_mismatch_stops_before_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'config.json'; config.write_text('{}', encoding='utf-8')
            result = subprocess.run([sys.executable, '-m', 'beaconbench.native_bridge', '--config', str(config),
                                     '--config-sha256', '0' * 64], input='', capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertIn('configuration differs', result.stderr)
            self.assertEqual(list(Path(directory).iterdir()), [config])

    def test_crm_changed_shared_state_is_rejected_before_a_target_call(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); tasks = root / 'tasks.json'; schema = root / 'schema.json'; connector = root / 'connector.py'
            write_once(tasks, [{'idx': 78, 'task': 'lead_qualification', 'reward_metric': 'exact_match'}]); write_once(schema, [])
            connector.write_text('''from pathlib import Path
class Connector:
 def __init__(self,c): self.marker=c['marker']
 def fingerprint(self,task): return 'a'*64
 def close(self): Path(self.marker).write_text('closed')
def connect(c): return Connector(c)
''', encoding='utf-8')
            config = {'tasks_file': str(tasks), 'schema_file': str(schema), 'connector_file': str(connector),
                      'native_turn_limit': 20, 'native_turn_limit_source_sha256': 'c'*64,
                      'environment_fingerprints': {'78': 'b'*64}, 'connection': {'marker': str(root / 'closed')},
                      'lock_files': [{'path': str(p), 'sha256': file_sha(p)} for p in (tasks, schema, connector)]}
            with self.assertRaisesRegex(BenchError, '固定した比較条件'):
                crm_run(config, native_id='78', work_root=root / 'trial', target_url='http://127.0.0.1:1/v1', judge_url='http://127.0.0.1:1/v1')
            self.assertTrue((root / 'closed').exists())
            self.assertFalse((root / 'trial/grade.json').exists())

    def test_native_boundary_identity_and_crm_extractor_fail_closed(self):
        self.assertEqual(task_time(Path('.'), {'duration': .5}, {'pointer': ['duration']}, 1), .5)
        self.assertIsNone(task_time(Path('.'), {}, None, 1))
        for value in (True, -1, math.inf, 2):
            with self.assertRaises(BenchError): task_time(Path('.'), {'duration': value}, {'pointer': ['duration']}, 1)
        payload = {'case_id': 'one', 'grader': {'native_id': '1', 'family': 'babel-deepplanning-shopping'},
                   'native_result': {'case_id': 'one', 'native_id': '1', 'family': 'babel-deepplanning-shopping',
                                     'coverage': {'all_model_calls_via_rpc': True}, 'grade_record': {'case_name': 'case_1', 'case_score': 1}}}
        self.assertTrue(native_grade(payload)['passed'])
        payload['native_result']['native_id'] = '2'
        with self.assertRaises(BenchError): native_grade(payload)
        with self.assertRaises(BenchError): normalize('babel-deepplanning-shopping', {'case_name': 'case_21', 'case_score': 1}, '1')
        self.assertEqual(extracted({'extracted_answers': ['Need', 'Authority']}), ['Authority', 'Need'])
        for value in ({'extracted_answers': []}, {'extracted_answers': ['Need', 'Need']}, {'extracted_answers': ['Need'], 'explanation': 'extra'}):
            with self.assertRaises(BenchError): extracted(value)

    def test_babel_recipe_uses_one_explicit_case_and_records_native_limits(self):
        for family, domain in [('babel-tau3', 'retail'), ('babel-vitabench', 'cross')]:
            prepared = recipe(family, domain)
            args = prepared['native_command']
            self.assertEqual(args[args.index('--num-trials') + 1], '1')
            self.assertEqual(args[args.index('--task-ids') + 1], '${NATIVE_ID}')
            self.assertEqual(args[args.index('--max-concurrency') + 1], '1')
            self.assertFalse(prepared['execution_ready'])
            self.assertIsNone(prepared['native_rules']['framework_caps'])
        self.assertIn('max_retries=3', recipe('babel-vitabench', 'cross')['known_blockers'][0])
        claw = recipe('babel-claw', 'general')
        self.assertIn('run', claw['native_command']); self.assertNotIn('batch', claw['native_command'])
        models = render(recipe('babel-tau3', 'retail')['inline_files']['models.json'], {
            **{role.upper() + '_BASE_URL': 'http://localhost/' + role for role in ('target', 'customer', 'judge')},
            **{role.upper() + '_CONTEXT_TOKENS': 262144 for role in ('target', 'customer', 'judge')},
            **{role.upper() + '_OUTPUT_TOKENS': 131072 for role in ('target', 'customer', 'judge')}, 'PROXY_KEY': 'ephemeral'})
        self.assertEqual(models['models'][0]['max_input_tokens'], 262144)

    def test_native_record_extraction_rejects_duplicate_trials_and_ambiguous_files(self):
        values = [{'type': 'message'}, {'type': 'grading_result', 'task_id': 'x', 'passed': True}]
        spec = {'filter': {'type': 'grading_result'}, 'id_field': 'task_id', 'expected_id': 'x'}
        self.assertTrue(extract_record(values, spec)['passed'])
        with self.assertRaises(BenchError): extract_record(values + values, spec)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'a.json').write_text('{}'); (root / 'b.json').write_text('{}')
            with self.assertRaises(BenchError): result_file(root, {'glob': '*.json'})

    def test_science_builds_independent_first_steps_and_frontier_requires_all_tests(self):
        preflight, solution = scripts({'required_dependencies': 'import numpy as np', 'step': {'step_number': '9.1', 'test_cases': ['assert answer == target']}}, 'answer=1', '9.1')
        self.assertNotIn('answer=1', preflight); self.assertIn('answer=1', solution)
        self.assertIn("process_hdf5_to_tuple('9.1',1", preflight)
        with self.assertRaises(BenchError): scripts({'step': {'step_number': '9.2'}}, 'answer=1', '9.2')
        result = {'status': 'done', 'score': 75, 'cases': [{'scoreRatio': 1}, {'scoreRatio': .5}]}
        self.assertEqual(checked_result(result, pid='0', expected_test_count=2, score_formula_sha256='a'*64)['score'], 75)
        self.assertNotIn('passed', checked_result(result, pid='0', expected_test_count=2, score_formula_sha256='a'*64))
        with self.assertRaises(BenchError): checked_result(result, pid='0', expected_test_count=3, score_formula_sha256='a'*64)
        with self.assertRaises(BenchError): checked_result({**result, 'score': 80}, pid='0', expected_test_count=2, score_formula_sha256='a'*64)
        self.assertEqual(checked_result({'status': 'error'}, pid='0', expected_test_count=2, score_formula_sha256='a'*64)['status'], 'infrastructure_error')
        self.assertEqual(checked_result({'status': 'error', 'error': 'Error: compile failed: invalid submitted code'}, pid='0', expected_test_count=2, score_formula_sha256='a'*64)['score'], 0)
        self.assertEqual(checked_result({'status': 'error', 'error': 'Error: checker compile failed: missing dependency'}, pid='0', expected_test_count=2, score_formula_sha256='a'*64)['status'], 'infrastructure_error')

    def test_selection_is_explicit_nested_and_does_not_control_trials(self):
        selection = read_json(ROOT / 'catalog/selection.v1.candidate.json')
        quick = read_json(ROOT / 'suites/quick.v1.candidate.json')
        detailed = read_json(ROOT / 'suites/detailed.v1.candidate.json')
        self.assertEqual(validate_selection(selection, quick, detailed)['cases_selected'], 100)
        self.assertFalse(detailed['selection_frozen'])
        self.assertFalse(detailed['execution_ready'])
        for mutate in ('duplicate', 'quota', 'trial', 'ready'):
            s, q, d = copy.deepcopy(selection), copy.deepcopy(quick), copy.deepcopy(detailed)
            if mutate == 'duplicate': s['cases'][1]['native_id'] = s['cases'][0]['native_id']; s['cases'][1]['domain'] = s['cases'][0]['domain']
            if mutate == 'quota': s['cases'][0]['source_id'] = 'unknown'
            if mutate == 'trial': q['trials_per_case'] = 3
            if mutate == 'ready': d['execution_ready'] = True
            with self.assertRaises(BenchError, msg=mutate): validate_selection(s, q, d)

    def test_native_grades_preserve_units_and_missing_is_not_failure(self):
        tau = {'task_id': 'x', 'reward_info': {'reward': .5}}
        result = normalize('babel-tau3', tau, 'x')
        self.assertEqual(result['score'], .5); self.assertFalse(result['passed'])
        self.assertTrue(normalize('babel-claw', {'task_id': 'x', 'passed': True, 'task_score': .8}, 'x')['passed'])
        sales = {'id': 'x', 'instrument_valid': True, 'conversation_quality': 7, 'A/B': 'B'}
        result = normalize('salesllm-ja', sales, 'x')
        self.assertEqual(result['score'], 7.5); self.assertNotIn('passed', result)
        science = {'step_id': 'x', 'per_environment': {'2024': {'passed': False}, '2025': {'passed': True}}}
        self.assertTrue(normalize('scicode-verified', science, 'x')['passed'])
        self.assertEqual(normalize('babel-deepplanning-travel', {'sample_id': 'x', 'conversion_valid': False}, 'x')['status'], 'invalid_instrument')
        for family, row, native_id in [
            ('babel-tau3', tau, 'wrong'),
            ('babel-tau3', {'task_id': 'x', 'reward_info': {'reward': math.nan}}, 'x'),
            ('babel-claw', {'task_id': 'x', 'passed': 'false', 'task_score': .5}, 'x'),
            ('babel-deepplanning-travel', {'sample_id': 'x', 'success': False}, 'x'),
            ('salesllm-ja', {**sales, 'conversation_quality': 7.1}, 'x'),
        ]:
            with self.assertRaises((BenchError, KeyError)): normalize(family, row, native_id)

    def test_continuous_controls_do_not_invent_a_pass_threshold(self):
        rows = []
        for value in (2, 8):
            expected = control_expected({'expected_score': value, 'metric_unit': 'native_points'})
            rows.append({'expected': expected, 'accepted': accepted_control({'status': 'scored', 'score': value, 'metric_unit': 'native_points'}, expected)})
        self.assertTrue(complete_controls(rows))
        self.assertFalse(complete_controls(rows[:1]))
        self.assertFalse(accepted_control({'status': 'scored', 'score': 8, 'metric_unit': 'percent'}, rows[-1]['expected']))

    def test_calibration_requires_coverage_and_keeps_unknowns(self):
        expected = [{'id': 'a', 'label': 'yes', 'critical': True}, {'id': 'b', 'label': 'no'}]
        self.assertTrue(evaluate_controls(expected, expected, ['yes', 'no'])['passed'])
        wrong = evaluate_controls(expected, [{'id': 'a', 'label': 'maybe'}, expected[1]], ['yes', 'no'])
        self.assertEqual(wrong['unknown'], 1); self.assertEqual(wrong['critical_errors'], ['a'])
        self.assertFalse(wrong['passed'])
        with self.assertRaises(BenchError): evaluate_controls(expected, expected[:1], ['yes', 'no'])
        with self.assertRaises(BenchError): customer_reply('{"reply":"はい","conversation_ended":"false","end_reason":""}')


class ModelFixture(BaseHTTPRequestHandler):
    def log_message(self, *args): pass

    def do_GET(self):
        raw = json.dumps({'data': [{'id': role} for role in ['target', 'customer', 'judge', 'intent_judge', 'language_judge']]}).encode()
        self.send_response(200); self.send_header('Content-Length', str(len(raw))); self.end_headers(); self.wfile.write(raw)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.server.requests.append(body)
        text = {'target': '42', 'customer': '{"reply":"条件に合うので購入します。","conversation_ended":true,"end_reason":"条件を確認して購入を選択"}',
                'judge': '{"score":8,"evidence":"商品条件に基づく会話"}',
                'intent_judge': '{"label":"A","evidence":"顧客が購入を明示"}',
                'language_judge': '{"valid":true,"target_japanese":false,"evidence":"顧客は日本語だが、営業役は数字だけで日本語の成立を確認できない"}'}[body['model']]
        frames = [{'choices': [{'index': 0, 'delta': {'role': 'assistant', 'content': text}}]},
                  {'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]},
                  {'choices': [], 'usage': {'prompt_tokens': 10, 'completion_tokens': 4, 'total_tokens': 14}}]
        raw = (''.join('data: ' + json.dumps(row, ensure_ascii=False) + '\n\n' for row in frames) + 'data: [DONE]\n\n').encode()
        self.send_response(200); self.send_header('Content-Type', 'text/event-stream'); self.send_header('Content-Length', str(len(raw))); self.end_headers(); self.wfile.write(raw)


class MeasuredAdapters(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.endpoint = ThreadingHTTPServer(('127.0.0.1', 0), ModelFixture)
        self.endpoint.requests = []; self.endpoint.daemon_threads = True
        self.thread = threading.Thread(target=self.endpoint.serve_forever, daemon=True); self.thread.start()
        os.environ['BEACON_EXPANSION_KEY'] = 'fixture-secret-never-forwarded'
        self.store = Store(self.root / 'data')
        self.preset = {**copy.deepcopy(DEFAULT_PRESET), 'context_tokens': 4096, 'output_tokens': 2048}
        self.models = {}
        for role in ['target', 'customer', 'judge', 'intent_judge', 'language_judge']:
            self.models[role] = self.store.registry.save('models', {
                'id': role, 'name': role, 'api_model': role, 'base_url': f'http://127.0.0.1:{self.endpoint.server_port}/v1',
                'api_key_env': 'BEACON_EXPANSION_KEY', 'context_tokens': 4096, 'output_tokens': 2048,
                'adapter': 'vllm', 'engine': {'reasoning_effort': 'xhigh'}, 'revision': 'fixture-1', 'cache_policy': 'shared-service-uncontrolled'})
            self.models[role] = self.store.registry.get('models', role)

    def tearDown(self):
        self.endpoint.shutdown(); self.endpoint.server_close(); self.thread.join(timeout=2)
        self.temp.cleanup(); os.environ.pop('BEACON_EXPANSION_KEY', None)

    def helper(self, role, policy):
        folder = self.store.root / 'instruments' / (role + digest(policy)[:8])
        write_once(folder / 'controls.json', {'fixture': True, 'expected': ['yes', 'no'], 'observed': ['yes', 'no']})
        receipt = {'fixture': True, 'passed': True, 'instrument_fingerprint': digest({'model': self.models[role], 'preset': self.preset, 'policy': policy}),
                   'files': {(folder / 'controls.json').relative_to(self.store.root).as_posix(): file_sha(folder / 'controls.json')}}
        write_once(folder / 'receipt.json', receipt)
        return {'model_id': role, 'preset': self.preset, 'policy': policy, 'instrument': {
            'path': (folder / 'receipt.json').relative_to(self.store.root).as_posix(), 'sha256': file_sha(folder / 'receipt.json')}}

    def plan(self, identity):
        return self.store.plan({'model_ids': ['target'], 'presets': [self.preset], 'suite_id': identity, 'run_settings': copy.deepcopy(DEFAULT_RUN_SETTINGS)})

    def test_sales_pipeline_keeps_personas_private_and_counts_all_roles(self):
        spec = {'kind': 'salesllm_ja', 'native_id': 'authored-control', 'proportion': .5,
                'quality_prompt': '会話品質の固定control基準。', 'intent_prompt': '購買意向の固定control基準。', 'language_prompt': '日本語成立の固定control基準。'}
        spec['helpers'] = {role: self.helper(role, {'prompt_sha256': digest({'prompt': spec[key], 'output_contract': OUTPUT_CONTRACTS[role]})}) for role, key in PROMPT_KEYS.items()}
        pack = {'id': 'sales-control', 'name': 'Sales fixture', 'language': 'ja', 'track': 'development',
                'source': {'name': 'authored fixture', 'revision': '1'}, 'cases': [{
                    'id': 'sale', 'title': '営業control', 'category': 'sales', 'generation': {
                        'adapter': 'dialogue', 'script': {'language': 'ja', 'assistant_system_prompt': 'PRIVATE_PRODUCT_42',
                         'customer_system_prompt': 'PRIVATE_CUSTOMER_42', 'trigger_sentence': '商品の条件を教えてください。'},
                        'policy': {'id': 'fixture-sales'}, 'helpers': {'customer': self.helper('customer', {'id': 'fixture-customer'})}}, 'grader': spec}]}
        install_pack(self.store.root, pack)
        plan = self.plan(pack['id']); identity = self.store.create_run(plan)
        asyncio.run(Worker(self.store, identity).run())
        self.assertEqual(self.store.status(identity)['phase'], 'completed')
        self.assertEqual(len(self.endpoint.requests), 5)
        target, customer = self.endpoint.requests[:2]
        self.assertNotIn('PRIVATE_CUSTOMER_42', json.dumps(target))
        self.assertNotIn('PRIVATE_PRODUCT_42', json.dumps(customer))
        summary = read_json(self.store.run_path(identity) / 'summary.json')
        self.assertEqual(summary['rows'][0]['grade']['score'], 9)
        self.assertFalse(summary['rows'][0]['grade']['language_consistency']['target_japanese'])
        self.assertNotIn('passed', summary['rows'][0]['grade'])
        self.assertEqual(sum(row['requests'] for row in summary['observed_usage_in_this_run'].values()), 5)
        dialogue = next(self.store.run_path(identity).rglob('dialogue.jsonl')).read_text(encoding='utf-8').splitlines()
        self.assertEqual(len(dialogue), 3); self.assertIn('購入します', dialogue[-1])
        self.assertIsNone(plan['framework_limits']['agent_turns'])
        # A fixture proof may not enter the formal benchmark track.
        pack['track'] = 'benchmark'; install_pack(self.store.root, pack)
        with self.assertRaises(BenchError): self.plan(pack['id'])
        for path in self.store.run_path(identity).rglob('*'):
            if path.is_file(): self.assertNotIn(b'fixture-secret-never-forwarded', path.read_bytes())

    def test_instrument_import_binds_reviewed_controls_and_forbids_fixture_promotion(self):
        policy = {'id': 'authored-control'}
        expected = self.root / 'expected.json'; observed = self.root / 'observed.json'
        cases = [{'id': '1', 'label': 'yes'}, {'id': '2', 'label': 'no'}]
        write_once(expected, {'language': 'ja', 'reviewed': True, 'review_basis': 'authored test control', 'fixture': True, 'labels': ['yes', 'no'], 'cases': cases})
        write_once(observed, {'fixture': True, 'instrument_fingerprint': digest({'model': self.models['judge'], 'preset': self.preset, 'policy': policy}), 'cases': cases})
        with self.assertRaises(BenchError): record_instrument(self.store, 'judge', self.preset, policy, expected, observed)
        reference = record_instrument(self.store, 'judge', self.preset, policy, expected, observed, fixture=True)
        self.assertTrue(reference['passed']); self.assertEqual(len(self.endpoint.requests), 0)
        verify_instrument(self.store.root, reference, self.models['judge'], self.preset, policy, allow_fixture=True)
        with self.assertRaises(BenchError): verify_instrument(self.store.root, reference, self.models['judge'], self.preset, policy)
        # Removing a fixture declaration does not replace measured raw calls.
        expected.unlink(); observed.unlink()
        write_once(expected, {'language': 'ja', 'reviewed': True, 'review_basis': 'authored test control', 'labels': ['yes', 'no'], 'cases': cases})
        write_once(observed, {'instrument_fingerprint': digest({'model': self.models['judge'], 'preset': self.preset, 'policy': policy}), 'cases': cases})
        with self.assertRaises(BenchError): record_instrument(self.store, 'judge', self.preset, policy, expected, observed)

    def test_native_pack_requires_paired_controls_and_the_frozen_worker_runs_it(self):
        script = self.root / 'native_worker_fixture.py'
        script.write_text('''import json,sys
p=json.loads(sys.stdin.readline())
print(json.dumps({"type":"model_request","id":"one","role":"target","body":{"messages":[{"role":"user","content":"fixture"}]}}),flush=True)
r=json.loads(sys.stdin.readline())
print(json.dumps({"protocol":p["protocol"],"type":"result","case_id":p["payload"]["case_id"],"answer":r["response"]["choices"][0]["message"]["content"],"coverage":{"all_model_calls_via_rpc":True}}),flush=True)
''', encoding='utf-8')
        generation_executor = {'id': 'native-worker-fixture', 'name': 'Native fixture', 'purpose': 'native_generation', 'family': 'babel-tau3',
                               'required_roles': ['target'], 'command': [sys.executable, '-u', str(script)],
                               'lock_files': [{'path': str(script), 'sha256': file_sha(script)}]}
        self.store.registry.save('executors', generation_executor)
        grader_script = self.root / 'native_grader_fixture.py'
        grader_script.write_text('import json,sys\np=json.load(sys.stdin)\nprint(json.dumps({"status":"scored","passed":p["answer"]=="42","metric_unit":"binary"}))\n', encoding='utf-8')
        self.store.registry.save('executors', {'id': 'native-fixture-grade', 'command': [sys.executable, str(grader_script)],
                                             'lock_files': [{'path': str(grader_script), 'sha256': file_sha(grader_script)}], 'qualified': False})
        grader = {'kind': 'command', 'executor_id': 'native-fixture-grade', 'native_executor_fingerprint': executor_fingerprint(generation_executor)}
        pack = {'id': 'native-pack-fixture', 'name': 'Native fixture pack', 'language': 'ja', 'track': 'development',
                'source': {'name': 'authored fixture', 'revision': '1'}, 'cases': [{'id': 'native-one', 'title': 'Native control', 'category': 'fixture',
                    'generation': {'adapter': 'native', 'executor_id': generation_executor['id'], 'native_id': 'x', 'family': 'babel-tau3'}, 'grader': grader}]}
        install_pack(self.store.root, pack)
        with self.assertRaises(BenchError): self.plan(pack['id'])
        controls = [{'expected_passed': value, 'payload': {'case_id': 'native-one', 'grader': grader, 'answer': '42' if value else 'wrong'}} for value in (True, False)]
        asyncio.run(qualify_executor(self.store, 'native-fixture-grade', controls))
        plan = self.plan(pack['id']); identity = self.store.create_run(plan)
        asyncio.run(Worker(self.store, identity).run())
        self.assertEqual(self.store.status(identity)['phase'], 'completed')
        self.assertEqual(len(self.endpoint.requests), 1)
        row = read_json(self.store.run_path(identity) / 'summary.json')['rows'][0]
        self.assertTrue(row['grade']['passed']); self.assertIsNone(row['trial']['task_wall_seconds'])

    def test_native_rpc_preserves_tools_overrides_sampling_and_hides_model_keys(self):
        script = self.root / 'native_fixture.py'
        script.write_text('''import json,os,sys
assert "BEACON_EXPANSION_KEY" not in os.environ
initial=json.loads(sys.stdin.readline())
print(json.dumps({"type":"model_request","id":"call1","role":"target","body":{"model":"native","temperature":0,"messages":[{"role":"user","content":"42"}],"tools":[{"type":"function","function":{"name":"fixture_tool","parameters":{"type":"object"}}}]}}),flush=True)
response=json.loads(sys.stdin.readline())
print(json.dumps({"protocol":initial["protocol"],"type":"result","case_id":initial["payload"]["case_id"],"answer":response["response"]["choices"][0]["message"]["content"],"task_wall_seconds":0.1,"coverage":{"all_model_calls_via_rpc":True}}),flush=True)
''', encoding='utf-8')
        executor = {'command': [sys.executable, '-u', str(script)], 'lock_files': [{'path': str(script), 'sha256': file_sha(script)}]}
        roles = {'target': {'model': self.models['target'], 'request_parameters': request_parameters(self.models['target'], self.preset)}}
        result = asyncio.run(execute(executor, {'case_id': 'x', 'generation': {}}, roles, self.root / 'trial', lambda: False, lambda fields: None, request_seed=37))
        self.assertEqual(result['answer'], '42')
        self.assertEqual(self.endpoint.requests[0]['temperature'], 1)
        self.assertEqual(self.endpoint.requests[0]['seed'], 37)
        self.assertEqual(self.endpoint.requests[0]['tools'][0]['function']['name'], 'fixture_tool')
        source = self.root / 'trial/calls/000000'
        names = ('request.json', 'measurement.json', 'message.json', 'response.sse', 'chunks.jsonl')
        replay = [{'folder': str(source), 'files': {name: file_sha(source / name) for name in names}}]
        again = asyncio.run(execute(executor, {'case_id': 'x', 'generation': {}}, roles, self.root / 'resumed',
                                    lambda: False, lambda fields: None, request_seed=37, replay_calls=replay))
        self.assertEqual(len(self.endpoint.requests), 1)
        self.assertEqual(again['answer'], '42')
        self.assertIsNone(again['task_wall_seconds'])
        self.assertEqual(again['recovery']['replayed_model_calls'], 1)

    def test_native_bridge_runs_one_pinned_cli_through_the_same_transport(self):
        script = self.root / 'cli_fixture.py'
        script.write_text('''import json,sys,urllib.request
url,key,output,task=sys.argv[1:]
request=urllib.request.Request(url+"/chat/completions",data=json.dumps({"model":"target","messages":[{"role":"user","content":"fixture"}]}).encode(),headers={"Content-Type":"application/json","Authorization":"Bearer "+key})
with urllib.request.urlopen(request) as r: response=json.load(r)
with open(output,"w") as f: json.dump({"task_id":task,"reward_info":{"reward":1},"answer":response["choices"][0]["message"]["content"]},f)
''', encoding='utf-8')
        config = {'work_root': str(self.root / 'native-work'), 'source_root': str(self.root), 'required_roles': ['target'],
                  'lock_files': [{'path': str(script), 'sha256': file_sha(script)}],
                  'native_command': [sys.executable, str(script), '${TARGET_BASE_URL}', '${PROXY_KEY}', '${WORK_ROOT}/grade.json', '${NATIVE_ID}'],
                  'result': {'path': 'grade.json', 'id_field': 'task_id'}}
        path = self.root / 'native-config.json'; write_once(path, config)
        executor = {'command': [sys.executable, '-u', '-m', 'beaconbench.native_bridge', '--config', str(path)],
                    'lock_files': [{'path': str(path), 'sha256': file_sha(path)}]}
        roles = {'target': {'model': self.models['target'], 'request_parameters': request_parameters(self.models['target'], self.preset)}}
        result = asyncio.run(execute(executor, {'case_id': 'x', 'generation': {'native_id': 'x', 'family': 'babel-tau3'}}, roles, self.root / 'trial', lambda: False, lambda fields: None, request_seed=37))
        self.assertTrue(normalize('babel-tau3', result['grade_record'], 'x')['passed'])
        self.assertIsNone(result['task_wall_seconds'])
        self.assertGreater(result['native_pipeline_seconds'], 0)
        self.assertEqual(len(self.endpoint.requests), 1)
        artifacts = read_json(self.root / 'trial/native/artifact-manifest.json')
        self.assertEqual(len(artifacts), 3)
        for entry in artifacts.values():
            self.assertEqual(file_sha(self.root / 'trial/native/artifacts' / entry['path']), entry['sha256'])


if __name__ == '__main__': unittest.main()
