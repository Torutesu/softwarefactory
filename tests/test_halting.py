"""halting の保障 (noprogress 打ち切り / reviewability ゲート) と judge 出力上限のテスト。"""
from __future__ import annotations

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
from factory.config import Config
from factory.harness.judge import (JUDGE_MAX_COMMENTS_CHARS, JUDGE_MAX_PRIORITIES,
                                   JUDGE_MAX_PRIORITY_CHARS, cap_judge_output)
from factory.init_scaffold import init_factory
from factory.loop import run_mission
from factory.spec_generator import SPEC_TEMPLATE
from factory.state import Factory
from factory.supervisor import step_execute


def _demo_with_seed(tmp: str, name: str, mutate) -> tuple:
    """demo を作り、demo-app Seed を複製して mutate(seed_dir) で答えキーを変える。"""
    init_factory(Path(tmp), demo=True)
    cfg = Config.create(tmp, demo=True)
    f = Factory(Path(tmp))
    src = Path(tmp) / "seeds" / "demo-app"
    dst = Path(tmp) / "seeds" / name
    shutil.copytree(src, dst)
    mutate(dst)
    return cfg, f


class TestNoProgress(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _stall_seed(self, d: Path) -> None:
        # mock ワーカーが何も変更しない → テストは失敗し続け、作業ツリーも不変
        (d / "mock" / "mock_actions.json").write_text('{"actions": []}', encoding="utf-8")

    def test_halts_after_consecutive_no_change(self):
        cfg, f = _demo_with_seed(self.tmp, "stall", self._stall_seed)
        m = f.create_mission("stall", kind="update", seed="stall",
                             repo=cfg.projects["demo_app"].path, max_iterations=5)
        m2 = run_mission(f, m.id, cfg=cfg)
        self.assertEqual(m2.status, "failed")
        self.assertEqual(m2.halt_reason, "noprogress")
        self.assertEqual(len(m2.iterations), cfg.noprogress_iterations)
        self.assertTrue(all(it.changed is False for it in m2.iterations))
        # 2 反復目の prompt には no-change 警告が載る
        p2 = (f.mission_dir(m.id) / "prompts" / "iter-2.md").read_text(encoding="utf-8")
        self.assertIn("作業ツリーに変更がありませんでした", p2)
        self.assertIn("打ち切り理由: noprogress", (f.reports_dir(m.id) / "final.md").read_text(encoding="utf-8"))

    def test_disabled_runs_to_max_iterations(self):
        cfg, f = _demo_with_seed(self.tmp, "stall", self._stall_seed)
        cfg.noprogress_iterations = 0
        m = f.create_mission("stall", kind="update", seed="stall",
                             repo=cfg.projects["demo_app"].path, max_iterations=3)
        m2 = run_mission(f, m.id, cfg=cfg)
        self.assertEqual(len(m2.iterations), 3)
        self.assertEqual(m2.halt_reason, "max_iterations")

    def test_escalated_by_tick(self):
        cfg, f = _demo_with_seed(self.tmp, "stall", self._stall_seed)
        shutil.rmtree(Path(self.tmp) / "seeds" / "demo-app")
        f.create_mission("stall", kind="update", seed="stall",
                         repo=cfg.projects["demo_app"].path, max_iterations=5)
        res = step_execute(f, cfg, dry_run=False)
        self.assertEqual(res["ran"][0]["halt_reason"], "noprogress")
        self.assertTrue(any("noprogress" in e for e in res["escalate"]))

    def test_success_is_not_flagged(self):
        init_factory(Path(self.tmp), demo=True)
        cfg = Config.create(self.tmp, demo=True)
        f = Factory(Path(self.tmp))
        m = f.create_mission("ok", kind="update", seed="demo-app", repo=cfg.projects["demo_app"].path)
        m2 = run_mission(f, m.id, cfg=cfg)
        self.assertEqual(m2.status, "done")
        self.assertEqual(m2.halt_reason, "")
        self.assertTrue(m2.iterations[0].changed)


class TestReviewabilityGate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _unreviewable_seed(self, d: Path) -> None:
        b = d / "mock" / "build.json"
        data = json.loads(b.read_text(encoding="utf-8"))
        data["test"] = None
        b.write_text(json.dumps(data), encoding="utf-8")
        shutil.rmtree(d / "mock" / "scenarios")

    def test_blocks_without_test_and_scenarios(self):
        cfg, f = _demo_with_seed(self.tmp, "noverify", self._unreviewable_seed)
        m = f.create_mission("noverify", kind="update", seed="noverify",
                             repo=cfg.projects["demo_app"].path)
        m2 = run_mission(f, m.id, cfg=cfg)
        self.assertEqual(m2.status, "blocked")
        self.assertEqual(m2.halt_reason, "unreviewable")
        self.assertEqual(m2.iterations, [])

    def test_gate_can_be_disabled(self):
        cfg, f = _demo_with_seed(self.tmp, "noverify", self._unreviewable_seed)
        cfg.require_reviewable = False
        cfg.noprogress_iterations = 0
        m = f.create_mission("noverify", kind="update", seed="noverify",
                             repo=cfg.projects["demo_app"].path, max_iterations=1)
        m2 = run_mission(f, m.id, cfg=cfg)
        self.assertNotEqual(m2.status, "blocked")
        self.assertEqual(len(m2.iterations), 1)


class TestFingerprint(unittest.TestCase):
    def test_content_based(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            gitx.ensure_repo(repo)
            (repo / "a.txt").write_text("1", encoding="utf-8")
            gitx.ensure_baseline_commit(repo)
            fp0 = gitx.tree_fingerprint(repo)
            self.assertEqual(fp0, gitx.tree_fingerprint(repo))
            (repo / "a.txt").write_text("2", encoding="utf-8")
            fp1 = gitx.tree_fingerprint(repo)
            self.assertNotEqual(fp0, fp1)
            gitx.commit_all(repo, "chg")
            fp2 = gitx.tree_fingerprint(repo)
            self.assertEqual(fp1, fp2)  # コミットしただけ (内容同一) は変化なし
            (repo / "new.txt").write_text("x", encoding="utf-8")
            self.assertNotEqual(fp2, gitx.tree_fingerprint(repo))
            (repo / "new.txt").unlink()
            (repo / "__pycache__").mkdir()
            (repo / "__pycache__" / "m.pyc").write_bytes(b"\0")
            self.assertEqual(fp2, gitx.tree_fingerprint(repo))


class TestJudgeCaps(unittest.TestCase):
    def test_truncates(self):
        d = cap_judge_output({"score": 50, "verdict": "revise", "comments": "あ" * 2000,
                              "priorities": ["p" * 500] * 10})
        self.assertEqual(len(d["comments"]), JUDGE_MAX_COMMENTS_CHARS)
        self.assertEqual(len(d["priorities"]), JUDGE_MAX_PRIORITIES)
        self.assertTrue(all(len(p) == JUDGE_MAX_PRIORITY_CHARS for p in d["priorities"]))

    def test_normalizes_shapes(self):
        self.assertEqual(cap_judge_output({"priorities": "one"})["priorities"], ["one"])
        self.assertEqual(cap_judge_output({"priorities": None})["priorities"], [])
        self.assertEqual(cap_judge_output({})["comments"], "")


class TestSpecTemplate(unittest.TestCase):
    def test_has_handoff_sections(self):
        t = SPEC_TEMPLATE.format(title="x")
        self.assertIn("## スコープ", t)
        self.assertIn("## エスカレート条件", t)


if __name__ == "__main__":
    unittest.main()
