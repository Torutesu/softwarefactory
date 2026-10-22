"""OpenRouter ワーカー (HTTP 直呼び) を手元の擬似サーバで検証する。"""
from __future__ import annotations

import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from factory.config import WorkerConfig
from factory.workers import openrouter as orw


class TestOpenRouter(unittest.TestCase):
    def setUp(self):
        self.reply = (200, {})
        self.seen = {}
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                outer.seen["auth"] = self.headers.get("Authorization")
                outer.seen["body"] = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                code, body = outer.reply
                raw = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *a):
                pass

        self.srv = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.patches = [mock.patch.object(orw, "API_URL", f"http://127.0.0.1:{self.srv.server_address[1]}/v1"),
                        mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "k-test"})]
        for p in self.patches:
            p.start()
        self.w = orw.OpenRouterWorker(WorkerConfig(type="openrouter", model="m", timeout=5))

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.srv.shutdown()

    def test_success(self):
        self.reply = (200, {"id": "gen-1", "choices": [{"message": {"content": "hi"}}],
                            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "cost": 0.01}})
        r = self.w.run("prompt", Path("."))
        self.assertEqual((r.ok, r.text, r.tokens_in, r.tokens_out, r.cost_usd, r.session_id),
                         (True, "hi", 3, 2, 0.01, "gen-1"))
        self.assertEqual(self.seen["auth"], "Bearer k-test")
        self.assertEqual(self.seen["body"]["model"], "m")

    def test_structured_content(self):
        self.reply = (200, {"choices": [{"message": {"content": [{"type": "text", "text": "a"}, "b"]}}]})
        r = self.w.run("p", Path("."))
        self.assertEqual(r.text, "a\nb")

    def test_error_body_with_200(self):
        self.reply = (200, {"error": {"message": "bad"}})
        r = self.w.run("p", Path("."))
        self.assertFalse(r.ok)

    def test_http_429_is_quota(self):
        self.reply = (429, {"error": {"message": "Rate limit exceeded"}})
        r = self.w.run("p", Path("."))
        self.assertEqual((r.ok, r.fail_kind), (False, "quota"))

    def test_non_json(self):
        self.reply = (200, b"<html>")
        self.assertFalse(self.w.run("p", Path(".")).ok)

    def test_missing_key(self):
        with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": ""}):
            r = self.w.run("p", Path("."))
        self.assertFalse(r.ok)
        self.assertIn("OPENROUTER_API_KEY", r.raw_tail)

    def test_unreachable(self):
        with mock.patch.object(orw, "API_URL", "http://127.0.0.1:1/v1"):
            self.assertFalse(self.w.run("p", Path(".")).ok)


if __name__ == "__main__":
    unittest.main()
