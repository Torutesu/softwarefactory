"""pr.py (gh 連携) と canary の revert 分岐。gh はスタブ、リモートはローカルの bare repo。"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from factory import canary as canary_mod
from factory.config import Config
from factory.init_scaffold import init_factory
from factory.loop import run_mission
from factory.pr import create_pr
from factory.state import Factory, mark_merged
from factory.supervisor import tick


def _git(d, *a):
    return subprocess.run(["git", "-C", str(d), *a], capture_output=True, text=True)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        init_factory(self.tmp, demo=True)
        self.cfg = Config.create(str(self.tmp), demo=True)
        self.f = Factory(self.tmp)
        self.repo = self.tmp / self.cfg.projects["demo_app"].path
        self.cwd = os.getcwd()
        os.chdir(self.tmp)

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _done_mission(self):
        m = self.f.create_mission("pr", kind="update", seed="demo-app", repo=self.cfg.projects["demo_app"].path)
        self.assertEqual(run_mission(self.f, m.id, cfg=self.cfg).status, "done")
        return m.id


class TestPr(Base):
    def _gh(self, script: str) -> Path:
        d = Path(tempfile.mkdtemp())
        (d / "gh").write_text("#!/bin/sh\n" + script, encoding="utf-8")
        (d / "gh").chmod(0o755)
        return d

    def _with_origin(self):
        bare = self.tmp / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
        _git(self.repo, "remote", "add", "origin", str(bare))

    def test_draft_without_remote(self):
        mid = self._done_mission()
        res = create_pr(self.f, mid)
        self.assertEqual(res["status"], "draft")
        body = Path(res["draft"]).read_text(encoding="utf-8")
        self.assertIn("Assisted-by", body)
        self.assertNotIn("FACTORY_HOLDOUT_DIR", body)  # シナリオの中身を PR に載せない

    def test_created_with_gh(self):
        mid = self._done_mission()
        self._with_origin()
        d = self._gh('echo "https://github.com/x/y/pull/7"\n')
        with mock.patch.dict(os.environ, {"PATH": f"{d}:{os.environ['PATH']}"}):
            res = create_pr(self.f, mid, base="main")
        self.assertEqual(res, {**res, "status": "created", "url": "https://github.com/x/y/pull/7"})

    def test_gh_failure_and_empty_output(self):
        mid = self._done_mission()
        self._with_origin()
        with mock.patch.dict(os.environ, {"PATH": f"{self._gh('echo nope >&2; exit 1')}:{os.environ['PATH']}"}):
            self.assertEqual(create_pr(self.f, mid)["status"], "failed")
        with mock.patch.dict(os.environ, {"PATH": f"{self._gh('exit 0')}:{os.environ['PATH']}"}):
            self.assertEqual(create_pr(self.f, mid)["status"], "created")

    def test_push_failure(self):
        mid = self._done_mission()
        _git(self.repo, "remote", "add", "origin", str(self.tmp / "missing.git"))
        with mock.patch.dict(os.environ, {"PATH": f"{self._gh('exit 0')}:{os.environ['PATH']}"}):
            self.assertEqual(create_pr(self.f, mid)["status"], "push-failed")

    def test_no_iterations(self):
        m = self.f.create_mission("x", repo=self.cfg.projects["demo_app"].path)
        with self.assertRaises(SystemExit):
            create_pr(self.f, m.id)


class TestCanaryRevert(Base):
    def _merged(self):
        self.cfg.autonomy_level = 2
        mid = self._done_mission()
        out = tick(self.f, self.cfg)
        self.assertIn(mid, out["deliver"]["merged"])
        return mid

    def _break_main(self):
        app = self.repo / "app.py"
        app.write_text(app.read_text(encoding="utf-8").replace("def delete_note", "def delete_note_broken"),
                       encoding="utf-8")
        _git(self.repo, "commit", "-qam", "human: unrelated change")

    def test_not_merged_is_refused(self):
        mid = self._done_mission()
        with self.assertRaises(SystemExit):
            canary_mod.run_canary(self.f, mid, cfg=self.cfg)

    def test_fail_and_revert_creates_repair(self):
        mid = self._merged()
        # main 上の factory コミットそのものを壊す (factory コミットが最新)
        app = self.repo / "app.py"
        app.write_text(app.read_text(encoding="utf-8").replace("del NOTES[note_id]", "pass"), encoding="utf-8")
        _git(self.repo, "commit", "-q", "--amend", "-am", f"factory: {mid} pr")
        res = canary_mod.run_canary(self.f, mid, cfg=self.cfg, fix=True)
        self.assertEqual(res["status"], "fail")
        self.assertTrue(res["revert"].startswith("reverted"), res)
        rm = self.f.get_mission(res["new_mission"])
        self.assertEqual(rm.repair_of, mid)
        res2 = canary_mod.run_canary(self.f, mid, cfg=self.cfg, fix=True)
        self.assertEqual(res2.get("revert"), "already-reverted")

    def test_revert_conflict_does_not_create_repair(self):
        mid = self._merged()
        self._break_main()  # factory コミットの後に人間の変更 → revert が衝突しうる / main は壊れている
        app = self.repo / "app.py"
        app.write_text(app.read_text(encoding="utf-8").replace("del NOTES[note_id]", "NOTES.pop(note_id)"),
                       encoding="utf-8")
        _git(self.repo, "commit", "-qam", "human: touch same lines")
        res = canary_mod.run_canary(self.f, mid, cfg=self.cfg, fix=True)
        self.assertEqual(res["status"], "fail")
        if not res["revert"].startswith(("reverted", "already-reverted")):
            self.assertIsNone(res["new_mission"])
            self.assertEqual(_git(self.repo, "status", "--porcelain", "--untracked-files=no").stdout.strip(), "")

    def test_dirty_repo_aborts_revert(self):
        mid = self._merged()
        self._break_main()
        (self.repo / "README.md").write_text("dirty\n", encoding="utf-8")
        res = canary_mod.run_canary(self.f, mid, cfg=self.cfg, fix=True)
        self.assertEqual(res["revert"], "aborted(dirty)")
        self.assertIsNone(res["new_mission"])

    def test_stale_canary_worktree_is_recreated(self):
        mid = self._merged()
        stale = self.f.mission_dir(mid) / "canary"
        stale.mkdir()
        (stale / "junk").write_text("x", encoding="utf-8")
        res = canary_mod.run_canary(self.f, mid, cfg=self.cfg)
        self.assertEqual(res["status"], "pass")

    def test_legacy_merged_note_is_accepted(self):
        mid = self._done_mission()
        m = self.f.get_mission(mid)
        mark_merged(m, "scoped")
        self.f.save_mission(m)
        self.assertIn(canary_mod.run_canary(self.f, mid, cfg=self.cfg)["status"], ("pass", "fail"))


if __name__ == "__main__":
    unittest.main()
