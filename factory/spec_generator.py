"""Spec Generator: Seed (ベンチマーク・要件・バグ報告) から spec.md と build.json を生成する。

- 生成は Worker (Claude Code / OpenCode) が行う。
- 出力物はミッションディレクトリ (missions/<id>/) に置かれ、実装対象リポジトリには置かない。
- spec.md は実装 Worker に渡る共有情報。ホールドアウトシナリオの中身は渡さない。
"""
from __future__ import annotations

from pathlib import Path
from typing import List

from .state import Factory, Mission
from .util import load_json, load_text, save_text, tail
from .workers.router import Router

SPEC_TEMPLATE = """\
# {title}

## 目的
(この製品/変更が何を解決するか。Seed の文脈から)

## ユーザーと使い方
(誰が・どう使うか)

## 機能要件
- FR-1: (検証可能な形で短く書く)
- FR-2: ...

## 非機能要件 (任意)
- 例: レスポンス時間、可読性、エラー表示

## 検証方針
(実装後にどう確認するか。HTTP / CLI / テストコマンドのいずれか)

## 補足
"""

BUILD_TEMPLATE = {
    "build": None,
    "test": None,
    "run": None,
    "health_url": None,
    "project_subdir": None,
}


def list_seed_files(seed_dir: Path) -> List[str]:
    if not seed_dir.exists():
        return []
    out = []
    for p in sorted(seed_dir.rglob("*")):
        if p.is_file() and ".git" not in p.parts:
            out.append(str(p.relative_to(seed_dir)))
    return out


def spec_prompt(factory: Factory, m: Mission, seed_dir: Path) -> str:
    files = list_seed_files(seed_dir)
    files_block = "\n".join(f"  - {f}" for f in files) or "  (ファイルなし)"
    repo_abs = str(Path(m.repo).resolve()) if m.repo and Path(m.repo).exists() else "(なし)"
    mission_dir = str(factory.mission_dir(m.id).resolve())
    kind_text = "0→1 新規開発 (リポジトリは空。この仕様に沿って新規実装する)" if m.kind == "greenfield" else "既存コードの更新/修正 (既存コードを読んで仕様を書く)"
    return f"""[Software Factory] Spec Generator — ミッション {m.id}: {m.title}

あなたは仕様生成担当 (Spec Generator) です。実装担当とは別のセッションです。

## 入力材料
- Seed ディレクトリ: {seed_dir.resolve()} (rootからの相対: {m.seed})
- Seed 内のファイル一覧:
{files_block}
- 対象リポジトリ (既存の場合に読んでよい): {repo_abs}
- ミッション種別: {kind_text}
{('- 人間からの補足: ' + m.notes) if m.notes else ''}

## タスク
あなたの作業ディレクトリ: {mission_dir}

1. Seed の材料をすべて読む。画像 (png/jpg) は Read ツールで開き、その内容を仕様に反映する。
2. `{mission_dir}/spec.md` を書く。形式は以下のテンプレートに従う:

{SPEC_TEMPLATE}

3. `{mission_dir}/build.json` を書く。形式は以下 (値は実態に合わせる。使わない項目は null):

```json
{{
  "build": "ビルドコマンド (省略可, 例: uv sync)",
  "test": "テストコマンド (例: pytest -q)",
  "run": "アプリ起動コマンド (HTTP/サーバを伴う場合)",
  "health_url": "起動待ち確認用URL (例: http://127.0.0.1:${{FACTORY_PORT}}/health)",
  "project_subdir": "リポジトリルートからプロジェクトのサブディレクトリ (省略可)"
}}
```

## 制約
- 機能要件は 3〜8 個。各要件は「検証で確認できること」として書く。
- spec.md は実装担当に渡る共有情報。ホールドアウトシナリオ (コード外の検証シナリオ) の内容はここに書かない。
- 既存リポジトリのファイルを書き換えない (仕様作成のみ)。
- 最終メッセージは 3〜5 行の要約 (主な判断ポイント)。
"""


def verify_spec(factory: Factory, m: Mission) -> List[str]:
    errors = []
    spec = factory.spec_file(m.id)
    build = factory.build_file(m.id)
    if not spec.exists() or load_text(spec).strip() == "":
        errors.append("spec.md が存在しない/空")
    build_data = load_json(build)
    if build_data is None:
        errors.append("build.json が存在しない/JSON不正")
    return errors


def _mock_mode(router: Router) -> bool:
    cands = router.candidates("spec")
    return bool(cands) and cands[0] == "mock"


def _mock_generate(factory: Factory, m: Mission, seed_dir: Path) -> bool:
    """mock モード: Seed に同梱された「答えキー」をコピーしてパイプラインを通す。"""
    import shutil

    mock_dir = seed_dir / "mock"
    spec = mock_dir / "spec.md"
    build = mock_dir / "build.json"
    if spec.is_file():
        save_text(factory.spec_file(m.id), spec.read_text(encoding="utf-8"))
    if build.is_file():
        (factory.mission_dir(m.id) / "build.json").write_text(build.read_text(encoding="utf-8"), encoding="utf-8")
    if (mock_dir / "mock_actions.json").is_file():
        shutil.copy2(mock_dir / "mock_actions.json", factory.mission_dir(m.id) / "mock_actions.json")
    errors = verify_spec(factory, m)
    if errors:
        m.status = "failed"
        factory.save_mission(m)
        factory.event("spec_failed", m.id)
        return False
    factory.event("spec_generated", m.id)
    return True


def generate(factory: Factory, m: Mission, router: Router) -> bool:
    """仕様生成を実行。成功で True。"""
    seed_dir = factory.seeds_dir / m.seed if m.seed else factory.seeds_dir
    md = factory.mission_dir(m.id)
    save_text(md / "spec.md", SPEC_TEMPLATE.format(title=m.title))
    m.status = "spec"
    factory.save_mission(m)

    if _mock_mode(router):
        return _mock_generate(factory, m, seed_dir)

    prompt = spec_prompt(factory, m, seed_dir)
    errors = verify_spec(factory, m)
    for attempt in range(2):
        try:
            _, res = router.run("spec", md, prompt if attempt == 0 else prompt + (
                "\n\n[再実行依頼] 前回の出力は不十分でした。未達の項目:\n- " + "\n- ".join(errors)
            ))
        except Exception:
            break
        errors = verify_spec(factory, m)
        if not errors:
            break
    if errors:
        m.status = "failed"
        m.notes = (m.notes + "\n" if m.notes else "") + "spec generation failed: " + "; ".join(errors)
        factory.save_mission(m)
        factory.event("spec_failed", m.id)
        return False
    factory.event("spec_generated", m.id)
    return True
