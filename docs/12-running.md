# 12 設定・実行・採点器の接続

## 実装した範囲

| 対象 | 現在の状態 |
|---|---|
| モデル | OpenAI互換Chat API。vLLM / llama.cppの生成パラメーターを明示して送信 |
| 設定 | モデル・生成presetの版付きregistry。UI / CLIが同じresolverを使用 |
| 実行 | 単発Chat、構成ごとの順次生成、全生成後の採点。各ケース既定1回 |
| 計測 | 生SSE、要求、思考・回答、usage、TTFT / TTFV、要求時間、課題時間、採点時間 |
| 継続 | 明示的な停止・再開。SHA検証付きの回答再利用。試行数を追加しても既存seedは維持 |
| 採点 | 接続診断、固定引数のcommand executor。正答・誤答controlの証拠と構成SHAを照合 |
| コード | 固定LCB policyをLinuxコンテナへ接続。生成コードをcontrollerやホストで実行しない |
| 画面 | モデル / preset登録、計画、進捗、比較表、回答全文・思考・実要求・採点証拠 |
| 問題 | 日本語原文のAtCoder課題を、対応する固定LCB policyへ取り込むCLI |

Quick / Detailedの選定、複数ターン・tool adapter、顧客役 / 変換役 / LLM judge、SciCode / Frontierの汎用化、信頼区間・ペア差、公開export、モデル起動・復帰の管理は未実装。元の14問や新しい4問を黙ってQuickへ読み替えない。データ契約のdraft schemaと現在のruntime内部記録は同一ではなく、拡張と受入時に揃える。

## 起動と認証

Python 3.11以降、uvを使う。初期化はモデルを呼ばない。

```sh
uv sync --locked
uv run beaconbench init
uv run beaconbench --env-file local/connection.env serve --port 18741
```

`local/connection.env` は自分で作り、例えば `LOCAL_LLM_API_KEY` を環境変数として設定する。鍵が不要ならenv-fileは省略できる。サーバーは既定でloopbackのみを受け付ける。ブラウザーからの操作には同一Originと一時トークンを確認する。外部の有料endpointは既定で無効で、必要な運用者だけ `BEACON_ALLOW_EXTERNAL_ENDPOINTS=1` を明示する。

Web画面の「モデルを追加」でAPI URL、モデルID、**APIキーの環境変数名**、容量、識別版、接続方式を登録する。鍵の値は画面に入れない。モデルを発見できたこと、容量を確認できたこと、実効サンプリングを確認できたことを区別する。discoveryで不明な容量や実効設定は確認済みにしない。

CLIでは[モデル設定例](../examples/model.local.example.json)を `local/model.json` へコピーして編集する。

```sh
uv run beaconbench model-add local/model.json
uv run beaconbench --env-file local/connection.env probe local-model
```

容量やKV / MTPは起動済みサービスの設定である。登録するだけではエンジン設定は変わらない。contextを変更する場合は、別途その容量で起動した接続構成を登録する。共有サービスのキャッシュは消去せず、その測定はcold条件と区別する。

共通presetは温度1.0、top-p 0.95、top-k無効、context 262144、最大出力131072、対応モデル固有の最大思考設定。上限は1要求あたりのエンジン条件で、独自の累積出力・操作・ターン・課題時間上限は加えない。容量のKは1024、消費tokensはAPI実値として記録する。起動済み構成の既定値と違うサンプリングも、実要求として保存する。

## 画面での実行

1. 「比較を作成」で、実行可能なセット・モデル・生成presetを選ぶ。
2. 試行回数を設定する。既定1回、1 / 3 / 5回のボタンは入力補助。セット変更で回数は変わらない。
3. 「計画を確認」で実条件・構成数・ケース数・総試行数を確認し、開始する。
4. 「実行状況」で生成・採点・再利用を別々に確認する。手動 / 5秒 / 15秒更新に対応。
5. 「比較結果」で保存回答の品質・消費・所要時間を比較し、「回答と根拠」から原文を開く。

診断は独自の短い応答確認で、通常能力の得点には使わない。development trackは抽出した問題の接続検証で、公式全体やQuick / Detailedの得点と区別する。

APIが思考・cached tokens・usageを返さない場合はnull。reasoningがcompletionの内数であるときは再加算しない。要求全体の出力throughputは入力処理・待機を含むため、native decode速度と混同しない。結果表は再利用した回答の元の計測も表示し、このrunで実際に観測した要求の消費は別に示す。

## CLIでも同じ計画を使う

`plan` の入力は `model_ids`、`presets`、`suite_id`、`run_settings`。presetはregistryの定義を、run_settingsは[共通実行設定](../execution/default.v0.draft.json)の `settings` を使う。UIの計画詳細にも解決した全値を表示する。

```sh
uv run beaconbench plan local/comparison.json
uv run beaconbench --env-file local/connection.env run p-PLAN_ID
uv run beaconbench status r-RUN_ID
uv run beaconbench stop r-RUN_ID
uv run beaconbench --env-file local/connection.env resume r-RUN_ID
uv run beaconbench results r-RUN_ID
```

出力された実際のIDを使う。resumeは同じ固定planを使う。preset・N・問題を変更する場合は新しいplan / runを作る。同じ生成fingerprintのcase / trialは検証後に再利用する。通信の途中結果は残し、完全な回答としては再利用しない。自動の生成再試行・品質によるやり直しは行わない。

## LCBの採点器を接続する

第三者のテストやsourceをrepoへ同梱しない。運用者が利用条件を確認し、上流revision、policy、試験データ、コンテナimage IDをprivate領域へ固定する。現在のbridgeは `compact14-scoring-v2` 形式の監査済みbundle（`policy.json`、`FILE-SHA256.json`、`lcb_policy.py`、`testing_util.py`、samples、worker、上流extractor）を受け取る。上流LCB checkoutをそのまま渡すだけではこの形式にならない。

採点ホストで `python -m beaconbench.lcb_executor --config local/lcb-executor.json` を使う。configは `policy_dir`、`worker_file`、immutable `image_id`、`extraction_source`、`output_root`、各ファイルの `lock_files: [{path, sha256}]` を持つ。workerファイル名は `lcb_worker.py`。bundle内のマニフェストとSHAも照合する。

実行はnetworkなし・非root・read-only・capabilityなしの一時コンテナで、1 CPU / 8GiB / swapなし / pids 128。nativeの6秒 / testとprocess allowanceを保つ。これは公式採点環境を動かすための設定で、モデルの思考や出力を短くする制限ではない。失敗でnative graderが早期停止した場合は不正解の証拠として残す。採点器の起動失敗・OOM・異常終了は誤答へ換算しない。所有する一時コンテナだけを停止・削除し、実行ログ・inspect・結果は保存する。

controller側の `local/scorer.json` は、`id`、`name`、`command`（固定引数配列）、任意の `cwd`、`lock_files` を指定する。別ホストならcommandからSSHで上記moduleを起動する。commandのstdinへcase ID・回答・grader版をJSONで渡し、stdoutでJSON判定を1つ受け取る。任意のshell文をWeb画面から登録する機能は設けない。

```sh
uv run beaconbench executor-add local/scorer.json
uv run python tools/prepare_lcb_ja.py --policy-dir local/lcb-policy --executor-id lcb-native --output local/lcb-ja.json --cases abc387_c
uv run beaconbench executor-qualify lcb-native local/controls.json
uv run beaconbench suite-import local/lcb-ja.json
```

controlは `[{expected_passed: true, payload: {case_id, answer, grader}}, ...]`。各ケースの同じgrader定義について、正答・誤答の両方を実際に採点する。受入後にcommandや固定ファイルが変われば計画を拒否する。`qualified: true` のフラグだけでは通らない。controlの原本と新しい受入証拠は残す。

日本語取込器はAtCoder公式の日本語原文を使い、問題・制約・入力・出力・例を保持する。元HTML、取得URL、内容SHA、statement SHA、対応テストSHAをprivate領域へ保存する。図が解答に必須の問題や対応policyのない問題は、人が確認して採用を止める。自動取得だけでQuick / Detailedへの採用を確定しない。

## 保存・運用上の境界

`.beaconbench/` にregistry、immutable plans、ケース資産、受入証拠、runs、回答再利用索引を保存する。秘密はenv参照のみで、実要求の認証headerは保存しない。原回答・SSE・usage・採点結果にはSHAを持たせ、変更を検出したら再生成で隠さず停止する。

計画作成時に実行コードも `runtime-sources/` へ保存する。workerはそのSHAを照合した `runtime-cache/` のコードで起動するため、アプリを更新しても同じplanの再開は当時の処理を使う。原本・cacheの不一致は自動修復や新しいコードへの差し替えで隠さない。新しい基盤で測りたい場合は新しいplanを作る。

server / workerが落ちても既存原本は残る。停止したworkerのleaseは所有状態を検証して回収し、生きている実行の予約は奪わない。追加の監視マシンへ推論・生成コード・重い採点を移さない。常用モデルの起動・停止・キャッシュ操作は現時点では行わない。
