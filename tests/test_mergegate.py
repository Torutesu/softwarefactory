"""マージゲート (blast radius 分類・ポリシー・帰属) のテスト。"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from factory import gitx, mergegate
from factory.config import Config
from factory.state import Mission


def _repo(tmp: str, name: str = "p1") -> Path:
    repo = Path(tmp) / "products" / name
    repo.mkdir(parents=True, exist_ok=True)
    gitx.ensure_repo(repo)
    if not (repo / "app.py").exists():
        (repo / "app.py").write_text("def main():\n    print('hi')\n", encoding="utf-8")
    gitx.ensure_baseline_commit(repo)
    return repo


def _mission(mid: str = "MTEST0001-000000-ab", kind: str = "update") -> Mission:
    return Mission(id=mid, title="テスト", kind=kind, repo="products/p1")


def _branch_with(repo: Path, wt: Path, files: dict, mid: str) -> str:
    """worktree ブランチに files をコミットし、ブランチ名を返す。"""
    branch = f"factory/{mid}"
    gitx.create_worktree(repo, wt, branch)
    for rel, content in files.items():
        p = wt / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    gitx.commit_all(wt, "test: apply")
    return branch


class TestClassify(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.repo = _repo(self.tmp)
        self.cfg = Config(root=self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _classify(self, mid: str, files: dict, kind: str = "update") -> dict:
        wt = Path(self.tmp) / "wt" / mid
        branch = _branch_with(self.repo, wt, files, mid)
        return mergegate.classify(self.cfg, self.repo, branch, _mission(mid, kind))

    def test_scoped_small_change(self):
        cls = self._classify("M1-1", {"app.py": "def main():\n    print('hello')\n"})
        self.assertEqual(cls["tier"], "scoped")
        self.assertLessEqual(cls["lines"], 50)

    def test_mechanical_docs_only(self):
        cls = self._classify("M1-2", {"README.md": "# 変更\n", "docs/note.md": "メモ\n"})
        self.assertEqual(cls["tier"], "mechanical")

    def test_behavioral_large_change(self):
        big = "\n".join(f"line_{i} = {i}" for i in range(80))
        cls = self._classify("M1-3", {"app.py": big})
        self.assertEqual(cls["tier"], "behavioral")

    def test_behavioral_greenfield(self):
        cls = self._classify("M1-4", {"README.md": "# 新製品\n"}, kind="greenfield")
        self.assertEqual(cls["tier"], "behavioral")

    def test_sensitive_path(self):
        cls = self._classify("M1-5", {"auth/session.py": "TOKEN = 'x'\n"})
        self.assertEqual(cls["tier"], "sensitive")
        self.assertTrue(cls["sensitive_matches"])
        self.assertIn("auth/session.py", cls["sensitive_matches"])

    def test_missing_branch_is_unknown(self):
        cls = mergegate.classify(self.cfg, self.repo, "factory/no-such-branch", _mission("MX"))
        self.assertEqual(cls["tier"], "unknown")

    def test_policy_unknown_is_review(self):
        self.assertEqual(mergegate.policy_for(self.cfg, "unknown"), "review")
        self.assertEqual(mergegate.policy_for(self.cfg, "mechanical"), "auto")
        cfg2 = Config(root=self.tmp)
        cfg2.merge_policy["scoped"] = "review"
        self.assertEqual(mergegate.policy_for(cfg2, "scoped"), "review")


class TestAttribution(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_workers_and_trailer(self):
        from factory.state import Iteration

        m = Mission(id="M2-1", title="x")
        m.iterations = [Iteration(n=1, worker="claude_code", started_utc="t"),
                        Iteration(n=2, worker="claude_code", started_utc="t"),
                        Iteration(n=3, worker="mock", started_utc="t")]
        self.assertEqual(mergegate.workers_of(m), "claude_code×2, mock×1")
        msg = mergegate.merge_message(m)
        self.assertTrue(msg.startswith("factory: M2-1 x"))
        self.assertIn("Assisted-by: SoftwareFactory M2-1", msg)
        self.assertIn("Signed-off-by は付与していません", msg)
        blurb = mergegate.pr_blurb(None, m)
        self.assertIn("Assisted-by: SoftwareFactory M2-1", blurb)


class TestBranchChanges(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.repo = _repo(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_counts(self):
        wt = Path(self.tmp) / "wt"
        _branch_with(self.repo, wt, {"app.py": "a = 1\n", "new.py": "b = 2\n"}, "MB-1")
        ch = gitx.branch_changes(self.repo, "factory/MB-1")
        self.assertEqual(ch["files"], 2)
        self.assertGreaterEqual(ch["lines"], 2)
        self.assertIn("new.py", ch["paths"])


if __name__ == "__main__":
    unittest.main()
