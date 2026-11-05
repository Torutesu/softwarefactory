"""ミッション依存 (DAG) とプロジェクト別クォータ。"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from factory import scheduling, supervisor
from factory.config import Config
from factory.init_scaffold import init_factory
from factory.loop import run_mission
from factory.state import Factory, is_merged


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        init_factory(self.tmp, demo=True)
        self.cfg = Config.create(str(self.tmp), demo=True)
        self.f = Factory(self.tmp)
        self.repo = self.cfg.projects["demo_app"].path

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def new(self, title, after=None, **kw):
        deps = scheduling.validate_deps(self.f, "(new)", after or [])
        return self.f.create_mission(title, kind="update", seed="demo-app", repo=self.repo,
                                     depends_on=deps, project="demo_app", **kw)


class TestDeps(Base):
    def test_waits_until_dependency_merged(self):
        a = self.new("a")
        b = self.new("b", after=[a.id])
        self.assertEqual(scheduling.deps_state(self.f, b)[0], "waiting")
        with self.assertRaises(SystemExit):
            run_mission(self.f, b.id, cfg=self.cfg)
        self.cfg.autonomy_level = 2
        out = supervisor.tick(self.f, self.cfg)  # 1 巡目: a だけ走ってマージされる
        self.assertEqual([r["mission"] for r in out["execute"]["ran"]], [a.id])
        self.assertTrue(any(b.id in d for d in out["execute"]["deferred"]))
        self.assertTrue(is_merged(self.f.get_mission(a.id)))
        out2 = supervisor.tick(self.f, self.cfg)  # 2 巡目: b が走る
        self.assertEqual([r["mission"] for r in out2["execute"]["ran"]], [b.id])

    def test_failed_dependency_blocks(self):
        a = self.new("a")
        m = self.f.get_mission(a.id)
        m.status = "failed"
        self.f.save_mission(m)
        b = self.new("b", after=[a.id])
        st, why = scheduling.deps_state(self.f, b)
        self.assertEqual(st, "blocked")
        out = supervisor.step_execute(self.f, self.cfg, dry_run=False)
        self.assertTrue(any(b.id in e for e in out["escalate"]))
        self.assertEqual(self.f.get_mission(b.id).status, "queued")

    def test_unknown_and_cycle_rejected(self):
        with self.assertRaises(ValueError):
            scheduling.validate_deps(self.f, "(new)", ["M-nope"])
        a = self.new("a")
        b = self.new("b", after=[a.id])
        m = self.f.get_mission(a.id)
        with self.assertRaises(ValueError):
            scheduling.validate_deps(self.f, m.id, [b.id])

    def test_ignore_deps(self):
        a = self.new("a")
        b = self.new("b", after=[a.id])
        self.assertEqual(run_mission(self.f, b.id, cfg=self.cfg, ignore_deps=True).status, "done")

    def test_cli_after(self):
        from factory.cli import main

        a = self.new("a")
        cwd = os.getcwd()
        os.chdir(self.tmp)
        try:
            main(["mission", "new", "b", "--project", "demo_app", "--seed", "demo-app", "--after", a.id])
            with self.assertRaises(SystemExit):
                main(["mission", "new", "c", "--project", "demo_app", "--after", "M-missing"])
        finally:
            os.chdir(cwd)
        b = [m for m in self.f.list_missions() if m.title == "b"][0]
        self.assertEqual(b.depends_on, [a.id])


class TestQuotas(Base):
    def test_daily_limit_defers(self):
        self.cfg.projects["demo_app"].daily_mission_limit = 1
        a, b = self.new("a"), self.new("b")
        out = supervisor.step_execute(self.f, self.cfg, dry_run=False)
        self.assertEqual(len(out["ran"]), 1)
        self.assertEqual(len(out["deferred"]), 1)
        self.assertEqual(out["escalate"], [])  # 待てば進むものは needs_human にしない
        out2 = supervisor.step_execute(self.f, self.cfg, dry_run=False)
        self.assertEqual(out2["ran"], [])  # 今日の枠は使い切った
        self.assertEqual(scheduling.starts_today(self.f, "demo_app"), 1)
        self.assertEqual(sorted(m.status for m in (self.f.get_mission(a.id), self.f.get_mission(b.id))),
                         ["done", "queued"])

    def test_max_concurrent(self):
        self.cfg.projects["demo_app"].max_concurrent = 1
        running = self.new("running")
        m = self.f.get_mission(running.id)
        m.status = "dev"
        self.f.save_mission(m)
        self.new("b")
        with self.f.lock(running.id):  # 実行中 (ロックを保持)
            chosen, notes = scheduling.select_runnable(
                self.f, self.cfg, [x for x in self.f.list_missions() if x.status == "queued"])
        self.assertEqual(chosen, [])
        self.assertTrue(any("同時実行上限" in n for n in notes))

    def test_project_inferred_from_repo(self):
        m = self.f.create_mission("x", repo=self.repo)
        self.assertEqual(scheduling.project_of(self.cfg, m), "demo_app")


if __name__ == "__main__":
    unittest.main()
