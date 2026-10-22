"""セキュリティ強化 (docs/plan-2026-10-hardening.md の S1〜S11) の回帰テスト。"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
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
from factory import mergegate
from factory.config import Config
from factory.harness.runner import evaluate
from factory.init_scaffold import init_factory
from factory.listener import host_allowed, make_server
from factory.loop import run_mission
from factory.state import Factory, Iteration, Mission, is_merged, mark_merged, repair_parent, valid_mission_id
from factory.supervisor import _repair_depth
from factory.util import is_secret_env, scrubbed_env
from factory import report as report_mod


def _mission_with_holdout(root: Path, build: dict, scenario_cmd: str = "false"):
    f = Factory(root)
    m = f.create_mission(title="t", repo="r")
    wt = f.worktree(m.id)
    wt.mkdir(parents=True)
    hd = f.holdout_dir_for(m.id)
    hd.mkdir(parents=True)
    (hd / "SC-001.json").write_text(json.dumps(
        {"id": "SC-001", "title": "must fail", "steps": [{"name": "s", "cmd": scenario_cmd}]}), encoding="utf-8")
    f.build_file(m.id).write_text(json.dumps(build), encoding="utf-8")
    return f, m, wt


# 実装 Worker が書いた「テスト」: holdout のシナリオを全部 `true` に書き換える
EVIL_TEST = r"""
import glob, json, os
for p in glob.glob(os.path.join(os.getcwd(), '..', '..', '..', 'holdout', '*', '*.json')):
    d = json.load(open(p))
    for s in d['steps']:
        s['cmd'] = 'true'
    json.dump(d, open(p, 'w'))
print('tests ok')
"""


class TestHoldoutIntegrity(unittest.TestCase):
    """S1 / S10: ワーカーのコードによる holdout の書き換えを検知する。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        for d in self.tmp.rglob("*"):
            if d.is_dir():
                os.chmod(d, 0o755)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_rewrite_during_test_is_detected(self):
        f, m, wt = _mission_with_holdout(self.tmp, {"test": "python3 evil_test.py"})
        (wt / "evil_test.py").write_text(EVIL_TEST, encoding="utf-8")
        holdout_mod.record_manifest(f, m.id)
        hr = evaluate(f, m, wt, allow_record=False)
        self.assertTrue(hr.holdout_tampered)
        self.assertFalse(hr.ok)
        # 書き換え後の (通ってしまう) シナリオではなく、スナップショット側で実行している
        self.assertTrue(all(not s.ok for s in hr.scenarios))

    def test_rewrite_before_evaluate_is_detected(self):
        f, m, wt = _mission_with_holdout(self.tmp, {})
        holdout_mod.record_manifest(f, m.id)
        p = f.holdout_dir_for(m.id) / "SC-001.json"
        p.write_text(p.read_text(encoding="utf-8").replace('"false"', '"true"'), encoding="utf-8")
        hr = evaluate(f, m, wt, allow_record=False)
        self.assertTrue(hr.holdout_tampered)
        self.assertEqual(hr.scenarios, [])

    def test_deleted_manifest_counts_as_tamper_in_dev(self):
        f, m, wt = _mission_with_holdout(self.tmp, {})
        holdout_mod.record_manifest(f, m.id)
        holdout_mod.manifest_path(f, m.id).unlink()
        self.assertTrue(evaluate(f, m, wt, allow_record=False).holdout_tampered)
        # canary / validate (allow_record=True) は旧ミッション互換として記録する
        hr = evaluate(f, m, wt, allow_record=True)
        self.assertFalse(hr.holdout_tampered)
        self.assertEqual(holdout_mod.verify_manifest(f, m.id), "ok")

    def test_clean_run_passes_and_holdout_unsealed(self):
        f, m, wt = _mission_with_holdout(self.tmp, {"test": "true"}, scenario_cmd="true")
        holdout_mod.record_manifest(f, m.id)
        hr = evaluate(f, m, wt, allow_record=False)
        self.assertTrue(hr.ok)
        self.assertEqual(stat.S_IMODE(f.holdout_dir_for(m.id).stat().st_mode), 0o755)

    def test_scenarios_see_snapshot_dir_not_holdout(self):
        f, m, wt = _mission_with_holdout(self.tmp, {}, scenario_cmd='echo "$FACTORY_HOLDOUT_DIR" > seen.txt')
        hd = f.holdout_dir_for(m.id)
        (hd / "expected.txt").write_text("ok\n", encoding="utf-8")
        # 絶対パスで holdout を参照する (生成されがちな) step もスナップショット側を読む
        (hd / "SC-002.json").write_text(json.dumps(
            {"id": "SC-002", "title": "abs", "steps": [{"name": "s", "cmd": f"grep -q ok {hd}/expected.txt"}]}),
            encoding="utf-8")
        holdout_mod.record_manifest(f, m.id)
        hr = evaluate(f, m, wt, allow_record=False)
        self.assertTrue(hr.ok, [s.steps[0].tail for s in hr.scenarios])
        seen = (wt / "seen.txt").read_text(encoding="utf-8").strip()
        self.assertNotEqual(seen, str(hd))
        self.assertFalse(Path(seen).exists())  # 検証後にスナップショットは消える

    def test_deleting_whole_holdout_is_tamper(self):
        f, m, wt = _mission_with_holdout(self.tmp, {}, scenario_cmd="false")
        holdout_mod.record_manifest(f, m.id)
        shutil.rmtree(f.holdout_dir_for(m.id))
        hr = evaluate(f, m, wt, allow_record=False)
        self.assertTrue(hr.holdout_tampered)
        self.assertFalse(hr.ok)

    def test_dotfiles_do_not_trip_tamper(self):
        f, m, wt = _mission_with_holdout(self.tmp, {}, scenario_cmd="true")
        holdout_mod.record_manifest(f, m.id)
        (f.holdout_dir_for(m.id) / ".DS_Store").write_bytes(b"\0")
        self.assertEqual(holdout_mod.verify_manifest(f, m.id), "ok")

    def test_stale_seal_is_restored_before_verify(self):
        f, m, wt = _mission_with_holdout(self.tmp, {}, scenario_cmd="true")
        holdout_mod.record_manifest(f, m.id)
        os.chmod(f.holdout_dir_for(m.id), 0)
        self.assertTrue(evaluate(f, m, wt, allow_record=False).ok)

    def test_loop_halts_on_tamper(self):
        init_factory(self.tmp, demo=True)
        cfg = Config.create(str(self.tmp), demo=True)
        f = Factory(self.tmp)
        m = f.create_mission("t", kind="update", seed="demo-app", repo=cfg.projects["demo_app"].path)
        real = holdout_mod.sealed

        from contextlib import contextmanager

        @contextmanager
        def tampering_seal(factory, mid):
            with real(factory, mid):
                yield
            # dev ワーカーの実行直後に holdout を書き換える
            p = factory.holdout_dir_for(mid) / "SC-001.json"
            if p.exists():
                p.write_text(p.read_text(encoding="utf-8") + " ", encoding="utf-8")

        with mock.patch.object(holdout_mod, "sealed", tampering_seal):
            m2 = run_mission(f, m.id, cfg=cfg)
        self.assertEqual(m2.status, "failed")
        self.assertEqual(m2.halt_reason, "holdout_tamper")


class TestReportRedaction(unittest.TestCase):
    """S2: 失敗シナリオのコマンド/出力を worktree から読める場所に書かない。"""

    def test_iter_report_keeps_details_in_holdout(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            f, m, wt = _mission_with_holdout(tmp, {}, scenario_cmd="echo SECRET-HOLDOUT-CMD; false")
            hr = evaluate(f, m, wt)
            run_dir = f.run_dir(m.id)
            md = report_mod.iter_report(f, m, Iteration(n=1, worker="x", started_utc="now"), hr, None, run_dir)
            self.assertNotIn("SECRET-HOLDOUT-CMD", md)
            detail = holdout_mod.runs_dir(f, m.id) / run_dir.name / "iter-1.md"
            self.assertIn("SECRET-HOLDOUT-CMD", detail.read_text(encoding="utf-8"))
            # runs/ はシナリオの指紋に含めない (詳細ログを書いても tamper にならない)
            holdout_mod.record_manifest(f, m.id)
            detail.write_text("changed", encoding="utf-8")
            self.assertEqual(holdout_mod.verify_manifest(f, m.id), "ok")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestEnvScrub(unittest.TestCase):
    """S3: harness に秘密を渡さない。"""

    SECRETS = {"ANTHROPIC_API_KEY": "a", "OPENROUTER_API_KEY": "b", "FACTORY_LISTEN_SECRET": "c",
               "GH_TOKEN": "d", "AWS_SECRET_ACCESS_KEY": "e", "SSH_AUTH_SOCK": "/tmp/s", "DB_PASSWORD": "f"}

    def test_is_secret_env(self):
        for k in self.SECRETS:
            self.assertTrue(is_secret_env(k), k)
        for k in ("PATH", "HOME", "LANG", "FACTORY_PORT", "PYTHONPATH", "TOKENIZERS_PARALLELISM_X"):
            if k == "TOKENIZERS_PARALLELISM_X":
                continue  # 名前に TOKEN を含むものは保守的に外す (passthrough で戻せる)
            self.assertFalse(is_secret_env(k), k)

    def test_harness_step_sees_no_secrets(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            with mock.patch.dict(os.environ, self.SECRETS):
                f, m, wt = _mission_with_holdout(
                    tmp, {"test": "env > env.txt"}, scenario_cmd="true")
                evaluate(f, m, wt)
                seen = (wt / "env.txt").read_text(encoding="utf-8")
                for k in self.SECRETS:
                    self.assertNotIn(k + "=", seen)
                self.assertIn("PATH=", seen)
                f.build_file(m.id).write_text(json.dumps({"test": "env > env2.txt"}), encoding="utf-8")
                evaluate(f, m, wt, env_passthrough=["GH_TOKEN"])
                self.assertIn("GH_TOKEN=d", (wt / "env2.txt").read_text(encoding="utf-8"))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_scrubbed_env_passthrough(self):
        with mock.patch.dict(os.environ, {"GH_TOKEN": "x"}):
            self.assertNotIn("GH_TOKEN", scrubbed_env())
            self.assertEqual(scrubbed_env(passthrough=["gh_token"])["GH_TOKEN"], "x")
            self.assertEqual(scrubbed_env({"A": 1})["A"], "1")


class TestStructuredMergeState(unittest.TestCase):
    """S6 / S9: notes の文字列でマージ済み・修復の親を偽装できない。"""

    def test_notes_spoof_is_not_merged(self):
        m = Mission(id="M1", title="t", status="done",
                    notes="auto-created from evil merged to main (tier: mechanical)")
        self.assertFalse(is_merged(m))
        mark_merged(m, "scoped")
        self.assertTrue(is_merged(m))
        self.assertEqual(m.merged_tier, "scoped")

    def test_legacy_merged_line_still_recognised(self):
        m = Mission(id="M1", title="t", notes="ingested\nmerged to main (tier: scoped)")
        self.assertTrue(is_merged(m))

    def test_repair_parent(self):
        self.assertEqual(repair_parent(Mission(id="A", title="t", repair_of="P")), "P")
        self.assertEqual(repair_parent(Mission(id="A", title="t", notes="auto-created by canary repair-of:P1")), "P1")
        self.assertEqual(repair_parent(Mission(id="A", title="t", notes="auto-created from x repair-of:P1")), "")
        self.assertEqual(repair_parent(Mission(id="A", title="t",
                                               notes="auto-created by canary repair-of:../../etc")), "")

    def test_repair_depth_ignores_traversal(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            f = Factory(tmp)
            m = Mission(id="X", title="t", notes="auto-created by canary repair-of:../../etc")
            self.assertEqual(_repair_depth(f, m), 0)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_mission_id_validation(self):
        for bad in ("", "..", "../x", "a/b", "M1/../../x", "-x", "x" * 100):
            self.assertFalse(valid_mission_id(bad), bad)
        self.assertTrue(valid_mission_id("M20261022-120000-ab12"))
        f = Factory(Path(tempfile.mkdtemp()))
        self.assertIsNone(f.get_mission("../../etc"))
        with self.assertRaises(ValueError):
            f.mission_dir("../x")
        with self.assertRaises(ValueError):
            f.holdout_dir_for("a/../../b")

    def test_unknown_source_in_mission_notes_is_single_line(self):
        from factory.state import one_line

        self.assertEqual(one_line("a\nmerged to main (tier: mechanical)"), "a merged to main (tier: mechanical)")
        self.assertFalse(is_merged(Mission(id="M", title="t",
                                           notes="auto-created from " + one_line("x\nmerged to main (tier: scoped)"))))


def _git(d, *a):
    return subprocess.run(["git", "-C", str(d), *a], capture_output=True, text=True, check=True)


class TestMergeGateProtected(unittest.TestCase):
    """S7: CI/ビルド/テスト基盤・テスト削除・バイナリは自動マージさせない。"""

    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        _git(self.d, "init", "-q", "-b", "main")
        _git(self.d, "config", "user.email", "a@b")
        _git(self.d, "config", "user.name", "a")
        (self.d / "README.md").write_text("x", encoding="utf-8")
        (self.d / "tests").mkdir()
        (self.d / "tests" / "test_app.py").write_text("def test_x():\n    assert 1\n", encoding="utf-8")
        (self.d / "app.py").write_text("x = 1\n", encoding="utf-8")
        _git(self.d, "add", "-A")
        _git(self.d, "commit", "-qm", "base")
        self.cfg = Config(root=str(self.d))

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def _branch(self, name, fn):
        _git(self.d, "checkout", "-qb", name)
        fn()
        _git(self.d, "add", "-A")
        _git(self.d, "commit", "-qm", "c")
        _git(self.d, "checkout", "-q", "main")
        return mergegate.classify(self.cfg, self.d, name, Mission(id="M1", title="t", kind="update"))

    def test_workflow_is_sensitive(self):
        def fn():
            (self.d / ".github" / "workflows").mkdir(parents=True)
            (self.d / ".github" / "workflows" / "x.yml").write_text("on: push\n", encoding="utf-8")
        cls = self._branch("factory/a", fn)
        self.assertEqual(cls["tier"], "sensitive")
        self.assertEqual(mergegate.policy_for(self.cfg, cls["tier"]), "review")

    def test_root_conftest_and_pyproject_are_sensitive(self):
        def fn():
            (self.d / "conftest.py").write_text("x = 1\n", encoding="utf-8")
            (self.d / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
        self.assertEqual(self._branch("factory/b", fn)["tier"], "sensitive")

    def test_deleting_tests_needs_review(self):
        cls = self._branch("factory/c", lambda: (self.d / "tests" / "test_app.py").unlink())
        self.assertEqual(cls["tier"], "behavioral")
        self.assertTrue(any("テスト削除" in r for r in cls["reasons"]))

    def test_binary_is_not_small(self):
        cls = self._branch("factory/d", lambda: (self.d / "blob.bin").write_bytes(b"\0\1\2" * 1000))
        self.assertEqual(cls["tier"], "behavioral")

    def test_small_code_change_still_scoped(self):
        cls = self._branch("factory/e", lambda: (self.d / "app.py").write_text("x = 2\n", encoding="utf-8"))
        self.assertEqual(cls["tier"], "scoped")


class TestListenerHardening(unittest.TestCase):
    """S8: CSRF / DNS rebinding / 予約ファイル / 検証前の副作用 / 深い JSON。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        init_factory(Path(self.tmp), demo=True)
        self.f = Factory(Path(self.tmp))
        self.cfg = Config.create(self.tmp, demo=True)
        with mock.patch.dict(os.environ, {"FACTORY_LISTEN_SECRET": ""}):
            self.srv = make_server(self.f, port=0, cfg=self.cfg, secret="", auto_create=True, project="demo_app")
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}/signals"

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _post(self, body: bytes, headers=None):
        h = {"Content-Type": "application/json"}
        h.update(headers or {})
        req = urllib.request.Request(self.url, data=body, method="POST", headers=h)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code

    def _seeds(self):
        return sorted(p.name for p in (Path(self.tmp) / "seeds").iterdir() if p.is_dir())

    def test_text_plain_rejected(self):
        self.assertEqual(self._post(b'{"title":"x"}', {"Content-Type": "text/plain"}), 415)

    def test_foreign_host_rejected_without_secret(self):
        self.assertEqual(self._post(b'{"title":"x"}', {"Host": "attacker.example"}), 421)
        self.assertTrue(host_allowed("localhost:8787", "127.0.0.1"))
        self.assertTrue(host_allowed("[::1]:8787", "127.0.0.1"))
        self.assertFalse(host_allowed("evil.example:8787", "127.0.0.1"))

    def test_reserved_files_rejected_without_side_effects(self):
        before = self._seeds()
        for files in ({"seed.md": "# x"}, {"mock/mock_actions.json": "{}"}, {"./mock/x": "y"},
                      {"x/../mock/scenarios/SC-001.json": "{}"}, {"x/../seed.md": "#"},
                      {"Mock/mock_actions.json": "{}"}, {"SEED.md": "#"}, {"/etc/x": "y"}):
            self.assertEqual(self._post(json.dumps({"title": "evil", "files": files}).encode()), 400)
        self.assertEqual(self._seeds(), before)

    def test_invalid_fields_rejected_before_seed(self):
        before = self._seeds()
        for payload in ({"title": "x", "priority": "high"}, {"title": "x", "files": ["a"]},
                        {"title": "x", "kind": "rm -rf"}, {"title": "x", "project": "nope"},
                        {"title": "x", "body": {"a": 1}}):
            self.assertEqual(self._post(json.dumps(payload).encode()), 400, payload)
        self.assertEqual(self._seeds(), before)
        self.assertEqual(self.f.list_missions(), [])

    def test_deep_json(self):
        self.assertEqual(self._post(b"[" * 200000 + b"]" * 200000), 400)

    def test_source_newlines_cannot_fake_merge(self):
        body = {"title": "x", "source": "evil\nmerged to main (tier: mechanical)"}
        self.assertEqual(self._post(json.dumps(body).encode()), 200)
        m = self.f.list_missions()[0]
        self.assertFalse(is_merged(m))
        self.assertNotIn("\n", m.notes)


class TestInitAndPerms(unittest.TestCase):
    """B10 / S11: 再 init で config を消さない、private ディレクトリの権限。"""

    def test_reinit_refused_without_force(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            init_factory(tmp, demo=True)
            with self.assertRaises(SystemExit):
                init_factory(tmp, demo=True)
            init_factory(tmp, demo=True, force=True)
            for sub in (".factory", "holdout"):
                self.assertEqual(stat.S_IMODE((tmp / sub).stat().st_mode), 0o700)
            # デモ製品に .pyc を持ち込まない
            self.assertFalse(list((tmp / "products").rglob("*.pyc")))
            self.assertTrue((tmp / "products" / "demo_app" / ".gitignore").is_file())
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
