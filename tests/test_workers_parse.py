"""各ワーカー CLI の出力パースと Router のフォールバック (実 CLI は呼ばない)。"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from factory.config import Config, WorkerConfig
from factory.usage import Usage
from factory.util import ProcResult
from factory.workers.base import AllWorkersFailed, WorkerResult
from factory.workers.claude_code import ClaudeCodeWorker
from factory.workers.codex import CodexWorker
from factory.workers.opencode import OpenCodeWorker
from factory.workers.router import Router


def pr(out="", err="", code=0):
    return ProcResult(code, out, err, 0.1)


class TestClaudeParse(unittest.TestCase):
    w = ClaudeCodeWorker(WorkerConfig(type="claude"))

    def test_json_result(self):
        out = json.dumps({"result": "done", "session_id": "s1", "total_cost_usd": 0.5,
                          "usage": {"input_tokens": 10, "cache_read_input_tokens": 5, "output_tokens": 3}})
        r = self.w.parse_output(pr(out))
        self.assertEqual((r.ok, r.text, r.session_id, r.tokens_in, r.tokens_out, r.cost_usd),
                         (True, "done", "s1", 15, 3, 0.5))

    def test_log_lines_before_json(self):
        r = self.w.parse_output(pr("warning: x\n" + json.dumps({"result": "ok"})))
        self.assertEqual(r.text, "ok")

    def test_is_error_flag(self):
        r = self.w.parse_output(pr(json.dumps({"result": "limit", "is_error": True})))
        self.assertFalse(r.ok)

    def test_garbage_and_nonzero(self):
        r = self.w.parse_output(pr("{not json", "boom", code=1))
        self.assertFalse(r.ok)
        self.assertIn("boom", r.raw_tail)

    def test_wrong_types_do_not_crash(self):
        r = self.w.parse_output(pr(json.dumps({"result": 3, "usage": {}, "total_cost_usd": "x"})))
        self.assertEqual(r.text, "3")
        self.assertIsNone(r.cost_usd)

    def test_args(self):
        w = ClaudeCodeWorker(WorkerConfig(type="claude", model="sonnet", max_turns=5,
                                          allowed_tools=["Read"], disallowed_tools=["WebSearch"]))
        a = w.build_args("p", resume="s1")
        for x in ("--resume", "s1", "--model", "sonnet", "--max-turns", "5", "--allowedTools", "--disallowedTools"):
            self.assertIn(x, a)
        self.assertNotIn("--max-turns", ClaudeCodeWorker(WorkerConfig(type="claude")).build_args("p"))


class TestCodexParse(unittest.TestCase):
    w = CodexWorker(WorkerConfig(type="codex"))

    def test_plain_text(self):
        r = self.w.parse_output(pr("all good"))
        self.assertEqual((r.text, r.session_id), ("all good", None))

    def test_jsonl_events(self):
        out = "\n".join([json.dumps({"session_id": "c1"}), "noise",
                         json.dumps({"msg": {"text": "hello"}}), json.dumps({"text": "world"})])
        r = self.w.parse_output(pr(out))
        self.assertEqual(r.session_id, "c1")
        self.assertIn("hello", r.text)

    def test_failure(self):
        r = self.w.parse_output(pr("", "auth error", code=2))
        self.assertFalse(r.ok)
        self.assertIn("auth error", r.raw_tail)

    def test_args(self):
        a = CodexWorker(WorkerConfig(type="codex", model="gpt")).build_args("p", resume="s")
        self.assertEqual(a[:4], ["codex", "exec", "resume", "s"])
        self.assertEqual(a[-1], "p")


class TestOpenCodeParse(unittest.TestCase):
    w = OpenCodeWorker(WorkerConfig(type="opencode"))

    def test_plain(self):
        self.assertEqual(self.w.parse_output(pr("txt")).text, "txt")

    def test_json_shapes(self):
        r = self.w.parse_output(pr(json.dumps({"sessionID": "o1", "content": "hi"})))
        self.assertEqual((r.text, r.session_id), ("hi", "o1"))
        r = self.w.parse_output(pr(json.dumps([{"text": "a"}, {"text": "b"}])))
        self.assertIn("a", r.text)

    def test_broken_json_falls_back(self):
        self.assertEqual(self.w.parse_output(pr("{oops")).text, "{oops")


class FakeWorker:
    def __init__(self, name, ok, kind=""):
        self.name, self.ok, self.kind = name, ok, kind
        self.calls = 0
        self.cfg = WorkerConfig(type="claude")

    def run(self, prompt, cwd, timeout=None, resume=None):
        self.calls += 1
        return WorkerResult(worker=self.name, ok=self.ok, text=self.name, fail_kind=self.kind)


class TestRouterFallback(unittest.TestCase):
    def _router(self, workers: dict, order, **cfg_kw):
        cfg = Config(root=".", worker_order=order,
                     workers={n: WorkerConfig(type="claude", command="true") for n in order}, **cfg_kw)
        r = Router(cfg, Usage(Path(tempfile.mkdtemp())))
        r.worker_for = lambda n: workers[n]
        r.available_names = lambda: list(order)
        return r

    def test_falls_back_in_order(self):
        a, b = FakeWorker("a", False, "quota"), FakeWorker("b", True)
        name, res = self._router({"a": a, "b": b}, ["a", "b"]).run("dev", Path("."), "p")
        self.assertEqual((name, a.calls, b.calls), ("b", 1, 1))

    def test_all_fail(self):
        a, b = FakeWorker("a", False), FakeWorker("b", False)
        with self.assertRaises(AllWorkersFailed) as ctx:
            self._router({"a": a, "b": b}, ["a", "b"]).run("dev", Path("."), "p")
        self.assertEqual(len(ctx.exception.results), 2)

    def test_over_budget_moves_to_back(self):
        a, b = FakeWorker("a", True), FakeWorker("b", True)
        r = self._router({"a": a, "b": b}, ["a", "b"])
        r.cfg.workers["a"].daily_call_limit = 1
        r.usage.record("a", 0.1, True)
        name, _ = r.run("dev", Path("."), "p")
        self.assertEqual(name, "b")

    def test_shutdown_stops_fallback(self):
        from factory import util

        a, b = FakeWorker("a", False), FakeWorker("b", True)
        util.SHUTDOWN.set()
        try:
            with self.assertRaises(AllWorkersFailed):
                self._router({"a": a, "b": b}, ["a", "b"]).run("dev", Path("."), "p")
            self.assertEqual(b.calls, 0)
        finally:
            util.SHUTDOWN.clear()

    def test_openrouter_not_used_for_dev(self):
        cfg = Config(root=".", worker_order=["or"], workers={"or": WorkerConfig(type="openrouter")})
        with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "k"}):
            r = Router(cfg, Usage(Path(tempfile.mkdtemp())))
            self.assertEqual(r.candidates("dev"), [])
            self.assertEqual(r.candidates("judge"), ["or"])


if __name__ == "__main__":
    unittest.main()
