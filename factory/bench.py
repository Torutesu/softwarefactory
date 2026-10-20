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
import time
from pathlib import Path
from typing import List, Optional, Tuple

from .config import Config
from .harness.judge import parse_judge_json
from .loop import run_mission
from .spec_generator import SPEC_TEMPLATE, spec_prompt
from .state import Factory, Iteration, Mission, mission_cost_usd
from .usage import Usage
from .util import load_json, load_text, now_utc_iso, save_json, save_text
from .workers.base import AllWorkersFailed
from .workers.router import Router


class BenchError(Exception):
    pass


def parse_variant(v: str) -> Tuple[str, str]:
    """variant 文字列を (worker名, model) に分ける。例: "claude_code@sonnet" → ("claude_code", "sonnet")"""
    name, _, model = v.partition("@")
    return name.strip(), model.strip()


def variant_label(name: str, model: str) -> str:
    return f"{name}@{model}" if model else name


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

    # purpose=spec モード: ワーカーに spec を書かせ、reference_spec との一致度を採点する
    if t.get("purpose") == "spec":
        seed = t.get("seed") or ""
        if not seed or not (factory.seeds_dir / seed).exists():
            raise BenchError(f"bench タスク {tid}: purpose=spec には seeds/ 配下に存在する seed 名が必要")
        if not (t.get("reference_spec") or t.get("reference_spec_file")):
            raise BenchError(f"bench タスク {tid}: purpose=spec には reference_spec が必要 (採点基準)")
        if t.get("reference_spec_file") and not t.get("reference_spec"):
            p = Path(t["reference_spec_file"])
            if not (p.is_absolute() and p.exists()) and not (factory.root / t["reference_spec_file"]).exists():
                raise BenchError(f"bench タスク {tid}: reference_spec_file が存在しません: {t['reference_spec_file']}")
        return

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
def _pinned_cfg(cfg: Config, name: str, model: str = "",
                lock_purpose: str = "dev") -> Config:
    """variant 用の config コピー: 対象用途を該当ワーカーに固定し、フォールバックを禁じる。

    model を指定した場合は全 worker 定義の model を差し替える
    (実 CLI ワーカーのモデル差 (= _effective_cfg の流儀) も比較対象にする)。
    lock_purpose で固定する用途を変えられる (dev 既定、purpose=spec の比較は "spec")。
    """
    import copy

    p = copy.deepcopy(cfg)
    p.use_worker = name
    if model:
        for wc in p.workers.values():
            wc.model = model
    if lock_purpose == "dev":
        setattr(p, "bench_lock_dev", True)
    setattr(p, "bench_lock_purpose", lock_purpose)
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
        "task": t["id"], "variant": worker, "purpose": t.get("purpose") or "dev",
        "repeat": rep, "mission": mid,
        "status": status, "iters": iters, "score": score,
        "tokens_in": tokens_in, "tokens_out": tokens_out, "cost_usd": cost,
        "seconds": round(seconds, 1), "note": (note or "")[:160],
    }


def _row_from_mission(t: dict, variant: str, rep: int, m) -> dict:
    its = m.iterations
    sec = sum(i.seconds for i in its)
    ti = [i.tokens_in for i in its if i.tokens_in]
    to = [i.tokens_out for i in its if i.tokens_out]
    score = its[-1].score if its else None
    return _row(
        t, variant, rep, m.id, m.status, iters=len(its), score=score,
        tokens_in=sum(ti) if ti else None, tokens_out=sum(to) if to else None,
        cost=mission_cost_usd(m), seconds=sec,
        note=("" if m.status == "done" else (its[-1].note if its else "")),
    )


def run_spec_reference_text(factory: Factory, t: dict) -> str:
    """タスクの reference_spec 本文を返す (holdout 隔離: 実装側からは見えない領域で管理)。"""
    if t.get("reference_spec"):
        return str(t["reference_spec"])
    p = Path(t["reference_spec_file"])
    p = p if p.is_absolute() else (factory.root / p)
    return p.read_text(encoding="utf-8")


def _prep_spec_mission(factory: Factory, t: dict, note: str) -> Mission:
    """purpose=spec 用ミッション (spec の事前書き込みはしない — 生成こそがベンチ対象)。

    reference_spec は holdout 領域 (コード外) に置き、実装/生成側から見えないようにする。
    """
    m = factory.create_mission(
        title=t.get("title") or t["id"],
        kind="bench",
        repo=t["repo"],
        risk="low",
        priority=9,
        max_iterations=1,
        pass_score=int(t.get("pass_score") or 80),
        notes=note,
    )
    hdir = factory.holdout_dir_for(m.id)
    hdir.mkdir(parents=True, exist_ok=True)
    hdir.joinpath("reference_spec.md").write_text(
        run_spec_reference_text(factory, t), encoding="utf-8")
    return m


SPEC_JUDGE_PROMPT = """以下の2つの仕様書を比較し、**生成された仕様**が**参照仕様**をどれだけ
実現できているかを 0〜100 で採点してください。

評価観点:
- カバレッジ: 参照仕様の機能要件・制約・検証方針が欠落なく書かれているか
- 実現可能性: 曖昧/矛盾がなく、実装 worker がそのまま着手できる具体度か
- ハンドオフ: 「触ってよい範囲 (scope)」「エスカレーション条件」まで書かれているか

出力は JSON 単体 (前後に説明文を付けない): {{"score": 0, "verdict": "pass|revise", "comments": "300字以内"}}

## 参照仕様

{reference}

## 生成された仕様

{generated}
"""


def score_spec(factory: Factory, cfg: Config, usage: Usage, reference: str,
               generated: str) -> dict:
    """生成 spec を参照 spec と比較して採点 (judge は全 variant 共通の器具)。"""
    prompt = SPEC_JUDGE_PROMPT.format(reference=reference[:6000], generated=generated[:6000])
    md = factory.state_dir  # テキスト用途なので cwd は任意
    try:
        _, res = _router_for(cfg, usage).run("judge", md, prompt, timeout=600)
    except AllWorkersFailed as e:
        return {"score": None, "verdict": "revise", "comments": f"judge 不可: {e}", "raw": ""}
    data = parse_judge_json(res.text) or {}
    try:
        data["score"] = int(data.get("score") or 0)
    except (TypeError, ValueError):
        data["score"] = 0
    data["verdict"] = str(data.get("verdict", "revise"))
    data["comments"] = str(data.get("comments") or "")[:600]
    data["tokens_in"] = res.tokens_in
    data["tokens_out"] = res.tokens_out
    data["cost_usd"] = res.cost_usd
    data["seconds"] = res.seconds
    return data


def _run_spec_variant(factory: Factory, cfg: Config, usage: Usage, t: dict,
                      name: str, model: str, rep: int) -> dict:
    """purpose=spec タスクを 1 variant 実行: spec 生成 → 参照 spec との照合採点。

    worktree もコミットも不要 (テキスト生成の品質比較) のため、run_mission を通さない。
    生成物は missions/<id>/spec.md に残し、ミッション自体も採点結果で締める。
    """
    label = variant_label(name, model)
    try:
        m = _prep_spec_mission(factory, t, note=f"bench: {label}")
    except Exception as e:  # noqa
        return _row(t, label, rep, "-", "error", note=str(e))
    md = factory.mission_dir(m.id)
    p = _pinned_cfg(cfg, name, model, lock_purpose="spec")
    prompt = spec_prompt(factory, m, factory.seeds_dir / (t.get("seed") or ""))
    try:
        _, res = _router_for(p, usage).run("spec", md, prompt, timeout=900)
    except BenchError as e:
        return _row(t, label, rep, m.id, "error", note=str(e))
    except Exception as e:  # noqa — 生成失敗も 1 行として記録する
        return _row(t, label, rep, m.id, "failed", note=f"spec 生成失敗: {e}")
    if not res.ok or not (res.text or "").strip():
        m.status = "failed"
        factory.save_mission(m)
        return _row(t, label, rep, m.id, "failed", seconds=res.seconds,
                    tokens_in=res.tokens_in, tokens_out=res.tokens_out, cost=res.cost_usd,
                    note=(res.raw_tail or "spec 生成が空")[:160])

    save_text(md / "spec.md", res.text)
    m.status = "spec"
    factory.save_mission(m)

    threshold = int(t.get("pass_score") or 80)
    j = score_spec(factory, cfg, usage, run_spec_reference_text(factory, t), res.text)
    score = j.get("score")
    passed = isinstance(score, int) and score >= threshold
    m.status = "done" if passed else "failed"
    m.notes = (m.notes + "\n" if m.notes else "") + (
        f"bench score={score} threshold={threshold} judge={j.get('verdict')} "
        f"comments={j.get('comments', '')[:120]}")
    factory.save_mission(m)
    factory.event("bench_spec_scored", f"{m.id} score={score}")
    return _row(
        t, label, rep, m.id, "done" if passed else "failed",
        score=score if isinstance(score, int) else 0,
        tokens_in=res.tokens_in, tokens_out=res.tokens_out, cost=res.cost_usd,
        seconds=res.seconds, note=(j.get("comments") or "")[:160])

def _router_for(cfg: Config, usage: Usage) -> Router:
    """Router 生成の共通窓 (テストでの置き換えポイント)。"""
    return Router(cfg, usage)


def run_bench(factory: Factory, cfg: Config, workers: Optional[List[str]] = None,
              task_ids: Optional[List[str]] = None, repeat: int = 1,
              max_iters: Optional[int] = None, dry_run: bool = False) -> dict:
    """ベンチを回す。結果 dict {ts, plan/rows/aggregate, skipped} を返す (CLI が描画)。

    workers は "名前" か "名前@model" (例: claude_code@sonnet) を許す。
    タスクの purpose で実行経路が分かれる:
    - purpose=dev (既定): spec/holdout を事前投入し run_mission (worktree/commit 実パス)
    - purpose=spec     : seed から spec を生成させ、参照 spec との照合採点で比較する
    """
    tasks = load_tasks(factory, ids=task_ids)
    if not tasks:
        raise BenchError("bench タスクがありません。`factory bench --init` でサンプルを書き出せます。")
    usage = Usage(factory.state_dir)
    router_probe = _router_for(cfg, usage)
    avail = set(router_probe.available_names())
    if not workers:
        variants = [(w, "") for w in router_probe.candidates("dev") if w in avail]
    else:
        variants = []
        for v in dict.fromkeys(w.strip() for w in workers if w.strip()):
            name, model = parse_variant(v)
            if name in avail:
                variants.append((name, model))
    if not variants:
        raise BenchError("利用可能なワーカーがありません (workers の指定と worker 状態を確認)")

    matrix = [(t, v, rep) for t in tasks for v in variants for rep in range(1, repeat + 1)]
    if dry_run:
        plan = [{"task": t["id"], "variant": variant_label(*v),
                 "purpose": t.get("purpose") or "dev", "repeat": rep, "repo": t["repo"],
                 "max_iterations": max_iters_for(t, max_iters),
                 "pass_score": int(t.get("pass_score") or 80)}
                for t, v, rep in matrix]
        return {"ts": now_utc_iso(), "dry_run": True, "rows": [], "aggregate": [],
                "plan": plan, "skipped": []}

    rows: List[dict] = []
    skipped: List[dict] = []
    for t, v, rep in matrix:
        name, model = v
        label = variant_label(name, model)
        if t.get("purpose") == "spec":
            rows.append(_run_spec_variant(factory, cfg, usage, t, name, model, rep))
            continue
        iter_cap = max_iters or int(t.get("max_iterations") or 3)
        mid = "-"
        try:
            m = _prep_mission(factory, t, note=f"bench: {label}")
            mid = m.id
            m2 = run_mission(factory, mid, confirm=True, iterations=iter_cap,
                             cfg=_pinned_cfg(cfg, name, model))
            rows.append(_row_from_mission(t, label, rep, m2))
        except Exception as e:  # run_mission/prepare の失敗も 1 行として記録する
            m2 = factory.get_mission(mid)
            if m2 and m2.iterations and m2.status in ("done", "failed"):
                rows.append(_row_from_mission(t, label, rep, m2))
            else:
                rows.append(_row(t, label, rep, mid, "error", note=str(e)))

    result = {
        "ts": now_utc_iso(),
        "bench": {"variants": [variant_label(*v) for v in variants],
                  "tasks": [t["id"] for t in tasks], "repeat": repeat},
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
    """(purpose, variant) ごとに集計し、purpose 内でランク付けした行を返す。"""
    groups = {}
    for r in rows:
        key = (r.get("purpose") or "dev", r.get("variant") or r.get("worker") or "")
        a = groups.setdefault(key, {
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
    for (purpose, variant), a in groups.items():
        rate = a["done"] / a["n"] if a["n"] else 0.0
        out.append({
            "purpose": purpose,
            "variant": variant,
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
    # purpose 内でランク: 完了率 → 平均スコア → cost
    out.sort(key=lambda a: (a["purpose"], -a["done_rate"],
                            -(a["avg_score"] if a["avg_score"] is not None else -1),
                            a["cost_usd"] if a["cost_usd"] is not None else 0.0))
    rank = {}
    for a in out:
        rank[a["purpose"]] = rank.get(a["purpose"], 0) + 1
        a["rank"] = rank[a["purpose"]]
    return out


def render_markdown(res: dict) -> str:
    lines: List[str] = [f"## ベンチ結果 ({res.get('ts')})", ""]

    if res.get("dry_run"):
        lines += ["(dry-run: 実行はしていません。`workers`/`max_iterations` を確認して再実行)", ""]
        lines += ["| # | タスク | variant | 目的 | repeat | 反復上限 | pass_score | repo |",
                  "|---|---|---|---|---|---|---|---|"]
        for i, p in enumerate(res.get("plan") or [], 1):
            lines.append(f"| {i} | {p['task']} | {p['variant']} | {p.get('purpose', 'dev')} | {p['repeat']} | "
                         f"{p['max_iterations']} | {p['pass_score']} | {p['repo']} |")
        return "\n".join(lines)

    if res.get("skipped"):
        lines += ["### スキップ"]
        for s in res["skipped"]:
            lines.append(f"- {s['task']} × {s['worker']}: {s['reason']}")
        lines.append("")

    lines += ["| タスク | variant | 目的 | 判定 | 反復 | score | tok_in | tok_out | cost | 秒 | ミッション |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in res.get("rows") or []:
        tok_in = r["tokens_in"] if r["tokens_in"] is not None else "-"
        tok_out = r["tokens_out"] if r["tokens_out"] is not None else "-"
        cost = f"${r['cost_usd']:.2f}" if r.get("cost_usd") is not None else "-"
        score = r.get("score") if r.get("score") is not None else "-"
        lines.append(f"| {r['task']} | {r.get('variant') or r.get('worker')} | {r.get('purpose', 'dev')} | "
                     f"{r['status']} | {r['iters']} | {score} | "
                     f"{tok_in} | {tok_out} | {cost} | {r['seconds']} | {r['mission']} |")
    lines.append("")

    lines += ["| 目的 | 順位 | variant | 完了 | 失敗/error | 反復平均 | score平均 | tok_in合計 | tok_out合計 | cost | 秒合計 |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
    for a in res.get("aggregate") or []:
        cost = f"${a['cost_usd']:.2f}" if a["cost_usd"] is not None else "-"
        lines.append(f"| {a['purpose']} | {a['rank']} | {a['variant']} | {a['done']} | {a['failed']}/{a['error']} | "
                     f"{a['avg_iters']} | {a['avg_score'] if a['avg_score'] is not None else '-'} | "
                     f"{a['tokens_in'] or '-'} | {a['tokens_out'] or '-'} | {cost} | {a['seconds']} |")
    lines.append("")

    # 提案は purpose ごと (dev → purpose_workers.dev / spec → purpose_workers.spec)
    by_purpose = {}
    for a in res.get("aggregate") or []:
        by_purpose.setdefault(a["purpose"], []).append(a)
    if by_purpose:
        lines += ["## 提案 (config への反映は手動で)", ""]
        for pu, list_ in by_purpose.items():
            best = list_[0]
            score_s = str(best["avg_score"]) if best["avg_score"] is not None else "-"
            cost_s = f"${best['cost_usd']:.2f}" if best["cost_usd"] is not None else "記録なし"
            lines.append(f"- `{pu}` 推奨: **{best['variant']}** — 完了 {best['done']} / "
                         f"score 平均 {score_s} / cost {cost_s}")
            tail = list_[1:]
            if tail:
                others = ", ".join(f"`{a['variant']}`" for a in tail)
                lines.append(f"  - 次点以降: {others}")
            lines.append(f'  - 反映例: `"purpose_workers": {{"{pu}": ["{best["variant"]}"], ...}}`')
        lines.append("  - 完了率が同率のときは score 平均・cost で並べています。"
                     "cost はワーカーの自己報告値 (記録されないワーカーは usage 側で見る)")
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
                       f"先頭: {best.get('variant') or best.get('worker')} ({best['done']}) → {p.name}")
        else:
            out.append(f"{r.get('ts', p.stem)} — 行なし ({p.name})")
    return out
