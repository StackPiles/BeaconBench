"""Explicit, read-only source fetching; no suite registration, model or scorer."""
import argparse
import time
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from beaconbench.util import BenchError, file_sha, read_json, utc, write_once
from prepare_lcb_ja import JapaneseStatement


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--selection', type=Path, default=Path('catalog/selection.v1.candidate.json'))
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--request-interval', type=float, default=2.5)
    args = parser.parse_args()
    if args.request_interval < 1: raise BenchError('公開元への要求間隔は1秒以上にしてください。')
    receipts = []
    last_request = None
    for row in read_json(args.selection)['cases']:
        if row['source_id'] != 'livecodebench': continue
        url = row['source']['official_ja_url']
        native = row['native_id']
        expected = f'https://atcoder.jp/contests/{native.split("_")[0]}/tasks/{native}?lang=ja'
        if url != expected: raise BenchError('Unexpected official Japanese URL')
        receipt_path = args.output_dir / native / 'receipt.json'
        if receipt_path.exists():
            receipt = read_json(receipt_path)
            for name, sha in receipt['files'].items():
                if file_sha(receipt_path.parent / name) != sha: raise BenchError('Cached source SHA changed')
        else:
            if last_request is not None: time.sleep(max(0, args.request_interval - (time.monotonic() - last_request)))
            last_request = time.monotonic()
            try:
                with urlopen(Request(url, headers={'User-Agent': 'BeaconBench source preparation'}), timeout=30) as response:
                    raw = response.read(4 * 1024 * 1024 + 1)
            except HTTPError as error:
                raise BenchError('公開元の取得応答: HTTP ' + str(error.code) + '。保存済みを保ち、時間を置いて再開してください。') from None
            if len(raw) > 4 * 1024 * 1024: raise BenchError('Unexpected source document size')
            parser = JapaneseStatement(); parser.feed(raw.decode('utf-8')); text = parser.text()
            receipt_path.parent.mkdir(parents=True, exist_ok=True)
            (receipt_path.parent / 'source.html').write_bytes(raw)
            (receipt_path.parent / 'statement.ja.txt').write_text(text, encoding='utf-8')
            receipt = {'id': row['id'], 'native_id': native, 'url': url, 'retrieved_at': utc(),
                       'selection_sha256': file_sha(args.selection), 'language': 'ja', 'adaptation': 'native source, not translated',
                       'content_reviewed': False, 'execution_ready': False,
                       'files': {name: file_sha(receipt_path.parent / name) for name in ('source.html', 'statement.ja.txt')}}
            write_once(receipt_path, receipt)
        receipts.append(receipt)
        print(native + ': cached Japanese source; content/test alignment review pending', flush=True)
    write_once(args.output_dir / 'manifest.json', {'cases': len(receipts), 'execution_ready': False, 'receipts': receipts})


if __name__ == '__main__': main()
