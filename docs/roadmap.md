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

## 外部記事から得た観点 (2026-10: Firecrawl「How to Build an AI Software Factory」)

上記次候補に加え、同記事で示された観点のうち未実装のもの。

- **Live web コンテキスト**: リポジトリだけでは分からない事実 (依存の breaking change・
  非推奨化・status page) を intake/検証に供給する。URL `ingest` はあるが、
  ワーカー実行中の参照は `disallowed_tools` で WebSearch を切っている。用途限定
  (spec/holdout セッションのみ許可する等) で開けるか検討
- **レビュー容量のメトリクス**: ボトルネックはレビュー容量 (記事: dotnet/runtime では上位 2 名の
  レビュアーで agent PR への指摘の 36%)。`usage --waste` の反復平均・失敗・上限ギリギリ集計は
  実装済み。次は `status --watch` にレビュー待ちティア数と `needs_human` 滞留を出す
- **エージェント間レビュー** (将来): behavioral ティアの中間レビューを別セッションの
  ワーカー (実装者と共犯でない) に任せる、現行 judge の拡張。指摘は「差分への指摘」
  「シナリオが見落とした観点」で価値を測り、confidence スコアによるフィルタは使わない
  (Faire の教訓: 自己申告 confidence は品質シグナルにならない)
- **失敗ヒエラルキーの運用** (Spotify の順序): generation 時の失敗 < CI 時の失敗 <
  通って機能的に誤り、というダメージの順で狙う。カナリアは最悪の種 (通って誤り) を
  拾うが、judge の `priorities` を generation 時の失敗として早めに Seed 化する
  仕組みは信号活用の余地あり
- **worktree の生涯管理**: 失敗ミッションの worktree は調査用に残す運用の明文化と、
  `git worktree remove` に相当する掃除コマンドの入口

## 外部記事から得た観点 2 (2026-10: Uber「Running a Software Factory Efficiently at Uber Scale」)

コスト方程式 (`users × sessions × turns × requests/turn × tokens/request × price/token`)
のミドル 3 項を絞るレバー。適用済みは design.md の「コスト方程式との対応」参照。

- [x] **用途別モデル/ワーカー路由** (`purpose_workers`) — 記事で最も効果が大きかった
  「サブエージェントに安いモデルを既定にする」の本リポジトリ流の一般化
- [x] **成果単価の記録と表示** (`Iteration.tokens/cost` → `Mission.cost_usd` → status/report)
- [x] **セッション分析の簡易版** (`usage --waste`: 16 アンチパターン相当のうち記録から拾える 4 種)
- [x] **予算 nudge** (tick `budget_nudges`)
- [x] **プロンプトの prefix 安定化と再送削除** (spec を先頭へ、反復 2 以降は再掲省略)
- [x] **ベンチ駆動のパレート最適モデル選択** (`factory bench`): 社内 SWE ベンチをタスク
  JSON (共通の spec/holdout を同梱) として定義し、dev 用途をワーカーに固定して完了率・
  score・cost を実測、`dev` 推奨と反映例を出す。次の発展: spec/holdout/judge 用途の
  テキスト品質比較 (現状は dev のみ)、モデル差分の比較 (`--model` を変えた variant 列挙)
- **Skill / 手順の自動改善**: judge の `priorities` を束ねて spec テンプレートや
  holdout 生成のガイダンスを更新するループ (記事の Continuous Skill Optimization 相当)

## 外部記事から得た観点 3 (2026-10 調査: Loop Engineering ほか)

Firecrawl「Loop Engineering」(2026-06) — 「プログラムがモデルを while で呼ぶ」構造の
設計論。本リポジトリはまさにこの構造なので、対照表として価値が高い。

- [x] **loop の構成要素はほぼ全実装済み**: trigger (listen/supervisor/seed)、isolation
  (worktrees)、codified context (spec/seeds)、writer/reviewer 分離 (judge)、memory
  (mission.json/state.json)。connector は listen (Webhook) のみで MCP は意図的に外し済み
- [x] **halting の 3 保障**: 反復上限 (`max_iterations`)・支出上限 (usage daily/window) に加え
  **noprogress 検知** (`noprogress_iterations`, 既定 2): 作業ツリー内容の指紋がワーカー呼出の
  前後で変わらない反復が連続したら打ち切り + `halt_reason=noprogress` で needs_human
- [x] **reviewable ゲート**: `build.json` に test も holdout シナリオも無いミッションは dev 前に
  `blocked` (`unreviewable`)。build.json は spec 生成で決まるため intake ではなく dev 直前で判定。
  repetitive / valuable の判定は未実装 (人間の Seed 確定に委ねる)
- **comprehension debt**: 「loop が速いほど理解が遅れる」— マージゲートの人間レビュー
  (behavioral/sensitive) がその防波堤。docs に明文化済み、運用で維持

Anthropic「2026 Agentic Coding Trends Report」(resources.anthropic.com PDF) — 8 トレンド。

- **マルチエージェント化 (Trend 1-2)**: agent teams / orchestrator。ロードマップの
  「エージェント間レビュー」節と同一方向 (Firecrawl 記事と重なり、優先度裏付け)
- **multi-part specs のハンドオフ表** (Trend 3 相当): why / success / scope (触ってよい
  領域) / risks / when-to-escalate の 5 問を spec に持たせる。本リポジトリの spec テン
  プレート (spec_generator.SPEC_TEMPLATE) に「スコープ」「エスカレート条件」を追加済み [x]。
  why/success/risks は既存の 目的/機能要件/非機能要件 が担う。次: spec のスコープと
  merge gate の実差分を突き合わせる (スコープ外変更をティア引き上げの根拠にする)
- 参考数字: multi-file 編集 34%→78%、エンタープライズ採用の拡大。orchestration 前提の
  レビュー容量問題が本命 (dotnet/runtime のレビュアー集中と同型)

Firecrawl「LLM Token Optimization: 9 Techniques in the Order to Apply Them」(2026-10-13) —

- 既適用: prompt caching (prefix 安定化)、context を削る (brief_spec)。
- [x] 出力の上限設計: judge の comments 400 字 / priorities 3 件×160 字 (prompt 指示 + 切り詰め)
- 次候補: バッチング
  (複数小タスクの同時投入)、用途別コンテキスト構造の継続見直し。実装は軽いので、
  次回実装バッチに混ぜやすい


## 保守メモ

- `mock` ワーカーはデモ・結合テストの要。実 LLM worker を追加しても、契約
  (prompt に入力 → 変更を出力) が同じならパイプラインへの影響はない
- `run-all --parallel` 併用時、usage JSON の同時書き込みはベストエフォート (破損時は記録欠落のみ)
