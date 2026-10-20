"""軽量ユーティリティ集 (標準ライブラリのみ)。"""
from __future__ import annotations

import datetime as dt
import contextlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class ProcResult:
    code: int
    out: str
    err: str
    seconds: float
    timed_out: bool = False


def now_utc_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def today_local() -> str:
    return dt.date.today().isoformat()


def ensure_dir(p: Path) -> Path:
    p = Path(p)
    p.mkdir(parents=True, exist_ok=True)
    return p


def load_json(path: Path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def save_json(path: Path, data) -> None:
    path = Path(path)
    ensure_dir(path.parent)
    tmp = tempfile.NamedTemporaryFile("w", delete=False, dir=str(path.parent), encoding="utf-8")
    try:
        json.dump(data, tmp, ensure_ascii=False, indent=2)
        tmp.flush()
        os.replace(tmp.name, str(path))
    finally:
        if os.path.exists(tmp.name):
            os.unlink(tmp.name)


def load_text(path: Path, default: str = "") -> str:
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError:
        return default


def save_text(path: Path, text: str) -> None:
    path = Path(path)
    ensure_dir(path.parent)
    path.write_text(text, encoding="utf-8")


@contextlib.contextmanager
def locked(path: Path):
    """同一パスへの read-modify-write を flock で直列化する。

    run-all --parallel ( ThreadPool ) / supervisor 同時実行時に state.json や
    usage の「読んで→書く」が交錯して片方の更新を落とすのを防ぐための最小限の排他。
    既定のロックファイルは `<path>.lock` (中身は使わない inode 目的)。
    """
    import fcntl

    path = Path(path)
    ensure_dir(path.parent)
    lockfile = path.parent / (path.name + ".lock")
    with open(lockfile, "a+", encoding="utf-8") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def run_cmd(args, cwd=None, timeout=None, env=None) -> ProcResult:
    """引数リストで外部コマンドを実行 (shell 不使用)。"""
    t0 = dt.datetime.now()
    try:
        r = subprocess.run(
            [str(a) for a in args],
            cwd=str(cwd) if cwd else None,
            timeout=timeout,
            capture_output=True,
            text=True,
            env=env,
        )
        pr = ProcResult(r.returncode, r.stdout or "", r.stderr or "", (dt.datetime.now() - t0).total_seconds())
    except subprocess.TimeoutExpired as e:
        pr = ProcResult(124, e.stdout or "", e.stderr or "", (dt.datetime.now() - t0).total_seconds(), timed_out=True)
    except FileNotFoundError as e:
        pr = ProcResult(127, "", str(e), 0.0)
    return pr


def run_shell(cmd: str, cwd=None, timeout=None, env=None) -> ProcResult:
    """シェル文字列 (/bin/bash -c) で実行。build.json やシナリオ検証用。"""
    args = ["/bin/bash", "-lc", cmd]
    return run_cmd(args, cwd=cwd, timeout=timeout, env=env)


def child_env(extra: Optional[dict] = None) -> dict:
    env = dict(os.environ)
    if extra:
        env.update({k: str(v) for k, v in extra.items()})
    return env


def tail(text: str, n: int = 1200) -> str:
    if not text:
        return ""
    text = text.strip()
    if len(text) <= n:
        return text
    return "…" + text[-n:]


def head(text: str, n: int = 1200) -> str:
    if not text:
        return ""
    text = text.strip()
    if len(text) <= n:
        return text
    return text[:n] + "…"


def slugify(s: str, fallback: str = "seed") -> str:
    """ディレクトリ名へ変換。英数字と \w (日本語などの文字) を許し、全部消えたら fallback。"""
    s = re.sub(r"[^\w\-]+", "-", s, flags=re.UNICODE).strip("-")
    return (s or fallback)[:60].lower()


def which(cli: str) -> Optional[str]:
    return shutil.which(cli)


def fsize(p: Path) -> int:
    try:
        return p.stat().st_size
    except OSError:
        return 0
