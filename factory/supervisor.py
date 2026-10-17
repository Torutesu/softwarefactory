"""ファクトリークロック: 取込 → 実行 → 出荷 (merge) → カナリア → 修復 の一巡。

    factory tick [--dry-run]      # 1巡だけ回して終了 (cron  Friendly)
    factory supervisor [--interval N]   # tick を常駐で回し続ける

自律度 (config.autonomy_level):
  1 = 人間が queue したミッションだけ実行・報告 (既定)
  2 = + low リスクの完了ミッションを自動マージし、マージ後に自動カナリア
  3 = + 未割当の Seed (seeds/ に置かれたシグナル) からミッションを自動作成
  4 = + カナリア失敗時の修復ミッションを即実行 (完全自動運転)

エスカレーション (人間の介入) は「高リスクの競合・反復上限到達・カナリア 2 連敗・
予算枯渇・プロジェクト未割当の Seed」に限定し、state.json の events と最終出力に
`needs_human` として積む。
"""
from __future__ import annotations

import datetime as dt
import json
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from . import canary as canary_mod
from . import gitx
from .config import Config, WorkerConfig
from .loop import run_mission
from .state import Factory
from .usage import Usage
from .workers.router import Router


def _repo_path(factory: Factory, m) -> Path:
    return Path(m.repo).resolve() if Path(m.repo).is_absolute() else factory.root / m.repo


def _project_for(cfg: Config) -> str:
    """intake 用の既定プロジェクト。"""
    if cfg.autopilot_project:
        return cfg.autopilot_project
    if len(cfg.projects) == 1:
        return next(iter(cfg.projects))
    return ""


def _unclaimed_seeds(factory: Factory) -> list:
    try:
        dirs = sorted(p for p in factory.seeds_dir.iterdir() if p.is_dir())
    except OSError:
        return []
    st = factory.state()
    claimed = {m.get("seed") for m in (st.get("missions") or {}).values()}
    return [d.name for d in dirs if (d / "seed.md").exists() and d.name not in claimed]


# ---------------------------------------------------------------------------
# 各段
# ---------------------------------------------------------------------------
def step_intake(factory: Factory, cfg: Config, dry_run: bool) -> dict:
    """未割当 Seed → ミッション自動作成 (level>=3)。"""
    if cfg.autonomy_level < 3:
        return {"skipped": "autonomy_level < 3"}
    seeds = _unclaimed_seeds(factory)
    if not seeds:
        return {"created": []}
    project = _project_for(cfg)
    created = []
    escalations = []
    if not project:
        return {"created": [], "escalate": "intake_skip: autopilot_project/project 未確定"}
    for name in seeds:
        if dry_run:
            created.append(f"(dry) {name}")
            continue
        m = factory.create_mission(
            title=f"AUTO {name}",
            kind="update",
            seed=name,
            repo=cfg.projects[project].path,
            notes="auto-created by tick",
        )
        factory.event("tick_intake", f"{m.id} seed={name}")
        created.append(m.id)
    return {"created": created}


def step_execute(factory: Factory, cfg: Config, dry_run: bool) -> dict:
    """queued ミッションを実行 (並列上限 cfg.tick_concurrency)。高リスクは人間扱い。"""
    queued = [m for m in factory.list_missions() if m.status == "queued"]
    queued.sort(key=lambda m: (m.priority, m.id))
    if not queued:
        return {"ran": [], "escalate": []}
    usage = Usage(factory.state_dir)
    router = Router(cfg, usage)
    avail = router.available_names()
    if not avail:
        return {"ran": [], "escalate": ["no_worker_available"]}
    over = [n for n in avail if usage.budget_exceeded(n, cfg.workers.get(n, WorkerConfig()))
            or usage.window_budget_exceeded(n, cfg.workers.get(n, WorkerConfig()))]
    if over and len(over) == len(avail):
        return {"ran": [], "escalate": ["budget_exhausted: 全ワーカーが予算超過"]}

    runnable = [m for m in queued if m.risk != "high"]
    escalations = [f"{m.id}: high-risk は自動実行しません" for m in queued if m.risk == "high"]
    ran = []
    if dry_run:
        return {"ran": [f"(dry) {m.id}" for m in runnable], "escalate": escalations}

    def one(m) -> tuple:
        try:
            m2 = run_mission(factory, m.id, confirm=False, cfg=cfg)
            return (m.id, m2.status, None)
        except SystemExit as e:
            return (m.id, m.status, str(e))
        except Exception as e:  # noqa
            return (m.id, m.status, f"error: {e}")

    cap = max(1, int(getattr(cfg, "tick_concurrency", 2) or 1))
    if cap > 1 and len(runnable) > 1:
        with ThreadPoolExecutor(max_workers=min(cap, len(runnable))) as ex:
            futs = [ex.submit(one, m) for m in runnable]
            for fut in as_completed(futs):
                mid, status, err = fut.result()
                ran.append({"mission": mid, "status": status, "error": err})
                if err:
                    factory.event("tick_run_error", f"{mid}: {err[:300]}")
    else:
        for m in runnable:
            mid, status, err = one(m)
            ran.append({"mission": mid, "status": status, "error": err})
            if err:
                factory.event("tick_run_error", f"{mid}: {err[:300]}")

    escalations += [f"{r['mission']}: {r['status']}" for r in ran if r["status"] == "failed"]
    return {"ran": ran, "escalate": escalations}


def step_deliver(factory: Factory, cfg: Config, dry_run: bool) -> dict:
    """完了ミッションの自動出荷 (merge + canary)。level>=2。

    カナリア失敗→修復ミッション→出荷…の往復が無限に続かないよう、修復チェーン
    (`repair-of:<id>` のつながり) の深度が max_repair_rounds を超えたら手動扱いにする。
    """
    if cfg.autonomy_level < 2:
        return {"merged": [], "canary": [], "escalate": []}
    merged, canaries, escalations = [], [], []
    for m in factory.list_missions():
        if m.status != "done" or "merged to main" in (m.notes or ""):
            continue
        if m.risk != "low":
            escalations.append(f"{m.id}: high-risk のマージは人間に確認してください")
            continue
        if _repair_depth(factory, m) > _max_repairs(cfg):
            escalations.append(f"{m.id}: 修復ラウンド上限 ({_max_repairs(cfg)}) 到達 — 人間確認を推奨")
            continue
        if dry_run:
            merged.append(f"(dry) {m.id}")
            canaries.append(f"(dry) {m.id}")
            continue
        repo = _repo_path(factory, m)
        wt = factory.worktree(m.id)
        if gitx.status_porcelain(wt):
            escalations.append(f"{m.id}: worktree に未コミット変更がありマージ中断")
            continue
        r = gitx.merge_squash(repo, f"factory/{m.id}", f"factory: {m.id} {m.title}")
        if r.code != 0:
            escalations.append(f"{m.id}: マージ失敗 {(r.err or r.out)[-200:]}")
            continue
        gitx.remove_worktree(repo, wt)
        m.status = "done"
        m.notes = (m.notes + "\n" if m.notes else "") + "merged to main"
        factory.save_mission(m)
        factory.event("mission_merged", m.id)
        merged.append(m.id)

        cr = canary_mod.run_canary(factory, m.id, cfg=cfg)
        canaries.append({"mission": m.id, "status": cr["status"]})
        if cr["status"] == "fail":
            if cfg.autonomy_level >= 4:
                cr2 = canary_mod.run_canary(factory, m.id, cfg=cfg, fix=True,
                                            run_complete=True)
                canaries.append({"repaired_from": m.id, "result": cr2})
            else:
                escalations.append(f"{m.id}: カナリア失敗 (revert+再ミッションは --fix / level 4 で自動)")
    return {"merged": merged, "canary": canaries, "escalate": escalations}


def _max_repairs(cfg: Config) -> int:
    return max(0, int(getattr(cfg, "max_repair_rounds", 1) or 0))


def _repair_depth(factory: Factory, m) -> int:
    """notes に `repair-of:<id>` の連鎖 (自分→親) を辿り、修復世代の深度を返す。"""
    depth = 0
    cur = m
    seen = set()
    for _ in range(10):
        token = next((t[len("repair-of:"):] for t in (cur.notes or "").split()
                      if t.startswith("repair-of:")), None)
        if not token:
            return depth
        parent = factory.get_mission(token)
        if not parent or parent.id in seen:
            return depth
        depth += 1
        seen.add(cur.id)
        cur = parent
    return depth


# ---------------------------------------------------------------------------
def tick(factory: Factory, cfg: Config, dry_run: bool = False) -> dict:
    out = {"ts": dt.datetime.now().isoformat(timespec="seconds")}
    out["intake"] = step_intake(factory, cfg, dry_run)
    out["execute"] = step_execute(factory, cfg, dry_run)
    out["deliver"] = step_deliver(factory, cfg, dry_run)

    needs_human = (out["intake"].get("escalate") if isinstance(out["intake"].get("escalate"), list) else
                   [out["intake"]["escalate"]] if out["intake"].get("escalate") else [])
    needs_human += out["execute"].get("escalate") or []
    needs_human += out["deliver"].get("escalate") or []
    out["needs_human"] = needs_human
    print("--- tick summary", ("(dry-run)" if dry_run else ""), "---")
    print(json.dumps(out, ensure_ascii=False, indent=2))
    if dry_run:
        return out
    factory.event("tick", json.dumps(out, ensure_ascii=False)[:480])
    if getattr(cfg, "notify_url", ""):
        _notify(cfg.notify_url, out)
    return out


def _notify(url: str, payload: dict) -> None:
    try:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:  # noqa — 通知失敗は致命ではない
        print(f"[notify] 失敗: {e}")


def supervise(factory: Factory, cfg: Config, interval: int = 300) -> None:
    """tick を interval 秒ごとに回し続ける (Ctrl-C で停止)。"""
    print(f"[supervisor] 常駐開始: {interval} 秒ごとに tick (Ctrl-C で停止)")
    i = 0
    try:
        while True:
            i += 1
            try:
                tick(factory, cfg)
            except Exception as e:  # noqa — 1巡の失敗でクロックを止めない
                print(f"[supervisor] tick 失敗: {e}")
            time.sleep(interval)
    except KeyboardInterrupt:
        print(f"[supervisor] 停止 ({i} 巡実施)")
