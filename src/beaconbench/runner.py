"""Finite, resumable workers. Generation precedes grading; no model management RPC."""
from __future__ import annotations

import asyncio
import copy
import json
import os
import subprocess
import sys
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
        values['request_wall_seconds'] = sum(row['times']['request_wall_seconds'] for row in selected)
        count, wall = values['completion_tokens'], values['request_wall_seconds']
        values['request_throughput_tokens_per_second'] = count / wall if count is not None and wall > 0 else None
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
                 'model': effective_model, 'parameters': combination['request_parameters'], 'runtime': self.plan['runtime_revision']}
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
            return previous, False
        reuse_path = self.store.root / 'reuse' / (fingerprint + '.json')
        if reuse_path.exists():
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
        if case['generation'].get('adapter', 'chat') != 'chat':
            raise BenchError('この生成アダプターはまだ実装されていません。')
        request = {**combination['request_parameters'], 'messages': copy.deepcopy(case['generation']['messages']), 'seed': seed}
        response = await chat(combination['model'], request, attempt / 'calls' / '000000', 'target', self.stopped,
                              lambda fields: self.status(**fields))
        answer = response['message'].get('content') or ''
        if response['message'].get('tool_calls'):
            raise BenchError('単発コード課題で予期しないツール呼び出しが返りました。原本を確認してください。')
        answer_path = attempt / 'final-answer.txt'
        answer_path.write_text(answer, encoding='utf-8', newline='')
        requests = saved_requests(attempt)
        snapshot = {'status': 'generated', 'case_id': case['id'], 'combination_id': combination['id'], 'trial_index': index,
                    'seed': seed, 'seed_effective': 'unknown', 'task_wall_seconds': time.monotonic() - start, 'role_totals': request_totals(requests),
                    'termination': response['measurement']['termination'], 'completed_at': utc()}
        write_once(attempt / 'trial.json', snapshot)
        files = {path.relative_to(self.store.root).as_posix(): file_sha(path) for path in attempt.rglob('*') if path.is_file() and path.name != '.write.lock'}
        record = {'generation_fingerprint': fingerprint, 'case_id': case['id'], 'trial_index': index,
                  'trial_ref': (attempt / 'trial.json').relative_to(self.store.root).as_posix(),
                  'answer_ref': answer_path.relative_to(self.store.root).as_posix(), 'files': files}
        write_once(folder / 'complete.json', record)
        write_once(reuse_path, {'trial_folder': folder.relative_to(self.store.root).as_posix(), 'source_run': self.identity})
        self.event('trial_generated', case_id=case['id'], trial_index=index, generation_fingerprint=fingerprint)
        return record, False

    async def grade(self, combination, case, index, folder, complete):
        executor = self.plan['executors'].get(case['grader'].get('executor_id'))
        grade_key = digest({'answer_sha': complete['files'][complete['answer_ref']], 'grader': case['grader'], 'executor': executor})
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
            result = await command_grade(executor, payload, attempt, self.stopped)
        marker = {'grader_fingerprint': grade_key, 'result_ref': (attempt / 'result.json').relative_to(self.store.root).as_posix(),
                  'result_sha256': file_sha(attempt / 'result.json')}
        if result['status'] == 'scored':
            write_once(grades / 'complete.json', marker)
        self.event('trial_graded', case_id=case['id'], trial_index=index, status=result['status'])
        return result, marker

    async def run(self):
        atomic_json(self.root / 'worker.json', {'pid': os.getpid(), 'started_at': utc()})
        try:
            self.status('preflight', message='モデル接続と固定資産を確認しています。', completed=0, scored=0, reused=0)
            if runtime_revision() != self.plan['runtime_revision']:
                raise BenchError('計画後に実行基盤が変更されています。新しい計画を作成してください。')
            if digest(self.pack) != self.plan['suite']['pack_sha256']:
                raise BenchError('ケース資産のSHAが異なります。')
            connection_evidence = []
            for model in {row['model']['id']: row['model'] for row in self.plan['combinations']}.values():
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
                rows[row_index]['grade'] = {key: grade[key] for key in ('status', 'passed', 'score', 'metric', 'metric_unit', 'grade_wall_seconds') if key in grade}
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
        observed = [read_json(path) for path in sorted(self.root.glob('trials/*/*/*/attempts/*/calls/*/measurement.json'))]
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
            identity = store.create_run(plan)
            folder = store.run_path(identity)
        try:
            store.reserve(identity, plan)
        except BenchError:
            if not resume_id:
                current = read_json(folder / 'status.json')
                atomic_json(folder / 'status.json', {**current, 'phase': 'infrastructure_error', 'message': '計算先が使用中のため開始しませんでした。'})
            raise
        try:
            (folder / 'stop.json').unlink(missing_ok=True)
            flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            with (folder / 'worker.log').open('ab') as log:
                process = subprocess.Popen([sys.executable, '-m', 'beaconbench', '--data-dir', str(store.root), 'worker', identity],
                                           stdin=subprocess.DEVNULL, stdout=log, stderr=log, creationflags=flags)
            atomic_json(folder / 'worker.json', {'pid': process.pid, 'launched_at': utc()})
            return identity
        except BaseException:
            store.release(identity, plan)
            raise


def stop(store: Store, identity: str):
    state = store.status(identity)
    if state['phase'] not in ACTIVE_PHASES:
        raise BenchError('この実行は稼働していません。')
    atomic_json(store.run_path(identity) / 'stop.json', {'requested_at': utc()})
    return {'run_id': identity, 'stop_requested': True}
