"""Loop Controller: 仕様 → ホールドアウト → 実装 → 検証 → 改善 のループを収束まで回す。"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Tuple

from . import gitx, spec_generator as specgen, holdout as holdout_mod, report as report_mod
from .config import Config
from .harness.judge import run_judge
from .harness.runner import HarnessResult, evaluate
from .state import Factory, Iteration, Mission, mission_cost_usd
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
    brief_spec: bool = False,
    no_change: bool = False,
) -> str:
    spec_text = load_text(factory.spec_file(m.id))
    build = load_json(factory.build_file(m.id)) or {}
    wt = str(factory.worktree(m.id).resolve())

    # 仕様を最初に置く (セッション内で接頭辞が安定 → プロンプトキャッシュが効く)。
    # 反復 2 以降で同一セッションを resume している場合、全文の再送は排除 (tokens/request レバー)。
    if brief_spec:
        spec_block = ("## 製品仕様\n"
                      "セッション継続中であり、仕様 (spec.md) は前回反復と同一です。再掲は省略します。\n"
                      "仕様の原文とその変更があれば作業ディレクトリ外のこちらを参照: "
                      f"{factory.spec_file(m.id)}\n")
    else:
        spec_block = f"## 製品仕様 (spec.md 全文)\n=== spec.md ===\n{spec_text}\n=== ここまで ===\n"

    fb = []
    if no_change:
        fb.append("### 警告: 前回反復で作業ツリーに変更がありませんでした\n"
                  "同じ状態のままもう一度反復すると、ループは noprogress として打ち切られ人間に返されます。"
                  "変更が不要・不可能と判断した場合は、その理由 (仕様の矛盾・スコープ外の変更が必要 等) を"
                  "最終メッセージの先頭に明記してください。")
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

    return f"""{spec_block}
## ミッション情報
- ミッション {m.id}: {m.title}
- Iteration {it_n} / 上限 {max_it}
- あなたは実装担当 Worker です。以下のディレクトリ (リポジトリ) 内でのみ作業してください:

    {wt}

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


def _effective_cfg(cfg: Config, m: Mission) -> Config:
    """ミッション単位のモデル上書きを適用した config のコピーを返す。"""
    if not m.model:
        return cfg
    import copy

    cfg2 = copy.deepcopy(cfg)
    for wc in cfg2.workers.values():
        wc.model = m.model
    return cfg2


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
    cfg = _effective_cfg(cfg or (Config.discover() or Config(root=str(factory.root))), m)
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

    # --- Isolation 衛生 -------------------------------------------------
    # worktree は空の checkout。.env 等の非追跡ファイルを複製しておく (Stage 2 の関門)
    seeded = gitx.seed_worktree_files(repo_path, wt, cfg.worktree_copy)
    if seeded:
        print(f"[loop] worktree に非追跡ファイルを複製: {', '.join(seeded[:5])}"
              + ("..." if len(seeded) > 5 else ""))

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
    if holdout_mod.unseal_if_stale(factory, m.id):
        print(f"[loop] {m.id}: 前回の中断で封印されたままの holdout を復旧しました")
    if not holdout_mod.scenario_files(factory, m.id):
        print("[loop] ホールドアウトシナリオを生成します (Holdout Generator)...")
        holdout_mod.generate(factory, m, router)
    # dev (ワーカーのコード) が一度も走っていない今の状態を正として指紋を記録する
    if holdout_mod.scenario_files(factory, m.id) and \
            holdout_mod.verify_manifest(factory, m.id) == "unrecorded":
        holdout_mod.record_manifest(factory, m.id)

    # --- Reviewability ゲート ---------------------------------------
    # loop-shaped の 3 条件 (repetitive / reviewable / valuable) のうち reviewable を機械判定:
    # テストもホールドアウトも無いと「通った」の意味が judge の自己申告だけになる
    if cfg.require_reviewable:
        build = load_json(factory.build_file(m.id)) or {}
        if not build.get("test") and not holdout_mod.scenario_files(factory, m.id):
            m.status = "blocked"
            m.halt_reason = "unreviewable"
            factory.save_mission(m)
            factory.event("mission_unreviewable", m.id)
            print(f"[loop] {m.id}: build.json に test も holdout シナリオも無いため dev に入りません "
                  "(test を設定するか holdout regen 後に再実行してください)")
            return m

    # --- Dev loop ------------------------------------------------------
    m.status = "dev"
    m.halt_reason = ""
    factory.save_mission(m)
    noprog_limit = max(0, int(cfg.noprogress_iterations or 0))
    stall = 0  # 実差分ゼロの反復が連続した回数
    iterations_log: List[Iteration] = []
    hr: Optional[HarnessResult] = None
    judge_data: Optional[dict] = None
    worker_tail = ""
    conclusion = "failed"
    next_signals: List[str] = []

    for n in range(1, max_it + 1):
        it = Iteration(n=n, worker="-", started_utc=now_utc_iso())
        # 反復 2 以降で同一セッションを継続している場合、spec 全文の再送を省く (tokens/request レバー)
        resume = None
        if n > 1 and m.session.get("worker") and m.session.get("session_id"):
            resume = (m.session["worker"], m.session["session_id"])
        prompt = build_dev_prompt(factory, m, n, max_it, hr, judge_data, worker_tail,
                                  brief_spec=(n > 1 and resume is not None), no_change=stall > 0)
        save_text(factory.mission_dir(m.id) / "prompts" / f"iter-{n}.md", prompt)

        before = gitx.tree_fingerprint(wt)
        try:
            with holdout_mod.sealed(factory, m.id):
                wname, res = router.run("dev", wt, prompt, resume=resume)
        except AllWorkersFailed as e:
            it.status = "failed"
            it.note = str(e)
            iterations_log.append(it)
            m.halt_reason = "all_workers_failed"
            print(f"[loop] 全ワーカー失敗: {e}")
            break
        it.changed = gitx.tree_fingerprint(wt) != before
        it.worker = wname
        it.seconds = res.seconds
        it.tokens_in = res.tokens_in
        it.tokens_out = res.tokens_out
        it.cost_usd = res.cost_usd
        worker_tail = tail(res.text, 1500)
        it.note = worker_tail
        print(f"[loop] iter {n}: worker={wname} ({res.seconds:.1f}s)")
        if res.session_id:
            m.session = {"worker": wname, "session_id": res.session_id}

        # ホールドアウトに到達した形跡があれば、この反復以降の検証は信頼できない
        leak = holdout_mod.detect_leak(factory, m.id, res.text, res.raw_tail)
        if leak:
            it.status = "failed"
            it.note = f"[holdout_leak] worker 出力に {leak!r} が出現\n" + it.note
            iterations_log.append(it)
            m.halt_reason = "holdout_leak"
            factory.event("holdout_leak_suspect", f"{m.id} iter={n} worker={wname}")
            print(f"[loop] {m.id}: worker 出力にホールドアウトのパスが出現 → 打ち切り (needs_human。"
                  "holdout regen でシナリオを作り直してから再実行)")
            break

        hr = evaluate(factory, m, wt, env_passthrough=cfg.harness_env_passthrough,
                      allow_record=False)  # dev 前に記録済み。無ければ消された疑い
        if hr.holdout_tampered:
            it.status = "failed"
            it.note = "[holdout_tamper] " + "; ".join(hr.warnings) + "\n" + it.note
            iterations_log.append(it)
            m.halt_reason = "holdout_tamper"
            factory.event("holdout_tamper_suspect", f"{m.id} iter={n} worker={wname}")
            print(f"[loop] {m.id}: holdout シナリオの改ざんを検知 → 打ち切り (needs_human。"
                  "holdout regen でシナリオを作り直してから再実行)")
            break
        # harness が落ちた反復は合格し得ない。judge を呼ぶと LLM 1 回分の枠を使うだけなので既定で省く
        # (次反復への指摘は build/test の tail と失敗シナリオ名で足りる)
        if hr.ok or cfg.judge_on_harness_fail:
            judge_data = run_judge(factory, m, hr, worker_tail, router)
        else:
            judge_data = None
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

        # halting の 3 つ目の保障: noprogress (反復上限・支出上限に加えて)
        stall = 0 if it.changed else stall + 1
        if noprog_limit and stall >= noprog_limit:
            m.halt_reason = "noprogress"
            factory.event("mission_noprogress", f"{m.id} iter={n} stall={stall}")
            print(f"[loop] {m.id}: {stall} 反復連続で作業ツリーに実差分なし → 打ち切り (needs_human)")
            break
    else:
        m.halt_reason = "max_iterations"  # break せずに抜けた = 上限まで未収束

    m.iterations = iterations_log
    m.status = "done" if conclusion == "done" else "failed"
    m.cost_usd = mission_cost_usd(m)  # 成果単価 (cost per mission) の元データ
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
