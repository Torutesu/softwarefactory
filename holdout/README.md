# Holdout (ホールドアウトシナリオ)

このディレクトリは **コードベース外** の検証シナリオ置き場です。

## 絶対ルール
- 実装 Worker に、このディレクトリのパスも中身も渡さない。
  Worker には検証結果 (pass/fail + 失敗概要) だけをフィードバックする。
- シナリオの生成は Spec Generator とは別セッションの Worker が行う
  (実装者と共犯関係を作らないため)。
- シナリオは `missions/<id>/` に対応付けられ、実行は検証ハーネスだけが行う。

## 形式
`<mission-id>/SC-001.json` の形式:

```json
{
  "id": "SC-001",
  "title": "何を確認するか",
  "weight": 1.0,
  "steps": [
    {"name": "health", "cmd": "curl -sf http://127.0.0.1:${FACTORY_PORT}/health", "expect_exit": 0}
  ]
}
```

ステップの期待値フィールド: `expect_exit` (既定 0), `expect_contains`, `expect_not_contains`, `expect_regex`。

UI の画像比較 (オプション): Seed に基準スクショを含めた場合、`expect_png_similar` で
レンダリング結果との類似判定ができます。基準画像はこの holdout ディレクトリにコピーしてください。

```json
{"name": "render", "cmd": "curl -s http://127.0.0.1:${FACTORY_PORT}/shot.png -o render.png",
 "expect_png_similar": {"file": "render.png", "to": "bench-home.png", "ratio": 0.85}}
```

- `file`: 作業ディレクトリ ($FACTORY_REPO) 基準の実行時画像
- `to`: holdout ディレクトリ基準の基準画像
- `ratio`: 合格閾値 (0..1、省略 0.85)

## 設計のポイント
- 仕様の主要な機能要件を漏れなく独立に検証すること。
- 実装リポジトリのテストと重複しない、ブラックボックスな視点で書く。
