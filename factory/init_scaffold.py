"""`factory init`: ファクトリの雛形を作る。"""
from __future__ import annotations

from pathlib import Path

from .config import Config, save as save_config
from .state import Factory

GITIGNORE = """\
# ファクトリの実行時データ
.factory/
usage/
logs/

# 製品リポジトリ (ターゲットコード)
products/
repos/

# macOS
.DS_Store
__pycache__/
*.pyc
"""

HOLDOUT_README = """\
# Holdout (ホールドアウトシナリオ)

このディレクトリは **コードベース外** の検証シナリオ置き場です。

## 絶対ルール
- 実装 Worker に、このディレクトリのパスも中身も渡さない。
  Worker には検証結果 (pass/fail + 失敗概要) だけをフィードバックする。
- シナリオの生成は Spec Generator とは別セッションの Worker が行う
  (実装者と共犯関係を作らないため)。
- シナリオは `missions/<id>/` に対応付けられ、実行は検証ハーネスだけが行う。

## 形式
`<mission-id>/SC-001.json` の形式:

```json
{
  "id": "SC-001",
  "title": "何を確認するか",
  "weight": 1.0,
  "steps": [
    {"name": "health", "cmd": "curl -sf http://127.0.0.1:${FACTORY_PORT}/health", "expect_exit": 0}
  ]
}
```

ステップの期待値フィールド: `expect_exit` (既定 0), `expect_contains`, `expect_not_contains`, `expect_regex`。

UI の画像比較 (オプション): Seed に基準スクショを含めた場合、`expect_png_similar` で
レンダリング結果との類似判定ができます。基準画像はこの holdout ディレクトリにコピーしてください。

```json
{"name": "render", "cmd": "curl -s http://127.0.0.1:${FACTORY_PORT}/shot.png -o render.png",
 "expect_png_similar": {"file": "render.png", "to": "bench-home.png", "ratio": 0.85}}
```

- `file`: 作業ディレクトリ ($FACTORY_REPO) 基準の実行時画像
- `to`: holdout ディレクトリ基準の基準画像
- `ratio`: 合格閾値 (0..1、省略 0.85)

## 設計のポイント
- 仕様の主要な機能要件を漏れなく独立に検証すること。
- 実装リポジトリのテストと重複しない、ブラックボックスな視点で書く。
"""

SEEDS_README = """\
# Seeds (シグナルの入り口)

新しい仕事の入り口です。次のいずれかを1ディレクトリにまとめて置きます。

- **ベンチマーク**: 参照UI/プロダクトのスクショ (png/jpg)、URL、説明
- **新規要件**: 作りたいものの説明
- **バグ報告・改善依頼**: 何がどう困るか、あるべき姿

最後に人間が `seed.md` に目的と受け入れ基準を書くのが原則です。
Worker はここにある材料 (画像含む) を読んで仕様を立てます。
"""


def init_factory(root: Path, demo: bool = False) -> None:
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    cfg = Config.create(str(root), demo=demo)

    for sub in ("seeds", "missions", "holdout", "repos", ".factory"):
        (root / sub).mkdir(parents=True, exist_ok=True)

    (root / ".gitignore").write_text(GITIGNORE, encoding="utf-8")
    (root / "holdout" / "README.md").write_text(HOLDOUT_README, encoding="utf-8")
    (root / "seeds" / "README.md").write_text(SEEDS_README, encoding="utf-8")

    save_config(cfg)
    f = Factory(root)
    f.event("init", f"demo={demo}")

    print(f"ファクトリを初期化しました: {root}")
    print(f"  config: {cfg.state_file}")
    if demo:
        proj = cfg.projects.get("demo_app")
        print(f"  demo project: {proj.path if proj else ''}")
        print("  demo seed: seeds/demo-app (自動投入済み)")
        print("")
        print("デモの実行方法:")
        print("  factory mission new \"メモAPIの実装\" --project demo_app --seed demo-app")
        print("  factory run <上で出たミッションID>")
        print("  factory report <上で出たミッションID>")
