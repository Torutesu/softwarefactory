"""サブスク既定認証・codex・OpenRouter・用途フィルタのテスト。"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from factory.workers.base import child_env_for
from factory.workers.claude_code import ClaudeCodeWorker
from factory.workers.codex import CodexWorker
from factory.workers.router import Router


class MockCfg:
    def __init__(self, **kw):
        defaults = dict(type="claude", command="", args=[], model="", timeout=10,
                        daily_call_limit=0, window_call_limit=0, window_minutes=300,
                        resume=False, auth="subscription", allowed_tools=[], disallowed_tools=[])
        defaults.update(kw)
        for k, v in defaults.items():
            setattr(self, k, v)


class TestSubscriptionAuth(unittest.TestCase):
    def test_subscription_scrubs_api_keys(self):
        os.environ["ANTHROPIC_API_KEY"] = "sk-test"
        os.environ["OPENAI_API_KEY"] = "sk-openai"
        os.environ["OPENROUTER_API_KEY"] = "sk-or"  # OpenRouter 専用なので残す
        env = child_env_for(MockCfg(type="claude", auth="subscription"))
        self.assertNotIn("ANTHROPIC_API_KEY", env)
        self.assertNotIn("OPENAI_API_KEY", env)
        self.assertEqual(env.get("OPENROUTER_API_KEY"), "sk-or")

    def test_api_key_mode_passes_through(self):
        os.environ["OPENAI_API_KEY"] = "sk-openai"
        env = child_env_for(MockCfg(type="codex", auth="api_key"))
        self.assertEqual(env.get("OPENAI_API_KEY"), "sk-openai")

    def test_claude_subscript_default(self):
        """デフォルト設定は subscription (サブスク利用が標準)。"""
        w = ClaudeCodeWorker(MockCfg(type="claude"))
        self.assertEqual(w.cfg.auth, "subscription")
        args = w.build_args("p")
        self.assertEqual(args[0], "claude")
        self.assertIn("-p", args)


class TestCodexWorker(unittest.TestCase):
    def test_build_args(self):
        w = CodexWorker(MockCfg(type="codex", model="gpt-5.2"))
        args = w.build_args("タスク説明")
        self.assertEqual(args[0], "codex")
        self.assertEqual(args[1], "exec")
        self.assertIn("--model", args)
        i = args.index("--model")
        self.assertEqual(args[i + 1], "gpt-5.2")
        self.assertIn("--dangerously-bypass-approvals-and-sandbox", args)
        self.assertIn("--skip-git-repo-check", args)
        self.assertEqual(args[-1], "タスク説明")

    def test_parse_plain_text(self):
        from factory.util import ProcResult

        w = CodexWorker(MockCfg(type="codex"))
        res = w.parse_output(ProcResult(0, "実装が完了しました", "", 1.0))
        self.assertTrue(res.ok)
        self.assertEqual(res.text, "実装が完了しました")

    def test_parse_jsonl_events(self):
        from factory.util import ProcResult

        w = CodexWorker(MockCfg(type="codex"))
        out = '\n'.join([
            '{"session_id":"cx-77","type":"turn.started"}',
            '{"type":"agent.message","text":"途中"}',
            '{"type":"item.completed","text":"結果":1}',  # 不正JSON → 無視される
            '{"type":"item.completed","text":"完了しました"}',
        ])
        res = w.parse_output(ProcResult(0, out, "", 2.0))
        self.assertIn("完了しました", res.text)
        self.assertEqual(res.session_id, "cx-77")


class TestOpenRouterWorker(unittest.TestCase):
    def test_run_http(self):
        os.environ["OPENROUTER_API_KEY"] = "sk-or-test"
        from factory.workers.openrouter import OpenRouterWorker

        captured = {}

        class FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                import json as _json

                data = {"choices": [{"message": {"content": "回答テキスト"}}],
                        "usage": {"prompt_tokens": 100, "completion_tokens": 50, "cost": 0.003},
                        "id": "gen-1"}
                return _json.dumps(data, ensure_ascii=False).encode("utf-8")

        real_urlopen = __import__("urllib.request", fromlist=["urlopen"]).urlopen

        def fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            captured["headers"] = {k: v for k, v in req.header_items()}
            captured["payload"] = req.data
            return FakeResp()

        import urllib.request as ur

        ur.urlopen = fake_urlopen
        try:
            w = OpenRouterWorker(MockCfg(type="openrouter", model="anthropic/claude-sonnet-4.5"))
            res = w.run("spec を書いて", Path("/tmp"), timeout=30)
            self.assertTrue(res.ok)
            self.assertEqual(res.text, "回答テキスト")
            self.assertEqual(res.tokens_in, 100)
            self.assertEqual(res.tokens_out, 50)
            self.assertEqual(res.cost_usd, 0.003)
            self.assertEqual(res.session_id, "gen-1")
            self.assertIn("Authorization", " ".join(captured["headers"].keys()))
            self.assertIn(b"anthropic/claude-sonnet-4.5", captured["payload"])
            self.assertIn("openrouter.ai", captured["url"])
        finally:
            ur.urlopen = real_urlopen

    def test_no_key_fails_cleanly(self):
        os.environ.pop("OPENROUTER_API_KEY", None)
        from factory.workers.openrouter import OpenRouterWorker

        w = OpenRouterWorker(MockCfg(type="openrouter"))
        res = w.run("p", Path("/tmp"), timeout=5)
        self.assertFalse(res.ok)
        self.assertIn("OPENROUTER_API_KEY", res.raw_tail)


class TestRouterPurpose(unittest.TestCase):
    def _cfg(self, worker_cfg: MockCfg):
        from factory.config import Config

        cfg = Config(root=".", use_worker="openrouter", worker_order=["openrouter"],
                     workers={"openrouter": worker_cfg})
        return cfg

    def test_openrouter_excluded_for_dev(self):
        os.environ["OPENROUTER_API_KEY"] = "sk-or"
        cfg = self._cfg(MockCfg(type="openrouter", auth="api_key"))
        r = Router(cfg, __import__("factory.usage", fromlist=["Usage"]).Usage(Path("/tmp/x")))
        self.assertEqual(r.candidates("dev"), [])
        self.assertEqual(r.candidates("judge"), ["openrouter"])
        self.assertEqual(r.candidates("spec"), ["openrouter"])

    def test_openrouter_requires_key(self):
        os.environ.pop("OPENROUTER_API_KEY", None)
        cfg = self._cfg(MockCfg(type="openrouter", auth="api_key"))
        r = Router(cfg, __import__("factory.usage", fromlist=["Usage"]).Usage(Path("/tmp/x")))
        self.assertEqual(r.available_names(), [])

    def test_codex_in_order(self):
        from factory.config import Config, WorkerConfig

        cfg = Config(root=".",
                     worker_order=["claude_code", "codex", "opencode", "mock"],
                     workers={"claude_code": WorkerConfig(type="claude"),
                              "codex": WorkerConfig(type="codex"),
                              "opencode": WorkerConfig(type="opencode")})
        r = Router(cfg, __import__("factory.usage", fromlist=["Usage"]).Usage(Path("/tmp/x")))
        # CLI 実体が無い環境: codex/claude/opencode はスキップされ mock のみ
        self.assertEqual(r.available_names(), ["mock"])
        self.assertEqual(r.candidates("dev"), ["mock"])
        self.assertEqual(r.candidates("judge"), ["mock"])


if __name__ == "__main__":
    unittest.main()
