"""Loop Controller: 仕様 → ホールドアウト → 実装 → 検証 → 改善 のループを収束まで回す。"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Tuple

from . import gitx, spec_generator as specgen, holdout as holdout_mod, report as report_mod
from .config import Config
from .harness.judge import run_judge
from .harness.runner import HarnessResult, evaluate
from .state import Factory, Iteration, Mission
from .util import load_json, load_text, now_utc_iso, save_text, tail
from .usage import Usage
from .workers.base import AllWorkersFailed
from .workers.router import Router


def build_dev_prompt(
    factory: Factory,
    m: Mission,
    it_n: int,
    max_it: int,
    hr: Optional[HarnessResult],
    judge_data: Optional[dict],
    worker_tail: str,
) -> str:
    spec_text = load_text(factory.spec_file(m.id))
    build = load_json(factory.build_file(m.id)) or {}
    wt = str(factory.worktree(m.id).resolve())

    fb = []
    if worker_tail:
        fb.append("### 前回のあなたの要約\n" + worker_tail)
    if hr:
        if hr.build and not hr.build.ok:
            fb.append("### Build 失敗\n```\n" + hr.build.tail[-1200:] + "\n```")
        if hr.test:
            if not hr.test.ok:
                fb.append("### Test 失敗\n```\n" + hr.test.tail[-1600:] + "\n```")
        bad = [s for s in hr.scenarios if not s.ok]
        if bad:
            names = ", ".join(f"{s.id}:{s.title}" for s in bad)
            fb.append(f"### 失敗したホールドアウトシナリオ (詳細は非公開): {names}")
        elif hr.scenarios:
            fb.append("### ホールドアウトシナリオ: 全パス")
    if judge_data:
        fb.append("### ジャッジ判定 (score %s, verdict %s)\n%s" % (
            judge_data.get("score"), judge_data.get("verdict"), judge_data.get("comments", "")))
        for p in judge_data.get("priorities") or []:
            fb.append(f"- 提案: {p}")

    failures_block = "\n\n".join(fb) if fb else "(初回実行)"

    return f"""[Software Factory] 実装ミッション {m.id}: {m.title}
Iteration {it_n} / 上限 {max_it}

あなたは実装担当 Worker です。以下のディレクトリ (リポジトリ) 内でのみ作業してください:

    {wt}

## 製品仕様 (spec.md 全文)
=== spec.md ===
{spec_text}
=== ここまで ===

## 検証コマンド (検証ハーネスが実行するもの)
- build: `{build.get('build') or '(なし)'}`
- test: `{build.get('test') or '(なし)'}`
- run (サーバ起動, $FACTORY_PORT で待ち受け): `{build.get('run') or '(なし)'}`

## 直近の検証結果と指摘
{failures_block}

## ルール
1. 上記ディレクトリ外のファイルを読み書きしない (holdout/ や .factory/ は触らない)。
2. 実装を完了したら build → test を自分で実行し、失敗したら原因を直して再実行すること。
3. 変更は git commit すること (conventional commit: feat/fix/chore ...)。
4. テストを通すための偽装 (仕様を満たさない近道) をしないこと。
5. 最終メッセージは 5〜10 行: 完了項目 / 未解決の論点 / 次の改善提案。
"""


def _pass_criteria(m: Mission, hr: HarnessResult, judge_data: Optional[dict]) -> Tuple[bool, int]:
    final_score = judge_data["score"] if judge_data and "score" in judge_data else hr.base_score
    verdict_pass = judge_data["verdict"] == "pass" if judge_data else True
    passed = hr.ok and verdict_pass and final_score >= m.pass_score
    return passed, int(final_score)


def run_mission(factory: Factory, mid: str, confirm: bool = False, iterations: Optional[int] = None, cfg: Optional[Config] = None) -> Mission:
    m = factory.get_mission(mid)
    if not m:
        raise SystemExit(f"mission not found: {mid}")
    if m.status == "done":
        print(f"[skip] {mid} は既に done")
        return m
    if m.risk == "high" and not confirm:
        raise SystemExit(f"高リスクミッション ({mid}) は --confirm を付けて実行してください")

    with factory.lock(mid):
        return _run_locked(factory, m, iterations, cfg)


def _run_locked(factory: Factory, m: Mission, iterations: Optional[int], cfg: Optional[Config]) -> Mission:
    cfg = cfg or (Config.discover() or Config(root=str(factory.root)))
    usage = Usage(factory.state_dir)
    router = Router(cfg, usage)
    max_it = iterations or m.max_iterations

    # --- リポジトリ準備 -------------------------------------------------
    repo_path = Path(m.repo).resolve() if Path(m.repo).is_absolute() else factory.root / m.repo
    if m.kind == "greenfield" and not repo_path.exists():
        repo_path.mkdir(parents=True)
    gitx.ensure_repo(repo_path)
    gitx.ensure_baseline_commit(repo_path)
    wt = gitx.create_worktree(repo_path, factory.worktree(m.id), f"factory/{m.id}")

    run_dir = factory.run_dir(m.id)

    # --- Spec ----------------------------------------------------------
    template = specgen.SPEC_TEMPLATE.format(title=m.title)
    current = load_text(factory.spec_file(m.id))
    if current.strip() == template.strip() or current.strip() == "":
        print("[loop] 仕様を生成します (Spec Generator)...")
        if not specgen.generate(factory, m, router):
            m.status = "failed"
            factory.save_mission(m)
            return m
        if load_text(factory.spec_file(m.id)).strip() == template.strip():
            # テンプレートのまま → 生成失敗扱い
            m.status = "failed"
            factory.save_mission(m)
            factory.event("spec_unfilled", m.id)
            return m

    # --- Holdout -------------------------------------------------------
    if not holdout_mod.scenario_files(factory, m.id):
        print("[loop] ホールドアウトシナリオを生成します (Holdout Generator)...")
        holdout_mod.generate(factory, m, router)

    # --- Dev loop ------------------------------------------------------
    m.status = "dev"
    factory.save_mission(m)
    iterations_log: List[Iteration] = []
    hr: Optional[HarnessResult] = None
    judge_data: Optional[dict] = None
    worker_tail = ""
    conclusion = "failed"
    next_signals: List[str] = []

    for n in range(1, max_it + 1):
        it = Iteration(n=n, worker="-", started_utc=now_utc_iso())
        prompt = build_dev_prompt(factory, m, n, max_it, hr, judge_data, worker_tail)
        save_text(factory.mission_dir(m.id) / "prompts" / f"iter-{n}.md", prompt)

        try:
            wname, res = router.run("dev", wt, prompt)
        except AllWorkersFailed as e:
            it.status = "failed"
            it.note = str(e)
            iterations_log.append(it)
            print(f"[loop] 全ワーカー失敗: {e}")
            break
        it.worker = wname
        it.seconds = res.seconds
        worker_tail = tail(res.text, 1500)
        it.note = worker_tail
        print(f"[loop] iter {n}: worker={wname} ({res.seconds:.1f}s)")

        hr = evaluate(factory, m, wt)
        judge_data = run_judge(factory, m, hr, worker_tail, router)
        passed, score = _pass_criteria(m, hr, judge_data)
        it.status = "passed" if passed else "failed"
        it.score = score
        iterations_log.append(it)

        md = report_mod.iter_report(factory, m, it, hr, judge_data, run_dir)
        save_text(run_dir / f"iter-{n}.md", md)
        print(f"[loop] iter {n}: {'PASS' if passed else 'FAIL'} score={score} "
              f"(scenarios {sum(1 for s in hr.scenarios if s.ok)}/{len(hr.scenarios)})")

        if passed:
            conclusion = "done"
            if judge_data:
                next_signals = [p for p in (judge_data.get("priorities") or []) if isinstance(p, str)]
            break

    m.iterations = iterations_log
    m.status = "done" if conclusion == "done" else "failed"
    factory.save_mission(m)
    final_md = report_mod.final_report(factory, m, iterations_log, conclusion, run_dir, next_signals)
    save_text(factory.reports_dir(m.id) / "final.md", final_md)
    if next_signals:
        st = factory.state()
        st.setdefault("signals", []).append({"ts": now_utc_iso(), "source": "judge", "mission": m.id, "items": next_signals})
        factory.save_state(st)
    factory.event("mission_" + m.status, m.id)
    print(f"[loop] {m.id} -> {m.status}")
    return m
