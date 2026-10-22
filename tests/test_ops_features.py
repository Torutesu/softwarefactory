"""運用機能 (F1 孤児回収 / F2 graceful shutdown / F4 doctor / F5 judge の実差分 / F7 selftest / F8 PAUSE・再読込)。"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from factory import doctor, gitx, supervisor
from factory.config import Config, save as save_config
from factory.harness.judge import judge_prompt
from factory.harness.runner import HarnessResult
from factory.init_scaffold import init_factory
from factory.loop import run_mission
from factory.state import Factory, Mission


def _demo(tmp: Path, level: int = 1):
    init_factory(tmp, demo=True, force=True)
    cfg = Config.create(str(tmp), demo=True)
    cfg.autonomy_level = level
    save_config(cfg)
    return cfg, Factory(tmp)


class TestRecover(unittest.TestCase):  # F1
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cfg, self.f = _demo(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _orphan(self):
        m = self.f.create_mission("o", kind="update", seed="demo-app", repo=self.cfg.projects["demo_app"].path)
        m.status = "dev"
        self.f.save_mission(m)
        hd = self.f.holdout_dir_for(m.id)
        hd.mkdir(parents=True, exist_ok=True)
        os.chmod(hd, 0)
        return m

    def test_orphan_is_requeued_and_completes(self):
        m = self._orphan()
        out = supervisor.step_recover(self.f, self.cfg, dry_run=False)
        self.assertEqual(out["recovered"], [m.id])
        m2 = self.f.get_mission(m.id)
        self.assertEqual((m2.status, m2.halt_reason, m2.recover_count), ("queued", "recovered", 1))
        self.assertNotEqual(os.stat(self.f.holdout_dir_for(m.id)).st_mode & 0o777, 0)
        self.assertEqual(run_mission(self.f, m.id, cfg=self.cfg).status, "done")

    def test_running_mission_is_not_touched(self):
        m = self._orphan()
        with self.f.lock(m.id):
            out = supervisor.step_recover(self.f, self.cfg, dry_run=False)
        self.assertEqual(out["recovered"], [])
        self.assertEqual(self.f.get_mission(m.id).status, "dev")
        os.chmod(self.f.holdout_dir_for(m.id), 0o755)

    def test_crash_loop_escalates(self):
        m = self._orphan()
        for _ in range(self.cfg.max_recover_attempts):
            supervisor.step_recover(self.f, self.cfg, dry_run=False)
            cur = self.f.get_mission(m.id)
            cur.status = "dev"
            self.f.save_mission(cur)
        out = supervisor.step_recover(self.f, self.cfg, dry_run=False)
        self.assertTrue(out["escalate"])
        self.assertEqual(self.f.get_mission(m.id).halt_reason, "crash_loop")

    def test_mission_reset_cli(self):
        m = self._orphan()
        os.chmod(self.f.holdout_dir_for(m.id), 0o755)
        cur = self.f.get_mission(m.id)
        cur.status, cur.halt_reason = "failed", "noprogress"
        self.f.save_mission(cur)
        env = dict(os.environ, PYTHONPATH=ROOT)
        r = subprocess.run([sys.executable, "-m", "factory", "mission", "reset", m.id], cwd=str(self.tmp),
                           capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.f.get_mission(m.id).status, "queued")


class TestPauseAndReload(unittest.TestCase):  # F8
    def test_pause_file_stops_tick(self):
        tmp = Path(tempfile.mkdtemp())
        cfg, f = _demo(tmp, level=4)
        supervisor.pause_file(f).touch()
        out = supervisor.tick(f, cfg)
        self.assertTrue(out.get("paused"))
        self.assertEqual(f.list_missions(), [])  # intake も走らない
        supervisor.pause_file(f).unlink()
        self.assertEqual(len(supervisor.tick(f, cfg)["intake"]["created"]), 1)

    def test_supervisor_reloads_config_each_tick(self):
        tmp = Path(tempfile.mkdtemp())
        cfg, f = _demo(tmp, level=1)
        seen = []
        real_tick = supervisor.tick

        def spy(factory, c, dry_run=False):
            seen.append(c.autonomy_level)
            if len(seen) == 1:
                d = json.loads(c.state_file.read_text(encoding="utf-8"))
                d["autonomy_level"] = 3
                c.state_file.write_text(json.dumps(d), encoding="utf-8")
            if len(seen) == 2:
                c.state_file.write_text("{broken", encoding="utf-8")  # 壊れたら直前の設定で続行
            return real_tick(factory, c, dry_run)

        with mock.patch.object(supervisor, "tick", spy):
            supervisor.supervise(f, cfg, interval=0, max_ticks=3)
        self.assertEqual(seen, [1, 3, 3])


class TestGracefulShutdown(unittest.TestCase):  # F2
    def test_sigterm_requeues_running_mission_and_kills_worker(self):
        tmp = Path(tempfile.mkdtemp())
        cfg, f = _demo(tmp, level=1)
        # dev ワーカー: 長く眠る CLI (claude 型の偽物)。SIGTERM で子ごと止まるはず
        stub = tmp / "slowclaude"
        stub.write_text("#!/bin/sh\nsleep 120\n", encoding="utf-8")
        stub.chmod(0o755)
        d = json.loads(cfg.state_file.read_text(encoding="utf-8"))
        d["use_worker"] = "slow"
        d["worker_order"] = ["slow"]
        d["workers"]["slow"] = {"type": "claude", "command": str(stub), "timeout": 300}
        d["purpose_workers"] = {"spec": ["mock"], "holdout": ["mock"], "judge": ["mock"]}
        d["worker_order"] = ["slow", "mock"]
        d["allow_mock_fallback"] = True
        cfg.state_file.write_text(json.dumps(d), encoding="utf-8")
        m = f.create_mission("slow", kind="update", seed="demo-app", repo=cfg.projects["demo_app"].path)
        env = dict(os.environ, PYTHONPATH=ROOT)
        p = subprocess.Popen([sys.executable, "-m", "factory", "supervisor", "--interval", "5"], cwd=str(tmp),
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
        try:
            deadline = time.time() + 60
            while time.time() < deadline:
                if f.get_mission(m.id).status == "dev":
                    break
                time.sleep(0.3)
            time.sleep(1.5)  # dev ワーカー (sleep 120) が起動するのを待つ
            self.assertEqual(f.get_mission(m.id).status, "dev")
            t0 = time.time()
            p.send_signal(signal.SIGTERM)
            out, _ = p.communicate(timeout=40)
            self.assertLess(time.time() - t0, 30, out)
            self.assertEqual(p.returncode, 0, out)
        finally:
            if p.poll() is None:
                p.kill()
        m2 = f.get_mission(m.id)
        self.assertEqual((m2.status, m2.halt_reason), ("queued", "shutdown"), out)
        self.assertNotEqual(os.stat(f.holdout_dir_for(m.id)).st_mode & 0o777, 0)
        ps = subprocess.run(["ps", "-eo", "cmd"], capture_output=True, text=True).stdout
        self.assertNotIn(str(stub), ps)


class TestDoctor(unittest.TestCase):  # F4
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cfg, self.f = _demo(self.tmp)
        self.cwd = os.getcwd()
        os.chdir(self.tmp)

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _by_name(self):
        # このマシンに入っているワーカー CLI (やスタブ) に左右されないよう、git 以外は未導入扱い
        real_which = doctor.which
        with mock.patch("os.geteuid", return_value=501), \
                mock.patch.object(doctor, "which", lambda c: real_which(c) if c == "git" else None):
            return {c.name: c for c in doctor.run_checks()}

    def test_demo_has_no_errors_as_normal_user(self):
        checks = self._by_name()
        errs = [c for c in checks.values() if c.level == doctor.ERROR]
        # demo は use_worker=mock なので実ワーカー不在はエラーにしない
        self.assertEqual(errs, [], errs)

    def test_root_is_error(self):
        with mock.patch("os.geteuid", return_value=0):
            checks = {c.name: c for c in doctor.run_checks()}
        self.assertEqual(checks["user"].level, doctor.ERROR)

    def test_stale_seal_orphan_pause_and_bad_config(self):
        m = self.f.create_mission("o", kind="update", seed="demo-app", repo=self.cfg.projects["demo_app"].path)
        m.status = "dev"
        self.f.save_mission(m)
        hd = self.f.holdout_dir_for(m.id)
        hd.mkdir(parents=True)
        os.chmod(hd, 0)
        supervisor.pause_file(self.f).touch()
        checks = self._by_name()
        self.assertEqual(checks["holdout"].level, doctor.WARN)
        self.assertEqual(checks["missions"].level, doctor.WARN)
        self.assertEqual(checks["pause"].level, doctor.WARN)
        os.chmod(hd, 0o755)
        d = json.loads(self.cfg.state_file.read_text(encoding="utf-8"))
        d["autonomy_level"] = 9
        self.cfg.state_file.write_text(json.dumps(d), encoding="utf-8")
        checks = self._by_name()
        self.assertEqual(checks["config:autonomy_level"].level, doctor.ERROR)
        self.assertEqual(doctor.exit_code(list(checks.values())), 2)

    def test_no_real_worker_is_error_when_not_mock(self):
        d = json.loads(self.cfg.state_file.read_text(encoding="utf-8"))
        d["use_worker"] = "auto"
        d["workers"] = {"claude_code": {"type": "claude", "command": "definitely-missing-claude"}}
        d["worker_order"] = ["claude_code"]
        d["purpose_workers"] = {}
        self.cfg.state_file.write_text(json.dumps(d), encoding="utf-8")
        checks = self._by_name()
        self.assertEqual(checks["workers"].level, doctor.ERROR)


class TestJudgeSeesRealDiff(unittest.TestCase):  # F5
    def test_empty_diff_is_flagged(self):
        p = judge_prompt(Mission(id="M1", title="t"), "spec", HarnessResult(), "全部完了しました", diff_text="")
        self.assertIn("差分なし", p)
        self.assertIn("自己申告 (参考情報", p)

    def test_diff_summary_of_worktree(self):
        d = Path(tempfile.mkdtemp())
        gitx.ensure_repo(d)
        (d / "a.py").write_text("x = 1\n", encoding="utf-8")
        gitx.ensure_baseline_commit(d)
        self.assertEqual(gitx.diff_summary(d, "main"), "")
        (d / "a.py").write_text("x = 2\n", encoding="utf-8")
        (d / "poetry.lock").write_text("lock\n", encoding="utf-8")
        s = gitx.diff_summary(d, "main")
        self.assertIn("a.py", s)
        self.assertIn("+x = 2", s)
        self.assertNotIn("poetry.lock", s)
        self.assertLessEqual(len(gitx.diff_summary(d, "main", max_chars=20)), 60)

    def test_loop_passes_diff_to_judge(self):
        tmp = Path(tempfile.mkdtemp())
        cfg, f = _demo(tmp)
        m = f.create_mission("x", kind="update", seed="demo-app", repo=cfg.projects["demo_app"].path)
        seen = {}
        from factory import loop as loop_mod

        real = loop_mod.run_judge

        def spy(*a, **kw):
            seen["diff"] = kw.get("diff_text")
            return real(*a, **kw)

        with mock.patch.object(loop_mod, "run_judge", spy):
            run_mission(f, m.id, cfg=cfg)
        self.assertIn("app.py", seen["diff"])
        self.assertNotIn(str(f.holdout_dir), seen["diff"])


class TestSelftest(unittest.TestCase):  # F7
    def test_selftest_passes(self):
        from factory import selftest

        ok, results, root = selftest.run()
        self.assertTrue(ok, results)
        self.assertFalse(root.exists())

    def test_selftest_detects_broken_pipeline(self):
        from factory import selftest

        with mock.patch("factory.harness.runner._check_step") as cs:
            from factory.harness.runner import StepResult

            cs.return_value = StepResult(name="x", cmd="x", ok=False, code=1)
            ok, results, _ = selftest.run()
        self.assertFalse(ok)
        self.assertIn("mission run", [r[0] for r in results if not r[1]])


if __name__ == "__main__":
    unittest.main()
