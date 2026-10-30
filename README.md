# BeaconBench

**ローカルLLMの実力・消費・速さを、日本語の同じ課題で測る。**

Japanese-first local LLM benchmarking for task quality, token usage, and speed.

BeaconBench（ビーコンベンチ）は、自律エージェントや業務システムで使うローカルLLMを、共通の問題・実行条件・採点規則で比較するための評価基盤です。計画、ツール操作、業務遂行、実装、科学計算、最適化、営業を対象にします。

**実モデルへ接続するCLI / Web画面、評価実行、保存・再開、結果表示を実装しています。** OpenAI互換Chat APIへ接続し、思考と最終回答、実要求、SSE、usage、時間、採点証拠を保存します。実行は画面かCLIから明示的に開始します。

**Quick20の日本語版を、受入済み環境で実行できる形にしました。** 日本語内容、実モデルの補助役、公式採点器の正答・誤答control、模擬サービス・隔離実行・保存までを確認し、20件を固定したprivate packとして登録します。実行計画は20ケース・既定各1回です。今回の確認用応答を対象モデルの成績には含めません。Detailed100は候補版のままです。[受入範囲・日本語版の違い・実行方法](docs/16-quick20-acceptance.md)を参照してください。

```sh
uv sync --locked
uv run beaconbench init
uv run beaconbench serve
```

ブラウザーで `http://127.0.0.1:18741/` を開き、起動済みモデルのAPIを登録します。認証が必要な場合は、鍵を環境変数に設定するか、`--env-file local/connection.env` を `serve` の前に指定します。[設定・実行・採点器の接続手順](docs/12-running.md)を参照してください。第三者の問題・重み・個別環境の設定は配布しません。

[静的な画面試作](ui/prototype/index.html)も設計資料として残しています。こちらの表示例は架空データで、実行APIへ接続しません。通常利用は上の `serve` で起動する実画面です。

Vita・CRM・営業の評価条件を公式の固定版と照合し、元の得点を保つ評価条件v2を実装しました。Vitaの顧客役の逸脱、CRMの参照答えへの疑義、営業の取引前進と商品情報への忠実さを分けて表示します。旧回答への点検は別の採点版として保存し、新しい対話条件の結果とは扱いません。[条件変更・再採点・再生成の範囲](docs/18-evaluation-contracts.md)を参照してください。

一部の課題だけを再測定し、条件が一致する他の回答・採点を保持した統合結果も作れます。元の生成識別値を変えず、実際に測る部分と読み取り専用の全体表示を分けます。[部分再測定の条件と実行](docs/19-subset-remeasurement.md)を参照してください。

Vita crossには、配送の支払い完了を実ツールの結果で確認してから顧客の終了を許す、明示的な追加条件を用意しています。[支払い確認・原応答保全・受入](docs/20-vita-payment-termination.md)を参照してください。

顧客役の固定条件を原文から項目化して毎ターン照合し、終了後の検査にも項目別の引用根拠を求める別版を実装しました。[固定条件の保持・点検・版の区別](docs/21-vita-fixed-goals.md)を参照してください。実モデルでの受入と適用は個別の記録で管理します。

公式の顧客終了条件を、元指示と実操作の全文から検証する別版も実装しました。不正な終了は測定器の失敗として停止し、対象モデルへの追加ヒントや自動の再生成は行いません。補助検査v2は提案・操作結果も含む全文で根拠を確認します。[終了判定・誤検出防止・原本保全](docs/22-vita-source-termination.md)を参照してください。既存のrunへは自動適用しません。

公式で意図された商品の意味と宿泊込みなどの合算予算を使う終了検査v2、保存回答だけの別版点検、測定器による停止の原因表示も実装しました。[公式意味情報・予算台帳・保存回答の検証](docs/23-vita-source-semantics.md)を参照してください。本評価での使用可否は個別の固定control・実対話の受入記録で判断します。

## 評価の方針

- 日本語を主評価にし、公開済みの日本語版を優先します。独自翻訳は原本との対応・検証・版を保存します。
- QuickとDetailedはケースの範囲・件数で分けます。各ケースの試行回数は実行時に独立して指定し、既定は両セットとも1回です。同じ比較では回数とseed方針を揃えます。
- フレームワーク独自の累積出力トークン上限・操作回数上限・対話回数上限は設けません。モデル上限とベンチマーク固有の終了条件は記録します。
- 品質、入力・出力・思考・キャッシュの消費、生成速度、課題完了時間を併記します。取得できない値は不明として残します。
- 公式の採点器をアダプターで接続し、分野ごとの得点を表示します。独自翻訳・抽出問題の得点を公開ベンチマーク全体の公式スコアとは扱いません。
- 回答、操作履歴、実要求、採点証拠を保存し、条件照合付きの再開と再採点を可能にします。
- モデル接続と起動済みエンジンの識別情報、生成設定を名前付きで登録し、画面で選択・複製・組み合わせ比較を行います。エンジンの起動設定は自動変更しません。
- 進捗、分野別の品質、消費、時間を画面で確認できます。ケース明細は問題・回答・採点根拠を読みやすく表示し、思考や要求・操作の原本は詳細から確認できます。

## セットの構成

元の問題IDは[候補版として100件選定](catalog/selection.v1.candidate.json)済みです。そのうちQuickの20件はIDを入れ替えずに受入しました。公開manifestは問題ID・版を示すもので、第三者の問題本文や個別環境を含みません。別環境では同じデータ・測定器・採点器の受入が必要です。

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

Quickの20ケースをDetailedに含めます。セットは問題ID・配分・版を保持し、試行回数とseed方針は別の[実行設定](execution/default.v0.draft.json)で管理します。例えばQuickを各3回なら20ケース・60試行、Detailedを各1回なら100ケース・100試行です。条件の一致するcase・trialは再利用できます。

英語の翻訳対照と画像・動画の拡張評価は別集計にします。採用条件を満たさない枠を黙って別問題へ置き換えず、セットを別版として更新します。

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
- [実装済み機能と実行手順](docs/12-running.md)
- [Quick20 / Detailed100の選定と準備状態](docs/13-selection-and-adapters.md)
- [native接続と日本語の測定器](docs/14-native-instruments.md)
- [BabelデータとCRM環境の取得状況](docs/15-data-and-crm-access.md)
- [Quick20日本語版の受入範囲と実行](docs/16-quick20-acceptance.md)
- [長い会話の出力枠と途中復旧](docs/17-context-budget-and-recovery.md)

機械可読の[候補一覧](catalog/benchmarks.json)、[セット案](suites/quick.v0.draft.json)、[比較プロトコル案](protocols/common-ja.v0.draft.json)、[データ契約](schemas/README.md)も保存しています。

## 開発時の確認

Python 3.11以降と、固定した開発用依存関係で実行できます。モデル用SDKやGPUランタイムは不要です。

```sh
uv sync --locked
uv run python tools/check_design.py
uv run python -m unittest discover -s tests -v
```

整合性checkerは設計のリンク・JSON・ケース数・公開内容を検査し、モデルを呼びません。統合テストはローカルの偽APIとfixture採点器を使い、実行・切断・停止・再利用・原本検証・採点器の受入・画面APIを確認します。実モデルでの受入はこれらと区別します。

## ライセンスと出典

このリポジトリ独自のコード・文書は[MIT License](LICENSE)です。参照するベンチマーク、データ、モデル、ツール、コンテナにはそれぞれの利用条件が適用されます。[第三者資産の扱い](NOTICE.md)を参照してください。重み、第三者の問題全文、個別環境の認証情報、過去の測定回答は同梱していません。
