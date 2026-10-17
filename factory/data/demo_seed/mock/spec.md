# メモAPIの実装 (demo-app)

## 目的
社内ツール用のシンプルなメモAPIを完成させる。現在は health と一覧取得のみ実装済み。

## ユーザーと使い方
社内ユーザーが CLI/スクリプトから HTTP でメモを登録・削除する。

## 機能要件
- FR-1: `POST /notes` でメモを作成できる。body は `{"title": "...", "body": "..."}`。成功時 201 と作成済みメモ JSON (id 連番・1始まり) を返す
- FR-2: title は 1〜100 文字の文字列。空・101文字以上・文字列以外は 400 + `{"error": "..."}` を返す
- FR-3: `DELETE /notes/<id>` でメモを削除できる。成功時 200 `{"ok": true}`、以後 GET では 404
- FR-4: 存在しない id への DELETE / GET は 404
- FR-5: 既存機能 (GET /health, GET /notes, GET /notes/<id>) は退行しない

## 検証方針
- 実装リポジトリの単体テスト (`python3 -m unittest`) が成功すること
- 起動した HTTP サーバへの curl ベースの検証シナリオで確認する

## 補足
- サーバは環境変数 FACTORY_PORT で待ち受けること (既存実装済み)
