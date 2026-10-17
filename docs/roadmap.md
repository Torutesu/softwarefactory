# Roadmap

「次に足すもの」の優先順位メモ。コンテキストの「自律性の段階」に沿って並べています。

## 段階 2 → 3: シグナルの自動化

- [x] **シグナル → ミッションの自動化**: `factory ingest <src|url> --project X --run` で Seed 化 → mission 作成 → 実行まで一括
- [x] **本番シグナル取り込み**: `factory listen --port N --auto-create` で webhook (POST /signals) → seeds/ 自動 Seed 化 (+ `--auto-run` で実行まで)
- [ ] **SNS 反応の取り込み**: `factory ingest <url>` 汎用取込は済み。検索結果の定期収集とドラフト生成 (人間が seed.md 確定) は今後
- [x] **シグナル一覧**: `factory signals`

## 検証の拡強

- [x] **UI ベンチマーク比較**: `expect_png_similar` ステップ (標準ライブラリ PNG デコード + 類似スコア)。Playwright 撮影は将来オプション
- [x] **シナリオの自動リフレッシュ**: `factory holdout regen <id>` (既存シナリオを掃除して再生成)
- [x] **カナリア運用**: `factory canary <id> [--fix]` — main 再検証、FAIL 時 revert + 失敗要約 Seed + 再ミッション (`--run-complete` で実行まで)

## Worker / 資源管理

- [x] **OpenCode 側 usage/セッション**: JSON 出力 (`--format json` を config.args に追加) からテキスト/usage/sessionID をベストエフォート抽出
- [x] **クォータ予測**: `daily_call_limit` + `window_call_limit`/`window_minutes` (例: 5h 窓) の二段管理。超過ワーカーは候補の後ろへ
- [x] **並列 Worker**: `factory run-all --parallel N` (ミッション単位ロック + 空きポート自動選定)
- [x] **ツール制限の明示化**: claude `allowed_tools` / `disallowed_tools` → `--allowedTools` / `--disallowedTools` 透過
- [x] **サブスク既定の認証**: `auth: "subscription"` が既定 (API キー環境変数を子プロセスから外す) — Claude Pro/Max / ChatGPT Codex をそのまま使用
- [x] **OpenAI (codex) ワーカー**: `codex exec` 非対話実行
- [x] **OpenRouter ワーカー**: Chat Completions 直呼び (spec/holdout/judge 用途)
- [x] **ファクトリークロック**: `factory tick` (取込→実行→出荷→カナリア→修復の 1 巡) + `factory supervisor` (常駐)。`autonomy_level` 1..4

## 顧客体験

- [x] **PR 連携**: `factory pr <id>` — origin + gh があれば `gh pr create`、無ければ `reports/pr-draft.md`
- [x] **ダッシュボード**: `factory status --watch [秒]`
- [x] **セッション Resume**: ワーカー config `resume: true` で反復を跨ぐセッション継続 (claude `--resume` / opencode `--session`)
- [x] **モデル指定の per-mission 上書き**: `factory mission new ... --model opus`

## 次の候補 (未実装)

- **UI 撮影の自動化**: Playwright/AppleScript によるスクショ取得ステップ (現在はアプリ自身が PNG を出力する前提)
- **定時シグナル収集**: cron で検索/SNS/エラートラッカーを巡回して Seed ドラフト化
- **OpenRouter のコスト予測**: 既定 auth は api_key。実際の usage.cost の有無に応じた予測表示
- **ダッシュボード強化**: usage 窓 / canary / needs_human を `status --watch` に表示
- **複数リポジトリ横断の依存解消**: ミッション間の順序制約 (DAG)

## 保守メモ

- `mock` ワーカーはデモ・結合テストの要。実 LLM worker を追加しても、契約
  (prompt に入力 → 変更を出力) が同じならパイプラインへの影響はない
- `run-all --parallel` 併用時、usage JSON の同時書き込みはベストエフォート (破損時は記録欠落のみ)
