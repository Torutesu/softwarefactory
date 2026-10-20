"""ベンチの拡張機能 (purpose=spec / variant@model) のテスト。

- variant 文字列のパース (worker@model)
-purpose=spec タスクの検証 (seed / reference_spec)
- spec 用途のピン留め (bench_lock_purpose) — 対象用途のみ 1 候補に絞る
- purpose=spec 実行 E2E: spec 生成 → 参照 spec 照合採点 → done/score 反映
- dev タスクでの variant@model 実行 (ラベルと notes への反映)
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
from factory.config import Config, WorkerConfig
from factory.init_scaffold import init_factory
from factory.state import Factory
from factory.workers.base import WorkerResult
from factory.workers.router import Router


GOOD_SPEC = """# bench spec (生成)

## 機能要件
1. メモ一覧が取れる
2. メモが作れる

## 検証方針
curl で HTTP 応答を確認する。

## 触ってよい範囲 (scope)
app.py と tests/ のみ。

## エスカレーション条件
依存の追加が必要になったら中断して報告する。
"""

JUDGE_PASS_JSON = '{"score": 88, "verdict": "pass", "comments": "参照をほぼ網羅"}'
JUDGE_FAIL_JSON = '{"score": 41, "verdict": "revise", "comments": "検証方針が曖昧"}'


class FakeRouter:
    """テスト用 Router 置換: purpose ごとに決定的な結果を返す。"""

    def __init__(self, cfg, usage, names=("spec_a", "spec_b"), judge_score=88):
        self.cfg = cfg
        self.usage = usage
        self.names = list(names)
        self.judge_score = judge_score
        self.calls = []

    def available_names(self):
        return list(self.names)

    def candidates(self, purpose):
        return list(self.names)

    def run(self, purpose, cwd, prompt, timeout=None, resume=None):
        self.calls.append((purpose, self.cfg.use_worker))
        if purpose == "spec":
            return self.names[0], WorkerResult(
                worker=self.names[0], ok=True, text=GOOD_SPEC, seconds=1.0)
        return "judge_x", WorkerResult(
            worker="judge_x", ok=True,
            text=(JUDGE_PASS_JSON if self.judge_score >= 80 else JUDGE_FAIL_JSON),
            seconds=1.0)


def _spec_cfg(tmp, names=("spec_a", "spec_b")) -> Config:
    cfg = Config(root=tmp)
    cfg.worker_order = list(names)
    cfg.workers = {n: WorkerConfig(type="mock") for n in names}
    return cfg


def _write_spec_task(tmp, tid="spec-q", purpose="spec", seed="demo-app",
                     with_ref=True, ref_file=False):
    d = Path(tmp) / ".factory" / "bench" / "tasks"
    d.mkdir(parents=True, exist_ok=True)
    t = {"id": tid, "title": f"[bench] {tid}", "repo": "products/demo_app",
         "purpose": purpose, "seed": seed,
         "reference_spec": "# 参照仕様\n\n1. メモ一覧\n2. メモ作成\n",
         "pass_score": 80}
    if ref_file:
        ref_path = Path(tmp) / "reference_spec.md"
        ref_path.write_text(t.pop("reference_spec"), encoding="utf-8")
        t["reference_spec"] = ""
        t["reference_spec_file"] = "reference_spec.md"
    if not with_ref:
        t.pop("reference_spec")
    p = d / f"{tid}.json"
    p.write_text(json.dumps(t, ensure_ascii=False, indent=2), encoding="utf-8")
    return t


class TestVariantString(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(bench_mod.parse_variant("claude_code@sonnet"), ("claude_code", "sonnet"))
        self.assertEqual(bench_mod.parse_variant("mock"), ("mock", ""))
        self.assertEqual(bench_mod.parse_variant(" opencode @opus "), ("opencode", "opus"))

    def test_label(self):
        self.assertEqual(bench_mod.variant_label("claude_code", "sonnet"), "claude_code@sonnet")
        self.assertEqual(bench_mod.variant_label("mock", ""), "mock")


class TestSpecTaskValidation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        init_factory(Path(self.tmp), demo=True)
        self.f = Factory(Path(self.tmp))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_requires_seed(self):
        _write_spec_task(self.tmp, seed="no-such-seed")
        with self.assertRaisesRegex(bench_mod.BenchError, "seed"):
            bench_mod.load_tasks(self.f)

    def test_requires_reference(self):
        _write_spec_task(self.tmp, with_ref=False)
        with self.assertRaisesRegex(bench_mod.BenchError, "reference_spec"):
            bench_mod.load_tasks(self.f)

    def test_reference_by_file(self):
        _write_spec_task(self.tmp, ref_file=True)
        ts = bench_mod.load_tasks(self.f)
        self.assertEqual(ts[0]["id"], "spec-q")

    def test_dev_task_still_needs_spec_and_scenarios(self):
        d = Path(self.tmp) / ".factory" / "bench" / "tasks"
        d.mkdir(parents=True, exist_ok=True)
        (d / "bad.json").write_text(json.dumps(
            {"id": "bad", "repo": "products/demo_app"}, ensure_ascii=False), encoding="utf-8")
        with self.assertRaisesRegex(bench_mod.BenchError, "spec"):
            bench_mod.load_tasks(self.f)


class TestRouterPin(unittest.TestCase):
    def test_pin_purpose_restricts_only_that_purpose(self):
        tmp = tempfile.mkdtemp()
        try:
            cfg = _spec_cfg(tmp)
            p = bench_mod._pinned_cfg(cfg, "spec_a", lock_purpose="spec")
            r = Router(p, __import__("factory.usage", fromlist=["Usage"]).Usage(Path(tmp)))
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(r.candidates("spec"), ["spec_a"])
                # dev は spec ピンの影響を受けない (dev の固定は bench_lock_dev)
                self.assertEqual(set(r.candidates("dev")), {"spec_a", "spec_b"})
                # judge は全 variant 共通の器具 → ピン留めの影響を受けない
                self.assertEqual(set(r.candidates("judge")), {"spec_a", "spec_b"})
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestBenchSpecE2E(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        init_factory(Path(self.tmp), demo=True)
        self.f = Factory(Path(self.tmp))
        self.fake = FakeRouter(_spec_cfg(self.tmp), __import__(
            "factory.usage", fromlist=["Usage"]).Usage(Path(self.tmp) / ".factory"))
        self._orig_router_for = bench_mod._router_for

        def _fake_router_for(cfg, usage):
            self.fake.cfg = cfg  # 実行のたびに渡された (pin 済) config を観測する
            return self.fake

        bench_mod._router_for = _fake_router_for

    def tearDown(self):
        bench_mod._router_for = self._orig_router_for
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_spec_bench_scores_and_saves(self):
        _write_spec_task(self.tmp)
        res = bench_mod.run_bench(self.f, self.fake.cfg, workers=["spec_a"])
        rows = res["rows"]
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["purpose"], "spec")
        self.assertEqual(r["status"], "done")
        self.assertEqual(r["score"], 88)
        self.assertEqual(r["variant"], "spec_a")
        gen = self.f.mission_dir(r["mission"]) / "spec.md"
        self.assertIn("機能要件", gen.read_text(encoding="utf-8"))
        # 参照 spec は holdout (コード外) に置かれる
        ref = self.f.holdout_dir_for(r["mission"]) / "reference_spec.md"
        self.assertTrue(ref.exists())
        # 集計と提案 (spec 用にも suggestions が出る)
        self.assertEqual(res["aggregate"][0]["purpose"], "spec")
        md = bench_mod.render_markdown(res)
        self.assertIn("`spec` 推奨", md)
        self.assertIn("bench_spec_scored", [e["kind"] for e in self.f.state()["events"]])

    def test_low_score_fails_row(self):
        _write_spec_task(self.tmp)
        self.fake.judge_score = 41
        res = bench_mod.run_bench(self.f, self.fake.cfg, workers=["spec_a"])
        self.assertEqual(res["rows"][0]["status"], "failed")
        self.assertEqual(res["rows"][0]["score"], 41)
        m = self.f.get_mission(res["rows"][0]["mission"])
        self.assertEqual(m.status, "failed")

    def test_spec_pin_per_variant(self):
        """spec 用のピンは variant ごと (use_worker) に効いている。"""
        _write_spec_task(self.tmp)
        bench_mod.run_bench(self.f, self.fake.cfg, workers=["spec_a", "spec_b"])
        spec_calls = [w for pu, w in self.fake.calls if pu == "spec"]
        self.assertEqual(spec_calls, ["spec_a", "spec_b"])


class TestDevVariant(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        init_factory(Path(self.tmp), demo=True)
        self.f = Factory(Path(self.tmp))
        d = Path(self.tmp) / ".factory" / "bench" / "tasks"
        d.mkdir(parents=True, exist_ok=True)
        (d / "demo-notes.json").write_text(json.dumps({
            "id": "demo-notes", "repo": "products/demo_app",
            "spec": ("# t\n\nNOTES.md を書く\n\n```mock-action\n"
                     '{"action": "write", "file": "NOTES.md", "content": "bench demo notes\\n"}\n'
                     "```\n"),
            "build": {}, "max_iterations": 1, "pass_score": 80,
            "scenarios": [{"id": "SC-001", "title": "t", "weight": 1.0,
                           "steps": [{"name": "n",
                                      "cmd": "grep -qx 'bench demo notes' NOTES.md",
                                      "expect_exit": 0}]}],
        }, ensure_ascii=False), encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_worker_at_model_runs_and_labels(self):
        cfg = _spec_cfg(self.tmp, names=("spec_a",))
        res = bench_mod.run_bench(self.f, cfg, workers=["spec_a@sonnet"])
        r = res["rows"][0]
        self.assertEqual(r["variant"], "spec_a@sonnet")
        self.assertEqual(r["purpose"], "dev")
        self.assertEqual(r["status"], "done")
        m = self.f.get_mission(r["mission"])
        self.assertIn("bench: spec_a@sonnet", m.notes)

    def test_unknown_worker_raises(self):
        cfg = _spec_cfg(self.tmp, names=("spec_a",))
        with self.assertRaisesRegex(bench_mod.BenchError, "利用可能なワーカー"):
            bench_mod.run_bench(self.f, cfg, workers=["no_such"])


if __name__ == "__main__":
    unittest.main()
