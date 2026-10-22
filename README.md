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

## 本番モードへの切替 (サブスクを既定にする)

**既定は"サブスクの枠"で使う構成です** — API キー (従量課金) ではなく、ログイン済み
プラン (Claude Pro/Max: `claude login` / ChatGPT Plus・Pro: `codex login`) をワーカーが
そのまま使います。`auth: "subscription"` がデフォルトで、このときオーケストレータは
`ANTHROPIC_API_KEY` / `OPENAI_API_KEY` を子プロセス環境から外すため、キーが環境に
あっても CLI は必ずサブスクの枠で動きます (API 課金にならない)。

対応ワーカー:

| worker | 実行方法 | auth 既定 | 担当できる用途 |
|---|---|---|---|
| `claude_code` | `claude -p` | subscription | spec / holdout / dev / judge |
| `codex` (OpenAI) | `codex exec` | subscription | spec / holdout / dev / judge |
| `opencode` | `opencode run` | login 設定に準ずる | spec / holdout / dev / judge |
| `openrouter` | Chat Completions API 直呼び (HTTP) | api_key (`OPENROUTER_API_KEY`) | spec / holdout / judge (ツール実行不可) |
| `mock` | Seed 同梱の答えキーで決定的にパッチ | — | 全用途 (デモ/CI) |

```json
{
  "use_worker": "auto",
  "worker_order": ["claude_code", "codex", "opencode", "openrouter", "mock"],
  "judge_worker": "claude_code",
  "workers": {
    "claude_code": {
      "type": "claude", "auth": "subscription", "model": "sonnet",
      "timeout": 1800, "daily_call_limit": 60, "window_call_limit": 30, "window_minutes": 300,
      "resume": true,
      "allowed_tools": ["Bash", "Edit", "Write", "Read", "Grep", "Glob"],
      "disallowed_tools": ["WebSearch"]
    },
    "codex": {
      "type": "codex", "auth": "subscription", "model": "gpt-5.2",
      "daily_call_limit": 40, "window_call_limit": 20
    },
    "opencode": { "type": "opencode", "timeout": 2400 },
    "openrouter": { "type": "openrouter", "model": "anthropic/claude-sonnet-4.5" }
  },
  "max_attempts": 2,
  "judge_pass_score": 80,
  "purpose_workers": { "spec": ["opencode"], "holdout": ["opencode"], "judge": ["claude_code"] },
  "merge_policy": { "mechanical": "auto", "scoped": "auto", "behavioral": "review", "sensitive": "review" },
  "sensitive_paths": ["auth", "credential", "password", "secret", "token", "payment", "billing", "crypto", "oauth", "jwt", "session", "permission", "migrat"],
  "small_change_lines": 50,
  "intake_dup_threshold": 0.85,
  "worktree_copy": [".env", ".env.*", "config/secrets.json", "*.local"],
  "noprogress_iterations": 2,
  "require_reviewable": true,
  "judge_on_harness_fail": false
}
```

- `claude_code` … `claude -p "<prompt>" --output-format json --dangerously-skip-permissions`
- `codex` … `codex exec --dangerously-bypass-approvals-and-sandbox --skip-git-repo-check`
- **フォールバックレーン**: Claude 枠枯渇 → Codex 枠 → OpenCode → OpenRouter
  (テキスト用途、要点/spec/judge) → mock。quota エラーは自動検知して切り替える
- **二段予算**: `daily_call_limit` (日次) + `window_call_limit`/`window_minutes`
  (例: 5時間窓) の両方を監視し、超過ワーカーは候補の後ろに回す (サブスク枠の保護)
- `allowed_tools` / `disallowed_tools` … Claude Code の `--allowedTools` / `--disallowedTools` に透過指定
- `resume: true` … 反復2回目以降、同一ワーカーのセッションを継続 (`--resume <session-id>`)
- ミッション単位のモデル指定: `factory mission new ... --model opus` で config を上書き
- 使用量は `.factory/usage/` に日次+呼び出し時刻を記録 (`factory usage` で表確認)

## ループの停止条件 (halting)

dev ループは次の 3 つの保障で必ず止まります。止まった理由は `mission.json` の
`halt_reason` に残り、`factory status` / `final.md` / tick の `needs_human` に出ます。

| 保障 | 設定 | `halt_reason` |
|---|---|---|
| 反復上限 | ミッションの `max_iterations` | `max_iterations` |
| 支出上限 | ワーカーの `daily_call_limit` / `window_call_limit` (全滅時) | `all_workers_failed` |
| **noprogress** | `noprogress_iterations` (既定 2, 0 で無効): ワーカー呼出の前後で作業ツリーの内容 (未コミット・未追跡を含む) が変わらない反復がこの回数連続したら打ち切り。1 回目の時点で次の prompt に警告を載せる | `noprogress` |

加えて dev に入る前の **reviewability ゲート** (`require_reviewable`, 既定 true):
`build.json` に `test` が無く、ホールドアウトシナリオも無いミッションは「通った」を
judge の自己申告でしか判定できないため、`blocked` (`unreviewable`) で止めて人間に返します。

その他の `halt_reason`:

| `halt_reason` | 意味 | 次の一手 |
|---|---|---|
| `holdout_leak` / `holdout_tamper` | ワーカー出力に holdout のパス / シナリオ一式が記録時の指紋と不一致 | `holdout regen <id>` → `mission reset <id>` |
| `holdout_failed` | ホールドアウト生成で不正なシナリオしか作れなかった | `holdout regen <id>` |
| `judge_unavailable` | judge の判定が取れない (枠切れ・JSON 不正)。harness だけでは合格にしない (`judge_required`) | 枠回復後に `mission reset <id>` |
| `crash` | 例外で中断 (`reports/crash.txt` にトレースバック) | 原因を直して `mission reset <id>` |
| `shutdown` / `interrupted` / `recovered` | supervisor 停止・Ctrl-C・電源断等。自動で queued に戻る | 不要 (次の tick で再実行) |
| `crash_loop` | 途中停止が `max_recover_attempts` (既定 2) 回を超えた | 原因調査 → `mission reset <id>` |

## ホールドアウトの隔離

実装ワーカーは確認なしモード (`--dangerously-skip-permissions` 等) で動くため、prompt の
「holdout/ を触らない」だけでは読めてしまいます。そこで二重に塞ぎます。

- **封印**: dev ワーカー呼出の間だけ `holdout/<id>` を `chmod 000` にし、呼出後 (例外時も) に戻す。
  kill されて封印が残った場合は次回実行の冒頭で復旧する。**root では権限が効かない**ので
  ファクトリーは一般ユーザーで動かす
- **漏洩検知**: ワーカー出力に holdout の絶対パスや `holdout/<id>` が出たら、その反復で打ち切り
  (`halt_reason=holdout_leak`)。シナリオは見られた前提で `holdout regen` してから再実行する
- **改ざん検知**: harness はワーカーが書いたコード (build/test) を動かすので、そのコードは同じ権限で
  シナリオを書き換えられる。dev 開始前にシナリオ一式の指紋を `.factory/holdout_manifest/` に記録し、
  検証のたびに「ワーカーのコードを動かす前」に照合 → 0700 の一時ディレクトリへスナップショット →
  シナリオはスナップショットから実行。検証後にも照合し、食い違えば `holdout_tamper` で止める
- **レポートの分離**: 失敗したシナリオのコマンドと出力は `holdout/<id>/runs/` にだけ書く
  (`missions/<id>/reports/` は worktree の親でワーカーから読めるため、ID・タイトル・判定のみ)

## 秘密情報の扱い (環境変数)

ワーカー CLI と harness (build/test/run/シナリオ) には、名前が秘密らしい環境変数
(`*API_KEY*`・`*TOKEN*`・`*SECRET*`・`*PASSWORD*`・`SSH_AUTH_SOCK` 等) を **渡しません**。

- ワーカー: `auth: "subscription"` なら API キーを外し、`auth: "api_key"` ならその CLI 種別の鍵だけ通す。
  headless ログイン用の `CLAUDE_CODE_OAUTH_TOKEN` は claude にだけ通す。個別に渡すなら worker の `env_passthrough`
- harness: 製品のテストが必要とする変数は `harness_env_passthrough: ["DATABASE_URL"]` のように明示する
- 実ワーカーが設定された構成では `mock` を黙ってフォールバックに使わない (`allow_mock_fallback`)。
  mock は prompt 中の `mock-action` (shell を含む) を実行するため、外部テキストが host で動きうる

## マージゲート (blast radius ティア)

マージ (`factory merge` / tick の自動出荷) の前に、`factory/<id>` ブランチの差分を
4 ティアに分類し、ティアごとのポリシーで自動マージの可否を決めます。

| ティア | 例 | 既定ポリシー |
|---|---|---|
| `mechanical` | テスト/ドキュメント/バージョンだけの変更 | 自動チェックでマージ可 |
| `scoped` | `small_change_lines` (既定 50) 行以下の修正 | 同上 |
| `behavioral` | 大きめの差分・greenfield (0→1) | **人間レビュー** |
| `sensitive` | `sensitive_paths` (auth/決済/認証情報等) か `protected_paths` (CI・ビルド・テスト基盤) を含む | **領域オーナーのレビュー** |

- `protected_paths` (既定: `.github/**`・`**/conftest.py`・`pyproject.toml`・`package.json`・`Makefile`・
  `Dockerfile`・`.claude/**` 等) に触れる差分は sensitive。自動マージで self-hosted runner に任意の
  ワークフローを持ち込ませない / テスト判定そのものを変えさせないため
- テストファイルの削除とバイナリファイル (行数に出ない) を含む差分は behavioral 以上
- マージは main repo の追跡ファイルに未コミット変更があると拒否し、競合したら元の状態に戻す
  (`merge --squash` の競合は `merge --abort` では戻らないため)。`status=done` 以外は `--force` が必要

- 自動出荷 (tick level>=2) は `autonomy_level` と risk に加え、ティアの `merge_policy` が
  `auto` のときだけ実行します。`review` のティアは `needs_human` に「マージゲート」エントリが出ます
- `factory merge <id>` はティアと推奨レビューを表示してから確認を取ります
- マージコミットと PR には帰属トレーラ (`Assisted-by: SoftwareFactory <id> (workers: ...)`) が
  付きます。`Signed-off-by` は人間のマージ判断が責任を持つため付与しません
- worktree は空の checkout のため、`.env` 等の非追跡ファイルを
  `worktree_copy` (またはリポジトリの `.worktreeinclude`) に従って複製します

## コストの可視化と節約 (用途別ルーティング)

AI コーディングのコストは「どこにトークンが向かったか」を見える化しないと削減できません
(Uber のコスト方程式: `price/token × tokens/request × requests/turn …` の分解)。

| レバー | コマンド / 設定 |
|---|---|
| **用途別ルーティング**: spec/holdout/judge (テキスト生成) を安いワーカーへ、dev を高価 but 強いワーカーへ | config `purpose_workers: {"spec": ["opencode"], "judge": ["claude_code"], ...}` (無指定なら従来の `judge_worker`) |
| **ベンチ駆動のモデル選択**: 実測で purpose_workers を更新する根拠 | `factory bench` — 全員に共通の spec/holdout で出題し、ワーカー別の完了率・score・cost を計測。`名前@モデル` でモデル差も比較、purpose=spec で spec 生成品質も比較 (詳細は `docs/ops.md` の「社内 SWE ベンチ」) |
| **成果単価**: ミッションあたりのコスト/トークン | `factory status` の COST 列、`factory report <id>` の反復表 (自己報告値のあるワーカーのみ) |
| **セッション分析**: 無駄の兆候検出 | `factory usage --waste` — 失敗率の高いワーカー / 単価の前日比急増 / 反復上限ギリギリ合格 / 失敗ミッション |
| **予算 nudge**: 枠の消費を事前に見せる | `factory tick` 出力の `budget_nudges` (使用率 50% / 80% で表示) |
| **プロンプトの再送削減**: 反復 2 以降は spec 全文を再送しない | 自動 (同一セッション resume 時)。spec はプロンプト先頭に置き prefix を安定させる |
| **合格し得ない反復の judge 省略**: harness が落ちた反復は judge を呼ばない | 既定 (`judge_on_harness_fail: false`)。指摘は build/test の tail と失敗シナリオ名で次反復に渡る |
| **Mac mini 常駐**: サブスク枠 + self-hosted CI で従量課金を外す | `deploy/macmini/install.sh` (launchd) と `.github/workflows/ci.yml` — 手順は `docs/macmini.md` |

## 自律運転 (ファクトリークロック)

1 回の `tick` が「収集 → 取込 → 実行 → 出荷 → カナリア → 修復」を一巡します
(収集は `collect.sources` を設定したときだけ動く)。外の素材 (記事/エラートラッカー/
コマンド出力) を cron で巡回してドラフト化し、人間が seed.md を確定したものだけが
仕事になる — 巡回は自動、仕事化は人間ゲートです:

```bash
factory collect                       # ソースを巡回 → .factory/drafts/ にドラフト化
factory collect --list                # ドラフト一覧
factory collect --promote D... --project X --run   # 確定して Seed 化 → 実行まで
factory collect --dismiss D...        # 却下
factory tick --dry-run                # 計画だけ見る
factory tick                          # 1 巡だけ (cron 併用可)
factory supervisor --interval 300     # 常駐で tick を回し続ける
```

`autonomy_level` で自律度を段階的に上げられる (既定 1 = 実行と報告のみ):

| level | 挙動 |
|---|---|
| 1 | queue されたミッションを実行・報告 |
| 2 | + low リスク完了ミッションの**自動マージ**と**自動カナリア** |
| 3 | + 未割当 Seed からミッションを**自動作成** (`autopilot_project`) |
| 4 | + カナリア失敗時に **revert + 修復ミッション自動実行** (深度上限 `max_repair_rounds`) |

人間は `needs_human` に積まれた項目だけ見ます (高リスク・反復上限・修復ラウンド上限・
予算枯渇・project 未確定の Seed)。要約は `notify_url` への Webhook POST でも受け取れます。

## シグナル → Seed → ミッション (Ingest)

外部/日常のシグナルを Seed 化する窓口が 3 種あります。

```bash
# 1) 手動取込: ディレクトリ/URL を seeds/ に化け物なく放り込む
factory ingest ./cstdint-tickets/SEARCH-123 --name search-slow
factory ingest https://teams.example.com/blog/idea-post --title "アイデア投稿"

# 2) 続けてミッションまで作る (--run で実行まで一括)
factory ingest ./materials/shopping-ui --project demo_app --kind greenfield --run

# 3) Webhook 受付 (サーバ常駐)。Slack/Teams/監視ツールから POST する
#    secret 設定時は Authorization: Bearer <secret> か X-Hub-Signature-256 (HMAC) が必須。
#    ループバック以外で待ち受けるなら secret 必須。本文は 1MB まで、files の名前は Seed 内に限る
FACTORY_LISTEN_SECRET=... factory listen --port 8787 --project demo_app --auto-create
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
| `init <root> [--demo] [--force]` | ファクトリの初期化 (初期化済みの config は --force でのみ作り直す) |
| `status [--watch [秒]]` | ミッション一覧 / ダッシュボード (--watch: usage 窓・review 待ちティア・canary・needs_human・ドラフト) |
| `signals` | 取り込んだシグナルの一覧 |
| `worker ping` | ワーカー CLI の疎通確認 (`--version` のみ。失敗があれば exit 1) |
| `seed add <src> [--name]` | Seed (材料一式) を追加 |
| `ingest <src\|url> [--project] [--run]` | Seed 化 (+ ミッション作成~実行まで一括) |
| `collect [--source] [--dry-run] [--list]` | 定時シグナル収集 → ドラフト化 (人間が `--promote` / `--dismiss`) |
| `listen [--port] [--auto-create]` | Webhook リスナー起動 |
| `mission new <title> --project NAME \|--repo PATH [--seed S] [--kind] [--model M]` | ミッション作成 |
| `run <id> [--worker W] [--confirm] [--iterations N]` | 自律ループ実行 |
| `run-all [--parallel N]` | queued ミッションを順に/並列に実行 |
| `validate <id> [--judge]` | 検証のみ (Worker を呼ばない) |
| `holdout <list\|regen> <id>` | シナリオの一覧 / 再生成 (陳腐化リフレッシュ) |
| `report <id>` | 最終レポート表示 |
| `merge <id> [--yes] [--force]` | worktree を main に squash マージ (done 以外は --force) |
| `canary <id> [--fix] [--run-complete]` | main 上での再検証 + リカバリ |
| `pr <id> [--base B]` | PR 作成 (gh 無ければ下書き) |
| `compare-img a.png b.png` | 2枚の PNG 類似度 |
| `usage [--days N] [--waste]` | 使用量レポート (+ セッション分析) |
| `bench [--init] [--dry-run] [--workers a,b@c] [--repeat N]` | 社内 SWE ベンチ (purpose_workers の更新根拠。`@モデル` でモデル差比較 / purpose=spec で spec 品質比較) |
| `tick [--dry-run]` | ファクトリークロック 1巡 (取込→実行→出荷→カナリア→修復) |
| `supervisor [--interval N]` | tick を常駐で回す (自律運転)。SIGTERM で実行中の子プロセスを止め、途中のミッションを queued に戻して終了。config は毎 tick 読み直す |
| `doctor [--json]` | 無人運転できる環境かの自己診断 (root・CLI・ログイン・git・空き容量・封印・孤児・PAUSE・launchd)。0/1/2 で終了 |
| `selftest [--keep]` | 一時ディレクトリで init→run→merge gate→merge→canary→tick 冪等性を mock で通す e2e QA (本物の CLI は呼ばない) |
| `config check [--json]` | config.json の検証 (型・範囲・ワーカー/プロジェクト参照・未知キー) |
| `needs-human [--ack KEY] [--all] [--json]` | 人間の判断待ちの台帳 (重複排除済み。通知は新規/解消のときだけ) |
| `mission reset <id>` | 失敗/停止したミッションを queued に戻す |
| `worktrees [--dry-run] [--force-failed]` | 完了/bench ミッションの worktree を掃除 |

緊急停止: `touch .factory/PAUSE` で tick は何もしなくなる (消せば次の tick から再開)。

## ディレクトリ構成

```
SoftwareFactory/            ← このリポジトリ (オーケストレータ本体)
  bin/factory               CLI ランチャ
  factory/                  Orchestrator 本体
    workers/                claude_code / codex / opencode / openrouter / mock + Router
    harness/                runner (build/test/シナリオ実行) + judge (LLM-as-Judge)
    mergegate.py            マージゲート (blast radius 分類 / 帰属トレーラ)
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
| 3 | シグナルが自動で Mission に | ✅ `ingest` / `listen --auto-create` / `tick` (level 3+ の intake) |
| 4 | 人間は異常時のみ介入 | ✅ `supervisor` + `autonomy_level 4`: 自動マージ・カナリア・revert+修復。高リスク/予算/修復上限でエスカレーション |

詳細は `docs/design.md` / `docs/ops.md` / `docs/roadmap.md` を参照してください。
