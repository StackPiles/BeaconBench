# 14 native接続と日本語の測定器

このページは追加実装の接続契約。稼働中のモデル・サーバー・試験を変更するコマンドではない。各公式環境の実機受入は未完了。[選定と準備状態](13-selection-and-adapters.md)を先に確認する。

## 生成アダプター

private case packの `generation.adapter` は `chat` / `dialogue` / `native`。

- `chat`：既存の単発コード生成。
- `dialogue`：日本語の営業派生。`script`の営業system、顧客system、開始文を分ける。対象モデルは顧客systemを受け取らない。`helpers.customer`は検証済み測定器を参照する。
- `native`：専用環境に置いた公式ハーネスを固定コマンドで実行する。`executor_id / family / native_id / domain`と必要なhelperを固定する。

helperは `model_id / preset / policy / instrument` で指定する。生成に使うhelperは `generation.helpers`、後段の採点に使うhelperは `grader.helpers`。営業派生などで後段だけの採点役を変えた場合は、生成条件が同じ完成回答を再利用し、元の判定は上書きしない。native CLIが同じ処理内で呼ぶjudgeはnative実行条件にも含め、未実装の軌跡再採点を実行済みとは扱わない。

## native RPC

controllerがnative環境のコマンドを起動し、最初に1行のJSONを渡す。nativeは `beaconbench.native_bridge --config <private-config>`、または同じprotocolの専用driver。実行先が別ホストなら、operatorが固定したSSHコマンドで接続する。自動のホスト探索やサービス切替はしない。

1. nativeは `model_request`で役割・messages・tools等を要求する。
2. controllerは登録済みのモデルと生成条件で呼ぶ。温度・出力上限・seed等はnativeの既定値より共通planを優先し、差を記録する。
3. providerへ送った要求、SSE原本、思考、最終message、usage、各時間を役割別に保存し、nativeへOpenAI互換JSONを返す。
4. nativeは結果とstdout/stderr、軌跡・成果物を転送する。分割転送後のバイト数とSHAを照合し、欠落があれば完成扱いにしない。
5. 結果のケースID・単一試行・必須項目を照合する。複数結果から最新・最高得点を選ばない。

モデルのAPIキーはcontroller内に留め、nativeのloopback APIには一時的なproxy tokenだけを渡す。Orgなど環境の認証が必要なときは、実際の値を保存せず、operatorの環境変数名を `credential_refs` で明示する。推論APIのキーを兼用する参照は拒否する。CRMは公式が研究用として明示した共有Orgの利用範囲を確認し、その認証値を公開物へコピーしない。

`lock_files`を空にできない。native側のsource/data/configとcontroller側の接続設定をそれぞれ固定する。`grader.native_executor_fingerprint`を生成executorに結び付け、同じ構成の正答・誤答controlを通す。別の構成の受入を流用しない。

RPCは呼び出しを順次処理する。公式CLIには1ケース・1試行・concurrency 1を明示する。SDKの自動再試行・timeout後の再送・隠れた直接API経路もsource auditの対象。汎用接続が動いたことだけでは全呼び出しcoverageの実機受入を満たさない。

`beaconbench.adapters.babel.recipe(family, domain)` は上流の固定版に対応する構成テンプレートを返す。独立したPython環境、native binary、data root、work root、Claw image等はoperatorが指定する。JSONはYAMLのsubsetとしてτ³/Vita/Clawの設定へ渡す。変数は `variables`、小さな設定ファイルは `inline_files`、生成された証拠は `artifact_globs`で指定する。source/dataとpaired controlsが揃うまでregistryには登録しない。

```sh
uv run python tools/export_native_recipe.py --family babel-tau3 --domain retail --output local/tau3-recipe.json
```

これは準備テンプレートの出力だけで、ハーネスやモデルを起動しない。実行器にする前に、native環境の実パス、`lock_files / source_root / work_root`を満たし、ケースと採点器を同じ構成で検証する。

## 時間と消費

対象モデル・顧客・converter・品質judge・意向judge・言語judgeを別々に計測する。APIがusageを返さない値はnullで、文字数からtoken数を推定しない。採点中のモデル呼び出しもrun全体の消費へ含める。nativeログの自己申告tokenとcontrollerの実測を足し合わせない。

対象課題の完了時間と、起動・変換・採点まで含むpipeline時間は別の値。native内の終了境界を測れないとき、課題完了時間はnullとして残す。nativeパイプライン全体の秒数で埋めない。営業派生の課題時間は開始から顧客の終了判定までで、後段judgeを含まない。

## 営業の日本語派生

顧客の応答は `reply / conversation_ended / end_reason` のJSON。日本語の「ありがとうございます」のような語だけで終了と決めず、検証済み顧客役の終了判定を保存する。対話回数の追加既定値はない。公式ルールとして回数を採用するときは `native_end_conditions.generated_turn_limit` と出典を明示し、生成した話者ターン数で数える。原版の `round_num=20` は営業・顧客20往復を意味しない。

後段の `grader.kind=salesllm_ja` は、日本語で成立するか、会話品質、購買意向の順に別の役を呼ぶ。品質は整数0〜10、意向はA/B/C/X/F。原版の式に合わせ、既定では品質と意向を各0.5で合成する。意向の点数は10/8/6/4/2。**合格閾値を新設しない。** 分類器をLLMに置き換える日本語派生なので、原版のCustomerLM/SaleIntent-BERTと同等とは呼ばない。

`quality_prompt / intent_prompt / language_prompt`は翻訳・review済みのprivate language packで固定し、helperのpolicyに判定文と出力契約のdigestを保持する。コード内で今回の回答に合わせた基準を作らない。不正なJSON、範囲外の得点、言語不成立は `invalid_instrument` で、0点ではない。

## helperの較正証拠

`instrument-import`は**保存済みの較正結果を読み込むだけ**で、モデルを呼ばない。

```sh
uv run beaconbench --data-dir local/evaluation-data instrument-import \
  --model-id fixed-judge --preset local/judge-preset.json --policy local/judge-policy.json \
  --expected local/reviewed-controls.json --predictions local/judge-predictions.json
```

expectedには `language=ja / reviewed=true / review_basis / labels / cases[{id,label,critical?}]`、predictionsには `instrument_fingerprint / cases[{id,label}]`を持たせる。fingerprintは登録済みmodel・validated preset・policyのcanonical SHA。較正集合は評価問題と分け、終了/継続、成立/不成立、意向の全クラス、重要な誤判定を含む。欠落・重複は拒否し、不明ラベルはunknownとして残す。

現実装の開発用受入はmacro-F1 0.9以上、不明0、critical誤判定0。`policy.control_acceptance.minimum_macro_f1`で固定基準を明示できる。この値は母集団精度の保証でも最終的な較正基準の合意でもない。数値品質judgeの較正は0〜10の各ラベルを含めるなど、用途に応じたcontrol集合のレビューが必要。顧客忠実性は発言内容・役割・終了判定の監査結果をcontrolにする。

input/output原本のSHAが較正receiptに含まれ、モデル・preset・policyが違うと利用できない。`--fixture`の証拠はdevelopment trackだけで、本評価の測定器には使えない。現在はfixtureのみ検証済みで、実モデルの日本語較正は未実施。

command採点器の `executor-qualify` は従来の正解/誤答boolに加え、`expected_score / metric_unit / absolute_tolerance`を扱う。連続得点は異なる期待値を2つ以上含む必要がある。これは得点の受入であり、営業に合格閾値を追加する処理ではない。

## コード採点器の追加

`beaconbench.science_executor --config ...` はLinux採点ホストで使う。固定した2024/2025のimage SHA、抽出器、HDF5、vendor、各小問資産が必要。第1小問のみを受け付け、HDF5読込を生成コードと別プロセスで先に確認する。生成コードはoffline・非root・read-onlyコンテナで実行し、2環境のORを保存する。nativeの1800秒実行上限とホスト保護条件を記録する。環境不成立を不正解にしない。現在はソース・fixture確認のみで、実機controlは未実施。

`beaconbench.frontier_executor --config ...` は分離されたnative judge環境の内部で使う。接続はloopback限定で、全テスト件数と問題固有の採点コードSHAが必要。nativeの各testのscoreRatioと、固定したjudge_engineの100倍平均を照合する。source、native制限、正当な全テストを保つ。judge自体の起動・cleanup・compile errorを含むcontrolは別途必要であり、このclientが特権コンテナを自動起動することはない。

CRMは公式研究用共有B2B Orgへの読み取り接続を確認した。選定8問のために専用Orgを必須とはしない。共有データの状態・時点、日本語入力、evaluator、読み取り専用の経路を受入する必要がある。SWEはamd64 executorと清浄イメージでのpatch再採点が必要。どちらも得点記録を読むnormalizerだけで専用agent・実行環境まで実装済みとは扱わない。[取得確認の詳細](15-data-and-crm-access.md)を参照。
