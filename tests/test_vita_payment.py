import copy
import json
import unittest

from beaconbench.util import BenchError
from beaconbench.vita_payment import PaymentGuard, STOP, VERSION, contract, validate


def receipt(identity, name, content, **args):
    return [{'role': 'assistant', 'tool_calls': [{'id': identity, 'function': {'name': name, 'arguments': json.dumps(args)}}]},
            {'role': 'tool', 'tool_call_id': identity, 'name': name, 'content': content}]


def created():
    return receipt('create', 'create_delivery_order', 'Order(order_id:TEST-1, order_type:delivery, status:unpaid, products:[])')


class VitaPayment(unittest.TestCase):
    def test_instruction_and_stop_in_one_reply_wait_for_a_real_payment(self):
        guard = PaymentGuard(); guard.observe(created())
        message = {'role': 'assistant', 'content': '今すぐ支払いを実行してください。完了ですね。 ' + STOP}
        original = copy.deepcopy(message)
        forwarded = guard.forward(message, 'customer', 'calls/1')
        self.assertNotIn(STOP, forwarded['content'])
        self.assertIn('支払いを実行', forwarded['content'])
        self.assertEqual(message, original)
        self.assertEqual(guard.report()['blocked_stops'], 1)

    def test_agent_claim_or_wrong_order_cannot_unlock_termination(self):
        guard = PaymentGuard(); guard.observe(created())
        guard.observe([{'role': 'assistant', 'content': '支払いは完了しました。 Payment successful'}])
        guard.observe(receipt('other', 'pay_delivery_order', 'Payment successful', order_id='OTHER'))
        value = guard.forward({'content': STOP}, 'customer', 'calls/1')
        self.assertNotIn(STOP, value['content'])
        self.assertEqual(guard.orders['TEST-1']['status'], 'unpaid')

    def test_real_payment_receipt_allows_completion_and_preserves_raw_reply(self):
        guard = PaymentGuard(); guard.observe(created())
        guard.observe(receipt('pay', 'pay_delivery_order', '"Payment successful"', order_id='TEST-1'))
        message = {'role': 'assistant', 'content': '支払い完了を確認しました。 ' + STOP}
        self.assertEqual(guard.forward(message, 'customer', 'calls/2'), message)
        self.assertEqual(guard.orders['TEST-1']['status'], 'paid')

    def test_status_query_can_confirm_payment_but_notes_cannot(self):
        guard = PaymentGuard(); guard.observe(created())
        guard.observe(receipt('detail', 'get_delivery_order_detail', 'Order(order_id:TEST-1, order_type:delivery, note:status:paid, status:unpaid)', order_id='TEST-1'))
        self.assertEqual(guard.orders['TEST-1']['status'], 'unpaid')
        guard.observe(receipt('status', 'get_delivery_order_status', 'Order TEST-1 status: paid', order_id='TEST-1'))
        self.assertEqual(guard.orders['TEST-1']['status'], 'paid')

    def test_failed_payment_does_not_masquerade_as_paid(self):
        guard = PaymentGuard(); guard.observe(created())
        guard.observe(receipt('pay', 'pay_delivery_order', 'Payment failed: unavailable', order_id='TEST-1'))
        self.assertNotIn(STOP, guard.forward({'content': '完了です。 ' + STOP}, 'customer', 'calls/2')['content'])
        incomplete = {'content': '処理エラーのため支払いは未完了です。 ' + STOP}
        self.assertEqual(guard.forward(incomplete, 'customer', 'calls/3'), incomplete)
        self.assertTrue(guard.decisions[-1]['verified_failure_acknowledged'])
        self.assertEqual(guard.orders['TEST-1']['status'], 'unpaid')

    def test_all_active_orders_need_payment_and_cancelled_orders_are_not_debt(self):
        guard = PaymentGuard(); guard.observe(created())
        guard.observe(receipt('create2', 'create_delivery_order', 'Order(order_id:TEST-2, order_type:delivery, status:unpaid)'))
        guard.observe(receipt('pay', 'pay_delivery_order', 'Payment successful', order_id='TEST-1'))
        self.assertNotIn(STOP, guard.forward({'content': STOP}, 'customer', 'calls/2')['content'])
        guard.observe(receipt('cancel', 'cancel_delivery_order', 'Order TEST-2 has been cancelled.', order_id='TEST-2'))
        self.assertIn(STOP, guard.forward({'content': STOP}, 'customer', 'calls/3')['content'])

    def test_replayed_history_is_idempotent_but_changed_receipts_are_rejected(self):
        guard = PaymentGuard(); history = created(); guard.observe(history); guard.observe(history)
        self.assertEqual(len(guard.receipts), 1)
        altered = copy.deepcopy(history); altered[-1]['content'] = 'Payment successful'
        with self.assertRaises(BenchError): guard.observe(altered)

    def test_customer_sees_verified_state_while_target_input_and_output_are_unchanged(self):
        guard = PaymentGuard(); guard.observe(created())
        body = {'messages': [{'role': 'system', 'content': '<instructions>固定要望</instructions>'}]}
        self.assertIs(guard.decorate(body, 'target'), body)
        changed = guard.decorate(body, 'customer')
        self.assertIn('検証済み', changed['messages'][0]['content'])
        self.assertIn('TEST-1', changed['messages'][0]['content'])
        self.assertNotIn('検証済み', body['messages'][0]['content'])
        message = {'content': STOP}
        self.assertIs(guard.forward(message, 'target', 'calls/1'), message)

    def test_guard_is_opt_in_and_bound_to_the_accepted_cross_customer(self):
        self.assertFalse(validate({'family': 'babel-vitabench'}))
        generation = {'family': 'babel-vitabench', 'domain': 'cross', 'policy': {'vita_payment_completion': VERSION},
                      'helpers': {'customer': {'policy': {'payment_completion_guard': contract()}}}}
        self.assertTrue(validate(generation))
        generation['domain'] = 'instore'
        with self.assertRaises(BenchError): validate(generation)


if __name__ == '__main__': unittest.main()
