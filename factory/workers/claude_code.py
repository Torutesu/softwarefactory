"""Claude Code アダプタ (サブスク優先)。

例: claude -p "<prompt>" --output-format json --dangerously-skip-permissions
"""
from __future__ import annotations

import json
from typing import Optional

from ..util import ProcResult
from .base import Worker, WorkerResult


class ClaudeCodeWorker(Worker):
    name = "claude_code"

    def build_args(self, prompt: str, resume: Optional[str] = None) -> list:
        cmd = self.cfg.command or "claude"
        args = [cmd]
        if resume:
            # 同一 cwd (worktree) で作られたセッションのみ有効
            args += ["--resume", str(resume)]
        args += ["-p", prompt, "--output-format", "json"]
        # 非対話で全ツール許可 (社内専用の前提)
        args.append("--dangerously-skip-permissions")
        if self.cfg.model:
            args += ["--model", self.cfg.model]
        if self.cfg.allowed_tools:
            args += ["--allowedTools", ",".join(self.cfg.allowed_tools)]
        if self.cfg.disallowed_tools:
            args += ["--disallowedTools", ",".join(self.cfg.disallowed_tools)]
        max_turns = getattr(self.cfg, "max_turns", 0)
        if max_turns and int(max_turns) > 0:
            args += ["--max-turns", str(max_turns)]
        args += list(self.cfg.args or [])
        return args

    def parse_output(self, pr: ProcResult) -> WorkerResult:
        data = None
        if pr.out and pr.out.strip().startswith("{"):
            try:
                data = json.loads(pr.out)
            except json.JSONDecodeError:
                data = None
        if data is None and pr.out:
            # 出力末尾の JSON オブジェクトを探す (前置きログ対策)
            idx = pr.out.find("{")
            if idx >= 0:
                try:
                    data = json.loads(pr.out[idx:])
                except json.JSONDecodeError:
                    data = None
        res = WorkerResult(worker=self.name, ok=pr.code == 0)
        if data:
            res.text = str(data.get("result") or "")
            res.session_id = data.get("session_id")
            u = data.get("usage") or {}
            res.tokens_in = int(u.get("input_tokens") or 0) + int(u.get("cache_read_input_tokens") or 0) or None
            res.tokens_out = int(u.get("output_tokens") or 0) or None
            cost = data.get("total_cost_usd")
            res.cost_usd = float(cost) if isinstance(cost, (int, float)) else None
            if data.get("is_error"):
                res.ok = False
        else:
            res.text = (pr.out or "").strip()
        if pr.code != 0:
            res.raw_tail = (pr.err or pr.out)[-800:]
        return res
