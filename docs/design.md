# 設計ドキュメント

このリポジトリは「社内ソフトウェアファクトリー」のコンテキストメモをそのまま実装したものです。設計上の判断と、対応するコンポーネントの対応表をまとめます。

## 原則 → 実装の対応表

| コンテキストの原則 | 実装 |
|---|---|
| コードを書く・読むのは原則エージェント | 実装は Worker (claude_code/opencode) が非対話実行。人間は Seed とマージ判断だけ |
| 仕様 + シナリオを受け取り、実装→テスト→検証→改善をループ | `factory/loop.py` (Loop Controller) |
| 検証はコードレビューに依存しない | `holdout/` (ホールドアウト) + `factory/harness/judge.py` (LLM-as-Judge) |
| テスト実行自体もエージェントに任せ、失敗したら自分で直させる | 実装プロンプトのルール「build → test を自分で実行し、失敗したら直す」+ Harness の独立再実行 |
| 最初は人間の引き継ぎ、後から外部シグナル追加可能な構造 | `seeds/` を入口に統一。state.json の `signals` に judge 由来の改善提案を蓄積 (拡張点) |
| Worker の切替 (Claude Code ⇄ OpenCode) | `factory/workers/router.py` — 優先順 + 予算 + quota 検出でフォールバック |
| Orchestrator は軽量プロセス (Python) | 標準ライブラリのみの Python パッケージ |
| 状態管理は Git + シナリオストア (Markdown/JSON) | Git worktree + `missions/<id>/*.json`, `holdout/<id>/SC-*.json` |

## ホールドアウトの隔離設計

問題: エージェントは「テストを通す」ことに最適化する。実装者がテストを書けば、
テストの穴を使った実装 (偽装) が行われるリスクがある。

対策:

1. 物理隔離 — シナリオは `holdout/<mission-id>/` (ファクトリ側) に置き、実装対象
   リポジトリには絶対にコピーしない。
2. 情報隔離 — 実装 Worker のプロンプトには「失敗したシナリオの ID と概要」だけを渡す。
   cmd の内容・期待値は渡らない。
3. 生成隔离 — シナリオ生成は仕様生成と別セッションの Worker が担当する。
   同じ LLM でもセッションを分けることで、実装側がシナリオを知らずに済む。
4. 実行隔離 — シナリオの実行は検証ハーネスのみが行う。実装 Worker が HOLDOUT_DIR の
   環境変数を持つことすらしない (ハーネス内で注入)。

## ジャッジの設計

- 入力: spec.md (共有), シナリオ結果 (id/title/pass + 失敗概要), テスト結果の末尾, 実装レポート
- 出力: `{score, verdict: "pass"|"revise", comments, priorities}` (JSON, 冗長出力は raw_decode で救済)
- 合格条件: `build ok ∧ test ok ∧ 全シナリオ pass ∧ judge.verdict == pass ∧ judge.score >= 閾値`
- ジャッジが使えない場合 (CLI 不在など): base_score (重み付きシナリオ達成率) で代替

## Worker 切替の判断基準

1. `worker_order` の先頭から順に試す (`use_worker` が auto 以外ならそれが最優先)。
2. 予算超過のワーカー (日次 `daily_call_limit`、または rolling 窓 `window_call_limit` 分) は後ろへ回す。
3. 実行して quota 系エラー (`usage limit`, `rate limit`, `credit balance`…) か
   timeout なら、残っている候補へフォールバック。
4. 全員失敗でミッションを failed にする (人間が介入する状態)。

CLI 実装差異:

- `claude_code`: `claude -p <prompt> --output-format json --dangerously-skip-permissions
  [--allowedTools ...] [--disallowedTools ...] [--resume <sid>]`
  → JSON から result / session_id / usage tokens / total_cost_usd を取得。
  config `resume: true` で反復 2 回目以降は同一セッションに継続。
- `opencode`: `opencode run [--model M] <prompt>` (config `args` で `--format json` 等を追加可)。
  JSON 出力時はテキスト / usage / sessionID をベストエフォートで抽出。

## シグナル → Seed の自動化 (Ingest / Listen)

- `factory ingest <src|url>`: ローカル素材はディレクトリごと `seeds/<name>/` に複製、
  URL は本文テキスト抽出 (script/style 除去) + 生 HTML 同梱で Seed 化。
  `--project/--repo` があればミッション作成、`--run` で実行まで一括。
- `factory listen`: 標準ライブラリの HTTP サーバ。`POST /signals {title, body, source,
  files}` を受けて Seed 化し、`--auto-create` でミッション作成 (`--auto-run` で実行まで)。
  取り込みは state.json の `signals` に積まれ、`factory signals` で見られる。
- カナリア (`factory canary <id> [--fix]`): main 先頭の detach worktree で Harness 再実行。
  FAIL 時は当該 squash コミットを revert し、失敗要約を Seed 化して再ミッションを立てる
  (`--run-complete` で即実行)。revert は worktree が dirty だと中止する。

## サブスク既定の認証 (auth)

- `WorkerConfig.auth = "subscription"` (既定): CLI ワーカーを**ログイン済みプラン**
  (Claude Pro/Max, ChatGPT Plus/Pro) で実行するため、`ANTHROPIC_API_KEY` /
  `OPENAI_API_KEY` を子プロセス環境から除去する。環境変数にキーがあっても
  CLI はサブスク側の認証に倒れる (意図しない API 課金を防ぐ)。
- `auth = "api_key"`: 環境変数のキーを使う従量課金モード (OpenRouter は常にこれ)。
- OpenRouter は Chat Completions を直呼び (urllib)。ツール実行がないため
  Router は spec/holdout/judge (テキスト生成) にのみ割当て、dev (実装) には使わない。

## ファクトリークロック (tick / supervisor) の設計

「工場として自律している」ことを確認する検査対象は 3 つ: **継続性** (誰もいなくても
仕事が流れ続けるか)、**出口の自律性** (実装だけでなく出荷・失敗回収まで終わるか)、
**逆振れしないこと** (失敗への自動対応が失敗を増幅しないこと)。

- **1 巡 = tick**: 取込 (intake) → 実行 (execute) → 出荷 (deliver: merge + canary +
  repair) の 3 段。各段は独立していて、相互の失敗でクロックが止まらない
  (`supervise` は tick ごとに例外を握って継続する)。
- **継続性**: intake は `seeds/` に置かれた未割当 Seed を常に拾う (level>=3)。
  execute は queued を並列 (`tick_concurrency`)・優先度で処理する。予算は
  「全ワーカーが日次/窓上限を超えたときのみ」実行を差し控える (一部超過なら
  ルーターの後ろ回しが効くので止めない)。
- **出口の自律性**: merge と canary までも deliver 段が実施する (level>=2)。
  高リスクだけ人間の確認に残す。
- **逆振れ防止**: カナリア失敗 → revert → 修復ミッション (`repair-of:` 鎖) →
  自動出荷…の往復は `max_repair_rounds` (既定 1) で深度制限する。超えたら
  `needs_human`。revert の二重適用もする。
- **冪等**: 全 Seed 割当済み & queued & 未出荷なし状態では tick は無活動で終わる。
  cron / 常駐 / 手動のどの走らせ方でも結果が同じになる。

## 検証ハーネスの追加機能

- 並列実行でも安全: ポートは `health_url` の番号を優先しつつ、使用中なら空きポートを選び、
  `FACTORY_PORT` と `health_url` の両方に反映する。
- UI 画像比較: ステップの `expect_png_similar: {file, to, ratio}`。特徴量なしの
  nearest サンプリング (既定 64×64) + RGB 平均絶対差。PNG は標準ライブラリで
  デコード (bit depth 8 / grey・RGB・palette・RGBA・非インターレース)。

## 0→1 と 継続アップデート

- **0→1 (greenfield)**: ミッション kind=greenfield。空リポジトリ (or 新規リポジトリ) を対象に
  Spec Generator が仕様を書き、実装 Worker がゼロから実装。
- **アップデート (update)**: kind=update。既存リポジトリへの worktree で同じループを回す。
  Seed にバグ報告やフィードバックを置くだけで、同じパイプラインが使える。

## 自律性の段階と、このリポジトリの現在地

| 段階 | 現在地 | 次に足りないもの |
|---|---|---|
| 1. Seed → 実装・検証 → 結果返却 | ✅ 完全動作 | — |
| 2. 提案を生成して回す | ◐ judge.priorities が signals に蓄積。手動で `seed add` に接続 | シグナル → ミッション自動化コマンド |
| 3. シグナル → 自動 Mission | ☐ | 本番/SNS シグナルの取り込み (roadmap) |
| 4. 異常時のみ介入 | ◐ risk ゲート (high は --confirm 必須) / マージ確認が現状必須 | merge --yes の自動化ポリシー、異常検知 |

## 非ゴール

- 外販/マルチテナント (1社内専用)
- クラウドスケールのキュー/スケジューラ (単一 Mac で動く規模を想定)
- 完全な人間排除 (マージ判定・高リスク判断は人間が持つ)
