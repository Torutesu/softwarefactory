"""Git 操作ヘルパー (標準ライブラリのみ)。"""
from __future__ import annotations

import os
import shutil
from fnmatch import fnmatch
from pathlib import Path
from typing import List, Optional

from .util import ProcResult, run_cmd

# seed_worktree_files で辿らないディレクトリ (重い/無意味)
_SKIP_WALK_DIRS = {
    ".git", "node_modules", ".venv", "venv", "__pycache__",
    "dist", "build", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "target", ".next", ".nuxt", "coverage",
}


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
        # 中断や競合で HEAD の無い (unborn) worktree が残っていると永久に失敗するので作り直す。
        # 「本当に unborn」(rev-parse が 128 かつ HEAD がブランチを指している) のときだけ。
        # タイムアウト (124) や停止要求 (130) で作業中の worktree を消さない
        r = _git(wt_path, "rev-parse", "--verify", "HEAD")
        if r.code != 128 or _git(wt_path, "symbolic-ref", "HEAD").code != 0:
            return wt_path
        _git(repo, "worktree", "remove", "--force", str(wt_path))
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


def main_branch_for(cfg, m, repo: Path) -> str:
    """projects.<p>.main_branch を返す (ミッションの repo が登録外 / そのブランチが無いなら checkout 中)。"""
    try:
        target = Path(repo).resolve()
        for pc in (getattr(cfg, "projects", None) or {}).values():
            p = Path(pc.path)
            p = p if p.is_absolute() else Path(cfg.root) / p
            if p.resolve() == target and pc.main_branch and branch_exists(repo, pc.main_branch):
                return pc.main_branch
    except OSError:
        pass
    return default_branch(repo)


_DIFF_EXCLUDES = (":(exclude,glob)**/*.lock", ":(exclude,glob)**/package-lock.json", ":(exclude,glob)**/*.min.js",
                  ":(exclude,glob)**/__pycache__/**", ":(exclude,glob)**/*.pyc")


def diff_summary(wt: Path, base: str, max_chars: int = 4000) -> str:
    """judge 用: base (main) からの実際の変更 (コミット済み + 未コミット) の要約。

    --stat と先頭の hunk を max_chars まで。lock/生成物は除外。差分が無ければ空文字。
    """
    wt = Path(wt)
    mb = _git(wt, "merge-base", base, "HEAD")
    start = mb.out.strip() if mb.code == 0 and mb.out.strip() else base
    stat = _git(wt, "diff", "--stat", "--no-renames", start, "--", ".", *_DIFF_EXCLUDES)
    if stat.code != 0 or not stat.out.strip():
        return ""
    body = _git(wt, "diff", "--no-renames", "-U2", start, "--", ".", *_DIFF_EXCLUDES).out
    text = stat.out.strip() + "\n\n" + body
    if len(text) > max_chars:
        text = text[:max_chars] + f"\n... (以下省略: 全 {len(text)} 文字)"
    return text


def repo_lock(repo: Path, lock_dir: Path):
    """repo 単位の排他 (baseline コミット作成と worktree 追加を並列ミッション間で直列化)。"""
    import hashlib

    from .util import locked

    key = hashlib.sha1(str(Path(repo).resolve()).encode("utf-8")).hexdigest()[:16]
    return locked(Path(lock_dir) / f"repo-{key}")


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


def worktrees(repo: Path) -> list:
    """`git worktree list --porcelain` を list[dict(path, head, branch)] にする。"""
    r = _git(repo, "worktree", "list", "--porcelain")
    if r.code != 0:
        return []
    out, cur = [], {}
    for line in r.out.splitlines():
        if not line.strip():
            if cur:
                out.append(cur)
            cur = {}
            continue
        k, _, v = line.partition(" ")
        if k == "worktree":
            cur["path"] = Path(v)
        elif k == "HEAD":
            cur["head"] = v
        elif k == "branch":
            cur["branch"] = v[len("refs/heads/"):]
    if cur:
        out.append(cur)
    return out


def log(wt: Path, n: int = 20, fmt: str = "%h %ad %s", extra: str = "") -> str:
    args = ["log", "--pretty=format:" + fmt, "-n", str(n)]
    if extra:
        args.append(extra)
    r = _git(wt, *args)
    return r.out.strip() if r.code == 0 else ""


def tree_fingerprint(wt: Path) -> str:
    """作業ツリー内容の指紋 (未コミット・未追跡を含む内容の tree ハッシュ)。

    noprogress 検知用: ワーカー呼出の前後で一致すれば「実差分ゼロ」。一時 index に
    `add -A` して `write-tree` するので、コミットしたか否かに左右されない (.gitignore と
    __pycache__ / *.pyc は対象外)。取得に失敗したら毎回異なる値を返す (=変化あり扱い)。
    """
    import secrets
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        env = dict(os.environ, GIT_INDEX_FILE=str(Path(td) / "index"))
        base = ["git", "-C", str(wt)]
        add = run_cmd(base + ["add", "-A", "--", ".", ":(glob,exclude)**/__pycache__/**",
                              ":(glob,exclude)**/*.pyc"], env=env, timeout=120)
        r = run_cmd(base + ["write-tree"], env=env, timeout=60) if add.code == 0 else add
    if r.code != 0 or not r.out.strip():
        return "unknown-" + secrets.token_hex(8)
    return r.out.strip()


def diff_stat(wt: Path, base: str = "HEAD") -> str:
    r = _git(wt, "diff", "--shortstat", base)
    return r.out.strip()


def changed_files(wt: Path, base: str = "HEAD") -> list:
    r = _git(wt, "diff", "--name-only", base)
    if r.code != 0:
        return []
    return [l for l in r.out.splitlines() if l.strip()]


def tracked_dirty(repo: Path) -> str:
    """追跡ファイルの未コミット変更 (staged/unstaged) と未解決の競合。未追跡は含めない。"""
    r = _git(repo, "status", "--porcelain", "--untracked-files=no")
    return r.out.strip() if r.code == 0 else "(git status failed)"


def merge_squash(repo: Path, branch: str, message: str, target_branch: str = "") -> ProcResult:
    """squash マージを試みる。失敗したら main repo を元の状態に戻してコード != 0 を返す。

    - 追跡ファイルに未コミット変更がある repo には触らない (人間の作業中の変更を factory の
      コミットに巻き込まない / 競合後の巻き戻しで消さない)
    - target_branch を渡すと、checkout 中のブランチがそれと違うときは拒否する
    - `merge --squash` は MERGE_HEAD を作らないので `merge --abort` では戻らない。
      事前に clean を確認しているので `reset --hard HEAD` で競合状態を確実に戻す
    """
    dirty = tracked_dirty(repo)
    if dirty:
        return ProcResult(1, "", "main repo に未コミットの変更があるためマージしません:\n" + dirty[:500], 0.0)
    if target_branch:
        cur = default_branch(repo)
        if cur != target_branch:
            return ProcResult(1, "", f"main repo の checkout が {cur} です ({target_branch} であること)", 0.0)
    r = _git(repo, "merge", "--squash", branch)
    if r.code != 0:
        _git(repo, "reset", "--hard", "HEAD")
        return r
    r2 = _git(repo, "commit", "-m", message)
    if r2.code != 0:
        _git(repo, "reset", "--hard", "HEAD")  # 空の squash 等: 半端な index を残さない
    return r2


def revert(repo: Path, sha: str) -> ProcResult:
    return _git(repo, "revert", "--no-edit", sha)


def abort_revert(repo: Path) -> ProcResult:
    return _git(repo, "revert", "--abort")


def remote_url(repo: Path, name: str = "origin") -> Optional[str]:
    r = _git(repo, "config", f"remote.{name}.url")
    return r.out.strip() if r.code == 0 and r.out.strip() else None


def branch_changes(repo: Path, branch: str, base: str = "") -> dict:
    """ブランチの base からの変更統計を返す。

    戻り値: {"files": int, "lines": int, "paths": [str], "binary": [str], "deleted": [str]}
    マージゲートの blast radius 分類 (mergegate.py) で使う。
    merge-base 基準 (3-dot) なので、base 側が進んでいても逆差分を数えない。
    """
    empty = {"files": 0, "lines": 0, "paths": [], "binary": [], "deleted": []}
    if not branch_exists(repo, branch) or not has_commits(repo):
        return empty
    base = base or default_branch(repo)
    rng = f"{base}...{branch}"
    r = _git(repo, "diff", "--numstat", "--no-renames", rng)
    if r.code != 0:
        rng = f"{base}..{branch}"
        r = _git(repo, "diff", "--numstat", "--no-renames", rng)
        if r.code != 0:
            return empty
    files, lines, paths, binary = 0, 0, [], []
    for ln in (r.out or "").splitlines():
        if not ln.strip():
            continue
        added, _, rest = ln.partition("\t")
        deleted, _, path = rest.partition("\t")
        path = path.strip()
        paths.append(path)
        files += 1
        if added == "-" and deleted == "-":
            binary.append(path)  # 行数に出ないので「小さい差分」に見せかけられないよう別枠で返す
        if added.isdigit():
            lines += int(added)
        if deleted.isdigit():
            lines += int(deleted)
    ns = _git(repo, "diff", "--name-status", "--no-renames", rng)
    removed = [ln.split("\t", 1)[1].strip() for ln in (ns.out or "").splitlines()
               if ln.startswith("D\t")] if ns.code == 0 else []
    return {"files": files, "lines": lines, "paths": paths, "binary": binary, "deleted": removed}


def seed_worktree_files(repo, wt, patterns=None):
    """リポジトリの非追跡ファイル (.env 等) を worktree に複製する。

    worktree は空の checkout なので、gitignore された環境ファイルが存在しない。
    並列エージェントの 4 つ目あたりで「ポートと .env」が壊れるのはこのため
    (Firecrawl: How to Build an AI Software Factory, Stage 2)。

    パターンは fnmatch (相対パス or ファイル名の両方に適用)。リポジトリに
    `.worktreeinclude` がある場合はそれを優先する (1 行 1 パターン, # はコメント)。
    patterns に空リストを渡した場合は何もコピーしない。
    """
    from pathlib import Path as _P
    repo, wt = _P(repo), _P(wt)
    if patterns is None:
        from .config import DEFAULT_WORKTREE_COPY
        patterns = list(DEFAULT_WORKTREE_COPY)
    include = list(patterns or [])
    inc_file = repo / ".worktreeinclude"
    if inc_file.exists():
        lines = load_lines(inc_file)
        include = [l for l in (s.strip() for s in lines) if l and not l.startswith("#")] or include
    if not include:
        return []

    copied = []
    for dirpath, dirnames, filenames in os.walk(repo):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_WALK_DIRS]
        for fn in filenames:
            full = _P(dirpath) / fn
            rel = full.relative_to(repo)
            posix = rel.as_posix()
            if not any(fnmatch(posix, p) or fnmatch(fn, p) for p in include):
                continue
            dst = wt / rel
            if dst.exists():
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(full, dst)
                copied.append(posix)
            except OSError:
                pass  # 複製失敗はベストエフォート
    return copied


def load_lines(path) -> List[str]:
    try:
        return Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
