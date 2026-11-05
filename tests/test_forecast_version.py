"""枠の見込み (forecast) による開始見送りと、バージョン・state の移行。"""
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

from factory import __version__, supervisor
from factory.config import Config, WorkerConfig
from factory.init_scaffold import init_factory
from factory.state import STATE_SCHEMA, Factory
from factory.usage import DEFAULT_CALLS_PER_MISSION, Usage


class TestForecast(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        init_factory(self.tmp, demo=True)
        self.f = Factory(self.tmp)
        self.u = Usage(self.f.state_dir)
        self.stub = self.tmp / "fakeclaude"
        self.stub.write_text("#!/bin/sh\necho ok\n", encoding="utf-8")
        self.stub.chmod(0o755)
        self.cfg = Config.create(str(self.tmp), demo=True)
        self.cfg.use_worker = "auto"
        self.cfg.worker_order = ["claude_code"]
        self.cfg.workers = {"claude_code": WorkerConfig(type="claude", command=str(self.stub),
                                                        daily_call_limit=10, window_call_limit=5)}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_forecast_rows(self):
        for _ in range(3):
            self.u.record("claude_code", 0.1, True)
        row = self.u.forecast(self.cfg, self.f)[0]
        self.assertEqual(row["per_mission"], DEFAULT_CALLS_PER_MISSION)  # ミッション履歴なし → 既定値
        self.assertEqual((row["day_left"], row["window_left"]), (7, 2))
        self.assertAlmostEqual(row["missions_fit"], 2 / DEFAULT_CALLS_PER_MISSION)
        self.assertIn("あと何ミッション", self.u.forecast_report(self.cfg, self.f))

    def test_unlimited_worker_is_infinite(self):
        self.cfg.workers["claude_code"] = WorkerConfig(type="claude", command=str(self.stub))
        self.assertEqual(self.u.forecast(self.cfg, self.f)[0]["missions_fit"], float("inf"))

    def test_tick_defers_when_budget_will_not_fit(self):
        for _ in range(4):
            self.u.record("claude_code", 0.1, True)  # 窓の残り 1 < 1 ミッション分 (4)
        m = self.f.create_mission("x", kind="update", seed="demo-app", repo=self.cfg.projects["demo_app"].path)
        out = supervisor.step_execute(self.f, self.cfg, dry_run=False)
        self.assertEqual(out["ran"], [])
        self.assertTrue(any("枠不足の見込み" in d for d in out["deferred"]))
        self.assertEqual(self.f.get_mission(m.id).status, "queued")
        self.cfg.forecast_defer = False
        self.assertEqual(supervisor.step_execute(self.f, self.cfg, dry_run=True)["ran"], [f"(dry) {m.id}"])


class TestVersion(unittest.TestCase):
    def test_cli_version(self):
        env = dict(os.environ, PYTHONPATH=ROOT)
        r = subprocess.run([sys.executable, "-m", "factory", "--version"], capture_output=True, text=True, env=env)
        self.assertEqual(r.stdout.strip(), f"factory {__version__}")

    def test_state_v1_is_migrated(self):
        tmp = Path(tempfile.mkdtemp())
        f = Factory(tmp)
        f.state_dir.mkdir(parents=True)
        f.state_file.write_text(json.dumps({"version": 1, "missions": {}, "signals": [], "events": [],
                                            "custom": 1}), encoding="utf-8")
        st = f.state()
        self.assertEqual(st["version"], STATE_SCHEMA)
        self.assertEqual(st["needs_human"], {})
        self.assertEqual(st["custom"], 1)
        f.event("x")
        self.assertEqual(json.loads(f.state_file.read_text(encoding="utf-8"))["version"], STATE_SCHEMA)

    def test_newer_state_is_read_as_is(self):
        tmp = Path(tempfile.mkdtemp())
        f = Factory(tmp)
        f.state_dir.mkdir(parents=True)
        f.state_file.write_text(json.dumps({"version": 99, "events": []}), encoding="utf-8")
        self.assertEqual(f.state()["version"], 99)


if __name__ == "__main__":
    unittest.main()
