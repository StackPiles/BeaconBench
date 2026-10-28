# 13 Quick20 / Detailed100の選定と実装状態

2026-10-28。**元の問題IDを100件選定し、そのうち20件をQuickとした。候補版であり、実行可能な正式セットではない。** 日本語内容・依存環境・測定器の受入が済んでから別のfrozen版を発行する。

元データの全文、参照解、顧客の内部設定は配布しない。[100件の選定一覧](../catalog/selection.v1.candidate.json)に元ID・出典revision・選定理由・未完了条件を保存し、[Quick](../suites/quick.v1.candidate.json)と[Detailed](../suites/detailed.v1.candidate.json)はそのIDを参照する。試行回数・試行seed・モデル設定はセットへ埋め込まない。

## 配分と選定理由

| 系列 | Quick / Detailed | 選定理由と注意点 |
|---|---:|---|
| Babel τ³ | 2 / 10 | retail 4、banking_knowledge 3、telecom 2、airline 1。規則・検索・ツールの異なる業務を含め、公式standardのIDを固定SHA順で抽出。本文はデータ取得後に点検する |
| Babel DeepPlanning | 2 / 10 | 購買の公式L1/L2/L3から各2件、旅行4件。旅行は元データの旅行日数で層化。日本語queryは確認できたが、DB・ツール資産は不足 |
| Babel VitaBench | 2 / 8 | cross 4、delivery 2、instore 1、ota 1。複合業務を厚くしつつ、単一領域も残す。standardのIDを固定SHA順で選ぶ |
| Babel ClawEval | 3 / 10 | メール、予定調整、追客、議事録、在庫、調達、苦情、サポート、利益分析、進捗報告。名称と公開subsetを根拠にした候補。実Web・画像・依存graderの要否は本文入手後に確認する |
| LiveCodeBench | 4 / 18 | AtCoder日本語原文へ対応するeasy 4、medium 7、hard 7。QuickはB/D/D/F。現在の接続試験4問は除外。取得済みJSONL範囲が母集団で、公開release全体の無作為標本とは呼ばない |
| SciCode-Verified | 2 / 10 | 主問題5/8/9/13/16/18/23/24/45/76の第1小問。10種類の主問題clusterを保ち、前段の生成コードが必要な小問を単独で出さない。線形代数・情報・幾何・物理数値計算を含む |
| Frontier Algorithmic | 1 / 6 | 0/5/6/7/59/205。packing、path、map、circuit、permutation、sequenceの構成・最適化を含める。得点は各問題のnative式のまま保存する |
| SalesLLM日本語派生 | 2 / 12 | easy/medium/hard/very_hard各3件。基になるシナリオUUIDも12件すべて異なる。Quickはmedium/hardを1件ずつ。英語scriptを日本語化し、顧客役・購買意向・品質の検証を別途行う |
| CRMArena-Pro営業系 | 2 / 8 | lead_qualification、quote_approval、wrong_stage_rectification、sales_cycle_understanding各2件。Quickは見込み客評価と見積承認。専用の評価Orgが必要 |
| SWE-bench Pro V2 | 0 / 8 | Python 3、Go 3、Web系2の異なる8リポジトリ。各repo内のIDを固定SHA順で選ぶ。修正量・対象モデルの成績・gold patchは選定に使わない |
| **計** | **20 / 100** | **同じ問題・条件で比較し、QuickはDetailedの部分集合** |

SHA順位は `sha256("beaconbench-selection-v1:" + native_id)`。営業は難易度の層を先に分け、同じ基底シナリオの重複を除く。Claw、科学、最適化、QuickのLCBは上の能力配分に基づく明示選択であり、単純無作為抽出ではない。対象モデルの回答・採点結果は選定に使用していない。

QuickのLCBは `abc390_b / abc389_d / abc392_d / abc389_f`、科学は `9.1 / 45.1`、Frontierは `0`。既存接続試験の `abc387_c / abc391_d / abc387_f / abc392_f` を混ぜない。制限なしのため、Quickでも短時間終了は保証しない。

## 固定した一次資料

| 資料 | revision | 内容 |
|---|---|---|
| [BabelArena](https://github.com/QwenLM/BabelArena/tree/36960fbb7ee1aac5082fa7307264abc612a180f4) | `36960fbb…` | official standard ID、JA query、CLI、native採点の形式 |
| [LCB data](https://huggingface.co/datasets/livecodebench/code_generation_lite/tree/0fe84c3912ea0c4d4a78037083943e8f0c4dd505) | `0fe84c39…` | test6 JSONL取得範囲のAtCoder問題。完全なreleaseを取得したとの主張はしない |
| [SciCode-Verified data](https://huggingface.co/datasets/shhu2001/SciCode-Verified/tree/eea11a866be6860725258702b39ef8651ed26abd) | `eea11a86…` | 修正済み問題・第1小問ID。HDF5と2環境の受入は別に必要 |
| [Frontier-CS](https://github.com/FrontierCS/Frontier-CS/tree/12ab26840cff15f1d0ab09004dafd07f92c14b60) | `12ab2684…` | statement、config、native judgeの集計式 |
| [SalesLLM](https://github.com/Bairong-Xdynamics/Benchmarking-LLM-Realistic-Selling-Skill/tree/782c0d22db7c931166e05c163c964b925119376c) | `782c0d22…` | 英語805シナリオ、難易度、交互対話、品質・購買意向の合成式 |
| [CRMArenaPro data](https://huggingface.co/datasets/Salesforce/CRMArenaPro/tree/8c055f5b45f15f7d996ee99277c4d0ea5049c6a8) | `8c055f5b…` | B2B single-turnの2140件から営業4分野を抽出 |
| [SWE-bench Pro V2](https://github.com/scaleapi/SWE-bench_Pro-os/tree/66f92766bba642462d4bbe5479e83f91f9211862/v2) | `66f92766…` | 642タスク・11repoの公開split、native verifier、清浄イメージでの再採点 |

SalesLLMの英語ファイルには、実際のpromptが英語でもlanguageメタデータがchineseの行がある。選定一覧には報告された値と確認したprompt言語を分けて残した。未確認の利用条件を本リポジトリのMITへ変更しない。

## 今回実装した範囲

| 部分 | コード実装 | 実データでの受入・残作業 |
|---|---|---|
| 共通native接続 | stdio RPC、loopback API、固定argv、元要求と実要求、役別SSE/usage、成果物の分割転送とSHA検証 | 各ハーネスの全経路が接続を通ること、retry・timeout・reset・cleanupを確認 |
| Babel | τ³/Vita/旅行/Claw単問CLIのrecipe、native記録の厳密な正規化 | 日本語data一式が未入手。Vitaにtask全体の再試行3回の固定値があり、無再試行版の差分とSHAを受入前に用意する。購買の起動recipeは未完了 |
| SalesLLM日本語派生 | 顧客の内部情報を分離した対話、構造化終了、品質・意向・言語の役別採点、固定較正との照合 | 12scriptと判定文の日本語review、顧客忠実性・意向・品質の較正。実モデルはまだ呼んでいない |
| 科学 | Linux専用の2環境コンテナ採点器、HDF5読込preflight、OR採点、所有コンテナのみの後始末 | 10小問の日本語pack、HDF5・vendor・image SHA、正解/誤答/空提出の実機control |
| 最適化 | native HTTP採点client、全テスト件数とnative得点式の照合、raw保存 | 分離judgeの起動・停止・全テスト資産、compile errorの分類を含む実機control |
| CRM / SWE | 汎用native接続とnative得点の正規化 | 専用Orgと初期状態reset / amd64環境、agent・日本語版・native verifierの専用実行recipeは取得・受入待ち |

CLI recipeとfixtureが通っても `execution_ready=true` にはしない。モデル間の優劣、100件の得点、所要時間はいずれも未測定。

## 現在の試験を保つ運用

進行中のrunのplan・case pack・registry・固定runtimeを変更しない。稼働中サーバーも再起動せず、追加実装は次の起動で使用する。ソース取得とfixture確認は手元の開発環境で行い、追加の推論・コンテナ採点・重みの取得を推論機や中継機で開始しない。

選定を画面上で確認する静的レポートは、既存サーバーと独立して生成できる。

```sh
uv run python tools/export_selection.py --output local/selection-review.html
```

このレポートには実回答・実測値を含めず、100件のID、Quickの範囲、選定理由、準備待ちの内容を表示する。[native接続と測定器の準備手順](14-native-instruments.md)も参照。
