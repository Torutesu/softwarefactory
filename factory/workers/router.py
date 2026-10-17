"""Worker Router: Claude Code (サブスク優先) ⇄ OpenCode (無料・代替) の切り替えとフォールバック。

- 用途別 (spec/holdout/dev/judge) の優先ワーカーを選び、予算超過や quota 系エラーで次へフォールバックする。
- 全員失敗なら AllWorkersFailed。
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional, Tuple

from ..config import Config, WorkerConfig
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
    def run(self, purpose: str, cwd: Path, prompt: str, timeout: Optional[int] = None) -> Tuple[str, WorkerResult]:
        cands = self.candidates(purpose)
        if not cands:
            raise AllWorkersFailed([WorkerResult(worker="(none)", ok=False, raw_tail="利用可能なワーカーがありません")])
        # 予算超過のワーカーは後ろへ (全員超過ならそのまま使う)
        under = [n for n in cands if not self.usage.budget_exceeded(n, self.cfg.workers.get(n, WorkerConfig()))]
        over = [n for n in cands if n not in under]
        order = under + over
        results = []
        for i, name in enumerate(order):
            w = self.worker_for(name)
            res = w.run(prompt, cwd, timeout)
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
