"""Worker 共通の結果型と基底クラス。"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..util import ProcResult, run_cmd

QUOTA_PATTERNS = [
    r"usage limit",
    r"rate.?limit",
    r"limit reached",
    r"quota",
    r"credit balance",
    r"billing",
    r"too many requests",
    r"overloaded",
    r"429",
]


@dataclass
class WorkerResult:
    worker: str
    ok: bool
    text: str = ""
    seconds: float = 0.0
    tokens_in: Optional[int] = None
    tokens_out: Optional[int] = None
    cost_usd: Optional[float] = None
    session_id: Optional[str] = None
    fail_kind: str = ""  # quota | timeout | unknown | ""
    raw_tail: str = ""

    @property
    def transient(self) -> bool:
        return self.fail_kind in ("quota", "timeout")


def classify_failure(stdout: str, stderr: str) -> str:
    blob = (stdout or "") + "\n" + (stderr or "")
    low = blob.lower()
    for p in QUOTA_PATTERNS:
        if re.search(p, low):
            return "quota"
    return "unknown"


class Worker:
    """非対話 CLI ワーカーの基底。サブクラスは build_args / parse_output を実装。"""

    name = "base"

    def __init__(self, cfg):
        self.cfg = cfg

    def build_args(self, prompt: str) -> list:
        raise NotImplementedError

    def parse_output(self, pr: ProcResult) -> WorkerResult:
        raise NotImplementedError

    # ------------------------------------------------------------------
    def run(self, prompt: str, cwd: Path, timeout: Optional[int] = None) -> WorkerResult:
        timeout = timeout or self.cfg.timeout
        args = self.build_args(prompt)
        pr = run_cmd(args, cwd=cwd, timeout=timeout)
        res = self.parse_output(pr)
        res.seconds = pr.seconds
        res.worker = self.name
        if pr.timed_out:
            res.ok = False
            res.fail_kind = "timeout"
            res.raw_tail = (pr.out or pr.err)[-800:]
        if not res.ok and not res.fail_kind:
            res.fail_kind = classify_failure(pr.out, pr.err)
        return res


class AllWorkersFailed(RuntimeError):
    def __init__(self, results: list):
        detail = "; ".join(
            f"{r.worker}:{r.fail_kind or 'unknown'}" for r in results
        )
        super().__init__("全ワーカーが失敗しました: " + detail)
        self.results = results
