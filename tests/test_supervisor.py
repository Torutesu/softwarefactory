"""ファクトリークロック (tick / supervisor) の統合テスト。"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from factory.config import Config
from factory.init_scaffold import init_factory
from factory.state import Factory
from factory.supervisor import step_intake, tick


def _demo(tmp, autonomy=4):
    init_factory(Path(tmp), demo=True)
    cfg = Config.create(tmp, demo=True)
    cfg.autonomy_level = autonomy
    cfg.max_repair_rounds = 1
    return cfg, Factory(Path(tmp))


class TestTick(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_full_autonomous_cycle(self):
        """level4: 未割当Seed → ミッション作成 → 実行 → 自動マージ → 自動カナリアまで一巡。"""
        cfg, f = _demo(self.tmp, autonomy=4)
        res = tick(f, cfg)
        # intake: demo-app Seed から自動作成
        self.assertEqual(len(res["intake"]["created"]), 1)
        # execute: 作ったミッションが done に
        ran = res["execute"]["ran"]
        self.assertEqual([r["status"] for r in ran], ["done"])
        # deliver: マージ + canary pass
        self.assertEqual(len(res["deliver"]["merged"]), 1)
        self.assertEqual(res["deliver"]["canary"][0]["status"], "pass")
        self.assertEqual(res["needs_human"], [])
        m = f.get_mission(res["intake"]["created"][0])
        self.assertIn("merged to main", m.notes)

    def test_second_tick_is_idle(self):
        cfg, f = _demo(self.tmp, autonomy=4)
        tick(f, cfg)
        res2 = tick(f, cfg)  # Seed は全て割当済み, queued もない → 無活動で終わる
        self.assertEqual(res2["intake"]["created"], [])
        self.assertEqual(res2["execute"]["ran"], [])
        self.assertEqual(res2["deliver"]["merged"], [])

    def test_dry_run_touches_nothing(self):
        cfg, f = _demo(self.tmp, autonomy=4)
        res = tick(f, cfg, dry_run=True)
        self.assertTrue(all(str(x).startswith("(dry)") for x in res["intake"]["created"]))
        self.assertEqual(f.list_missions(), [])  # state 変化なし

    def test_level1_does_not_intake_nor_merge(self):
        cfg, f = _demo(self.tmp, autonomy=1)
        # 事前に手動でミッションを作っておく (人間が queue した状態)
        m = f.create_mission(title="手動", seed="demo-app", repo=cfg.projects["demo_app"].path)
        res = tick(f, cfg)
        self.assertEqual(res["intake"], {"skipped": "autonomy_level < 3"})
        self.assertEqual(len(res["execute"]["ran"]), 1)
        self.assertEqual(res["deliver"]["merged"], [])  # level1 は自動マージしない
        m2 = f.get_mission(m.id)
        self.assertEqual(m2.status, "done")
        self.assertNotIn("merged to main", m2.notes)

    def test_high_risk_needs_human(self):
        cfg, f = _demo(self.tmp, autonomy=4)
        f.create_mission(title="危ない変更", seed="demo-app", repo=cfg.projects["demo_app"].path, risk="high")
        res = tick(f, cfg)
        self.assertEqual(res["execute"]["ran"], [])
        self.assertTrue(any("high-risk" in e for e in res["needs_human"]))
        # 高リスクは queued のまま (人間が --confirm で回す)
        self.assertEqual(f.list_missions()[0].status, "queued")

    def test_intake_skips_claimed_seeds(self):
        cfg, f = _demo(self.tmp, autonomy=3)
        f.create_mission(title="既に割当済み", seed="demo-app", repo=cfg.projects["demo_app"].path)
        intake = step_intake(f, cfg, dry_run=False)
        self.assertEqual(intake["created"], [])

    def test_tick_event_recorded(self):
        cfg, f = _demo(self.tmp, autonomy=4)
        tick(f, cfg)
        kinds = [e["kind"] for e in f.state().get("events")]
        self.assertIn("tick", kinds)

    def test_tick_collects_when_configured(self):
        """collect.sources 設定時は tick が収集してドラフト化する (promote は人間)。"""
        from factory import collect as collect_mod

        collect_mod._http_get = lambda url, timeout=30: (
            "text/html", b"<html><title>t</title><body>signal body</body></html>")
        cfg, f = _demo(self.tmp, autonomy=4)
        cfg.collect = {"sources": [{"name": "blog", "type": "url", "url": "https://example.com/x"}]}
        out = tick(f, cfg)
        self.assertEqual(out["collect"]["created"], 1)
        self.assertEqual(len(collect_mod.list_drafts(f)), 1)
        out2 = tick(f, cfg)
        self.assertEqual(out2["collect"]["created"], 0)  # 同じ本文は dup
        # 未設定なら skipped
        cfg2, f2 = _demo(self.tmp, autonomy=4)
        self.assertIn("skipped", tick(f2, cfg2)["collect"])


class TestRepairDepth(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        init_factory(Path(self.tmp), demo=True)
        self.f = Factory(Path(self.tmp))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_depth_and_limit(self):
        from factory.supervisor import _repair_depth

        f = self.f
        m0 = f.create_mission(title="本体", seed="demo-app", repo="products/demo_app")
        m1 = f.create_mission(title="修復1", seed="demo-app", repo="products/demo_app",
                              notes=f"auto-created by canary repair-of:{m0.id}")
        m2 = f.create_mission(title="修復2", seed="demo-app", repo="products/demo_app",
                              notes=f"auto-created by canary repair-of:{m1.id}")
        self.assertEqual(_repair_depth(f, m0), 0)
        self.assertEqual(_repair_depth(f, m1), 1)
        self.assertEqual(_repair_depth(f, m2), 2)

        cfg = Config.create(self.tmp, demo=True)  # max_repair_rounds=1 相当
        cfg.max_repair_rounds = 1
        # depth 1 はまだ自動出荷可、depth 2 は上限で人間扱い (進め方の確認は deliver 側)
        self.assertLessEqual(_repair_depth(f, m1), cfg.max_repair_rounds)
        self.assertGreater(_repair_depth(f, m2), cfg.max_repair_rounds)
