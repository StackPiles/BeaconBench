"""Opt-in Qwen/vLLM context fitting, without removing any input history."""
from __future__ import annotations

import copy
import time
from urllib.parse import quote

from .util import BenchError, digest, write_once


POLICIES = {'qwen-vllm-context-fit-v1', 'llama-swap-qwen-vllm-context-fit-v1'}


def tokenizer_payload(body):
    messages = copy.deepcopy(body['messages'])
    reasoning_messages = 0
    for message in messages:
        # TokenizeChatRequest drops the nonstandard reasoning_content field.
        # Keep its text in the tokenizer-only representation. On the qualified
        # Qwen template this adds marker overhead, giving a conservative bound.
        if message.get('role') == 'assistant' and message.get('reasoning_content'):
            if not isinstance(message.get('content') or '', str):
                raise BenchError('この長文枠計数はテキストのQwen会話用です。')
            thought = message.pop('reasoning_content')
            message['content'] = '<think>\n' + thought + '\n</think>\n\n' + (message.get('content') or '')
            reasoning_messages += 1
    value = {key: copy.deepcopy(body[key]) for key in ('model', 'tools', 'chat_template_kwargs') if key in body}
    value.update(messages=messages, add_generation_prompt=True, return_token_strs=False)
    return value, reasoning_messages


async def fit(model, body, folder, connection_factory, auth_headers):
    policy = model['engine'].get('context_policy')
    if policy is None:
        return body, None
    if policy not in POLICIES or model['adapter'] != 'vllm':
        raise BenchError('未受入の長文枠の計数方針です。')
    base = model['base_url'].removesuffix('/v1')
    if policy.startswith('llama-swap-'):
        base += '/upstream/' + quote(model['api_model'], safe='')
    payload, thoughts = tokenizer_payload(body)
    started = time.monotonic()
    async with connection_factory() as connection:
        response = await connection.post(base + '/tokenize', headers=auth_headers, json=payload)
        if response.status_code != 200:
            raise BenchError('長文枠の計数APIに接続できません。生成は開始していません。')
        value = response.json()
    count, capacity = value.get('count'), value.get('max_model_len')
    if type(count) is not int or count < 0 or type(capacity) is not int or capacity != model['context_tokens']:
        raise BenchError('計数APIと登録済みコンテキスト容量が一致しません。')
    requested = body['max_tokens']
    remaining = capacity - count
    receipt = {'policy': policy, 'prompt_tokens_upper_bound': count, 'context_tokens': capacity,
               'configured_output_cap': requested, 'effective_output_cap': min(requested, max(0, remaining)),
               'reasoning_messages_preserved_for_count': thoughts, 'input_history_removed': False,
               'tokenizer_payload_sha256': digest(payload), 'preparation_wall_seconds': time.monotonic() - started}
    write_once(folder / 'context-budget.json', receipt)
    if remaining < 1:
        raise BenchError('入力だけでコンテキスト容量に達しました。履歴を削らず停止しました。')
    return {**body, 'max_tokens': min(requested, remaining)}, receipt
