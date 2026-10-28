"""Resolve helper instruments and native executors before any model request."""
from __future__ import annotations

import copy

from ..calibration import verify_instrument
from ..config import request_parameters, validate_preset
from ..util import BenchError, digest
from .native import locked_files


def executor_fingerprint(value):
    return digest({key: item for key, item in value.items() if key not in {'config_revision', 'qualified', 'qualification'}})


def resolve_case(store, case, track):
    generation = case['generation']
    helpers = {}
    configs = dict(generation.get('helpers', {}))
    for role, config in case['grader'].get('helpers', {}).items():
        if role in configs and configs[role] != config:
            raise BenchError('生成と採点で同じ補助役の設定が異なります。')
        configs[role] = config
    for role, config in configs.items():
        if role not in {'customer', 'converter', 'judge', 'intent_judge', 'language_judge'}:
            raise BenchError('未対応の補助モデルの役割です。')
        model = store.registry.get('models', config['model_id'])
        preset = validate_preset(config['preset'])
        verify_instrument(store.root, config['instrument'], model, preset, config['policy'], allow_fixture=track == 'development')
        helpers[role] = {'model': copy.deepcopy(model), 'preset': preset,
                         'request_parameters': request_parameters(model, preset), 'policy': config['policy']}
    adapter = generation.get('adapter', 'chat')
    if adapter == 'dialogue' and 'customer' not in helpers:
        raise BenchError('営業対話には較正済みの顧客役が必要です。')
    if case['grader']['kind'] == 'salesllm_ja' and not {'judge', 'intent_judge', 'language_judge'} <= set(helpers):
        raise BenchError('営業採点には較正済みの品質・購買意向・言語の判定役が必要です。')
    if case['grader']['kind'] == 'salesllm_ja':
        from .sales import validate_policy
        validate_policy(case['grader'], helpers)
    executor = None
    if adapter == 'native':
        executor = store.registry.get('executors', generation['executor_id'])
        locked_files(executor)
        if executor.get('purpose') != 'native_generation' or executor.get('family') != generation['family']:
            raise BenchError('native実行器とケースの系列が異なります。')
        if case['grader'].get('native_executor_fingerprint') != executor_fingerprint(executor):
            raise BenchError('採点controlが生成用native実行器の構成へ結び付いていません。')
        expected = set(executor.get('required_roles', [])) - {'target'}
        if not expected <= set(helpers):
            raise BenchError('native実行器が必要とする補助モデルが不足しています。')
    return helpers, copy.deepcopy(executor)
