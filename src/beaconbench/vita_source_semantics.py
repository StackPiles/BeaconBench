"""Pinned evaluator-only source facts and arithmetic from native tool receipts."""
from __future__ import annotations

import copy
import json
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

from . import vita_goals
from .util import BenchError, digest, file_sha, read_json

VERSION = 'vita-source-semantics-v1'


def contract():
    return {'version': VERSION, 'implementation_sha256': file_sha(Path(__file__))}


def source_facts(spec, reference):
    """Read a separately locked task record, never supplied to target/customer."""
    if not isinstance(spec, dict) or set(spec) != {'path', 'sha256', 'task_sha256'}:
        raise BenchError('公式意味情報の固定資産指定が不正です。')
    path = Path(spec['path'])
    if file_sha(path) != spec['sha256']:
        raise BenchError('公式意味情報の資産SHAが異なります。')
    asset = read_json(path)
    if set(asset) != {'task', 'source', 'budget_scopes'} or digest(asset['task']) != spec['task_sha256']:
        raise BenchError('公式意味情報の原問題SHAが異なります。')
    task, origin = asset['task'], asset['source']
    if (not isinstance(origin, dict) or not origin.get('repository') or not origin.get('revision')
            or not origin.get('path') or not re.fullmatch('[0-9a-f]{64}', origin.get('file_sha256', ''))):
        raise BenchError('公式意味情報に固定版の出典がありません。')
    if task.get('instructions') not in reference:
        raise BenchError('公式意味情報と顧客に実際に渡した指示が異なります。')
    criteria = task['evaluation_criteria']
    rubrics, orders = [], []
    for state in criteria.get('expected_states', []):
        rubrics.extend(state.get('state_rubrics', []))
        orders.extend(state.get('required_orders', []))
    rubrics = list(dict.fromkeys([*rubrics, *criteria.get('overall_rubrics', [])]))
    unique = {digest(row): copy.deepcopy(row) for row in orders}
    conditions = vita_goals.conditions(reference)
    scopes = copy.deepcopy(asset['budget_scopes'])
    for row in scopes:
        if (set(row) != {'condition_quote', 'rubric_quote', 'order_types', 'people'}
                or row['condition_quote'] not in {c['quote'] for c in conditions}
                or row['rubric_quote'] not in rubrics or type(row['people']) is not int or row['people'] <= 0
                or not re.search(r'(?<!\d)' + str(row['people']) + r'\s*(?:人|名|people|persons)', task['instructions'], re.I)
                or not isinstance(row['order_types'], list) or not row['order_types']
                or len(set(row['order_types'])) != len(row['order_types'])
                or set(row['order_types']) - {'instore', 'hotel', 'delivery'}):
            raise BenchError('予算の対象範囲・人数を公式の原文へ対応付けできません。')
        row['condition_index'] = next(c['index'] for c in conditions if c['quote'] == row['condition_quote'])
    return {'version': VERSION, 'task_id': task['id'], 'source': origin, 'task_sha256': spec['task_sha256'],
            'native_rubrics': rubrics, 'reference_orders': list(unique.values()), 'budget_scopes': scopes,
            'reference_products': [copy.deepcopy(product) for shop in task.get('environment', {}).get('shops', {}).values()
                for product in shop.get('products', []) if any(product.get('product_id') == item.get('product_id')
                    for order in unique.values() for item in order.get('products', []))]}


def amount(value):
    if isinstance(value, bool):
        raise BenchError('金額が数値ではありません。')
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise BenchError('金額が数値ではありません。') from None
    if not number.is_finite() or number < 0:
        raise BenchError('金額が有限の非負数ではありません。')
    return number


def text_amount(value):
    return format(value.normalize(), 'f')


def ledger(messages, scopes):
    """Count actual native receipts; never trust totals in assistant prose."""
    calls, orders, issues = {}, {}, []
    for index, message in enumerate(messages):
        for call in message.get('tool_calls') or []:
            if call.get('id') in calls:
                issues.append({'message_index': index, 'reason': 'duplicate tool call id'})
            calls[call.get('id')] = call.get('function', {})
        if message.get('role') != 'tool':
            continue
        call = calls.get(message.get('tool_call_id'), {})
        name, content = call.get('name', message.get('name', '')), message.get('content') or ''
        if name in {'create_instore_product_order', 'create_hotel_order', 'create_delivery_order'}:
            match = re.fullmatch(r'Order\(order_id:([^,]+), order_type:(instore|hotel|delivery),.*?total_price:([^,]+),.*', content, re.S)
            if not match or match[1] in orders:
                issues.append({'message_index': index, 'reason': 'unrecognized or duplicate native order receipt'})
                continue
            try:
                price = text_amount(amount(match[3]))
            except BenchError:
                issues.append({'message_index': index, 'reason': 'invalid native order amount'})
                continue
            orders[match[1]] = {'order_id': match[1], 'type': match[2], 'total': price,
                                  'message_index': index, 'paid': False}
        elif name in {'pay_instore_order', 'pay_hotel_order', 'pay_delivery_order'}:
            try:
                args = call.get('arguments', {})
                args = json.loads(args) if isinstance(args, str) else args
                identity = args['order_id']
            except (ValueError, TypeError, KeyError):
                issues.append({'message_index': index, 'reason': 'unrecognized native payment arguments'})
                continue
            if identity not in orders or content != 'Payment successful':
                issues.append({'message_index': index, 'reason': 'payment receipt not reconciled'})
            else:
                orders[identity]['paid'] = True
                orders[identity]['payment_message_index'] = index
        elif any(word in name.lower() for word in ('cancel', 'refund', 'modify', 'update_order')):
            # Unsupported mutations must not silently leave stale totals.
            issues.append({'message_index': index, 'reason': 'order mutation requires reconciliation'})
    budgets = []
    for index, scope in enumerate(scopes):
        included = [row for row in orders.values() if row['type'] in scope['order_types']]
        total = sum((amount(row['total']) for row in included), Decimal(0))
        per_person = (total / scope['people']).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
        budgets.append({'scope_index': index, 'condition_index': scope['condition_index'],
            'order_ids': [row['order_id'] for row in included], 'people': scope['people'],
            'total': text_amount(total), 'per_person': text_amount(per_person),
            'receipt_message_indices': [row['message_index'] for row in included]})
    return {'orders': list(orders.values()), 'issues': issues, 'budget_totals': budgets,
            'arithmetic_only': True, 'qualitative_tolerance': 'native rubric; no added numeric pass threshold'}
