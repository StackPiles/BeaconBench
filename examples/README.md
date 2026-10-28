# 契約例

このディレクトリのfixtureはすべて架空の設計例。公開ベンチマークの問題・実回答・実測速度・実測消費ではない。

- case、run、measurement、grade、translationは [schema](../schemas/README.md) に対応する。
- measurement例の入力100・出力20・思考8では、思考が出力に含まれるのでtotalは120。128へ足さない。
- cached 40は入力100の内訳。totalへ再加算しない。
- `native_decode_tokens_per_second: null` はnative decode速度が取得されていない例。
- [model.example.json](model.example.json) はendpointと鍵の環境変数名だけを持つ。モデルrevision・engine・能力は未確認で、実行可能な設定ではない。

実行を開始する設定には、fixture参照を含まず、固定した実資産・モデル・helperと受入証拠が必要。
