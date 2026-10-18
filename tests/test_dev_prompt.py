"""dev プロンプトの構造 (キャッシュ映える/反復2以降の再送省略) のテスト。"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from factory.loop import build_dev_prompt
from factory.state import Mission


class _FStub:
    """build_dev_prompt に必要な Factory だけを満たすスタブ。"""

    def __init__(self, root: Path):
        self.root = root

    def spec_file(self, mid: str) -> Path:
        return self.root / "spec.md"

    def build_file(self, mid: str) -> Path:
        return self.root / "build.json"

    def worktree(self, mid: str) -> Path:
        return self.root / "work"

    def mission_dir(self, mid: str) -> Path:
        return self.root


class TestDevPrompt(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        root = Path(self.tmp)
        (root / "spec.md").write_text("# 仕様: メモAPI\n\n要件 A/B/C\n", encoding="utf-8")
        (root / "build.json").write_text('{"build": "make", "test": "pytest"}', encoding="utf-8")
        self.f = _FStub(root)
        self.m = Mission(id="MP1", title="メモAPI", repo="products/demo_app")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _prompt(self, n=1, brief=False):
        return build_dev_prompt(self.f, self.m, n, 3, None, None, "", brief_spec=brief)

    def test_spec_first_for_stable_prefix(self):
        """仕様が先頭に来て (セッション内で接頭辞が安定)、可変部分が後に続く。"""
        p = self._prompt()
        self.assertTrue(p.startswith("## 製品仕様"))
        self.assertIn("=== spec.md ===", p)
        # 可変 (反復番号) 部分は spec より後
        self.assertLess(p.find("# 仕様: メモAPI"), p.find("Iteration 1"))

    def test_brief_spec_on_resume(self):
        """同一セッションの継続では spec 全文を再送しない (tokens/request レバー)。"""
        p = self._prompt(n=2, brief=True)
        self.assertNotIn("=== spec.md ===", p)
        self.assertIn("前回反復と同一", p)
        self.assertIn("Iteration 2", p)
        # 検証コマンドとルールは残る
        self.assertIn("build:", p)
        self.assertIn("build → test", p)

    def test_full_spec_by_default(self):
        p = self._prompt()
        self.assertIn("要件 A/B/C", p)
        self.assertNotIn("前回反復と同一", p)


if __name__ == "__main__":
    unittest.main()
