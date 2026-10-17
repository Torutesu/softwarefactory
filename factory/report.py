"""レポート生成: 各反復・最終結果の Markdown を書く。"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from .harness.runner import HarnessResult
from .state import Factory, Iteration, Mission
from .util import save_text


def iter_report(
    factory: Factory,
    m: Mission,
    it: Iteration,
    hr: HarnessResult,
    judge_data: Optional[dict],
    run_dir: Path,
) -> str:
    lines = [f"# Iteration {it.n} — {m.id}: {m.title}", ""]
    lines.append(f"- 実行: {it.started_utc} / worker: `{it.worker}` / {it.seconds:.1f}s")
    lines.append("")

    def step_block(title, sr):
        if not sr:
            return
        lines.append(f"## {title}: {'PASS' if sr.ok else 'FAIL'} (`{sr.cmd}`)")
        if not sr.ok:
            lines.append("```")
            lines.append(sr.tail[-1000:])
            lines.append("```")
        lines.append("")

    step_block("Build", hr.build)
    step_block("Test", hr.test)

    if hr.warnings:
        lines.append("## 注意")
        for w in hr.warnings:
            lines.append(f"- {w}")
        lines.append("")

    lines.append("## ホールドアウトシナリオ (コード外)")
    lines.append("| ID | 判定 | 時間 | タイトル |")
    lines.append("|---|---|---|---|")
    for s in hr.scenarios:
        lines.append(f"| {s.id} | {'✅ PASS' if s.ok else '❌ FAIL'} | {s.seconds:.1f}s | {s.title} |")
    lines.append("")
    failed_steps = []
    for s in hr.scenarios:
        for st in s.steps:
            if not st.ok:
                failed_steps.append((s.id, st.name, st.cmd, st.tail))
    if failed_steps:
        lines.append("### 失敗ステップの詳細")
        for sid, name, cmd, tl in failed_steps[:8]:
            lines.append(f"- **{sid}/{name}**: `{cmd[:120]}`")
            lines.append("```")
            lines.append(tl[-600:])
            lines.append("```")
        lines.append("")

    if judge_data:
        lines.append("## LLM-as-Judge")
        lines.append(f"- score: **{judge_data.get('score')}** / verdict: **{judge_data.get('verdict')}**")
        lines.append(f"- comments: {judge_data.get('comments','')}")
        for p in judge_data.get("priorities") or []:
            lines.append(f"- (提案) {p}")
        lines.append("")

    if it.note:
        lines.append("## ワーカー要約")
        lines.append("```")
        lines.append(it.note[-1500:])
        lines.append("```")

    return "\n".join(lines)


def final_report(
    factory: Factory,
    m: Mission,
    iterations: List[Iteration],
    conclusion: str,
    run_dir: Path,
    next_signals: List[str],
) -> str:
    lines = [f"# Mission {m.id}: {m.title}", ""]
    lines.append(f"- 結論: **{conclusion}**")
    lines.append(f"- 反復数: {len(iterations)} / 上限 {m.max_iterations}")
    scores = [i.score for i in iterations if i.score is not None]
    lines.append(f"- 最終スコア: {scores[-1] if scores else '-'} (閾値 {m.pass_score})")
    lines.append("")
    lines.append("## 反復の推移")
    lines.append("| # | worker | 判定 | score |")
    lines.append("|---|---|---|---|")
    for it in iterations:
        lines.append(f"| {it.n} | {it.worker} | {it.status} | {it.score if it.score is not None else '-'} |")
    lines.append("")
    wt = factory.worktree(m.id)
    if wt.exists():
        from . import gitx

        lg = gitx.log(wt, n=10, fmt="%h %s")
        if lg:
            lines.append("## コミット (worktree)")
            lines.append("```")
            lines.append(lg)
            lines.append("```")
            lines.append("")
    if next_signals:
        lines.append("## 次の改善シグナル候補")
        for s in next_signals:
            lines.append(f"- {s}")
        lines.append("")
    lines.append(f"成果物: worktree `{wt}` (ブランチ `factory/{m.id}`)")
    lines.append("")
    lines.append("マージ: `factory merge " + m.id + "` (確認後に main へ squash マージ)")
    return "\n".join(lines)
