"""Worker Router: Claude Code (サブスク優先) ⇄ OpenCode (無料・代替) の切り替えとフォールバック。

- 用途別 (spec/holdout/dev/judge) の優先ワーカーを選び、予算超過や quota 系エラーで次へフォールバックする。
- 予算は「日次」と「rolling window (例: 5h)」の二段で判定する。
- resume 指定時は同一ワーカーへの割当時にセッション継続する。
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional, Tuple

from ..config import WorkerConfig
from ..usage import Usage
from ..util import which
from .base import AllWorkersFailed, Worker, WorkerResult
from .claude_code import ClaudeCodeWorker
from .opencode import OpenCodeWorker

TYPE_MAP = {"claude_code": "claude", "claude": "claude", "opencode": "opencode", "mock": "mock"}


class Router:
    def __init__(self, cfg: Config, usage: Usage):
        self.cfg = cfg
        self.usage = usage
        self._cache = {}

    # ------------------------------------------------------------------
    def worker_for(self, name: str) -> Optional[Worker]:
        wc = self.cfg.workers.get(name) or WorkerConfig(type=TYPE_MAP.get(name, name))
        t = TYPE_MAP.get(wc.type, wc.type)
        if t == "claude":
            return ClaudeCodeWorker(wc)
        if t == "opencode":
            return OpenCodeWorker(wc)
        if t == "mock":
            from .mock import MockWorker

            return MockWorker(wc)
        return None

    def available_names(self) -> list:
        out = []
        for n in self.cfg.worker_order:
            w = self.worker_for(n)
            if w is None:
                continue
            if w.cfg.type != "mock" and not which(w.cfg.resolved_command()):
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
        names = set(self.available_names())
        return [n for n in dict.fromkeys(order) if n in names]

    # ------------------------------------------------------------------
    def run(self, purpose: str, cwd: Path, prompt: str, timeout: Optional[int] = None,
            resume: Optional[Tuple[str, str]] = None) -> Tuple[str, WorkerResult]:
        """resume: (worker名, session_id)。

        その worker の config.resume が有効で、候補の中にその worker が含まれるときだけ
        セッション継続する (F ルートへフォールバックした結果、相手が変わる場合は継続しない)。
        """
        cands = self.candidates(purpose)
        if not cands:
            raise AllWorkersFailed([WorkerResult(worker="(none)", ok=False, raw_tail="利用可能なワーカーがありません")])

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
