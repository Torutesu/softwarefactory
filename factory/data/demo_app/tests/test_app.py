"""既存テスト: 初期状態で通る部分のみカバー (作成・削除はホールドアウトで検証される)。"""
from __future__ import annotations

import unittest

import app  # noqa: E402  (repo root をパスに含めて実行される)


class TestStore(unittest.TestCase):
    def test_list_empty(self):
        app.NOTES.clear()
        self.assertEqual(app.list_notes(), [])

    def test_get_missing(self):
        self.assertIsNone(app.get_note(999))


if __name__ == "__main__":
    unittest.main()
