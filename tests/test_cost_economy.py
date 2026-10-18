"""コスト経済 (Uber のコスト方程式対応) のテスト。

- purpose_workers による用途別ルーティング (Price/token レバー)
- Iteration/Mission への tokens・cost 記録 (成果単価)
- usage の使用率 (nudge 用) と waste_report (セッション分析)
- dev プロンプトのキャッシュ映える構造と反復2以降の spec 再掲省略
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from factory.config import Config, WorkerConfig
from factory.state import Factory, Iteration, Mission, mission_cost_usd
from factory.usage import Usage
from factory.workers.router import Router


def _cfg(tmp: str, **kw) -> Config:
    cfg = Config(root=tmp)
    cfg.use_worker = "auto"
    cfg.worker_order = ["mock_a", "mock_b"]
    cfg.workers = {"mock_a": WorkerConfig(type="mock"), "mock_b": WorkerConfig(type="mock")}
    for k, v in kw.items():
        setattr(cfg, k, v)
    return cfg


class TestPurposeWorkers(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.f = Factory(Path(self.tmp))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_spec_routed_to_preferred(self):
        cfg = _cfg(self.tmp, purpose_workers={"spec": ["mock_b"]})
        r = Router(cfg, Usage(self.f.state_dir))
        self.assertEqual(r.candidates("spec"), ["mock_b", "mock_a"])
        self.assertEqual(r.candidates("dev"), ["mock_a", "mock_b"])  # dev は既定順のまま

    def test_all_purposes_routed(self):
        cfg = _cfg(self.tmp, purpose_workers={"spec": ["mock_b"], "judge": ["mock_a"]})
        r = Router(cfg, Usage(self.f.state_dir))
        self.assertEqual(r.candidates("judge"), ["mock_a", "mock_b"])
        self.assertEqual(r.candidates("holdout"), ["mock_a", "mock_b"])

    def test_preferred_but_unavailable_falls_back(self):
        # 使用不能な worker 名が type としては存在しない → 無視され既定順
        cfg = _cfg(self.tmp, purpose_workers={"spec": ["ghost"]})
        r = Router(cfg, Usage(self.f.state_dir))
        self.assertEqual(r.candidates("spec"), ["mock_a", "mock_b"])

    def test_judge_worker_still_honored(self):
        cfg = _cfg(self.tmp, judge_worker="mock_b")
        r = Router(cfg, Usage(self.f.state_dir))
        self.assertEqual(r.candidates("judge"), ["mock_b", "mock_a"])


class TestMissionCost(unittest.TestCase):
    def test_mission_cost_and_roundtrip(self):
        m = Mission(id="MC1", title="x")
        m.iterations = [
            Iteration(n=1, worker="claude_code", started_utc="t", tokens_in=1200, tokens_out=800, cost_usd=0.05),
            Iteration(n=2, worker="claude_code", started_utc="t", tokens_in=900, tokens_out=300, cost_usd=None),
        ]
        self.assertAlmostEqual(mission_cost_usd(m) or 0, 0.05)
        d = m.to_dict()
        m2 = Mission.from_dict(d)
        self.assertEqual(m2.iterations[0].tokens_in, 1200)
        self.assertAlmostEqual(m2.iterations[0].cost_usd or 0, 0.05)
        self.assertIsNone(m2.iterations[1].cost_usd)

    def test_no_cost_is_none(self):
        m = Mission(id="MC2", title="y")
        m.iterations = [Iteration(n=1, worker="mock", started_utc="t")]
        self.assertIsNone(mission_cost_usd(m))


class TestUsageRatio(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.u = Usage(Path(self.tmp) / ".factory")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_usage_ratio_day_and_window(self):
        cfg = WorkerConfig(type="mock", daily_call_limit=4, window_call_limit=0)
        self.u.record("mock", 1.0, True)
        self.u.record("mock", 1.0, True)
        self.assertAlmostEqual(self.u.usage_ratio("mock", cfg), 0.5)
        cfg2 = WorkerConfig(type="mock", daily_call_limit=4, window_call_limit=2)
        # window 3回目 → window 側 1.0 が支配的
        self.u.record("mock", 1.0, True)
        self.assertGreaterEqual(self.u.usage_ratio("mock", cfg2), 1.0)

    def test_no_limits_ratio_zero(self):
        cfg = WorkerConfig(type="mock")
        self.u.record("mock", 1.0, True)
        self.assertEqual(self.u.usage_ratio("mock", cfg), 0.0)


class TestWasteReport(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.u = Usage(Path(self.tmp) / ".factory")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _day(self, day_name, worker, calls=1, fails=0, tokens_in=0, tokens_out=0, cost=0.0):
        p = (Path(self.tmp) / ".factory" / "usage" / f"{day_name}.json")
        p.parent.mkdir(parents=True, exist_ok=True)
        import json
        d = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"workers": {}}
        d["workers"][worker] = {"calls": calls, "fails": fails, "seconds": 60,
                                "tokens_in": tokens_in, "tokens_out": tokens_out, "cost_usd": cost}
        p.write_text(json.dumps(d), encoding="utf-8")

    def test_failure_rate_hint(self):
        import datetime as dt
        self._day(dt.date.today().isoformat(), "w1", calls=5, fails=3)
        text = self.u.waste_report()
        self.assertIn("w1", text)
        self.assertIn("失敗率", text)

    def test_cost_jump_hint(self):
        import datetime as dt
        today = dt.date.today()
        self._day(today.isoformat(), "w2", calls=5, cost=10.0)
        self._day((today - dt.timedelta(days=1)).isoformat(), "w2", calls=4, cost=1.0)
        self._day((today - dt.timedelta(days=2)).isoformat(), "w2", calls=4, cost=1.0)
        text = self.u.waste_report()
        self.assertIn("w2", text)
        self.assertIn("倍", text)

    def test_mission_section(self):
        import datetime as dt
        today = dt.date.today().isoformat()
        self._day(today, "mock", calls=2, cost=0.0)

        class _F:
            def list_missions(self):
                mf = Mission(id="MF1", title="f", status="failed")
                mc = Mission(id="MC1", title="c", status="done", max_iterations=3)
                mc.iterations = [Iteration(n=1, worker="mock", started_utc=today, cost_usd=0.2),
                                 Iteration(n=2, worker="mock", started_utc=today, cost_usd=0.3),
                                 Iteration(n=3, worker="mock", started_utc=today, cost_usd=0.1)]
                return [mf, mc]

        text = self.u.waste_report(factory=_F())
        self.assertIn("MF1", text)          # 失敗ミッション
        self.assertIn("MC1", text)          # 上限ギリギリ合格
        self.assertIn("$0.6", text)         # 実装コスト合計 (0.2+0.3+0.1)
        self.assertIn("反復平均 1.5", text)  # n_done=2, iterations 0+3


if __name__ == "__main__":
    unittest.main()
