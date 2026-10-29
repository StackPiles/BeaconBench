"""Review saved answers in a new immutable record, with no generation/resume path."""
from __future__ import annotations

import copy
import uuid

from .adapters.bindings import resolve_case
from .adapters.grades import normalize
from .assets import load_pack
from .evaluation_contracts import material_from_saved, review
from .util import BenchError, digest, file_lock, file_sha, inside, read_json, slug, utc, write_once


def reweight_sales(grade, spec):
    result = copy.deepcopy(grade)
    # A retained final_score belongs to the old weighting, not the new profile.
    native = {key: value for key, value in grade['evidence']['native'].items() if key != 'final_score'}
    derived = normalize('salesllm-ja', native, spec['native_id'], proportion=spec['proportion'])
    result.update({key: value for key, value in derived.items() if key != 'evidence'})
    result['score_profile'] = spec['score_profile']
    result['source_score'] = {'score': grade['score'], 'proportion': grade['components']['proportion']}
    return result


def versions(store, run_id):
    rows = []
    for path in (store.root / 'evaluation-reviews').glob('*/summary.json'):
        record = read_json(path)
        if record.get('source_run_id') == run_id:
            rows.append({'id': path.parent.name, 'name': record['name'], 'created_at': record['created_at']})
    return sorted(rows, key=lambda row: row['created_at'], reverse=True)


def load(store, run_id, identity):
    folder = inside(store.root, 'evaluation-reviews/' + slug(identity))
    record = read_json(folder / 'summary.json')
    if record['source_run_id'] != run_id:
        raise BenchError('別の実行に対する点検記録です。')
    if digest(read_json(store.run_path(run_id) / 'pack.json')) != record['source_pack_sha256']:
        raise BenchError('点検元の評価セットのSHAが一致しません。')
    for row in record['rows']:
        result_path = inside(store.root, row['result_ref'])
        source_path = inside(store.root, row['source_result_ref'])
        if file_sha(result_path) != row['result_sha256'] or read_json(result_path) != row['grade']:
            raise BenchError('点検結果の原本SHAが一致しません。')
        if file_sha(source_path) != row['source_result_sha256'] or read_json(source_path) != row['source_grade']:
            raise BenchError('点検元の採点原本SHAが一致しません。')
    return record


async def run(store, run_id, config):
    root = store.run_path(run_id)
    if store.status(run_id)['phase'] != 'completed':
        raise BenchError('完了した実行の保存回答だけを点検できます。')
    original_pack = read_json(root / 'pack.json')
    summary = read_json(root / 'summary.json')
    proposed = load_pack(store.root, store.registry.get('suites', config['suite_id']))
    by_id = {row['id']: row for row in proposed['cases']}
    if set(by_id) != {row['id'] for row in original_pack['cases']}:
        raise BenchError('保存回答の点検で問題の追加・削除は行いません。')
    bindings = {}
    for case in proposed['cases']:
        if case['grader'].get('evaluation_review'):
            bindings[case['id']] = resolve_case(store, case, proposed['track'])[0]
    identity = 'e-' + uuid.uuid4().hex
    folder = store.root / 'evaluation-reviews' / identity
    reservation = {'combinations': [], 'case_bindings': bindings}
    with file_lock(store.root / 'launch.lock'):
        store.reserve(identity, reservation)
    record = {'id': identity, 'name': config.get('name', '保存回答の評価条件点検v2'), 'created_at': utc(),
              'source_run_id': run_id, 'source_pack_sha256': digest(original_pack), 'proposed_pack_sha256': digest(proposed),
              'generation_calls': 0, 'rows': []}
    try:
        for row in summary['rows']:
            case = by_id[row['case_id']]
            if not case['grader'].get('evaluation_review'):
                continue
            source_case = next(c for c in original_pack['cases'] if c['id'] == case['id'])
            if case.get('source') != source_case.get('source'):
                raise BenchError('点検用の出典と保存回答の出典が異なります。')
            trial = root / 'trials' / row['combination_id'] / digest(row['case_id'])[:20] / str(row['trial_index'])
            complete = store.verify_completion(trial)
            if not complete or not row.get('grade_marker'):
                raise BenchError('保存済みの完了回答・採点がありません。')
            marker = row['grade_marker']
            source_result = inside(store.root, marker['result_ref'])
            if file_sha(source_result) != marker['result_sha256']:
                raise BenchError('点検元の採点SHAが異なります。')
            source_grade = read_json(source_result)
            base = copy.deepcopy(source_grade)
            if case['grader']['kind'] == 'salesllm_ja':
                base = reweight_sales(source_grade, case['grader'])
            attempt = inside(store.root, complete['answer_ref']).parent
            native = read_json(attempt / 'native-result.json')
            material = material_from_saved(case, native, attempt)
            destination = folder / 'cases' / digest([row['combination_id'], case['id'], row['trial_index']])[:24]
            result = await review(base, case['grader']['evaluation_review'], material, bindings[case['id']], destination,
                                  lambda: False, lambda fields: None, read_json(inside(store.root, complete['trial_ref']))['seed'])
            result_path = destination / 'result.json'
            record['rows'].append({'case_id': case['id'], 'combination_id': row['combination_id'], 'trial_index': row['trial_index'],
                                   'source_result_ref': marker['result_ref'], 'source_result_sha256': marker['result_sha256'],
                                   'result_ref': result_path.relative_to(store.root).as_posix(), 'result_sha256': file_sha(result_path),
                                   'grade': result, 'source_grade': source_grade,
                                   'requires_regeneration': case['generation_revision'] != source_case['generation_revision']})
        record['historical_generation_notice'] = '保存された旧生成条件の回答を点検しています。生成条件が変わる課題は新しい対話が必要です。'
        write_once(folder / 'summary.json', record)
    finally:
        with file_lock(store.root / 'launch.lock'):
            store.release(identity, reservation)
    return {'id': identity, 'reviewed': len(record['rows']), 'generation_calls': 0,
            'comparison_withheld': sum(row['grade'].get('comparison_eligible') is False for row in record['rows']),
            'requires_regeneration': [row['case_id'] for row in record['rows'] if row['requires_regeneration']]}
