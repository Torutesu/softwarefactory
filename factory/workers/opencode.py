"""OpenCode アダプタ (無料・代替)。

例: opencode run "<prompt>"
"""
from __future__ import annotations

from ..util import ProcResult
from .base import Worker, WorkerResult


class OpenCodeWorker(Worker):
    name = "opencode"

    def build_args(self, prompt: str) -> list:
        cmd = self.cfg.command or "opencode"
        args = [cmd, "run"]
        if self.cfg.model:
            args += ["--model", self.cfg.model]
        args += list(self.cfg.args or [])
        args.append(prompt)
        return args

    def parse_output(self, pr: ProcResult) -> WorkerResult:
        res = WorkerResult(worker=self.name, ok=pr.code == 0)
        res.text = (pr.out or "").strip()
        if pr.code != 0:
            res.raw_tail = (pr.err or pr.out)[-800:]
        return res
