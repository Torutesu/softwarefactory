"""社内ソフトウェアファクトリーの結合テスト (mock worker による全パイプライン)。"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYTHONPATH = str(ROOT)


def run_cli(args, cwd):
    env = dict(os.environ)
    env["PYTHONPATH"] = PYTHONPATH
    return subprocess.run(
        [sys.executable, "-m", "factory", *args],
        cwd=str(cwd), capture_output=True, text=True, env=env, timeout=600,
    )


class TestDemoPipeline(unittest.TestCase):
    def test_init_mission_run_merge(self):
        with tempfile.TemporaryDirectory() as td:
            r = run_cli(["init", td, "--demo"], td)
            self.assertEqual(r.returncode, 0, r.stderr or r.stdout)

            r = run_cli(["mission", "new", "メモAPIの実装", "--project", "demo_app", "--seed", "demo-app"], td)
            self.assertEqual(r.returncode, 0, r.stderr or r.stdout)
            mid = r.stdout.split("mission created: ")[1].split()[0]

            r = run_cli(["run", mid], td)
            self.assertEqual(r.returncode, 0, r.stderr or r.stdout)
            self.assertIn("PASS", r.stdout, r.stdout)
            self.assertIn("done", r.stdout, r.stdout)

            # ワークツリーで実装がコミットされている
            wt = Path(td) / "missions" / mid / "work"
            self.assertTrue((wt / "app.py").exists())
            st = subprocess.run(["git", "-C", str(wt), "status", "--porcelain"], capture_output=True, text=True)
            self.assertEqual(st.stdout.strip(), "", st.stdout)

            # merge (--yes)
            r = run_cli(["merge", mid, "--yes"], td)
            self.assertEqual(r.returncode, 0, r.stderr or r.stdout)
            log = subprocess.run(
                ["git", "-C", str(Path(td) / "products" / "demo_app"), "log", "--oneline", "-2"],
                capture_output=True, text=True,
            )
            self.assertIn(mid or "factory:", log.stdout, log.stdout)


class TestUnits(unittest.TestCase):
    def test_judge_json_parse(self):
        from factory.harness.judge import parse_judge_json

        text = '前置きがある本文\n```json\n{"score": 88, "verdict": "pass", "comments": "ok"}\n```'
        d = parse_judge_json(text)
        self.assertIsNotNone(d)
        self.assertEqual(d["score"], 88)

    def test_scenario_schema_tolerance(self):
        from factory.holdout import Scenario

        sc = Scenario.from_dict({"id": "SC-1", "title": "t", "steps": [
            {"name": "s1", "cmd": "true"},
            {"name": "s2", "cmd": "true", "expect_exit": 0, "未知のキー": 1},
        ]})
        self.assertEqual(len(sc.steps), 2)

    def test_config_worker_order(self):
        from factory.config import Config

        cfg = Config.from_dict({"worker_order": ["opencode", "claude_code"]})
        self.assertEqual(cfg.worker_order[0], "opencode")

        cfg2 = Config.from_dict({})
        self.assertEqual(cfg2.worker_order[0], "claude_code")


if __name__ == "__main__":
    unittest.main()
