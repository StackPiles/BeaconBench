# 15 BabelデータとCRM環境の取得状況

確認日：2026-10-28。**Babelの公式公開データを取得し、CRMは公式研究用共有B2B環境への読み取り接続を確認した。** 専用Orgの新規作成は行っていない。対象モデルの追加生成・本評価も開始していない。

## 前の説明の修正

[BabelArenaのGitHub実行資料](https://github.com/QwenLM/BabelArena/blob/36960fbb7ee1aac5082fa7307264abc612a180f4/docs/RUNNING.md)には、hosted datasetは未公開で `--source` の指定が必要と記され、codeの既定URLも空だった。しかし、実際のHugging Face配布APIには [Qwen/BabelArena](https://huggingface.co/datasets/Qwen/BabelArena) が公開されていた。前の「取得待ち」は配布先の確認不足によるもの。公式の配布APIと実ファイルを確認した結果へ更新する。

CRMについても、専用Orgを用意しなければ評価できないという扱いを修正する。[公式リポジトリ](https://github.com/SalesforceAIResearch/CRMArena)は研究用の共有OrgとAPIでの利用方法を案内している。[公式connector](https://github.com/SalesforceAIResearch/CRMArena/blob/22033e5364b4ca5faf71fe29deae181b4c518706/crm_sandbox/env/connect_sandbox.py)のクエリ経路と、今回選んだ8件の依頼を確認した。いずれもデータの検索・読み取りと答えの判定であり、見積の承認や商談段階を実際に更新する操作ではない。

## 取得・接続を確認した範囲

| 対象 | 確認結果 | まだ意味しないこと |
|---|---|---|
| Babel公開先 | `Qwen/BabelArena`、public、利用申請不要。revision `e8f095bcd3c1a0e01cfa7d7fad526bf448e09823` | GitHub codeとdataの全条件が受入済みという意味ではない |
| Babel取得物 | 日本語・英語・既定の共通資産4,871ファイル、1,653,226,974 bytes（約1.54GiB）。全ファイルを上流Git blob / LFS SHAと照合 | 23言語すべてをダウンロードしたとは扱わない |
| 候補との対応 | 選定したBabel38件すべての日本語task / DB / graderの所在を確認 | 本文・依存先・採点結果の内容検証は別に必要 |
| CRM公開データ | `Salesforce/CRMArenaPro` revision `8c055f5b45f15f7d996ee99277c4d0ea5049c6a8`。B2B/B2C schema取得済み。選定元B2B taskも保持 | 日本語翻訳や評価器の受入済みという意味ではない |
| CRM共有B2B Org | 公式に研究用として案内された接続先で認証成功。Lead / Opportunity / Quote / Task / EmailMessage / Knowledgeをdescribe・件数取得 | 専用環境を作成・複製したとは扱わない |

Babelのbanking_knowledgeは `tasks_Japanese.json` 一括形式ではなく `tasks/Japanese/task_NNN.json`。この形式の違いも考慮して選定IDを照合した。Clawは選定した10件すべてで日本語taskとgrader、元のgraderが存在した。ダウンロードしたPythonコードは実行していない。

CRM接続確認は認証とGETによる読み取りのみ。レコードや設定の変更、外部へのメール送信、課金されるモデル呼び出しは行っていない。公開資料に研究用認証が記載されていても、本リポジトリへ値をコピーしない。接続証拠・schema・取得データ・認証情報は公開物から分ける。

## 評価へ進む前の条件

- Babel：固定codeとdataの互換性、日本語入力・tool・DB・graderの対応、依存task、模擬サービス、tokenizer、顧客役・変換器・judgeの固定と較正、native controls。
- CRM：共有環境のデータ状態・参照日時とgoldの整合、日本語の依頼、読み取り専用の操作経路、native evaluatorのcontrols。比較の前後で環境識別と対象データの状態を確認する。

共有環境は他の利用者によって状態が変わり得る。今回の接続確認を恒久的な状態固定の保証にはしない。snapshot再生へ変える場合は、公式のlive API評価とは別の環境版として記録する。更新操作を含む課題や、独立してresetできる環境が必要になった場合に専用Orgを検討する。無料Orgの新規作成だけで、ベンチマークの独自schema・データ・権限が再現されるとは仮定しない。

したがって、**現在の8問の準備にSalesforce新規登録をお願いする必要はない**。この時点ではQuick / Detailedを未受入としていた。その後、Quick20の日本語・測定器・実行環境を[受入記録](16-quick20-acceptance.md)の範囲で検証した。Detailed100は候補のまま。ケースID・配分・試行回数・保存済み回答を変更せず、取得状況と残条件だけを更新する。

[取得状況の機械可読記録](../catalog/source-availability.v1.json) / [問題選定と実装状態](13-selection-and-adapters.md) / [native接続](14-native-instruments.md)。
