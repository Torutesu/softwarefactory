"""社内 SWE ベンチ (factory bench) のテスト。

- タスクの読み込み/検証 (共通出題の構造)
- dev 用途の variant 固定 (bench_lock_dev → フォールバック禁止)
- mock ワーカー 2 体での実行 → 結果保存・集計・提案
- status から bench ミッションが隠れること
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

from factory import bench as bench_mod
from factory import gitx
from factory.cli import _print_status
from factory.config import Config, WorkerConfig
from factory.state import Factory
from factory.workers.router import Router


def _cfg(tmp: str, names=("mock_a", "mock_b")) -> Config:
    cfg = Config(root=tmp)
    cfg.use_worker = "auto"
    cfg.worker_order = list(names)
    cfg.workers = {n: WorkerConfig(type="mock") for n in names}
    return cfg


def _write_task(tmp: str, tid: str = "demo-notes") -> Path:
    d = Path(tmp) / ".factory" / "bench" / "tasks"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{tid}.json"
    p.write_text(json.dumps({
        "id": tid,
        "title": f"[bench] {tid} NOTES.md",
        "repo": "products/demo_app",
        "spec": ("# bench タスク\n\nNOTES.md を作成し、1 行目に正確に `bench demo notes` と書く。\n\n"
                 "```mock-action\n"
                 '{"action": "write", "file": "NOTES.md", "content": "bench demo notes\\n"}\n'
                 "```\n"),
        "build": {},
        "max_iterations": 1,
        "pass_score": 80,
        "scenarios": [
            {"id": "SC-001", "title": "NOTES.md が書かれている", "weight": 1.0,
             "steps": [{"name": "notes",
                        "cmd": "test -f NOTES.md && grep -qx 'bench demo notes' NOTES.md",
                        "expect_exit": 0}]},
        ],
    }, ensure_ascii=False), encoding="utf-8")
    return p


class TestBench(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.f = Factory(Path(self.tmp))
        # 対象リポジトリ (demo 相当) — git リポジトリであることまでは既存パイプラインが面倒を見ないので用意
        repo = Path(self.tmp) / "products" / "demo_app"
        (repo / "src").mkdir(parents=True)
        (repo / "src" / "main.py").write_text("print('hi')\n", encoding="utf-8")
        gitx.ensure_repo(repo)
        gitx.ensure_baseline_commit(repo)
        _write_task(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_pinned_dev_locked(self):
        """bench_lock_dev で dev は variant 1 候補に固定され、judge は通常ルーティング。"""
        cfg = _cfg(self.tmp)
        p = bench_mod._pinned_cfg(cfg, "mock_b")
        r = Router(p, None)
        self.assertEqual(r.candidates("dev"), ["mock_b"])
        self.assertEqual(len(r.candidates("judge")), 2)  # 器具 (judge) は航空便どおり全候補

    def test_load_task_validation(self):
        tasks = bench_mod.load_tasks(self.f)
        self.assertEqual([t["id"] for t in tasks], ["demo-notes"])
        # repo が無い/シナリオが無いタスクは検証で落ちる
        bad = dict(tasks[0], repo="products/no_such_repo")
        with self.assertRaises(bench_mod.BenchError):
            bench_mod._validate_task(self.f, bad)
        bad2 = dict(tasks[0], scenarios=[])
        with self.assertRaises(bench_mod.BenchError):
            bench_mod._validate_task(self.f, bad2)

    def test_dry_run_creates_nothing(self):
        cfg = _cfg(self.tmp)
        res = bench_mod.run_bench(self.f, cfg, dry_run=True)
        self.assertTrue(res["dry_run"])
        self.assertEqual(len(res["plan"]), 2)  # 1 task × 2 workers
        self.assertFalse(list(self.f.missions_dir.iterdir()) if self.f.missions_dir.exists() else [])

    def test_unknown_task_raises(self):
        with self.assertRaises(bench_mod.BenchError):
            bench_mod.run_bench(self.f, _cfg(self.tmp), task_ids=["ghost"])

    def test_run_two_mock_workers(self):
        cfg = _cfg(self.tmp)
        res = bench_mod.run_bench(self.f, cfg)
        rows = res["rows"]
        self.assertEqual(len(rows), 2)
        self.assertEqual({r["variant"] for r in rows}, {"mock_a", "mock_b"})
        for r in rows:
            self.assertEqual(r["status"], "done")
            self.assertEqual(r["iters"], 1)
            self.assertEqual(r["score"], 100)
            self.assertTrue(r["mission"].startswith("M"))
        # 結果が保存されている
        self.assertTrue(list(bench_mod.results_dir(self.f).glob("*-bench.json")))
        # bench ミッションが kind="bench" で作られている
        bms = [m for m in self.f.list_missions() if m.kind == "bench"]
        self.assertEqual(len(bms), 2)
        # 提案が描ける
        md = bench_mod.render_markdown(res)
        self.assertIn("dev", md)
        self.assertIn("purpose_workers", md)

    def test_prepared_spec_and_holdout_are_shared(self):
        """全 variant で spec/holdout が同一出題であること (比較の公正さ)。"""
        cfg = _cfg(self.tmp)
        bench_mod.run_bench(self.f, cfg, workers=["mock_a"])
        bench_mod.run_bench(self.f, cfg, workers=["mock_b"])
        ms = [m for m in self.f.list_missions() if m.kind == "bench"]
        specs = set()
        scen = set()
        for m in ms:
            specs.add(self.f.spec_file(m.id).read_text(encoding="utf-8"))
            for p in self.f.holdout_dir_for(m.id).glob("*.json"):
                scen.add(p.read_text(encoding="utf-8"))
        self.assertEqual(len(specs), 1)
        self.assertEqual(len(scen), 1)

    def test_status_hides_bench_missions(self):
        cfg = _cfg(self.tmp)
        bench_mod.run_bench(self.f, cfg, workers=["mock_a"])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            _print_status(self.f)
        out = buf.getvalue()
        self.assertNotIn("[bench]", out)
        self.assertIn("bench ミッション", out)
        # --all なら表示する (ヘッダ行に通常ミッション列が出る)
        buf2 = io.StringIO()
        with contextlib.redirect_stdout(buf2):
            _print_status(self.f, show_bench=True)
        self.assertIn("[bench]", buf2.getvalue())


if __name__ == "__main__":
    unittest.main()
