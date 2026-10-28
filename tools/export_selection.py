"""Human-readable, standalone selection review; never reads model responses."""
import argparse
from collections import Counter
from html import escape
from pathlib import Path

from beaconbench.selection import validate_selection
from beaconbench.util import BenchError, file_sha, read_json

ROOT = Path(__file__).resolve().parents[1]
LABELS = {'babel-tau3': '規則・ツール · τ³', 'babel-deepplanning': '計画 · DeepPlanning', 'babel-vitabench': '複合業務 · VitaBench',
          'babel-claw': '調査・事務 · ClawEval', 'livecodebench': 'コード · LiveCodeBench', 'scicode-verified': '科学 · SciCode-Verified',
          'frontier-algorithmic': '最適化 · Frontier', 'salesllm-ja': '営業対話 · SalesLLM派生', 'crmarena-pro-sales-ja': '営業実務 · CRMArena',
          'swebench-pro-v2-ja': '実装修正 · SWE-bench Pro V2'}
REASONS = {
    'babel-tau3': '小売・知識検索・通信・航空の4領域。公開standard IDから一定のSHA順位で選定。公式日本語データを取得済みで、内容とtoolの整合を点検します。',
    'babel-deepplanning': '購買は公式3難易度から各2件、旅行は日数で分けた4件。制約の種類を分散させました。公式日本語DBも取得済みで、変換器・native環境の受入が必要です。',
    'babel-vitabench': '複数領域の連携を4件に厚くし、配送・店舗・旅行の単一領域も含めました。',
    'babel-claw': 'メール・予定・追客・議事録・在庫・調達・苦情・サポート・利益・報告を含む候補です。本文・graderを取得済みで、実Web・画像・依存先の要否を点検します。',
    'livecodebench': '日本語原文のあるAtCoderからeasy 4 / medium 7 / hard 7。接続試験の4問を除外。取得済みJSONL範囲からの選定で、release全体の無作為標本ではありません。',
    'scicode-verified': '異なる10主問題の第1小問。前段の生成結果を省略する問題を避け、科学・数値実装の分野を広げました。',
    'frontier-algorithmic': 'packing・path・map・circuit・permutation・sequenceの6種類。全テストと問題固有のnative得点式を維持します。',
    'salesllm-ja': '難易度4段階から各3件、基底シナリオも12件すべて別。日本語の顧客役と採点役は実際の較正が必要です。',
    'crmarena-pro-sales-ja': '見込み客評価・見積承認・案件段階の修正・営業サイクルから各2件。公式共有Orgの読み取り接続済みで、状態・時点・評価器を検証します。',
    'swebench-pro-v2-ja': 'Python 3 / Go 3 / Web 2の異なる8repo。対象モデルの成績やgold patchを使わずに選定。amd64の採点環境が必要です。'}


def export(selection, quick, detailed):
    validate_selection(selection, quick, detailed)
    counts = Counter(row['source_id'] for row in selection['cases'])
    small = Counter(row['source_id'] for row in selection['cases'] if row['quick'])
    cards = ''.join(f'<article><span>{escape(LABELS[key])}</span><strong>{small[key]} <small>/ {counts[key]}</small></strong><p>{escape(REASONS[key])}</p></article>' for key in LABELS)
    rows = []
    for index, row in enumerate(selection['cases'], 1):
        source = row['source']; hub = 'huggingface.co/datasets' if row['source_id'] in {'livecodebench', 'scicode-verified', 'crmarena-pro-sales-ja'} else 'github.com'
        url = f'https://{hub}/{source["repository"]}/blob/{source["revision"]}/{source["path"]}'
        title = row['title']; search = ' '.join((LABELS[row['source_id']], row['native_id'], title, row['domain']))
        gates = ' · '.join(row['readiness_gates'])
        rows.append(f'<tr data-search="{escape(search.casefold(), quote=True)}" data-quick="{str(row["quick"]).lower()}"><td class="number">{index:03}</td>'
                    + f'<td><span class="family">{escape(LABELS[row["source_id"]])}</span><strong>{escape(title)}</strong><code>{escape(row["native_id"])}</code></td>'
                    + f'<td>{"<span class=badge>Quick</span>" if row["quick"] else "Detailed"}<br><span class="muted">{escape(row["difficulty"])}</span></td>'
                    + f'<td><details><summary>準備待ちの内容・出典</summary><p>{escape(REASONS[row["source_id"]])}</p><p>{escape(gates)}</p>'
                    + f'<p><a href="{escape(url, quote=True)}" target="_blank" rel="noreferrer">固定した出典</a> · <code>{escape(source["revision"])}</code></p><p>{escape(row["selection_reason"])}</p></details></td></tr>')
    return '''<!doctype html><html lang="ja"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>BeaconBench · 問題選定</title>
<style>*{box-sizing:border-box}body{margin:0;background:#f6f8fc;color:#17243b;font:15px/1.7 system-ui,"Yu Gothic UI",sans-serif}main{max-width:1240px;margin:auto;padding:40px 28px 70px}a{color:#315bea}h1{font-size:32px;line-height:1.3;margin:12px 0}h2{font-size:20px;margin:38px 0 16px}.eyebrow{color:#315bea;letter-spacing:.1em;font-size:12px;font-weight:700}.intro{max-width:870px;color:#526078}.metrics{display:flex;gap:40px;margin:26px 0}.metrics strong{font-size:32px;line-height:1.2;display:block}.metrics span,.muted{color:#68768d;font-size:12px}.note{border-left:3px solid #e6ae50;padding:12px 18px;background:#fff9ed;max-width:950px}.cards{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}article{border:1px solid #e1e7f0;border-radius:10px;background:white;padding:18px 20px;position:relative}article>span{font-weight:650;display:block;padding-right:90px}article>strong{position:absolute;right:20px;top:15px;font-size:23px}article small{font-size:15px;color:#718098}article p{font-size:13px;color:#5b687f;margin:9px 0 0}.toolbar{display:flex;gap:20px;align-items:center;flex-wrap:wrap;margin:16px 0}.toolbar input[type=search]{border:1px solid #c9d4e5;border-radius:7px;padding:11px 14px;min-width:290px;font:inherit;background:white}.toolbar label{cursor:pointer}table{width:100%;border-collapse:collapse;table-layout:fixed;background:white;border:1px solid #e1e7f0;font-size:13px}th{text-align:left;color:#6c7890;font-weight:550;padding:12px 15px;background:#edf2fa}td{border-top:1px solid #e8edf4;padding:17px 15px;vertical-align:top;overflow-wrap:anywhere}td strong{display:block;font-size:14px;margin:4px 0}code{font:11px/1.5 ui-monospace,monospace;color:#6a7891;overflow-wrap:anywhere}.family{font-size:11px;color:#597094}.number{color:#9aa6b8;font-size:12px}.badge{display:inline-block;padding:2px 8px;color:#315bea;background:#edf1ff;border-radius:4px;margin-bottom:5px}details p{font-size:12px;color:#657289}summary{cursor:pointer;color:#526481}tr[hidden]{display:none}footer{color:#7b869a;font-size:12px;margin-top:28px}.empty{display:none;padding:20px;background:white}.empty.visible{display:block}@media(max-width:740px){main{padding:26px 15px}.cards{grid-template-columns:1fr}.metrics{gap:24px}.metrics strong{font-size:27px}.table-wrap{overflow:auto}table{min-width:720px}h1{font-size:27px}}</style>
<main><header><div class="eyebrow">BEACONBENCH / SELECTION REVIEW</div><h1>測る課題を、見える形に。</h1><p class="intro">Quick20とDetailed100の候補一覧。対象モデルの正誤を使わず、業務・分野・元の難易度で選びました。QuickはDetailedと同じ問題の部分集合です。</p></header>
<div class="metrics"><div><strong>20 / 100</strong><span>Quick / Detailed · ケース数</span></div><div><strong>10</strong><span>評価系列</span></div><div><strong>準備中</strong><span>正式セットの状態</span></div></div>
<p class="note">問題IDの選定は済んでいます。Babelデータは取得し、CRM共有環境の読み取り接続も確認済みです。日本語版・実行環境・採点器の受入完了までは実行可能にしません。このページに実測得点や実回答は含まれていません。</p>
<h2>分野のバランスと選定理由</h2><div class="cards">''' + cards + '''</div>
<h2>100件の候補</h2><div class="toolbar"><input type="search" id="search" placeholder="分野・問題IDで絞り込み" aria-label="候補を検索"><label><input type="checkbox" id="quick"> Quick20のみ</label><span id="count" class="muted">100件を表示</span></div>
<div class="table-wrap"><table><colgroup><col style="width:6%"><col style="width:39%"><col style="width:15%"><col style="width:40%"></colgroup><thead><tr><th>#</th><th>系列・元の問題ID</th><th>セット / 元の難易度</th><th>取得・受入条件</th></tr></thead><tbody>''' + ''.join(rows) + '''</tbody></table></div><div class="empty" id="empty">一致する候補がありません。</div>
<footer>候補版 v1 · 2026-10-28 · 試行回数はセットから独立して既定1回。追加の累積出力・操作回数・対話回数上限なし。異なる系列の難易度ラベルは同じ尺度ではありません。</footer></main>
<script>const rows=[...document.querySelectorAll('tbody tr')],search=document.querySelector('#search'),quick=document.querySelector('#quick');function update(){let n=0;const q=search.value.toLowerCase().trim();rows.forEach(row=>{row.hidden=(!row.dataset.search.includes(q)||(quick.checked&&row.dataset.quick!=='true'));if(!row.hidden)n++});document.querySelector('#count').textContent=n+'件を表示';document.querySelector('#empty').classList.toggle('visible',n===0)}search.addEventListener('input',update);quick.addEventListener('change',update);</script></html>'''


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    html = export(read_json(ROOT / 'catalog/selection.v1.candidate.json'), read_json(ROOT / 'suites/quick.v1.candidate.json'), read_json(ROOT / 'suites/detailed.v1.candidate.json'))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists() and args.output.read_text(encoding='utf-8') != html: raise BenchError('既存のレポートを上書きせず、別の出力先を指定してください。')
    args.output.write_text(html, encoding='utf-8')
    print('Exported source selection review: 20 / 100 cases; no model calls')


if __name__ == '__main__': main()
