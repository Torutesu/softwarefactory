"""品質監査で再現したバグ (docs/plan-2026-10-hardening.md の B1〜B28) の回帰テスト。"""
from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import zlib
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from factory import gitx
from factory.config import Config, ConfigError, WorkerConfig
from factory.configcheck import errors, validate
from factory.harness import runner
from factory.harness.png import decode_png, encode_png
from factory.holdout import Scenario
from factory.init_scaffold import init_factory
from factory.loop import _effective_cfg, run_mission
from factory.state import Factory, Mission
from factory.usage import Usage
from factory.util import run_cmd, run_shell
from factory.workers.base import classify_failure

PYTHONPATH = str(Path(ROOT))


def _cli(args, cwd, env_extra=None):
    env = dict(os.environ, PYTHONPATH=PYTHONPATH)
    env.update(env_extra or {})
    return subprocess.run([sys.executable, "-m", "factory", *args], cwd=str(cwd),
                          capture_output=True, text=True, env=env, timeout=300)


def _demo(tmp: Path):
    init_factory(tmp, demo=True, force=True)
    return Config.create(str(tmp), demo=True), Factory(tmp)


def _git(d, *a):
    return subprocess.run(["git", "-C", str(d), *a], capture_output=True, text=True)


class TestRunCmd(unittest.TestCase):
    def test_timeout_returns_text_and_kills_group(self):  # B1 / B16
        marker = f"sleep {37 + os.getpid() % 50}.5"
        t0 = time.time()
        r = run_shell(f"echo partial; ({marker} &) ; {marker}", timeout=1)
        self.assertLess(time.time() - t0, 10)
        self.assertTrue(r.timed_out)
        self.assertEqual(r.code, 124)
        self.assertIsInstance(r.out, str)
        self.assertIn("partial", r.out)
        time.sleep(0.2)
        ps = subprocess.run(["ps", "-eo", "cmd"], capture_output=True, text=True).stdout
        self.assertNotIn(marker, ps)

    def test_oserror_is_a_result(self):  # B21
        self.assertEqual(run_cmd(["/etc/passwd"]).code, 126)
        self.assertEqual(run_cmd(["definitely-not-a-command-x"]).code, 127)

    def test_worker_parse_survives_timeout(self):  # B1: 以前は bytes で TypeError
        from factory.workers.claude_code import ClaudeCodeWorker

        stub = Path(tempfile.mkdtemp()) / "claude"
        stub.write_text("#!/bin/sh\necho '{\"partial\": tr'\nsleep 30\n", encoding="utf-8")
        stub.chmod(0o755)
        w = ClaudeCodeWorker(WorkerConfig(type="claude", command=str(stub), timeout=1))
        res = w.run("p", Path(tempfile.mkdtemp()))
        self.assertFalse(res.ok)
        self.assertEqual(res.fail_kind, "timeout")


class TestScenarioValidation(unittest.TestCase):  # B4 / B6
    def test_from_dict_rejects_bad_shapes(self):
        for bad in ([], {"id": "x"}, {"id": "x", "steps": [1]}, {"id": "x", "steps": [{"cmd": ""}]},
                    {"id": "x", "steps": [{"cmd": "true", "timeout": "soon"}]}):
            with self.assertRaises(ValueError):
                Scenario.from_dict(bad)
        sc = Scenario.from_dict({"id": "x", "steps": [{"cmd": "true", "timeout": "30", "expect_exit": "1"}]})
        self.assertEqual(sc.steps[0].timeout, 30)
        self.assertEqual(sc.steps[0].expect_exit, 1)

    def _eval(self, files: dict, build=None):
        tmp = Path(tempfile.mkdtemp())
        f = Factory(tmp)
        m = f.create_mission(title="t", repo="r")
        wt = f.worktree(m.id)
        wt.mkdir(parents=True)
        hd = f.holdout_dir_for(m.id)
        hd.mkdir(parents=True)
        for name, text in files.items():
            (hd / name).write_text(text, encoding="utf-8")
        f.build_file(m.id).write_text(json.dumps(build or {}), encoding="utf-8")
        return runner.evaluate(f, m, wt)

    def test_truncated_json_fails_instead_of_passing(self):
        hr = self._eval({"SC-001.json": '{"id": "SC-001", "steps": [{"cmd": "true"'})
        self.assertFalse(hr.ok)
        self.assertEqual(len(hr.scenarios), 1)
        self.assertLess(hr.base_score, 100)

    def test_expect_exit_honoured(self):
        hr = self._eval({"SC-001.json": json.dumps(
            {"id": "SC-001", "steps": [{"cmd": "exit 3", "expect_exit": 3}]})})
        self.assertTrue(hr.ok)
        hr = self._eval({"SC-001.json": json.dumps({"id": "SC-001", "steps": [{"cmd": "exit 3"}]})})
        self.assertFalse(hr.ok)

    def test_step_timeout_fails(self):
        hr = self._eval({"SC-001.json": json.dumps(
            {"id": "SC-001", "steps": [{"cmd": "sleep 5", "timeout": 1, "expect_exit": 124}]})})
        self.assertFalse(hr.ok)  # タイムアウトは expect_exit=124 でも合格にしない
        self.assertIn("タイムアウト", hr.scenarios[0].steps[0].tail)


class TestLoopRobustness(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_crash_marks_failed_not_stuck_in_dev(self):  # B5
        cfg, f = _demo(self.tmp)
        m = f.create_mission("x", kind="update", seed="demo-app", repo=cfg.projects["demo_app"].path)
        with mock.patch("factory.loop.evaluate", side_effect=RuntimeError("boom")):
            m2 = run_mission(f, m.id, cfg=cfg)
        self.assertEqual(m2.status, "failed")
        self.assertEqual(m2.halt_reason, "crash")
        self.assertTrue((f.reports_dir(m.id) / "crash.txt").is_file())
        self.assertNotEqual(oct(f.holdout_dir_for(m.id).stat().st_mode & 0o777), "0o0")

    def test_interrupt_requeues(self):
        cfg, f = _demo(self.tmp)
        m = f.create_mission("x", kind="update", seed="demo-app", repo=cfg.projects["demo_app"].path)
        with mock.patch("factory.loop.evaluate", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                run_mission(f, m.id, cfg=cfg)
        m2 = f.get_mission(m.id)
        self.assertEqual((m2.status, m2.halt_reason), ("queued", "interrupted"))

    def test_judge_unavailable_is_fail_closed(self):  # B19
        cfg, f = _demo(self.tmp)
        m = f.create_mission("x", kind="update", seed="demo-app", repo=cfg.projects["demo_app"].path)
        with mock.patch("factory.loop.run_judge", return_value=None):
            m2 = run_mission(f, m.id, cfg=cfg)
        self.assertEqual((m2.status, m2.halt_reason), ("failed", "judge_unavailable"))
        cfg.judge_required = False
        m3 = f.create_mission("y", kind="update", seed="demo-app", repo=cfg.projects["demo_app"].path)
        with mock.patch("factory.loop.run_judge", return_value=None):
            self.assertEqual(run_mission(f, m3.id, cfg=cfg).status, "done")

    def test_mock_judge_scores_demo(self):
        cfg, f = _demo(self.tmp)
        m = f.create_mission("x", kind="update", seed="demo-app", repo=cfg.projects["demo_app"].path)
        m2 = run_mission(f, m.id, cfg=cfg)
        self.assertEqual(m2.status, "done")
        self.assertEqual(m2.iterations[-1].score, 100)

    def test_parallel_run_on_fresh_repo(self):  # B2
        cfg, f = _demo(self.tmp)
        repo = Path(self.tmp) / "products" / "fresh"
        shutil.copytree(Path(self.tmp) / "products" / "demo_app", repo)
        shutil.rmtree(repo / ".git", ignore_errors=True)
        ids = [f.create_mission(f"p{i}", kind="update", seed="demo-app", repo="products/fresh").id
               for i in range(3)]
        out = {}

        def go(mid):
            out[mid] = run_mission(f, mid, cfg=cfg).status

        ts = [threading.Thread(target=go, args=(mid,)) for mid in ids]
        for t in ts:
            t.start()
        for t in ts:
            t.join(300)
        self.assertEqual(sorted(out.values()), ["done", "done", "done"])
        log = _git(repo, "log", "--oneline", "--all").stdout
        self.assertEqual(log.count("factory: baseline"), 1)

    def test_model_override_only_for_dev_worker_type(self):  # B18
        cfg = Config(root=str(self.tmp), use_worker="mock", worker_order=["mock", "codex"],
                     workers={"mock": WorkerConfig(type="mock"), "codex": WorkerConfig(type="codex", model="gpt")})
        cfg2 = _effective_cfg(cfg, Mission(id="M1", title="t", model="opus"))
        self.assertEqual(cfg2.workers["mock"].model, "opus")
        self.assertEqual(cfg2.workers["codex"].model, "gpt")


class TestMergeSafety(unittest.TestCase):  # B3 / B28
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        _git(self.d, "init", "-q", "-b", "main")
        _git(self.d, "config", "user.email", "a@b")
        _git(self.d, "config", "user.name", "a")
        (self.d / "a.txt").write_text("base\n", encoding="utf-8")
        _git(self.d, "add", "-A")
        _git(self.d, "commit", "-qm", "base")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def _branch(self, name, text):
        _git(self.d, "checkout", "-qb", name)
        (self.d / "a.txt").write_text(text, encoding="utf-8")
        _git(self.d, "commit", "-qam", name)
        _git(self.d, "checkout", "-q", "main")

    def test_conflict_leaves_repo_clean(self):
        self._branch("b1", "one\n")
        self._branch("b2", "two\n")
        self.assertEqual(gitx.merge_squash(self.d, "b1", "m1").code, 0)
        r = gitx.merge_squash(self.d, "b2", "m2")
        self.assertNotEqual(r.code, 0)
        self.assertEqual(gitx.tracked_dirty(self.d), "")
        self.assertFalse((self.d / ".git" / "SQUASH_MSG").exists() and gitx.tracked_dirty(self.d))
        self._branch("b3", "three\n")  # 次のマージは通常どおりできる
        _git(self.d, "checkout", "-q", "b3")
        _git(self.d, "rebase", "-q", "main")
        _git(self.d, "checkout", "-q", "main")

    def test_dirty_main_repo_refused(self):
        self._branch("b1", "one\n")
        (self.d / "a.txt").write_text("human edit\n", encoding="utf-8")
        r = gitx.merge_squash(self.d, "b1", "m1")
        self.assertNotEqual(r.code, 0)
        self.assertEqual((self.d / "a.txt").read_text(encoding="utf-8"), "human edit\n")

    def test_wrong_branch_refused(self):
        self._branch("b1", "one\n")
        r = gitx.merge_squash(self.d, "b1", "m1", target_branch="release")
        self.assertNotEqual(r.code, 0)


class TestUsageTimezone(unittest.TestCase):  # B8
    def test_window_counts_local_date_files(self):
        old_tz = os.environ.get("TZ")
        os.environ["TZ"] = "Asia/Tokyo"
        time.tzset()
        try:
            d = Path(tempfile.mkdtemp())
            u = Usage(d)
            jst = dt.timezone(dt.timedelta(hours=9))
            day = dt.date(2026, 10, 22)
            ts = [dt.datetime(2026, 10, 22, h, 0, tzinfo=jst).astimezone(dt.timezone.utc).isoformat()
                  for h in (1, 2, 3, 4, 5)]
            (d / "usage" / f"{day.isoformat()}.json").write_text(
                json.dumps({"workers": {"w": {"calls": 5, "ts_list": ts}}}), encoding="utf-8")
            now = dt.datetime(2026, 10, 22, 5, 30, tzinfo=jst)
            wc = WorkerConfig(window_call_limit=5, window_minutes=300)
            self.assertEqual(u.window_calls("w", wc, now=now), 5)
            self.assertTrue(u.window_budget_exceeded("w", wc, now=now))
        finally:
            if old_tz is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = old_tz
            time.tzset()


class TestStateFiles(unittest.TestCase):  # B11 / B12
    def test_corrupt_state_is_quarantined(self):
        tmp = Path(tempfile.mkdtemp())
        f = Factory(tmp)
        f.event("a")
        f.state_file.write_text("{broken", encoding="utf-8")
        f.event("b")
        self.assertTrue(list(f.state_dir.glob("state.json.corrupt-*")))
        self.assertEqual([e["kind"] for e in f.state()["events"]], ["b"])

    def test_unknown_mission_keys_ignored(self):
        tmp = Path(tempfile.mkdtemp())
        f = Factory(tmp)
        m = f.create_mission(title="t", repo="r")
        p = f.mission_dir(m.id) / "mission.json"
        d = json.loads(p.read_text(encoding="utf-8"))
        d["future_field"] = 1
        d["iterations"] = [{"n": 1, "worker": "x", "started_utc": "now", "future": True}]
        p.write_text(json.dumps(d), encoding="utf-8")
        m2 = f.get_mission(m.id)
        self.assertEqual(m2.iterations[0].n, 1)
        self.assertEqual(len(f.list_missions()), 1)

    def test_corrupt_mission_is_skipped_not_fatal(self):
        tmp = Path(tempfile.mkdtemp())
        f = Factory(tmp)
        m = f.create_mission(title="t", repo="r")
        (f.mission_dir(m.id) / "mission.json").write_text("{", encoding="utf-8")
        self.assertIsNone(f.get_mission(m.id))
        self.assertEqual(f.list_missions(), [])


class TestPorts(unittest.TestCase):  # B15
    def test_reserved_ports_are_unique(self):
        ports = [runner._pick_port(None) for _ in range(20)]
        try:
            self.assertEqual(len(set(ports)), 20)
            p = ports[0]
            self.assertNotEqual(runner._pick_port(p), p)  # 予約中の番号は再度渡さない
        finally:
            for p in ports:
                runner.release_port(p)


class TestConfigValidation(unittest.TestCase):  # F3 / B24
    def _cfg(self, **over):
        cfg = Config.create(tempfile.mkdtemp(), demo=True).to_dict()
        cfg.update(over)
        return cfg

    def test_demo_config_is_clean(self):
        self.assertEqual(validate(self._cfg()), [])

    def test_rules(self):
        cases = {
            "autonomy_level": ({"autonomy_level": 7}, "範囲外"),
            "tick_concurrency": ({"tick_concurrency": "2"}, "整数"),
            "worker_order[0]": ({"worker_order": ["claud_code"]}, "もしかして"),
            "merge_policy.scoped": ({"merge_policy": {"scoped": "yes"}}, "auto か review"),
            "autopilot_project": ({"autopilot_project": "nope"}, "projects"),
            "notify_url": ({"notify_url": "ftp://x"}, "http"),
            "allow_mock_fallback": ({"allow_mock_fallback": "true"}, "true/false"),
        }
        for path, (over, words) in cases.items():
            issues = errors(validate(self._cfg(**over)))
            self.assertTrue(any(i.path == path and words in i.msg for i in issues), (path, issues))

    def test_unknown_key_is_warning_with_hint(self):
        issues = validate(self._cfg(autonomy_levl=3))
        self.assertTrue(any(i.level == "warning" and "autonomy_level" in i.msg for i in issues))
        self.assertEqual(errors(issues), [])

    def test_worker_rules(self):
        cfg = self._cfg()
        cfg["workers"]["mock"]["auth"] = "oauth"
        cfg["workers"]["x"] = {"type": "gpt", "timeout": 0}
        paths = {i.path for i in errors(validate(cfg))}
        self.assertTrue({"workers.mock.auth", "workers.x.type", "workers.x.timeout"} <= paths)

    def test_load_raises_config_error(self):
        tmp = Path(tempfile.mkdtemp())
        p = tmp / "config.json"
        p.write_text("{", encoding="utf-8")
        with self.assertRaises(ConfigError):
            Config.load(p)
        p.write_text(json.dumps(self._cfg(autonomy_level=9)), encoding="utf-8")
        with self.assertRaises(ConfigError) as ctx:
            Config.load(p)
        self.assertIn("autonomy_level", str(ctx.exception))


class TestCliErrors(unittest.TestCase):  # B22 / B24
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        _cli(["init", str(self.tmp), "--demo"], self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_bad_inputs_exit_2_without_traceback(self):
        for args in (["collect", "--promote", "NOPE"], ["report", "../../etc"], ["bench", "run"]):
            r = _cli(args, self.tmp)
            self.assertNotIn("Traceback", r.stderr, (args, r.stderr))
            self.assertNotEqual(r.returncode, 0, args)

    def test_broken_config_is_reported(self):
        p = self.tmp / ".factory" / "config.json"
        d = json.loads(p.read_text(encoding="utf-8"))
        d["autopilot_project"] = "nope"
        p.write_text(json.dumps(d), encoding="utf-8")
        r = _cli(["status"], self.tmp)
        self.assertEqual(r.returncode, 2)
        self.assertIn("autopilot_project", r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        r = _cli(["config", "check"], self.tmp)
        self.assertEqual(r.returncode, 2)

    def test_config_check_ok(self):
        r = _cli(["config", "check"], self.tmp)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_worker_ping_exit_code(self):
        stubs = Path(tempfile.mkdtemp())
        for c in ("claude", "codex", "opencode"):
            (stubs / c).write_text("#!/bin/sh\nexit 97\n", encoding="utf-8")
            (stubs / c).chmod(0o755)
        r = _cli(["worker", "ping"], self.tmp, {"PATH": f"{stubs}:{os.environ['PATH']}", "OPENROUTER_API_KEY": ""})
        self.assertEqual(r.returncode, 1)
        self.assertIn("[ng]", r.stdout)

    def test_reinit_refused(self):
        r = _cli(["init", str(self.tmp)], self.tmp)
        self.assertEqual(r.returncode, 1)
        self.assertIn("--force", r.stderr)


class TestQuotaClassification(unittest.TestCase):  # B20
    def test_body_text_is_not_quota(self):
        body = "請求 (billing) 機能を実装しました。HTTP 429 を返す rate limit の仕組みも追加。\n" * 50 + "done"
        self.assertEqual(classify_failure(body, ""), "unknown")
        self.assertEqual(classify_failure("", "Error: usage limit reached"), "quota")
        self.assertEqual(classify_failure("...\nAPI Error: 429 Too Many Requests", ""), "quota")


class TestPngRobustness(unittest.TestCase):  # B27
    def _png(self, w, h, color_type, rows: bytes, plte=None):
        def chunk(tag, data):
            return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))
        out = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, color_type, 0, 0, 0))
        if plte is not None:
            out += chunk(b"PLTE", plte)
        return out + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")

    def test_bad_inputs_raise_value_error(self):
        bad = [
            self._png(2, 2, 2, b"\0" + b"\0" * 6),                # 途中で切れたデータ
            self._png(1, 1, 3, b"\0\x05"),                       # PLTE なし
            self._png(1, 1, 3, b"\0\x05", plte=b"\0\0\0"),       # パレット範囲外
            self._png(0, 1, 2, b"\0"),                           # 幅 0
            b"\x89PNG\r\n\x1a\n\0\0",                            # チャンク途中で終わる
        ]
        for data in bad:
            with self.assertRaises(ValueError):
                decode_png(data)

    def test_roundtrip_still_works(self):
        img = decode_png(encode_png(2, 1, bytes([255, 0, 0, 255, 0, 255, 0, 255])))
        self.assertEqual(img.data[1], (0, 255, 0, 255))


class TestNeedsHumanLedger(unittest.TestCase):  # B7 / F6
    def test_dedupe_resolve_ack_and_notify(self):
        from factory import supervisor

        tmp = Path(tempfile.mkdtemp())
        f = Factory(tmp)
        r1 = supervisor.record_needs_human(f, ["A: merge gate", "B: high-risk"])
        self.assertEqual(sorted(r1["new"]), ["A: merge gate", "B: high-risk"])
        r2 = supervisor.record_needs_human(f, ["A: merge gate", "B: high-risk"])
        self.assertEqual(r2["new"], [])
        r3 = supervisor.record_needs_human(f, ["A: merge gate"])
        self.assertEqual(r3["resolved"], ["B: high-risk"])
        key = supervisor.open_needs_human(f)[0]["key"]
        self.assertTrue(supervisor.ack_needs_human(f, key))
        self.assertEqual(supervisor.open_needs_human(f), [])
        self.assertEqual(len(supervisor.open_needs_human(f, include_acked=True)), 1)
        self.assertEqual(supervisor.record_needs_human(f, ["A: merge gate"])["new"], [])

    def test_tick_notifies_only_on_change(self):
        from factory import supervisor

        got = []

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                got.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                self.send_response(200)
                self.end_headers()

            def log_message(self, *a):
                pass

        srv = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            tmp = Path(tempfile.mkdtemp())
            cfg, f = _demo(tmp)
            cfg.notify_url = f"http://127.0.0.1:{srv.server_address[1]}/"
            cfg.autonomy_level = 1
            m = f.create_mission("hr", kind="update", seed="demo-app", repo=cfg.projects["demo_app"].path)
            m.risk = "high"
            f.save_mission(m)
            supervisor.tick(f, cfg)
            supervisor.tick(f, cfg)
            self.assertEqual(len(got), 1)
            self.assertTrue(any("high-risk" in t for t in got[0]["new"]))
        finally:
            srv.shutdown()


class TestCollectDraftNames(unittest.TestCase):  # B25
    def test_extra_cannot_overwrite_signal(self):
        from factory import collect as collect_mod

        tmp = Path(tempfile.mkdtemp())
        f = Factory(tmp)
        _, d = collect_mod.write_draft(f, "s", "dir", {"title": "t", "origin": "o", "text": "body",
                                                       "extra": "evil", "extra_name": "../meta.json"})
        self.assertEqual(json.loads((d / "meta.json").read_text(encoding="utf-8"))["title"], "t")
        self.assertIn("収集本文", (d / "signal.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
