"""Finite, resumable workers. Generation precedes grading; no model management RPC."""
from __future__ import annotations

import asyncio
import copy
import json
import math
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from .config import request_parameters
from .store import Store, runtime_revision
from .transport import TransportFailure, UserStop, chat, probe
from .util import BenchError, append_json, atomic_json, canonical, digest, file_lock, file_sha, inside, process_alive, read_json, trial_seed, utc, write_once

ACTIVE_PHASES = {'queued', 'preflight', 'generating', 'scoring'}


def metric_sum(rows: list[dict], field: str):
    values = [row['usage'].get(field) for row in rows]
    return None if any(value is None for value in values) else sum(values)


def request_totals(rows: list[dict]) -> dict:
    roles = {}
    for role in sorted({row['role'] for row in rows} | {'target', 'converter', 'judge'}):
        selected = [row for row in rows if row['role'] == role]
        values = {field: metric_sum(selected, field) for field in ('prompt_tokens', 'cached_prompt_tokens', 'completion_tokens', 'reasoning_tokens', 'total_tokens')}
        values['requests'] = len(selected)
        values['requests_with_usage'] = sum(row['usage']['total_tokens'] is not None for row in selected)
        values['known_total_tokens'] = sum(row['usage']['total_tokens'] or 0 for row in selected)
        walls = [row['times']['request_wall_seconds'] for row in selected]
        values['request_wall_seconds'] = None if any(value is None for value in walls) else sum(walls)
        count, wall = values['completion_tokens'], values['request_wall_seconds']
        values['request_throughput_tokens_per_second'] = count / wall if count is not None and wall is not None and wall > 0 else None
        values['censored_requests'] = sum(bool(row['times'].get('request_wall_censored')) for row in selected)
        native = [row['native_decode'] for row in selected if row.get('native_decode')]
        values['native_decode'] = native
        roles[role] = values
    return roles


def saved_requests(folder: Path) -> list[dict]:
    return [read_json(path) for path in sorted(folder.glob('calls/*/measurement.json'))]


async def command_grade(executor: dict, payload: dict, folder: Path, stopped) -> dict:
    folder.mkdir(parents=True, exist_ok=False)
    write_once(folder / 'input.json', payload)
    command = executor['command']
    if not isinstance(command, list) or not command or not all(isinstance(x, str) for x in command):
        raise BenchError('採点コマンドは固定した引数配列で指定してください。')
    for asset in executor.get('lock_files', []):
        if file_sha(Path(asset['path'])) != asset['sha256']:
            raise BenchError('採点器が固定したSHAから変更されています。')
    options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
    started = time.monotonic()
    with (folder / 'stdout.txt').open('wb') as output, (folder / 'stderr.txt').open('wb') as errors:
        process = await asyncio.create_subprocess_exec(*command, cwd=executor.get('cwd'), stdin=asyncio.subprocess.PIPE,
                                                       stdout=output, stderr=errors, **options)
        try:
            process.stdin.write(canonical(payload) + b'\n')
            await process.stdin.drain()
            process.stdin.close()
            waiter = asyncio.create_task(process.wait())
            while process.returncode is None:
                if stopped():
                    process.terminate()
                    await process.wait()
                    raise UserStop()
                try:
                    await asyncio.wait_for(asyncio.shield(waiter), timeout=.2)
                except asyncio.TimeoutError:
                    pass
            if process.returncode:
                raise BenchError('採点器が異常終了しました。保存した標準出力・エラーを確認してください。')
        finally:
            if process.returncode is None:
                process.terminate()
                await process.wait()
            if 'waiter' in locals():
                await waiter
    result = read_json(folder / 'stdout.txt')
    if not isinstance(result, dict) or result.get('status') not in {'scored', 'invalid_instrument', 'infrastructure_error'}:
        raise BenchError('採点器の結果形式が未対応です。')
    if result['status'] == 'scored' and type(result.get('passed')) is not bool and not (type(result.get('score')) in (int, float) and result.get('metric_unit')):
        raise BenchError('採点器の得点と単位が不足しています。')
    result['grade_wall_seconds'] = time.monotonic() - started
    write_once(folder / 'result.json', result)
    return result


def diagnostic_grade(answer: str, spec: dict) -> dict:
    return {'status': 'scored', 'passed': answer.strip() == spec['expected'], 'metric': 'connection_control',
            'metric_unit': 'binary', 'grade_wall_seconds': 0.0,
            'evidence': {'rule': 'strip-then-exact-v1', 'expected': spec['expected'], 'actual': answer.strip()}}


class Worker:
    def __init__(self, store: Store, identity: str):
        self.store, self.identity = store, identity
        self.root = store.run_path(identity)
        self.plan = read_json(self.root / 'plan.json')
        self.pack = read_json(self.root / 'pack.json')
        self.state = read_json(self.root / 'status.json')
        self.started = time.monotonic()

    def stopped(self):
        return (self.root / 'stop.json').exists()

    def status(self, phase=None, **fields):
        if phase:
            self.state['phase'] = phase
        self.state.update(fields, updated_at=utc(), worker_elapsed_seconds=time.monotonic() - self.started)
        atomic_json(self.root / 'status.json', self.state)

    def event(self, kind: str, **fields):
        append_json(self.root / 'events.jsonl', {'utc': utc(), 'kind': kind, **fields})

    def trial_path(self, combination: dict, case: dict, index: int) -> Path:
        return self.root / 'trials' / combination['id'] / digest(case['id'])[:20] / str(index)

    def generation_fingerprint(self, combination: dict, case: dict, index: int):
        seed = trial_seed(self.plan['run_settings']['seed_policy']['base_seed'], case['id'], case['generation_revision'], index)
        effective_model = {key: value for key, value in combination['model'].items() if key not in {'id', 'name'}}
        value = {'case_id': case['id'], 'case_revision': case['generation_revision'], 'trial_index': index, 'seed': seed,
                 'model': effective_model, 'parameters': combination['request_parameters'], 'runtime': self.plan['runtime_revision'],
                 'helpers': {role: binding for role, binding in self.plan.get('case_bindings', {}).get(case['id'], {}).items()
                             if case['generation'].get('adapter') == 'native' or role in case['generation'].get('helpers', {})},
                 'native_executor': self.plan.get('native_executors', {}).get(case['id'])}
        return digest(value), seed

    def records(self):
        for combination in self.plan['combinations']:
            for case in self.pack['cases']:
                for index in range(self.plan['run_settings']['trials_per_case']):
                    yield combination, case, index, self.trial_path(combination, case, index)

    def verified(self, folder: Path, expected: str) -> dict | None:
        record = self.store.verify_completion(folder)
        if record and record['generation_fingerprint'] != expected:
            raise BenchError('保存済み回答と現在の生成条件が異なります。')
        return record

    async def generate(self, combination, case, index, folder):
        fingerprint, seed = self.generation_fingerprint(combination, case, index)
        previous = self.verified(folder, fingerprint)
        if previous:
            return previous, bool(previous.get('reused_from'))
        reuse_path = self.store.root / 'reuse' / (fingerprint + '.json')
        fresh = case['id'] in self.plan.get('fresh_generation_case_ids', [])
        if reuse_path.exists() and not fresh:
            reference = read_json(reuse_path)
            previous_folder = inside(self.store.root, reference['trial_folder'])
            previous = self.verified(previous_folder, fingerprint)
            if not previous:
                raise BenchError('再利用先の完了原本が見つかりません。')
            record = {**previous, 'reused_from': reference, 'reused_at': utc()}
            write_once(folder / 'complete.json', record)
            return record, True
        attempts = folder / 'attempts'
        number = len(list(attempts.iterdir())) if attempts.exists() else 0
        attempt = attempts / str(number)
        attempt.mkdir(parents=True, exist_ok=False)
        write_once(attempt / 'identity.json', {'generation_fingerprint': fingerprint, 'seed': seed, 'case_id': case['id'], 'trial_index': index})
        self.state['attempted'] += 1
        start = time.monotonic()
        self.status('generating', active_case={'id': case['id'], 'title': case['title'], 'trial_index': index},
                    active_model={'id': combination['model']['id'], 'name': combination['model']['name']}, received_characters=0,
                    message='回答を生成しています。途中の原本も保存しています。')
        self.event('trial_started', combination_id=combination['id'], case_id=case['id'], trial_index=index, seed=seed)
        adapter = case['generation'].get('adapter', 'chat')
        task_seconds = None
        if adapter == 'chat':
            request = {**combination['request_parameters'], 'messages': copy.deepcopy(case['generation']['messages']), 'seed': seed}
            response = await chat(combination['model'], request, attempt / 'calls' / '000000', 'target', self.stopped,
                                  lambda fields: self.status(**fields))
            answer = response['message'].get('content') or ''
            termination = response['measurement']['termination']
            if response['message'].get('tool_calls'):
                raise BenchError('単発コード課題で予期しないツール呼び出しが返りました。原本を確認してください。')
            task_seconds = time.monotonic() - start
        else:
            roles = {'target': {'model': combination['model'], 'request_parameters': combination['request_parameters']},
                     **self.plan.get('case_bindings', {}).get(case['id'], {})}
            if adapter == 'native':
                from .adapters.native import execute
                replay_calls = None
                replay_manifest = self.root / 'native-replay.json'
                if replay_manifest.exists():
                    replay = read_json(replay_manifest)
                    if replay.get('case_id') == case['id']:
                        if replay.get('generation_fingerprint') != fingerprint:
                            raise BenchError('途中再開の条件が保存済みの指紋と一致しません。')
                        replay_calls = replay['calls']
                native = await execute(self.plan['native_executors'][case['id']],
                    {'case_id': case['id'], 'generation': case['generation']}, roles, attempt,
                    self.stopped, lambda fields: self.status(**fields), request_seed=seed, replay_calls=replay_calls)
                task_seconds = native.get('task_wall_seconds')
                if task_seconds is not None and (type(task_seconds) not in (int, float) or not math.isfinite(task_seconds) or task_seconds < 0):
                    raise BenchError('nativeの課題時間の形式が不正です。')
                answer = native['answer']; termination = native.get('termination', 'native_process_completed')
            elif adapter == 'dialogue':
                from .adapters.dialogue import generate
                native = await generate(case['generation'], roles, attempt, self.stopped, lambda fields: self.status(**fields), seed)
                task_seconds = native['task_wall_seconds']; answer = native['answer']; termination = native['termination']
            else:
                raise BenchError('未対応の生成アダプターです。')
        answer_path = attempt / 'final-answer.txt'
        answer_path.write_text(answer, encoding='utf-8', newline='')
        requests = saved_requests(attempt)
        snapshot = {'status': 'generated', 'case_id': case['id'], 'combination_id': combination['id'], 'trial_index': index,
                    'seed': seed, 'seed_effective': 'unknown', 'task_wall_seconds': task_seconds,
                    'pipeline_wall_seconds': time.monotonic() - start, 'role_totals': request_totals(requests),
                    'termination': termination, 'completed_at': utc()}
        write_once(attempt / 'trial.json', snapshot)
        files = {path.relative_to(self.store.root).as_posix(): file_sha(path) for path in attempt.rglob('*') if path.is_file() and path.name != '.write.lock'}
        record = {'generation_fingerprint': fingerprint, 'case_id': case['id'], 'trial_index': index,
                  'trial_ref': (attempt / 'trial.json').relative_to(self.store.root).as_posix(),
                  'answer_ref': answer_path.relative_to(self.store.root).as_posix(), 'files': files}
        write_once(folder / 'complete.json', record)
        if not fresh:
            write_once(reuse_path, {'trial_folder': folder.relative_to(self.store.root).as_posix(), 'source_run': self.identity})
        self.event('trial_generated', case_id=case['id'], trial_index=index, generation_fingerprint=fingerprint)
        return record, False

    async def grade(self, combination, case, index, folder, complete):
        executor = self.plan['executors'].get(case['grader'].get('executor_id'))
        helpers = self.plan.get('case_bindings', {}).get(case['id'], {})
        grade_key = digest({'answer_sha': complete['files'][complete['answer_ref']], 'generation_fingerprint': complete['generation_fingerprint'], 'grader': case['grader'], 'executor': executor,
                            'helpers': helpers})
        grades = folder / 'grades' / grade_key
        if (grades / 'complete.json').exists():
            marker = read_json(grades / 'complete.json')
            path = inside(self.store.root, marker['result_ref'])
            if file_sha(path) != marker['result_sha256']:
                raise BenchError('保存済み採点のSHAが異なります。')
            return read_json(path), marker
        count = len(list(grades.glob('attempt-*')))
        attempt = grades / ('attempt-' + str(count))
        answer = inside(self.store.root, complete['answer_ref']).read_text(encoding='utf-8')
        self.status('scoring', active_case={'id': case['id'], 'title': case['title'], 'trial_index': index},
                    active_model={'id': combination['model']['id'], 'name': combination['model']['name']}, message='保存済みの回答を採点しています。')
        if case['grader']['kind'] == 'diagnostic_exact':
            result = diagnostic_grade(answer, case['grader'])
            write_once(attempt / 'result.json', result)
        else:
            payload = {'case_id': case['id'], 'answer': answer, 'grader': case['grader'], 'run_id': self.identity,
                       'trial_index': index, 'seed': read_json(inside(self.store.root, complete['trial_ref']))['seed']}
            native_path = inside(self.store.root, complete['answer_ref']).parent / 'native-result.json'
            if native_path.exists():
                payload['native_result'] = read_json(native_path)
            if case['grader']['kind'] == 'salesllm_ja':
                from .adapters.sales import grade
                native = {**payload['native_result'], 'sales_goal': case['generation']['script']['assistant_system_prompt']}
                result = await grade(case['grader'], native, helpers, attempt, self.stopped,
                                     lambda fields: self.status(**fields), payload['seed'])
            else:
                result = await command_grade(executor, payload, attempt, self.stopped)
        result_path = attempt / 'result.json'
        if case['grader'].get('evaluation_review'):
            from .evaluation_contracts import material_from_saved, review
            native = payload.get('native_result', {})
            material = material_from_saved(case, native, inside(self.store.root, complete['answer_ref']).parent)
            result = await review(result, case['grader']['evaluation_review'], material, helpers,
                                  attempt / 'evaluation-review', self.stopped, lambda fields: self.status(**fields), payload['seed'])
            if (attempt / 'evaluation-review/result.json').is_file():
                result_path = attempt / 'evaluation-review/result.json'
        marker = {'grader_fingerprint': grade_key, 'result_ref': result_path.relative_to(self.store.root).as_posix(),
                  'result_sha256': file_sha(result_path)}
        if result['status'] == 'scored':
            write_once(grades / 'complete.json', marker)
        self.event('trial_graded', case_id=case['id'], trial_index=index, status=result['status'])
        return result, marker

    async def run(self):
        atomic_json(self.root / 'worker.json', {'pid': os.getpid(), 'started_at': utc(),
                                               'runtime_location': str(Path(__file__).parent),
                                               'runtime_revision': self.plan['runtime_revision']})
        try:
            self.status('preflight', message='モデル接続と固定資産を確認しています。', completed=0, scored=0, reused=0)
            if runtime_revision() != self.plan['runtime_revision']:
                raise BenchError('計画後に実行基盤が変更されています。新しい計画を作成してください。')
            if digest(self.pack) != self.plan['suite']['pack_sha256']:
                raise BenchError('ケース資産のSHAが異なります。')
            connection_evidence = []
            bindings = list(self.plan['combinations']) + [row for roles in self.plan.get('case_bindings', {}).values() for row in roles.values()]
            for model in {digest(row['model']): row['model'] for row in bindings}.values():
                evidence = await probe(model)
                connection_evidence.append({'model_id': model['id'], **evidence})
                if not evidence['available']:
                    raise BenchError(evidence['message'])
            write_once(self.root / ('preflight-' + str(time.time_ns()) + '.json'), {'utc': utc(), 'models': connection_evidence})
            rows = []
            for combination, case, index, folder in self.records():
                if self.stopped():
                    raise UserStop()
                complete, reused = await self.generate(combination, case, index, folder)
                self.state['completed'] += 1
                self.state['reused'] += int(reused)
                rows.append({'combination_id': combination['id'], 'case_id': case['id'], 'title': case['title'], 'category': case['category'],
                             'trial_index': index, 'trial': read_json(inside(self.store.root, complete['trial_ref'])),
                             'grade': {'status': 'pending'}, 'grade_marker': None,
                             'answer_ref': complete['answer_ref'], 'generation_fingerprint': complete['generation_fingerprint'],
                             'reused': 'reused_from' in complete})
                atomic_json(self.root / 'summary.json', self.summary(rows))
                self.status()
            for row_index, (combination, case, index, folder) in enumerate(self.records()):
                if self.stopped():
                    raise UserStop()
                complete = self.verified(folder, self.generation_fingerprint(combination, case, index)[0])
                grade, marker = await self.grade(combination, case, index, folder, complete)
                trial = read_json(inside(self.store.root, complete['trial_ref']))
                rows[row_index]['grade'] = {key: grade[key] for key in ('status', 'passed', 'score', 'metric', 'metric_unit', 'grade_wall_seconds',
                    'components', 'language_consistency', 'adaptation', 'evaluation_review', 'comparison_eligible') if key in grade}
                rows[row_index]['grade_marker'] = marker
                self.state['scored'] += int(grade['status'] == 'scored')
                atomic_json(self.root / 'summary.json', self.summary(rows))
                self.status()
            phase = 'completed' if all(row['grade']['status'] == 'scored' for row in rows) else 'evaluation_incomplete'
            self.status(phase, message='生成・採点・保存が完了しました。' if phase == 'completed' else '未採点または採点環境のエラーがあります。',
                        active_case=None, active_model=None, finished_at=utc())
            atomic_json(self.root / 'summary.json', self.summary(rows))
        except UserStop:
            self.status('interrupted', message='停止しました。途中の原本を保持し、完成済み回答を再利用して再開できます。', finished_at=utc())
        except Exception as error:
            if not isinstance(error, (BenchError, TransportFailure, OSError, ValueError)):
                import traceback
                traceback.print_exc()
            self.event('run_error', error_type=type(error).__name__)
            self.status('infrastructure_error', message=str(error) if isinstance(error, (BenchError, TransportFailure)) else '実行環境のエラーです。保存した記録を確認してください。', finished_at=utc())
        finally:
            self.store.release(self.identity, self.plan)

    def summary(self, rows):
        from .recovery import unclosed_measurement
        observed = []
        requests = list(self.root.glob('trials/*/*/*/attempts/*/calls/*/request.json'))
        requests += list(self.root.glob('trials/*/*/*/grades/*/attempt-*/calls/*/request.json'))
        requests += list(self.root.glob('trials/*/*/*/grades/*/attempt-*/evaluation-review/calls/*/request.json'))
        for request in sorted(requests):
            path = request.parent / 'measurement.json'
            measurement = read_json(path) if path.exists() else unclosed_measurement(request.parent)
            if not measurement.get('reused_from'):
                observed.append(measurement)
        return {'run_id': self.identity, 'suite': self.plan['suite'], 'run_settings': self.plan['run_settings'],
                'updated_at': utc(), 'phase': self.state['phase'], 'planned_trials': self.plan['planned_trials'],
                'rows': rows, 'combinations': [{'id': row['id'], 'model': self.store.registry.public_model(row['model']), 'preset': row['preset']} for row in self.plan['combinations']],
                'warnings': self.plan['warnings'], 'observed_usage_in_this_run': request_totals(observed)}


def launch(store: Store, plan_id: str | None = None, resume_id: str | None = None) -> str:
    with store.lock, file_lock(store.root / 'locks/launch.lock'):
        if resume_id:
            identity = resume_id
            state = store.status(identity)
            if state['phase'] in ACTIVE_PHASES or state['phase'] == 'completed':
                raise BenchError('この実行は稼働中、または完了済みです。')
            folder = store.run_path(identity)
            plan = read_json(folder / 'plan.json')
        else:
            plan = store.get_plan(plan_id)
            if plan.get('read_only_composite'):
                raise BenchError('統合結果は読み取り専用です。計測用の部分実行を確認してください。')
            identity = store.create_run(plan)
            folder = store.run_path(identity)
        if plan.get('read_only_composite'):
            raise BenchError('統合結果は読み取り専用です。計測用の部分実行を確認してください。')
        try:
            store.reserve(identity, plan)
        except BenchError:
            if not resume_id:
                current = read_json(folder / 'status.json')
                atomic_json(folder / 'status.json', {**current, 'phase': 'infrastructure_error', 'message': '計算先が使用中のため開始しませんでした。'})
            raise
        process = None
        try:
            (folder / 'stop.json').unlink(missing_ok=True)
            if resume_id:
                from .recovery import recover_requests
                recover_requests(folder)
            from .frozen_runtime import materialize_runtime
            runtime = materialize_runtime(store.root, plan['runtime_revision'])
            environment = {**os.environ, 'PYTHONPATH': str(runtime)}
            current = read_json(folder / 'status.json')
            atomic_json(folder / 'status.json', {**current, 'phase': 'queued', 'updated_at': utc(), 'message': '固定した実行基盤で開始しています。'})
            flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            with (folder / 'worker.log').open('ab') as log:
                process = subprocess.Popen([sys.executable, '-m', 'beaconbench', '--data-dir', str(store.root), 'worker', identity],
                                           stdin=subprocess.DEVNULL, stdout=log, stderr=log, creationflags=flags, env=environment)
            atomic_json(folder / 'worker.json', {'pid': process.pid, 'launched_at': utc(),
                                               'runtime_location': str(runtime / 'beaconbench'), 'runtime_revision': plan['runtime_revision']})
            # Reap only this owned child without blocking the API or retaining
            # an unbounded list of process objects across completed runs.
            threading.Thread(target=process.wait, daemon=True).start()
            return identity
        except BaseException:
            if process is not None and process.poll() is None:
                process.terminate()
                process.wait()
            current = read_json(folder / 'status.json')
            atomic_json(folder / 'status.json', {**current, 'phase': 'infrastructure_error', 'message': '実行基盤の起動に失敗しました。保存記録を確認してください。'})
            store.release(identity, plan)
            raise


def stop(store: Store, identity: str):
    state = store.status(identity)
    if state['phase'] not in ACTIVE_PHASES:
        raise BenchError('この実行は稼働していません。')
    atomic_json(store.run_path(identity) / 'stop.json', {'requested_at': utc()})
    return {'run_id': identity, 'stop_requested': True}
