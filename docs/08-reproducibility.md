# 08 保存・同一性・再開・再採点・公開

## 保存する単位

```text
runs/<run-id>/
  plan.json                     解決済みの実行条件・版・role
  manifest.json                 状態と成果物索引
  assets.lock.json              上流・翻訳・環境・helperの固定情報
  events.jsonl                  append-onlyのイベント
  trials/<case-id>/<trial-id>/
    case-ref.json
    requests/                   credentialを除いた実要求
    responses/                  原応答・usage・終了理由
    stream/                     到着順と時刻を持つchunk
    actions.jsonl               tool要求・応答・状態差分
    final-answer.txt
    measurement.json
    completion.json             完了markerとhash
  grades/<grader-fingerprint>/  生判定・証拠・派生点
  reports/<report-version>/     比較・全文・未確認事項
  restoration.json              所有資源と常用環境の復帰結果
  FILE-SHA256.json
```

原本と派生結果を分け、上書きによる再採点をしない。最終回答がない場合はその理由を残す。思考はendpointが返したものだけを記録し、原本から削除して最終回答に見せかけない。表示・公開の可否は別のpolicyで決める。

## 同一性

`run_id` は試行の識別子、fingerprintは条件の識別子。時刻が違うだけでcaseの同一性を失わせず、同じmodel名だけで回答を再利用しない。

生成fingerprintは次を含む。

- source task ID・source revision・資料・初期状態・日本語packのhash。
- 実要求本文、system指示、tool schema、template、tokenizerと、確認可能な入力ID。
- モデル重みrevision・量子化・engine build・worker拡張・GPU graph・MTP・KV・ハードウェア。
- 有効サンプリング、思考設定、capacity profile、cache policy、並列度、seed、native終了条件。
- 固定helperのID・revision・prompt・sampling・環境、agent harnessとadapterの版。

Suite名やQuick/Detailedの表示名だけは生成fingerprintへ入れない。同じcase・trial・全実行条件ならQuickからDetailedへ再利用できる。予定値だけ一致し、実際の要求・有効設定の確認がない回答は同一条件扱いにしない。

採点fingerprintは、回答原本SHA、grader code、テスト集合、許容誤差・除外規則、コンテナdigest、採点helper・prompt、adapter版を含む。生成と採点のfingerprintを分け、採点だけを新しい版で実行できるようにする。

JSONのfingerprintはUTF-8、key順固定、有限数、明示的なnullで作る。入力・コード・第三者の原本byte列を勝手にUnicode正規化・改行変換して同じhashと扱わない。raw SHAとcanonical object SHAを区別する。

## 再開

1. run plan・lock・schemaと固定SHAを確認する。
2. trialのcompletion marker・全成果物・要求・原回答SHAを照合する。
3. 完成済み・同一条件の回答だけを再利用する。
4. 不完全なstreamや途中のactionは保全し、未完了attemptとして新しいattemptを作る。
5. 既存grader fingerprintと一致する完成判定だけを再利用し、再利用件数・元runを表示する。

ベンチマーク固有の再開が完了ファイルだけを見る場合は、その手前で共通の同一性検証を行う。品質の悪い完成回答を再試行対象へ戻さない。

## 再採点と点検

別のgrader lockで新しいgrade directoryを作る。元の回答・原判定・点数を残す。人または別モデルの点検は、指摘・根拠引用・変更理由・点検者・対象範囲をannotationとして重ねる。

自動判定と点検が一致した件数だけで判定器の正確さを主張しない。不明、境界例、点検していない範囲、実際に人が確認した範囲を表示する。

上流の問題修正・テスト除外を採用するときは、モデル結果に依存しない理由と新しいpolicy版を作り、同じ新条件で各モデルの保存回答を再採点する。異なるpolicyの得点を同じ比較列に混ぜない。

## 公開

通常のrun原本はGitの対象外。公開用exportは明示的に実行し、項目allowlist、第三者資産の条件、秘密情報、個別環境のmetadataを確認する。公開用を作るために手元のraw記録を改変しない。

公開候補は構成の公開情報、ケースの配布可能な識別情報、条件SHA、集計、採点器の版、分母、消費・時間の測定由来、再現手順。回答全文・問題全文・toolのraw結果は個別に公開可否を判断する。host情報は公開用の別名に置換できるが、手元の条件照合には元情報のhashを保持する。

Gitへ `.env`、非公開資料、重み、run原本、API header、個別host設定を含めない。自動のgitignoreだけを公開判断の代用にしない。

## 旧評価の移行

既存14問の条件・共通採点v2を、同じ入力とテスト集合で再現できる互換manifestへ移す。旧runは原本をそのまま外部のprivate保存先に残し、importは読取専用とする。保存形式が不足する値はunknownとし、新しいフォーマットへ移しただけで完全再現を宣言しない。

個別モデル用の起動・復帰処理、個別host path、認証や方向データを、共通frameworkのコードへ持ち込まない。必要な再利用部分は依存と権利を確認し、汎用adapterへ分けて検証する。
