"""Authored source controls: swapped IDs, combined costs, immutable stop views."""
import asyncio
import copy
import json
import tempfile
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from beaconbench import vita_source_semantics as facts
from beaconbench import vita_termination_v2 as stop
from beaconbench.run_stops import presentation
from beaconbench.adapters.native import execute
from beaconbench.termination_reviews import latest
from beaconbench.util import BenchError, digest, file_sha, write_once


def fixture(root, *, extra=False, alternate=False):
    package_id = 'PACKAGE-OTHER' if alternate else 'PACKAGE'
    instruction = '夕食と活動を含むセットを4人分注文してください。宿泊込みで1人100元前後です。'
    task = {'id': 'authored-2' if alternate else 'authored-1', 'instructions': instruction,
        'environment': {'shops': {'s': {'products': [{'product_id': package_id, 'name': '食事付きセット', 'tags': ['食事付き', '活動'], 'product_type': 'target'}]}}},
        'evaluation_criteria': {'expected_states': [{'required_orders': [
            {'order_type': 'instore', 'total_price': 240, 'products': [{'product_id': package_id, 'quantity': 1}]},
            {'order_type': 'hotel', 'total_price': 160, 'products': []}],
            'state_rubrics': ['セットは夕食と活動を含む。', 'セットと宿泊の合計は1人100元前後。']}], 'overall_rubrics': []}}
    asset = {'task': task, 'source': {'repository': 'authored-controls', 'revision': 'v1', 'path': 'own.json', 'file_sha256': 'a' * 64},
        'budget_scopes': [{'condition_quote': '宿泊込みで1人100元前後です。', 'rubric_quote': 'セットと宿泊の合計は1人100元前後。',
                          'order_types': ['instore', 'hotel'], 'people': 4}]}
    path = root / ('source-other.json' if alternate else 'source.json')
    write_once(path, asset)
    context = {'path': str(path), 'sha256': file_sha(path), 'task_sha256': digest(task)}
    messages = [{'role': 'user', 'content': instruction}]
    basket = [('P', 'instore', 240, 'create_instore_product_order'), ('H', 'hotel', 160, 'create_hotel_order')]
    if extra:
        basket.append(('X', 'instore', 120, 'create_instore_product_order'))
    for order_id, kind, price, name in basket:
        messages.extend([{'role': 'assistant', 'content': None, 'tool_calls': [{'id': order_id, 'function': {'name': name, 'arguments': '{}'}}]},
            {'role': 'tool', 'tool_call_id': order_id, 'name': name,
             'content': f'Order(order_id:{order_id}, order_type:{kind}, total_price:{price}, products:[{package_id}])'}])
    messages += [{'role': 'assistant', 'content': '手配しました。'}, {'role': 'user', 'content': 'ありがとうございます。###STOP###'}]
    source = stop.material('<instructions>' + instruction + '</instructions>', messages, context)
    costs = copy.deepcopy(source['cost_ledger']['budget_totals'])
    budgets = [{k: v for k, v in row.items() if k != 'receipt_message_indices'} for row in costs]
    # State is judged qualitatively; arithmetic and cost scope are independently checked.
    for row in budgets: row['state'] = 'unmet' if extra else 'completed'
    candidate = {'verdict': 'premature' if extra else 'completed', 'explanation': '宿泊を含む実金額で比較。', 'technical_evidence': [],
        'checks': [{'condition_index': i, 'state': 'unmet' if extra and i == 1 else 'completed',
                    'explanation': '原条件と実注文を確認。', 'disclosure': [0], 'evidence': [2, 4]} for i in range(2)],
        'budget_checks': budgets}
    return context, source, candidate


class SourceSemantics(unittest.TestCase):
    def test_omission_control_does_not_bundle_the_missing_product_in_a_room(self):
        controls = json.loads((Path(__file__).parents[1] / 'fixtures/vita-source-semantics-controls.json').read_text(encoding='utf-8'))['cases']
        missing = next(row for row in controls if row['id'] == 'missing-package')
        products = {p['product_id'] for state in missing['asset']['task']['evaluation_criteria']['expected_states']
                    for order in state['required_orders'] if order['order_type'] == 'instore' for p in order['products']}
        for message in missing['messages']:
            if message['role'] == 'tool':
                self.assertTrue(all(product not in message['content'] for product in products))

    def test_combined_costs_include_hotel_and_alternative_product_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for alternate in (False, True):
                _, source, value = fixture(root, alternate=alternate)
                self.assertEqual(source['cost_ledger']['budget_totals'][0]['per_person'], '100')
                self.assertEqual(stop.parse(json.dumps(value), source)['verdict'], 'completed')
                self.assertIn('食事付き', source['source_facts']['reference_products'][0]['tags'])

    def test_omitting_hotel_or_additional_order_cannot_validate_a_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, source, value = fixture(Path(tmp), extra=True)
            self.assertEqual(source['cost_ledger']['budget_totals'][0]['per_person'], '130')
            self.assertEqual(stop.parse(json.dumps(value), source)['verdict'], 'premature')
            for replacement in ({'total': '360', 'per_person': '90', 'order_ids': ['P', 'X']}, {'people': 5}, {'per_person': '100'}, {'order_ids': ['P', 'H', 'X', 'X']}):
                bad = copy.deepcopy(value); bad['budget_checks'][0].update(replacement)
                with self.assertRaises(BenchError): stop.parse(json.dumps(bad), source)

    def test_source_lock_and_actual_instructions_are_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            context, source, _ = fixture(Path(tmp))
            with self.assertRaises(BenchError): stop.material('<instructions>別の依頼です。</instructions>', source['messages'], context)
            path = Path(context['path']);path.write_text('{}', encoding='utf-8')
            with self.assertRaises(BenchError): stop.material(source['reference'], source['messages'], context)

    def test_unreconciled_mutation_does_not_keep_a_completed_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, source, value = fixture(Path(tmp))
            source['messages'].insert(-1, {'role': 'tool', 'name': 'cancel_order', 'content': 'cancelled'})
            source['cost_ledger'] = facts.ledger(source['messages'], source['source_facts']['budget_scopes'])
            self.assertTrue(source['cost_ledger']['issues'])
            with self.assertRaises(BenchError): stop.parse(json.dumps(value), source)

    def test_negative_stop_also_gets_independent_verification(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp);_, source, value = fixture(root, extra=True)
            calls = []
            async def fake(model, parameters, folder, role, stopped, progress):
                calls.append(parameters['messages'])
                reply = value if len(calls) == 1 else {'verdict': 'supported', 'explanation': '宿泊込みで予算を超過。', 'evidence': [2, 4, 6]}
                return {'message': {'content': json.dumps(reply)}}
            with patch('beaconbench.vita_termination_v2.chat', fake):
                result = asyncio.run(stop.review(source, {'model': {}, 'request_parameters': {}}, root / 'review', lambda: False, lambda _: None, 42))
            self.assertEqual(len(calls), 2)
            self.assertTrue(result['confirmed']);self.assertFalse(result['allowed'])
            self.assertEqual(json.loads(calls[1][1]['content'])['cost_ledger'], source['cost_ledger'])

    def test_legacy_stop_presentation_preserves_status_and_composite_cause(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp);child = root / 'runs/child';parent = root / 'runs/parent'
            write_once(child / 'status.json', {'phase': 'infrastructure_error', 'message': '顧客の終了を確認できません。'})
            (child / 'events.jsonl').write_text(json.dumps({'kind': 'run_error', 'error_type': 'InvalidCustomerTermination'}) + '\n', encoding='utf-8')
            store = SimpleNamespace(root=root, run_path=lambda identity: root / 'runs' / identity)
            state = {'phase': 'infrastructure_error', 'message': '一般的な親メッセージ', 'measurement_run_id': 'child'}
            original_sha = file_sha(child / 'status.json')
            result = presentation(store, 'parent', state)
            self.assertEqual(result['phase'], 'infrastructure_error')
            self.assertEqual(result['display_phase'], 'instrument_review_required')
            self.assertEqual(result['stop_reason']['message'], '顧客の終了を確認できません。')
            self.assertFalse(result['stop_reason']['can_resume'])
            self.assertEqual(file_sha(child / 'status.json'), original_sha)
            (child / 'events.jsonl').write_text(json.dumps({'kind': 'run_error', 'error_type': 'TransportFailure'}) + '\n', encoding='utf-8')
            self.assertNotIn('display_phase', presentation(store, 'parent', state))

    def test_source_context_never_enters_target_or_customer_messages(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp);context, source, _ = fixture(root)
            script = root / 'rpc.py'
            target_messages = [{'role': 'system', 'content': '通常の対象モデル指示。'}, {'role': 'user', 'content': '相談です。'}]
            script.write_text('import json,sys\nstart=json.loads(input())\n'
                + 'print(json.dumps({"type":"model_request","id":"target","role":"target","body":{"messages":' + repr(target_messages) + '}}),flush=True)\ninput()\n'
                + 'print(json.dumps({"type":"model_request","id":"customer","role":"customer","body":{"messages":[{"role":"system","content":' + repr(source['reference']) + '},{"role":"user","content":"手配しました。"}]}}),flush=True)\n'
                + 'input()\n', encoding='utf-8')
            generation = {'family': 'babel-vitabench', 'policy': {'vita_termination': stop.VERSION, 'vita_source_context': context},
                'helpers': {'customer': {'policy': {'termination_contract': stop.contract()}}, stop.ROLE: {'policy': stop.policy()}}}
            roles = {name: {'model': {'api_key_env': None, 'api_model': 'fixture', 'context_tokens': 4096}, 'request_parameters': {'max_tokens': 256}}
                     for name in ('target', 'customer', stop.ROLE)}
            calls = []
            async def fake(model, parameters, folder, role, stopped, progress):
                calls.append(role)
                if role == 'target': self.assertEqual(parameters['messages'], target_messages)
                else:
                    text = json.dumps(parameters['messages'], ensure_ascii=False)
                    self.assertNotIn('reference_orders', text);self.assertNotIn('PACKAGE', text)
                    self.assertIn(stop.REMINDER, parameters['messages'][0]['content'])
                return {'message': {'content': '以上です。###STOP###' if role == 'customer' else '手配しました。'},
                        'measurement': {'finish_reason': 'stop', 'raw_usage': {}}}
            async def rejected(*args, **kwargs):
                return {'allowed': False, 'action': 'invalid-instrument-stop'}
            executor = {'command': [sys.executable, str(script)], 'lock_files': [{'path': str(script), 'sha256': file_sha(script)}]}
            with patch('beaconbench.adapters.native.chat', fake), patch('beaconbench.vita_termination_v2.review', rejected):
                with self.assertRaises(stop.InvalidCustomerTermination):
                    asyncio.run(execute(executor, {'case_id': 'authored', 'generation': generation}, roles,
                                        root / 'attempt', lambda: False, lambda _: None, request_seed=42))
            self.assertEqual(calls, ['target', 'customer'])

    def test_review_view_verifies_the_original_and_new_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp);_, source, candidate = fixture(root)
            origin = root / 'runs/r/trials/a/b/0/attempts/0/calls/0/termination/result.json'
            result = root / 'termination-reviews/r/tr-new/review/result.json'
            write_once(origin, {'candidate': {'verdict': 'unknown'}})
            write_once(result, {'candidate': candidate})
            write_once(result.parent / 'input.json', source)
            record = {'id': 'tr-new', 'created_at': '2026-01-01', 'source_files': {origin.relative_to(root).as_posix(): file_sha(origin)},
                      'result_ref': result.relative_to(root).as_posix(), 'result_sha256': file_sha(result)}
            write_once(result.parents[1] / 'summary.json', record)
            store = SimpleNamespace(root=root)
            checked = latest(store, 'r')
            self.assertEqual(checked['original_verdict'], 'unknown')
            self.assertEqual(checked['condition_checks'][1]['condition_quote'], '宿泊込みで1人100元前後です。')
            original = origin.read_bytes();origin.write_text('{}', encoding='utf-8')
            with self.assertRaises(BenchError): latest(store, 'r')
            origin.write_bytes(original)


if __name__ == '__main__': unittest.main()
