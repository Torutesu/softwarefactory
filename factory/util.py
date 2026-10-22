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
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return default


def quarantine_corrupt(path: Path) -> Optional[Path]:
    """壊れた JSON を <name>.corrupt-<ts> に退避する (次の書き込みで黙って消さないため)。

    ファイルが存在してパースできないときだけ退避し、退避先を返す。
    """
    path = Path(path)
    if not path.is_file():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            json.load(f)
        return None
    except (json.JSONDecodeError, UnicodeDecodeError):
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        dst = path.with_name(f"{path.name}.corrupt-{stamp}")
        try:
            os.replace(path, dst)
        except OSError:
            return None
        return dst
    except OSError:
        return None


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


def _to_text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bytes):
        return v.decode("utf-8", "replace")
    return v


def _kill_group(proc) -> None:
    """プロセスグループごと止める (TERM → 3 秒 → KILL)。孫プロセスを残さない。"""
    import signal

    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except (OSError, ProcessLookupError):
            return
        try:
            proc.wait(timeout=3)
            return
        except subprocess.TimeoutExpired:
            continue


def run_cmd(args, cwd=None, timeout=None, env=None) -> ProcResult:
    """引数リストで外部コマンドを実行 (shell 不使用)。

    新しいプロセスグループで起動し、タイムアウト時はグループごと止める
    (ワーカー CLI や build/test が起動した孫プロセスを残さない)。
    出力は常に str で返す (TimeoutExpired の途中出力は bytes で来るため decode する)。
    """
    t0 = dt.datetime.now()
    try:
        proc = subprocess.Popen(
            [str(a) for a in args],
            cwd=str(cwd) if cwd else None,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            env=env,
            start_new_session=True,
        )
    except FileNotFoundError as e:
        return ProcResult(127, "", str(e), 0.0)
    except OSError as e:  # PermissionError (実行権なし) / E2BIG (引数が長すぎる) など
        return ProcResult(126, "", f"{type(e).__name__}: {e}", 0.0)
    try:
        out, err = proc.communicate(timeout=timeout)
        return ProcResult(proc.returncode, out or "", err or "", (dt.datetime.now() - t0).total_seconds())
    except subprocess.TimeoutExpired as e:
        _kill_group(proc)
        try:
            out, err = proc.communicate(timeout=5)
        except (subprocess.TimeoutExpired, ValueError, OSError):
            out, err = _to_text(e.stdout), _to_text(e.stderr)
        return ProcResult(124, _to_text(out), _to_text(err), (dt.datetime.now() - t0).total_seconds(),
                          timed_out=True)
    except BaseException:
        _kill_group(proc)  # Ctrl-C / SIGTERM 中断でも子を残さない
        raise


def run_shell(cmd: str, cwd=None, timeout=None, env=None) -> ProcResult:
    """シェル文字列 (/bin/bash -c) で実行。build.json やシナリオ検証用。"""
    args = ["/bin/bash", "-lc", cmd]
    return run_cmd(args, cwd=cwd, timeout=timeout, env=env)


def child_env(extra: Optional[dict] = None) -> dict:
    env = dict(os.environ)
    if extra:
        env.update({k: str(v) for k, v in extra.items()})
    return env


# 名前から秘密情報と判断する環境変数 (値は見ない)。LLM が書いたコードを動かす harness と、
# 確認なしで動くワーカー CLI には、明示的に許可したもの以外渡さない
_SECRET_ENV_RE = re.compile(
    r"(API_?KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIALS?|PRIVATE_KEY|ACCESS_KEY|SESSION_KEY)",
    re.IGNORECASE)
_SECRET_ENV_EXACT = {"SSH_AUTH_SOCK", "GPG_AGENT_INFO", "NETRC", "DATABASE_URL"}


def is_secret_env(name: str) -> bool:
    return name.upper() in _SECRET_ENV_EXACT or bool(_SECRET_ENV_RE.search(name))


def scrubbed_env(extra: Optional[dict] = None, passthrough=()) -> dict:
    """os.environ から秘密っぽい変数を除いた環境 (passthrough に挙げた名前は残す)。"""
    keep = {p.upper() for p in (passthrough or ())}
    env = {k: v for k, v in os.environ.items() if k.upper() in keep or not is_secret_env(k)}
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
    r"""ディレクトリ名へ変換。英数字と \w (日本語などの文字) を許し、全部消えたら fallback。"""
    s = re.sub(r"[^\w\-]+", "-", s, flags=re.UNICODE).strip("-")
    return (s or fallback)[:60].lower()


def which(cli: str) -> Optional[str]:
    return shutil.which(cli)


def fsize(p: Path) -> int:
    try:
        return p.stat().st_size
    except OSError:
        return 0
