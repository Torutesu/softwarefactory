"""Git 操作ヘルパー (標準ライブラリのみ)。"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Optional

from .util import ProcResult, run_cmd


def _git(repo: Path, *args, timeout: int = 60) -> ProcResult:
    return run_cmd(["git", "-C", str(repo), *args], timeout=timeout)


def is_repo(path: Path) -> bool:
    r = _git(path, "rev-parse", "--is-inside-work-tree")
    return r.code == 0 and r.out.strip() == "true"


def ensure_repo(path: Path) -> Path:
    """git リポジトリを保証し、bot 用のローカルIDも設定する。"""
    path = Path(path)
    if not path.exists():
        path.mkdir(parents=True)
    if not is_repo(path):
        run_cmd(["git", "init", "-b", "main"], cwd=path)
    # bot 用IDが未設定なら設定 (コミットを可能にする)
    r = _git(path, "config", "user.email")
    if r.code != 0 or not r.out.strip():
        _git(path, "config", "user.name", "Software Factory Bot")
        _git(path, "config", "user.email", "factory-bot@internal.local")
    return path


def ensure_baseline_commit(repo: Path, message: str = "factory: baseline") -> Optional[str]:
    """HEAD が無い (commit 0) なら初期コミットを作る。戻り値はコミットハッシュ。"""
    if has_commits(repo):
        return head_commit(repo)
    run_cmd(["git", "add", "-A"], cwd=repo)
    r = _git(repo, "commit", "-m", message)
    if r.code != 0:
        # 変更が1つも無い空リポジトリの場合
        r = _git(repo, "commit", "--allow-empty", "-m", message)
    return head_commit(repo)


def has_commits(repo: Path) -> bool:
    return _git(repo, "rev-parse", "--verify", "HEAD").code == 0


def head_commit(repo: Path) -> Optional[str]:
    r = _git(repo, "rev-parse", "HEAD")
    return r.out.strip() if r.code == 0 else None


def default_branch(repo: Path) -> str:
    r = _git(repo, "symbolic-ref", "--short", "HEAD")
    return r.out.strip() if r.code == 0 else "main"


def branch_exists(repo: Path, branch: str) -> bool:
    return _git(repo, "rev-parse", "--verify", "--quiet", branch).code == 0


def create_worktree(repo: Path, wt_path: Path, branch: str = "", detached: bool = False) -> Path:
    """worktree を作る。既存があれば再利用。branch を省略か detached=True で detach。"""
    wt_path = Path(wt_path)
    if wt_path.exists() and (wt_path / ".git").exists():
        return wt_path
    # ゴミ掃除 (パスが残っているが worktree が無い場合)
    _git(repo, "worktree", "prune")
    if wt_path.exists():
        shutil.rmtree(wt_path)
    if detached or not branch:
        args = ["worktree", "add", "--detach", str(wt_path)] + ([branch] if branch else [])
        r = _git(repo, *args)
    elif branch_exists(repo, branch):
        r = _git(repo, "worktree", "add", str(wt_path), branch)
    else:
        r = _git(repo, "worktree", "add", "-b", branch, str(wt_path))
    if r.code != 0:
        raise RuntimeError("worktree add failed: " + (r.err or r.out))
    return wt_path


def remove_worktree(repo: Path, wt_path: Path, force: bool = True) -> None:
    wt_path = Path(wt_path)
    if not wt_path.exists():
        _git(repo, "worktree", "prune")
        return
    args = ["worktree", "remove"]
    if force:
        args.append("--force")
    args.append(str(wt_path))
    r = _git(repo, *args)
    if r.code != 0:
        _git(repo, "worktree", "prune")


def commit_all(wt: Path, message: str) -> ProcResult:
    run_cmd(["git", "add", "-A"], cwd=wt)
    return _git(wt, "commit", "-m", message)


def status_porcelain(wt: Path) -> str:
    return _git(wt, "status", "--porcelain").out.strip()


def log(wt: Path, n: int = 20, fmt: str = "%h %ad %s", extra: str = "") -> str:
    args = ["log", "--pretty=format:" + fmt, "-n", str(n)]
    if extra:
        args.append(extra)
    r = _git(wt, *args)
    return r.out.strip() if r.code == 0 else ""


def diff_stat(wt: Path, base: str = "HEAD") -> str:
    r = _git(wt, "diff", "--shortstat", base)
    return r.out.strip()


def changed_files(wt: Path, base: str = "HEAD") -> list:
    r = _git(wt, "diff", "--name-only", base)
    if r.code != 0:
        return []
    return [l for l in r.out.splitlines() if l.strip()]


def merge_squash(repo: Path, branch: str, message: str) -> ProcResult:
    """squash マージを試みる。競合時はコード 1 を返す。"""
    r = _git(repo, "merge", "--squash", branch)
    if r.code != 0:
        _git(repo, "merge", "--abort")
        return r
    r2 = _git(repo, "commit", "-m", message)
    return r2


def revert(repo: Path, sha: str) -> ProcResult:
    return _git(repo, "revert", "--no-edit", sha)


def abort_revert(repo: Path) -> ProcResult:
    return _git(repo, "revert", "--abort")


def remote_url(repo: Path, name: str = "origin") -> Optional[str]:
    r = _git(repo, "config", f"remote.{name}.url")
    return r.out.strip() if r.code == 0 and r.out.strip() else None
