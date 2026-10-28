# BeaconBench

**ローカルLLMの実力・消費・速さを、日本語の同じ課題で測る。**

Japanese-first local LLM benchmarking for task quality, token usage, and speed.

BeaconBench（ビーコンベンチ）は、自律エージェントや業務システムで使うローカルLLMを、共通の問題・実行条件・採点規則で比較するための評価基盤です。計画、ツール操作、業務遂行、実装、科学計算、最適化、営業を対象にします。

**現在は詳細設計段階です。ベンチマーク実行CLI、各アダプター、日本語翻訳、測定結果はまだ実装・作成していません。** このリポジトリの動作確認コマンドは設計資料の整合性を検査するもので、モデル呼び出しやデータダウンロードは行いません。

[画面の試作](ui/prototype/index.html)をブラウザーで開くと、比較結果、モデル・設定の選択、実行計画、ケースの回答・根拠を確認できます。表示例は架空データで、実行APIには接続していません。リポジトリ取得後にHTMLを開くか、[確認手順](ui/prototype/README.md)を参照してください。

## 評価の方針

- 日本語を主評価にし、公開済みの日本語版を優先します。独自翻訳は原本との対応・検証・版を保存します。
- QuickとDetailedは問題数・反復数で分けます。同じ問題の生成条件と採点条件を揃えます。
- フレームワーク独自の累積出力トークン上限・操作回数上限・対話回数上限は設けません。モデル上限とベンチマーク固有の終了条件は記録します。
- 品質、入力・出力・思考・キャッシュの消費、生成速度、課題完了時間を併記します。取得できない値は不明として残します。
- 公式の採点器をアダプターで接続し、分野ごとの得点を表示します。独自翻訳・抽出問題の得点を公開ベンチマーク全体の公式スコアとは扱いません。
- 回答、操作履歴、実要求、採点証拠を保存し、条件照合付きの再開と再採点を可能にします。
- モデル、エンジン設定、生成設定を名前付きで登録し、画面で選択・複製・差分確認・組み合わせ比較を行える設計です。
- 進捗、分野別の品質、消費、時間を画面で確認し、ケースの回答と採点根拠まで辿れるようにします。

## セットの構成案

問題IDと日本語版は未固定です。以下は選定枠であり、実行可能な問題セットではありません。

| 分野・候補 | Quick | Detailed |
|---|---:|---:|
| ツール・規則遵守 / BabelArena τ³-Bench | 2 | 10 |
| 計画・制約 / BabelArena DeepPlanning | 2 | 10 |
| 複合業務 / BabelArena VitaBench | 2 | 8 |
| 調査・事務・連携 / BabelArena ClawEval | 3 | 10 |
| コード生成 / LiveCodeBench | 4 | 18 |
| 科学・数値実装 / SciCode-Verified | 2 | 10 |
| 最適化 / Frontier-CS Algorithmic | 1 | 6 |
| 営業対話 / SalesLLM日本語派生版 | 2 | 12 |
| 営業実務 / CRMArena-Pro営業系 | 2 | 8 |
| リポジトリ修正 / SWE-bench Pro V2 | 0 | 8 |
| **合計ケース数** | **20** | **100** |
| **ケースごとの試行数** | **1** | **3** |

Quickの20ケースをDetailedに含め、最初の試行条件を同じにする設計です。英語の翻訳対照と画像・動画の拡張評価は別集計にします。採用条件を満たさない枠を黙って別問題へ置き換えず、セットを別版として更新します。

追加制限を設けないため、Quickの所要時間は保証しません。問題の実測時間を見て、選定と規模を調整します。

## 詳細設計

[設計の入口](docs/README.md)から、全体構成・実行プロトコル・日本語化・測定・採点・再現性・実装計画を読めます。

- [要件と合意事項](docs/01-requirements.md)
- [ベンチマーク候補と採用条件](docs/02-benchmark-catalog.md)
- [問題セット・選定・統計](docs/03-suites-and-statistics.md)
- [構成とアダプター契約](docs/04-architecture.md)
- [実行条件と測定指標](docs/05-protocol-and-metrics.md)
- [日本語化と採点器の検証](docs/06-japanese-adaptation.md)
- [実行環境・分離・復帰](docs/07-execution.md)
- [保存・再開・再採点・公開](docs/08-reproducibility.md)
- [実装順序と受入条件](docs/09-implementation-plan.md)
- [画面とモデル・設定切替](docs/10-interface-and-configuration.md)
- [判断記録と未決事項](docs/decisions.md)

機械可読の[候補一覧](catalog/benchmarks.json)、[セット案](suites/quick.v0.draft.json)、[比較プロトコル案](protocols/common-ja.v0.draft.json)、[データ契約](schemas/README.md)も保存しています。

## この段階で実行できる確認

Python 3.11以降と、固定した開発用依存関係で実行できます。モデル用SDKやGPUランタイムは不要です。

```sh
uv sync --locked
uv run python tools/check_design.py
uv run python -m unittest discover -s tests -v
```

この確認は設計のリンク、JSONの整合性、問題数、追加制限がないこと、公開対象への秘密情報の混入を検査します。将来の実行CLIは[構成設計](docs/04-architecture.md)に記載しています。

## ライセンスと出典

このリポジトリ独自のコード・文書は[MIT License](LICENSE)です。参照するベンチマーク、データ、モデル、ツール、コンテナにはそれぞれの利用条件が適用されます。[第三者資産の扱い](NOTICE.md)を参照してください。重み、第三者の問題全文、個別環境の認証情報、過去の測定回答は同梱していません。
