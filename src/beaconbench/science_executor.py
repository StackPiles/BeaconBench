"""SciCode-Verified first-step grading on the configured Linux grading host.

No inference or model lifecycle changes. Generated code only runs in owned,
offline containers. Input assets, extraction function and both images are fixed.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

from .adapters.grades import normalize
from .adapters.native import locked_files, require_locked
from .util import BenchError, file_sha, read_json, utc, write_once


def extracted(source, answer):
    node = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == 'extract_code')
    namespace = {'re': re}
    exec(compile(ast.get_source_segment(source, node), 'pinned_scicode_extract.py', 'exec'), namespace)
    return namespace['extract_code'](answer)


def scripts(row, code, step_id):
    if not step_id.endswith('.1') or row['step']['step_number'] != step_id:
        raise BenchError('この採点器は前段の生成結果を必要としない第1小問だけを扱います。')
    tests = row['step']['test_cases']
    if not isinstance(tests, list) or not tests or not all(isinstance(item, str) for item in tests):
        raise BenchError('科学小問の固定テストが不足しています。')
    bootstrap = ("import sys;sys.path.insert(0,'/vendor')\nfrom scicode.parse.parse import process_hdf5_to_tuple\n"
                 + f"targets=process_hdf5_to_tuple({step_id!r},{len(tests)},'/data/test_data_cleaned.h5')\n"
                 + f"assert len(targets)=={len(tests)}\n")
    preflight = bootstrap + "print('BEACON_TARGETS_READY',flush=True)\n"
    solution = row['required_dependencies'] + '\n' + code + '\n' + bootstrap
    for index, test in enumerate(tests): solution += f'\ntarget=targets[{index}]\n{test}\n'
    return preflight, solution


def image_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'sha256:[0-9a-f]{64}', value):
        raise BenchError('採点イメージはローカルの不変なimage SHAで指定してください。')
    return value


def grade(config, payload):
    if sys.platform != 'linux': raise BenchError('科学コードの実行先は専用のLinux採点ホストにしてください。')
    locked_files(config)
    if set(config['images']) != {'2024', '2025'}: raise BenchError('固定した2環境が必要です。')
    step_id = payload['grader']['native_id']
    vendor = Path(config['vendor_dir'])
    vendor_files = list(vendor.rglob('*.py'))
    if not vendor_files: raise BenchError('科学採点の固定vendorコードがありません。')
    require_locked(config, [config['cases'][step_id], config['extraction_source'], Path(config['data_dir']) / 'test_data_cleaned.h5', *vendor_files])
    row = read_json(Path(config['cases'][step_id]))
    if payload['grader']['case_asset_sha256'] != file_sha(Path(config['cases'][step_id])):
        raise BenchError('ケースと科学採点資産の版が異なります。')
    # 1800 seconds is the pinned SciCode native subprocess limit, not a task
    # generation limit. Require an explicit source receipt for that rule.
    if config.get('native_timeout_seconds') != 1800 or not config.get('native_timeout_source_sha256'):
        raise BenchError('SciCode固有の実行時間規則を固定してください。')
    code = extracted(Path(config['extraction_source']).read_text(encoding='utf-8'), payload['answer'])
    preflight, solution = scripts(row, code, step_id)
    folder = Path(config['output_root']).resolve() / ('science-' + uuid.uuid4().hex)
    folder.mkdir(parents=True, exist_ok=False); folder.chmod(0o755)
    for name, text in [('preflight.py', preflight), ('solution.py', solution)]:
        path = folder / name; path.write_text(text, encoding='utf-8'); path.chmod(0o644)
    write_once(folder / 'input.json', payload)
    # Preflight remains a different process from generated code. A missing
    # cleaned HDF5 group must not be reported as a model's incorrect answer.
    driver = "import subprocess,sys;r=subprocess.run([sys.executable,'/request/preflight.py'],timeout=1800);print('BEACON_PREFLIGHT_EXIT='+str(r.returncode),flush=True);sys.exit(90) if r.returncode else None;sys.exit(subprocess.run([sys.executable,'/request/solution.py'],timeout=1800).returncode)"
    environments = {}
    def command(args): return subprocess.run(args, capture_output=True, text=True, encoding='utf-8', timeout=45)
    def interrupted(signum, frame): raise InterruptedError('Scientific grader interrupted')
    previous = {sig: signal.signal(sig, interrupted) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        for era in ('2024', '2025'):
            name = 'beaconbench-science-' + uuid.uuid4().hex
            args = ['docker', 'run', '-d', '--pull=never', '--name', name, '--label', 'io.beaconbench.owner=' + name,
                    '--network=none', '--read-only', '--user', '65534:65534', '--cap-drop=ALL', '--security-opt=no-new-privileges',
                    '--tmpfs', '/tmp:rw,nosuid,nodev,mode=1777', '--env', 'HOME=/tmp',
                    '--mount', f'type=bind,source={folder},target=/request,readonly',
                    '--mount', f'type=bind,source={Path(config["vendor_dir"]).resolve()},target=/vendor,readonly',
                    '--mount', f'type=bind,source={Path(config["data_dir"]).resolve()},target=/data,readonly']
            resources = config.get('host_guards', {})
            if resources and not config.get('host_guards_policy'): raise BenchError('ホスト保護の制限を使う場合は比較条件に記録してください。')
            for key, value in resources.items():
                if key not in {'memory', 'memory-swap', 'cpus', 'pids-limit'} or not re.fullmatch(r'[0-9.]+[gmk]?', str(value)):
                    raise BenchError('科学採点のホスト保護条件が不正です。')
                args += ['--' + key, str(value)]
            args += [image_id(config['images'][era]), 'python', '-c', driver]
            write_once(folder / (era + '.invocation.json'), {'utc': utc(), 'argv': args})
            created = command(args)
            if created.returncode: raise BenchError('科学採点コンテナを開始できませんでした。')
            cid = created.stdout.strip(); started = time.monotonic()
            write_once(folder / (era + '.container.json'), {'id': cid, 'name': name})
            def inspect():
                inspected = command(['docker', 'inspect', cid])
                if inspected.returncode: raise BenchError('所有する科学採点コンテナを確認できません。')
                info = json.loads(inspected.stdout)[0]
                if info['Config'].get('Labels', {}).get('io.beaconbench.owner') != name: raise BenchError('科学採点コンテナの所有情報が異なります。')
                return info
            watchdog = False
            try:
                while True:
                    info = inspect()
                    if not info['State']['Running']: break
                    # Preflight and target execution each have their own
                    # process boundary. This watchdog is infrastructure-only.
                    if time.monotonic() - started > 3630:
                        watchdog = True; command(['docker', 'stop', '--time', '2', cid]); info = inspect(); break
                    time.sleep(.2)
                logs = command(['docker', 'logs', cid])
                (folder / (era + '.stdout.txt')).write_text(logs.stdout, encoding='utf-8')
                (folder / (era + '.stderr.txt')).write_text(logs.stderr, encoding='utf-8')
                state = info['State']
                preflight_results = [line.partition('=')[2] for line in logs.stdout.splitlines() if line.startswith('BEACON_PREFLIGHT_EXIT=')]
                infra = bool(watchdog or state.get('OOMKilled') or not preflight_results or preflight_results[0] != '0')
                environments[era] = {'passed': state['ExitCode'] == 0 and not infra, 'infrastructure_error': infra,
                                     'state': state, 'seconds': time.monotonic() - started, 'image_id': config['images'][era]}
                write_once(folder / (era + '.result.json'), environments[era])
            finally:
                info = inspect()
                if info['State']['Running']: command(['docker', 'stop', '--time', '2', cid])
                write_once(folder / (era + '.cleanup.json'), inspect())
                command(['docker', 'rm', cid])
        result = normalize('scicode-verified', {'step_id': step_id, 'per_environment': environments}, step_id)
        result['native_artifact'] = str(folder)
        write_once(folder / 'result.json', result)
        return result
    finally:
        for sig, handler in previous.items(): signal.signal(sig, handler)


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--config', type=Path, required=True)
    args = parser.parse_args()
    try: result = grade(read_json(args.config), json.load(sys.stdin))
    except (BenchError, OSError, subprocess.SubprocessError, ValueError, KeyError) as error:
        result = {'status': 'infrastructure_error', 'message': str(error) if isinstance(error, BenchError) else '科学採点器の固定資産または実行環境に問題があります。'}
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))


if __name__ == '__main__': main()
