# SoftwareFactory — 社内ソフトウェアファクトリー

StrongDM(非対話型開発)と Factory.ai(組織全体のフィードバックループ)の考え方を参考にした、**社内専用**の自律開発オーケストレータです。

Seed(ベンチマーク/要件/バグ報告)を投げると、エージェントが 仕様生成 → ホールドアウト設計 → 実装 → 検証 → 改善 をループで回し、**コード外の検証シナリオとLLMジャッジ**で判定します。人間は意図の定義・最終判断(マージ)に専念できます。

```
[Signal / Seed]
   ├─ ベンチマーク (UI/プロダクト/スクショ/URL)
   ├─ 新規要件・バグ・改善依頼
   └─ 人間からの引き継ぎ
        ↓
[Factory Orchestrator]  (factory/)         ← このリポジトリ
   ├─ Spec Generator (Seed → spec.md + build.json)
   ├─ Loop Controller (収束するまで反復)
   └─ Worker Router (Claude Code ⇄ OpenCode ⇄ Mock 切替)
        ↓
[Worker]  非対話 CLI (claude -p / opencode run)
        ↓
[Validation Harness]  (コードベース外で実行)
   ├─ build / test コマンド実行
   ├─ ホールドアウトシナリオ (holdout/<mission>/, Worker には中身非公開)
   └─ LLM-as-Judge (満足度スコア)          (状態: .factory/state.json)
        ↓
[Output]
   ├─ レポート (missions/<id>/reports/)
   ├─ worktree ブランチ (factory/<mission-id>)
   └─ 次の改善シグナル (judges → signals)
```

## クイックスタート (デモ)

```bash
# 任意の作業ディレクトリで
/path/to/SoftwareFactory/bin/factory init ./my-factory --demo

cd my-factory
/path/to/SoftwareFactory/bin/factory mission new "メモAPIの実装" --project demo_app --seed demo-app
/path/to/SoftwareFactory/bin/factory run <ミッションID>
/path/to/SoftwareFactory/bin/factory report <ミッションID>
/path/to/SoftwareFactory/bin/factory merge <ミッションID>
```

`--demo` は小さなサンプル製品リポジトリ (`products/demo_app`) と、
Seed (`seeds/demo-app`) を自動投入します。CLI が無い環境では **Mock worker** が
決定的にパッチを当てるため、パイプライン全体 (仕様 → ホールドアウト → 実装 →
Harness → レポート → マージ) を即座に体験できます。

## 本番モードへの切替

`.factory/config.json` の `worker_order` で優先順を指定します (基本はサブスク枠の Claude Code 優先)。

```json
{
  "use_worker": "auto",
  "worker_order": ["claude_code", "opencode", "mock"],
  "judge_worker": "claude_code",
  "workers": {
    "claude_code": {
      "type": "claude",
      "model": "sonnet",
      "timeout": 1800,
      "daily_call_limit": 60,
      "window_call_limit": 30,
      "window_minutes": 300,
      "resume": true,
      "allowed_tools": ["Bash", "Edit", "Write", "Read", "Grep", "Glob"],
      "disallowed_tools": ["WebSearch"]
    },
    "opencode": { "type": "opencode", "model": "", "timeout": 1800 }
  },
  "max_attempts": 2,
  "judge_pass_score": 80
}
```

- `claude_code` … `claude -p "<prompt>" --output-format json --dangerously-skip-permissions`
- `opencode` … `opencode run "<prompt>"` (config `args` に `--format json` 等を追加可)
- **フォールバック**: 予算超過・quota/timeout エラー時は次のワーカーへ自動切替
- **二段予算**: `daily_call_limit` (日次) + `window_call_limit`/`window_minutes` (例: 5時間窓) の
  両方を監視し、超過ワーカーは候補の後ろに回す (サブスク枠.Consumer保護)
- `allowed_tools` / `disallowed_tools` … Claude Code の `--allowedTools` / `--disallowedTools` に透過指定
- `resume: true` … 反復2回目以降、同一ワーカーのセッションを継続 (`--resume <session-id>`)
- ミッション単位のモデル指定: `factory mission new ... --model opus` で config を上書き
- 使用量は `.factory/usage/` に日次+呼び出し時刻を記録 (`factory usage` で表確認)

## シグナル → Seed → ミッション (Ingest)

外部/日常のシグナルを Seed 化する窓口が 3 種あります。

```bash
# 1) 手動取込: ディレクトリ/URL を seeds/ に化け物なく放り込む
factory ingest ./cstdint-tickets/SEARCH-123 --name search-slow
factory ingest https://teams.example.com/blog/idea-post --title "アイデア投稿"

# 2) 続けてミッションまで作る (--run で実行まで一括)
factory ingest ./materials/shopping-ui --project demo_app --kind greenfield --run

# 3) Webhook 受付 (サーバ常駐)。Slack/Teams/監視ツールから POST する
factory listen --port 8787 --project demo_app --auto-create
#   POST /signals  {"title": "...", "body": "...", "source": "...",
#                   "project": "demo_app", "files": {"note.md": "text"}}
#   GET  /health
```

`--auto-create` で受信時ミッション自動作成、`--auto-run` で実行まで自動。取り込まれた
シグナルは `factory signals` で一覧できます。

## マージ後のカナリア & PR

```bash
factory merge <id> --yes     # squash マージ
factory canary <id>          # main 先頭を一時 worktree で再検証 (PASS/FAIL レポート)
factory canary <id> --fix    # FAIL 時: 当該コミット revert + 失敗要約を Seed 化 + 再ミッション作成
factory canary <id> --fix --run-complete   # 再ミッションの実行まで自動
factory pr <id>              # origin + gh があれば PR 作成、無ければ reports/pr-draft.md
```

## コマンド一覧 (`bin/factory`)

| コマンド | 説明 |
|---|---|
| `init <root> [--demo]` | ファクトリの初期化 |
| `status [--watch [秒]]` | ミッション一覧 / ダッシュボード |
| `signals` | 取り込んだシグナルの一覧 |
| `worker ping` | claude / opencode / mock の疎通確認 |
| `seed add <src> [--name]` | Seed (材料一式) を追加 |
| `ingest <src\|url> [--project] [--run]` | Seed 化 (+ ミッション作成~実行まで一括) |
| `listen [--port] [--auto-create]` | Webhook リスナー起動 |
| `mission new <title> --project NAME \|--repo PATH [--seed S] [--kind] [--model M]` | ミッション作成 |
| `run <id> [--worker W] [--confirm] [--iterations N]` | 自律ループ実行 |
| `run-all [--parallel N]` | queued ミッションを順に/並列に実行 |
| `validate <id> [--judge]` | 検証のみ (Worker を呼ばない) |
| `holdout <list\|regen> <id>` | シナリオの一覧 / 再生成 (陳腐化リフレッシュ) |
| `report <id>` | 最終レポート表示 |
| `merge <id> [--yes]` | worktree を main に squash マージ |
| `canary <id> [--fix] [--run-complete]` | main 上での再検証 + リカバリ |
| `pr <id> [--base B]` | PR 作成 (gh 無ければ下書き) |
| `compare-img a.png b.png` | 2枚の PNG 類似度 |
| `usage [--days N]` | 使用量レポート |

## ディレクトリ構成

```
SoftwareFactory/            ← このリポジトリ (オーケストレータ本体)
  bin/factory               CLI ランチャ
  factory/                  Orchestrator 本体
    workers/                claude_code / opencode / mock + Router
    harness/                runner (build/test/シナリオ実行) + judge (LLM-as-Judge)
    data/                   デモ用アセット (demo_app / demo_seed)
  tests/                    結合テスト
  docs/                     設計ドキュメント

my-factory/                 ← factory init で作る実行環境 (ここがファクトリの現場)
  seeds/                    Signal/Seed の入り口
  missions/<id>/            mission.json / spec.md / build.json / prompts/ / reports/
  missions/<id>/work/       実装対象リポジトリの worktree
  holdout/<id>/             ★コード外の検証シナリオ (Worker には一切見せない)
  repos/ products/          对象リポジトリの置き場 (0→1の母体)
  .factory/                 config / 索引 / 使用量 / ロック
```

## 検証の設計 (なぜこうなっているか)

1. **ホールドアウトシナリオ** — 実装 Worker にパスも中身も渡しません。エージェントが
   「テストを通すための最適化」をしようがない構造です。シナリオは Spec 生成とは別
   セッションの Worker が作成します (共犯関係の排除)。
2. **LLM-as-Judge** — シナリオ結果 + テスト結果 + 実装レポートをジャッジが 0〜100 点で
   採点し、`verdict: pass|revise` と次の改善提案を返します。コードレビューに依存せず、
   仕様との適合で判定します。
3. **テスト実行もエージェントに任せる** — 実装 Worker は自分で build/test を叩き、
   失敗したら直します。Harness はそれを**独立に再実行**して検証します (信頼して検証)。
4. **もぐら叩き防止** — 実装コードと検証シナリオを物理的に隔离し、実装者の作業
   ツリー (worktree) をミッションごとに切り離すので、成果物は必ずブランチ
   `factory/<id>` でレビュー可能です。

## 自律性の段階

| 段階 | 説明 | このリポジトリでの実装 |
|---|---|---|
| 1 | 人間が Seed → 工場が実装・検証 → 結果返却 | ✅ `mission new` → `run` → `report` → `merge` |
| 2 | 工場が提案を生成して回す | ✅ 終了時に judge が `priorities` (改善シグナル) を state.json へ記録 |
| 3 | シグナルが自動で Mission に | ✅ `ingest` / `listen --auto-create` / カナリア自動再ミッション |
| 4 | 人間は異常時のみ介入 | ◐ `--risk high` ゲート + マージ確認 + `canary` は明示実行 |

詳細は `docs/design.md` / `docs/ops.md` / `docs/roadmap.md` を参照してください。
