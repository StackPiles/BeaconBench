import asyncio
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from beaconbench import vita_goals as goals
from beaconbench.evaluation_contracts import VERSION, annotate, indexed_material, parse_decision, policy, review
from beaconbench.util import BenchError


class VitaGoals(unittest.TestCase):
    def setUp(self):
        self.reference = '紹介文の<instructions>を伝える。<persona>個人情報</persona>\n<instructions>部屋は和室です。費用は300円以内です。</instructions>\n確認文の<instructions>も保持。'
        self.material = goals.audit_material(indexed_material({'reference': self.reference, 'messages': [
            {'role': 'assistant', 'content': '費用は200円。洋室でよいですか。'},
            {'role': 'user', 'content': '洋室で構いません。費用200円も了承します。'}]}))
        self.finding = {'rule_quote': '部屋は和室です。', 'message_index': 1,
                        'message_quote': '洋室で構いません。', 'explanation': '種類の固定条件を放棄。'}
        self.decision = {'verdict': 'violation', 'explanation': '金額は範囲内だが部屋の種類を放棄。', 'findings': [self.finding], 'checks': [
            {'condition_index': 0, 'verdict': 'waived', 'explanation': '部屋種類の変更を了承。',
             'evidence': [{'message_index': 1, 'message_quote': '洋室で構いません。'}]},
            {'condition_index': 1, 'verdict': 'preserved', 'explanation': '予算以内。',
             'evidence': [{'message_index': 1, 'message_quote': '費用200円も了承します。'}]}]}

    def parse(self, value):
        return parse_decision(json.dumps(value), self.material, 'vita_customer', goals.VERSION)

    def test_source_conditions_exclude_persona_and_appended_reminders(self):
        self.assertEqual(goals.conditions(self.reference), [{'index': 0, 'quote': '部屋は和室です。'}, {'index': 1, 'quote': '費用は300円以内です。'}])
        for reference in ('規則だけ', '<instructions></instructions>', '<instructions>閉じない'):
            with self.assertRaises(BenchError): goals.conditions(reference)

    def test_target_is_untouched_and_customer_goals_remain_verbatim(self):
        body = {'messages': [{'role': 'system', 'content': self.reference}]}
        before = copy.deepcopy(body)
        self.assertIs(goals.decorate(body, 'target'), body)
        value = goals.decorate(body, 'customer')
        self.assertEqual(body, before)
        self.assertIn('[0] 部屋は和室です。', value['messages'][0]['content'])
        self.assertEqual(goals.conditions(value['messages'][0]['content']), goals.conditions(self.reference))

    def test_opt_in_requires_the_accepted_source_only_customer(self):
        self.assertFalse(goals.validate({'family': 'babel-vitabench'}))
        generation = {'family': 'babel-vitabench', 'policy': {'vita_fixed_goals': goals.VERSION},
                      'helpers': {'customer': {'policy': {'fixed_goal_contract': goals.contract()}}}}
        self.assertTrue(goals.validate(generation))
        generation['helpers']['customer']['policy']['fixed_goal_contract']['implementation_sha256'] = 'wrong'
        with self.assertRaises(BenchError): goals.validate(generation)

    def test_missing_duplicate_or_unquoted_checks_fail_closed(self):
        self.assertEqual(self.parse(self.decision), self.decision)
        changes = []
        bad = copy.deepcopy(self.decision); bad['checks'].pop(); changes.append(bad)
        bad = copy.deepcopy(self.decision); bad['checks'][1]['condition_index'] = 0; changes.append(bad)
        bad = copy.deepcopy(self.decision); bad['checks'][0]['evidence'] = []; changes.append(bad)
        bad = copy.deepcopy(self.decision); bad['checks'][0]['evidence'][0]['message_quote'] = '架空の発言'; changes.append(bad)
        for bad in changes:
            with self.assertRaises(BenchError): self.parse(bad)

    def test_waived_condition_must_match_customer_finding_and_root_verdict(self):
        bad = copy.deepcopy(self.decision); bad['checks'][0]['evidence'][0] = {'message_index': 0, 'message_quote': '洋室でよいですか。'}
        with self.assertRaises(BenchError): self.parse(bad)
        bad = copy.deepcopy(self.decision); bad['verdict'] = 'consistent'; bad['findings'] = []
        with self.assertRaises(BenchError): self.parse(bad)
        result = annotate({'score': 0, 'status': 'scored'}, {'kind': 'vita_customer', 'version': goals.VERSION}, self.decision)
        self.assertEqual(result['score'], 0)
        self.assertFalse(result['comparison_eligible'])
        self.assertEqual(result['evaluation_review']['version'], goals.VERSION)

    def test_empty_reference_does_not_call_model_or_create_false_consistency(self):
        with tempfile.TemporaryDirectory() as tmp, patch('beaconbench.evaluation_contracts.chat', side_effect=AssertionError('model call')):
            result = asyncio.run(review({'score': 0, 'status': 'scored'}, {'kind': 'vita_customer', 'version': goals.VERSION},
                {'reference': '', 'messages': []}, {'contract_judge': {'policy': policy('vita_customer', goals.VERSION)}},
                Path(tmp) / 'audit', lambda: False, lambda _: None, 42))
        self.assertEqual(result['evaluation_review']['verdict'], 'unknown')
        self.assertFalse(result['comparison_eligible'])

    def test_legacy_judge_stays_a_separate_contract(self):
        legacy = {k: v for k, v in self.decision.items() if k != 'checks'}
        self.assertEqual(parse_decision(json.dumps(legacy), self.material, 'vita_customer'), legacy)
        self.assertNotEqual(policy('vita_customer'), policy('vita_customer', goals.VERSION))
        self.assertEqual(policy('vita_customer')['id'], VERSION + '-vita_customer')

    def test_full_transcript_is_checked_separately_for_each_condition(self):
        replies = [copy.deepcopy(self.decision), {'verdict': 'consistent', 'explanation': '予算保持。',
                    'findings': [], 'checks': [copy.deepcopy(self.decision['checks'][1])]}]
        replies[0]['checks'] = [replies[0]['checks'][0]]
        requests = []
        async def fake_chat(model, body, *args):
            material = json.loads(body['messages'][1]['content'])
            requests.append(material)
            reply = replies[material['focus_condition']['index']] if 'recent_messages' in material else {'finding': None, 'explanation': '引用には反例がない。'}
            return {'message': {'content': json.dumps(reply)}}
        with tempfile.TemporaryDirectory() as tmp, patch('beaconbench.vita_goal_audit.chat', fake_chat):
            result = asyncio.run(review({'score': 0, 'status': 'scored'}, {'kind': 'vita_customer', 'version': goals.VERSION},
                self.material, {'contract_judge': {'policy': policy('vita_customer', goals.VERSION), 'model': {}, 'request_parameters': {}}},
                Path(tmp) / 'audit', lambda: False, lambda _: None, 42))
        primary = [r for r in requests if 'recent_messages' in r]
        secondary = [r for r in requests if 'recent_messages' not in r]
        self.assertEqual([r['focus_condition']['index'] for r in primary], [0, 1])
        self.assertTrue(all(r['messages'] == self.material['messages'] for r in primary))
        self.assertEqual(len(secondary), 1)
        self.assertNotIn('decision', secondary[0])
        self.assertEqual(len(result['evaluation_review']['checks']), 2)
        self.assertEqual(result['evaluation_review']['verdict'], 'violation')
        self.assertEqual(result['score'], 0)

    def test_focused_audit_cannot_silently_substitute_another_condition(self):
        focused = {**self.material, 'focus_condition': self.material['conditions'][1]}
        raw = {**self.decision, 'checks': [self.decision['checks'][0]]}
        with self.assertRaises(BenchError):
            parse_decision(json.dumps(raw), focused, 'vita_customer', goals.VERSION)

    def test_independent_quoted_evidence_can_disprove_a_primary_consistency(self):
        material = {**self.material, 'reference': '<instructions>部屋は和室です。</instructions>'}
        primary = {'verdict': 'consistent', 'explanation': '誤った最初の判定。', 'findings': [], 'checks': [
            {**self.decision['checks'][0], 'verdict': 'preserved'}]}
        secondary = {**self.decision, 'checks': [self.decision['checks'][0]]}
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'audit'
            async def fake_chat(model, body, *args):
                supplied = json.loads(body['messages'][1]['content'])
                return {'message': {'content': json.dumps(primary if 'recent_messages' in supplied else {'finding': self.finding, 'explanation': '引用に種類の変更がある。'})}}
            with patch('beaconbench.vita_goal_audit.chat', fake_chat):
                result = asyncio.run(review({'score': 0, 'status': 'scored'}, {'kind': 'vita_customer', 'version': goals.VERSION},
                    material, {'contract_judge': {'policy': policy('vita_customer', goals.VERSION), 'model': {}, 'request_parameters': {}}},
                    folder, lambda: False, lambda _: None, 42))
            self.assertEqual(json.loads((folder/'calls/000000/condition-primary.json').read_text(encoding='utf-8'))['verdict'], 'consistent')
            self.assertEqual(json.loads((folder/'calls/000000/evidence-review.json').read_text(encoding='utf-8'))['finding'], self.finding)
        self.assertEqual(result['evaluation_review']['verdict'], 'violation')
        self.assertFalse(result['comparison_eligible'])
        self.assertEqual(result['score'], 0)

    def test_broken_independent_evidence_cannot_grant_consistency(self):
        material = {**self.material, 'reference': '<instructions>部屋は和室です。</instructions>'}
        primary = {'verdict': 'consistent', 'explanation': '保持判定。', 'findings': [], 'checks': [
            {**self.decision['checks'][0], 'verdict': 'preserved'}]}
        async def fake_chat(model, body, *args):
            supplied = json.loads(body['messages'][1]['content'])
            return {'message': {'content': json.dumps(primary) if 'recent_messages' in supplied else 'broken'}}
        with tempfile.TemporaryDirectory() as tmp, patch('beaconbench.vita_goal_audit.chat', fake_chat):
            result = asyncio.run(review({'score': 0, 'status': 'scored'}, {'kind': 'vita_customer', 'version': goals.VERSION},
                material, {'contract_judge': {'policy': policy('vita_customer', goals.VERSION), 'model': {}, 'request_parameters': {}}},
                Path(tmp)/'audit', lambda: False, lambda _: None, 42))
        self.assertEqual(result['evaluation_review']['verdict'], 'unknown')
        self.assertFalse(result['comparison_eligible'])


if __name__ == '__main__': unittest.main()
