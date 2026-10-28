"""Materialize only the verified source captured by the immutable run plan."""
from __future__ import annotations

import hashlib
import os
import re
import uuid
from pathlib import Path

from .util import BenchError, digest, file_lock, file_sha, inside, read_json


def materialize_runtime(root: Path, revision: str) -> Path:
    if not re.fullmatch(r'[a-f0-9]{64}', revision):
        raise BenchError('固定した実行基盤の識別子が不正です。')
    archive = root / 'runtime-sources' / (revision + '.json')
    if not archive.exists():
        raise BenchError('当時の実行基盤の原本がありません。コードを差し替えず記録を確認してください。')
    snapshot = read_json(archive)
    files = snapshot['files']
    hashes = {name: hashlib.sha256(content.encode('utf-8')).hexdigest() for name, content in files.items()}
    if snapshot['revision'] != revision or digest(hashes) != revision:
        raise BenchError('当時の実行基盤のSHAが一致しません。')
    if not {'__init__.py', '__main__.py'}.issubset(files):
        raise BenchError('実行基盤の原本が不足しています。')
    parent = root / 'runtime-cache' / revision
    package = parent / 'beaconbench'
    with file_lock(root / 'locks' / ('runtime-' + revision + '.lock')):
        for name, content in files.items():
            path = inside(package, name)
            if path.suffix != '.py':
                raise BenchError('実行基盤に未対応のファイルがあります。')
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists():
                if file_sha(path) != hashes[name]:
                    raise BenchError('固定した実行コードが変更されています。再開せず確認してください。')
                continue
            temporary = path.with_suffix('.pending-' + uuid.uuid4().hex)
            try:
                with temporary.open('xb') as handle:
                    handle.write(content.encode('utf-8'))
                    handle.flush()
                    os.fsync(handle.fileno())
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
        if {p.relative_to(package).as_posix() for p in package.rglob('*.py')} != set(files):
            raise BenchError('固定した実行コードに未登録の追加があります。')
    return parent
