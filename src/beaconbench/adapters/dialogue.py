"""Japanese SalesLLM-derived dialogue with a private, fixed customer instrument."""
from __future__ import annotations

import json
import time

from ..transport import chat
from ..util import BenchError, append_json, write_once


CUSTOMER_JSON_POLICY = '''顧客としてのみ応答してください。営業担当の目標に協力する役へ移らず、指定された顧客情報と購入条件を守ってください。
出力はJSONオブジェクトのみで、reply（顧客の発言）、conversation_ended（会話が終了した場合だけtrue）、end_reason（継続なら空文字列）を含めてください。
礼儀正しい相づちだけで購入成立にせず、顧客の条件と発言に基づいて終了を判断してください。'''


def customer_reply(text):
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        raise BenchError('顧客役が構造化した終了判定を返しませんでした。invalid_instrumentとして原本を確認してください。') from None
    if not isinstance(value, dict) or set(value) != {'reply', 'conversation_ended', 'end_reason'} or not isinstance(value['reply'], str) or type(value['conversation_ended']) is not bool or not isinstance(value['end_reason'], str):
        raise BenchError('顧客役の終了判定形式が不正です。')
    if not value['reply'].strip() or value['conversation_ended'] and not value['end_reason'].strip():
        raise BenchError('顧客役の発言または終了根拠が不足しています。')
    return value


async def generate(generation, roles, folder, stopped, progress, seed):
    script = generation['script']
    if script['language'] != 'ja':
        raise BenchError('この営業派生adapterは日本語用です。')
    # The salesperson never sees the private customer system prompt. The
    # customer never sees the private product system prompt directly.
    sales_messages = [{'role': 'system', 'content': script['assistant_system_prompt']},
                      {'role': 'user', 'content': script['trigger_sentence']}]
    customer_messages = [{'role': 'system', 'content': script['customer_system_prompt'] + '\n\n' + CUSTOMER_JSON_POLICY},
                         {'role': 'assistant', 'content': script['trigger_sentence']}]
    transcript = [{'role': 'user', 'content': script['trigger_sentence']}]
    append_json(folder / 'dialogue.jsonl', transcript[0])
    native_limit = generation.get('native_end_conditions', {}).get('generated_turn_limit')
    index = 0
    termination = None
    started = time.monotonic()
    while native_limit is None or index < native_limit:
        role = 'target' if index % 2 == 0 else 'customer'
        binding = roles[role]
        messages = sales_messages if role == 'target' else customer_messages
        body = {**binding['request_parameters'], 'messages': messages, 'seed': seed}
        if role == 'customer':
            body['response_format'] = {'type': 'json_object'}
        response = await chat(binding['model'], body, folder / 'calls' / f'{index:06d}', role, stopped, progress)
        index += 1
        if response['message'].get('tool_calls'):
            raise BenchError('営業対話で想定外のツール応答が返りました。原本を確認してください。')
        content = response['message']['content']
        if role == 'target':
            sales_messages.append({'role': 'assistant', 'content': content})
            customer_messages.append({'role': 'user', 'content': content})
            transcript.append({'role': 'assistant', 'content': content})
        else:
            decision = customer_reply(content)
            text = decision['reply']
            transcript.append({'role': 'user', 'content': text})
            customer_messages.append({'role': 'assistant', 'content': text})
            sales_messages.append({'role': 'user', 'content': text})
            append_json(folder / 'customer-decisions.jsonl', decision)
            if decision['conversation_ended']:
                termination = 'customer_ended'
        append_json(folder / 'dialogue.jsonl', {'role': transcript[-1]['role'], 'content': transcript[-1]['content']})
        if termination:
            break
    if termination is None:
        termination = 'benchmark_turn_limit'
    result = {'family': 'salesllm-ja', 'messages': transcript, 'generated_messages': index,
              'termination': termination, 'native_end_conditions': generation.get('native_end_conditions', {}),
              'task_wall_seconds': time.monotonic() - started,
              'instrument_policy': generation['policy'], 'adaptation': 'Japanese structured customer end signal; not the official EN/ZH metric'}
    write_once(folder / 'native-result.json', result)
    return {'answer': '\n\n'.join(('顧客' if row['role'] == 'user' else '営業') + ': ' + row['content'] for row in transcript),
            'task_wall_seconds': result['task_wall_seconds'], 'termination': termination}
