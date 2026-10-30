"""Meaningful isolation and evidence boundaries for multi-turn helper QA."""
import copy
import json
import unittest
from pathlib import Path

from beaconbench import customer_acceptance as qa
from beaconbench.util import BenchError


class CustomerAcceptance(unittest.TestCase):
    def setUp(self):
        self.controls = json.loads((Path(__file__).parents[1] / 'fixtures/customer-multiturn-controls.json').read_text(encoding='utf-8'))
        self.case = self.controls['cases'][0]
        self.reference = '<instructions>' + self.case['goal'] + '</instructions>情報を捏造しないこと'

    @staticmethod
    def reply(text='条件は変えません。', finish='stop'):
        return {'message': {'role': 'assistant', 'content': text, 'reasoning_content': '保存する内部思考。'},
                'measurement': {'completed': True, 'finish_reason': finish}}

    def material(self):
        return {'criteria': [{'id': 'fixed', 'source_quote': '青を2個'}], 'messages': [
            {'message_index': 0, 'role': 'assistant', 'content': '赤を1個でよいですか。'},
            {'message_index': 1, 'role': 'user', 'content': '青を2個の条件は変えません。'}]}

    def primary(self, verdict='faithful', evidence=None):
        return json.dumps({'checks': [{'criterion_id': 'fixed', 'verdict': verdict,
            'explanation': '顧客自身の条件保持を確認。', 'evidence_indices': [1] if evidence is None else evidence}]})

    def test_authored_suite_is_multiturn_and_criteria_are_source_bound(self):
        qa.validate_controls(self.controls, '情報を捏造しないこと')
        self.assertEqual(len(self.controls['cases']), 12)
        self.assertEqual(sum(len(c['steps']) for c in self.controls['cases']), 60)
        bad = copy.deepcopy(self.controls); bad['cases'][0]['criteria'][0]['source_quote'] = '正解に合わせた後付け条件'
        with self.assertRaises(BenchError): qa.validate_controls(bad)
        bad = copy.deepcopy(self.controls); bad['cases'].append(copy.deepcopy(bad['cases'][0]))
        with self.assertRaises(BenchError): qa.validate_controls(bad)
        bad = copy.deepcopy(self.controls); bad['cases'][0]['id'] = '../outside'
        with self.assertRaises(BenchError): qa.validate_controls(bad)

    def test_native_history_preserves_each_models_actual_reply_without_reasoning_injection(self):
        a = qa.next_request(self.case, self.reference, [self.reply('青い案を選びます。')])
        b = qa.next_request(self.case, self.reference, [self.reply('赤い案を断ります。')])
        self.assertEqual(a['messages'][-1], b['messages'][-1])
        self.assertNotEqual(a['messages'][-2], b['messages'][-2])
        self.assertEqual(a['seed'], b['seed'])
        self.assertEqual(a['messages'][-2], {'role': 'assistant', 'content': '青い案を選びます。', 'tool_calls': None})
        self.assertNotIn('reasoning_content', a['messages'][-2])
        self.assertEqual(a['messages'][0]['content'], self.reference)
        self.assertNotIn('criteria', json.dumps(a))
        self.assertNotEqual(qa.turn_seed(self.case['id'], 0), qa.turn_seed(self.case['id'], 1))

    def test_stop_is_retained_and_never_resampled_or_forced_to_continue(self):
        replies = [self.reply('今日はここで終わります。###STOP###')]
        with self.assertRaises(BenchError): qa.next_request(self.case, self.reference, replies)
        issues = qa.objective_findings(self.case, replies)
        self.assertEqual(issues[0]['verdict'], 'violation')
        self.assertIn('###STOP###', qa.transcript(self.case, replies)[-1]['content'])

    def test_final_stop_is_optional_but_missing_calls_are_not_complete(self):
        replies = [self.reply() for _ in self.case['steps']]
        self.assertEqual(qa.objective_findings(self.case, replies), [])
        replies[-1] = self.reply('確認しました。###STOP###')
        self.assertEqual(qa.objective_findings(self.case, replies), [])
        self.assertEqual(qa.objective_findings(self.case, replies[:-1])[0]['verdict'], 'unknown')
        replies[-1] = self.reply('途中で切れました', finish='length')
        self.assertEqual(qa.objective_findings(self.case, replies)[0]['verdict'], 'unknown')

    def test_judge_cannot_label_agent_only_evidence_as_customer_violation(self):
        with self.assertRaises(BenchError): qa.parse_primary(self.primary('violation', [0]), self.material())
        for indices in ([99], [True], [1, 1], []):
            with self.assertRaises(BenchError): qa.parse_primary(self.primary('faithful', indices), self.material())
        decision = qa.parse_primary(self.primary(), self.material())
        self.assertEqual(decision['checks'][0]['evidence'][0]['quote'], '青を2個の条件は変えません。')
        raw = json.loads(self.primary()); raw['checks'].append(copy.deepcopy(raw['checks'][0]))
        with self.assertRaises(BenchError): qa.parse_primary(json.dumps(raw), self.material())

    def test_independent_disagreement_preserves_candidate_and_yields_unknown(self):
        candidate = qa.parse_primary(self.primary(), self.material())
        raw = json.dumps({'verdict': 'contradicted', 'explanation': '判断が一致しません。', 'evidence_indices': [1]})
        result = qa.confirm(candidate, raw, self.material(), [])
        self.assertEqual(result['verdict'], 'unknown')
        self.assertEqual(result['candidate']['verdict'], 'faithful')
        raw = json.dumps({'verdict': 'supported', 'explanation': '元対話で確認。', 'evidence_indices': [1]})
        result = qa.confirm(candidate, raw, self.material(), [{'verdict': 'violation', 'reason': '早期終了'}])
        self.assertEqual(result['verdict'], 'violation')

    def test_model_identity_and_reasoning_are_absent_from_judge_material(self):
        material = qa.judge_material(self.case, self.reference, [self.reply()])
        text = json.dumps(material, ensure_ascii=False)
        self.assertNotIn('reasoning_content', text)
        self.assertNotIn('profile', text)
        self.assertNotIn('自前候補', text)
        self.assertEqual(material['messages'][1]['role'], 'user')

    def test_partial_suite_or_unknown_usage_cannot_be_reported_as_accepted_or_zero(self):
        row = {'profile': 'a', 'case_id': 'one', 'decision': {'verdict': 'faithful'},
               'usage': {'completion_tokens': None, 'request_wall_seconds': 3.0}}
        result = qa.summarize([row], ['one', 'two'])['a']
        self.assertFalse(result['accepted_on_fixed_controls'])
        self.assertIsNone(result['completion_tokens_including_reasoning'])
        self.assertIsNone(result['request_throughput_tokens_per_second'])


if __name__ == '__main__':
    unittest.main()
