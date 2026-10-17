"""Intake 重複ゲートと worktree ファイル複製 (Isolation 衛生) のテスト。"""
from __future__ import annotations

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
from factory.init_scaffold import init_factory
from factory.state import Factory
from factory.supervisor import step_intake, _dup_matches


def _demo(tmp, autonomy=3):
    init_factory(Path(tmp), demo=True)
    cfg = Config.create(tmp, demo=True)
    cfg.autonomy_level = autonomy
    return cfg, Factory(Path(tmp))


class TestIntakeDup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def create_seed(self, name: str, body: str = "テスト用シード"):
        d = Path(self.tmp) / "seeds" / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "seed.md").write_text(f"# Seed: {name}\n\n{body}\n", encoding="utf-8")
        return d

    def test_similar_seed_is_skipped_and_escalated(self):
        cfg, f = _demo(self.tmp)
        # 既出: demo-app にミッションを割当済み
        f.create_mission(title="既に割当済み", seed="demo-app", repo=cfg.projects["demo_app"].path)
        # 類似名の新 Seed を投入
        self.create_seed("demo-app2")
        intake = step_intake(f, cfg, dry_run=False)
        self.assertEqual(intake["created"], [])
        self.assertTrue(any("重複" in e or "類疑似い" in e for e in intake.get("escalate", [])))
        # 状態にイベントが残る
        kinds = [e["kind"] for e in f.state().get("events")]
        self.assertIn("intake_dup_suspect", kinds)

    def test_unrelated_seed_still_created(self):
        cfg, f = _demo(self.tmp)
        f.create_mission(title="既に割当済み", seed="demo-app", repo=cfg.projects["demo_app"].path)
        self.create_seed("全く別の案件")
        intake = step_intake(f, cfg, dry_run=False)
        self.assertEqual(len(intake["created"]), 1)
        self.assertEqual(intake.get("escalate", []), [])

    def test_duplicate_title_still_blocked(self):
        cfg, f = _demo(self.tmp)
        f.create_mission(title="検索が遅い問題", seed="demo-app", repo=cfg.projects["demo_app"].path)
        self.create_seed("検索が遅い問題")  # seed は異なるが題目が重複
        intake = step_intake(f, cfg, dry_run=False)
        self.assertEqual(intake["created"], [])

    def test_threshold_zero_disables_gate(self):
        cfg, f = _demo(self.tmp)
        cfg.intake_dup_threshold = 0.0
        f.create_mission(title="既に割当済み", seed="demo-app", repo=cfg.projects["demo_app"].path)
        self.create_seed("demo-app2")
        intake = step_intake(f, cfg, dry_run=False)
        self.assertEqual(len(intake["created"]), 1)

    def test_dup_matches_shape(self):
        cfg, f = _demo(self.tmp)
        f.create_mission(title="AUTO demo-app", seed="demo-app", repo=cfg.projects["demo_app"].path)
        matches = _dup_matches(f, "demo-app2", 0.85)
        self.assertTrue(any(m.startswith("seed:demo-app") for m in matches))


class TestWorktreeSeeding(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _repo(self):
        repo = Path(self.tmp) / "repo"
        repo.mkdir(parents=True)
        gitx.ensure_repo(repo)
        (repo / "app.py").write_text("print('x')\n", encoding="utf-8")
        (repo / ".gitignore").write_text(".env\n.env.local\nextra.txt\n", encoding="utf-8")
        (repo / ".env").write_text("DB=localhost\n", encoding="utf-8")
        (repo / ".env.local").write_text("PORT=1\n", encoding="utf-8")
        (repo / "extra.txt").write_text("sei-no-hin\n", encoding="utf-8")
        from subprocess import run

        run(["git", "add", "-A"], cwd=repo, check=True)
        run(["git", "commit", "-m", "init"], cwd=repo, check=True,
            env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                 "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})
        return repo

    def test_env_files_copied(self):
        repo = self._repo()
        wt = Path(self.tmp) / "wt"
        gitx.create_worktree(repo, wt, "factory/W1")
        # worktree は空の checkout → .env は無い
        self.assertFalse((wt / ".env").exists())
        copied = gitx.seed_worktree_files(repo, wt)
        self.assertIn(".env", copied)
        self.assertIn(".env.local", copied)
        self.assertNotIn("extra.txt", copied)  # 既定パターン (*.local) には match しない
        self.assertTrue((wt / ".env").exists())
        self.assertEqual((wt / ".env").read_text(encoding="utf-8"), "DB=localhost\n")

    def test_worktreeinclude_overrides(self):
        repo = self._repo()
        (repo / ".worktreeinclude").write_text("# コメント\nextra.txt\n", encoding="utf-8")
        wt = Path(self.tmp) / "wt2"
        gitx.create_worktree(repo, wt, "factory/W2")
        copied = gitx.seed_worktree_files(repo, wt)
        self.assertEqual(copied, ["extra.txt"])
        self.assertFalse((wt / ".env").exists())

    def test_empty_patterns_copies_nothing(self):
        repo = self._repo()
        wt = Path(self.tmp) / "wt3"
        gitx.create_worktree(repo, wt, "factory/W3")
        self.assertEqual(gitx.seed_worktree_files(repo, wt, []), [])

    def test_no_double_walk_via_skip_dirs(self):
        repo = self._repo()
        node = repo / "node_modules" / "pkg"
        node.mkdir(parents=True, exist_ok=True)
        (node / ".env").write_text("should not copy\n", encoding="utf-8")
        wt = Path(self.tmp) / "wt4"
        gitx.create_worktree(repo, wt, "factory/W4")
        copied = gitx.seed_worktree_files(repo, wt)
        self.assertNotIn("node_modules/pkg/.env", copied)


if __name__ == "__main__":
    unittest.main()
