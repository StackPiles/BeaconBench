"""Presentation fidelity and grading distinctions, with no model/executor access."""
import json
import tempfile
import unittest
from pathlib import Path

from beaconbench.case_review import build_review, grade_view


class CaseReview(unittest.TestCase):
    def test_native_answer_uses_model_text_and_written_draft_not_grader_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            def save(name, value):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
            save('calls/0/measurement.json', {'role': 'target'})
            prompt = '問題全文\n' + '長い本文' * 10000
            save('calls/0/request.json', {'body': {'messages': [{'role': 'system', 'content': '共通指示'}, {'role': 'user', 'content': prompt}]}})
            save('calls/0/message.json', {'content': 'モデルが作成した回答', 'reasoning_content': '非表示の思考',
                                        'tool_calls': [{'function': {'name': 'gmail_save_draft', 'arguments': json.dumps({'body': '草案全文', 'to': ['fixture@example.test']})}}]})
            save('calls/1/measurement.json', {'role': 'judge'})
            save('calls/1/message.json', {'content': '採点器の回答を混ぜない'})
            before = {str(p): p.read_bytes() for p in root.rglob('*') if p.is_file()}
            view = build_review({}, root, '{"type":"grading_result"}', [], {})
            self.assertEqual(view['question']['text'], prompt)
            self.assertEqual(view['question']['instructions'], '共通指示')
            self.assertEqual(view['response']['messages'][0]['content'], 'モデルが作成した回答')
            self.assertEqual(view['response']['artifacts'][0]['fields']['body'], '草案全文')
            self.assertNotIn('非表示の思考', json.dumps(view, ensure_ascii=False))
            self.assertNotIn('採点器の回答を混ぜない', json.dumps(view, ensure_ascii=False))
            self.assertEqual(before, {str(p): p.read_bytes() for p in root.rglob('*') if p.is_file()})

    def test_conversation_keeps_all_roles_and_empty_messages(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            messages = [{'role': 'user', 'content': '質問'}, {'role': 'assistant', 'content': ''},
                        {'role': 'tool', 'content': '秘密の操作結果'}, {'role': 'assistant', 'content': '最終回答'}]
            (root / 'native-result.json').write_text(json.dumps({'grade_record': {'messages': messages}}), encoding='utf-8')
            view = build_review({'generation': {'messages': [{'role': 'user', 'content': '課題'}]}}, root, '{}', [], {})
            self.assertEqual(view['response']['kind'], 'conversation')
            self.assertEqual(view['response']['messages'], [messages[0], messages[1], messages[3]])
            self.assertEqual(view['question']['text'], '課題')

    def test_environment_error_is_unknown_not_incorrect(self):
        view = grade_view({'metric': 'two_era_or', 'passed': True, 'evidence': {'native': {'per_environment': {
            '2024': {'passed': False, 'infrastructure_error': True}, '2025': {'passed': True}}}}})
        self.assertEqual([row['passed'] for row in view['sections'][1]['items']], [None, True])

    def test_shopping_execution_success_does_not_become_task_pass(self):
        view = grade_view({'passed': False, 'score': 0, 'evidence': {'native': {
            'success': True, 'matched_products': ['a'], 'ground_truth_products': [{'product_id': 'a', 'name': '商品A'}],
            'matched_count': 1, 'expected_count': 2, 'unmatched_ground_truth_products': [{'product_id': 'b', 'name': '商品B'}]}}})
        self.assertIs(view['passed'], False)
        self.assertEqual(view['score'], 0)
        self.assertEqual(view['sections'][2]['items'][0]['label'], '商品A')

    def test_sales_reasons_and_language_flag_remain_distinct_from_score(self):
        view = grade_view({'status': 'scored', 'score': 7, 'components': {'conversation_quality': 6, 'intent_score': 8, 'proportion': .5},
                           'language_consistency': {'target_japanese': False}}, {'judge': {'evidence': '保存された理由の全文'}})
        self.assertEqual(view['score'], 7)
        self.assertIsNone(view['passed'])
        self.assertIn('保存された理由の全文', str(view))
        language = next(row['items'] for row in view['sections'] if row['title'] == '日本語の判定')
        self.assertIsNone(language[0]['passed'])
        self.assertIs(language[1]['passed'], False)

    def test_crm_does_not_invent_gold_and_unknown_scores_stay_missing(self):
        crm = grade_view({'evidence': {'native': {'parsed_answer': ['a']}}})
        self.assertIn('正解の集合', str(crm))
        self.assertIsNone(crm['score'])
        self.assertIsNone(crm['passed'])
        unknown = grade_view({'metric': 'future', 'evidence': {'native': {'unknown': {'nested': 5}}}})
        self.assertIsNone(unknown['score'])
        self.assertIn('補っていません', str(unknown))

    def test_respond_tool_is_the_submitted_answer_even_with_empty_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            call = root / 'calls/0'
            call.mkdir(parents=True)
            (call / 'measurement.json').write_text('{"role":"target"}', encoding='utf-8')
            (call / 'message.json').write_text(json.dumps({'content': '', 'tool_calls': [
                {'function': {'name': 'respond', 'arguments': '{"content":"Authority, Timeline"}'}}]}), encoding='utf-8')
            view = build_review({}, root, '{"reward":0}', [], {})
            self.assertEqual(view['response']['messages'][0]['content'], 'Authority, Timeline')

    def test_travel_partial_scores_do_not_override_case_score_and_full_reason_is_retained(self):
        view = grade_view({'native_family': 'babel-deepplanning-travel', 'score': 0, 'passed': False, 'evidence': {'native': {
            'scores': {'case_acc': 0, 'composite_score': .9375}, 'commonsense_dimension_details': {'Time Feasibility': {
                'checks': [{'name': 'reasonable_transfer_time', 'passed': False, 'message': '保存された不一致理由の全文'}]}}}}})
        self.assertEqual(view['score'], 0)
        self.assertIs(view['passed'], False)
        self.assertIn('保存された不一致理由の全文', str(view))

    def test_claw_marks_source_rubric_preview_as_partial(self):
        view = grade_view({'native_family': 'babel-claw', 'evidence': {'native': {'judge_calls': [
            {'score': .9, 'reasoning': 'Full original explanation', 'rubric_preview': 'Partial rubric'}]}}})
        self.assertTrue(view['sections'][0]['rubric_partial'])
        self.assertEqual(view['sections'][0]['text'], 'Full original explanation')

    def test_customer_condition_checks_show_source_evidence_and_keep_score(self):
        view = grade_view({'score': 0, 'comparison_eligible': False, 'evaluation_review': {
            'explanation': '顧客が条件を放棄。', 'conditions': [{'index': 0, 'quote': '和室のみ。'}],
            'checks': [{'condition_index': 0, 'verdict': 'waived', 'explanation': '洋室を了承した。',
                        'evidence': [{'message_index': 5, 'message_quote': '洋室で構いません。'}]}]}})
        section = next(row for row in view['sections'] if row['title'] == '顧客の固定条件ごとの点検')
        self.assertEqual(view['score'], 0)
        self.assertFalse(view['comparison_eligible'])
        self.assertTrue(section['collapsible'])
        self.assertIn('和室のみ。', section['items'][0]['detail'])
        self.assertIn('発言 6：洋室で構いません。', section['items'][0]['detail'])


if __name__ == '__main__':
    unittest.main()
