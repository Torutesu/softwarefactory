"""ログのローテーション・gc (保持期間)・バックアップ/リストア。"""
from __future__ import annotations

import io
import json
import os
import shutil
import stat
import sys
import tarfile
import tempfile
import time
import unittest
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from factory import holdout as holdout_mod
from factory import housekeeping as hk
from factory import supervisor
from factory.config import Config
from factory.init_scaffold import init_factory
from factory.loop import run_mission
from factory.state import Factory


class TestRotate(unittest.TestCase):
    def test_rotate_and_keep(self):
        d = Path(tempfile.mkdtemp())
        p = d / "x.log"
        for _ in range(5):
            p.write_text("y" * 100, encoding="utf-8")
            self.assertTrue(hk.rotate_log(p, 50, keep=2))
        self.assertFalse(p.exists())
        self.assertTrue((d / "x.log.1").exists())
        self.assertTrue((d / "x.log.2").exists())
        self.assertFalse((d / "x.log.3").exists())

    def test_copytruncate_keeps_open_handle_working(self):
        d = Path(tempfile.mkdtemp())
        p = d / "launchd.log"
        fh = open(p, "a", encoding="utf-8")  # launchd のように開きっぱなし (O_APPEND)
        fh.write("a" * 200)
        fh.flush()
        self.assertTrue(hk.rotate_log(p, 100, keep=3, copytruncate=True))
        fh.write("after\n")
        fh.flush()
        fh.close()
        self.assertEqual(p.read_text(encoding="utf-8"), "after\n")
        self.assertEqual(len((d / "launchd.log.1").read_text(encoding="utf-8")), 200)

    def test_append_log_rotates(self):
        d = Path(tempfile.mkdtemp())
        p = d / "l.log"
        for _ in range(30):
            hk.append_log(p, "z" * 20, max_bytes=100, keep=2)
        self.assertLessEqual(p.stat().st_size, 121)
        self.assertTrue((d / "l.log.1").exists())


class TestGcBackup(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        init_factory(self.tmp, demo=True)
        self.cfg = Config.create(str(self.tmp), demo=True)
        self.f = Factory(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _done(self):
        m = self.f.create_mission("g", kind="update", seed="demo-app", repo=self.cfg.projects["demo_app"].path)
        run_mission(self.f, m.id, cfg=self.cfg)
        return m.id

    def test_gc_prunes_old_runs_and_prompts_only(self):
        mid = self._done()
        rep = self.f.reports_dir(mid)
        for i in range(8):
            (rep / f"run-2026010{i}-000000").mkdir(exist_ok=True)
        old = time.time() - 40 * 86400
        prompt = next((self.f.mission_dir(mid) / "prompts").iterdir())
        os.utime(prompt, (old, old))
        # 実行中扱いのミッションの prompt は残す
        active = self.f.create_mission("a", repo=self.cfg.projects["demo_app"].path)
        (self.f.mission_dir(active.id) / "prompts" / "iter-1.md").write_text("p", encoding="utf-8")
        os.utime(self.f.mission_dir(active.id) / "prompts" / "iter-1.md", (old, old))
        dry = hk.gc(self.f, self.cfg, dry_run=True)
        self.assertTrue(dry["removed"])
        self.assertTrue(any(r.startswith(str(rep)) for r in dry["removed"]))
        res = hk.gc(self.f, self.cfg)
        runs = [p for p in rep.iterdir() if p.name.startswith("run-")]
        self.assertEqual(len(runs), hk.DEFAULT_RETENTION["reports_keep_runs"])
        self.assertTrue((rep / "final.md").exists())
        self.assertFalse(prompt.exists())
        self.assertTrue((self.f.mission_dir(active.id) / "prompts" / "iter-1.md").exists())
        self.assertTrue((self.f.mission_dir(mid) / "mission.json").exists())
        self.assertTrue(holdout_mod.scenario_files(self.f, mid))
        self.assertEqual(hk.gc(self.f, self.cfg)["removed"], [])  # 冪等
        self.assertTrue(res["removed"])

    def test_backup_restore_roundtrip(self):
        mid = self._done()
        arc = hk.backup(self.f, keep=2)
        self.assertEqual(stat.S_IMODE(arc.stat().st_mode), 0o600)
        dst = Path(tempfile.mkdtemp())
        names = hk.restore(dst, arc)
        self.assertIn(".factory/config.json", names)
        f2 = Factory(dst)
        self.assertEqual(f2.get_mission(mid).status, "done")
        self.assertEqual(holdout_mod.verify_manifest(f2, mid), "ok")
        self.assertEqual(stat.S_IMODE((dst / "holdout").stat().st_mode), 0o700)
        with self.assertRaises(SystemExit):
            hk.restore(dst, arc)  # 既存 config は --force 無しでは上書きしない
        hk.restore(dst, arc, force=True)
        for _ in range(3):
            time.sleep(1.05)
            hk.backup(self.f, keep=2)
        self.assertEqual(len(list((self.f.state_dir / "backups").glob("factory-*.tar.gz"))), 2)

    def test_restore_rejects_unsafe_members(self):
        bad = Path(tempfile.mkdtemp()) / "bad.tar.gz"
        with tarfile.open(bad, "w:gz") as tar:
            for name in ("../evil", "/etc/evil", "other/evil"):
                info = tarfile.TarInfo(name)
                info.size = 1
                tar.addfile(info, io.BytesIO(b"x"))
        with self.assertRaises(SystemExit):
            hk.restore(Path(tempfile.mkdtemp()), bad)
        link = Path(tempfile.mkdtemp()) / "link.tar.gz"
        with tarfile.open(link, "w:gz") as tar:
            info = tarfile.TarInfo(".factory/config.json")
            info.type = tarfile.SYMTYPE
            info.linkname = "/etc/passwd"
            tar.addfile(info)
        with self.assertRaises(SystemExit):
            hk.restore(Path(tempfile.mkdtemp()), link)

    def test_tick_runs_housekeeping_once_a_day(self):
        out1 = supervisor.tick(self.f, self.cfg)
        self.assertIn("backup", out1["housekeeping"])
        out2 = supervisor.tick(self.f, self.cfg)
        self.assertIn("skipped", out2["housekeeping"])
        self.assertEqual(json.loads(json.dumps(self.f.state()["housekeeping"]))["last"],
                         time.strftime("%Y-%m-%d"))


if __name__ == "__main__":
    unittest.main()
