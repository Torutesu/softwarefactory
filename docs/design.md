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
2. その日の呼び出し回数が `daily_call_limit` を超えたワーカーは後ろへ回す。
3. 実行して quota 系エラー (`usage limit`, `rate limit`, `credit balance`…) か
   timeout なら、残っている候補へフォールバック。
4. 全員失敗でミッションを failed にする (人間が介入する状態)。

CLI 実装差異:

- `claude_code`: `claude -p <prompt> --output-format json --dangerously-skip-permissions`
  → JSON から result / session_id / usage tokens / total_cost_usd を取得
- `opencode`: `opencode run <prompt>` (テキスト)。usage は取れないのでトークン記録なし

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
