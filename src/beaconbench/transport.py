"""Cancellable OpenAI-compatible transport. No automatic generation retries."""
from __future__ import annotations

import asyncio
import codecs
import json
import math
import os
import time
from pathlib import Path
from typing import Callable

import httpx

from .util import BenchError, append_json, atomic_json, utc, write_once


class UserStop(Exception):
    pass


class TransportFailure(Exception):
    pass


def client() -> httpx.AsyncClient:
    # Read has no wall-clock deadline. Cancellation remains available while idle.
    return httpx.AsyncClient(timeout=httpx.Timeout(connect=15, read=None, write=60, pool=15),
                             follow_redirects=False, trust_env=False)


def headers(model: dict) -> dict:
    result = {'Content-Type': 'application/json'}
    if model['api_key_env']:
        key = os.environ.get(model['api_key_env'])
        if not key:
            raise BenchError('認証用の環境変数が設定されていません。')
        result['Authorization'] = 'Bearer ' + key
    return result


async def probe(model: dict) -> dict:
    try:
        async with client() as connection:
            response = await connection.get(model['base_url'] + '/models', headers=headers(model), timeout=20)
            if response.status_code != 200:
                return {'available': False, 'http_status': response.status_code, 'checked_at': utc(), 'message': 'APIの認証または応答を確認してください。'}
            body = response.json()
            row = next((x for x in body.get('data', []) if x.get('id') == model['api_model']), None)
            return {'available': row is not None, 'checked_at': utc(), 'api_model': model['api_model'],
                    'advertised_context_tokens': row.get('max_model_len') if row else None,
                    'message': 'モデルIDへの接続を確認しました。' if row else '指定したモデルIDがAPIにありません。',
                    'loaded_state': 'unknown', 'sampling_effective_verification': 'not_reported_by_discovery'}
    except (httpx.HTTPError, ValueError):
        return {'available': False, 'checked_at': utc(), 'message': '接続先に到達できないか、応答形式が未対応です。'}


def usage_values(raw: dict | None) -> dict:
    raw = raw or {}
    def integer(value):
        return value if type(value) is int and value >= 0 else None
    def nested(key, child):
        return integer(raw[key].get(child)) if isinstance(raw.get(key), dict) else None
    values = {
        'prompt_tokens': integer(raw.get('prompt_tokens')),
        'cached_prompt_tokens': nested('prompt_tokens_details', 'cached_tokens'),
        'completion_tokens': integer(raw.get('completion_tokens')),
        'reasoning_tokens': nested('completion_tokens_details', 'reasoning_tokens'),
        'provider_total_tokens': integer(raw.get('total_tokens')),
    }
    if values['reasoning_tokens'] is None:
        values['reasoning_tokens'] = integer(raw.get('reasoning_tokens'))
    prompt, completion = values['prompt_tokens'], values['completion_tokens']
    values['total_tokens'] = prompt + completion if prompt is not None and completion is not None else None
    values['provenance'] = 'provider_reported' if raw else 'unavailable'
    values['provider_total_consistent'] = values['provider_total_tokens'] == values['total_tokens'] if values['provider_total_tokens'] is not None and values['total_tokens'] is not None else None
    return values


async def chat(model: dict, body: dict, folder: Path, role: str, stopped: Callable[[], bool],
               progress: Callable[[dict], None]) -> dict:
    folder.mkdir(parents=True, exist_ok=False)
    body = {**body, 'stream': True, 'stream_options': {'include_usage': True}}
    write_once(folder / 'request.json', {'role': role, 'body': body, 'started_at': utc()})
    started = time.monotonic()
    content, reasoning, calls = [], [], {}
    raw_usage = None
    finish_reason = None
    ttft = ttfv = None
    done = False
    native_decode = None
    received = 0
    last_progress = 0.0

    def consume(payload: str):
        nonlocal raw_usage, finish_reason, ttft, ttfv, done, native_decode, received, last_progress
        if payload.strip() == '[DONE]':
            done = True
            return
        data = json.loads(payload)
        # Ensure no nonfinite values get silently serialized into measurements.
        json.dumps(data, allow_nan=False)
        append_json(folder / 'chunks.jsonl', {'elapsed_seconds': time.monotonic() - started, 'data': data})
        if data.get('error'):
            raise TransportFailure('モデルAPIがストリーム内でエラーを返しました。')
        if isinstance(data.get('usage'), dict):
            raw_usage = data['usage']
        timing = data.get('timings', {})
        speed = timing.get('predicted_per_second') if isinstance(timing, dict) else None
        if type(speed) in (int, float) and math.isfinite(speed) and speed >= 0:
            native_decode = {'value': speed, 'source': 'llamacpp.timings.predicted_per_second'}
        for choice in data.get('choices', []):
            if choice.get('index', 0) != 0:
                raise TransportFailure('複数候補の生成には対応していません。')
            delta = choice.get('delta', choice.get('message', {})) or {}
            text = delta.get('content') or ''
            thought = delta.get('reasoning_content') or delta.get('reasoning') or ''
            if not isinstance(text, str) or not isinstance(thought, str):
                raise TransportFailure('未対応の出力形式です。')
            if text:
                content.append(text)
                with (folder / 'content.txt').open('a', encoding='utf-8', newline='') as handle:
                    handle.write(text)
                received += len(text)
            if thought:
                reasoning.append(thought)
                with (folder / 'reasoning.txt').open('a', encoding='utf-8', newline='') as handle:
                    handle.write(thought)
                received += len(thought)
            tool_delta = delta.get('tool_calls') or []
            semantic_tool = False
            for item in tool_delta:
                index = item.get('index', 0)
                call = calls.setdefault(index, {'id': '', 'type': 'function', 'function': {'name': '', 'arguments': ''}})
                if item.get('id'):
                    if not call['id']:
                        call['id'] = item['id']
                    elif item['id'] != call['id']:
                        call['id'] += item['id']
                function = item.get('function') or {}
                for key in ('name', 'arguments'):
                    piece = function.get(key) or ''
                    call['function'][key] += piece
                    semantic_tool = semantic_tool or bool(piece.strip())
            elapsed = time.monotonic() - started
            if ttft is None and (text.strip() or thought.strip() or semantic_tool):
                ttft = elapsed
            if ttfv is None and text.strip():
                ttfv = elapsed
            if choice.get('finish_reason') is not None:
                finish_reason = choice['finish_reason']
        elapsed = time.monotonic() - started
        if elapsed - last_progress >= 1:
            last_progress = elapsed
            progress({'role': role, 'request_elapsed_seconds': elapsed, 'received_characters': received})

    async def transfer():
        nonlocal done, ttft, ttfv
        async with client() as connection:
            async with connection.stream('POST', model['base_url'] + '/chat/completions', headers=headers(model), json=body) as response:
                safe_headers = {key: response.headers[key] for key in ('content-type', 'x-request-id') if key in response.headers}
                write_once(folder / 'http.json', {'status': response.status_code, 'headers': safe_headers})
                if response.status_code != 200:
                    # Error bodies may echo credentials. Persist the status, never authorization data.
                    raise TransportFailure('モデルAPIの応答に失敗しました（HTTP ' + str(response.status_code) + '）。')
                if 'text/event-stream' not in response.headers.get('content-type', ''):
                    raw = await response.aread()
                    (folder / 'response-body.bin').write_bytes(raw)
                    data = json.loads(raw)
                    for choice in data.get('choices', []):
                        choice['delta'] = choice.get('message', {})
                    consume(json.dumps(data, ensure_ascii=False, allow_nan=False))
                    ttft = ttfv = None  # A buffered response does not measure first-token latency.
                    if finish_reason is None:
                        raise TransportFailure('応答に終了理由がありません。')
                    done = True
                    return
                decoder = codecs.getincrementaldecoder('utf-8')()
                buffer, event = '', []
                with (folder / 'response.sse').open('wb') as raw_file:
                    async for chunk in response.aiter_bytes():
                        if stopped():
                            raise UserStop()
                        raw_file.write(chunk)
                        raw_file.flush()
                        buffer += decoder.decode(chunk)
                        while '\n' in buffer:
                            line, buffer = buffer.split('\n', 1)
                            line = line.removesuffix('\r')
                            if not line:
                                if event:
                                    consume('\n'.join(event))
                                    event = []
                            elif line.startswith('data:'):
                                event.append(line[5:].lstrip(' '))
                        if done:
                            break
                    buffer += decoder.decode(b'', final=True)
                    if buffer.strip() == 'data: [DONE]':
                        consume('[DONE]')
        if not done or finish_reason is None:
            raise TransportFailure('ストリームが完了通知前に切断されました。途中の回答を保存しました。')

    async def cancellation():
        while not stopped():
            await asyncio.sleep(.2)
        raise UserStop()

    request_task, cancel_task = asyncio.create_task(transfer()), asyncio.create_task(cancellation())
    failure = None
    try:
        finished, _ = await asyncio.wait({request_task, cancel_task}, return_when=asyncio.FIRST_COMPLETED)
        for task in finished:
            await task
    except UserStop:
        failure = 'user_stop'
        raise
    except (httpx.HTTPError, json.JSONDecodeError, UnicodeError, TransportFailure, ValueError) as error:
        failure = 'transport_lost'
        if isinstance(error, TransportFailure):
            raise
        raise TransportFailure('通信または応答の解析に失敗しました。原本を保存しました。') from None
    finally:
        for task in (request_task, cancel_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(request_task, cancel_task, return_exceptions=True)
        wall = time.monotonic() - started
        values = usage_values(raw_usage)
        termination = failure or {'length': 'engine_output_limit', 'stop': 'eos', 'tool_calls': 'tool_calls'}.get(finish_reason, finish_reason)
        measurement = {'role': role, 'usage': values, 'raw_usage': raw_usage,
                       'completion_includes_reasoning': True if model['adapter'] in {'vllm', 'llamacpp'} else None,
                       'times': {'request_wall_seconds': wall, 'ttft_seconds': ttft, 'ttfv_seconds': ttfv},
                       'native_decode': native_decode, 'finish_reason': finish_reason,
                       'termination': termination, 'completed': failure is None and done,
                       'received_characters': received}
        atomic_json(folder / 'measurement.json', measurement)
    message = {'role': 'assistant', 'content': ''.join(content)}
    if reasoning:
        message['reasoning_content'] = ''.join(reasoning)
    if calls:
        message['tool_calls'] = [calls[key] for key in sorted(calls)]
    write_once(folder / 'message.json', message)
    return {'message': message, 'measurement': measurement}
