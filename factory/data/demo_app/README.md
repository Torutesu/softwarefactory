# デモアプリ (メモAPI)

社内ソフトウェアファクトリーのデモ用に作った、ごく小さな製品リポジトリ。

- `app.py`: stdlib http server。health / GET /notes が実装済み。作成・削除は未実装。
- `tests/test_app.py`: 既存の単体テスト。

実運用ではこのディレクトリを任意の既存リポジトリ (git) に置き換える。
