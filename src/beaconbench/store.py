"""Immutable plans, bounded progress snapshots, and verified trial reuse."""
from __future__ import annotations

import copy
import json
import os
import threading
import uuid
from pathlib import Path

from .assets import load_pack
from .config import Registry, request_parameters, validate_preset, validate_settings
from .util import BenchError, atomic_json, digest, file_sha, inside, process_alive, read_json, slug, utc, write_once


def runtime_revision() -> str:
    source = Path(__file__).resolve().parent
    return digest({path.relative_to(source).as_posix(): file_sha(path) for path in sorted(source.rglob('*.py'))})


class Store:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.registry = Registry(self.root)
        self.lock = threading.RLock()

    def plan(self, value: dict) -> dict:
        if set(value) != {'model_ids', 'presets', 'suite_id', 'run_settings'}:
            raise BenchError('計画の項目を確認してください。')
        if not value['model_ids'] or not value['presets']:
            raise BenchError('モデルと生成プリセットを1つ以上選択してください。')
        if len(set(value['model_ids'])) != len(value['model_ids']):
            raise BenchError('同じモデルが重複しています。')
        presets = [validate_preset(row) for row in value['presets']]
        if len({row['id'] for row in presets}) != len(presets):
            raise BenchError('同じプリセットIDが重複しています。')
        settings = validate_settings(value['run_settings'])
        suite = self.registry.get('suites', value['suite_id'])
        if not suite.get('execution_ready'):
            raise BenchError('この評価セットは未準備です。')
        pack = load_pack(self.root, suite)
        models = [self.registry.get('models', identity) for identity in value['model_ids']]
        executors = {}
        for case in pack['cases']:
            if case['grader']['kind'] == 'command':
                executor = self.registry.get('executors', case['grader']['executor_id'])
                from .qualification import verify_qualification
                verify_qualification(self.root, executor, case)
                executors[executor['id']] = executor
        combos = []
        for model in models:
            for preset in presets:
                parameters = request_parameters(model, preset)
                identity = digest({'model': model, 'preset': {key: item for key, item in preset.items() if key not in {'name', 'id'}}})[:20]
                combos.append({'id': identity, 'model': copy.deepcopy(model), 'preset': copy.deepcopy(preset), 'request_parameters': parameters})
        plan = {'schema_version': '0.3.0', 'plan_id': 'p-' + uuid.uuid4().hex, 'created_at': utc(),
                'suite': suite, 'run_settings': settings, 'combinations': combos, 'executors': executors,
                'case_count': len(pack['cases']), 'planned_trials': len(combos) * len(pack['cases']) * settings['trials_per_case'],
                'planned_api_calls': None, 'runtime_revision': runtime_revision(),
                'framework_limits': {'total_output_tokens': None, 'tool_calls': None, 'agent_turns': None, 'framework_deadline_seconds': None},
                'warnings': ['APIの識別情報・登録済み容量・エンジン構成に基づく比較です。実効設定の確認範囲は接続情報へ記録します。'],
                'execution_ready': True}
        plan['warnings'].append('キャッシュの制御・cold条件の確認は未実装です。共有サービスを変更せず、申告した条件を記録します。')
        if suite['track'] == 'diagnostic':
            plan['warnings'].append('接続診断です。通常能力のベンチマーク得点へ含めません。')
        plan['fingerprint'] = digest({key: row for key, row in plan.items() if key not in {'plan_id', 'created_at'}})
        source = Path(__file__).resolve().parent
        write_once(self.root / 'runtime-sources' / (plan['runtime_revision'] + '.json'),
                   {'revision': plan['runtime_revision'], 'files': {p.relative_to(source).as_posix(): p.read_bytes().decode('utf-8') for p in sorted(source.rglob('*.py'))}})
        write_once(self.root / 'plans' / (plan['plan_id'] + '.json'), plan)
        return plan

    def get_plan(self, identity: str) -> dict:
        return read_json(self.root / 'plans' / (slug(identity) + '.json'))

    def public_plan(self, plan: dict) -> dict:
        result = {key: copy.deepcopy(row) for key, row in plan.items() if key not in {'combinations', 'executors'}}
        result['combinations'] = [{'id': row['id'], 'model': self.registry.public_model(row['model']),
                                   'preset': row['preset'], 'request_parameters': row['request_parameters']} for row in plan['combinations']]
        result['evaluation_executors'] = [{'id': row['id'], 'name': row.get('name', row['id']), 'qualified': bool(row.get('qualified'))} for row in plan['executors'].values()]
        return result

    def run_path(self, identity: str) -> Path:
        return self.root / 'runs' / slug(identity)

    def create_run(self, plan: dict) -> str:
        identity = 'r-' + uuid.uuid4().hex
        folder = self.run_path(identity)
        write_once(folder / 'plan.json', plan)
        write_once(folder / 'pack.json', load_pack(self.root, plan['suite']))
        atomic_json(folder / 'status.json', {'run_id': identity, 'plan_id': plan['plan_id'], 'phase': 'queued',
                                           'created_at': utc(), 'updated_at': utc(), 'planned': plan['planned_trials'],
                                           'completed': 0, 'scored': 0, 'reused': 0, 'attempted': 0})
        return identity

    def status(self, identity: str) -> dict:
        folder = self.run_path(identity)
        result = read_json(folder / 'status.json')
        pid_file = folder / 'worker.json'
        if pid_file.exists() and result['phase'] in {'queued', 'preflight', 'generating', 'scoring'}:
            pid = read_json(pid_file).get('pid', 0)
            if not process_alive(pid):
                result = {**result, 'phase': 'interrupted', 'message': '実行プロセスが終了しています。保存済み回答を確認して再開できます。'}
        return result

    def runs(self) -> list[dict]:
        rows = []
        parent = self.root / 'runs'
        if parent.exists():
            for folder in parent.iterdir():
                if folder.is_dir() and (folder / 'status.json').exists():
                    state = self.status(folder.name)
                    plan = read_json(folder / 'plan.json')
                    rows.append({**state, 'suite': plan['suite'], 'trials_per_case': plan['run_settings']['trials_per_case'],
                                 'models': [row['model']['name'] for row in plan['combinations']],
                                 'result_available': (folder / 'summary.json').exists()})
        return sorted(rows, key=lambda row: row['created_at'], reverse=True)

    def available_suites(self) -> list[dict]:
        from .qualification import verify_qualification
        rows = []
        for suite in self.registry.read()['suites']:
            value = copy.deepcopy(suite)
            try:
                for case in load_pack(self.root, suite)['cases']:
                    if case['grader']['kind'] == 'command':
                        executor = self.registry.get('executors', case['grader']['executor_id'])
                        verify_qualification(self.root, executor, case)
            except (BenchError, OSError, ValueError, KeyError) as error:
                value.update(execution_ready=False,
                             unavailable_reason=str(error) if isinstance(error, BenchError) else 'ケース資産または採点器の受入証拠を確認してください。')
            rows.append(value)
        return rows

    def lease_keys(self, plan: dict) -> list[str]:
        return sorted({digest(row['model']['engine'].get('resource_group') or row['model']['base_url']) for row in plan['combinations']})

    def reserve(self, identity: str, plan: dict) -> None:
        acquired = []
        try:
            for key in self.lease_keys(plan):
                path = self.root / 'leases' / (key + '.json')
                path.parent.mkdir(parents=True, exist_ok=True)
                try:
                    with path.open('x', encoding='utf-8') as handle:
                        json.dump({'run_id': identity, 'reserved_at': utc()}, handle)
                    acquired.append(path)
                except FileExistsError:
                    previous = read_json(path)
                    owner = previous.get('run_id')
                    # launch.lock serializes recovery with every server/CLI launch.
                    # An unknown owner or a live worker is never displaced.
                    owner_path = self.run_path(owner) if owner else None
                    if not owner_path or not (owner_path / 'status.json').exists():
                        raise BenchError('計算先に所有者不明の予約があります。記録を確認してください。') from None
                    state = self.status(owner)
                    worker = owner_path / 'worker.json'
                    if state['phase'] in {'queued', 'preflight', 'generating', 'scoring'} or worker.exists() and process_alive(read_json(worker).get('pid', 0)):
                        raise BenchError('この計算先は別の実行が使用しています。停止状態も確認してください。') from None
                    write_once(self.root / 'leases' / 'recovered' / (key + '-' + uuid.uuid4().hex + '.json'), previous)
                    path.unlink()
                    with path.open('x', encoding='utf-8') as handle:
                        json.dump({'run_id': identity, 'reserved_at': utc()}, handle)
                    acquired.append(path)
        except BaseException:
            for path in acquired:
                path.unlink(missing_ok=True)
            raise

    def release(self, identity: str, plan: dict) -> None:
        for key in self.lease_keys(plan):
            path = self.root / 'leases' / (key + '.json')
            if path.exists() and read_json(path)['run_id'] == identity:
                path.unlink()

    def verify_completion(self, folder: Path) -> dict | None:
        marker = folder / 'complete.json'
        if not marker.exists():
            return None
        record = read_json(marker)
        for relative, expected in record['files'].items():
            if file_sha(inside(self.root, relative)) != expected:
                raise BenchError('保存済み回答のSHAが一致しません。再生成せず確認してください。')
        return record
