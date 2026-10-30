"""Source-aligned stop review; legacy v1 artifacts and behavior stay intact."""
from __future__ import annotations

import json
from pathlib import Path

from . import vita_source_semantics as facts
from . import vita_termination as legacy
from .transport import chat
from .util import BenchError, digest, file_sha, write_once

VERSION = 'vita-source-termination-v2'
ROLE = legacy.ROLE
REMINDER = legacy.REMINDER
InvalidCustomerTermination = legacy.InvalidCustomerTermination
decorate = legacy.decorate
has_stop = legacy.has_stop
PROMPT = legacy.PROMPT + '''
source_factsは固定された公式データから取り出した、判定役だけが使う参照情報です。正解例は唯一の許容手順や商品を指定するものではなく、同じ条件を満たす代替も認めます。参照商品が元の要望を満たすものとして公式例に使われている場合、その商品の一般的な表記だけを根拠に不足としません。例えば食事付きのセットが夕食条件を持つ公式例に使われるなら、夕食という文字の省略だけでは未達・不明としません。注文なし、人数不足、別商品への置換は個別に検査します。
予算の対象範囲はsource_facts.budget_scopesの原文と公式rubricに従います。cost_ledgerは実ツール結果から計算した全対象注文です。宿泊込みの予算を活動費だけと比較したり、別枠の賞品を合算したりしません。エージェントや顧客の合計・完了宣言を再計算の代わりに使いません。元の「前後」に独自の合格幅を作らず、公式例とrubricを踏まえて判断します。範囲外の了承は元の条件を変更しません。
出力にはbudget_checksも追加してください。各cost_ledger.budget_totalsに1要素、キーはscope_index、condition_index、order_ids、people、total、per_person、stateだけです。金額は台帳どおりの文字列、stateは対応する条件のstateと同じです。issuesがある場合はその予算をunknownとし、推測で完了にしません。'''
VERIFY = legacy.VERIFY + '''
source_factsの公式の意味とcost_ledgerの実金額も全文と照合します。公式例に含まれるセットを文字の省略だけで不足としないでください。宿泊込み予算の分母・分子・全注文の範囲を確認し、活動部分の単価だけで充足としないでください。premature/unknownという候補も誤判定の可能性を検証します。'''


def contract():
    return {'version': VERSION, 'implementation_sha256': file_sha(Path(__file__)),
            'source_semantics': facts.contract(), 'legacy_validation': legacy.contract(),
            'prompt_sha256': digest({'primary': PROMPT, 'verification': VERIFY}),
            'on_invalid_stop': 'stop-with-instrument-reason; no target feedback or resampling'}


def policy():
    return {'id': VERSION + '-judge', 'contract': contract(), 'control_acceptance': {'minimum_macro_f1': 1.0}}


def validate(generation):
    version = generation.get('policy', {}).get('vita_termination')
    if version != VERSION:
        raise BenchError('公式意味情報を使う終了検査の版が異なります。')
    helpers = generation.get('helpers', {})
    if (generation.get('family') != 'babel-vitabench'
            or generation.get('policy', {}).get('vita_payment_completion')
            or generation.get('policy', {}).get('vita_fixed_goals')
            or helpers.get('customer', {}).get('policy', {}).get('termination_contract') != contract()
            or helpers.get(ROLE, {}).get('policy') != policy()
            or not generation.get('policy', {}).get('vita_source_context')):
        raise BenchError('終了検査v2の固定資産・補助役が一致しません。')
    return True


def material(reference, messages, context):
    source = legacy.material(reference, messages)
    source['source_facts'] = facts.source_facts(context, reference)
    source['cost_ledger'] = facts.ledger(source['messages'], source['source_facts']['budget_scopes'])
    return source


def parse(raw, source):
    try:
        value = json.loads(raw)
        budgets = value.pop('budget_checks')
    except (ValueError, KeyError, TypeError, AttributeError):
        raise BenchError('終了検査v2の予算検査がありません。') from None
    value = legacy.parse(json.dumps(value, ensure_ascii=False), source)
    expected = source['cost_ledger']['budget_totals']
    if not isinstance(budgets, list) or len(budgets) != len(expected):
        raise BenchError('全対象予算の検査がありません。')
    seen = set()
    for row in budgets:
        if not isinstance(row, dict) or set(row) != {'scope_index', 'condition_index', 'order_ids', 'people', 'total', 'per_person', 'state'}:
            raise BenchError('予算検査の形式が不正です。')
        i = row['scope_index']
        if type(i) is not int or not 0 <= i < len(expected) or i in seen:
            raise BenchError('予算検査の対象が重複または不明です。')
        seen.add(i)
        target = expected[i]
        if (type(row['condition_index']) is not int or row['condition_index'] != target['condition_index']
                or type(row['people']) is not int or row['people'] != target['people']
                or not isinstance(row['order_ids'], list) or any(not isinstance(x, str) for x in row['order_ids'])
                or sorted(row['order_ids']) != sorted(target['order_ids'])
                or facts.amount(row['total']) != facts.amount(target['total'])
                or facts.amount(row['per_person']) != facts.amount(target['per_person'])):
            raise BenchError('予算の全注文・宿泊・人数・実金額が台帳と一致しません。')
        condition = next(c for c in value['checks'] if c['condition_index'] == target['condition_index'])
        if row['state'] != condition['state'] or source['cost_ledger']['issues'] and row['state'] != 'unknown':
            raise BenchError('予算の状態が条件検査または台帳の不明状態と一致しません。')
    return {**value, 'budget_checks': budgets}


async def review(source, binding, folder, stopped, progress, seed, *, call_prefix=None):
    write_once(folder / 'input.json', source)

    async def request(prompt, payload, name):
        dest = folder / name if call_prefix is None else call_prefix.with_name(call_prefix.name + '-' + name)
        response = await chat(binding['model'], {**binding['request_parameters'], 'seed': seed,
            'response_format': {'type': 'json_object'}, 'messages': [
                {'role': 'system', 'content': prompt}, {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)}]},
            dest, ROLE, stopped, progress)
        return response['message']['content']

    try:
        candidate = parse(await request(PROMPT, source, 'primary'), source)
    except BenchError as error:
        candidate = {'verdict': 'unknown', 'explanation': str(error), 'checks': [], 'technical_evidence': [], 'budget_checks': []}
    write_once(folder / 'candidate.json', candidate)
    try:
        verified = json.loads(await request(VERIFY, {**source, 'candidate': candidate}, 'verification'))
        if (not isinstance(verified, dict) or set(verified) != {'verdict', 'explanation', 'evidence'}
                or verified['verdict'] not in {'supported', 'contradicted', 'insufficient'}
                or not isinstance(verified['explanation'], str) or not verified['explanation'].strip()):
            raise BenchError('終了検査v2の全文確認が不正です。')
        verified['evidence'] = legacy.resolve_evidence(verified['evidence'], source)
        legacy.validate_evidence(verified['evidence'], source, required=verified['verdict'] != 'insufficient')
    except (BenchError, ValueError, TypeError, KeyError) as error:
        verified = {'verdict': 'insufficient', 'explanation': str(error), 'evidence': []}
    write_once(folder / 'verification.json', verified)
    confirmed = verified['verdict'] == 'supported'
    allowed = confirmed and candidate['verdict'] in {'completed', 'technical_failure'}
    result = {'version': VERSION, 'allowed': allowed, 'candidate': candidate, 'verification': verified,
              'confirmed': confirmed, 'action': 'forward-unchanged' if allowed else 'invalid-instrument-stop',
              'contract': contract(), 'source_sha256': digest(source)}
    write_once(folder / 'result.json', result)
    return result


class Guard(legacy.Guard):
    def __init__(self, context):
        super().__init__()
        self.context = context

    async def check(self, message, role, binding, folder, stopped, progress, seed):
        if role == 'target':
            self.messages.append(__import__('copy').deepcopy(message))
        if role != 'customer' or not has_stop(message):
            return
        source = material(self.reference, [*self.messages, {**message, 'role': 'user'}], self.context)
        result = await review(source, binding, folder, stopped, progress, seed,
                              call_prefix=folder.parent.with_name(folder.parent.name + '-stop'))
        self.reports.append(result)
        if not result['allowed']:
            raise InvalidCustomerTermination('模擬顧客の終了条件に未達または不明点があり、検査の確認が必要です。原回答・原判定を保持して停止しました。')
