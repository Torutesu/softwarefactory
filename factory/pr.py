"""PR 連携 (gh CLI)。

    factory pr <mission>

- リポジトリに origin リモートがある場合: ブランチを push し `gh pr create` でPR作成
- gh/リモートが無い場合: PR 下書き (reports/pr-draft.md) を出力する
"""
from __future__ import annotations

import sys
from pathlib import Path

from . import gitx
from .config import Config
from .state import Factory, Mission
from .util import save_text, which


def create_pr(factory: Factory, mid: str, base: str = "") -> dict:
    m = factory.get_mission(mid)
    if not m:
        raise SystemExit(f"mission not found: {mid}")
    if not m.iterations:
        raise SystemExit(f"{mid} は実行履歴がありません")

    repo = Path(m.repo).resolve() if Path(m.repo).is_absolute() else factory.root / m.repo
    title = f"factory: {m.id} {m.title}"
    body = _make_body(factory, m)
    draft = factory.mission_dir(mid) / "reports" / "pr-draft.md"
    save_text(draft, f"# {title}\n\n{body}\n")

    remote = gitx.remote_url(repo)
    gh = which("gh")
    branch = f"factory/{m.id}"
    if not gh or not remote:
        print("gh または origin リモートが無いため PR は作成せず下書きを保存しました:")
        print(f"  {draft}")
        return {"status": "draft", "draft": str(draft)}

    # ブランチが worktree 内にしかある場合も反映のため push する
    r0 = gitx._git(repo, "push", "-u", "origin", branch)
    if r0.code != 0:
        print(f"push 失敗: {r0.err or r0.out}", file=sys.stderr)
        return {"status": "push-failed", "draft": str(draft)}

    args = [gh, "pr", "create", "--title", title, "--body-file", str(draft), "--head", branch]
    if base:
        args += ["--base", base]
    from .util import run_cmd

    r = run_cmd(args, cwd=repo, timeout=120)
    if r.code != 0:
        print(f"PR 作成失敗: {r.err or r.out}", file=sys.stderr)
        return {"status": "failed", "draft": str(draft)}
    lines = (r.out or "").strip().splitlines()
    url = lines[-1] if lines else "(gh は URL を出力しませんでした)"
    print(f"PR created: {url}")
    return {"status": "created", "url": url, "draft": str(draft)}


def _make_body(factory: Factory, m: Mission) -> str:
    lines = ["## 検証結果", ""]
    lines.append("| # | worker | 判定 | score |")
    lines.append("|---|---|---|---|")
    for it in m.iterations:
        lines.append(f"| {it.n} | {it.worker} | {it.status} | {it.score if it.score is not None else '-'} |")
    final = (factory.reports_dir(m.id) / "final.md").read_text(encoding="utf-8") if (factory.reports_dir(m.id) / "final.md").exists() else ""
    if final:
        lines.append("\n---\n\n" + final)
    # マージゲート (ティア・帰属) — 分類できない (ブランチ消滅済み等) 場合は帰属だけ
    from . import mergegate

    cls = None
    try:
        repo = Path(m.repo).resolve() if Path(m.repo).is_absolute() else factory.root / m.repo
        cls = mergegate.classify(Config.discover() or Config(root=str(factory.root)),
                                 repo, f"factory/{m.id}", m)
    except Exception:  # noqa — PR 本文は必ず出せる
        cls = None
    lines.append("\n---\n\n" + mergegate.pr_blurb(cls, m))
    return "\n".join(lines)
