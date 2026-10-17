# 運用手順 (Operations)

## 日常の流れ (段階1: 人間が Seed を投げる)

```bash
bin/factory init ./my-factory
cd my-factory

# (1) Seed を用意: seeds/<name>/seed.md + 参照材料 (スクショ jpg/png、URL、説明)
bin/factory seed add path/to/materials --name next-app

# (2) ミッション作成 (既存コードの update ならそのリポジトリのパスを --project に登録しておく)
bin/factory mission new "◯◯を作る" --project demo_app --seed next-app \
    --kind greenfield --iterations 3 --pass-score 80

# (3) 自律ループ
bin/factory run MYYYYMMDD-HHMMSS-xxxx

# (4) レポート確認 → worktree を読む → 良ければマージ
bin/factory report MYYYYMMDD-HHMMSS-xxxx
bin/factory merge MYYYYMMDD-HHMMSS-xxxx
```

`run` 内部の流れ:

```
仕様生成 (別セッション) → ホールドアウト生成 (さらに別セッション) →
反復 { 実装 Worker → Harness (build/test/シナリオ) → Judge } → 合格か上限反復まで
```

各反復の prompt は `missions/<id>/prompts/` に、worker ログと検証結果は
`missions/<id>/reports/run-<ts>/iter-N.md` に残ります。创业者が読んで引き継げます。

## 既存リポジトリを対象にする

`.factory/config.json` にプロジェクトを登録 (init 後に手編集、または `--repo PATH` を使う):

```json
"projects": {
  "my-svc": { "name": "my-svc", "path": "repos/my-svc", "main_branch": "main", "test_cmd": "pytest -q" }
}
```

バグ報告・改善依頼は Seed に書くだけ:

```bash
bin/factory seed add ./bug-report-dir --name bug-001
bin/factory mission new "orders API の500バグ修正" --project my-svc --seed bug-001 --kind update
```

## Worker の設定 (サブスクの枠を既定にする)

**既定はサブスク (ログイン済みプラン)** です。`claude login` / `codex login` を済ませて
おけば API キー不要。オーケストレータは `auth: "subscription"` (既定) のとき
`ANTHROPIC_API_KEY` / `OPENAI_API_KEY` を子プロセスから外すので、環境にキーが
あってそちらに流れる事故が起きません。キー課金にしたい worker だけ
`auth: "api_key"` にします (OpenRouter は常に `OPENROUTER_API_KEY`)。

`.factory/config.json`:

```json
{
  "use_worker": "auto",
  "worker_order": ["claude_code", "codex", "opencode", "openrouter", "mock"],
  "judge_worker": "claude_code",
  "workers": {
    "claude_code": { "type": "claude", "auth": "subscription", "model": "sonnet",
                     "timeout": 1800, "daily_call_limit": 60, "window_call_limit": 30 },
    "codex":       { "type": "codex", "auth": "subscription", "model": "gpt-5.2",
                     "daily_call_limit": 40, "window_call_limit": 20 },
    "opencode":    { "type": "opencode", "timeout": 2400 },
    "openrouter":  { "type": "openrouter", "model": "anthropic/claude-sonnet-4.5" }
  }
}
```

- 朝イチで `bin/factory worker ping` を叩き、CLI の生存 (`[ok] claude_code: (auth=subscription)` 等) と
  `OPENROUTER_API_KEY` の有無を確認。
- `bin/factory usage --days 7` で日次の呼び出し・トークン・コストを見る。
- 枠が逼迫したら `daily_call_limit` / `window_call_limit` を下げる (例: 5時間窓で30回)。
  超過したワーカーは Router が候補の後ろに回し、quota エラー時も自動フォールバックする
  (Claude 枠 → Codex 枠 → OpenCode → OpenRouter → mock)。
- 特定ミッションだけ別 worker で回したい: `--worker opencode`。
- ミッション単位のモデル: `mission new ... --model opus` (config の model を上書き)。
- 並列実行: `run-all --parallel 3`。ミッションごとに worktree とポートが分かれる
  (ポートは使用中なら空きポートを自動選択)。
- セッション継続: `resume: true` のワーカーは反復 2 回目以降、前反復のセッション
  (`--resume <id>` / `--session <id>`) に続けて相談する。フォールバックでワーカーが
  変わった場合は継続しない (文脈は prompt 内の要約で引き継がれる)。

## シグナルの取り込み (Ingest / Webhook)

```bash
# 手動: 素材/URL を Seed 化し、--project があればミッションも作る
bin/factory ingest ./tickets/UX-231 --project demo_app --title "一覧の絞り込み" --kind update
bin/factory ingest https://example.com/feedback/1234 --title "UI 改善の投稿"

# 常駐: 監視/Slack/Teams からの POST を受けて Seed 化 → ミッション化 → 実行まで
bin/factory listen --port 8787 --bind 127.0.0.1 --project demo_app --auto-create
#   POST /signals  {"title","body","source","project","files"}   / GET /health

bin/factory signals          # 取り込んだシグナル一覧
```

listener のログは `.factory/listener.log`、取り込み履歴は state.json の `signals` に残る。
外部システム側の Webhook URL 例: `http://127.0.0.1:8787/signals`。

## マージ後: カナリア検証と PR

```bash
bin/factory merge M... --yes
bin/factory canary M...            # main 先頭を一時 worktree で build/test/シナリオ再実行
bin/factory canary M... --fix --run-complete
#   FAIL 時: main 上の該当 squash コミットを revert → 失敗要約を Seed 化 →
#   再ミッション作成 (priority=1) → 即実行
bin/factory pr M...                # origin + gh があれば PR、無ければ reports/pr-draft.md
```

カナリアはマージが原因で main が壊れた場合の早期発見・自動ロールバック用。
revert は未コミット変更があると中止する (安全第一)。main が既に revert 済みなら
二重 revert をせず `already-reverted` として扱う。

## 自律運転: tick (ファクトリークロック) と supervisor

1 回の tick は「取込 → 実行 → 出荷 → カナリア → 修復」の一巡:

```bash
bin/factory tick --dry-run            # 計画だけ表示 (state 変化なし)
bin/factory tick                      # 1 巡だけ回す (cron に登録する形)
bin/factory supervisor --interval 300 # 常駐で tick を回し続ける
```

自律度は `config.autonomy_level` (1..4) で段階導入:

| level | 挙動 |
|---|---|
| 1 | 人間が queue したミッションを実行・報告 |
| 2 | + low リスク完了ミッションを自動マージし、マージ後に自動カナリア |
| 3 | + 未割当 Seed (`seeds/` に置いたシグナル) からミッション自動作成。割当先は `autopilot_project` (未設定なら project が 1 つだけのときそれ) |
| 4 | + カナリア失敗時 revert + 修復ミッションを即実行 (完全自動) |

- **エスカレーション**: 高リスク・予算枯渇・修復ラウンド上限 (`max_repair_rounds`)・
  Seed の project 未確定・反復上限は `needs_human` に積まれ、tick の要約 JSON と
  `state.json` の events に残る。`notify_url` を設定すると要約を Webhook POST 。
- **逆振れ防止**: 修復ミッションには `repair-of:<id>` が付くので、修復の修復が
  `max_repair_rounds` (既定 1) を超えると自動出荷されない。
- **冪等**: tick は 2 巡連続で回しても 2 巡目は無活動で終わる (全 Seed 割当済み・
  queued なし)。cron/常駐どちらでも安全。

cron 例 (5 分ごとに一巡):

```
*/5 * * * * cd /path/to/my-factory && /path/to/bin/factory tick
```

listener (`bin/factory listen`) は常駐の Webhook 受付として supervisor と並行運用する。

## ホールドアウトの保守

- シナリオ一覧: `bin/factory holdout list M...`
- 陳腐化したら再生成: `bin/factory holdout regen M...` (done ミッションは `--force` 必要)。
  既存 SC-*.json とヘルパーを掃除してから再生成する。実装側 worktree には影響なし。
- UI 比較シナリオ: Seed に基準スクショ (PNG) を入れ、holdout にコピーして
  `expect_png_similar` を使う (holdout/README.md 参照)。</think>## 高リスクのミッション

削除系・本番近接の作業は `--risk high` で作る:

```bash
bin/factory mission new "..." --project my-svc --seed x --kind update --risk high
```

`run` は `--confirm` がないと拒否します (ヒューマンゲート)。

## 検証だけやり直す (人間が手を入れた後の確認)

```bash
bin/factory validate MYYYYMMDD-HHMMSS-xxxx --judge
```

Worker を呼ばずに Harness と Judge だけ実行します。

## ミッションの状態遷移

```
queued → (spec) → (holdout) → dev (n 反復) → done | failed
failed は再実行可 (work の変更は残る)
```

- done の後も `validate` や `run` での再検証が可能 (`run` は done をスキップ)
- merge は worktree ブランチ `factory/<id>` を main に squash して worktree を掃除
