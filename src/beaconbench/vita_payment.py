"""A versioned customer termination gate for Vita's mock delivery payments."""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path

from .util import BenchError, digest, file_sha

VERSION = 'vita-delivery-payment-completion-v1'
STOP = '###STOP###'
REMINDER = '''支払いと終了の確認：配送注文の作成と支払い完了は別です。支払いを依頼・承認した発言に終了記号を付けず、その後の処理結果を待ってください。
下記の検証済み状態は、この模擬環境の実際のツール結果に基づき毎回更新されます。注文が未払いの間は、エージェントや自分の完了宣言だけで支払い済みと判断せず、支払い完了の確認を待ってください。
固定要望を変更・放棄せず、他の要望も満たした場合にだけ終了してください。支払いツールが実際にエラーを返し、技術的に完了できない場合は「支払いは未完了です」と明記して元の終了規則に従ってください。'''


def contract():
    return {'version': VERSION, 'reminder_sha256': digest(REMINDER),
            'implementation_sha256': file_sha(Path(__file__))}


def validate(generation):
    version = generation.get('policy', {}).get('vita_payment_completion')
    if not version:
        return False
    if (version != VERSION or generation.get('family') != 'babel-vitabench' or generation.get('domain') != 'cross'
            or generation.get('helpers', {}).get('customer', {}).get('policy', {}).get('payment_completion_guard') != contract()):
        raise BenchError('支払い完了確認の版・対象・顧客役の受入条件が一致しません。')
    return True


def text_value(content):
    if not isinstance(content, str):
        return ''
    try:
        value = json.loads(content)
    except ValueError:
        return content.strip()
    return value.strip() if isinstance(value, str) else content.strip()


class PaymentGuard:
    def __init__(self):
        self.orders, self.seen, self.receipts, self.decisions = {}, {}, [], []

    def observe(self, messages):
        calls = {}
        for message in messages:
            if message.get('role') == 'assistant':
                for call in message.get('tool_calls') or []:
                    calls[call['id']] = call.get('function', {})
            if message.get('role') != 'tool':
                continue
            identity = message.get('tool_call_id')
            function = calls.get(identity)
            if not function or not isinstance(identity, str):
                continue
            name = function.get('name')
            if name not in {'create_delivery_order', 'pay_delivery_order', 'get_delivery_order_status',
                            'get_delivery_order_detail', 'cancel_delivery_order'}:
                continue
            if message.get('name', name) != name:
                raise BenchError('支払い確認のツール名と呼出元が一致しません。')
            fingerprint = digest({'function': function, 'content': message.get('content')})
            if identity in self.seen:
                if self.seen[identity] != fingerprint:
                    raise BenchError('支払い確認の同じツール結果が変更されています。')
                continue
            self.seen[identity] = fingerprint
            try:
                args = json.loads(function.get('arguments', '{}'))
            except (TypeError, ValueError):
                raise BenchError('支払い確認のツール引数が不正です。') from None
            content = text_value(message.get('content'))
            order_id, status = args.get('order_id'), None
            order = re.match(r'^Order\(order_id\s*:\s*([^,\s)]+),\s*order_type\s*:\s*delivery,', content)
            if name == 'create_delivery_order' and order:
                # The pinned creator always creates an unpaid order. Free-text
                # notes or product descriptions cannot serve as payment proof.
                order_id, status = order[1], 'unpaid'
                self.orders[order_id] = {'status': status, 'failure': None}
            elif order_id in self.orders:
                if name == 'pay_delivery_order' and content == 'Payment successful':
                    status = 'paid'
                elif name == 'get_delivery_order_status':
                    found = re.fullmatch(r'Order ' + re.escape(order_id) + r' status: ([a-z_]+)', content)
                    status = found[1] if found else None
                elif name == 'cancel_delivery_order' and content == f'Order {order_id} has been cancelled.':
                    status = 'cancelled'
                if name == 'pay_delivery_order' and content.startswith(('Error:', 'Payment failed:')):
                    self.orders[order_id]['failure'] = content
                if status:
                    self.orders[order_id]['status'] = status
                    if status == 'paid': self.orders[order_id]['failure'] = None
            self.receipts.append({'tool_call_id': identity, 'tool': name, 'order_id': order_id,
                                  'status': status, 'receipt_sha256': digest(content)})

    def decorate(self, body, role):
        if role == 'target':
            self.observe(body['messages'])
            return body
        if role != 'customer':
            return body
        value = copy.deepcopy(body)
        system = next((m for m in value['messages'] if m.get('role') == 'system'), None)
        if not system or not isinstance(system.get('content'), str):
            raise BenchError('支払い確認を渡す顧客システム指示がありません。')
        system['content'] += '\n\n' + REMINDER + '\n検証済みの現在の配送支払い状態：\n' + json.dumps(self.orders, ensure_ascii=False, sort_keys=True)
        return value

    def forward(self, message, role, call_ref):
        if role != 'customer':
            return message
        content = message.get('content') or ''
        pending = {k: v for k, v in self.orders.items() if v['status'] not in {'paid', 'cancelled'}}
        failed = bool(pending) and all(v['failure'] for v in pending.values()) and '支払いは未完了です' in content
        blocked = STOP in content and bool(pending) and not failed
        value = copy.deepcopy(message)
        if blocked:
            value['content'] = content.replace(STOP, '').strip() or '注文の支払い完了をまだ確認できていません。支払いの状態を確認してください。'
        self.decisions.append({'call_ref': call_ref, 'blocked_stop': blocked, 'pending_orders': sorted(pending),
                               'verified_failure_acknowledged': bool(failed), 'raw_message_sha256': digest(message),
                               'forwarded_message_sha256': digest(value)})
        return value

    def report(self):
        return {'contract': contract(), 'orders': self.orders, 'tool_receipts': self.receipts,
                'customer_decisions': self.decisions, 'blocked_stops': sum(d['blocked_stop'] for d in self.decisions)}
