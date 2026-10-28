"""Prepare native Japanese AtCoder cases against an operator-supplied LCB policy.

Never commits statements/tests and never labels this development subset as the
full official benchmark. Network fetches happen only through this explicit CLI.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.request import Request, urlopen

from beaconbench.util import BenchError, digest, file_sha, read_json, utc, write_once


class JapaneseStatement(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.depth = None
        self.parts = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if self.depth is None and 'lang-ja' in attributes.get('class', '').split():
            self.depth = len(self.stack)
        if self.depth is not None and tag in {'p', 'h2', 'h3', 'pre', 'li', 'section', 'br'}:
            self.parts.append('\n')
        if tag not in {'br', 'img', 'hr', 'input', 'meta', 'link', 'wbr', 'source'}:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in self.stack:
            index = len(self.stack) - 1 - self.stack[::-1].index(tag)
            self.stack = self.stack[:index]
            if self.depth is not None:
                if tag in {'p', 'h2', 'h3', 'pre', 'li', 'section'}:
                    self.parts.append('\n')
                if len(self.stack) <= self.depth:
                    self.depth = None

    def handle_data(self, data):
        if self.depth is not None and not {'script', 'style'}.intersection(self.stack):
            self.parts.append(data)

    def text(self):
        text = ''.join(self.parts).replace('\r\n', '\n').replace('\r', '\n')
        text = re.sub(r'\n[ \t]+', '\n', text)
        text = re.sub(r'\n{3,}', '\n\n', text).strip()
        if not all(label in text for label in ['問題文', '制約', '入力', '出力']):
            raise BenchError('公式日本語の問題文を抽出できませんでした。')
        return text


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--policy-dir', type=Path, required=True)
    parser.add_argument('--executor-id', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cases', nargs='+', required=True)
    parser.add_argument('--suite-id', default='lcb-atcoder-ja-development-v1')
    args = parser.parse_args()
    policy = read_json(args.policy_dir / 'policy.json')
    manifest = file_sha(args.policy_dir / 'FILE-SHA256.json')
    cache = args.output.parent / 'source-cache'
    cache.mkdir(parents=True, exist_ok=True)
    cases, receipts = [], []
    for source_id in args.cases:
        if not re.fullmatch(r'abc[0-9]+_[a-z]', source_id):
            raise BenchError('この取込器はAtCoder ABCの日本語原文だけに対応しています。')
        matches = [key for key, item in policy['lcb_tasks'].items() if item['source_id'] == source_id]
        if len(matches) != 1:
            raise BenchError('固定採点器に対応する問題がありません。')
        case_id = matches[0]
        url = f'https://atcoder.jp/contests/{source_id.split("_")[0]}/tasks/{source_id}?lang=ja'
        with urlopen(Request(url, headers={'User-Agent': 'BeaconBench source preparation'}), timeout=30) as response:
            raw = response.read()
        html_sha = hashlib.sha256(raw).hexdigest()
        html_path = cache / (html_sha + '.html')
        if not html_path.exists():
            html_path.write_bytes(raw)
        extractor = JapaneseStatement()
        extractor.feed(raw.decode('utf-8'))
        statement = extractor.text()
        receipt = {'source_id': source_id, 'url': url, 'retrieved_at': utc(), 'html_sha256': html_sha,
                   'statement_sha256': hashlib.sha256(statement.encode()).hexdigest(), 'language': 'ja',
                   'adaptation': 'native-ja; block-preserving HTML text extraction; no translation',
                   'tests_sha256': file_sha(args.policy_dir / policy['lcb_tasks'][case_id]['sample'])}
        receipt_key = digest({key: value for key, value in receipt.items() if key != 'retrieved_at'})
        receipt_path = cache / (receipt_key + '.receipt.json')
        if receipt_path.exists():
            previous = read_json(receipt_path)
            if {key: value for key, value in previous.items() if key != 'retrieved_at'} != {key: value for key, value in receipt.items() if key != 'retrieved_at'}:
                raise BenchError('保存済みの取得証拠が変更されています。')
            receipt = previous
        write_once(receipt_path, receipt)
        receipts.append(receipt)
        cases.append({'id': case_id, 'title': source_id + '（公式日本語）', 'category': 'code_generation',
                      'generation': {'adapter': 'chat', 'messages': [
                          {'role': 'system', 'content': '競技プログラミングの問題をPython 3で解いてください。標準入力から読み取り、標準出力へ答えを出すプログラムを、最後のpythonコードブロックに示してください。'},
                          {'role': 'user', 'content': statement}]},
                      'grader': {'kind': 'command', 'executor_id': args.executor_id, 'policy': policy['policy_id'],
                                 'policy_manifest_sha256': manifest}})
    pack = {'id': args.suite_id, 'name': f'LCB日本語・動作検証 {len(cases)}問', 'language': 'ja', 'track': 'development',
            'source': {'name': 'LiveCodeBench / AtCoder native Japanese development subset',
                       'revision': policy['source_revisions']['livecodebench']['revision'],
                       'data_revision': policy['source_revisions']['livecodebench_data']['revision'],
                       'policy_manifest_sha256': manifest, 'receipts': receipts,
                       'score_label': 'Japanese development subset; not the official full benchmark score'}, 'cases': cases}
    write_once(args.output, pack)
    print(json.dumps({'prepared': len(cases), 'output': str(args.output), 'controls_required': True}, ensure_ascii=False))


if __name__ == '__main__':
    main()
