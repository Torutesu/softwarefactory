"""マージ直前の最新 main での再検証 (並列ミッションの組み合わせ破壊を出荷前に止める)。"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from factory import gitx, supervisor
from factory.config import Config
from factory.init_scaffold import init_factory
from factory.loop import run_mission
from factory.state import Factory, is_merged


def _git(d, *a):
    return subprocess.run(["git", "-C", str(d), *a], capture_output=True, text=True)


class TestRevalidate(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        init_factory(self.tmp, demo=True)
        self.cfg = Config.create(str(self.tmp), demo=True)
        self.cfg.autonomy_level = 2
        self.f = Factory(self.tmp)
        self.repo = self.tmp / self.cfg.projects["demo_app"].path

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _done(self, title="m"):
        m = self.f.create_mission(title, kind="update", seed="demo-app", repo=self.cfg.projects["demo_app"].path)
        self.assertEqual(run_mission(self.f, m.id, cfg=self.cfg).status, "done")
        return m.id

    def _commit_on_main(self, find, replace, msg):
        app = self.repo / "app.py"
        text = app.read_text(encoding="utf-8")
        self.assertIn(find, text)
        app.write_text(text.replace(find, replace, 1), encoding="utf-8")
        _git(self.repo, "commit", "-qam", msg)

    def test_up_to_date_branch_merges(self):
        mid = self._done()
        out = supervisor.step_deliver(self.f, self.cfg, dry_run=False)
        self.assertIn(mid, out["merged"])

    def test_main_moved_compatibly_revalidates_and_merges(self):
        mid = self._done()
        _git(self.repo, "commit", "-q", "--allow-empty", "-m", "unrelated")
        (self.repo / "NOTES.txt").write_text("docs\n", encoding="utf-8")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-qm", "docs")
        out = supervisor.step_deliver(self.f, self.cfg, dry_run=False)
        self.assertIn(mid, out["merged"])
        self.assertTrue(any(e["kind"] == "base_merged_for_revalidation" for e in self.f.state()["events"]))

    def test_main_broke_combination_requeues(self):
        mid = self._done()
        # main 側で一覧 API を壊す (ミッションの差分とは別の場所 = マージは衝突しない)
        self._commit_on_main("def list_notes", "def list_notes_renamed", "rename list_notes")
        out = supervisor.step_deliver(self.f, self.cfg, dry_run=False)
        self.assertNotIn(mid, out["merged"])
        m = self.f.get_mission(mid)
        self.assertEqual((m.status, m.halt_reason), ("queued", "stale_base"))
        self.assertFalse(is_merged(m))
        self.assertTrue(any("stale_base" in e for e in out["escalate"]))

    def test_conflict_escalates_and_leaves_worktree_clean(self):
        mid = self._done()
        # ミッションが書き換えた関数を main 側でも別の形に変える
        self._commit_on_main("def delete_note(note_id: int) -> bool:", "def delete_note(note_id) -> bool:",
                             "change delete_note signature")
        out = supervisor.step_deliver(self.f, self.cfg, dry_run=False)
        self.assertNotIn(mid, out["merged"])
        wt = self.f.worktree(mid)
        if any("競合" in e for e in out["escalate"]):
            self.assertEqual(gitx.tracked_dirty(wt), "")
            self.assertEqual(self.f.get_mission(mid).status, "done")

    def test_parallel_identical_change_counts_as_merged(self):
        a = self._done("a")
        b = self._done("b")
        out = supervisor.step_deliver(self.f, self.cfg, dry_run=False)
        self.assertEqual(sorted(out["merged"]), sorted([a, b]))
        self.assertEqual(gitx.tracked_dirty(self.repo), "")


if __name__ == "__main__":
    unittest.main()
