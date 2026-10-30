"""Versioned rechecks of saved stop attempts, without generating target output."""
from __future__ import annotations

import uuid

from . import vita_termination_v2 as stop
from .calibration import verify_instrument
from .config import request_parameters, validate_preset
from .util import BenchError, digest, file_lock, file_sha, inside, read_json, slug, utc, write_once


async def run(store, identity, config):
    state = store.status(identity)
    if state['phase'] in {'queued', 'preflight', 'generating', 'scoring'}:
        raise BenchError('実行中の回答の終了検査は行えません。')
    source_id = state.get('measurement_run_id', identity)
    root = store.run_path(source_id)
    candidates = list(root.glob('trials/*/*/*/attempts/*/calls/*/termination/input.json'))
    if len(candidates) != 1:
        raise BenchError('終了判定の原本を一意に指定できません。別の試行を選択してください。')
    source_path = candidates[0]
    old_result = source_path.with_name('result.json')
    native_start = source_path.parents[3] / 'native/start.json'
    original = read_json(source_path)
    source = stop.material(original['reference'], original['messages'], config['source_context'])
    case_hash = source_path.relative_to(root).parts[2]
    case = next((c for c in read_json(root / 'pack.json')['cases'] if digest(c['id'])[:20] == case_hash), None)
    if case is None or source['source_facts']['task_id'] != case['generation'].get('native_id'):
        raise BenchError('公式意味情報と保存回答の問題IDが異なります。')
    helper = config['helper']
    model = store.registry.get('models', helper['model_id'])
    preset = validate_preset(helper['preset'])
    verify_instrument(store.root, helper['instrument'], model, preset, stop.policy())
    binding = {'model': model, 'preset': preset, 'request_parameters': request_parameters(model, preset), 'policy': stop.policy()}
    revision = 'tr-' + uuid.uuid4().hex
    folder = store.root / 'termination-reviews' / source_id / slug(revision)
    protected = {p.relative_to(store.root).as_posix(): file_sha(p) for p in (source_path, old_result, root / 'status.json', root / 'pack.json', root / 'plan.json')}
    reservation = {'combinations': [], 'case_bindings': {'saved-stop': {stop.ROLE: binding}}}
    with file_lock(store.root / 'locks/launch.lock'):
        store.reserve(revision, reservation)
    try:
        write_once(folder / 'config.json', config)
        result = await stop.review(source, binding, folder / 'review', lambda: False, lambda _: None,
                                   read_json(native_start)['seed'])
        if any(file_sha(inside(store.root, name)) != sha for name, sha in protected.items()):
            raise BenchError('点検中に原回答・原判定・状態が変わりました。')
        result_path = folder / 'review/result.json'
        record = {'id': revision, 'name': config.get('name', '保存回答の終了検査 v2'), 'created_at': utc(),
            'source_run_id': source_id, 'version': stop.VERSION, 'verdict': result['candidate']['verdict'],
            'confirmed': result['confirmed'], 'explanation': result['candidate']['explanation'],
            'budget_totals': source['cost_ledger']['budget_totals'], 'official_grade_changed': False,
            'target_generation_calls': 0, 'source_files': protected,
            'result_ref': result_path.relative_to(store.root).as_posix(), 'result_sha256': file_sha(result_path)}
        write_once(folder / 'summary.json', record)
    finally:
        with file_lock(store.root / 'locks/launch.lock'):
            store.release(revision, reservation)
    return record


def latest(store, source_id):
    records = [read_json(path) for path in (store.root / 'termination-reviews' / source_id).glob('*/summary.json')]
    if not records:
        return None
    record = max(records, key=lambda row: row['created_at'])
    if (file_sha(inside(store.root, record['result_ref'])) != record['result_sha256']
            or any(file_sha(inside(store.root, name)) != sha for name, sha in record['source_files'].items())):
        raise BenchError('保存した終了検査の原本・結果SHAが異なります。')
    result_path = inside(store.root, record['result_ref'])
    result = read_json(result_path)
    source = read_json(result_path.parent / 'input.json')
    old_ref = next((name for name in record['source_files'] if name.endswith('/termination/result.json')), None)
    return {**record, 'original_verdict': read_json(inside(store.root, old_ref)).get('candidate', {}).get('verdict') if old_ref else 'unknown',
            'condition_checks': [{**check, 'condition_quote': source['conditions'][check['condition_index']]['quote']}
                                 for check in result['candidate'].get('checks', [])]}
