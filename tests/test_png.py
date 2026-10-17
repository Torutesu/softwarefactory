"""PNG 類似スコア (UI ベンチマーク比較) のテスト。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from factory.harness.png import decode_png, encode_png, similarity, similarity_images


def _solid_png(w, h, rgba):
    px = bytes(rgba) * (w * h)
    return encode_png(w, h, px)


def _gradient_png(w, h):
    rows = []
    for y in range(h):
        for x in range(w):
            rows.append(bytes((x * 255 // max(1, w - 1), y * 255 // max(1, h - 1), 128, 255)))
    return encode_png(w, h, b"".join(rows))


class TestPng(unittest.TestCase):
    def test_encode_decode_roundtrip(self):
        raw = _gradient_png(16, 8)
        img = decode_png(raw)
        self.assertEqual((img.width, img.height), (16, 8))
        # 左上と右下の色を確認
        top_left = img.data[0]
        bottom_right = img.data[-1]
        self.assertEqual(top_left, (0, 0, 128, 255))
        self.assertEqual(bottom_right, (255, 255, 128, 255))

    def test_identical_is_1(self):
        raw = _solid_png(32, 32, (10, 200, 30, 255))
        self.assertEqual(similarity_images(decode_png(raw), decode_png(raw)), 1.0)

    def test_similar_vs_different(self):
        tmp = tempfile.mkdtemp()
        a = os.path.join(tmp, "a.png")
        b = os.path.join(tmp, "b.png")
        c = os.path.join(tmp, "c.png")
        with open(a, "wb") as f:
            f.write(_solid_png(40, 40, (200, 30, 40, 255)))
        with open(b, "wb") as f:
            f.write(_solid_png(40, 40, (210, 40, 50, 255)))  # ほぼ同色
        with open(c, "wb") as f:
            f.write(_solid_png(80, 20, (10, 240, 10, 255)))  # 全然違う
        self.assertGreater(similarity(a, b), 0.95)
        self.assertLess(similarity(a, c), 0.9)

    def test_size_agnostic(self):
        """サイズ違いでも構図比較として値が出る。"""
        img_a = decode_png(_solid_png(64, 64, (128, 128, 128, 255)))
        img_b = decode_png(_solid_png(16, 16, (120, 120, 120, 255)))
        s = similarity_images(img_a, img_b)
        self.assertGreater(s, 0.9)


class TestScenarioStep(unittest.TestCase):
    """_check_step の expect_png_similar 連携。"""

    def _check(self, tmp, spec, ok_color=True):
        os.chdir(tmp)
        try:
            holdout = os.path.join(tmp, "holdout")
            os.makedirs(holdout, exist_ok=True)
            bench = os.path.join(holdout, "bench.png")
            with open(bench, "wb") as f:
                f.write(_solid_png(24, 24, (180, 60, 90, 255)))
            if ok_color:
                cmd = 'cp "$FACTORY_HOLDOUT_DIR/bench.png" render.png'
            else:
                cmd = 'cp "$FACTORY_HOLDOUT_DIR/bench.png" render.png && python3 -c "pass"'
            step = type("S", (), {
                "name": "render",
                "cmd": cmd,
                "expect_exit": 0,
                "expect_contains": None,
                "expect_not_contains": None,
                "expect_regex": None,
                "expect_png_similar": spec,
                "timeout": 30,
            })()
            from factory.harness.runner import _check_step

            return _check_step(step, os.path.join(tmp, "repo"), None, holdout)
        finally:
            os.chdir(ROOT)

    def test_similar_pass(self):
        tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(tmp, "repo"), exist_ok=True)
        r = self._check(tmp, {"file": "render.png", "to": "bench.png", "ratio": 0.9})
        self.assertTrue(r.ok)
        self.assertIn("similarity", r.tail)

    def test_different_fails(self):
        """同一画像コピーは必ず通るので、閾値 1.01 (到達不能) で失敗側を試す。"""
        tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(tmp, "repo"), exist_ok=True)
        r = self._check(tmp, {"file": "render.png", "to": "bench.png", "ratio": 1.01})
        self.assertFalse(r.ok)

    def test_missing_bench_fails(self):
        tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(tmp, "repo"), exist_ok=True)
        os.makedirs(os.path.join(tmp, "holdout"), exist_ok=True)
        os.chdir(tmp)
        try:
            step = type("S", (), {
                "name": "render",
                "cmd": "cp $FACTORY_HOLDOUT_DIR/none.png render.png",
                "expect_exit": 0,
                "expect_contains": None,
                "expect_not_contains": None,
                "expect_regex": None,
                "expect_png_similar": {"file": "render.png", "to": "none.png", "ratio": 0.9},
                "timeout": 30,
            })()
            from factory.harness.runner import _check_step

            r = _check_step(step, os.path.join(tmp, "repo"), None, os.path.join(tmp, "holdout"))
        finally:
            os.chdir(ROOT)
        self.assertFalse(r.ok)


if __name__ == "__main__":
    unittest.main()
