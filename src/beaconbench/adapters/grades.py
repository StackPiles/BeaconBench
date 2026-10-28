"""Strict mappings from pinned upstream records; missing results never become zero."""
from __future__ import annotations

import json
import math
import sys

from ..util import BenchError


FAMILIES = {'babel-tau3', 'babel-vitabench', 'babel-deepplanning-shopping',
            'babel-deepplanning-travel', 'babel-claw', 'salesllm-ja',
            'crmarena-pro-sales-ja', 'swebench-pro-v2-ja', 'scicode-verified', 'frontier-algorithmic'}
INTENT_SCORES = {'A': 10, 'B': 8, 'C': 6, 'X': 4, 'F': 2}


def number(value, low=None, high=None):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise BenchError('採点器の得点が有限数ではありません。')
    if low is not None and value < low or high is not None and value > high:
        raise BenchError('採点器の得点が公式尺度の範囲外です。')
    return value


def full(value):
    return math.isclose(number(value, 0, 1), 1.0, rel_tol=0, abs_tol=1e-9)


def identity(record, field, expected):
    if field not in record or str(record[field]) != str(expected):
        raise BenchError('採点結果と固定した元の問題IDが一致しません。')


def normalize(family: str, native: dict, expected_id: str, *, proportion=.5) -> dict:
    if family not in FAMILIES or not isinstance(native, dict):
        raise BenchError('未対応の公式判定形式です。')
    base = {'status': 'scored', 'native_family': family, 'evidence': {'native': native}}
    if native.get('infrastructure_error') or native.get('termination_reason') == 'infrastructure_error':
        return {**base, 'status': 'infrastructure_error', 'message': '上流が実行環境の失敗を報告しました。'}
    if family in {'babel-tau3', 'babel-vitabench'}:
        identity(native, 'task_id', expected_id)
        info = native['reward_info']
        if 'evaluation_error' in (info.get('info') or {}):
            return {**base, 'status': 'invalid_instrument', 'message': '上流の評価器が失敗しました。'}
        score = number(info['reward'], 0, 1)
        return {**base, 'score': score, 'passed': full(score), 'metric': 'mean_reward', 'metric_unit': 'native_reward_0_1'}
    if family == 'babel-deepplanning-shopping':
        # The selection uses a numeric task ID; the native evaluator names its
        # database directory case_<ID>. Do not accept arbitrary suffix matches.
        identity(native, 'case_name', 'case_' + str(expected_id) if str(expected_id).isdigit() else expected_id)
        score = number(native['case_score'], 0, 1)
        return {**base, 'score': score, 'passed': full(score), 'metric': 'case_score', 'metric_unit': 'shopping_case_score_0_1'}
    if family == 'babel-deepplanning-travel':
        identity(native, 'sample_id', expected_id)
        if native.get('conversion_valid') is False or native.get('error_type') == 'conversion_error':
            return {**base, 'status': 'invalid_instrument', 'message': '旅程変換の欠落・不成立を検出しました。'}
        if type(native.get('success')) is not bool:
            raise BenchError('旅程評価の実行結果がありません。')
        scores = native.get('scores')
        if not isinstance(scores, dict) or 'case_acc' not in scores:
            raise BenchError('旅程評価のcase_accがありません。失敗を0で補完しません。')
        score = number(scores['case_acc'], 0, 1)
        return {**base, 'score': score, 'passed': native['success'] and full(score),
                'metric': 'case_acc', 'metric_unit': 'travel_case_acc_0_1'}
    if family == 'babel-claw':
        identity(native, 'task_id', expected_id)
        if type(native.get('passed')) is not bool:
            raise BenchError('Clawのnative passedがありません。文字列をboolへ変換しません。')
        score = number(native['task_score'], 0, 1)
        return {**base, 'score': score, 'passed': native['passed'], 'metric': 'task_score', 'metric_unit': 'claw_task_score_0_1'}
    if family == 'salesllm-ja':
        identity(native, 'id', expected_id)
        if native.get('instrument_valid') is not True:
            return {**base, 'status': 'invalid_instrument', 'message': '日本語の顧客役・採点役の妥当性が未確認です。'}
        quality = number(native['conversation_quality'], 0, 10)
        if type(quality) is not int:
            raise BenchError('SalesLLMの会話品質は0から10の整数で指定してください。')
        label = native['A/B']
        if label not in INTENT_SCORES:
            raise BenchError('購買意向が公式のA/B/C/X/Fラベルではありません。')
        weight = number(proportion, 0, 1)
        score = (1 - weight) * quality + weight * INTENT_SCORES[label]
        if 'final_score' in native and not math.isclose(number(native['final_score']), score, rel_tol=0, abs_tol=1e-8):
            raise BenchError('SalesLLMの重み付き得点式と判定値が一致しません。')
        # No invented pass threshold: the upstream metric is continuous.
        return {**base, 'score': score, 'metric': 'weighted_sales_score', 'metric_unit': 'salesllm_score_0_10',
                'components': {'conversation_quality': quality, 'intent_label': label, 'intent_score': INTENT_SCORES[label], 'proportion': weight}}
    if family == 'crmarena-pro-sales-ja':
        identity(native, 'idx', expected_id)
        metric = native['reward_metric']
        if metric not in {'exact_match', 'fuzzy_match'}:
            raise BenchError('営業系の対象外のCRM採点方式です。')
        score = number(native['reward'], 0, 1)
        return {**base, 'score': score, 'passed': full(score), 'metric': metric, 'metric_unit': 'crm_reward_0_1'}
    if family == 'swebench-pro-v2-ja':
        identity(native, 'instance_id', expected_id)
        if native.get('fresh_image_regrade') is not True:
            return {**base, 'status': 'invalid_instrument', 'message': '清浄イメージでの再採点証拠がありません。'}
        if type(native.get('resolved')) is not bool:
            raise BenchError('SWE-benchのresolved判定がありません。')
        return {**base, 'passed': native['resolved'], 'metric': 'resolved', 'metric_unit': 'binary'}
    if family == 'scicode-verified':
        identity(native, 'step_id', expected_id)
        eras = native['per_environment']
        if set(eras) != {'2024', '2025'} or not all(type(row.get('passed')) is bool for row in eras.values()):
            raise BenchError('SciCode-Verifiedの固定2環境の採点が不足しています。')
        if any(row.get('infrastructure_error') for row in eras.values()):
            return {**base, 'status': 'infrastructure_error', 'message': '科学採点の一方の環境が成立していません。'}
        return {**base, 'passed': any(row['passed'] for row in eras.values()), 'metric': 'two_era_or', 'metric_unit': 'binary'}
    identity(native, 'pid', expected_id)
    if native.get('all_tests_evaluated') is not True:
        return {**base, 'status': 'infrastructure_error', 'message': 'Frontierの全テストの採点が完了していません。'}
    score = number(native['score'])
    if not isinstance(native.get('score_formula_sha256'), str) or len(native['score_formula_sha256']) != 64 or any(c not in '0123456789abcdef' for c in native['score_formula_sha256']):
        raise BenchError('Frontierの問題固有の得点式が固定されていません。')
    return {**base, 'score': score, 'metric': 'native_problem_score', 'metric_unit': 'frontier_native_points',
            'feasible': native.get('feasible')}


def main():
    payload = json.load(sys.stdin)
    spec = payload['grader']
    try:
        result = normalize(spec['family'], payload['native_result']['grade_record'], spec['native_id'], proportion=spec.get('proportion', .5))
    except (BenchError, KeyError, TypeError, ValueError) as error:
        result = {'status': 'invalid_instrument', 'message': str(error) if isinstance(error, BenchError) else 'native判定の必須項目が不足しています。'}
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))


if __name__ == '__main__':
    main()
