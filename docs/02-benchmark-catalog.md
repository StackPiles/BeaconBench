# 02 ベンチマーク候補と採用条件

調査日：2026-10-28。[機械可読の候補一覧](../catalog/benchmarks.json) のrevisionは調査時点の参照先。データ・コンテナ・補助モデルまで固定した評価用lockは、まだ作成していない。

## 中核候補

| 候補 | 評価すること | 日本語・採点の扱い | 主な受入条件 |
|---|---|---|---|
| [BabelArena τ³-Bench](https://github.com/QwenLM/BabelArena) | 対話、ツール、知識、業務規則 | 上流の日本語と元の採点器を優先 | 顧客役・自然言語判定・検索用tokenizerを含め固定 |
| BabelArena DeepPlanning | 旅行・購買計画の制約充足 | 上流日本語。旅行の自由文→構造化の変換器も測定器 | 変換前後の内容一致、決定的採点、ドメイン別得点を確認 |
| BabelArena VitaBench | 複数領域にまたがる業務と顧客対話 | 上流日本語、顧客役と採点役を固定 | 最終状態・操作証拠と顧客役の忠実さを確認 |
| BabelArena ClawEval | ファイル・事務・調査・サービス連携 | 上流日本語。テキストと画像・動画を別trackにする | 模擬サービス、依存する元タスクの採点器、実検索の条件を固定 |
| [LiveCodeBench](https://github.com/LiveCodeBench/LiveCodeBench) | アルゴリズム、コードの正しさ | 版の対応を確認した公式日本語、または検証付き独自翻訳 | 公開・非公開として配布された全テストを固定、独立実行 |
| [SciCode-Verified](https://github.com/flyingwagner/scicode-verified) | 科学・数値実装 | 原本の修正済み仕様を翻訳し、コードとデータは保つ | 修正済みHDF5、累積小問、背景の有無、2環境OR採点を固定 |
| [Frontier-CS Algorithmic](https://github.com/FrontierCS/Frontier-CS) | 実行可能解と最適化品質 | 問題説明を翻訳。checker・入力・資源制限・得点式を維持 | 全テストを使い、連続点と実行可能性を別に保存 |
| [SalesLLM](https://github.com/Bairong-Xdynamics/Benchmarking-LLM-Realistic-Selling-Skill) | 営業対話の進展と購買意向 | 顧客役・商品・採点まで含む日本語派生版 | 日本語の顧客役と購買意向判定を別途較正 |
| [CRMArena-Pro](https://github.com/SalesforceAIResearch/CRMArena) | 見込み客の適格性、商談分析、案件段階、見積承認 | 営業系を抽出し、日本語の依頼と資料を対応づける | 外部Orgの状態・reset・データ版・利用条件を確認 |
| [SWE-bench Pro V2](https://github.com/scaleapi/SWE-bench_Pro-os/tree/main/v2) | リポジトリを読んだ修正と回帰確認 | 依頼の文章を翻訳。コード・テストは保つ | 公式イメージのLinux/amd64対応、固定ハーネス、清浄環境で再採点 |

## BabelArenaの統合で固定するもの

[公式実行資料](https://github.com/QwenLM/BabelArena/blob/main/docs/RUNNING.md) に従い、4系列は別々のPython環境に置く。一つのvenvへ依存関係を押し込まない。共通なのは起動・測定・保存契約であり、上流ハーネスは各アダプターの環境で動かす。

顧客役・採点役・変換役・言語判定役は、モデルID、revision、プロンプト、サンプリング、提供経路まで固定する。DeepPlanningの採点は規則ベースでも、自由文の変換器が結果を変える。変換不良を対象モデルの推論失敗に含めない。

ClawEvalの公式ラッパーには、custom subsetの各splitを空にできない制約が記載されている。テキストだけの評価では、選定したsplitだけを正式な採点条件で起動できるかをアダプター受入で確認する。未対応の画像ケースをテキストケースとして数えない。必要な依存元grader・fixtureは選定外でも取得する。

実Web検索は外部状態に依存する。初版は固定資料・模擬サービスで評価できるものを優先。実検索を使う場合は検索backendと取得日時・資料・応答を記録し、オフラインの結果と別系列にする。録画の再生で対話を置き換える場合も別の環境版とする。

## 既存14問との互換性

既存の14問はLiveCodeBench 8、SciCode-Verifiedの独立小問4、Frontier-CS 2からなる抽出セット。共通採点v2には数値許容誤差の修正と制約違反テスト2件の除外がある。

移植時は上流のままの採点と共通採点v2を区別し、問題文・許容誤差・除外理由・テストID・環境・policy SHAを再取得可能なlockにする。ここには過去の回答や個別環境の実行スクリプトをコピーしない。元の14問は互換track、新しい日本語版は別のlanguage packとする。

## 営業分野の具体的な測定

SalesLLMでは、営業過程の評価と会話末尾の購買意向を別々に保存する。原版のCustomerLMとSaleIntent-BERTは英語・中国語での評価を公表しており、日本語にその性能を移せるとは仮定しない。日本語の主評価器は較正に合格してから決める。原版の配点・合成式は、固定するコードと論文・モデルカードの整合を確認してからlockへ記す。

営業成績の補助指標として、商品事実との矛盾、権限外の値引き・約束、顧客の条件違反、顧客役の役割崩れも記録する。これらを原版の公式点へ後付けで混ぜず、日本語派生版の規則と監査指標を明示する。模擬顧客の購買意向を実売上に換算しない。

CRMArena-Proは[公式論文](https://arxiv.org/abs/2505.18878)のSales Lead Routing、Lead Qualification、Quote Approval、Sales Insight Mining、Wrong Stage Rectification等を候補にする。B2B/B2C、single/multi-turnは別の属性として残す。営業対話の説得力とは別の能力を測る。

## 拡張候補

- [TERMS-Bench](https://terms-bench.github.io/)：固定方策の相手との価格交渉。利益・不成立条件での合意・制約違反をプログラムで評価できる設計。公式評価実装・入力・seed・利用条件の入手先は未確認。独自に類似ゲームを作って公式結果と呼ばない。
- [FrontierCS 2](https://github.com/FrontierCS/FrontierCS-2)：研究コードの改善。既存Algorithmicとはハーネス・反復提出・得点の意味が異なるため、別adapter・別track。
- ClawEvalの画像・動画：モデルの対応能力を確認した上で追加し、テキスト共通スコアへ混ぜない。

## 採用の共通条件

原本・依存データ・採点器の取得とSHA検証、日本語の内容確認、参照解と不正解対照、モデル接続、対象アーキテクチャでの実行を通過してから `execution_ready=true` のlockを発行する。未確認の枠がある段階で「100問セットが完成」と表示しない。
