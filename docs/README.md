# BeaconBench 詳細設計 v0.3

作成日：2026-10-28。設計案と実装の進行状態を管理する。モデル接続・実行・保存・実画面とLCBの採点接続を実装した。分野別adapter・日本語化・問題選定全体の完成を表さない。

## 読み方

| 文書 | 責務 |
|---|---|
| [01 要件](01-requirements.md) | 目的、合意事項、比較単位、対象範囲 |
| [02 候補一覧](02-benchmark-catalog.md) | 分野との対応、出典、実装・日本語・環境上の採用条件 |
| [03 セットと統計](03-suites-and-statistics.md) | Quick / Detailed、問題選定、反復、集計と限界 |
| [04 構成](04-architecture.md) | 共通実行管理、モデル接続、アダプター、予定CLIと契約 |
| [05 プロトコル・指標](05-protocol-and-metrics.md) | 条件一致、トークン・時間・速度・終了理由の定義 |
| [06 日本語化](06-japanese-adaptation.md) | 翻訳範囲、検証、顧客役・採点器の較正 |
| [07 実行](07-execution.md) | 計算ホスト、コード実行の分離、リソース所有、復帰 |
| [08 再現性](08-reproducibility.md) | 保存形式、同一性、再利用、再採点、公開用出力 |
| [09 実装計画](09-implementation-plan.md) | 段階ごとの成果物・受入条件・着手順序 |
| [10 画面と設定](10-interface-and-configuration.md) | モデル・プリセット・比較行列、進捗と結果の画面、設定の版管理 |
| [11 画面レビュー](11-ui-review.md) | 情報整理、読みやすさ、操作、画面幅の確認 |
| [12 実行手順](12-running.md) | 実装済み機能、起動、モデル登録、実行・再開、採点器とcontrol |
| [13 問題選定と実装状態](13-selection-and-adapters.md) | Quick20 / Detailed100の候補ID、理由、出典、取得・受入待ち |
| [14 native接続と測定器](14-native-instruments.md) | Babel接続、営業派生、helper較正、科学・最適化の採点 |
| [15 データとCRM環境](15-data-and-crm-access.md) | 公式Babelデータ取得、CRM共有環境の読み取り接続、残る受入条件 |
| [判断記録](decisions.md) | 合意と提案の区別、根拠、未決事項 |

関連する機械可読設計： [catalog](../catalog/benchmarks.json)、[Quick](../suites/quick.v0.draft.json)、[Detailed](../suites/detailed.v0.draft.json)、[protocol](../protocols/common-ja.v0.draft.json)、[実行設定](../execution/default.v0.draft.json)、[schemas](../schemas/README.md)。

[操作できる画面試作](../ui/prototype/index.html)と[確認手順](../ui/prototype/README.md)も用意した。画面内の結果は架空の例であり、ベンチマークやモデル起動は行わない。

実データの画面は `beaconbench serve` で起動する。実行・停止・再開・結果・全文の確認は[実行手順](12-running.md)を参照する。

## 全体の流れ

```mermaid
flowchart LR
  A[上流資産と版の確認] --> B[問題選定と日本語化]
  B --> C[採点器・補助モデルの検証]
  C --> D[セットと実行条件を固定]
  D --> E[モデル・環境の事前確認]
  E --> F[生成・対話・ツール操作]
  F --> G[保存回答の採点]
  G --> H[品質・消費・時間を比較]
  H --> I[原本保存と常用環境の復帰]
```

対話中に必要な顧客役は生成段階で動く。後処理の採点器とは別の計算資源・エンドポイントを割り当てる。単発のコード生成は全生成後に対象モデルを解放して採点できる。

## 設計を固定する境界

- 本文中の「必須」はユーザーの合意または比較を成立させる契約を示す。
- 「初版案」「候補」は設計上の提案。問題ID、翻訳、実行環境、補助モデルは受入確認後に固定する。
- 候補・翻訳の準備不足を、問題の成功・失敗や0点に換算しない。
- ソースの最新変更を既存の評価セットへ自動適用しない。異なる版は別の比較系列にする。

- [Quick20日本語版の受入と実行](16-quick20-acceptance.md)
- [長い会話の出力枠と途中復旧](17-context-budget-and-recovery.md)
- [Vita・CRM・営業の評価条件と保存回答の点検](18-evaluation-contracts.md)
- [一部の課題だけの再測定と結果の統合](19-subset-remeasurement.md)
- [Vita crossの支払い確認と顧客の終了](20-vita-payment-termination.md)
- [Vitaの固定条件保持と項目別点検](21-vita-fixed-goals.md)
- [Vitaの公式終了条件と全文による根拠検証](22-vita-source-termination.md)
- [Vitaの公式商品意味・合算予算・停止理由](23-vita-source-semantics.md)
