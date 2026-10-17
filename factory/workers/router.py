"""Worker Router: Claude Code / Codex / OpenCode / OpenRouter / Mock の切替とフォールバック。

- 用途別 (spec/holdout/dev/judge) の優先ワーカーを選び、予算超過や quota 系エラーで次へフォールバックする。
- 予算は「日次」と「rolling window (例: 5h)」の二段で判定する。
- resume 指定時は同一ワーカーへの割当時にセッション継続する。
- 純粋な HTTP チャット系 (OpenRouter) はテキスト用途 (spec/holdout/judge) に限る。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, Tuple

from ..config import WorkerConfig
from ..usage import Usage
from ..util import which
from .base import AllWorkersFailed, Worker, WorkerResult
from .claude_code import ClaudeCodeWorker
from .codex import CodexWorker
from .opencode import OpenCodeWorker
from .openrouter import OpenRouterWorker

TYPE_MAP = {
    "claude_code": "claude",
    "claude": "claude",
    "codex": "codex",
    "opencode": "opencode",
    "openrouter": "openrouter",
    "mock": "mock",
}

# ワーカータイプが担える用途 (codex/claude/opencode はツール実行可=全用途)
PURPOSE_CAPS = {
    "claude": ("spec", "holdout", "dev", "judge"),
    "codex": ("spec", "holdout", "dev", "judge"),
    "opencode": ("spec", "holdout", "dev", "judge"),
    "mock": ("spec", "holdout", "dev", "judge"),
    "openrouter": ("spec", "holdout", "judge"),  # ツール不可なので実装 (dev) には使わない
}

ALL_PURPOSES = ("spec", "holdout", "dev", "judge")


class Router:
    def __init__(self, cfg, usage: Usage):
        self.cfg = cfg
        self.usage = usage

    # ------------------------------------------------------------------
    def worker_for(self, name: str) -> Optional[Worker]:
        wc = self.cfg.workers.get(name) or WorkerConfig(type=TYPE_MAP.get(name, name))
        t = TYPE_MAP.get(wc.type, wc.type)
        if t == "claude":
            return ClaudeCodeWorker(wc)
        if t == "codex":
            return CodexWorker(wc)
        if t == "opencode":
            return OpenCodeWorker(wc)
        if t == "openrouter":
            return OpenRouterWorker(wc)
        if t == "mock":
            from .mock import MockWorker

            return MockWorker(wc)
        return None

    def caps_for(self, name: str) -> tuple:
        w = self.worker_for(name)
        if w is None:
            return ()
        t = TYPE_MAP.get(w.cfg.type, w.cfg.type)
        return PURPOSE_CAPS.get(t, ALL_PURPOSES)

    def available_names(self) -> list:
        out = []
        for n in self.cfg.worker_order:
            w = self.worker_for(n)
            if w is None:
                continue
            t = TYPE_MAP.get(w.cfg.type, w.cfg.type)
            if t == "mock":
                out.append(n)  # 常時利用可
                continue
            if t == "openrouter":
                if os.environ.get("OPENROUTER_API_KEY", "").strip():
                    out.append(n)
                continue
            if not which(w.cfg.resolved_command()):
                continue  # CLI 未インストール → スキップ
            out.append(n)
        return out

    def candidates(self, purpose: str) -> list:
        order = list(self.cfg.worker_order)
        use = getattr(self.cfg, "use_worker", "auto")
        if use and use != "auto":
            order = [use] + [n for n in order if n != use]
        if purpose == "judge" and self.cfg.judge_worker and self.cfg.judge_worker in order:
            order = [self.cfg.judge_worker] + [n for n in order if n != self.cfg.judge_worker]
        avail = set(self.available_names())
        return [n for n in dict.fromkeys(order) if n in avail and purpose in self.caps_for(n)]

    # ------------------------------------------------------------------
    def run(self, purpose: str, cwd: Path, prompt: str, timeout: Optional[int] = None,
            resume: Optional[Tuple[str, str]] = None) -> Tuple[str, WorkerResult]:
        """resume: (worker名, session_id)。

        その worker の config.resume が有効で、候補の中にその worker が含まれるときだけ
        セッション継続する (フォールバックでワーカーが変わる場合は継続しない)。
        """
        cands = self.candidates(purpose)
        if not cands:
            raise AllWorkersFailed([WorkerResult(
                worker="(none)", ok=False,
                raw_tail="利用可能なワーカーがありません (用途=%s)" % purpose)])

        # 予算超過 (日次 or window) のワーカーは後ろへ (全員超過ならそのまま使う)
        def over(n: str) -> bool:
            wc = self.cfg.workers.get(n, WorkerConfig())
            return self.usage.budget_exceeded(n, wc) or self.usage.window_budget_exceeded(n, wc)

        under = [n for n in cands if not over(n)]
        order = under + [n for n in cands if n not in under]

        resume_worker = resume[0] if resume and getattr(
            self.cfg.workers.get(resume[0], WorkerConfig()), "resume", False) else None
        results = []
        for i, name in enumerate(order):
            w = self.worker_for(name)
            sid = resume[1] if (resume and name == resume_worker) else None
            res = w.run(prompt, cwd, timeout, resume=sid)
            self.usage.record(name, res.seconds, res.ok, purpose, res.tokens_in, res.tokens_out, res.cost_usd)
            if res.ok:
                return name, res
            results.append(res)
            # 残りの候補がなければ失敗で終了
            if i == len(order) - 1:
                break
            # quota/timeout/unknown いずれも次のワーカーへフォールバック
            print(f"[router] worker {name} failed ({res.fail_kind or 'unknown'}) -> 次のワーカーへ")
        raise AllWorkersFailed(results)
