"""UI and CLI share the exact same resolver, registry, worker and evidence store."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from .assets import install_diagnostic, install_pack
from .runner import Worker, launch, stop
from .server import serve
from .store import Store
from .transport import probe
from .util import BenchError, load_env, read_json


def parser():
    result = argparse.ArgumentParser(prog='beaconbench')
    result.add_argument('--data-dir', type=Path, default=Path('.beaconbench'))
    result.add_argument('--env-file', type=Path)
    commands = result.add_subparsers(dest='command', required=True)
    commands.add_parser('init')
    web = commands.add_parser('serve')
    web.add_argument('--host', default='127.0.0.1')
    web.add_argument('--port', type=int, default=18741)
    for name in ('model-add', 'preset-add', 'suite-import', 'executor-add', 'plan'):
        command = commands.add_parser(name)
        command.add_argument('config', type=Path)
    qualify = commands.add_parser('executor-qualify')
    qualify.add_argument('executor_id')
    qualify.add_argument('controls', type=Path)
    instrument = commands.add_parser('instrument-import')
    for name in ('model-id', 'preset', 'policy', 'expected', 'predictions'):
        instrument.add_argument('--' + name, type=str if name == 'model-id' else Path, required=True)
    instrument.add_argument('--fixture', action='store_true')
    check = commands.add_parser('probe')
    check.add_argument('model_id')
    review = commands.add_parser('evaluation-review')
    review.add_argument('run_id')
    review.add_argument('config', type=Path)
    subset = commands.add_parser('subset-prepare')
    subset.add_argument('config', type=Path)
    subset_worker = commands.add_parser('subset-worker')
    subset_worker.add_argument('run_id')
    for name, argument in [('run', 'plan_id'), ('status', 'run_id'), ('resume', 'run_id'), ('stop', 'run_id'), ('worker', 'run_id'), ('results', 'run_id')]:
        command = commands.add_parser(name)
        command.add_argument(argument)
    commands.add_parser('runs')
    return result


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    args = parser().parse_args(argv)
    if args.env_file:
        load_env(args.env_file)
    store = Store(args.data_dir)
    try:
        if args.command == 'serve':
            serve(store, args.host, args.port)
            return
        if args.command == 'init':
            result = {'initialized': True, 'diagnostic': install_diagnostic(store.root)}
        elif args.command in {'model-add', 'preset-add', 'executor-add'}:
            kind = {'model-add': 'models', 'preset-add': 'presets', 'executor-add': 'executors'}[args.command]
            result = store.registry.save(kind, read_json(args.config))
            if kind == 'models':
                result = store.registry.public_model(result)
        elif args.command == 'suite-import':
            result = install_pack(store.root, read_json(args.config))
        elif args.command == 'executor-qualify':
            from .qualification import qualify_executor
            result = asyncio.run(qualify_executor(store, args.executor_id, read_json(args.controls)))
        elif args.command == 'instrument-import':
            from .calibration import record_instrument
            result = record_instrument(store, args.model_id, read_json(args.preset), read_json(args.policy),
                                       args.expected, args.predictions, fixture=args.fixture)
        elif args.command == 'plan':
            result = store.public_plan(store.plan(read_json(args.config)))
        elif args.command == 'probe':
            result = asyncio.run(probe(store.registry.get('models', args.model_id)))
        elif args.command == 'run':
            result = {'run_id': launch(store, plan_id=args.plan_id)}
        elif args.command == 'resume':
            result = {'run_id': launch(store, resume_id=args.run_id)}
        elif args.command == 'stop':
            result = stop(store, args.run_id)
        elif args.command == 'status':
            result = store.status(args.run_id)
        elif args.command == 'runs':
            result = store.runs()
        elif args.command == 'results':
            result = read_json(store.run_path(args.run_id) / 'summary.json')
        elif args.command == 'evaluation-review':
            from .saved_reviews import run
            result = asyncio.run(run(store, args.run_id, read_json(args.config)))
        elif args.command == 'subset-prepare':
            from .subset_rerun import prepare
            result = prepare(store, read_json(args.config))
        elif args.command == 'subset-worker':
            from .subset_rerun import run
            result = run(store, args.run_id)
        elif args.command == 'worker':
            asyncio.run(Worker(store, args.run_id).run())
            return
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    except (BenchError, FileNotFoundError) as error:
        raise SystemExit(str(error)) from None
