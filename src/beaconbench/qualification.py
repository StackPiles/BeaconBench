"""Execute paired controls and bind evidence to the exact scorer configuration."""
from __future__ import annotations

import copy
import math
import uuid
from pathlib import Path

from .util import BenchError, digest, file_sha, inside, read_json, utc, write_once


def control_expected(row):
    if 'expected_passed' in row:
        if type(row['expected_passed']) is not bool:
            raise BenchError('controlの期待合否はboolで指定してください。')
        return {'passed': row['expected_passed']}
    score = row.get('expected_score')
    if type(score) not in (int, float) or not math.isfinite(score) or not row.get('metric_unit'):
        raise BenchError('連続得点のcontrolには有限の期待値と単位が必要です。')
    tolerance = row.get('absolute_tolerance', 1e-8)
    if type(tolerance) not in (int, float) or not math.isfinite(tolerance) or not 0 <= tolerance <= 1e-4:
        raise BenchError('controlの数値許容誤差が不正です。')
    return {'score': score, 'metric_unit': row['metric_unit'], 'absolute_tolerance': tolerance}


def accepted_control(result, expected):
    if result.get('status') != 'scored':
        return False
    if 'passed' in expected:
        return result.get('passed') is expected['passed']
    actual = result.get('score')
    return (type(actual) in (int, float) and math.isfinite(actual) and result.get('metric_unit') == expected['metric_unit']
            and math.isclose(actual, expected['score'], rel_tol=0, abs_tol=expected['absolute_tolerance']))


def complete_controls(rows):
    if not rows or not all(row['accepted'] for row in rows):
        return False
    expected = [row.get('expected', {'passed': row.get('expected_passed')}) for row in rows]
    if all('passed' in item for item in expected):
        return {item['passed'] for item in expected} == {True, False}
    return (all('score' in item for item in expected) and len({item['metric_unit'] for item in expected}) == 1
            and len({item['score'] for item in expected}) >= 2)


def executor_fingerprint(executor: dict) -> str:
    return digest({key: value for key, value in executor.items() if key not in {'qualified', 'qualification', 'config_revision'}})


def verify_qualification(root: Path, executor: dict, case: dict) -> None:
    reference = executor.get('qualification')
    if not executor.get('qualified') or not reference:
        raise BenchError('採点環境の正答・誤答controlが未検証です。')
    path = inside(root, reference['path'])
    if file_sha(path) != reference['sha256']:
        raise BenchError('採点器の受入証拠が変更されています。')
    proof = read_json(path)
    if proof['executor_fingerprint'] != executor_fingerprint(executor):
        raise BenchError('受入後に採点器の構成が変更されています。')
    for asset in executor.get('lock_files', []):
        if file_sha(Path(asset['path'])) != asset['sha256']:
            raise BenchError('登録済み採点器の資産が変更されています。')
    for relative, expected in proof['files'].items():
        if file_sha(inside(root, relative)) != expected:
            raise BenchError('controlの原本が変更されています。')
    accepted = [row for row in proof['controls'] if row['case_id'] == case['id'] and row['grader_sha256'] == digest(case['grader'])]
    if not complete_controls(accepted):
        raise BenchError('このケースの採点条件は正答・誤答controlを通過していません。')


async def qualify_executor(store, identity: str, controls: list[dict]) -> dict:
    from .runner import command_grade
    executor = copy.deepcopy(store.registry.get('executors', identity))
    if not controls:
        raise BenchError('期待する合否または得点を持つcontrolを指定してください。')
    folder = store.root / 'qualifications' / uuid.uuid4().hex
    rows = []
    for index, row in enumerate(controls):
        expected = control_expected(row)
        payload = row['payload']
        if payload['grader'].get('executor_id') != identity:
            raise BenchError('controlと採点器IDが異なります。')
        result = await command_grade(executor, payload, folder / str(index), lambda: False)
        accepted = accepted_control(result, expected)
        rows.append({'case_id': payload['case_id'], 'grader_sha256': digest(payload['grader']),
                     'expected': expected, 'accepted': accepted})
    groups = {(row['case_id'], row['grader_sha256']) for row in rows}
    passed = all(complete_controls([row for row in rows if (row['case_id'], row['grader_sha256']) == key]) for key in groups)
    proof = {'utc': utc(), 'executor_fingerprint': executor_fingerprint(executor), 'controls': rows,
             'accepted': passed, 'files': {p.relative_to(store.root).as_posix(): file_sha(p) for p in folder.rglob('*') if p.is_file() and p.name != '.write.lock'}}
    path = folder / 'qualification.json'
    write_once(path, proof)
    if not passed:
        raise BenchError('正答・誤答controlが期待どおりになりませんでした。原本を保存しました。')
    executor.update(qualified=True, qualification={'path': path.relative_to(store.root).as_posix(), 'sha256': file_sha(path)})
    store.registry.save('executors', executor)
    return {'id': identity, 'qualified': True, 'case_count': len(groups), 'evidence_sha256': file_sha(path)}
