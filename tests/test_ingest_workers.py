"""Ingest (Seed 化) とミッション作成連鎖、worker 引数組み立てのテスト。"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from factory.config import Config, WorkerConfig
from factory.ingest import create_from_payload, ingest_dir
from factory.state import Factory
from factory.workers.claude_code import ClaudeCodeWorker
from factory.workers.opencode import OpenCodeWorker
from factory.workers.router import Router
from factory.usage import Usage


def _demo_root() -> Path:
    return Path(tempfile.mkdtemp())


class TestIngest(unittest.TestCase):
    def setUp(self):
        self.root = _demo_root()
        subprocess.run(
            [sys.executable, "-m", "factory", "init", str(self.root), "--demo"],
            cwd=ROOT, check=True, capture_output=True,
        )

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_ingest_dir_and_mission(self):
        f = Factory(self.root)
        cfg_d = json.loads((Path(self.root) / ".factory" / "config.json").read_text(encoding="utf-8"))
        repo_path = cfg_d["projects"]["demo_app"]["path"]
        # 取込元の別 Seed
        src_dir = Path(tempfile.mkdtemp())
        (src_dir / "note.md").write_text("欲しい機能のメモ", encoding="utf-8")
        seed = ingest_dir(f, src_dir, name="kibo")
        self.assertTrue((seed / "seed.md").exists())
        self.assertTrue((seed / "note.md").exists())
        m = f.create_mission(title="kibo から", kind="update", seed=seed.name, repo=repo_path)
        self.assertEqual(m.status, "queued")

    def test_create_from_payload_unique(self):
        f = Factory(self.root)
        a = create_from_payload(f, "障害: 保存失敗", "本文", "slack")
        b = create_from_payload(f, "障害: 保存失敗", "本文2", "slack")
        self.assertNotEqual(a.name, b.name)
        self.assertIn("slack", f.state()["signals"][-1]["source"])

    def test_url_text_extraction(self):
        from factory.ingest import _extract_text

        html = "<html><head><title>ページ</title></head><body><p>こんにちは &amp; さようなら</p><script>bad()</script><div>改行<br>チェック</div></body></html>"
        text = _extract_text(html)
        self.assertIn("こんにちは & さようなら", text)
        self.assertIn("改行\nチェック", text)
        self.assertNotIn("bad()", text)


class TestWorkerArgs(unittest.TestCase):
    def test_claude_resume_and_tools(self):
        class Cfg:
            type = "claude"
            command = "claude"
            args = ["--verbose"]
            model = "sonnet"
            timeout = 10
            allowed_tools = ["Bash", "Edit"]
            disallowed_tools = ["WebSearch"]
            max_turns = None
        w = ClaudeCodeWorker(Cfg())
        args = w.build_args("プロンプト", resume="sess-123")
        i = args.index("--resume")
        self.assertEqual(args[i + 1], "sess-123")
        self.assertIn("プロンプト", args)
        self.assertNotIn("--allowedTools", args[:i])  # resume より前には無い
        self.assertEqual(args[args.index("--allowedTools") + 1], "Bash,Edit")
        self.assertEqual(args[args.index("--disallowedTools") + 1], "WebSearch")

    def test_opencode_json_parse(self):
        class Cfg:
            type = "opencode"
            command = "opencode"
            args = ["--format", "json"]
            model = ""
            timeout = 10
        w = OpenCodeWorker(Cfg())

        class PR:
            code = 0
            out = '{"sessionID":"oc-9","content":"SQL を実装しました"}'
            err = ""
            seconds = 1.0
        res = w.parse_output(PR())
        self.assertEqual(res.session_id, "oc-9")
        self.assertIn("SQL", res.text)

    def test_router_passes_resume_to_matching_worker(self):
        """resume 指定時、先頭候補が同じ worker なら resume 引数が渡る。"""
        captured = {}

        class FakeWorker:
            name = "mock"
            cfg_type = "mock"

            def __init__(self, cfg):
                self.cfg = cfg

            def run(self, prompt, cwd, timeout=None, resume=None):
                captured["resume"] = resume
                from factory.workers.base import WorkerResult

                return WorkerResult(worker="mock", ok=True, text="ok")

        cfg = Config(root=".", use_worker="mock", worker_order=["mock"],
                     workers={"mock": WorkerConfig(type="mock", resume=True)})
        r = Router(cfg, Usage(Path(tempfile.mkdtemp())))
        r.worker_for = lambda n: FakeWorker(cfg)
        r.available_names = lambda: ["mock"]
        r.caps_for = lambda n: ("spec", "holdout", "dev", "judge")
        r.run("dev", Path("."), "p", resume=("mock", "sess-abc"))
        self.assertEqual(captured["resume"], "sess-abc")

    def test_router_resume_skipped_for_other_worker(self):
        """resume 先が先頭候補と違うワーカーなら resume を渡さない。"""
        captured = {}

        class FakeWorker:
            name = "mock"

            def __init__(self, cfg):
                self.cfg = cfg

            def run(self, prompt, cwd, timeout=None, resume=None):
                captured["resume"] = resume
                from factory.workers.base import WorkerResult

                return WorkerResult(worker="mock", ok=True, text="ok")

        cfg = Config(root=".", use_worker="claude_code", worker_order=["claude_code", "mock"])
        cfg.workers = {"claude_code": WorkerConfig(type="claude", resume=True)}
        r = Router(cfg, Usage(Path(tempfile.mkdtemp())))

        def for_name(n):
            return FakeWorker(cfg)

        r.worker_for = for_name
        r.available_names = lambda: ["claude_code", "mock"]
        r.caps_for = lambda n: ("spec", "holdout", "dev", "judge")
        r.run("dev", Path("."), "p", resume=("mock", "sess-abc"))
        self.assertIsNone(captured["resume"])


if __name__ == "__main__":
    unittest.main()
