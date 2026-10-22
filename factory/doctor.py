"""`factory doctor`: この環境 (Mac mini 等) で無人運転できるかの自己診断。

    factory doctor [--json]

終了コード: 0 = 問題なし / 1 = 警告あり / 2 = エラーあり。
ワーカー CLI には `--version` だけを投げる (prompt は送らないのでサブスク枠を使わない)。
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from . import gitx
from .config import Config, WorkerConfig
from .util import run_cmd, which

OK, WARN, ERROR = "ok", "warning", "error"
_LEVEL_RANK = {OK: 0, WARN: 1, ERROR: 2}


@dataclass
class Check:
    name: str
    level: str
    msg: str


def _cfg_checks(cfg_path: Optional[Path]) -> List[Check]:
    from .configcheck import validate

    if cfg_path is None:
        return [Check("config", ERROR, ".factory/config.json が見つかりません (factory init)")]
    try:
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return [Check("config", ERROR, f"{cfg_path}: 読めません ({e})")]
    issues = validate(data)
    if not issues:
        return [Check("config", OK, str(cfg_path))]
    return [Check(f"config:{i.path}", ERROR if i.level == "error" else WARN, i.msg) for i in issues]


def _worker_checks(cfg: Config) -> List[Check]:
    out = []
    real_ok = 0
    for name in cfg.worker_order:
        wc = cfg.workers.get(name) or WorkerConfig(type=name)
        if wc.type == "mock":
            continue
        if wc.type == "openrouter":
            if os.environ.get("OPENROUTER_API_KEY", "").strip():
                out.append(Check(f"worker:{name}", OK, "OPENROUTER_API_KEY 設定済み"))
                real_ok += 1
            else:
                out.append(Check(f"worker:{name}", WARN, "OPENROUTER_API_KEY 未設定 (このワーカーは使われません)"))
            continue
        cmd = wc.resolved_command()
        path = which(cmd)
        if not path:
            out.append(Check(f"worker:{name}", WARN, f"`{cmd}` が見つかりません (PATH: launchd では install.sh 時点の PATH)"))
            continue
        r = run_cmd([path, "--version"], timeout=30)
        if r.code != 0:
            out.append(Check(f"worker:{name}", ERROR, f"`{cmd} --version` が失敗 (exit {r.code}): {(r.err or r.out)[-200:]}"))
            continue
        ver = (r.out or r.err).strip().splitlines()[:1]
        out.append(Check(f"worker:{name}", OK, f"{cmd} {ver[0] if ver else ''} (auth={wc.auth})"))
        real_ok += 1
        if wc.auth == "subscription":
            leak = [k for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY") if os.environ.get(k)]
            if leak:
                out.append(Check(f"worker:{name}", WARN,
                                 f"{', '.join(leak)} が環境にあります (subscription なので子プロセスからは外します)"))
    use = getattr(cfg, "use_worker", "auto")
    mock_selected = use != "auto" and (cfg.workers.get(use) or WorkerConfig(type=use)).type == "mock"
    if real_ok == 0 and not mock_selected:
        out.append(Check("workers", ERROR, "使える実ワーカーがありません (claude login / codex login / OPENROUTER_API_KEY)"))
    return out


def _project_checks(cfg: Config) -> List[Check]:
    out = []
    if not cfg.projects:
        out.append(Check("projects", WARN, "projects が未登録です (mission new --project が使えません)"))
    for name, pc in cfg.projects.items():
        p = Path(pc.path)
        p = p if p.is_absolute() else Path(cfg.root) / p
        if not p.exists():
            out.append(Check(f"project:{name}", ERROR, f"{p} がありません"))
            continue
        if not gitx.is_repo(p):
            out.append(Check(f"project:{name}", WARN, f"{p} は git リポジトリではありません (初回実行で init します)"))
            continue
        if pc.main_branch and not gitx.branch_exists(p, pc.main_branch):
            out.append(Check(f"project:{name}", WARN,
                             f"main_branch={pc.main_branch} がありません (checkout 中の {gitx.default_branch(p)} を使います)"))
        dirty = gitx.tracked_dirty(p)
        if dirty:
            out.append(Check(f"project:{name}", WARN, f"未コミットの変更があります (自動マージは拒否されます): {dirty[:120]}"))
        else:
            out.append(Check(f"project:{name}", OK, str(p)))
    return out


def _state_checks(cfg: Config) -> List[Check]:
    from .state import Factory
    from .supervisor import ACTIVE_STATUSES, pause_file

    out = []
    root = Path(cfg.root)
    f = Factory(root)
    for sub in (".factory", "holdout"):
        d = root / sub
        if d.is_dir() and stat.S_IMODE(d.stat().st_mode) & 0o077:
            out.append(Check(f"perms:{sub}", WARN, f"{d} が他ユーザーから読めます (chmod 700 推奨)"))
    sealed = []
    hdir = root / "holdout"
    if hdir.is_dir():
        for d in hdir.iterdir():
            try:
                if d.is_dir() and stat.S_IMODE(d.stat().st_mode) == 0:
                    sealed.append(d.name)
            except OSError:
                continue
    orphans = []
    for m in f.list_missions():
        if m.status in ACTIVE_STATUSES:
            try:
                with f.lock(m.id):
                    orphans.append(m.id)
            except RuntimeError:
                pass  # 実行中
    if sealed:
        out.append(Check("holdout", WARN, f"封印 (000) のまま残っています: {', '.join(sealed[:5])} (次の実行/tick で復旧)"))
    if orphans:
        out.append(Check("missions", WARN, f"実行途中で止まったミッション: {', '.join(orphans[:5])} (次の tick で queued に回収)"))
    if pause_file(f).exists():
        out.append(Check("pause", WARN, f"{pause_file(f)} があるため tick は停止中です"))
    try:
        free = shutil.disk_usage(root).free
        gb = free / 1024 ** 3
        level = ERROR if gb < 0.5 else WARN if gb < 2 else OK
        out.append(Check("disk", level, f"空き {gb:.1f} GB ({root})"))
    except OSError as e:
        out.append(Check("disk", WARN, f"空き容量を取得できません: {e}"))
    if not (sealed or orphans):
        out.append(Check("state", OK, "孤児ミッション・残った封印なし"))
    return out


def _env_checks() -> List[Check]:
    out = []
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        out.append(Check("user", ERROR, "root で実行しています (holdout の権限封印が効きません。一般ユーザーで運用)"))
    else:
        out.append(Check("user", OK, f"uid={os.getuid() if hasattr(os, 'getuid') else '?'}"))
    if sys.version_info < (3, 9):
        out.append(Check("python", ERROR, f"Python {sys.version.split()[0]} (3.9 以上が必要)"))
    else:
        out.append(Check("python", OK, sys.version.split()[0]))
    git = which("git")
    if not git:
        out.append(Check("git", ERROR, "git が見つかりません"))
    else:
        r = run_cmd([git, "--version"], timeout=10)
        out.append(Check("git", OK if r.code == 0 else ERROR, (r.out or r.err).strip()))
    if sys.platform == "darwin" and hasattr(os, "getuid"):
        r = run_cmd(["launchctl", "print", f"gui/{os.getuid()}/com.softwarefactory.supervisor"], timeout=10)
        out.append(Check("launchd", OK if r.code == 0 else WARN,
                         "supervisor LaunchAgent 登録済み" if r.code == 0
                         else "supervisor が launchd に未登録 (deploy/macmini/install.sh)"))
    return out


def run_checks() -> List[Check]:
    checks = _env_checks()
    cfg_path = Config.find_file()
    checks += _cfg_checks(cfg_path)
    if cfg_path is None or any(c.level == ERROR for c in checks if c.name.startswith("config")):
        return checks
    cfg = Config.load(cfg_path)
    cfg.root = str(cfg_path.parent.parent)
    checks += _worker_checks(cfg)
    checks += _project_checks(cfg)
    checks += _state_checks(cfg)
    return checks


def exit_code(checks: List[Check]) -> int:
    return max((_LEVEL_RANK[c.level] for c in checks), default=0)


def main(as_json: bool = False) -> int:
    checks = run_checks()
    if as_json:
        print(json.dumps([vars(c) for c in checks], ensure_ascii=False, indent=2))
    else:
        mark = {OK: "✓", WARN: "!", ERROR: "✗"}
        for c in checks:
            print(f" {mark[c.level]} {c.name:<22} {c.msg}")
        code = exit_code(checks)
        print({0: "\n問題ありません", 1: "\n警告があります", 2: "\nエラーがあります (無人運転の前に直してください)"}[code])
    return exit_code(checks)
