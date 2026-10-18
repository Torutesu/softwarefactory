"""holdout の隔離 (封印・漏洩検知)、harness 失敗時の judge 省略、listener の認証と入力検証のテスト。"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import shutil
import stat
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from factory import holdout as holdout_mod
from factory.config import Config
from factory.init_scaffold import init_factory
from factory.listener import is_loopback, make_server
from factory.loop import run_mission
from factory.state import Factory
from factory.workers.router import Router


def _mode(p: Path) -> int:
    return stat.S_IMODE(p.stat().st_mode)


def _demo(tmp: str):
    init_factory(Path(tmp), demo=True)
    cfg = Config.create(tmp, demo=True)
    return cfg, Factory(Path(tmp))


class TestHoldoutSeal(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.f = Factory(Path(self.tmp))
        self.d = self.f.holdout_dir_for("M1")
        self.d.mkdir(parents=True)
        (self.d / "SC-001.json").write_text("{}", encoding="utf-8")

    def tearDown(self):
        os.chmod(self.d, 0o755)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_sealed_during_and_restored_after(self):
        with holdout_mod.sealed(self.f, "M1"):
            self.assertEqual(_mode(self.d), 0)
        self.assertEqual(_mode(self.d), 0o755)

    def test_restored_on_exception(self):
        with self.assertRaises(RuntimeError):
            with holdout_mod.sealed(self.f, "M1"):
                raise RuntimeError("worker crashed")
        self.assertEqual(_mode(self.d), 0o755)

    def test_unseal_if_stale(self):
        os.chmod(self.d, 0)  # kill -9 で封印が残った状態
        self.assertTrue(holdout_mod.unseal_if_stale(self.f, "M1"))
        self.assertEqual(_mode(self.d), 0o755)
        self.assertFalse(holdout_mod.unseal_if_stale(self.f, "M1"))

    def test_missing_dir_is_noop(self):
        with holdout_mod.sealed(self.f, "nope"):
            pass

    def test_detect_leak(self):
        abs_path = str(self.d.resolve())
        self.assertEqual(holdout_mod.detect_leak(self.f, "M1", f"cat {abs_path}/SC-001.json"), abs_path)
        self.assertEqual(holdout_mod.detect_leak(self.f, "M1", "ls ../../holdout/M1"), "holdout/M1")
        # prompt のルール文 (holdout/ は触らない) をなぞっただけでは検知しない
        self.assertIsNone(holdout_mod.detect_leak(self.f, "M1", "holdout/ や .factory/ は触りませんでした"))


class TestLoopIsolation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _mission(self):
        cfg, f = _demo(self.tmp)
        m = f.create_mission("iso", kind="update", seed="demo-app", repo=cfg.projects["demo_app"].path)
        return cfg, f, m

    def test_holdout_sealed_only_during_dev(self):
        cfg, f, m = self._mission()
        seen = {}
        real_run = Router.run

        def spy(self_, purpose, cwd, prompt, resume=None):
            d = f.holdout_dir_for(m.id)
            if d.is_dir():
                seen.setdefault(purpose, []).append(_mode(d))
            return real_run(self_, purpose, cwd, prompt, resume=resume)

        with mock.patch.object(Router, "run", spy):
            m2 = run_mission(f, m.id, cfg=cfg)
        self.assertEqual(m2.status, "done")
        self.assertEqual(seen["dev"], [0])
        self.assertTrue(all(mode != 0 for mode in seen.get("judge", [])))
        self.assertEqual(_mode(f.holdout_dir_for(m.id)), 0o755)

    def test_leak_in_worker_output_halts(self):
        cfg, f, m = self._mission()
        real_run = Router.run

        def leaky(self_, purpose, cwd, prompt, resume=None):
            name, res = real_run(self_, purpose, cwd, prompt, resume=resume)
            if purpose == "dev":
                res.text += f"\nread {f.holdout_dir_for(m.id).resolve()}/SC-001.json"
            return name, res

        with mock.patch.object(Router, "run", leaky):
            m2 = run_mission(f, m.id, cfg=cfg)
        self.assertEqual(m2.status, "failed")
        self.assertEqual(m2.halt_reason, "holdout_leak")
        self.assertIn("[holdout_leak]", m2.iterations[0].note)


class TestJudgeSkip(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _failing_mission(self):
        cfg, f = _demo(self.tmp)
        seed = Path(self.tmp) / "seeds" / "demo-app" / "mock" / "mock_actions.json"
        seed.write_text('{"actions": []}', encoding="utf-8")  # 何も直さない → test が落ち続ける
        cfg.noprogress_iterations = 0
        m = f.create_mission("fail", kind="update", seed="demo-app",
                             repo=cfg.projects["demo_app"].path, max_iterations=2)
        return cfg, f, m

    def _count_judge(self, cfg, f, m) -> int:
        calls = []
        real_run = Router.run

        def spy(self_, purpose, cwd, prompt, resume=None):
            calls.append(purpose)
            return real_run(self_, purpose, cwd, prompt, resume=resume)

        with mock.patch.object(Router, "run", spy):
            m2 = run_mission(f, m.id, cfg=cfg)
        self.assertEqual(m2.status, "failed")
        return calls.count("judge")

    def test_skipped_by_default(self):
        cfg, f, m = self._failing_mission()
        self.assertEqual(self._count_judge(cfg, f, m), 0)

    def test_opt_in(self):
        cfg, f, m = self._failing_mission()
        cfg.judge_on_harness_fail = True
        self.assertEqual(self._count_judge(cfg, f, m), 2)


class TestListener(unittest.TestCase):
    SECRET = "s3cret-for-test"

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        init_factory(Path(self.tmp), demo=True)
        self.f = Factory(Path(self.tmp))
        self.cfg = Config.create(self.tmp, demo=True)
        self.srv = None

    def tearDown(self):
        if self.srv:
            self.srv.shutdown()
            self.srv.server_close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _start(self, secret, max_body=1_000_000):
        self.srv = make_server(self.f, port=0, cfg=self.cfg, secret=secret, max_body=max_body)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{self.srv.server_address[1]}/signals"

    def _post(self, url, body: bytes, headers=None):
        req = urllib.request.Request(url, data=body, method="POST",
                                     headers={"Content-Type": "application/json", **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_rejects_without_credentials(self):
        url = self._start(self.SECRET)
        code, _ = self._post(url, b'{"title": "x"}')
        self.assertEqual(code, 401)
        code, _ = self._post(url, b'{"title": "x"}', {"Authorization": "Bearer wrong"})
        self.assertEqual(code, 401)

    def test_bearer_and_hmac(self):
        url = self._start(self.SECRET)
        code, out = self._post(url, b'{"title": "bearer"}', {"Authorization": f"Bearer {self.SECRET}"})
        self.assertEqual(code, 200, out)
        body = b'{"title": "signed"}'
        sig = "sha256=" + hmac.new(self.SECRET.encode(), body, hashlib.sha256).hexdigest()
        code, out = self._post(url, body, {"X-Hub-Signature-256": sig})
        self.assertEqual(code, 200, out)
        # 署名後に本文を改ざんすると通らない
        code, _ = self._post(url, b'{"title": "tampered"}', {"X-Hub-Signature-256": sig})
        self.assertEqual(code, 401)

    def test_body_limit(self):
        url = self._start("", max_body=64)
        code, _ = self._post(url, json.dumps({"title": "x" * 200}).encode())
        self.assertEqual(code, 413)

    def test_path_traversal_rejected(self):
        url = self._start("")
        cfg_path = Path(self.tmp) / ".factory" / "config.json"
        before = cfg_path.read_text(encoding="utf-8")
        for name in ("../../.factory/config.json", str(cfg_path)):
            code, out = self._post(url, json.dumps({"title": "evil", "files": {name: "{}"}}).encode())
            self.assertEqual(code, 400, out)
        self.assertEqual(cfg_path.read_text(encoding="utf-8"), before)
        self.assertFalse((Path(self.tmp) / "seeds" / "evil").exists())  # 中途半端な Seed も残さない

    def test_non_loopback_requires_secret(self):
        with mock.patch.dict(os.environ, {"FACTORY_LISTEN_SECRET": ""}):
            with self.assertRaises(SystemExit):
                make_server(self.f, port=0, bind="0.0.0.0", cfg=self.cfg)

    def test_is_loopback(self):
        self.assertTrue(is_loopback("127.0.0.1"))
        self.assertTrue(is_loopback("localhost"))
        self.assertTrue(is_loopback("::1"))
        self.assertFalse(is_loopback("0.0.0.0"))
        self.assertFalse(is_loopback(""))
        self.assertFalse(is_loopback("192.168.1.10"))


if __name__ == "__main__":
    unittest.main()
