"""CLI の全サブコマンドを demo ファクトリで通しで叩くスモークテスト (in-process, mock ワーカー)。

各コマンドが例外 (トレースバック) を出さず、期待した終了コードで終わることを確かめる。
本物のワーカー CLI は失敗するスタブで PATH から隠す。
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
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from factory.cli import main
from factory.harness.png import encode_png
from factory.state import Factory, is_merged


class TestCliSmoke(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.stubs = Path(tempfile.mkdtemp())
        for c in ("claude", "codex", "opencode", "gh"):
            p = cls.stubs / c
            p.write_text("#!/bin/sh\nexit 97\n", encoding="utf-8")
            p.chmod(0o755)
        cls.env = mock.patch.dict(os.environ, {"PATH": f"{cls.stubs}:{os.environ['PATH']}",
                                               "OPENROUTER_API_KEY": ""})
        cls.env.start()
        cls.cwd = os.getcwd()
        os.chdir(cls.tmp)

    @classmethod
    def tearDownClass(cls):
        os.chdir(cls.cwd)
        cls.env.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)
        shutil.rmtree(cls.stubs, ignore_errors=True)

    def cli(self, *argv, expect=0):
        out, err = io.StringIO(), io.StringIO()
        code = 0
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                main([str(a) for a in argv])
            except SystemExit as e:
                code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
        if expect is not None:
            self.assertEqual(code, expect, f"{argv}: code={code}\nstdout={out.getvalue()[-800:]}\n"
                                           f"stderr={err.getvalue()[-800:]}")
        return out.getvalue(), err.getvalue()

    def _new_mission(self, title="smoke"):
        out, _ = self.cli("mission", "new", title, "--project", "demo_app", "--seed", "demo-app", "--kind", "update")
        return out.split("mission created: ", 1)[1].split()[0]

    def test_full_lifecycle(self):
        self.cli("init", ".", "--demo")
        self.cli("init", ".", expect=1)  # 再 init は拒否
        self.cli("config", "check")
        out, _ = self.cli("config", "check", "--json")
        self.assertEqual(json.loads(out), [])
        self.cli("status")
        mid = self._new_mission()
        self.cli("mission", "list")
        out, _ = self.cli("holdout", "list", mid)
        self.assertIn("シナリオなし", out)

        out, _ = self.cli("run", mid)
        self.assertIn("status: done", out)
        out, _ = self.cli("report", mid)
        self.assertIn(mid, out)
        out, _ = self.cli("validate", mid)
        self.assertTrue(json.loads(out)["all_scenarios_ok"])
        self.cli("status")
        self.cli("status", "--all")
        self.cli("usage")
        self.cli("usage", "--waste")
        self.cli("pr", mid)  # origin も gh も無い → 下書き
        self.assertTrue((self.tmp / "missions" / mid / "reports" / "pr-draft.md").is_file())

        self.cli("merge", mid, "--yes")
        self.assertTrue(is_merged(Factory(self.tmp).get_mission(mid)))
        self.cli("merge", mid, "--yes", expect=1)  # 二重マージは拒否
        out, _ = self.cli("canary", mid)
        self.assertEqual(json.loads(out[out.index("{\n"):])["status"], "pass")
        self.cli("holdout", "regen", mid, expect=1)  # done は --force 必須
        self.cli("holdout", "regen", mid, "--force")
        self.cli("signals")

        # seed / ingest / run-all
        src = self.tmp / "materials"
        src.mkdir()
        (src / "note.md").write_text("# 新しい要望\n", encoding="utf-8")
        self.cli("seed", "add", src, "--name", "smoke-seed")
        out, _ = self.cli("ingest", src, "--name", "smoke-ingest", "--project", "demo_app", "--kind", "update")
        self.assertIn("mission created", out)
        self.cli("run-all", "--parallel", "2")

        # clock / needs_human
        self.cli("tick", "--dry-run")
        self.cli("tick")
        self.cli("needs-human")
        out, _ = self.cli("needs-human", "--json")
        self.assertIsInstance(json.loads(out), list)
        self.cli("needs-human", "--ack", "nope", expect=1)
        self.cli("worktrees", "--dry-run")
        self.cli("worktrees")
        self.cli("mission", "reset", mid, expect=1)  # done は reset できない
        self.cli("mission", "reset", "../../etc", expect=1)
        self.cli("report", "../../etc", expect=2)

    def test_misc_commands(self):
        self.cli("init", ".", "--demo", "--force")
        a, b = self.tmp / "a.png", self.tmp / "b.png"
        a.write_bytes(encode_png(1, 1, bytes([10, 20, 30, 255])))
        b.write_bytes(encode_png(1, 1, bytes([10, 20, 30, 255])))
        out, _ = self.cli("compare-img", a, b)
        self.assertIn("1.0000", out)
        (self.tmp / "bad.png").write_bytes(b"nope")
        self.cli("compare-img", a, self.tmp / "bad.png", expect=2)
        self.cli("bench", "--init")
        self.cli("bench", "--dry-run")
        self.cli("bench", "--history")
        self.cli("collect", "--list")
        self.cli("collect", "--promote", "NOPE", expect=2)
        out, _ = self.cli("worker", "ping", expect=1)  # スタブは --version に失敗する
        self.assertIn("[ng]", out)
        out, _ = self.cli("doctor", "--json", expect=None)
        self.assertTrue(any(c["name"] == "config" for c in json.loads(out)))
        self.cli("mission", "new", expect=2)  # タイトル無し


if __name__ == "__main__":
    unittest.main()
