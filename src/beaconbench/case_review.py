"""Read-only views of saved evidence. Never generate answers or infer missing grades."""
from __future__ import annotations

import json
from pathlib import Path

from .util import read_json


def content(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return '\n\n'.join(part.get('text', '') for part in value if isinstance(part, dict) and part.get('text'))
    return ''


def optional(path: Path):
    return read_json(path) if path.is_file() else {}


def facts(title, values):
    return {'kind': 'facts', 'title': title, 'items': [{'label': key, 'value': value} for key, value in values if value is not None]}


def checks(title, items):
    return {'kind': 'checks', 'title': title, 'items': items}


def text(title, value):
    return {'kind': 'text', 'title': title, 'text': value}


def grade_view(grade, decisions=None):
    """Project known grader schemas; unknowns stay unknown and raw evidence remains available."""
    native = grade.get('evidence', {}).get('native', {})
    sections = []
    family = grade.get('native_family', '')
    metric = grade.get('metric', '')
    if metric == 'all_tests_pass':
        results = native.get('results', [])
        sections += [text('採点ルール', '保存された全テストが成功すると合格です。'),
                     facts('テスト結果', [('成功', sum(value is True for value in results)), ('テスト数', native.get('test_count'))]),
                     checks('各テスト', [{'label': 'テスト ' + str(index + 1), 'passed': value if type(value) is bool else None,
                                          'detail': '' if type(value) is bool else '保存された結果: ' + str(value)} for index, value in enumerate(results)])]
    elif metric == 'two_era_or':
        sections += [text('採点ルール', '2024・2025のいずれかの環境でテストが成功すると合格です。'),
                     checks('環境ごとの結果', [{'label': str(key) + '環境', 'passed': None if row.get('infrastructure_error') else row.get('passed'),
                                               'detail': '実行環境のエラー' if row.get('infrastructure_error') else
                                               ('実行時間: ' + str(row['seconds']) + ' 秒' if row.get('seconds') is not None else '')}
                                              for key, row in native.get('per_environment', {}).items()])]
    elif family == 'babel-claw' or 'judge_calls' in native:
        names = {'completion': '課題の達成', 'robustness': '頑健性', 'communication': '伝達', 'safety': '安全性',
                 'efficiency_tokens': 'トークン効率', 'efficiency_turns': '対話回数の効率', 'efficiency_wall_time_s': '時間効率'}
        for index, row in enumerate(native.get('judge_calls', [])):
            sections.append({'kind': 'criterion', 'title': '評価 ' + str(index + 1),
                             'score': row.get('score'), 'text': row.get('reasoning', ''),
                             'rubric': row.get('rubric_preview', ''), 'rubric_partial': True})
        sections.append({**facts('保存された項目別スコア', [(names.get(key, key), value) for key, value in native.get('scores', {}).items()]), 'collapsible': True})
    elif 'matched_products' in native:
        lookup = {str(row.get('product_id')): row.get('name') for row in native.get('ground_truth_products', [])}
        sections += [text('採点ルール', '期待される商品との一致と余分な選択を、固定した採点器で評価します。'),
                     facts('商品の照合', [('一致した商品数', native.get('matched_count')), ('期待する商品数', native.get('expected_count')),
                                         ('余分な商品数', native.get('extra_products_count')), ('商品スコア', native.get('score'))]),
                     checks('選択内容の照合',
                            [{'label': lookup.get(str(value)) or str(value), 'passed': True, 'detail': '期待する商品と一致'} for value in native.get('matched_products', [])] +
                            [{'label': row.get('name') or str(row.get('product_id')), 'passed': False, 'detail': '期待する商品が未選択'} for row in native.get('unmatched_ground_truth_products', [])] +
                            [{'label': row.get('name') or str(row.get('product_id')), 'passed': False, 'detail': '期待する集合にない商品を選択'} for row in native.get('extra_products', [])])]
    elif family == 'babel-deepplanning-travel':
        names = {'Activity Diversity': '活動の多様性', 'Business Hours': '営業時間', 'Cost Calculation Accuracy': '費用の計算',
                 'Duration Rationality': '滞在時間の妥当性', 'Itinerary Structure': '旅程の構成', 'Route Consistency': '経路の整合性',
                 'Sandbox Compliance': '利用可能な施設・交通との一致', 'Time Feasibility': '時間の実現可能性'}
        sections += [text('採点ルール', '主指標は旅程全体の達成度 case_acc です。制約別の部分点・合成点とは区別して表示します。'),
                     facts('保存された得点', [('旅程全体の達成度', native.get('scores', {}).get('case_acc')),
                                              ('一般条件の加重点', native.get('scores', {}).get('commonsense_weighted_score')),
                                              ('個別条件の得点', native.get('scores', {}).get('personalized_score')),
                                              ('合成点', native.get('scores', {}).get('composite_score'))])]
        for name, dimension in native.get('commonsense_dimension_details', {}).items():
            sections.append(checks(names.get(name, name), [{'label': row.get('name', ''), 'passed': row.get('passed'),
                                                           'detail': row.get('message') or ''} for row in dimension.get('checks', [])]))
        constraints = native.get('personalized_dimension_score', {}).get('constraints', {})
        sections.append(checks('個別条件', [{'label': key, 'passed': value.get('passed'), 'detail': value.get('message') or ''} for key, value in constraints.items()]))
    elif 'reward_info' in native:
        reward = native['reward_info'] or {}
        sections.append(facts('保存された報酬内訳', list((reward.get('reward_breakdown') or {}).items())))
        if reward.get('nl_rubrics'):
            sections.append(checks('採点基準と判定理由', [{'label': row.get('nl_rubric', ''), 'passed': row.get('met'),
                                                         'detail': row.get('justification', '')} for row in reward['nl_rubrics']]))
        if reward.get('db_check'):
            db = reward['db_check']
            sections.append(checks('最終状態の照合', [{'label': '期待されるデータの状態と一致', 'passed': db.get('db_match'),
                                                      'detail': '状態の報酬: ' + str(db.get('db_reward', '不明'))}]))
        if reward.get('action_checks'):
            sections.append(checks('操作の照合', [{'label': row.get('action', {}).get('name', '操作'), 'passed': row.get('action_match'),
                                                  'detail': '操作の報酬: ' + str(row.get('action_reward', '不明'))} for row in reward['action_checks']]))
        sections.append(text('終了理由', str(native.get('termination_reason', '記録なし'))))
    elif 'parsed_answer' in native:
        sections += [text('採点ルール', '提出回答を抽出し、期待する回答の集合と完全一致するかを判定します。'),
                     facts('抽出された回答', [('回答', native['parsed_answer'])]),
                     text('根拠の保存範囲', 'この採点記録には正解の集合や項目別の不一致理由が含まれていません。自動採点の結果と抽出回答を表示しています。')]
    elif grade.get('components'):
        components = grade['components']
        for key, title in [('judge', '会話品質の理由'), ('intent_judge', '購入意向の理由'), ('language_judge', '日本語判定の理由')]:
            decision = (decisions or {}).get(key, {})
            if decision.get('evidence'):
                sections.append(text(title, decision['evidence']))
        language = grade.get('language_consistency', {})
        if language:
            sections.append(checks('日本語の判定', [{'label': label, 'passed': language.get(key), 'detail': ''}
                                                    for key, label in [('customer_japanese', '顧客役'), ('target_japanese', '評価対象の営業役')]]))
        sections.append(facts('得点の内訳', [('会話品質（0〜10）', components.get('conversation_quality')), ('購入意向', components.get('intent_label')),
                                             ('購入意向スコア', components.get('intent_score')), ('購入意向の重み', components.get('proportion'))]))
    elif 'all_tests_evaluated' in native:
        sections += [text('採点ルール', '公式採点器が返した最適化課題のネイティブ得点です。正答率とは別の指標です。'),
                     checks('評価の完了', [{'label': '全テストの評価完了', 'passed': native.get('all_tests_evaluated'), 'detail': ''}]),
                     text('根拠の保存範囲', '保存された採点結果には、テストごとの得点内訳は含まれていません。')]
    else:
        # Travel and future schemas must not borrow explanations from another task.
        for key in ('reasoning', 'explanation', 'feedback'):
            if isinstance(native.get(key), str):
                sections.append(text('採点コメント', native[key]))
        scalars = [(key, value) for key, value in native.items() if isinstance(value, (str, int, float, bool)) or value is None]
        if scalars:
            sections.append(facts('採点器が保存した項目', scalars))
        sections.append(text('根拠の保存範囲', 'この採点形式の詳細は「詳細情報」の採点原本でも確認できます。記録のない理由は補っていません。'))
    if grade.get('message'):
        sections.insert(0, text('採点器からのメッセージ', grade['message']))
    return {'status': grade.get('status'), 'passed': grade.get('passed'), 'score': grade.get('score'),
            'metric': metric, 'unit': grade.get('metric_unit'), 'sections': [row for row in sections if row.get('items', True)]}


def build_review(case, attempt: Path, answer: str, grades: list, trial: dict):
    """Load the first target input once, and omit repeated requests and reasoning from this view."""
    native = optional(attempt / 'native-result.json')
    generation = case.get('generation', {})
    target = []
    first_input = None
    artifacts = []
    submitted = None
    calls_root = attempt / 'calls'
    for folder in sorted(calls_root.iterdir()) if calls_root.is_dir() else []:
        measurement = optional(folder / 'measurement.json')
        if measurement.get('role') != 'target':
            continue
        message = optional(folder / 'message.json')
        if first_input is None:
            request = optional(folder / 'request.json')
            first_input = request.get('body', {}).get('messages', [])
        target.append({'role': 'assistant', 'content': content(message.get('content'))})
        for tool in message.get('tool_calls') or []:
            function = tool.get('function', {})
            name = function.get('name', '')
            args = function.get('arguments', {})
            try:
                args = json.loads(args) if isinstance(args, str) else args
            except ValueError:
                args = {'content': args}
            if name == 'respond' and isinstance(args, dict) and isinstance(args.get('content'), str):
                submitted = args['content']
            # Written artifacts are primary results; read/query operations remain in the trace.
            if not any(part in name.lower() for part in ('save_draft', 'create_event', 'update_event', 'create_document')):
                continue
            artifacts.append({'title': name, 'fields': args if isinstance(args, dict) else {'content': args}})
    inputs = first_input if first_input is not None else generation.get('messages', [])
    prompt = '\n\n'.join(content(row.get('content')) for row in inputs if row.get('role') == 'user')
    instructions = '\n\n'.join(content(row.get('content')) for row in inputs if row.get('role') in {'system', 'developer'})
    script = generation.get('script', {})
    if script.get('assistant_system_prompt'):
        prompt = script['assistant_system_prompt']
        instructions = ''
    grade_record = native.get('grade_record', {})
    conversation = native.get('messages') or grade_record.get('messages') or []
    messages = [{'role': row['role'], 'content': content(row.get('content'))} for row in conversation
                if row.get('role') in {'assistant', 'user'}]
    is_dialogue = bool(messages)
    if not is_dialogue:
        # Native result.answer can be the evaluator's JSON, so use saved target output.
        messages = [{'role': 'assistant', 'content': submitted}] if submitted is not None else (
            [target[-1]] if target else [{'role': 'assistant', 'content': answer}])
    decisions = {}
    views = []
    for grade in grades:
        decisions = grade.get('decisions', {})
        views.append({**grade_view(grade['result'], decisions), 'fingerprint': grade['fingerprint']})
    return {'question': {'text': prompt, 'instructions': instructions},
            'response': {'kind': 'conversation' if is_dialogue else 'answer', 'messages': messages, 'artifacts': artifacts},
            'grading': views, 'trial': trial,
            'source_note': '保存済みの入力・モデル出力・採点記録から表示しています。再生成・再採点は行っていません。'}
