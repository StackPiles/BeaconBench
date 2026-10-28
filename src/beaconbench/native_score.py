"""Normalize a retained native result without another target-model request."""
from __future__ import annotations

import json
import sys

from .adapters.grades import normalize
from .util import BenchError


def grade(payload):
    spec = payload['grader']
    saved = payload['native_result']
    if saved.get('case_id') != payload['case_id'] or saved.get('native_id') != spec['native_id'] or saved.get('family') != spec['family']:
        raise BenchError('保存済みnative結果とケースの識別が一致しません。')
    if saved.get('coverage', {}).get('all_model_calls_via_rpc') is not True:
        raise BenchError('nativeの全モデル呼び出しの計測証拠がありません。')
    return normalize(spec['family'], saved['grade_record'], spec['native_id'])


def main():
    try:
        result = grade(json.load(sys.stdin))
    except (BenchError, KeyError, TypeError, ValueError) as error:
        result = {'status': 'infrastructure_error', 'message': str(error) if isinstance(error, BenchError) else 'nativeの保存結果が不足しています。'}
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))


if __name__ == '__main__':
    main()
