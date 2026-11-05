"""macOS sandbox-exec の組み込み (Linux では擬似 sandbox-exec で配線だけ確かめる)。"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from factory import sandbox
from factory.config import Config
from factory.configcheck import errors, validate
from factory.init_scaffold import init_factory
from factory.loop import run_mission
from factory.state import Factory


class TestProfile(unittest.TestCase):
    def test_profile_denies_holdout_state_and_secrets(self):
        cfg = Config(root="/srv/fac tory", sandbox="harness")
        prof = sandbox.profile(cfg, "harness")
        self.assertTrue(prof.startswith("(version 1)\n(allow default)"))
        self.assertIn(os.path.realpath("/srv/fac tory/holdout"), prof)
        self.assertIn(os.path.realpath("/srv/fac tory/.factory"), prof)
        self.assertIn(".ssh", prof)
        self.assertIn("Keychains", prof)
        self.assertNotIn("Keychains", sandbox.profile(cfg, "worker"))  # CLI のログインに要る

    def test_quoting(self):
        self.assertEqual(sandbox._q('a"b\\c'), '"a\\"b\\\\c"')

    def test_prefix_modes(self):
        with mock.patch.object(sandbox, "available", return_value=True):
            self.assertEqual(sandbox.prefix(Config(root="/r", sandbox="off"), "harness"), [])
            self.assertEqual(sandbox.prefix(Config(root="/r", sandbox="harness"), "worker"), [])
            self.assertEqual(sandbox.prefix(Config(root="/r", sandbox="harness"), "harness")[:2],
                             [sandbox.SANDBOX_EXEC, "-p"])
            self.assertEqual(len(sandbox.prefix(Config(root="/r", sandbox="all"), "worker")), 3)
        with mock.patch.object(sandbox, "available", return_value=False):
            self.assertEqual(sandbox.prefix(Config(root="/r", sandbox="all"), "harness"), [])

    def test_config_validation(self):
        d = Config.create(tempfile.mkdtemp(), demo=True).to_dict()
        d["sandbox"] = "strict"
        self.assertTrue(any(i.path == "sandbox" for i in errors(validate(d))))


class TestWiring(unittest.TestCase):
    """擬似 sandbox-exec: 受け取った profile を記録して、残りの引数をそのまま実行する。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        init_factory(self.tmp, demo=True)
        self.cfg = Config.create(str(self.tmp), demo=True)
        self.log = self.tmp / "sandbox.log"
        self.fake = self.tmp / "fake-sandbox-exec"
        self.fake.write_text(f'#!/bin/sh\nprintf "%s\\n" "$3 ::: $2" | head -c 300 >> "{self.log}"\n'
                             f'echo >> "{self.log}"\nshift 2\nexec "$@"\n', encoding="utf-8")
        self.fake.chmod(0o755)
        self.f = Factory(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, mode):
        self.cfg.sandbox = mode
        m = self.f.create_mission("sb", kind="update", seed="demo-app", repo=self.cfg.projects["demo_app"].path)
        with mock.patch.object(sandbox, "available", return_value=True), \
                mock.patch.object(sandbox, "SANDBOX_EXEC", str(self.fake)):
            m2 = run_mission(self.f, m.id, cfg=self.cfg)
        return m2, (self.log.read_text(encoding="utf-8") if self.log.exists() else "")

    def test_harness_commands_go_through_sandbox(self):
        m2, log = self._run("harness")
        self.assertEqual(m2.status, "done")
        self.assertIn("/bin/bash", log)  # build/test/シナリオ
        self.assertIn("(deny file-read* file-write*", log)
        self.assertIn(os.path.realpath(str(self.tmp / "holdout")), log)

    def test_off_means_no_wrapping(self):
        m2, log = self._run("off")
        self.assertEqual(m2.status, "done")
        self.assertEqual(log, "")

    def test_worker_scope_only_for_dev(self):
        from factory.workers.router import Router
        from factory.usage import Usage

        self.cfg.sandbox = "all"
        r = Router(self.cfg, Usage(self.f.state_dir))
        seen = {}

        from factory.config import WorkerConfig

        class W:
            cfg = WorkerConfig(type="mock")

            def run(self_, prompt, cwd, timeout=None, resume=None):
                from factory.workers.base import WorkerResult

                seen.setdefault("prefix", []).append(list(getattr(self_, "exec_prefix", [])))
                return WorkerResult(worker="mock", ok=True, text="{}")

        w = W()
        r.worker_for = lambda n: w
        with mock.patch.object(sandbox, "available", return_value=True):
            r.run("spec", self.tmp, "p")
            r.run("dev", self.tmp, "p")
        self.assertEqual(seen["prefix"][0], [])
        self.assertEqual(seen["prefix"][1][:2], [sandbox.SANDBOX_EXEC, "-p"])
        json.dumps(seen)


if __name__ == "__main__":
    unittest.main()
