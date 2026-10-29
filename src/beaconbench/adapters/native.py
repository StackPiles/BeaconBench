"""Bidirectional native-harness RPC, usable locally or over an owned SSH command.

All LLM requests return to the controller's measured transport. No target keys
are passed to the native process. Native tool/agent code runs in its own pinned
environment; stdout is protocol, upstream logs are separate artifacts.
"""
from __future__ import annotations

import asyncio
import base64
import copy
import json
import os
import subprocess
import time
import re
from pathlib import Path

from ..transport import TransportFailure, UserStop, chat
from ..util import BenchError, append_json, canonical, file_sha, inside, read_json, utc, write_once


WIRE = 'beaconbench-native-rpc-v1'
TASK_FIELDS = {'messages', 'tools', 'tool_choice', 'parallel_tool_calls', 'response_format', 'stop'}
CONTROLLED_FIELDS = {'model', 'stream', 'stream_options', 'max_tokens', 'max_completion_tokens', 'temperature',
                     'top_p', 'top_k', 'min_p', 'seed', 'presence_penalty', 'frequency_penalty',
                     'repetition_penalty', 'reasoning_effort', 'chat_template_kwargs'}
SAFE_ENV = {'PATH', 'PATHEXT', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'TEMP', 'TMP', 'TMPDIR',
            'PYTHONPATH', 'VIRTUAL_ENV', 'LANG', 'LC_ALL', 'USERPROFILE', 'HOME', 'PROGRAMDATA'}


def locked_files(executor):
    if not executor.get('lock_files'):
        raise BenchError('native実行器のコード・データlockがありません。')
    for asset in executor['lock_files']:
        if file_sha(Path(asset['path'])) != asset['sha256']:
            raise BenchError('native実行器の固定ファイルが変更されています。')


def require_locked(executor, paths):
    listed = {Path(asset['path']).resolve() for asset in executor.get('lock_files', [])}
    if not {Path(path).resolve() for path in paths} <= listed:
        raise BenchError('実際に使うnative資産がlock_filesに含まれていません。')


async def execute(executor: dict, payload: dict, roles: dict, folder: Path, stopped, progress,
                  *, request_seed: int, stage='generate', replay_calls=None) -> dict:
    locked_files(executor)
    from ..vita_payment import PaymentGuard, validate as validate_payment
    payment_guard = PaymentGuard() if validate_payment(payload.get('generation', {})) else None
    from ..vita_goals import decorate as decorate_goals, validate as validate_goals
    fixed_goals = validate_goals(payload.get('generation', {}))
    command = executor['command']
    if not command or not isinstance(command, list) or not all(isinstance(part, str) for part in command):
        raise BenchError('native commandは固定した引数配列で指定してください。')
    folder.mkdir(parents=True, exist_ok=True)
    work = folder / 'native'
    work.mkdir(exist_ok=False)
    initial = {'protocol': WIRE, 'type': 'start', 'stage': stage, 'payload': payload,
               'roles': sorted(roles), 'seed': request_seed,
               'model_capacities': {role: {'context_tokens': row['model']['context_tokens'],
                                          'output_tokens': row['request_parameters']['max_tokens']} for role, row in roles.items()}}
    write_once(work / 'start.json', initial)
    environment = {key: value for key, value in os.environ.items() if key.upper() in SAFE_ENV}
    # Explicit non-secret native setup values; never inherit model credentials.
    for key, value in executor.get('environment', {}).items():
        if not isinstance(value, str) or any(token in key.upper() for token in ('KEY', 'TOKEN', 'SECRET', 'PASSWORD')):
            raise BenchError('native環境に認証値を渡せません。モデル接続は計測RPCを使います。')
        environment[key] = value
    model_secrets = {os.environ.get(row['model']['api_key_env']) for row in roles.values() if row['model']['api_key_env']}
    for destination, reference in executor.get('credential_refs', {}).items():
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', destination) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', reference):
            raise BenchError('native環境の認証参照名が不正です。')
        value = os.environ.get(reference)
        if not value or value in model_secrets:
            raise BenchError('native環境の専用認証がないか、推論APIの鍵と混在しています。')
        environment[destination] = value
    options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {'start_new_session': True}
    started = time.monotonic()
    failure = None
    result = None
    pending = None
    artifacts, streams = {}, {}
    with (work / 'stderr.txt').open('wb') as errors:
        process = await asyncio.create_subprocess_exec(*command, cwd=executor.get('cwd'), env=environment,
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=errors,
                    limit=32 * 1024 * 1024, **options)

        async def send(row):
            process.stdin.write(canonical(row) + b'\n')
            await process.stdin.drain()

        async def receive():
            nonlocal result
            await send(initial)
            seen = set()
            sequence = 0
            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                frame = json.loads(line)
                canonical(frame)
                append_json(work / 'protocol.jsonl', {'direction': 'native-to-controller', 'utc': utc(), 'frame': frame})
                kind = frame.get('type')
                if result is not None:
                    raise BenchError('native結果の後に追加の要求がありました。')
                if kind == 'artifact_start':
                    key, relative, size, expected_sha = (frame.get(k) for k in ('id', 'path', 'bytes', 'sha256'))
                    if (not isinstance(key, str) or not key or key in streams or key in artifacts or type(size) is not int or size < 0
                            or not isinstance(relative, str) or not isinstance(expected_sha, str) or not re.fullmatch('[0-9a-f]{64}', expected_sha)):
                        raise BenchError('native成果物の転送宣言が不正です。')
                    path = inside(work / 'artifacts', relative)
                    if path.exists(): raise BenchError('native成果物の同じ保存先が重複しています。')
                    path.parent.mkdir(parents=True, exist_ok=True)
                    streams[key] = {'handle': path.open('xb'), 'path': path, 'size': size, 'sha256': expected_sha, 'received': 0}
                    continue
                if kind == 'artifact_chunk':
                    stream = streams.get(frame.get('id'))
                    if stream is None or frame.get('offset') != stream['received']:
                        raise BenchError('native成果物の転送位置が不正です。')
                    data = base64.b64decode(frame['data'], validate=True)
                    if stream['received'] + len(data) > stream['size']: raise BenchError('native成果物の転送量が宣言と異なります。')
                    stream['handle'].write(data); stream['received'] += len(data)
                    continue
                if kind == 'artifact_end':
                    stream = streams.pop(frame.get('id'), None)
                    if stream is None: raise BenchError('native成果物の終了宣言が重複しています。')
                    stream['handle'].close()
                    if stream['received'] != stream['size'] or file_sha(stream['path']) != stream['sha256']:
                        raise BenchError('native成果物の転送SHAが一致しません。')
                    artifacts[frame['id']] = {'path': stream['path'].relative_to(work / 'artifacts').as_posix(), 'sha256': stream['sha256'], 'bytes': stream['size']}
                    continue
                if kind == 'event':
                    continue
                if kind == 'result':
                    if frame.get('protocol') != WIRE or frame.get('case_id') != payload['case_id']:
                        raise BenchError('native結果のprotocol・ケースIDが一致しません。')
                    if frame.get('coverage', {}).get('all_model_calls_via_rpc') is not True:
                        raise BenchError('native内のモデル呼び出しの計測coverageが未確認です。')
                    if not isinstance(frame.get('answer'), str):
                        raise BenchError('nativeの最終成果物の表現がありません。')
                    if streams or frame.get('artifact_manifest', {}) != artifacts:
                        raise BenchError('native成果物の転送が未完了か、一覧と一致しません。')
                    result = frame
                    continue
                if kind != 'model_request':
                    raise BenchError('native実行器が未対応の制御メッセージを返しました。')
                identity, role = frame.get('id'), frame.get('role')
                if not isinstance(identity, str) or not identity or identity in seen:
                    raise BenchError('native要求IDが空、または重複しています。')
                seen.add(identity)
                if role not in roles or stage == 'grade' and role == 'target':
                    raise BenchError('nativeが未登録の役割、または採点中の対象モデルを要求しました。')
                body = frame.get('body')
                if not isinstance(body, dict) or set(body) - TASK_FIELDS - CONTROLLED_FIELDS or not isinstance(body.get('messages'), list):
                    raise BenchError('nativeの生成引数に未対応の項目があります。')
                binding = roles[role]
                parameters = {**binding['request_parameters'], **{key: copy.deepcopy(value) for key, value in body.items() if key in TASK_FIELDS},
                              'seed': request_seed}
                from ..evaluation_contracts import remind_customer
                parameters = remind_customer(parameters, payload.get('generation', {}), role)
                if payment_guard:
                    parameters = payment_guard.decorate(parameters, role)
                if fixed_goals:
                    parameters = decorate_goals(parameters, role)
                request_folder = folder / 'calls' / f'{sequence:06d}'
                sequence += 1
                try:
                    if replay_calls and sequence <= len(replay_calls):
                        from ..recovery import replay_model_call
                        response = replay_model_call(replay_calls[sequence - 1], parameters, role, request_folder)
                    else:
                        response = await chat(binding['model'], parameters, request_folder, role,
                                              lambda: stopped() or process.returncode is not None, progress)
                except UserStop:
                    if not stopped():
                        raise BenchError('native実行器が要求の途中で終了しました。途中の原本を保全しました。') from None
                    raise
                measurement = response['measurement']
                forwarded = payment_guard.forward(response['message'], role, request_folder.relative_to(folder).as_posix()) if payment_guard else response['message']
                if payment_guard and role == 'customer':
                    write_once(request_folder / 'payment-termination.json', {**payment_guard.decisions[-1], 'forwarded_message': forwarded})
                reply = {'type': 'model_response', 'id': identity,
                         'response': {'id': identity, 'created': int(time.time()), 'object': 'chat.completion', 'model': binding['model']['api_model'],
                                      'choices': [{'index': 0, 'message': forwarded, 'finish_reason': measurement['finish_reason']}],
                                      'usage': measurement['raw_usage']}}
                write_once(request_folder / 'native-request.json', {'rpc_id': identity, 'role': role, 'body': body,
                    'controlled_fields_overridden': sorted(set(body) & CONTROLLED_FIELDS)})
                await send(reply)
            await process.wait()
            if process.returncode or result is None:
                raise BenchError('native実行器が結果を返さず終了しました。原本を確認してください。')
            if replay_calls and sequence < len(replay_calls):
                raise BenchError('保存済み応答の再生が完了する前にnativeが終了しました。')

        async def cancel():
            while not stopped():
                await asyncio.sleep(.2)
            raise UserStop()

        pending = asyncio.create_task(receive())
        cancellation = asyncio.create_task(cancel())
        try:
            completed, _ = await asyncio.wait({pending, cancellation}, return_when=asyncio.FIRST_COMPLETED)
            for task in completed:
                await task
        except (BaseException,) as error:
            failure = type(error).__name__
            raise
        finally:
            for task in (pending, cancellation):
                if not task.done():
                    task.cancel()
            await asyncio.gather(pending, cancellation, return_exceptions=True)
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), 5)
                except asyncio.TimeoutError:
                    process.kill(); await process.wait()
            write_once(work / 'execution.json', {'utc': utc(), 'process_wall_seconds': time.monotonic() - started,
                                                'exit_code': process.returncode, 'failure': failure})
            for stream in streams.values(): stream['handle'].close()
            write_once(work / 'artifact-manifest.json', artifacts)
            if payment_guard:
                write_once(folder / 'payment-guard.json', payment_guard.report())
    if replay_calls:
        result['recovery'] = {'replayed_model_calls': len(replay_calls), 'current_native_task_wall_seconds': result.get('task_wall_seconds'),
                              'task_wall_seconds_unavailable': 'original task boundary was not persisted before infrastructure failure'}
        result['task_wall_seconds'] = None
    if payment_guard:
        result['customer_payment_guard'] = payment_guard.report()
    write_once(folder / 'native-result.json', result)
    return result
