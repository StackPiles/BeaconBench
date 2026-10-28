"""Small, platform independent primitives for recording and verifying evidence."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class BenchError(ValueError):
    """An actionable configuration or evidence error safe to display in the UI."""


def utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def file_sha(path: Path) -> str:
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def read_json(path: Path) -> Any:
    def bad_constant(value):
        raise BenchError('有限数以外のJSON値は使えません。')
    # Windows can briefly deny a reader while an atomic metadata replacement
    # or antivirus handle is being released. Do not turn that race into a run
    # failure, and do not hide a persistent permission error.
    for attempt in range(6):
        try:
            text = path.read_text(encoding='utf-8-sig')
            break
        except PermissionError:
            if os.name != 'nt' or attempt == 5:
                raise
            time.sleep(.02 * (attempt + 1))
    value = json.loads(text, parse_constant=bad_constant)
    canonical(value)  # Also reject numeric overflow such as 1e999.
    return value


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = canonical(value) + b'\n'
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        # Windows readers/antivirus can briefly keep a share handle open.
        for attempt in range(6):
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                if attempt == 5:
                    raise
                time.sleep(.02 * (attempt + 1))
    finally:
        Path(temporary).unlink(missing_ok=True)


def write_once(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = canonical(value) + b'\n'
    # A process lock plus atomic replace also works on filesystems where hard
    # links are unavailable. The original is never replaced if it already exists.
    with file_lock(path.parent / '.write.lock'):
        if path.exists():
            if path.read_bytes() != data:
                raise BenchError('保存済みの原本を上書きできません。')
            return
        atomic_json(path, value)


@contextmanager
def file_lock(path: Path):
    """OS-released lock shared by the local server and separate CLI processes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as handle:
        if handle.seek(0, os.SEEK_END) == 0:
            handle.write(b'\0')
            handle.flush()
        handle.seek(0)
        if os.name == 'nt':
            import msvcrt
            deadline = time.monotonic() + 10
            while True:
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise BenchError('別の設定操作が処理中です。少し待って再度操作してください。') from None
                    time.sleep(.05)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def append_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('ab') as handle:
        handle.write(canonical(value) + b'\n')
        handle.flush()


def inside(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root.resolve()):
        raise BenchError('保存領域の外のファイルは参照できません。')
    return candidate


def slug(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,95}', value):
        raise BenchError('IDには英数字・ハイフン・アンダースコアを使用してください。')
    return value


def positive_int(value: Any, name: str) -> int:
    if type(value) is not int or value < 1:
        raise BenchError(name + 'は1以上の整数で指定してください。')
    return value


def trial_seed(base: int, case_id: str, revision: str, index: int) -> int:
    if type(base) is not int or not 0 <= base <= 2147483647 or type(index) is not int or index < 0:
        raise BenchError('seedまたは試行番号が不正です。')
    array = ['beaconbench-seed-v1', base, case_id, revision, index]
    raw = json.dumps(array, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')
    return int.from_bytes(hashlib.sha256(raw).digest()[:4], 'big') & 0x7fffffff


def load_env(path: Path) -> None:
    """Explicit opt-in file; never searches parent folders or logs secret values."""
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('export '):
            line = line[7:]
        key, separator, value = line.partition('=')
        if separator and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key.strip()):
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
                value = value[1:-1]
            os.environ.setdefault(key.strip(), value)


def process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == 'nt':
        # os.kill(pid, 0) is not a safe liveness probe on Windows.
        import ctypes
        from ctypes import wintypes
        api = ctypes.WinDLL('kernel32', use_last_error=True)
        api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        api.OpenProcess.restype = wintypes.HANDLE
        api.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        api.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = api.OpenProcess(0x1000, False, pid)
        if not handle:
            return ctypes.get_last_error() == 5  # Access denied is not proof of death.
        try:
            code = wintypes.DWORD()
            return bool(api.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
        finally:
            api.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
