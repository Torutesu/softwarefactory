"""ブランチ全体のレビューで見つかった回帰の再発防止テスト。"""
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

from factory import canary as canary_mod
from factory import gitx, supervisor, util
from factory import holdout as holdout_mod
from factory.config import Config, WorkerConfig
from factory.init_scaffold import init_factory
from factory.loop import run_mission
from factory.state import Factory


def _demo(tmp: Path, level=1):
    init_factory(tmp, demo=True, force=True)
    cfg = Config.create(str(tmp), demo=True)
    cfg.autonomy_level = level
    return cfg, Factory(tmp)


class TestShutdownKeepsWork(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cfg, self.f = _demo(self.tmp)

    def tearDown(self):
        util.SHUTDOWN.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_existing_worktree_survives_shutdown(self):
        repo = self.tmp / "products" / "demo_app"
        gitx.ensure_repo(repo)
        gitx.ensure_baseline_commit(repo)
        wt = gitx.create_worktree(repo, self.tmp / "wt", "factory/x")
        (wt / "work-in-progress.txt").write_text("keep me", encoding="utf-8")
        util.SHUTDOWN.set()
        self.assertEqual(gitx.create_worktree(repo, wt, "factory/x"), wt)
        self.assertTrue((wt / "work-in-progress.txt").exists())

    def test_queued_missions_not_failed_after_shutdown(self):
        ids = [self.f.create_mission(f"m{i}", kind="update", seed="demo-app",
                                     repo=self.cfg.projects["demo_app"].path).id for i in range(2)]
        self.cfg.tick_concurrency = 1
        util.SHUTDOWN.set()
        out = supervisor.step_execute(self.f, self.cfg, dry_run=False)
        self.assertEqual(out["ran"], [])
        self.assertEqual([self.f.get_mission(i).status for i in ids], ["queued", "queued"])

    def test_exception_during_shutdown_requeues(self):
        m = self.f.create_mission("x", kind="update", seed="demo-app", repo=self.cfg.projects["demo_app"].path)

        def boom(*a, **k):
            util.SHUTDOWN.set()
            raise RuntimeError("worktree add failed: shutdown requested")

        with mock.patch("factory.loop.evaluate", side_effect=boom):
            m2 = run_mission(self.f, m.id, cfg=self.cfg)
        self.assertEqual((m2.status, m2.halt_reason), ("queued", "shutdown"))


class TestNoUsableWorker(unittest.TestCase):
    def test_tick_escalates_instead_of_failing_missions(self):
        tmp = Path(tempfile.mkdtemp())
        cfg, f = _demo(tmp)
        cfg.use_worker = "auto"
        cfg.workers = {"claude_code": WorkerConfig(type="claude", command="definitely-missing"),
                       "mock": WorkerConfig(type="mock")}
        cfg.worker_order = ["claude_code", "mock"]
        m = f.create_mission("x", kind="update", seed="demo-app", repo=cfg.projects["demo_app"].path)
        out = supervisor.step_execute(f, cfg, dry_run=False)
        self.assertTrue(any("no_worker_available" in e for e in out["escalate"]))
        self.assertEqual(f.get_mission(m.id).status, "queued")


class TestManifestLifecycle(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cfg, self.f = _demo(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_regenerate_after_failed_regen_is_not_tamper(self):
        m = self.f.create_mission("x", kind="update", seed="demo-app", repo=self.cfg.projects["demo_app"].path)
        self.assertEqual(run_mission(self.f, m.id, cfg=self.cfg).status, "done")
        # regen で一度シナリオを消した (生成失敗) 状態 → 次の run は生成し直して通る
        for p in holdout_mod.scenario_files(self.f, m.id):
            p.unlink()
        cur = self.f.get_mission(m.id)
        cur.status = "queued"
        self.f.save_mission(cur)
        m2 = run_mission(self.f, m.id, cfg=self.cfg)
        self.assertEqual(m2.status, "done", m2.halt_reason)

    def test_rerecord_cli_accepts_hand_edit(self):
        m = self.f.create_mission("x", kind="update", seed="demo-app", repo=self.cfg.projects["demo_app"].path)
        run_mission(self.f, m.id, cfg=self.cfg)
        p = holdout_mod.scenario_files(self.f, m.id)[0]
        d = json.loads(p.read_text(encoding="utf-8"))
        d["title"] = d["title"] + " (人間が修正)"
        p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(holdout_mod.verify_manifest(self.f, m.id), "tampered")
        from factory.cli import main

        cwd = os.getcwd()
        os.chdir(self.tmp)
        try:
            main(["holdout", "rerecord", m.id])
        finally:
            os.chdir(cwd)
        self.assertEqual(holdout_mod.verify_manifest(self.f, m.id), "ok")


class TestCanaryTamper(unittest.TestCase):
    def test_tampered_holdout_escalates_without_revert(self):
        tmp = Path(tempfile.mkdtemp())
        cfg, f = _demo(tmp, level=4)
        m = f.create_mission("x", kind="update", seed="demo-app", repo=cfg.projects["demo_app"].path)
        run_mission(f, m.id, cfg=cfg)
        out = supervisor.tick(f, cfg)
        self.assertIn(m.id, out["deliver"]["merged"])
        p = holdout_mod.scenario_files(f, m.id)[0]
        p.write_text(p.read_text(encoding="utf-8") + " ", encoding="utf-8")
        res = canary_mod.run_canary(f, m.id, cfg=cfg, fix=True)
        self.assertEqual(res["status"], "escalate")
        self.assertNotIn("revert", res)
        repo = tmp / cfg.projects["demo_app"].path
        self.assertNotIn("Revert", gitx.log(repo, n=3))


if __name__ == "__main__":
    unittest.main()
