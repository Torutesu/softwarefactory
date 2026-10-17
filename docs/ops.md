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

## Worker の設定 (サブスク枠を保護する)

`.factory/config.json`:

```json
{
  "use_worker": "auto",
  "worker_order": ["claude_code", "opencode", "mock"],
  "judge_worker": "claude_code",
  "workers": {
    "claude_code": { "type": "claude", "model": "sonnet", "timeout": 1800, "daily_call_limit": 60 },
    "opencode":    { "type": "opencode", "timeout": 2400 }
  }
}
```

- 朝イチで `bin/factory worker ping` を叩き、CLI の生存と使用量を確認。
- `bin/factory usage --days 7` で日次の呼び出し・トークン・コストを見る。
- 枠が逼迫したら `daily_call_limit` を下げる / `worker_order` の先頭を `opencode` に変える。
  quota 系エラーが Router で検出され、自動でフォールバックする。
- 特定ミッションだけ別 worker で回したい: `--worker opencode`。

## 高リスクのミッション

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
