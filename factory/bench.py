"""社内 SWE ベンチ: ワーカー×タスクの cost/品質を計測し、purpose_workers 更新の根拠にする。

Uber 「Running a Software Factory Efficiently at Uber Scale」の
「ベンチ駆動のパレート最適モデル選択」相当。感覚で書いた purpose_workers を
実測 (完了率・スコア・cost) で眺め直せるようにする。

- タスクは .factory/bench/tasks/<id>.json: 自前の spec / build 設定 / holdout シナリオを持つ。
  全 variant で同一の出題 (spec と holdout は事前投入し、生成フェーズをスキップする)。
- variant = ワーカー。各 variant ごとにミッションを作り、dev 用途だけその variant に固定
  (loop.bench_lock_dev → Router がフォールバックを禁止) して run_mission を回す。
- judge は対象 worker と同型だが「全 variant で同一の器具」として通常ルーティングが使われる。
- 結果は .factory/bench/results/<ts>.json に保存し、markdown 表 + dev の推奨を出す。

注意: ベンチも本番と同じ実行パス (usage / worktree / commit) を通る。
実 CLI ワーカーでのベンチは枠とコストを本当に消す (--dry-run で計画を先に確認)。
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import List, Optional

from .config import Config
from .loop import run_mission
from .state import Factory, Mission, mission_cost_usd
from .usage import Usage
from .util import load_json, now_utc_iso, save_json
from .workers.router import Router


class BenchError(Exception):
    pass


def tasks_dir(factory: Factory) -> Path:
    return factory.state_dir / "bench" / "tasks"


def results_dir(factory: Factory) -> Path:
    return factory.state_dir / "bench" / "results"


# ---------------------------------------------------------------------------
# タスク
# ---------------------------------------------------------------------------
def load_tasks(factory: Factory, ids: Optional[List[str]] = None,
               validate: bool = True) -> List[dict]:
    """タスク JSON を読む。JSON 不正は BenchError。repo/spec/scenarios の検証は選択分のみ。"""
    d = tasks_dir(factory)
    tasks = []
    for p in sorted(d.glob("*.json")) if d.exists() else []:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise BenchError(f"bench タスク {p.name}: JSON不正 ({e})")
        data.setdefault("id", p.stem)
        tasks.append(data)
    if ids is not None:
        want = set(ids)
        missing = want - {t["id"] for t in tasks}
        if missing:
            raise BenchError(f"未知の bench タスク: {', '.join(sorted(missing))}")
        tasks = [t for t in tasks if t["id"] in want]
    if validate:
        for t in tasks:
            _validate_task(factory, t)
    return tasks


def _validate_task(factory: Factory, t: dict) -> None:
    tid = t.get("id") or "(id なし)"
    repo = t.get("repo") or ""
    if not repo:
        raise BenchError(f"bench タスク {tid}: repo がありません")
    repo_path = Path(repo)
    repo_path = repo_path if repo_path.is_absolute() else (factory.root / repo)
    if not repo_path.exists():
        raise BenchError(f"bench タスク {tid}: repo が存在しません: {repo_path}")
    if not (t.get("spec") or t.get("spec_file")):
        raise BenchError(f"bench タスク {tid}: spec か spec_file が必要 (全 variant で共通出題のため)")
    if t.get("spec_file") and not t.get("spec"):
        sp = Path(t["spec_file"])
        if not (sp.is_absolute() and sp.exists()) and not (factory.root / t["spec_file"]).exists():
            raise BenchError(f"bench タスク {tid}: spec_file が存在しません: {t['spec_file']}")
    scen = t.get("scenarios") or []
    if not scen:
        raise BenchError(f"bench タスク {tid}: scenarios がありません (検証の本体)")
    for sc in scen:
        if not sc.get("steps"):
            raise BenchError(f"bench タスク {tid}: シナリオ {sc.get('id')} に steps がありません")


def init_sample_tasks(factory: Factory) -> List[Path]:
    """サンプルタスク (mock/実 CLI の両方が答えられる小タスク) を書き出す。"""
    d = tasks_dir(factory)
    d.mkdir(parents=True, exist_ok=True)
    spec = (
        "# bench タスク: NOTES.md の作成\n\n"
        "作業ディレクトリ直下に `NOTES.md` を作成し、1 行目に正確に `bench demo notes`\n"
        "と書いてください。その他の変更はしないこと。\n\n"
        "(mock モード用ショートカット — 実 CLI で読んでも害はありません)\n\n"
        "```mock-action\n"
        '{"action": "write", "file": "NOTES.md", "content": "bench demo notes\\n"}\n'
        "```\n"
    )
    task = {
        "id": "demo-notes",
        "title": "[bench] NOTES.md 追記",
        "repo": "products/demo_app",
        "spec": spec,
        "build": {},
        "max_iterations": 1,
        "pass_score": 80,
        "scenarios": [
            {"id": "SC-001", "title": "NOTES.md が正確に書かれている", "weight": 1.0,
             "steps": [
                 {"name": "notes", "cmd": "test -f NOTES.md && grep -qx 'bench demo notes' NOTES.md",
                  "expect_exit": 0},
             ]},
        ],
    }
    p = d / "demo-notes.json"
    p.write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")
    return [p]


# ---------------------------------------------------------------------------
# 実行
# ---------------------------------------------------------------------------
def _pinned_cfg(cfg: Config, name: str) -> Config:
    """variant 用の config コピー: dev を該当ワーカーに固定し、フォールバックを禁じる。"""
    import copy

    p = copy.deepcopy(cfg)
    p.use_worker = name
    setattr(p, "bench_lock_dev", True)  # Router.candidates が dev を 1 候補に絞る
    return p


def _prep_mission(factory: Factory, t: dict, note: str) -> Mission:
    """タスクを 1 ミッションとして投入 (spec/build/holdout を事前書き込み)。"""
    m = factory.create_mission(
        title=t.get("title") or t["id"],
        kind="bench",
        repo=t["repo"],
        risk="low",
        priority=9,
        max_iterations=int(t.get("max_iterations") or 3),
        pass_score=int(t.get("pass_score") or 80),
        notes=note,
    )
    # --- spec (全 variant 共通) — テンプレのまま/空でなければ生成をスキップされる
    if t.get("spec"):
        spec_text = t["spec"]
    else:
        sp = Path(t["spec_file"])
        spec_text = (sp if sp.is_absolute() else factory.root / sp).read_text(encoding="utf-8")
    if not spec_text.strip():
        raise BenchError(f"bench タスク {t['id']}: spec が空です")
    factory.spec_file(m.id).write_text(spec_text, encoding="utf-8")

    # --- build 設定 (evaluate が読むものをそのまま)
    build = t.get("build") or {}
    factory.build_file(m.id).write_text(
        json.dumps(build, ensure_ascii=False, indent=2), encoding="utf-8")

    # --- holdout シナリオ (答えキー。実装側から見えないのは既存の隔離機構どおり)
    hdir = factory.holdout_dir_for(m.id)
    hdir.mkdir(parents=True, exist_ok=True)
    for i, sc in enumerate(t.get("scenarios") or [], 1):
        sc = dict(sc)
        sc.setdefault("id", f"SC-{i:03d}")
        sc.setdefault("title", sc["id"])
        sc.setdefault("weight", 1.0)
        (hdir / f"SC-{i:03d}.json").write_text(
            json.dumps(sc, ensure_ascii=False, indent=2), encoding="utf-8")
    return m


def _row(t: dict, worker: str, rep: int, mid: str, status: str, iters: int = 0,
         score: Optional[int] = None, tokens_in=None, tokens_out=None,
         cost=None, seconds: float = 0.0, note: str = "") -> dict:
    return {
        "task": t["id"], "worker": worker, "repeat": rep, "mission": mid,
        "status": status, "iters": iters, "score": score,
        "tokens_in": tokens_in, "tokens_out": tokens_out, "cost_usd": cost,
        "seconds": round(seconds, 1), "note": (note or "")[:160],
    }


def _row_from_mission(t: dict, worker: str, rep: int, m) -> dict:
    its = m.iterations
    sec = sum(i.seconds for i in its)
    ti = [i.tokens_in for i in its if i.tokens_in]
    to = [i.tokens_out for i in its if i.tokens_out]
    score = its[-1].score if its else None
    return _row(
        t, worker, rep, m.id, m.status, iters=len(its), score=score,
        tokens_in=sum(ti) if ti else None, tokens_out=sum(to) if to else None,
        cost=mission_cost_usd(m), seconds=sec,
        note=("" if m.status == "done" else (its[-1].note if its else "")),
    )


def run_bench(factory: Factory, cfg: Config, workers: Optional[List[str]] = None,
              task_ids: Optional[List[str]] = None, repeat: int = 1,
              max_iters: Optional[int] = None, dry_run: bool = False) -> dict:
    """ベンチを回す。結果 dict {ts, plan/rows/aggregate, skipped} を返す (CLI が描画)。"""
    tasks = load_tasks(factory, ids=task_ids)
    if not tasks:
        raise BenchError("bench タスクがありません。`factory bench --init` でサンプルを書き出せます。")
    usage = Usage(factory.state_dir)
    router = Router(cfg, usage)
    avail = set(router.available_names())
    if not workers:
        base_cands = router.candidates("dev")
        workers = [n for n in base_cands if n in avail]
    else:
        workers = list(dict.fromkeys(workers))
    workers = [w for w in workers if w in avail]
    if not workers:
        raise BenchError("利用可能なワーカーがありません (workers の指定と worker 状態を確認)")

    matrix = [(t, w, rep) for t in tasks for w in workers for rep in range(1, repeat + 1)]
    if dry_run:
        plan = [{"task": t["id"], "worker": w, "repeat": rep, "repo": t["repo"],
                 "max_iterations": max_iters_for(t, max_iters),
                 "pass_score": int(t.get("pass_score") or 80)}
                for t, w, rep in matrix]
        return {"ts": now_utc_iso(), "dry_run": True, "rows": [], "aggregate": [],
                "plan": plan, "skipped": []}

    rows: List[dict] = []
    skipped: List[dict] = []
    for t, w, rep in matrix:
        iter_cap = max_iters or int(t.get("max_iterations") or 3)
        mid = "-"
        try:
            m = _prep_mission(factory, t, note=f"bench: {w}")
            mid = m.id
            m2 = run_mission(factory, mid, confirm=True, iterations=iter_cap,
                             cfg=_pinned_cfg(cfg, w))
            rows.append(_row_from_mission(t, w, rep, m2))
        except Exception as e:  # run_mission/prepare の失敗も 1 行として記録する
            m2 = factory.get_mission(mid)
            if m2 and m2.iterations and m2.status in ("done", "failed"):
                rows.append(_row_from_mission(t, w, rep, m2))
            else:
                rows.append(_row(t, w, rep, mid, "error", note=str(e)))

    result = {
        "ts": now_utc_iso(),
        "bench": {"workers": workers, "tasks": [t["id"] for t in tasks], "repeat": repeat},
        "rows": rows,
        "aggregate": aggregate(rows),
        "skipped": skipped,
    }
    _save_result(factory, result)
    return result


def max_iters_for(t: dict, max_iters: Optional[int]) -> int:
    return max_iters or int(t.get("max_iterations") or 3)


def _save_result(factory: Factory, result: dict) -> Path:
    d = results_dir(factory)
    d.mkdir(parents=True, exist_ok=True)
    fname = dt.datetime.now().strftime("%Y%m%d-%H%M%S") + "-bench.json"
    p = d / fname
    save_json(p, result)
    return p


# ---------------------------------------------------------------------------
# 集計と描画
# ---------------------------------------------------------------------------
def aggregate(rows: List[dict]) -> List[dict]:
    agg = {}
    for r in rows:
        a = agg.setdefault(r["worker"], {
            "n": 0, "done": 0, "failed": 0, "error": 0, "iters": 0,
            "score_sum": 0, "score_ct": 0, "ti": 0, "to": 0,
            "cost": 0.0, "cost_ct": 0, "sec": 0.0,
        })
        a["n"] += 1
        if r["status"] == "done":
            a["done"] += 1
        elif r["status"] == "failed":
            a["failed"] += 1
        else:
            a["error"] += 1
        a["iters"] += int(r.get("iters") or 0)
        if r.get("score") is not None:
            a["score_sum"] += r["score"]
            a["score_ct"] += 1
        a["ti"] += r.get("tokens_in") or 0
        a["to"] += r.get("tokens_out") or 0
        if r.get("cost_usd"):
            a["cost"] += r["cost_usd"]
            a["cost_ct"] += 1
        a["sec"] += r.get("seconds") or 0.0
    out = []
    for w, a in agg.items():
        rate = a["done"] / a["n"] if a["n"] else 0.0
        out.append({
            "worker": w,
            "done": f"{a['done']}/{a['n']}",
            "done_rate": round(rate, 2),
            "failed": a["failed"],
            "error": a["error"],
            "avg_iters": round(a["iters"] / a["n"], 1) if a["n"] else 0,
            "avg_score": round(a["score_sum"] / a["score_ct"], 1) if a["score_ct"] else None,
            "tokens_in": a["ti"] or None,
            "tokens_out": a["to"] or None,
            "cost_usd": round(a["cost"], 2) if a["cost_ct"] else None,
            "seconds": round(a["sec"], 1),
        })
    # ランク: 完了率 → 平均スコア → cost
    out.sort(key=lambda a: (-a["done_rate"],
                            -(a["avg_score"] if a["avg_score"] is not None else -1),
                            a["cost_usd"] if a["cost_usd"] is not None else 0.0))
    for i, a in enumerate(out, 1):
        a["rank"] = i
    return out


def render_markdown(res: dict) -> str:
    lines: List[str] = [f"## ベンチ結果 ({res.get('ts')})", ""]

    if res.get("dry_run"):
        lines += ["(dry-run: 実行はしていません。`workers`/`max_iterations` を確認して再実行)", ""]
        lines += ["| # | タスク | worker | repeat | 反復上限 | pass_score | repo |",
                  "|---|---|---|---|---|---|---|"]
        for i, p in enumerate(res.get("plan") or [], 1):
            lines.append(f"| {i} | {p['task']} | {p['worker']} | {p['repeat']} | "
                         f"{p['max_iterations']} | {p['pass_score']} | {p['repo']} |")
        return "\n".join(lines)

    if res.get("skipped"):
        lines += ["### スキップ"]
        for s in res["skipped"]:
            lines.append(f"- {s['task']} × {s['worker']}: {s['reason']}")
        lines.append("")

    lines += ["| タスク | worker | 判定 | 反復 | score | tok_in | tok_out | cost | 秒 | ミッション |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for r in res.get("rows") or []:
        tok_in = r["tokens_in"] if r["tokens_in"] is not None else "-"
        tok_out = r["tokens_out"] if r["tokens_out"] is not None else "-"
        cost = f"${r['cost_usd']:.2f}" if r.get("cost_usd") is not None else "-"
        score = r.get("score") if r.get("score") is not None else "-"
        lines.append(f"| {r['task']} | {r['worker']} | {r['status']} | {r['iters']} | {score} | "
                     f"{tok_in} | {tok_out} | {cost} | {r['seconds']} | {r['mission']} |")
    lines.append("")

    lines += ["| 順位 | worker | 完了 | 失敗/error | 反復平均 | score平均 | tok_in合計 | tok_out合計 | cost | 秒合計 |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for a in res.get("aggregate") or []:
        cost = f"${a['cost_usd']:.2f}" if a["cost_usd"] is not None else "-"
        lines.append(f"| {a['rank']} | {a['worker']} | {a['done']} | {a['failed']}/{a['error']} | "
                     f"{a['avg_iters']} | {a['avg_score'] if a['avg_score'] is not None else '-'} | "
                     f"{a['tokens_in'] or '-'} | {a['tokens_out'] or '-'} | {cost} | {a['seconds']} |")
    lines.append("")

    agg = res.get("aggregate") or []
    if agg:
        best = agg[0]
        score_s = str(best["avg_score"]) if best["avg_score"] is not None else "-"
        cost_s = f"${best['cost_usd']:.2f}" if best["cost_usd"] is not None else "記録なし"
        lines += ["## 提案 (config への反映は手動で)", ""]
        lines.append(f"- `dev` 推奨: **{best['worker']}** — 完了 {best['done']} / "
                     f"score 平均 {score_s} / cost {cost_s}")
        lines.append("  - 完了率が同率のときは score 平均・cost で並べています。"
                     "cost はワーカーの自己報告値 (記録されないワーカーは usage 側で見る)")
        tail = agg[1:]
        if tail:
            others = ", ".join(f"`{a['worker']}`" for a in tail)
            lines.append(f"  - 次点以降: {others}")
        lines.append(f'  - 反映例: `"purpose_workers": {{"dev": ["{best["worker"]}"], ...}}`')
    return "\n".join(lines)


def history(factory: Factory, limit: int = 20) -> List[str]:
    """保存済み結果の要約 (新しい順)。"""
    d = results_dir(factory)
    if not d.exists():
        return ["結果なし。`factory bench` を実行すると .factory/bench/results/ に保存されます。"]
    out = []
    for p in sorted(d.glob("*-bench.json"), reverse=True)[:limit]:
        r = load_json(p) or {}
        agg = r.get("aggregate") or []
        best = agg[0] if agg else None
        rows = r.get("rows") or []
        done = sum(1 for x in rows if x.get("status") == "done")
        if best:
            out.append(f"{r.get('ts', p.stem)} — {done}/{len(rows)} done, "
                       f"先頭: {best['worker']} (完了 {best['done']}) → {p.name}")
        else:
            out.append(f"{r.get('ts', p.stem)} — 行なし ({p.name})")
    return out
