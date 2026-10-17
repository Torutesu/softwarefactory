"""カナリア運用: マージ済みミッションを main 先頭で再検証し、失敗なら revert + 再ミッション。

    factory canary <mission> [--fix] [--run-complete]

- main の先頭に一時 worktree (detach) を作り、ミッションの Harness (build/test/シナリオ) を再実行。
- 合格 → 記録して worktree を掃除。
- 失敗 → --fix 指定時: 当該 squash コミットを main 上で revert し、
  失敗要約を Seed 化して新しい update ミッションを立てる (--run-complete で自動実行まで)。
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path
from typing import Optional

from . import gitx
from .harness.runner import evaluate
from .state import Factory, Mission
from .util import now_utc_iso, save_text


def _repo_path(factory: Factory, m: Mission) -> Path:
    return Path(m.repo).resolve() if Path(m.repo).is_absolute() else factory.root / m.repo


def run_canary(factory: Factory, mid: str, cfg=None, fix: bool = False, run_complete: bool = False) -> dict:
    m = factory.get_mission(mid)
    if not m:
        raise SystemExit(f"mission not found: {mid}")
    if m.status != "done" or "merged to main" not in (m.notes or ""):
        raise SystemExit(f"{mid} は merge 済みミッションではありません (status={m.status})")

    repo = _repo_path(factory, m)
    default = gitx.default_branch(repo)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    cwt = factory.mission_dir(mid) / "canary"
    gitx.create_worktree(repo, cwt, detached=True, branch=default)
    try:
        hr = evaluate(factory, m, cwt)
    finally:
        gitx.remove_worktree(repo, cwt, force=True)

    run_dir = factory.mission_dir(mid) / "reports"
    report_lines = [f"# Canary Report — {mid}", "", f"- 実行: {now_utc_iso()} (main 最新: {default})"]

    def step_block(title, sr) -> str:
        if not sr:
            return ""
        block = f"## {title}: {'PASS' if sr.ok else 'FAIL'}\n`{sr.cmd}`"
        if not sr.ok:
            block += "\n```\n" + sr.tail[-800:] + "\n```"
        return block

    report_lines.append(step_block("Build", hr.build))
    report_lines.append(step_block("Test", hr.test))
    report_lines.append("## シナリオ")
    report_lines.append("| ID | 判定 | タイトル |")
    report_lines.append("|---|---|---|")
    for s in hr.scenarios:
        report_lines.append(f"| {s.id} | {'✅' if s.ok else '❌'} | {s.title} |")
    report_path = run_dir / f"canary-{stamp}.md"
    save_text(report_path, "\n".join(report_lines))

    if hr.ok:
        factory.event("canary_pass", mid)
        print(f"[canary] {mid}: PASS on main")
        return {"status": "pass", "report": str(report_path)}

    factory.event("canary_fail", mid)
    print(f"[canary] {mid}: FAIL on main (report: {report_path})")
    result = {"status": "fail", "report": str(report_path)}

    if fix:
        result["revert"] = _try_revert(factory, repo, mid)
        result["new_mission"] = _reseed_failure(factory, m, hr)
        new_mid = result["new_mission"]
        if new_mid and run_complete:
            try:
                from .loop import run_mission

                run_mission(factory, new_mid, cfg=cfg)
                result["new_mission_status"] = "done"
            except SystemExit as e:
                print(f"[canary] auto-run skip: {e}")
    return result


def _try_revert(factory: Factory, repo: Path, mid: str) -> str:
    bad = find_factory_commit(repo, mid)
    if gitx.status_porcelain(repo):
        msg = f"revert 中止: {repo} に未コミットの変更があります"
        print(f"[canary] {msg}", file=sys.stderr)
        return "aborted(dirty)"
    if bad is None:
        msg = f"revert 中止: {mid} のコミットが見つかりません"
        print(f"[canary] {msg}", file=sys.stderr)
        return "not-found"
    r = gitx.revert(repo, bad)
    if r.code == 0:
        print(f"[canary] main 上の {bad[:8]} を revert しました")
        return f"reverted {bad[:8]}"
    gitx.abort_revert(repo)
    print(f"[canary] revert 失敗: {r.err or r.out}", file=sys.stderr)
    return "failed: " + (r.err or r.out)[-200:]


def _reseed_failure(factory: Factory, m: Mission, hr) -> Optional[str]:
    fail_rows = [f"{s.id} FAIL: {s.title}" for s in hr.scenarios if not s.ok]
    extra = list(hr.warnings)
    if hr.test and not hr.test.ok:
        extra.append("test 失敗 (レポート参照)")
    body = "\n".join(["- " + w for w in (fail_rows + extra)[:12]]) or "(詳細はレポート参照)"
    seed_name = f"{m.id}-canary-fail"
    from .ingest import write_seed_dir

    write_seed_dir(
        factory, seed_name, f"カナリア失敗の再挑戦: {m.title}",
        f"# カナリア失敗: {m.id} {m.title}\n\n"
        f"main マージ後のカナリア検証に失敗しました。原因を修正し、再びカナリア検証を通してください。\n\n"
        f"## 失敗要約\n\n{body}\n\n"
        f"レポート: reports/{m.id}/canary-*.md\n",
    )
    new_m = factory.create_mission(
        title=f"カナリア再挑戦: {m.title}",
        kind="update",
        seed=seed_name,
        repo=m.repo,
        risk=m.risk,
        priority=1,
        pass_score=m.pass_score,
        notes="auto-created by canary",
    )
    print(f"[canary] 再ミッション: {new_m.id}")
    return new_m.id


def find_factory_commit(repo: Path, mid: str) -> Optional[str]:
    """メッセージが `factory: <mid> ...` で始まる main 上のコミットを探す。"""
    r = gitx._git(repo, "log", "--pretty=format:%h %s", "-n", "50")
    if r.code != 0:
        return None
    for line in r.out.splitlines():
        sha, _, subject = line.partition(" ")
        if subject.startswith(f"factory: {mid} "):
            return sha
    return None
