"""config.json の検証 (`factory config check` / supervisor 起動時)。

外部ライブラリなしで、型・範囲・列挙・参照の整合 (worker 名・project 名) を確かめる。
未知のキーは typo の可能性があるので警告し、近い名前を提案する。
自律運転 (level 3/4) では設定ミスが「黙って違う動き」になるのが一番危ないので、
エラーがあれば supervisor / tick は起動しない。
"""
from __future__ import annotations

import dataclasses
import difflib
from dataclasses import dataclass
from typing import Any, List

from .config import Config, ProjectConfig, WorkerConfig

KNOWN_WORKER_TYPES = ("claude", "codex", "opencode", "openrouter", "mock")
BUILTIN_WORKER_NAMES = ("claude_code", "claude", "codex", "opencode", "openrouter", "mock")
PURPOSES = ("spec", "holdout", "dev", "judge")
TIERS = ("mechanical", "scoped", "behavioral", "sensitive")
AUTH_MODES = ("subscription", "api_key")

# (最小, 最大) — None は制限なし
RANGES = {
    "autonomy_level": (1, 4),
    "tick_concurrency": (1, 32),
    "max_repair_rounds": (0, 10),
    "small_change_lines": (0, None),
    "intake_dup_threshold": (0.0, 1.0),
    "noprogress_iterations": (0, None),
    "judge_pass_score": (0, 100),
    "max_attempts": (1, 20),
    "max_recover_attempts": (0, 20),
    "judge_diff_chars": (0, 200_000),
}
WORKER_RANGES = {
    "timeout": (1, None),
    "daily_call_limit": (0, None),
    "window_call_limit": (0, None),
    "window_minutes": (1, 7 * 24 * 60),
    "max_turns": (0, None),
}


@dataclass
class Issue:
    path: str
    level: str  # error | warning
    msg: str

    def __str__(self) -> str:
        return f"[{self.level}] {self.path}: {self.msg}"


def _fields(cls) -> dict:
    out = {}
    for f in dataclasses.fields(cls):
        if f.default is not dataclasses.MISSING:
            out[f.name] = f.default
        elif f.default_factory is not dataclasses.MISSING:  # type: ignore[misc]
            out[f.name] = f.default_factory()  # type: ignore[misc]
        else:
            out[f.name] = None
    return out


def _type_ok(value: Any, default: Any) -> bool:
    if default is None:
        return True
    if isinstance(default, bool):
        return isinstance(value, bool)
    if isinstance(default, int):
        return isinstance(value, int) and not isinstance(value, bool)
    if isinstance(default, float):
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if isinstance(default, str):
        return isinstance(value, str)
    if isinstance(default, list):
        return isinstance(value, list)
    if isinstance(default, dict):
        return isinstance(value, dict)
    return True


def _tname(default: Any) -> str:
    return {bool: "true/false", int: "整数", float: "数値", str: "文字列", list: "配列", dict: "オブジェクト"}.get(
        type(default), type(default).__name__)


def _unknown(path: str, key: str, known) -> Issue:
    near = difflib.get_close_matches(key, list(known), n=1, cutoff=0.7)
    hint = f" (もしかして `{near[0]}`?)" if near else ""
    return Issue(f"{path}{key}", "warning", f"未知のキーです{hint}")


def _range(path: str, value, lo, hi) -> List[Issue]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return []
    if lo is not None and value < lo or hi is not None and value > hi:
        bounds = f"{lo if lo is not None else '-∞'}..{hi if hi is not None else '∞'}"
        return [Issue(path, "error", f"{value} は範囲外です ({bounds})")]
    return []


def validate(data: Any) -> List[Issue]:
    issues: List[Issue] = []
    if not isinstance(data, dict):
        return [Issue("$", "error", "config はオブジェクトであること")]
    top = _fields(Config)
    top.pop("root", None)
    for key, value in data.items():
        if key == "root":
            continue
        if key not in top:
            issues.append(_unknown("", key, top))
            continue
        default = top[key]
        if key in ("projects", "workers"):
            continue
        if not _type_ok(value, default):
            issues.append(Issue(key, "error", f"{_tname(default)}であること (値: {value!r})"))
            continue
        if key in RANGES:
            issues += _range(key, value, *RANGES[key])

    workers = data.get("workers") or {}
    if not isinstance(workers, dict):
        issues.append(Issue("workers", "error", "オブジェクトであること"))
        workers = {}
    wfields = _fields(WorkerConfig)
    for name, raw in workers.items():
        p = f"workers.{name}."
        if not isinstance(raw, dict):
            issues.append(Issue(p[:-1], "error", "オブジェクトであること"))
            continue
        for k, v in raw.items():
            if k not in wfields:
                issues.append(_unknown(p, k, wfields))
                continue
            if not _type_ok(v, wfields[k]):
                issues.append(Issue(p + k, "error", f"{_tname(wfields[k])}であること (値: {v!r})"))
                continue
            if k in WORKER_RANGES:
                issues += _range(p + k, v, *WORKER_RANGES[k])
        wtype = raw.get("type", name)
        if wtype not in KNOWN_WORKER_TYPES and wtype != "claude_code":
            issues.append(Issue(p + "type", "error", f"{wtype!r} は未知の種別です ({', '.join(KNOWN_WORKER_TYPES)})"))
        if raw.get("auth", "subscription") not in AUTH_MODES:
            issues.append(Issue(p + "auth", "error", f"{raw.get('auth')!r} は subscription か api_key"))

    known_names = set(workers) | set(BUILTIN_WORKER_NAMES)

    def check_names(path: str, names) -> None:
        if not isinstance(names, list):
            return
        for i, n in enumerate(names):
            if n not in known_names:
                near = difflib.get_close_matches(str(n), sorted(known_names), n=1, cutoff=0.6)
                hint = f" (もしかして `{near[0]}`?)" if near else ""
                issues.append(Issue(f"{path}[{i}]", "error", f"ワーカー {n!r} は workers に定義がありません{hint}"))

    check_names("worker_order", data.get("worker_order"))
    use = data.get("use_worker", "auto")
    if isinstance(use, str) and use != "auto" and use not in known_names:
        issues.append(Issue("use_worker", "error", f"{use!r} は auto か workers のキー"))
    jw = data.get("judge_worker", "")
    if isinstance(jw, str) and jw and jw not in known_names:
        issues.append(Issue("judge_worker", "error", f"{jw!r} は workers のキー"))
    pw = data.get("purpose_workers") or {}
    if isinstance(pw, dict):
        for purpose, names in pw.items():
            if purpose not in PURPOSES:
                issues.append(Issue(f"purpose_workers.{purpose}", "error", f"用途は {', '.join(PURPOSES)}"))
            elif not isinstance(names, list):
                issues.append(Issue(f"purpose_workers.{purpose}", "error", "配列であること"))
            else:
                check_names(f"purpose_workers.{purpose}", names)
    mp = data.get("merge_policy") or {}
    if isinstance(mp, dict):
        for tier, pol in mp.items():
            if tier not in TIERS:
                issues.append(Issue(f"merge_policy.{tier}", "error", f"ティアは {', '.join(TIERS)}"))
            elif str(pol).lower() not in ("auto", "review"):
                issues.append(Issue(f"merge_policy.{tier}", "error", f"{pol!r} は auto か review"))

    projects = data.get("projects") or {}
    if not isinstance(projects, dict):
        issues.append(Issue("projects", "error", "オブジェクトであること"))
        projects = {}
    pfields = _fields(ProjectConfig)
    for name, raw in projects.items():
        p = f"projects.{name}."
        if not isinstance(raw, dict):
            issues.append(Issue(p[:-1], "error", "オブジェクトであること"))
            continue
        if not isinstance(raw.get("path"), str) or not raw.get("path"):
            issues.append(Issue(p + "path", "error", "リポジトリのパス (文字列) が必要です"))
        for k, v in raw.items():
            if k not in pfields:
                issues.append(_unknown(p, k, pfields))
            elif pfields[k] is not None and not _type_ok(v, pfields[k]):
                issues.append(Issue(p + k, "error", f"{_tname(pfields[k])}であること"))
    ap = data.get("autopilot_project", "")
    if isinstance(ap, str) and ap and ap not in projects:
        issues.append(Issue("autopilot_project", "error", f"{ap!r} は projects に登録がありません"))
    url = data.get("notify_url", "")
    if isinstance(url, str) and url and not url.startswith(("http://", "https://")):
        issues.append(Issue("notify_url", "error", "http(s):// の URL であること"))
    return issues


def errors(issues: List[Issue]) -> List[Issue]:
    return [i for i in issues if i.level == "error"]
