# Changelog

## 0.3.0 (2026-11)

- マージ前に最新 main を取り込んで再検証 (`revalidate_before_merge`、失敗は `stale_base`)
- spec の「スコープ」と実際の差分をマージゲートで突き合わせ (スコープ外 → behavioral、触らない領域 → sensitive)
- ミッション依存 `mission new --after ID` と、プロジェクト別クォータ (`daily_mission_limit` / `max_concurrent`)
- 枠の見込み `usage --forecast`。dev の実ワーカー全員が枠不足の見込みなら始めない (`forecast_defer`)
- ログのローテーション、`factory gc` (保持期間)、`factory backup` / `factory restore`、tick の日次ハウスキーピング
- macOS `sandbox-exec` による隔離 (`sandbox`: `off` / `harness` / `all`)
- `factory --version`、state.json のスキーマ版 (v2) と移行

## 0.2.0 (2026-10)

品質・セキュリティ強化 (`docs/plan-2026-10-hardening.md`): holdout の改ざん検知、秘密環境変数の除去、
mock フォールバック禁止、protected_paths、listener の強化、孤児回収、graceful shutdown、
`doctor` / `selftest` / `config check` / `needs-human`。

## 0.1.0

初期版: spec → holdout → dev → harness → judge のループ、マージゲート、カナリア、tick / supervisor、
listener、collect、bench。
