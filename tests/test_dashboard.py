"""status --watch ダッシュボードの表示テスト。

- workers (usage): 日次/窓上限の実測表示
- review 待ち: done 未マージミッションをティア分類で数える (未分行は unknown = fail-safe)
- canary: 直近の canary_pass/canary_fail 実績
- needs_human 滞留: 最新 tick イベントからの取り出し
- collect 未処理ドラフト数
- 普通の status には滞留があるときだけ 1 行サマリが出る
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from factory import gitx
from factory.cli import _print_dashboard, _print_status
from factory.config import Config, WorkerConfig
from factory.init_scaffold import init_factory
from factory.state import Factory
from factory.usage import Usage


def _cfg(tmp) -> Config:
    cfg = Config(root=tmp)
    cfg.worker_order = ["mock"]
    cfg.workers = {"mock": WorkerConfig(type="mock", daily_call_limit=50)}
    return cfg


class TestDashboard(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        init_factory(Path(self.tmp), demo=True)
        self.f = Factory(Path(self.tmp))
        self.cfg = _cfg(self.tmp)
        repo = Path(self.tmp) / "products" / "demo_app"
        gitx.ensure_repo(repo)
        gitx.ensure_baseline_commit(repo)
        # done 未マージ (レビュー待ち) のミッション
        self.m = self.f.create_mission(title="直したがレビュー待ち", kind="update",
                                       seed="demo-app", repo="products/demo_app")
        self.m.status = "done"
        self.f.save_mission(self.m)
        # 実績: usage, canary, tick (needs_human)
        Usage(Path(self.tmp) / ".factory").record("mock", 2.0, ok=True)
        self.f.event("canary_fail", self.m.id)
        self.f.event("tick", json.dumps({"needs_human": ["repair limit: M999"], "intake": {}}))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _dashboard(self) -> str:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            _print_dashboard(self.f, self.cfg)
        return buf.getvalue()

    def test_usage_panel(self):
        out = self._dashboard()
        self.assertIn("mock", out)
        self.assertIn("day 1/50", out)

    def test_review_queue_unknown_tier(self):
        out = self._dashboard()
        # factory/<id> ブランチが無い → fail-safe で unknown ティア扱い
        self.assertIn("[unknown   ] 1", out)
        self.assertIn(self.m.id, out)

    def test_canary_and_needs_human(self):
        out = self._dashboard()
        self.assertIn("canary_fail", out)
        self.assertIn("repair limit: M999", out)

    def test_drafts_line(self):
        out = self._dashboard()
        self.assertIn("collect 未処理ドラフト", out)
        self.assertIn("0 件", out)

    def test_plain_status_compact_summary(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            _print_status(self.f)
        out = buf.getvalue()
        self.assertIn("review 待ち 1", out)
        self.assertIn("needs_human 1", out)


if __name__ == "__main__":
    unittest.main()
