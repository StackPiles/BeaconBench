"""Boundary and evidence tests for source-only termination and audit v2."""
import asyncio
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from beaconbench import vita_goal_audit_v2 as audit
from beaconbench import vita_termination as stop
from beaconbench.adapters.native import execute
from beaconbench.evaluation_contracts import policy, review
from beaconbench.runner import Worker, saved_requests
from beaconbench.util import BenchError, file_sha, read_json


class SourceContracts(unittest.TestCase):
    def setUp(self):
        self.reference = '<instructions>青いマグを2個注文してください。</instructions>'
        self.messages = [{'role': 'user', 'content': '青いマグを2個注文してください。'},
            {'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'a', 'type': 'function',
                'function': {'name': 'create_order', 'arguments': '{"color":"blue","count":2}'}}]},
            {'role': 'tool', 'tool_call_id': 'a', 'name': 'create_order', 'content': '注文完了：青いマグ2個。'},
            {'role': 'assistant', 'content': '注文を完了しました。'},
            {'role': 'user', 'content': 'ありがとうございます。###STOP###'}]
        self.source = stop.material(self.reference, self.messages)
        self.candidate = {'verdict': 'completed', 'explanation': '実注文が完了。', 'checks': [
            {'condition_index': 0, 'state': 'completed', 'explanation': '青いマグ2個の注文結果。',
             'disclosure': [{'message_index': 0, 'message_quote': '青いマグを2個注文してください。'}],
             'evidence': [{'message_index': 2, 'message_quote': '注文完了：青いマグ2個。'}]}], 'technical_evidence': []}
        self.verified = {'verdict': 'supported', 'explanation': '注文結果で確認。',
                         'evidence': [{'message_index': 2, 'message_quote': '注文完了：青いマグ2個。'}]}

    def test_stop_requires_every_source_condition_and_genuine_actor_quotes(self):
        self.assertEqual(stop.parse(json.dumps(self.candidate), self.source), self.candidate)
        invalid = []
        value = copy.deepcopy(self.candidate); value['checks'] = []; invalid.append(value)
        value = copy.deepcopy(self.candidate); value['checks'][0]['state'] = 'unmet'; invalid.append(value)
        value = copy.deepcopy(self.candidate); value['verdict'] = 'premature'; invalid.append(value)
        value = copy.deepcopy(self.candidate); value['checks'][0]['evidence'][0]['message_quote'] = '架空'; invalid.append(value)
        value = copy.deepcopy(self.candidate); value['checks'][0]['disclosure'] = [{'message_index': 3, 'message_quote': '注文を完了しました。'}]; invalid.append(value)
        for value in invalid:
            with self.assertRaises(BenchError): stop.parse(json.dumps(value), self.source)

    def test_technical_failure_and_inactive_branch_are_legitimate_ends(self):
        source = stop.material(self.reference, [self.messages[0], {'role': 'assistant', 'content': '注文APIが停止し、技術的に注文できません。'}, self.messages[-1]])
        value = copy.deepcopy(self.candidate)
        value.update(verdict='technical_failure', technical_evidence=[{'message_index': 1, 'message_quote': '注文APIが停止し、技術的に注文できません。'}])
        value['checks'][0].update(state='unmet', evidence=[])
        self.assertEqual(stop.parse(json.dumps(value), source)['verdict'], 'technical_failure')
        source = stop.material('<instructions>雨の場合だけ傘を予約してください。今日は晴れです。</instructions>', self.messages)
        value = copy.deepcopy(self.candidate)
        value['checks'] = [{'condition_index': i, 'state': 'not_applicable', 'explanation': '晴れで作業不要。', 'disclosure': [], 'evidence': []} for i in range(2)]
        self.assertEqual(stop.parse(json.dumps(value), source)['verdict'], 'completed')

    def test_single_condition_verdict_is_aggregated_without_a_competing_global_label(self):
        from beaconbench.evaluation_contracts import indexed_material, parse_decision
        from beaconbench import vita_goals
        material = vita_goals.audit_material(indexed_material({'reference': '<instructions>和室です。</instructions>',
            'messages': [{'role': 'user', 'content': '和室を取り下げて洋室に変えます。'}]}))
        raw = {'check': {'condition_index': 0, 'verdict': 'waived', 'explanation': '顧客の変更。',
                        'evidence': [{'message_index': 0, 'message_quote': '和室を取り下げて洋室に変えます。'}]},
               'finding': {'rule_quote': '和室です。', 'message_index': 0,
                           'message_quote': '和室を取り下げて洋室に変えます。', 'explanation': '固定条件を変更。'}}
        parsed = parse_decision(json.dumps(raw), material, 'vita_customer', audit.VERSION)
        self.assertEqual(parsed['verdict'], 'violation')
        raw['finding'] = None
        with self.assertRaises(BenchError): parse_decision(json.dumps(raw), material, 'vita_customer', audit.VERSION)

    def test_index_citations_copy_exact_source_text_and_never_repair_bad_quotes(self):
        evidence = audit.resolve_evidence([{'message_index': 2}], self.source)
        self.assertEqual(evidence, [{'message_index': 2, 'message_quote': '注文完了：青いマグ2個。'}])
        self.assertEqual(audit.resolve_evidence([2], self.source), evidence)
        for invalid in ([{'message_index': 999}], [{'message_index': True}],
                        [True], [2.0], [999], [{'message_index': 2, 'message_quote': '注文完了：赤いマグ2個。'}]):
            with self.assertRaises(BenchError): audit.resolve_evidence(invalid, self.source)

    def test_technical_error_can_corroborate_an_agent_statement_but_not_replace_it(self):
        source = stop.material(self.reference, [self.messages[0],
            {'role': 'tool', 'content': 'Error: 注文API停止'},
            {'role': 'assistant', 'content': 'API停止により技術的に注文できません。'}, self.messages[-1]])
        value = copy.deepcopy(self.candidate)
        value.update(verdict='technical_failure', technical_evidence=[
            {'message_index': 1, 'message_quote': 'Error: 注文API停止'},
            {'message_index': 2, 'message_quote': 'API停止により技術的に注文できません。'}])
        value['checks'][0].update(state='unmet', evidence=[])
        self.assertEqual(stop.parse(json.dumps(value), source)['verdict'], 'technical_failure')
        value['technical_evidence'].pop()
        with self.assertRaises(BenchError): stop.parse(json.dumps(value), source)

    def test_all_native_stop_markers_are_checked_and_tool_calls_do_not_stop(self):
        for marker in stop.MARKERS:
            self.assertTrue(stop.has_stop({'content': marker}))
            self.assertFalse(stop.has_stop({'content': marker, 'tool_calls': [{'id': 'tool'}]}))

    def test_target_is_unmodified_and_old_stop_rewriting_cannot_be_combined(self):
        body = {'messages': [{'role': 'system', 'content': self.reference}]}
        self.assertIs(stop.decorate(body, 'target'), body)
        new = stop.decorate(body, 'customer')
        self.assertNotEqual(new, body)
        self.assertEqual(body['messages'][0]['content'], self.reference)
        generation = {'family': 'babel-vitabench', 'policy': {'vita_termination': stop.VERSION}, 'helpers': {
            'customer': {'policy': {'termination_contract': stop.contract()}}, stop.ROLE: {'policy': stop.policy()}}}
        self.assertTrue(stop.validate(generation))
        generation['policy']['vita_payment_completion'] = 'legacy'
        with self.assertRaises(BenchError): stop.validate(generation)
        self.assertFalse(stop.validate({'family': 'babel-vitabench'}))

    def test_guard_preserves_full_target_history_and_counts_stop_helper_calls(self):
        guard = stop.Guard()
        guard.observe({'messages': [{'role': 'system', 'content': '対象モデル用'}, *self.messages[:-2]]}, 'target')
        asyncio.run(guard.check(self.messages[-2], 'target', {}, Path('.'), lambda: False, lambda _: None, 42))
        guard.observe({'messages': [{'role': 'system', 'content': self.reference}, {'role': 'user', 'content': '注文を完了しました。'}]}, 'customer')
        supplied = []
        async def fake_chat(model, body, folder, role, *args):
            supplied.append(json.loads(body['messages'][1]['content']))
            folder.mkdir(parents=True)
            (folder / 'measurement.json').write_text(json.dumps({'role': role}), encoding='utf-8')
            value = self.candidate if len(supplied) == 1 else self.verified
            return {'message': {'content': json.dumps(value)}}
        with tempfile.TemporaryDirectory() as directory, patch('beaconbench.vita_termination.chat', fake_chat):
            attempt = Path(directory)
            original = copy.deepcopy(self.messages[-1])
            asyncio.run(guard.check(original, 'customer', {'model': {}, 'request_parameters': {}},
                attempt / 'calls/000004/termination', lambda: False, lambda _: None, 42))
            self.assertEqual(supplied[0]['messages'], self.source['messages'])
            self.assertEqual(supplied[1]['messages'], self.source['messages'])
            self.assertEqual(original, self.messages[-1])
            self.assertEqual(len(saved_requests(attempt)), 2)
            self.assertTrue(guard.reports[0]['allowed'])

    def test_rejected_or_malformed_confirmation_never_forwards_stop(self):
        for verification in ('broken', json.dumps({**self.verified, 'verdict': 'contradicted'})):
            replies = iter([json.dumps(self.candidate), verification])
            async def fake_chat(*args): return {'message': {'content': next(replies)}}
            with tempfile.TemporaryDirectory() as tmp, patch('beaconbench.vita_termination.chat', fake_chat):
                result = asyncio.run(stop.review(self.source, {'model': {}, 'request_parameters': {}}, Path(tmp), lambda: False, lambda _: None, 42))
                self.assertFalse(result['allowed'])
                self.assertEqual(result['action'], 'invalid-instrument-stop')

    def test_invalid_instrument_cannot_be_quality_retried_by_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / 'trial'
            evidence = folder / 'attempts/0/native/execution.json'
            evidence.parent.mkdir(parents=True)
            evidence.write_text('{"failure":"InvalidCustomerTermination"}', encoding='utf-8')
            before = file_sha(evidence)
            worker = SimpleNamespace(store=SimpleNamespace(root=root), plan={},
                generation_fingerprint=lambda *args: ('a' * 64, 42), verified=lambda *args: None)
            with self.assertRaisesRegex(BenchError, '同じ条件で再生成できません'):
                asyncio.run(Worker.generate(worker, {}, {'id': 'fixture'}, 0, folder))
            self.assertEqual(file_sha(evidence), before)
            self.assertFalse((folder / 'attempts/1').exists())

    def test_invalid_customer_stop_aborts_rpc_without_another_target_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = root / 'native_fixture.py'
            script.write_text('''import json,sys
start=json.loads(input())
print(json.dumps({'type':'model_request','id':'customer-0','role':'customer','body':{'messages':[
 {'role':'system','content':'<instructions>マグを注文してください。</instructions>'},
 {'role':'user','content':'注文しないまま終了します。'}]}}),flush=True)
if sys.stdin.readline():
 open('unexpected-forward','w').write('customer stop was forwarded')
''', encoding='utf-8')
            generation = {'family': 'babel-vitabench', 'policy': {'vita_termination': stop.VERSION}, 'helpers': {
                'customer': {'policy': {'termination_contract': stop.contract()}}, stop.ROLE: {'policy': stop.policy()}}}
            executor = {'command': [sys.executable, str(script)], 'cwd': str(root),
                        'environment': {'PYTHONUTF8': '1'}, 'lock_files': [{'path': str(script), 'sha256': file_sha(script)}]}
            roles = {r: {'model': {'api_key_env': None, 'context_tokens': 8192}, 'request_parameters': {'max_tokens': 1024}} for r in ('target', 'customer', stop.ROLE)}
            calls = []
            async def fake_chat(model, body, folder, role, *args):
                calls.append(role)
                folder.mkdir(parents=True)
                (folder / 'message.json').write_text('{"content":"未注文です。###STOP###"}', encoding='utf-8')
                return {'message': {'content': '未注文です。###STOP###'}, 'measurement': {}}
            async def rejected(*args, **kwargs):
                return {'allowed': False, 'action': 'invalid-instrument-stop'}
            with patch('beaconbench.adapters.native.chat', fake_chat), patch('beaconbench.vita_termination.review', rejected):
                with self.assertRaises(stop.InvalidCustomerTermination):
                    asyncio.run(execute(executor, {'case_id': 'fixture', 'generation': generation}, roles,
                        root / 'attempt', lambda: False, lambda _: None, request_seed=42))
            self.assertEqual(calls, ['customer'])
            self.assertFalse((root / 'unexpected-forward').exists())
            self.assertFalse((root / 'attempt/native-result.json').exists())
            self.assertEqual(read_json(root / 'attempt/termination-checks.json')['failure'], 'InvalidCustomerTermination')
            self.assertTrue((root / 'attempt/calls/000000/native-request.json').exists())

    def test_valid_customer_stop_is_forwarded_byte_for_byte_by_native_adapter(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = root / 'native_fixture.py'
            script.write_text('''import json,sys
start=json.loads(input())
print(json.dumps({'type':'model_request','id':'customer-0','role':'customer','body':{'messages':[
 {'role':'system','content':'<instructions>席の種類を教えてください。</instructions>'},
 {'role':'user','content':'窓側と通路側です。'}]}}),flush=True)
reply=json.loads(input())['response']['choices'][0]['message']
print(json.dumps({'type':'result','protocol':start['protocol'],'case_id':start['payload']['case_id'],
 'answer':reply['content'],'coverage':{'all_model_calls_via_rpc':True}}),flush=True)
''', encoding='utf-8')
            generation = {'family': 'babel-vitabench', 'policy': {'vita_termination': stop.VERSION}, 'helpers': {
                'customer': {'policy': {'termination_contract': stop.contract()}}, stop.ROLE: {'policy': stop.policy()}}}
            executor = {'command': [sys.executable, str(script)], 'cwd': str(root),
                        'environment': {'PYTHONUTF8': '1'}, 'lock_files': [{'path': str(script), 'sha256': file_sha(script)}]}
            roles = {r: {'model': {'api_model': 'fixture', 'api_key_env': None, 'context_tokens': 8192},
                         'request_parameters': {'max_tokens': 1024}} for r in ('target', 'customer', stop.ROLE)}
            message = {'role': 'assistant', 'content': '回答を確認しました。###STOP###'}
            async def fake_chat(model, body, folder, *args):
                folder.mkdir(parents=True)
                return {'message': copy.deepcopy(message), 'measurement': {'finish_reason': 'stop', 'raw_usage': None}}
            async def allowed(*args, **kwargs): return {'allowed': True, 'action': 'forward-unchanged'}
            with patch('beaconbench.adapters.native.chat', fake_chat), patch('beaconbench.vita_termination.review', allowed):
                result = asyncio.run(execute(executor, {'case_id': 'fixture', 'generation': generation}, roles,
                    root / 'attempt', lambda: False, lambda _: None, request_seed=42))
            self.assertEqual(result['answer'], message['content'])
            self.assertTrue(result['customer_termination_checks'][0]['allowed'])

    def test_audit_verifier_sees_same_tools_and_scope_not_a_customer_only_excerpt(self):
        material = {'reference': self.reference, 'messages': self.messages}
        decision = {'verdict': 'consistent', 'explanation': '正しい注文の了承。', 'findings': [], 'checks': [
            {'condition_index': 0, 'verdict': 'preserved', 'explanation': '条件保持。', 'evidence': [
                {'message_index': 0, 'message_quote': '青いマグを2個注文してください。'}]}]}
        supplied = []
        async def fake_chat(model, body, *args):
            supplied.append(json.loads(body['messages'][1]['content']))
            value = {'check': decision['checks'][0], 'finding': None} if len(supplied) == 1 else self.verified
            return {'message': {'content': json.dumps(value)}}
        with tempfile.TemporaryDirectory() as tmp, patch('beaconbench.vita_goal_audit_v2.chat', fake_chat):
            result = asyncio.run(review({'status': 'scored', 'score': 0, 'passed': False},
                {'kind': 'vita_customer', 'version': audit.VERSION}, material,
                {'contract_judge': {'model': {}, 'request_parameters': {}, 'policy': policy('vita_customer', audit.VERSION)}},
                Path(tmp) / 'audit', lambda: False, lambda _: None, 42))
        self.assertEqual(supplied[0]['messages'], supplied[1]['messages'])
        self.assertIn('tool_call_id', supplied[1]['messages'][2])
        self.assertEqual(result['score'], 0)
        self.assertFalse(result['passed'])
        self.assertEqual(result['evaluation_review']['verdict'], 'consistent')


if __name__ == '__main__': unittest.main()
