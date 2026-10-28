"""Private connection registry and immutable versions, shared by UI and CLI."""
from __future__ import annotations

import copy
import ipaddress
import math
import os
import re
import threading
from pathlib import Path
from urllib.parse import urlsplit

from .util import BenchError, atomic_json, digest, file_lock, positive_int, read_json, slug, write_once

DEFAULT_RUN_SETTINGS = {
    'trials_per_case': 1,
    'seed_policy': {'base_seed': 42, 'derivation': 'sha256-case-revision-trial-v1', 'trial_index_origin': 0},
    'aggregation_policy': 'all-trials',
}
DEFAULT_PRESET = {
    'id': 'standard', 'name': '基準設定', 'temperature': 1.0, 'top_p': 0.95,
    'top_k': -1, 'min_p': 0.0, 'presence_penalty': 0.0, 'frequency_penalty': 0.0,
    'repetition_penalty': 1.0, 'context_tokens': 262144, 'output_tokens': 131072,
    'thinking': 'native-maximum',
}
_REGISTRY_LOCK = threading.RLock()


def validate_settings(value: dict) -> dict:
    if not isinstance(value, dict) or set(value) != set(DEFAULT_RUN_SETTINGS):
        raise BenchError('実行設定の項目を確認してください。')
    positive_int(value['trials_per_case'], '試行回数')
    policy = value['seed_policy']
    if not isinstance(policy, dict) or set(policy) != set(DEFAULT_RUN_SETTINGS['seed_policy']):
        raise BenchError('seed方針の項目を確認してください。')
    if type(policy['base_seed']) is not int or not 0 <= policy['base_seed'] <= 2147483647:
        raise BenchError('基底seedは0〜2147483647の整数で指定してください。')
    if policy['derivation'] != DEFAULT_RUN_SETTINGS['seed_policy']['derivation'] or policy['trial_index_origin'] != 0:
        raise BenchError('未対応のseed方針です。')
    if value['aggregation_policy'] != 'all-trials':
        raise BenchError('すべての試行を集計してください。')
    return copy.deepcopy(value)


def validate_preset(value: dict) -> dict:
    if not isinstance(value, dict) or set(value) != set(DEFAULT_PRESET):
        raise BenchError('生成プリセットに不明または不足している項目があります。')
    slug(value['id'])
    if not isinstance(value['name'], str) or not value['name'].strip():
        raise BenchError('プリセット名を入力してください。')
    limits = {'temperature': (0, 2), 'top_p': (0, 1), 'min_p': (0, 1),
              'presence_penalty': (-2, 2), 'frequency_penalty': (-2, 2), 'repetition_penalty': (0, math.inf)}
    for key, (low, high) in limits.items():
        item = value[key]
        if type(item) not in (int, float) or not math.isfinite(item) or not low <= item <= high:
            raise BenchError('生成設定の数値が不正です: ' + key)
    if value['top_p'] <= 0 or value['repetition_penalty'] <= 0:
        raise BenchError('top-pとrepetition penaltyは0より大きくしてください。')
    if type(value['top_k']) is not int or value['top_k'] not in (-1, 0) and value['top_k'] < 1:
        raise BenchError('top-kは-1・0（無効）または正の整数で指定してください。')
    positive_int(value['context_tokens'], 'コンテキスト')
    positive_int(value['output_tokens'], '最大出力')
    if value['output_tokens'] >= value['context_tokens']:
        raise BenchError('入力の余地を残すため、出力をコンテキストより小さくしてください。')
    if value['thinking'] not in {'native-maximum', 'off'}:
        raise BenchError('未対応の思考設定です。')
    return copy.deepcopy(value)


def validate_model(value: dict) -> dict:
    required = {'id', 'name', 'api_model', 'base_url', 'api_key_env', 'context_tokens', 'output_tokens', 'adapter', 'engine', 'revision', 'cache_policy'}
    if not isinstance(value, dict) or set(value) != required:
        raise BenchError('モデル接続設定の項目を確認してください。')
    slug(value['id'])
    for key in ('name', 'api_model', 'revision'):
        if not isinstance(value[key], str) or not value[key].strip():
            raise BenchError('モデル名・APIモデルID・識別版を指定してください。')
    parsed = urlsplit(value['base_url'])
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise BenchError('認証情報を含まないHTTP(S)のAPI URLを指定してください。')
    # External paid endpoints require an explicit operator launch flag.
    local = parsed.hostname.lower() == 'localhost'
    try:
        address = ipaddress.ip_address(parsed.hostname)
        local = local or address.is_loopback or address.is_private
    except ValueError:
        local = local or parsed.hostname.lower().endswith('.local')
    if not local and os.environ.get('BEACON_ALLOW_EXTERNAL_ENDPOINTS') != '1':
        raise BenchError('外部APIは無効です。ローカル接続先を指定してください。')
    key = value['api_key_env']
    if not isinstance(key, str) or key and not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key):
        raise BenchError('APIキーは環境変数名で指定してください。')
    positive_int(value['context_tokens'], 'モデルのコンテキスト')
    positive_int(value['output_tokens'], 'モデルの最大出力')
    if value['output_tokens'] > value['context_tokens']:
        raise BenchError('モデルの最大出力はコンテキスト以内で指定してください。')
    if value['adapter'] not in {'openai', 'vllm', 'llamacpp'}:
        raise BenchError('未対応のモデル接続方式です。')
    if not isinstance(value['engine'], dict) or not isinstance(value['cache_policy'], str):
        raise BenchError('エンジン設定とキャッシュ方針を指定してください。')
    if set(value['engine']) - {'label', 'reasoning_effort', 'kv', 'mtp', 'resource_group', 'weight_edit_sha256', 'build', 'quantization', 'context_policy'}:
        raise BenchError('エンジン欄には公開可能な構成情報だけを指定してください。')
    for key, item in value['engine'].items():
        if key == 'mtp':
            if type(item) is not int or item < 0:
                raise BenchError('MTPは0以上の整数で指定してください。')
        elif not isinstance(item, str):
            raise BenchError('エンジンの識別情報は文字列で指定してください。')
    if 'context_policy' in value['engine']:
        from .context_budget import POLICIES
        if value['adapter'] != 'vllm' or value['engine']['context_policy'] not in POLICIES:
            raise BenchError('未受入のコンテキスト計数方針です。')
    result = copy.deepcopy(value)
    result['base_url'] = result['base_url'].rstrip('/')
    return result


class Registry:
    def __init__(self, root: Path):
        self.root = root
        self.path = root / 'registry.json'
        with _REGISTRY_LOCK, file_lock(root / 'locks/registry.lock'):
            if not self.path.exists():
                atomic_json(self.path, {'models': [], 'presets': [DEFAULT_PRESET], 'suites': [], 'executors': [], 'run_defaults': DEFAULT_RUN_SETTINGS})

    def read(self) -> dict:
        with _REGISTRY_LOCK:
            return read_json(self.path)

    def save(self, kind: str, item: dict) -> dict:
        if kind == 'models':
            item = validate_model(item)
        elif kind == 'presets':
            item = validate_preset(item)
        elif kind not in {'suites', 'executors'}:
            raise BenchError('未対応の登録種別です。')
        slug(item['id'])
        version = digest(item)
        with _REGISTRY_LOCK, file_lock(self.root / 'locks/registry.lock'):
            state = self.read()
            write_once(self.root / 'registry-versions' / kind / (version + '.json'), item)
            rows = [row for row in state[kind] if row['id'] != item['id']]
            state[kind] = rows + [item]
            atomic_json(self.path, state)
        return {**item, 'config_revision': version}

    def get(self, kind: str, identity: str) -> dict:
        for item in self.read()[kind]:
            if item['id'] == identity:
                return item
        raise BenchError('登録項目が見つかりません: ' + identity)

    def public_model(self, value: dict) -> dict:
        return {key: copy.deepcopy(value[key]) for key in ('id', 'name', 'api_model', 'context_tokens', 'output_tokens', 'adapter', 'engine', 'revision', 'cache_policy')} | {
            'auth_configured': not value['api_key_env'] or bool(os.environ.get(value['api_key_env'])),
            'config_revision': digest({key: row for key, row in value.items() if key != 'config_revision'}),
        }


def request_parameters(model: dict, preset: dict) -> dict:
    if preset['context_tokens'] != model['context_tokens']:
        raise BenchError('コンテキストは起動済みモデルの容量と揃えてください。変更には別のモデル構成を登録します。')
    if preset['output_tokens'] > model['output_tokens']:
        raise BenchError('要求する出力が登録済みモデルの容量を超えています。')
    result = {'model': model['api_model'], 'temperature': preset['temperature'], 'top_p': preset['top_p'], 'max_tokens': preset['output_tokens']}
    if model['adapter'] in {'vllm', 'llamacpp'}:
        result.update(top_k=preset['top_k'], min_p=preset['min_p'], presence_penalty=preset['presence_penalty'],
                      frequency_penalty=preset['frequency_penalty'], repetition_penalty=preset['repetition_penalty'])
        if model['adapter'] == 'llamacpp':
            result['repeat_penalty'] = result.pop('repetition_penalty')
            result['top_k'] = 0 if preset['top_k'] < 1 else preset['top_k']
        result['chat_template_kwargs'] = {'enable_thinking': preset['thinking'] != 'off'}
        if preset['thinking'] != 'off':
            effort = model['engine'].get('reasoning_effort')
            if not effort:
                raise BenchError('モデル固有の思考設定が未登録です。')
            result['reasoning_effort'] = effort
    else:
        if preset['thinking'] != 'off' or preset['top_k'] > 0 or preset['min_p'] != 0 or preset['repetition_penalty'] != 1:
            raise BenchError('汎用APIで未確認の設定です。対応する接続方式を選択してください。')
        result.update(presence_penalty=preset['presence_penalty'], frequency_penalty=preset['frequency_penalty'])
    return result
