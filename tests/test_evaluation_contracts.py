import asyncio
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from beaconbench.evaluation_contracts import (ROLE, VERSION, VITA_REMINDER, annotate, material_from_saved,
                                            indexed_material, parse_decision, policy, remind_customer, review, revised_pack, validate_case)
from beaconbench.util import BenchError, digest
from beaconbench.adapters.grades import normalize
from beaconbench.saved_reviews import reweight_sales


class EvaluationContracts(unittest.TestCase):
    def setUp(self):
        self.material = {'reference': '要望を変更しない。ツインルームが必要。', 'messages': [
            {'role': 'assistant', 'content': '4人部屋に変更しますか。'}, {'role': 'user', 'content': 'ツインを放棄して4人部屋でいいです。'}]}
        self.violation = {'verdict': 'violation', 'explanation': '顧客が固定条件を放棄した。', 'findings': [
            {'rule_quote': 'ツインルームが必要', 'message_index': 1, 'message_quote': 'ツインを放棄', 'explanation': '固定の部屋条件と矛盾。'}]}

    def test_evidence_must_be_exact_and_belong_to_the_audited_actor(self):
        self.assertEqual(parse_decision(json.dumps(self.violation), self.material, 'vita_customer'), self.violation)
        for key, value in [('rule_quote', '存在しない指示'), ('message_quote', '存在しない発言'), ('message_index', 0), ('message_index', True), ('message_index', 99)]:
            decision = copy.deepcopy(self.violation); decision['findings'][0][key] = value
            with self.assertRaises(BenchError):
                parse_decision(json.dumps(decision), self.material, 'vita_customer')
        with self.assertRaises(BenchError):
            parse_decision(json.dumps(self.violation), self.material, 'sales_target')

    def test_indexed_input_does_not_require_the_judge_to_count_tool_messages(self):
        before = copy.deepcopy(self.material)
        material = {**self.material, 'messages': [self.material['messages'][0], {'role': 'tool', 'content': '操作結果'}, self.material['messages'][1]]}
        indexed = indexed_material(material)
        self.assertEqual([row['message_index'] for row in indexed['messages']], [0, 1, 2])
        self.assertEqual(self.material, before)
        decision = copy.deepcopy(self.violation); decision['findings'][0]['message_index'] = 2
        self.assertEqual(parse_decision(json.dumps(decision), indexed, 'vita_customer'), decision)

    def test_unknown_or_broken_judge_is_not_an_invented_model_failure(self):
        value = {'verdict': 'unknown', 'explanation': '固定条件が不足している。', 'findings': []}
        self.assertEqual(parse_decision(json.dumps(value), self.material, 'vita_customer'), value)
        for verdict in [[], {}, True, None, 'other']:
            bad = {**value, 'verdict': verdict}
            with self.assertRaises(BenchError):
                parse_decision(json.dumps(bad), self.material, 'vita_customer')
        with self.assertRaises(BenchError):
            parse_decision(json.dumps({**self.violation, 'findings': []}), self.material, 'vita_customer')

    def test_source_score_is_preserved_and_target_violations_are_not_excluded(self):
        original = {'status': 'scored', 'score': 9, 'metric_unit': 'salesllm_score_0_10'}
        result = annotate(original, {'kind': 'sales_target'}, self.violation)
        self.assertEqual(original, {'status': 'scored', 'score': 9, 'metric_unit': 'salesllm_score_0_10'})
        self.assertEqual(result['score'], 9)
        self.assertTrue(result['comparison_eligible'])
        for verdict in ['violation', 'unknown']:
            result = annotate(original, {'kind': 'vita_customer'}, {**self.violation, 'verdict': verdict})
            self.assertFalse(result['comparison_eligible'])
            self.assertEqual(result['score'], 9)

    def test_reweighting_preserves_original_native_final_score_and_defaults(self):
        native = {'id': 'fixture', 'instrument_valid': True, 'conversation_quality': 8, 'A/B': 'A', 'final_score': 9}
        old = normalize('salesllm-ja', native, 'fixture')
        before = copy.deepcopy(old)
        new = reweight_sales(old, {'native_id': 'fixture', 'proportion': .6, 'score_profile': 'readme-ja-derived'})
        self.assertEqual(old, before)
        self.assertEqual(new['score'], 9.2)
        self.assertEqual(new['evidence']['native']['final_score'], 9)
        self.assertEqual(new['components']['proportion'], .6)
        self.assertEqual(new['source_score'], {'score': 9, 'proportion': .5})

    def test_vita_reminder_never_reaches_target_or_changes_goals(self):
        generation = {'family': 'babel-vitabench', 'policy': {'vita_customer_contract': VERSION}}
        body = {'messages': [{'role': 'system', 'content': '<instructions>条件はツイン</instructions>'}, {'role': 'user', 'content': '会話'}], 'seed': 42}
        original = copy.deepcopy(body)
        self.assertIs(remind_customer(body, generation, 'target'), body)
        updated = remind_customer(body, generation, 'customer')
        self.assertEqual(body, original)
        self.assertEqual(updated['messages'][1], original['messages'][1])
        self.assertTrue(updated['messages'][0]['content'].startswith(original['messages'][0]['content']))
        self.assertIn(VITA_REMINDER, updated['messages'][0]['content'])
        with self.assertRaises(BenchError):
            remind_customer({'messages': []}, generation, 'customer')

    def test_reference_review_cannot_change_source_pin_or_gold(self):
        source = {'record_sha256': 'a' * 64}
        spec = {'version': VERSION, 'kind': 'reference', 'source_record_sha256': 'b' * 64,
                'state': 'questioned', 'reason': '参照に疑義', 'evidence': ['固定規則と参照答えが不整合'], 'source_url': 'https://example.org/source'}
        case = {'source': source, 'generation': {'adapter': 'native', 'family': 'crmarena-pro-sales-ja'}, 'grader': {'evaluation_review': spec}}
        with self.assertRaises(BenchError):
            validate_case(case)
        case['source']['record_sha256'] = 'b' * 64
        validate_case(case)
        with tempfile.TemporaryDirectory() as tmp:
            native = {'status': 'scored', 'score': 0, 'passed': False, 'evidence': {'native': {'parsed_answer': ['A', 'B']}}}
            result = asyncio.run(review(native, spec, {'source_record_sha256': 'b' * 64}, {}, Path(tmp) / 'audit', lambda: False, lambda _: None, 42))
            self.assertEqual(result['evidence'], native['evidence'])
            self.assertEqual(result['score'], 0)
            self.assertFalse(result['comparison_eligible'])

    def test_bad_citation_becomes_unknown_with_retained_native_result(self):
        async def fake_chat(*args):
            bad = copy.deepcopy(self.violation); bad['findings'][0]['rule_quote'] = '幻の引用'
            return {'message': {'content': json.dumps(bad)}}
        binding = {ROLE: {'policy': policy('vita_customer'), 'request_parameters': {}, 'model': {}}}
        with tempfile.TemporaryDirectory() as tmp, patch('beaconbench.evaluation_contracts.chat', fake_chat):
            result = asyncio.run(review({'status': 'scored', 'score': 0}, {'version': VERSION, 'kind': 'vita_customer'},
                                        self.material, binding, Path(tmp) / 'audit', lambda: False, lambda _: None, 42))
            self.assertEqual(result['evaluation_review']['verdict'], 'unknown')
            self.assertFalse(result['comparison_eligible'])
            self.assertEqual(result['score'], 0)

    def test_missing_reference_and_transcript_are_guarded_without_judge_calls(self):
        for kind in ('vita_customer', 'sales_target'):
            for material in ({'reference': '', 'messages': [{'role': 'assistant', 'content': '回答'}]}, {'reference': '固定指示', 'messages': []}):
                binding = {ROLE: {'policy': policy(kind)}}
                with tempfile.TemporaryDirectory() as tmp, patch('beaconbench.evaluation_contracts.chat', side_effect=AssertionError('No judge call on missing input')):
                    result = asyncio.run(review({'status': 'scored', 'score': 0}, {'version': VERSION, 'kind': kind}, material,
                                                binding, Path(tmp) / 'audit', lambda: False, lambda _: None, 42))
                    self.assertEqual(result['evaluation_review']['verdict'], 'unknown')

    def test_new_pack_preserves_old_conditions_and_explicitly_versions_the_changes(self):
        pack = {'id': 'old', 'name': 'Authored control', 'cases': [
            {'id': 'sales', 'generation': {'adapter': 'dialogue', 'script': {'assistant_system_prompt': '元の仕様'}}, 'grader': {'kind': 'salesllm_ja', 'proportion': .5}},
            {'id': 'vita', 'generation': {'adapter': 'native', 'family': 'babel-vitabench', 'policy': {}, 'helpers': {'customer': {'old': True}}}, 'grader': {'kind': 'command'}}]}
        before = copy.deepcopy(pack)
        helpers = {kind: {'model_id': 'fixture', 'policy': policy(kind)} for kind in ['vita_customer', 'sales_target']}
        customer = {'policy': {'reminder_sha256': digest(VITA_REMINDER)}}
        new = revised_pack(pack, 'new', helpers, {}, customer=customer)
        self.assertEqual(pack, before)
        self.assertEqual([c['id'] for c in new['cases']], ['sales', 'vita'])
        self.assertEqual(new['cases'][0]['generation'], before['cases'][0]['generation'])
        self.assertEqual(new['cases'][0]['grader']['proportion'], .6)
        self.assertEqual(new['cases'][1]['generation']['policy']['vita_customer_contract'], VERSION)
        with self.assertRaises(BenchError):
            revised_pack(pack, 'old', helpers, {}, customer=customer)

    def test_material_uses_customer_prompt_only_in_post_generation_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'calls/0'; folder.mkdir(parents=True)
            (folder / 'measurement.json').write_text('{"role":"customer"}', encoding='utf-8')
            (folder / 'request.json').write_text(json.dumps({'body': {'messages': [{'role': 'system', 'content': 'PRIVATE_FIXED_GOALS'}]}}), encoding='utf-8')
            case = {'grader': {'evaluation_review': {'kind': 'vita_customer'}}}
            material = material_from_saved(case, {'grade_record': {'messages': [{'role': 'user', 'content': '発言', 'reasoning_content': '思考'}]}}, Path(tmp))
            self.assertEqual(material['reference'], 'PRIVATE_FIXED_GOALS')
            self.assertNotIn('reasoning_content', material['messages'][0])

    def test_sales_audit_cannot_add_instructions_the_target_did_not_receive(self):
        case = {'grader': {'evaluation_review': {'kind': 'sales_target'}},
                'generation': {'script': {'assistant_system_prompt': '架空の商品の営業担当として対話する。'}}}
        native = {'messages': [{'role': 'assistant', 'content': '来週のご相談を提案します。'}]}
        material = material_from_saved(case, native, Path('unused'))
        self.assertEqual(material['reference'], case['generation']['script']['assistant_system_prompt'])


if __name__ == '__main__':
    unittest.main()
