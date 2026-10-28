# 05 実行条件と測定指標

## 条件固定

全構成でcase本文、資料、翻訳、tool schema、環境初期状態、補助モデル、採点器、試行seed、cache方針、並列度を対応させる。モデルごとの公式templateは一度だけ適用し、レンダリングとtokenizerの版を保存する。

初版の比較条件案は [common-ja.v0](../protocols/common-ja.v0.draft.json)。温度1.0、top-p 0.95、同時実行1、思考ありを基準とする。top-k / min-p等は無効または中立の意味をbackendごとに検証する。要求を省略しただけで中立になったと判断しない。正式なパラメーター変換はmodel adapter受入で固定する。

参照する容量profileはcontext 262,144、一要求の最大出力131,072（思考を含む）。これは従来の比較容量に対応するprofileで、Quick独自の短い出力制限ではない。非対応モデルへ黙って小さい値を送らない。別容量profileを作り、その結果は別系列にする。コンテキスト切り捨て・自動要約を追加する場合も別プロトコル版が必要。

独自の累積出力・tool呼び出し・agent turn・全体deadlineはnull。native benchmarkの終了ルールは同じtaskの全構成で揃える。ルール上の回数制約を取り払った派生実験は元の公式条件と混同しない。

公式再現用profileはbenchmark自身のサンプリング・helper・全task範囲を参照する別系列。温度1.0の抽出日本語セットを、そのまま公式ランキングのスコアとして出さない。

## キャッシュと速度測定条件

標準比較案は、case間のprefix cacheを冷状態にし、同じ対話内では通常の再利用を許す。複数caseの並列実行は行わない。cache無効・case単位resetを検証できないbackendはその事実を保存し、cold条件と同じ速度系列にしない。

稼働中サービスの管理APIや他利用者のcacheを消すことを標準操作にしない。専用endpointまたは明示的に所有した実行環境を使う。互換14問は元のcache政策を維持し、標準profileとの差を保存する。

固定長32Kのdecode試験は任意の独立した性能track。EOSを無視する性能試験を通常課題の生成へ適用しない。warm-up、入力長、目標長、context使用量、反復数、engineのbatch設定を記録する。

## トークンの定義

| 値 | 定義 |
|---|---|
| `prompt_tokens` | 全API呼び出しで実際に処理した入力の累積。毎回送る履歴もその都度数える |
| `cached_prompt_tokens` | 上記入力のうちcacheから再利用した部分。promptへ加算しない |
| `completion_tokens` | APIが出力消費として報告した数。思考込みかをnative仕様で記録 |
| `reasoning_tokens` | completionの内訳として得られる場合に保存。completionへ重複加算しない |
| `visible_output_tokens` | 実測できる場合の本文・tool出力等の内訳。思考との差分から勝手に推定しない |
| `total_tokens` | provider値と計算値を区別。通常の計算はprompt + completion |
| `uncached_prompt_tokens` | 両値が既知で0以上のときだけ prompt - cached を計算 |

usageは `provider_reported / engine_reported / retokenized_estimate / unavailable` の由来を持つ。SSEの最終usageが得られなければ、応答やengine計測で回収できるか確認し、不明を0にしない。再tokenizeによる近似値は実消費と区別する。異なるtokenizerのtoken/sは同じ作業量を意味しない。

思考をcompletionに含めるAPIでは、`completion + reasoning` を総消費にしない。MTP等のdraft候補・受理token数は推測実行の診断値であり、通常のcompletion数へ足さない。

評価対象、顧客役、変換役、採点役、言語判定役を別々に集計する。合計を出す場合はcoverageと各roleの既知・不明を併記する。API再試行・失敗呼び出しの消費も、分かる範囲でattemptごとに残す。

## 時間の定義

測定には各processのmonotonic clockを使い、監査用のUTC時刻も残す。異なるhostのmonotonic値を直接引かない。共通observerから測るか、各区間の時計の所在を明記する。

| 値 | 開始と終了 |
|---|---|
| `request_wall_seconds` | 要求を送り始めてから、生成のterminal応答まで |
| `ttft_seconds` | 同開始から最初の意味のある出力chunk（思考・本文・tool引数）まで |
| `ttfv_seconds` | 同開始から最初のユーザー表示本文まで。思考のみならnull |
| `task_wall_seconds` | endpoint ready後にcaseを開始してから、全対話・toolと最終成果物の保存を終えるまで |
| `support_wait_seconds` | 課題中に顧客役・変換役を待った区間 |
| `tool_wall_seconds` | 課題中の各toolの実時間 |
| `grade_wall_seconds` | 保存回答の採点を始めてから判定保存まで |
| `model_load_seconds` | 起動からreadyまで。課題時間へ黙って混ぜない |
| `run_wall_seconds` | 準備・load・生成・採点・復帰を含むrun全体の実時間 |

task_wallはhelper待ち・tool時間を含む。含まれる部分時間を再び足して総時間を水増ししない。非同期に重なる時間は単純加算せず、区間とexclusive / inclusiveの意味を保存する。

全生成後に一括採点する場合、generation秒 + grade秒は処理量の指標であり、回答が採点済みになるまでの実経過時間ではない。待ち時間を含む実時刻との差を別に表示する。

## 速度の表示

1. **課題完了時間**：実用比較の主な時間指標。品質と並べる。
2. **要求全体の出力throughput**：completion_tokens / request_wall_seconds。prefill・待ちを含むのでdecode速度と呼ばない。
3. **native decode token/s**：engineが定義とともに報告した値。取得できるときだけ表示。
4. **streamからのdecode推定**：最初と最後の出力区間とchunk内token数を確認できる場合の推定。first chunkを無条件に1 tokenと仮定しない。

APIのchunk時刻をGPU kernelの時間として扱わない。streamingしないモデルのTTFTを全応答時間から作らない。各metricはnullableにし、nativeと推定を別の列にする。

## 終了・失敗

`eos / task_completed / benchmark_deadline / engine_output_limit / context_limit / tool_error / transport_lost / process_failure / user_stop / invalid_simulator` を区別する。自由文の終了理由は原文のまま別に残す。

品質のための再生成は0回。通信失敗の扱いはattemptを追加し、要求がserverへ受理されたか不明な場合は自動的に成功回答を置き換えない。手動再開でも途中回答を保全する。基盤エラーの再試行、公式の対話上の自己修正、best-of-Nは別概念として記録する。
