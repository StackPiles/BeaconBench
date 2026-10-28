"""JSON-line bridge to a pinned LCB policy inside disposable Linux containers.

Runs on the grading host. No model lifecycle operations, network, or code on
the controller. Third-party source and tests are supplied in a private bundle.
"""
from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

from .util import BenchError, file_sha, read_json, utc, write_once


def grade(config: dict, payload: dict) -> dict:
    for asset in config['lock_files']:
        if file_sha(Path(asset['path'])) != asset['sha256']:
            raise BenchError('Pinned native scoring asset changed')
    policy_root = Path(config['policy_dir']).resolve()
    spec = importlib.util.spec_from_file_location('beaconbench_native_lcb_policy', policy_root / 'lcb_policy.py')
    policy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(policy)
    manifest_sha = policy.verify_assets(policy_root)
    task_id = payload['case_id']
    sample = policy.load_sample(task_id, policy_root)
    if payload['grader'].get('policy_manifest_sha256') != manifest_sha:
        raise BenchError('Case and executor policy revisions differ')
    source = Path(config['extraction_source']).read_text(encoding='utf-8')
    node = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == 'extract_code')

    class Styles:
        CodeLLaMaInstruct = 'CodeLLaMaInstruct'
        GenericBase = 'GenericBase'

    namespace = {'LMStyle': Styles}
    exec(compile(ast.get_source_segment(source, node), 'pinned_extract_code.py', 'exec'), namespace)
    code = namespace['extract_code'](payload['answer'], None)
    folder = Path(config['output_root']).resolve() / ('grade-' + uuid.uuid4().hex)
    folder.mkdir(parents=True, exist_ok=False)
    request = folder / 'request.json'
    write_once(request, {'sample': sample, 'code': code, 'task_id': task_id, 'control': False})
    request.chmod(0o644)
    folder.chmod(0o755)
    (folder / 'extracted.py').write_text(code, encoding='utf-8')
    count = len(json.loads(sample['input_output'])['inputs'])
    # Native policy: 6 seconds/test, plus the upstream process allowance.
    timeout = (6 + 1) * count + 25
    image = config['image_id']
    if not image.startswith('sha256:') or len(image) != 71:
        raise BenchError('Use an immutable local image SHA, not a mutable tag')
    name = 'beaconbench-grade-' + uuid.uuid4().hex
    label = 'io.beaconbench.owner=' + name
    driver = "import sys;sys.stdin=open('/request/request.json');exec(compile(open('/worker/lcb_worker.py').read(),'/worker/lcb_worker.py','exec'))"
    args = ['docker', 'run', '-d', '--name', name, '--label', label, '--network=none',
            '--log-driver=local', '--log-opt', 'max-size=20m', '--log-opt', 'max-file=2',
            '--user', '65534:65534', '--read-only', '--cap-drop=ALL', '--security-opt=no-new-privileges',
            '--pids-limit=128', '--memory=8g', '--memory-swap=8g', '--cpus=1',
            '--tmpfs', '/tmp:rw,nosuid,nodev,size=512m,mode=1777', '--env', 'HOME=/tmp',
            '--mount', f'type=bind,source={policy_root},target=/support/policy,readonly',
            '--mount', f'type=bind,source={Path(config["worker_file"]).resolve().parent},target=/worker,readonly',
            '--mount', f'type=bind,source={folder},target=/request,readonly', image, 'python', '-c', driver]
    started = time.monotonic()
    write_once(folder / 'invocation.json', {'utc': utc(), 'args': args, 'payload': payload, 'native_timeout_seconds': timeout})
    cid = None

    def command(arguments):
        return subprocess.run(arguments, capture_output=True, text=True, encoding='utf-8', timeout=30)

    def inspect():
        result = command(['docker', 'inspect', name])
        if result.returncode:
            return None
        info = json.loads(result.stdout)[0]
        if info['Config'].get('Labels', {}).get('io.beaconbench.owner') != name:
            raise BenchError('Grader container ownership differs')
        return info

    def interrupted(signum, frame):
        raise InterruptedError('Grader interrupted')

    previous_signals = {sig: signal.signal(sig, interrupted) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        created = command(args)
        (folder / 'create.stderr.txt').write_text(created.stderr, encoding='utf-8')
        if created.returncode:
            raise BenchError('Could not start the grading container')
        cid = created.stdout.strip()
        write_once(folder / 'container.json', {'id': cid, 'name': name, 'image_id': image})
        timed_out = False
        while True:
            info = inspect()
            if info is None:
                raise BenchError('Grading container disappeared')
            if not info['State']['Running']:
                break
            if time.monotonic() - started > timeout:
                timed_out = True
                command(['docker', 'stop', '--time', '2', cid])
                info = inspect()
                break
            time.sleep(.2)
        logs = command(['docker', 'logs', cid])
        (folder / 'stdout.txt').write_text(logs.stdout, encoding='utf-8')
        (folder / 'stderr.txt').write_text(logs.stderr, encoding='utf-8')
        write_once(folder / 'inspect.json', info)
        markers = [line.split('COMPACT14_RESULT=', 1)[1] for line in logs.stdout.splitlines() if line.startswith('COMPACT14_RESULT=')]
        if timed_out or info['State'].get('OOMKilled') or not markers or info['State']['ExitCode'] != 0:
            result = {'status': 'infrastructure_error', 'message': 'Native grader did not complete',
                      'evidence': {'timed_out': timed_out, 'oom_killed': info['State'].get('OOMKilled'), 'exit_code': info['State']['ExitCode']}}
        else:
            native = json.loads(markers[-1])
            results = native['results']
            # LCB intentionally stops at the first failed/errored test. A
            # failure witness is conclusive; a partial all-true list is not.
            if not results or len(results) > count or len(results) < count and all(item is True for item in results):
                raise BenchError('Native grader returned incomplete test coverage')
            result = {'status': 'scored', 'passed': all(item is True for item in results), 'metric': 'all_tests_pass',
                      'metric_unit': 'binary', 'test_count': count, 'executed_tests': len(results),
                      'passed_tests': sum(item is True for item in results),
                      'test_coverage': 'full' if len(results) == count else 'native-stop-after-failure',
                      'evidence': {'native': native, 'image_id': image, 'policy_manifest_sha256': manifest_sha,
                                   'extraction_source_sha256': file_sha(Path(config['extraction_source'])),
                                   'container_id': cid, 'execution_host': 'configured-grading-host',
                                   'native_timeout_seconds': timeout}}
        result['executor_wall_seconds'] = time.monotonic() - started
        write_once(folder / 'result.json', result)
        return result
    finally:
        try:
            if cid:
                info = inspect()
                if info and info['State']['Running']:
                    command(['docker', 'stop', '--time', '2', cid])
                if info:
                    write_once(folder / 'cleanup-inspect.json', inspect())
                    command(['docker', 'rm', cid])
        finally:
            for sig, previous in previous_signals.items():
                signal.signal(sig, previous)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, required=True)
    args = parser.parse_args()
    try:
        result = grade(read_json(args.config), json.load(sys.stdin))
    except Exception as error:
        print(type(error).__name__ + ': ' + str(error), file=sys.stderr)
        result = {'status': 'infrastructure_error', 'message': 'See retained executor stderr and host evidence'}
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))


if __name__ == '__main__':
    main()
