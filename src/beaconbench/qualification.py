"""Execute paired controls and bind evidence to the exact scorer configuration."""
from __future__ import annotations

import copy
import uuid
from pathlib import Path

from .util import BenchError, digest, file_sha, inside, read_json, utc, write_once


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
    if {row['expected_passed'] for row in accepted} != {True, False} or not all(row['accepted'] for row in accepted):
        raise BenchError('このケースの採点条件は正答・誤答controlを通過していません。')


async def qualify_executor(store, identity: str, controls: list[dict]) -> dict:
    from .runner import command_grade
    executor = copy.deepcopy(store.registry.get('executors', identity))
    if not controls or not all(type(row.get('expected_passed')) is bool for row in controls):
        raise BenchError('期待する合否を持つcontrolを指定してください。')
    folder = store.root / 'qualifications' / uuid.uuid4().hex
    rows = []
    for index, row in enumerate(controls):
        payload = row['payload']
        if payload['grader'].get('executor_id') != identity:
            raise BenchError('controlと採点器IDが異なります。')
        result = await command_grade(executor, payload, folder / str(index), lambda: False)
        accepted = result['status'] == 'scored' and result.get('passed') is row['expected_passed']
        rows.append({'case_id': payload['case_id'], 'grader_sha256': digest(payload['grader']),
                     'expected_passed': row['expected_passed'], 'accepted': accepted})
    groups = {(row['case_id'], row['grader_sha256']) for row in rows}
    passed = all(row['accepted'] for row in rows) and all(
        {row['expected_passed'] for row in rows if (row['case_id'], row['grader_sha256']) == key} == {True, False}
        for key in groups)
    proof = {'utc': utc(), 'executor_fingerprint': executor_fingerprint(executor), 'controls': rows,
             'accepted': passed, 'files': {p.relative_to(store.root).as_posix(): file_sha(p) for p in folder.rglob('*') if p.is_file() and p.name != '.write.lock'}}
    path = folder / 'qualification.json'
    write_once(path, proof)
    if not passed:
        raise BenchError('正答・誤答controlが期待どおりになりませんでした。原本を保存しました。')
    executor.update(qualified=True, qualification={'path': path.relative_to(store.root).as_posix(), 'sha256': file_sha(path)})
    store.registry.save('executors', executor)
    return {'id': identity, 'qualified': True, 'case_count': len(groups), 'evidence_sha256': file_sha(path)}
