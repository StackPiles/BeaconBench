# データ契約案

JSON Schema Draft 2020-12を使用する。これらはM0の契約案であり、完全な実行ログ形式の実装ではない。hashと型を持つ最小のcase / run / measurement / grade / translationを定義する。M1以降で実際のraw request、event、asset lockの契約を追加する。

| Schema | 用途 | 架空の例 |
|---|---|---|
| [case](case.schema.json) | 元task・言語・観測・非公開採点資産の参照 | [case fixture](../examples/case.fixture.json) |
| [run](run.schema.json) | 計画・状態・版・分母・復帰 | [run fixture](../examples/run.fixture.json) |
| [run settings](run-settings.schema.json) | セットと独立した試行回数・seed・全試行集計 | [run settings fixture](../examples/run-settings.fixture.json) |
| [measurement](measurement.schema.json) | 呼び出しrole・usage・時間・終了理由 | [measurement fixture](../examples/measurement.fixture.json) |
| [grade](grade.schema.json) | 生判定への参照と版付きmetric | [grade fixture](../examples/grade.fixture.json) |
| [translation](translation.schema.json) | 原文・翻訳SHA、変更可能箇所、検証状態 | [translation fixture](../examples/translation.fixture.json) |

例はすべて `fixture: true` であり、モデルを動かして得た回答・速度・消費ではない。`fixture://` は契約説明用の架空のasset参照で、解決可能な取得先ではない。runtimeはfixtureを本評価へ投入してはいけない。

type検証に加え、usageの包含関係・時間の順序・readyと資産の整合等は意味上の検証が必要。M0のcheckerはschema自体とfixtureの適合、主要な二重計上・不整合を検査する。将来の実run受入にはasset lockと実ファイルSHAの照合も必要。

参考：[JSON Schema Draft 2020-12](https://json-schema.org/draft/2020-12)、[python-jsonschema](https://pypi.org/project/jsonschema/)。
