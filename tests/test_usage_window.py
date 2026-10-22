"""使用量: 日次 + rolling window 二段予算のテスト。"""
from __future__ import annotations

import datetime as dt
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from factory.config import WorkerConfig
from factory.usage import Usage


class TestUsageWindow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.usage = Usage(__import__("pathlib").Path(self.tmp))

    def test_daily_budget(self):
        wc = WorkerConfig(type="mock", daily_call_limit=2)
        self.assertFalse(self.usage.budget_exceeded("mock", wc))
        self.usage.record("mock", 0.1, True)
        self.assertFalse(self.usage.budget_exceeded("mock", wc))
        self.usage.record("mock", 0.1, True)
        self.assertTrue(self.usage.budget_exceeded("mock", wc))

    def test_window_budget(self):
        wc = WorkerConfig(type="mock", window_call_limit=2, window_minutes=60)
        now = dt.datetime.now(dt.timezone.utc)
        # 2 コール → 上限到達
        self.usage.record("mock", 0.1, True)
        self.usage.record("mock", 0.1, True)
        self.assertTrue(self.usage.window_budget_exceeded("mock", wc, now=now))
        # 別のワーカー名には影響しない
        self.assertFalse(self.usage.window_budget_exceeded("other", wc, now=now))
        # 61 分前の呼び出しは 60 分窓に含まれない
        day = now.date().isoformat()
        path = self.usage._file(day)
        import json

        d = json.load(open(path, encoding="utf-8"))
        old = (now - dt.timedelta(minutes=61)).isoformat()
        d["workers"]["mock"]["ts_list"] = [old, old]
        json.dump(d, open(path, "w", encoding="utf-8"))
        self.assertFalse(self.usage.window_budget_exceeded("mock", wc, now=now))

    def test_report_has_window(self):
        self.usage.record("mock", 1.0, True, "dev", tokens_in=10, tokens_out=5, cost_usd=0.25)
        rep = self.usage.report(days=1)
        self.assertIn("| mock |", rep)
        self.assertIn("0.25", rep)
