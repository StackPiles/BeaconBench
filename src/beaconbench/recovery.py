"""Account for an interrupted request without rewriting its raw response."""
from __future__ import annotations

import json
import math
from pathlib import Path

from .transport import usage_values
from .util import BenchError, file_sha, read_json, utc, write_once


def unclosed_measurement(call: Path) -> dict:
    request = read_json(call / 'request.json')
    elapsed = None
    usage = None
    chunks = call / 'chunks.jsonl'
    if chunks.exists():
        with chunks.open(encoding='utf-8') as handle:
            for line in handle:
                try:
                    chunk = json.loads(line)
                except (ValueError, UnicodeError):
                    break  # A trailing partial line stays untouched in the raw file.
                value = chunk.get('elapsed_seconds')
                if type(value) in (int, float) and math.isfinite(value) and value >= 0:
                    elapsed = value
                data = chunk.get('data', {})
                if isinstance(data, dict) and isinstance(data.get('usage'), dict):
                    usage = data['usage']
    return {'role': request['role'], 'usage': usage_values(usage), 'raw_usage': usage,
            'times': {'request_wall_seconds': elapsed, 'request_wall_censored': True,
                      'ttft_seconds': None, 'ttfv_seconds': None},
            'native_decode': None, 'finish_reason': None, 'termination': 'not_finalized',
            'completed': False, 'received_characters': None,
            'measurement_provenance': 'derived_from_retained_chunks; wall is a lower bound'}


def recover_requests(run: Path) -> int:
    count = 0
    for request in run.glob('trials/*/*/*/attempts/*/calls/*/request.json'):
        call = request.parent
        if (call / 'measurement.json').exists():
            continue
        recovery_path = call / 'worker-exit-recovery.json'
        if recovery_path.exists():
            for name, expected in read_json(recovery_path)['raw_sha256'].items():
                if file_sha(call / name) != expected:
                    raise BenchError('中断した要求の原本が復旧記録と一致しません。')
            continue
        measurement = unclosed_measurement(call)
        measurement['termination'] = 'process_failure'
        receipt = {'recovered_at': utc(), 'measurement': measurement,
                   'raw_sha256': {p.name: file_sha(p) for p in call.iterdir() if p.is_file() and p.name in {'request.json', 'response.sse', 'chunks.jsonl', 'content.txt', 'reasoning.txt'}}}
        write_once(recovery_path, receipt)
        # Older frozen workers require a numeric wall interval. Only publish a
        # compatible measurement when an actual chunk supplies that lower bound.
        # A request with no observed interval remains unknown, never zero-filled.
        if measurement['times']['request_wall_seconds'] is not None:
            write_once(call / 'measurement.json', measurement)
        count += 1
    return count
