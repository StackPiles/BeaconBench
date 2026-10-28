"""Client for a pinned Frontier judge, run inside its isolated grading host.

Does not create a privileged container, touch model services, or choose tests.
The enclosing owned native environment must provide cleanup and source locks.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from .adapters.grades import normalize
from .adapters.native import locked_files
from .util import BenchError, read_json, write_once


def extract_cpp(answer):
    # Fixed compatibility policy from the existing compact code evaluation.
    fences = [index for index, line in enumerate(answer.splitlines()) if '```' in line]
    return '\n'.join(answer.splitlines()[fences[-2] + 1:fences[-1]]) if len(fences) >= 2 else answer.strip()


def checked_result(native, *, pid, expected_test_count, score_formula_sha256):
    if native.get('status') != 'done':
        return {'status': 'infrastructure_error', 'message': 'Frontierの判定が未完了か、nativeエラーです。0点に補完しません。', 'evidence': {'native': native}}
    cases = native.get('cases')
    if not isinstance(cases, list) or len(cases) != expected_test_count or expected_test_count < 1:
        raise BenchError('Frontierの全テストの実行証拠が不足しています。')
    if any(type(row.get('scoreRatio')) not in (int, float) or not math.isfinite(row['scoreRatio']) or not 0 <= row['scoreRatio'] <= 1 for row in cases):
        raise BenchError('Frontierのテスト別得点が不正です。')
    # This is the pinned judge_engine.js native aggregation, not a new scale.
    expected = 100 * sum(row['scoreRatio'] for row in cases) / expected_test_count
    score = native.get('score')
    if type(score) not in (int, float) or not math.isfinite(score) or not math.isclose(score, expected, rel_tol=0, abs_tol=1e-7):
        raise BenchError('Frontierのnative得点式と集計値が一致しません。')
    result = normalize('frontier-algorithmic', {'pid': pid, 'score': score, 'all_tests_evaluated': True,
                       'score_formula_sha256': score_formula_sha256}, pid)
    result['evidence']['official_result'] = native
    return result


def grade(config, payload):
    locked_files(config)
    base = config['base_url'].rstrip('/')
    parts = urlsplit(base)
    if parts.scheme != 'http' or parts.hostname not in {'localhost', '127.0.0.1', '::1'} or parts.username or parts.password or parts.query or parts.fragment:
        raise BenchError('Frontier clientは採点環境内のloopback APIだけに接続してください。')
    pid = payload['grader']['native_id']; task = config['problems'][pid]
    if payload['grader']['score_formula_sha256'] != task['score_formula_sha256']:
        raise BenchError('Frontierの問題固有の得点式がケースと異なります。')
    folder = Path(config['output_root']) / ('frontier-' + uuid.uuid4().hex)
    write_once(folder / 'input.json', payload)
    def request(path, body=None):
        req = Request(base + path, data=None if body is None else json.dumps(body).encode(), headers={'Content-Type': 'application/json'})
        with urlopen(req, timeout=30) as response: return json.load(response)
    submitted = request('/submit', {'pid': pid, 'lang': 'cpp', 'code': extract_cpp(payload['answer'])})
    sid = submitted['sid']
    if type(sid) is not int or sid < 0: raise BenchError('Frontierの提出IDが不正です。')
    write_once(folder / 'submission.json', submitted)
    # Native go-judge owns per-test time limits; no new whole-task deadline.
    while True:
        native = request('/result/' + str(sid))
        if native.get('status') in {'done', 'error'}: break
        if native.get('status') not in {'queued', 'running', 'judging', 'compiling'}: raise BenchError('未対応のFrontier状態です。')
        time.sleep(.5)
    write_once(folder / 'official.json', native)
    result = checked_result(native, pid=pid, expected_test_count=task['test_count'], score_formula_sha256=task['score_formula_sha256'])
    result['native_artifact'] = str(folder)
    write_once(folder / 'result.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--config', type=Path, required=True)
    args = parser.parse_args()
    try: result = grade(read_json(args.config), json.load(sys.stdin))
    except (BenchError, OSError, ValueError, KeyError) as error:
        result = {'status': 'infrastructure_error', 'message': str(error) if isinstance(error, BenchError) else 'Frontierの固定資産またはnative APIの確認が必要です。'}
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))


if __name__ == '__main__': main()
