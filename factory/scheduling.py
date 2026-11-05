"""実行順の制御: ミッション間の依存 (DAG) とプロジェクト別のクォータ。

依存: `factory mission new ... --after <id>` で「<id> が main にマージされてから始める」。
  - 前提が failed / blocked / 存在しない → 後続は blocked 扱いで人間に返す
  - 前提が done でも未マージ → 待つ (後続は main に入った前提のコードの上で作業する)
  - 循環は作成時に拒否する

クォータ (projects.<p>): daily_mission_limit (1 日に開始するミッション数), max_concurrent
  (同時に実行中のミッション数)。0 は無制限。超えた分は次の tick に回す (deferred)。
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import List, Optional, Tuple

from .state import Factory, Mission, is_merged

ACTIVE = ("spec", "holdout", "dev")


# ---------------------------------------------------------------------------
# dependencies
# ---------------------------------------------------------------------------
def deps_state(factory: Factory, m: Mission) -> Tuple[str, str]:
    """("ready" | "waiting" | "blocked", 説明)。"""
    waiting = []
    for dep in m.depends_on or []:
        dm = factory.get_mission(dep)
        if dm is None:
            return "blocked", f"前提 {dep} が見つかりません"
        if dm.status in ("failed", "blocked"):
            return "blocked", f"前提 {dep} が {dm.status} ({dm.halt_reason or '-'})"
        if not is_merged(dm):
            waiting.append(f"{dep} ({'未マージ' if dm.status == 'done' else dm.status})")
    if waiting:
        return "waiting", "前提待ち: " + ", ".join(waiting)
    return "ready", ""


def check_cycle(factory: Factory, mid: str, deps: List[str]) -> None:
    """mid が deps に依存すると循環になるなら ValueError。"""
    seen = set()
    stack = list(deps)
    while stack:
        cur = stack.pop()
        if cur == mid:
            raise ValueError(f"依存が循環します: {mid} -> ... -> {mid}")
        if cur in seen:
            continue
        seen.add(cur)
        dm = factory.get_mission(cur)
        if dm is not None:
            stack.extend(dm.depends_on or [])


def validate_deps(factory: Factory, mid: str, deps: List[str]) -> List[str]:
    out = []
    for d in deps:
        d = d.strip()
        if not d:
            continue
        if factory.get_mission(d) is None:
            raise ValueError(f"前提ミッション {d} が見つかりません")
        if d not in out:
            out.append(d)
    check_cycle(factory, mid, out)
    return out


# ---------------------------------------------------------------------------
# quotas
# ---------------------------------------------------------------------------
def project_of(cfg, m: Mission) -> str:
    """ミッションのプロジェクト名 (mission.project か、repo パスの一致で求める)。"""
    if m.project:
        return m.project
    try:
        target = (Path(m.repo) if Path(m.repo).is_absolute() else Path(cfg.root) / m.repo).resolve()
        for name, pc in (getattr(cfg, "projects", None) or {}).items():
            p = Path(pc.path)
            if (p if p.is_absolute() else Path(cfg.root) / p).resolve() == target:
                return name
    except (OSError, ValueError):
        pass
    return ""


def _today() -> str:
    return dt.date.today().isoformat()


def starts_today(factory: Factory, project: str) -> int:
    return int(((factory.state().get("project_starts") or {}).get(project) or {}).get(_today(), 0))


def record_start(factory: Factory, project: str) -> None:
    if not project:
        return
    today = _today()

    def fn(st: dict) -> None:
        ps = st.setdefault("project_starts", {}).setdefault(project, {})
        ps[today] = int(ps.get(today, 0)) + 1
        for day in sorted(ps)[:-14]:  # 直近 2 週間だけ残す
            ps.pop(day, None)

    factory._mutate_state(fn)


def select_runnable(factory: Factory, cfg, candidates: List[Mission]) -> Tuple[List[Mission], List[str]]:
    """依存とクォータを満たすものだけ選ぶ。戻り値 (実行するもの, 人間向けメッセージ)。

    メッセージは blocked (人間の対応が要る) と deferred (待てば進む) の両方を含み、
    deferred は "deferred:" で始まる (needs_human ではなく情報として扱う)。
    """
    chosen: List[Mission] = []
    notes: List[str] = []
    active_by_project: dict = {}
    for m in factory.list_missions():
        if m.status in ACTIVE:
            p = project_of(cfg, m)
            active_by_project[p] = active_by_project.get(p, 0) + 1
    started: dict = {}
    for m in candidates:
        st, why = deps_state(factory, m)
        if st == "blocked":
            notes.append(f"{m.id}: {why} — 前提を直すか `mission new` で依存を付け直してください")
            continue
        if st == "waiting":
            notes.append(f"deferred: {m.id}: {why}")
            continue
        p = project_of(cfg, m)
        pc = (getattr(cfg, "projects", None) or {}).get(p)
        daily = int(getattr(pc, "daily_mission_limit", 0) or 0) if pc else 0
        conc = int(getattr(pc, "max_concurrent", 0) or 0) if pc else 0
        if daily and starts_today(factory, p) + started.get(p, 0) >= daily:
            notes.append(f"deferred: {m.id}: {p} の 1 日の開始上限 ({daily}) に達しました")
            continue
        if conc and active_by_project.get(p, 0) + started.get(p, 0) >= conc:
            notes.append(f"deferred: {m.id}: {p} の同時実行上限 ({conc}) に達しました")
            continue
        started[p] = started.get(p, 0) + 1
        chosen.append(m)
    return chosen, notes


def deps_label(m: Mission) -> Optional[str]:
    return ("after " + ",".join(m.depends_on)) if m.depends_on else None
