"""Private, content-addressed case packs; unprepared draft suites stay disabled."""
from __future__ import annotations

import copy
from pathlib import Path

from .config import Registry
from .util import BenchError, digest, inside, read_json, slug, write_once


def install_pack(root: Path, pack: dict) -> dict:
    if not isinstance(pack, dict) or not {'id', 'name', 'language', 'track', 'source', 'cases'}.issubset(pack):
        raise BenchError('ケースパックの必須項目が不足しています。')
    slug(pack['id'])
    if pack['track'] not in {'diagnostic', 'development', 'benchmark', 'legacy'}:
        raise BenchError('評価系列を指定してください。')
    if not pack['cases'] or not isinstance(pack['cases'], list):
        raise BenchError('ケースがありません。')
    identities = set()
    for case in pack['cases']:
        if not isinstance(case.get('id'), str) or not case['id'] or case['id'] in identities:
            raise BenchError('ケースIDが空、または重複しています。')
        identities.add(case['id'])
        if not {'title', 'category', 'generation', 'grader'}.issubset(case):
            raise BenchError('ケースの生成条件または採点条件がありません。')
        adapter = case['generation'].get('adapter', 'chat')
        if adapter not in {'chat', 'native', 'dialogue'}:
            raise BenchError('未対応の生成アダプターです。')
        allowed = {'chat': {'adapter', 'messages'},
                   'native': {'adapter', 'executor_id', 'family', 'native_id', 'domain', 'helpers', 'policy', 'language_pack_ref'},
                   'dialogue': {'adapter', 'script', 'helpers', 'policy', 'native_end_conditions', 'language_pack_ref'}}[adapter]
        if set(case['generation']) - allowed:
            raise BenchError('このアダプターで未対応の生成設定があります。黙って無視せず取込を停止しました。')
        if case['grader'].get('kind') not in {'diagnostic_exact', 'command', 'salesllm_ja'}:
            raise BenchError('未対応の採点器です。')
        if pack['track'] != 'diagnostic' and case['grader']['kind'] == 'diagnostic_exact':
            raise BenchError('接続診断の採点器は通常ベンチマークへ使えません。')
        if case['generation'].get('adapter', 'chat') == 'chat':
            messages = case['generation'].get('messages')
            if not isinstance(messages, list) or not messages:
                raise BenchError('入力メッセージがありません。')
            if not all(isinstance(row, dict) and row.get('role') in {'system', 'user', 'assistant'} and isinstance(row.get('content'), str) for row in messages):
                raise BenchError('単発入力のメッセージ形式が不正です。')
        elif adapter == 'native':
            if not all(isinstance(case['generation'].get(key), str) and case['generation'][key] for key in ('executor_id', 'family', 'native_id')):
                raise BenchError('nativeの実行器・系列・元の問題IDが不足しています。')
        else:
            script = case['generation'].get('script', {})
            if script.get('language') != pack['language'] or not all(isinstance(script.get(key), str) and script[key] for key in ('assistant_system_prompt', 'customer_system_prompt', 'trigger_sentence')):
                raise BenchError('営業対話の同じ言語の顧客・営業・開始文が不足しています。')
            end = case['generation'].get('native_end_conditions', {})
            if 'generated_turn_limit' in end and (type(end['generated_turn_limit']) is not int or end['generated_turn_limit'] < 1 or not end.get('source')):
                raise BenchError('対話上限を使う場合は、公式の終了条件と出典を指定してください。')
        if case['grader'].get('kind') == 'salesllm_ja':
            if adapter != 'dialogue' or not {'quality_prompt', 'intent_prompt', 'language_prompt', 'native_id'} <= set(case['grader']):
                raise BenchError('営業採点には日本語対話と固定した品質・意向・言語の判定文が必要です。')
        from .evaluation_contracts import validate_case
        validate_case(case)
        from .vita_payment import validate as validate_payment
        validate_payment(case['generation'])
        from .vita_goals import validate as validate_goals
        validate_goals(case['generation'])
        # Retrieval timestamps, other cases' receipts, suite size and grader
        # policy are not generation inputs. Each case may pin extra environment
        # identity explicitly; its actual messages are always hashed in full.
        provenance = case.get('source', {key: pack['source'][key] for key in ('name', 'revision', 'data_revision') if key in pack['source']})
        case['generation_revision'] = digest({'generation': case['generation'], 'source': provenance, 'language': pack['language']})
        case['grader_revision'] = digest(case['grader'])
    asset = digest(pack)
    write_once(root / 'assets' / 'packs' / (asset + '.json'), pack)
    entry = {'id': pack['id'], 'name': pack['name'], 'language': pack['language'], 'track': pack['track'],
             'case_count': len(pack['cases']), 'pack_sha256': asset, 'source': copy.deepcopy(pack['source']),
             'execution_ready': True}
    return Registry(root).save('suites', entry)


def load_pack(root: Path, suite: dict) -> dict:
    path = inside(root, 'assets/packs/' + suite['pack_sha256'] + '.json')
    pack = read_json(path)
    if digest(pack) != suite['pack_sha256']:
        raise BenchError('固定したケース資産が変更されています。')
    return pack


def install_diagnostic(root: Path) -> dict:
    # Own authored controls, always separate from capability benchmark scores.
    return install_pack(root, {
        'id': 'connection-check-ja', 'name': '接続・計測の動作確認', 'language': 'ja', 'track': 'diagnostic',
        'source': {'name': 'BeaconBench authored connection control', 'revision': '1', 'license': 'MIT'},
        'cases': [{'id': 'ja-exact-42', 'title': '日本語の短い応答を確認', 'category': 'connection_check',
                   'generation': {'adapter': 'chat', 'messages': [{'role': 'user', 'content': '21と21の和を、数字だけで回答してください。'}]},
                   'grader': {'kind': 'diagnostic_exact', 'expected': '42', 'policy': 'strip-then-exact-v1'}}],
    })


def draft_suites() -> list[dict]:
    return [{'id': identity + '.v0.draft', 'name': label, 'language': 'ja', 'case_count': count,
             'track': 'benchmark', 'execution_ready': False,
             'unavailable_reason': '問題ID・日本語版・採点器の受入が未完了です。'}
            for identity, label, count in [('quick', 'Quick', 20), ('detailed', 'Detailed', 100)]]
