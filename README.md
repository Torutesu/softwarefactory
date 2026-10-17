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
      "daily_call_limit": 60
    },
    "opencode": { "type": "opencode", "model": "", "timeout": 1800 }
  },
  "max_attempts": 2,
  "judge_pass_score": 80
}
```

- `claude_code` … `claude -p "<prompt>" --output-format json --dangerously-skip-permissions`
- `opencode` … `opencode run "<prompt>"`
- **フォールバック**: 予算超過 (`daily_call_limit`)・quota/timeout エラー時は次のワーカーへ自動切替
- 使用量は日次で `.factory/usage/` に記録 (`factory usage` で表確認)

## コマンド一覧 (`bin/factory`)

| コマンド | 説明 |
|---|---|
| `init <root> [--demo]` | ファクトリの初期化 |
| `status` | ミッション一覧 |
| `worker ping` | claude / opencode / mock の疎通確認 |
| `seed add <src> [--name]` | Seed (材料一式) を追加 |
| `mission new <title> --project NAME \|--repo PATH [--seed S] [--kind greenfield\|update]` | ミッション作成 |
| `run <id> [--worker W] [--confirm] [--iterations N]` | 自律ループ実行 |
| `run-all` | queued ミッションを順に実行 |
| `validate <id> [--judge]` | 検証のみ (Worker を呼ばない) |
| `report <id>` | 最終レポート表示 |
| `merge <id> [--yes]` | worktree を main に squash マージ |
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
| 3 | シグナルが自動で Mission に | ◐ `factory seed add` (手動)。自動化は roadmap |
| 4 | 人間は異常時のみ介入 | ◐ `--risk high` ゲート + マージ確認は現状必須 |

詳細は `docs/design.md` / `docs/ops.md` / `docs/roadmap.md` を参照してください。
