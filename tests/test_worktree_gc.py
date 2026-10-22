"""worktree の生涯管理 (`factory worktrees`) のテスト。

- done / bench ミッションの worktree が掃除対象、failed は既定で保留
- --dry-run では何も消さない
- --force-failed で failed も消せる
- main worktree / 関連のない worktree には触れない (worktrees() の parse も確認)
"""
from __future__ import annotations

import contextlib
import io
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from factory import gitx
from factory.cli import cmd_worktrees
from factory.config import Config
from factory.init_scaffold import init_factory
from factory.state import Factory


def _cfg(tmp) -> Config:
    cfg = Config.create(tmp, demo=True)
    cfg.use_worker = "mock"
    return cfg


class _Args:
    def __init__(self, dry_run=False, force_failed=False):
        self.dry_run = dry_run
        self.force_failed = force_failed


class TestGitxWorktrees(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.repo = Path(self.tmp) / "repo"
        self.repo.mkdir()
        import subprocess
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "--allow-empty", "-q",
                        "-m", "baseline"], check=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_parse_porcelain(self):
        wt = self.repo / "factory" / "x1"
        gitx.create_worktree(self.repo, wt, detached=True)
        listings = gitx.worktrees(self.repo)
        paths = {w["path"].resolve() for w in listings}
        self.assertIn(wt.resolve(), paths)  # macOS の /var → /private/var を吸収
        # detached worktree には branch が載らない (refs/heads/ 接頭辞は剥がして返す)
        x = [w for w in listings if w["path"].resolve() == wt.resolve()][0]
        self.assertNotIn("branch", x)


class TestWorktreesCommand(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        init_factory(Path(self.tmp), demo=True)
        self.f = Factory(Path(self.tmp))
        self.cfg = _cfg(self.tmp)
        self.root_prev = os.getcwd()
        os.chdir(self.tmp)  # CLI コマンドの設定探索は cwd 基準
        self.repo = Path(self.tmp) / "products" / "demo_app"
        gitx.ensure_repo(self.repo)
        gitx.ensure_baseline_commit(self.repo, "factory: baseline")
        # done / failed / bench の 3 ミッションに worktree を直で作る
        self.mids = {}
        for status, kind in (("done", "update"), ("failed", "update"), ("done", "bench")):
            m = self.f.create_mission(title=f"t-{status}-{kind}", kind=kind,
                                      seed="demo-app", repo="products/demo_app")
            gitx.create_worktree(self.repo, self.f.worktree(m.id), detached=True)
            self.f.worktree(m.id).joinpath(".marker").write_text("x", encoding="utf-8")
            m.status = status
            if status == "done" and kind == "update":
                m.notes = "merged to main"
            self.f.save_mission(m)
            self.mids[(status, kind)] = m.id

    def tearDown(self):
        os.chdir(ROOT)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _wt_exists(self, mid) -> bool:
        wt = self.f.worktree(mid)
        return (wt / ".git").exists()

    def _run(self, **kw) -> str:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmd_worktrees(_Args(**kw))
        return buf.getvalue()

    def test_dry_run_reports_without_removing(self):
        out = self._run(dry_run=True)
        self.assertIn(self.mids[("done", "update")], out)
        self.assertIn(self.mids[("done", "bench")], out)
        self.assertIn("(dry-run", out)
        for mid in self.mids.values():
            self.assertTrue(self._wt_exists(mid))

    def test_prune_removes_done_and_bench_keeps_failed(self):
        out = self._run()
        self.assertTrue(self._wt_exists(self.mids[("failed", "update")]))
        self.assertIn("保留", out)
        self.assertFalse(self._wt_exists(self.mids[("done", "update")]))
        self.assertFalse(self._wt_exists(self.mids[("done", "bench")]))
        kinds = [e["kind"] for e in self.f.state()["events"]]
        self.assertIn("worktree_pruned", kinds)

    def test_force_failed_removes_failed(self):
        self._run(force_failed=True)
        self.assertFalse(self._wt_exists(self.mids[("failed", "update")]))
        # bench/done も同様に消える
        self.assertFalse(self._wt_exists(self.mids[("done", "update")]))

    def test_no_targets(self):
        # 全 worktree を事前に消しておく (failed も含めて)
        self._run(force_failed=True)
        out2 = self._run()  # もう対象がない
        self.assertIn("掃除対象の worktree はありません", out2)


if __name__ == "__main__":
    unittest.main()
