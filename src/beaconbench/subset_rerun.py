"""Measure an explicit subset and compose an immutable view of retained results.

The composed run is read-only: old generation fingerprints stay unchanged and
can never be used to launch a worker against the whole suite.
"""
from __future__ import annotations

import copy
import os
import time
import uuid

from .assets import install_pack, load_pack
from .qualification import executor_fingerprint
from .saved_reviews import load as load_review
from .util import BenchError, atomic_json, digest, file_sha, inside, read_json, utc, write_once


def generation_contract(plan, case):
    if len(plan['combinations']) != 1:
        raise BenchError('部分再測定は単一構成の実行を指定してください。')
    combo = plan['combinations'][0]
    roles = set(case['generation'].get('helpers', {}))
    native = plan.get('native_executors', {}).get(case['id'])
    if native:
        roles |= set(native.get('required_roles', [])) - {'target'}
    bindings = plan.get('case_bindings', {}).get(case['id'], {})
    return {'generation': case['generation'], 'source': case.get('source'), 'language': plan['suite']['language'],
            'model': {k: v for k, v in combo['model'].items() if k not in {'id', 'name', 'config_revision'}},
            'parameters': combo['request_parameters'], 'preset': {k: v for k, v in combo['preset'].items() if k not in {'id', 'name'}},
            'helpers': {role: bindings[role] for role in sorted(roles)},
            'native_executor': executor_fingerprint(native) if native else None,
            'seed_policy': plan['run_settings']['seed_policy'], 'trials_per_case': plan['run_settings']['trials_per_case']}


def prepare(store, config):
    source_id = config['source_run_id']
    source_root = store.run_path(source_id)
    if store.status(source_id)['phase'] != 'completed':
        raise BenchError('完了した実行だけを再利用できます。')
    old_plan, old_pack = read_json(source_root / 'plan.json'), read_json(source_root / 'pack.json')
    old_cases = {c['id']: c for c in old_pack['cases']}
    selected = config['regenerate_case_ids']
    if not selected or len(set(selected)) != len(selected) or not set(selected) < set(old_cases):
        raise BenchError('再測定するケースは重複のない空でない一部を指定してください。')
    if len(old_plan['combinations']) != 1:
        raise BenchError('部分再測定は単一構成の実行に対応しています。')
    combo = old_plan['combinations'][0]
    base = store.plan({'model_ids': [combo['model']['id']], 'presets': [combo['preset']],
                       'suite_id': config['suite_id'], 'run_settings': old_plan['run_settings']})
    pack = load_pack(store.root, base['suite'])
    if {c['id'] for c in pack['cases']} != set(old_cases):
        raise BenchError('統合する評価セットのケースIDが異なります。')
    review = load_review(store, source_id, config['evaluation_revision']) if config.get('evaluation_revision') else None
    if review and review['proposed_pack_sha256'] != digest(pack):
        raise BenchError('保存採点の点検版と今回の評価条件が異なります。')
    reviewed = {(r['case_id'], r['combination_id'], r['trial_index']): r for r in (review or {}).get('rows', [])}
    rows = read_json(source_root / 'summary.json')['rows']
    expected = len(pack['cases']) * old_plan['run_settings']['trials_per_case']
    if len(rows) != expected or len({(r['case_id'], r['combination_id'], r['trial_index']) for r in rows}) != expected:
        raise BenchError('元の回答・採点の集合が揃っていません。')
    retained = []
    for case in pack['cases']:
        if case['id'] in selected:
            continue
        old = old_cases[case['id']]
        if generation_contract(old_plan, old) != generation_contract(base, case):
            raise BenchError('再利用する回答の生成条件が異なります：' + case['id'])
        for row in (r for r in rows if r['case_id'] == case['id']):
            source_trial = source_root / 'trials' / row['combination_id'] / digest(case['id'])[:20] / str(row['trial_index'])
            complete = store.verify_completion(source_trial)
            if not complete or not row.get('grade_marker'):
                raise BenchError('再利用する完了回答と採点がありません。')
            checked = reviewed.get((case['id'], row['combination_id'], row['trial_index']))
            marker = copy.deepcopy(row['grade_marker'])
            origin_marker = copy.deepcopy(marker)
            if checked:
                marker.update(result_ref=checked['result_ref'], result_sha256=checked['result_sha256'])
            elif case['grader'] != old['grader']:
                raise BenchError('変更した採点条件に対応する保存採点がありません。')
            else:
                key = case['grader'].get('executor_id')
                old_executor, new_executor = old_plan['executors'].get(key), base['executors'].get(key)
                if bool(old_executor) != bool(new_executor) or old_executor and executor_fingerprint(old_executor) != executor_fingerprint(new_executor):
                    raise BenchError('再利用する採点器の構成が異なります。')
                if old_plan.get('case_bindings', {}).get(case['id'], {}) != base.get('case_bindings', {}).get(case['id'], {}):
                    raise BenchError('再利用する採点役の構成が異なります。')
            result_path = inside(store.root, marker['result_ref'])
            if file_sha(result_path) != marker['result_sha256']:
                raise BenchError('保存採点のSHAが一致しません。')
            grade = read_json(result_path)
            if grade.get('status') != 'scored':
                raise BenchError('完了した保存採点だけを再利用できます。')
            retained.append({'row': {**copy.deepcopy(row), 'combination_id': base['combinations'][0]['id'],
                                      'grade': grade, 'grade_marker': marker, 'reused': True},
                             'complete': complete, 'source_trial_ref': source_trial.relative_to(store.root).as_posix(),
                             'original_grade_marker': origin_marker})
    subset = copy.deepcopy(pack)
    subset.update(id='subset-' + uuid.uuid4().hex, name=config.get('name', '部分再測定'))
    subset['cases'] = [c for c in subset['cases'] if c['id'] in selected]
    suite = install_pack(store.root, subset)
    child_plan = store.plan({'model_ids': [combo['model']['id']], 'presets': [combo['preset']],
                             'suite_id': suite['id'], 'run_settings': old_plan['run_settings']})
    child_id = store.create_run(child_plan)
    child_state = store.status(child_id)
    atomic_json(store.run_path(child_id) / 'status.json', {**child_state, 'phase': 'prepared', 'message': '部分再測定の起動待ちです。'})
    parent_plan = copy.deepcopy(base)
    parent_plan.update(plan_id='p-' + uuid.uuid4().hex, created_at=utc(), read_only_composite=True,
                       subset_rerun={'source_run_id': source_id, 'child_run_id': child_id,
                                     'evaluation_revision': config.get('evaluation_revision'), 'regenerate_case_ids': selected})
    parent_plan['suite']['name'] = config.get('name', '部分再測定') + ' · 保存結果を統合'
    parent_plan['warnings'] += ['指定した一部だけを新しく測定し、他の保存回答・採点を照合して再利用した読み取り専用の統合結果です。各回答の元の生成識別値と実行基盤を保持しています。']
    parent_plan['fingerprint'] = digest({k: v for k, v in parent_plan.items() if k not in {'plan_id', 'created_at'}})
    write_once(store.root / 'plans' / (parent_plan['plan_id'] + '.json'), parent_plan)
    parent_id = store.create_run(parent_plan)
    parent = store.run_path(parent_id)
    for item in retained:
        row = item['row']
        folder = parent / 'trials' / row['combination_id'] / digest(row['case_id'])[:20] / str(row['trial_index'])
        write_once(folder / 'complete.json', {**item['complete'], 'reused_from': {'source_run': source_id, 'trial_folder': item['source_trial_ref']}})
        marker = {**row['grade_marker'], 'source_grade_marker': item['original_grade_marker']}
        write_once(folder / 'grades' / marker['grader_fingerprint'] / 'complete.json', marker)
    write_once(parent / 'composition.json', {'source_run_id': source_id, 'child_run_id': child_id,
                                            'retained': retained, 'regenerate_case_ids': selected,
                                            'source_plan_sha256': file_sha(source_root / 'plan.json')})
    refresh(store, parent_id)
    return {'run_id': parent_id, 'measurement_run_id': child_id, 'retained': len(retained),
            'new_trials': child_plan['planned_trials'], 'phase': 'prepared'}


def refresh(store, parent_id):
    parent = store.run_path(parent_id)
    config, plan, pack = read_json(parent / 'composition.json'), read_json(parent / 'plan.json'), read_json(parent / 'pack.json')
    child, child_root = store.status(config['child_run_id']), store.run_path(config['child_run_id'])
    source = store.run_path(config['source_run_id'])
    if file_sha(source / 'plan.json') != config['source_plan_sha256']:
        raise BenchError('再利用元の実行条件が変わっています。')
    rows = [copy.deepcopy(item['row']) for item in config['retained']]
    for row in rows:
        marker = row['grade_marker']
        result_path = inside(store.root, marker['result_ref'])
        if file_sha(result_path) != marker['result_sha256'] or read_json(result_path) != row['grade']:
            raise BenchError('再利用する採点原本が変わっています。')
    if (child_root / 'summary.json').exists():
        for original in read_json(child_root / 'summary.json')['rows']:
            row = copy.deepcopy(original)
            row['combination_id'] = plan['combinations'][0]['id']
            folder = parent / 'trials' / row['combination_id'] / digest(row['case_id'])[:20] / str(row['trial_index'])
            child_folder = child_root / 'trials' / original['combination_id'] / digest(row['case_id'])[:20] / str(row['trial_index'])
            complete = store.verify_completion(child_folder)
            if complete:
                write_once(folder / 'complete.json', {**complete, 'measured_in_run': config['child_run_id']})
            if row.get('grade_marker') and row['grade'].get('status') == 'scored':
                marker = row['grade_marker']
                write_once(folder / 'grades' / marker['grader_fingerprint'] / 'complete.json', marker)
            rows.append(row)
    order = {c['id']: i for i, c in enumerate(pack['cases'])}
    rows.sort(key=lambda r: (order[r['case_id']], r['trial_index']))
    phase = 'prepared' if child['phase'] == 'queued' and not (parent / 'worker.json').exists() else child['phase']
    state = {**read_json(parent / 'status.json'), 'phase': phase, 'updated_at': utc(),
             'completed': len(rows), 'scored': sum(r['grade'].get('status') == 'scored' for r in rows),
             'reused': len(config['retained']), 'attempted': child.get('attempted', 0), 'active_case': child.get('active_case'),
             'message': '保存済みの回答・採点を保持し、指定した課題だけを測定しています。', 'measurement_run_id': config['child_run_id']}
    for key in ('active_model', 'received_characters', 'received_reasoning_characters', 'active_role', 'finished_at'):
        if key in child: state[key] = child[key]
    atomic_json(parent / 'status.json', state)
    from .runner import Worker
    worker = Worker(store, parent_id)
    summary = worker.summary(rows)
    summary['composition'] = {'source_run_id': config['source_run_id'], 'measurement_run_id': config['child_run_id'], 'retained_trials': len(config['retained'])}
    child_summary = read_json(child_root / 'summary.json') if (child_root / 'summary.json').exists() else {}
    summary['observed_usage_in_this_run'] = child_summary.get('observed_usage_in_this_run', {})
    atomic_json(parent / 'summary.json', summary)
    return state


def run(store, parent_id):
    from .runner import ACTIVE_PHASES, launch, stop
    parent = store.run_path(parent_id)
    plan = read_json(parent / 'plan.json')
    if not plan.get('read_only_composite') or (parent / 'worker.json').exists():
        raise BenchError('未起動の部分再測定を指定してください。')
    atomic_json(parent / 'worker.json', {'pid': os.getpid(), 'started_at': utc(), 'purpose': 'finite subset coordinator'})
    child_id = read_json(parent / 'composition.json')['child_run_id']
    try:
        launch(store, resume_id=child_id)
        previous = None
        while True:
            child = store.status(child_id)
            signature = (child['phase'], child.get('completed'), child.get('scored'))
            if signature != previous:
                refresh(store, parent_id); previous = signature
            else:
                state = read_json(parent / 'status.json')
                atomic_json(parent / 'status.json', {**state, 'updated_at': utc(), 'active_case': child.get('active_case'),
                                                    'received_characters': child.get('received_characters', 0), 'active_role': child.get('active_role')})
            if child['phase'] not in ACTIVE_PHASES:
                return store.status(parent_id)
            if (parent / 'stop.json').exists() and not (store.run_path(child_id) / 'stop.json').exists():
                stop(store, child_id)
            time.sleep(5)
    except BaseException:
        state = read_json(parent / 'status.json')
        atomic_json(parent / 'status.json', {**state, 'phase': 'infrastructure_error', 'updated_at': utc(),
                                            'message': '部分再測定の管理で停止しました。計測用実行と保存記録を確認してください。'})
        raise
