"""Own a disposable offline native Frontier judge on the Linux grading host."""
from __future__ import annotations

import argparse
import json
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

from .adapters.native import locked_files, require_locked
from .frontier_executor import checked_result, extract_cpp
from .util import BenchError, read_json, utc, write_once


def grade(config, payload):
    if sys.platform != 'linux':
        raise BenchError('Frontierの実行先はLinux採点ホストにしてください。')
    locked_files(config)
    require_locked(config, [config['client_file']])
    image = config['image_id']
    if not isinstance(image, str) or not __import__('re').fullmatch(r'sha256:[0-9a-f]{64}', image):
        raise BenchError('Frontier imageは不変なローカルimage SHAで固定してください。')
    spec = payload['grader']; task = config['problems'][spec['native_id']]
    if spec['score_formula_sha256'] != task['score_formula_sha256']:
        raise BenchError('Frontierのケースと得点式が一致しません。')
    name = 'beaconbench-frontier-' + uuid.uuid4().hex
    folder = Path(config['output_root']) / name
    write_once(folder / 'input.json', payload)
    cid = None
    def command(argv, **kw):
        return subprocess.run(argv, capture_output=True, text=True, encoding='utf-8', timeout=kw.pop('timeout', 45), **kw)
    def inspect():
        info = command(['docker', 'inspect', name])
        if info.returncode: raise BenchError('所有するFrontierコンテナを確認できません。')
        value = json.loads(info.stdout)[0]
        if value['Config'].get('Labels', {}).get('io.beaconbench.owner') != name:
            raise BenchError('Frontierコンテナの所有情報が異なります。')
        return value
    def interrupted(signum, frame): raise InterruptedError('Frontier grader interrupted')
    previous = {sig: signal.signal(sig, interrupted) for sig in (signal.SIGINT, signal.SIGTERM)}
    started = time.monotonic()
    args = ['docker', 'run', '-d', '--pull=never', '--name', name, '--label', 'io.beaconbench.owner=' + name,
            '--network=none', '--privileged', '--memory=16g', '--memory-swap=16g', '--cpus=1', '--shm-size=1g',
            '--env', 'GJ_PARALLELISM=1', '--env', 'JUDGE_WORKERS=1', '--env', 'SAVE_OUTPUTS=true',
            '--mount', f'type=bind,source={Path(config["problems_root"]).resolve()},target=/app/problems,readonly',
            '--mount', f'type=bind,source={Path(config["client_file"]).resolve().parent},target=/support,readonly', image]
    write_once(folder / 'invocation.json', {'utc': utc(), 'argv': args,
                'isolation': 'owned offline judge; native go-judge isolation; no host Docker socket; readonly native test mount'})
    try:
        created = command(args)
        if created.returncode: raise BenchError('Frontierのnative環境を開始できません。')
        cid = created.stdout.strip(); write_once(folder / 'container.json', {'id': cid, 'name': name})
        ready = False
        for _ in range(120):
            health = command(['docker', 'exec', cid, 'node', '-e', "fetch('http://127.0.0.1:8081/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"], timeout=10)
            if health.returncode == 0: ready = True; break
            if not inspect()['State']['Running']: break
            time.sleep(1)
        if not ready: raise BenchError('Frontierのnative APIが起動しませんでした。')
        # Generated C++ is data to the native judge; it is never run on the host.
        request = {'pid': spec['native_id'], 'code': extract_cpp(payload['answer'])}
        judged = command(['docker', 'exec', '-i', cid, 'node', '/support/' + Path(config['client_file']).name], input=json.dumps(request), timeout=None)
        (folder / 'client.stdout.txt').write_text(judged.stdout, encoding='utf-8')
        (folder / 'client.stderr.txt').write_text(judged.stderr, encoding='utf-8')
        if judged.returncode: raise BenchError('Frontierのnative clientが終了しました。')
        native = json.loads(judged.stdout)
        write_once(folder / 'official.json', native)
        result = checked_result(native, pid=spec['native_id'], expected_test_count=task['test_count'], score_formula_sha256=task['score_formula_sha256'])
        result.update(native_artifact=str(folder), executor_wall_seconds=time.monotonic() - started)
        write_once(folder / 'result.json', result)
        return result
    finally:
        try:
            if cid:
                info = inspect()
                logs = command(['docker', 'logs', cid])
                (folder / 'container.stdout.txt').write_text(logs.stdout, encoding='utf-8')
                (folder / 'container.stderr.txt').write_text(logs.stderr, encoding='utf-8')
                if info['State']['Running']: command(['docker', 'stop', '--time', '2', cid])
                write_once(folder / 'cleanup-inspect.json', inspect())
                command(['docker', 'rm', cid])
        finally:
            for sig, handler in previous.items(): signal.signal(sig, handler)


def main():
    p = argparse.ArgumentParser(); p.add_argument('--config', type=Path, required=True)
    args = p.parse_args()
    try: result = grade(read_json(args.config), json.load(sys.stdin))
    except (BenchError, OSError, KeyError, ValueError, subprocess.SubprocessError) as error:
        result = {'status': 'infrastructure_error', 'message': str(error) if isinstance(error, BenchError) else 'Frontierの実行環境の確認が必要です。'}
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))


if __name__ == '__main__': main()
