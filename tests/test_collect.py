"""定時シグナル収集 (factory collect) のテスト。

- config の collect.sources 検証 (name/type/必須フィールド)
- url 取得 → ドラフト化 → 重複スキップ (同じ本文は 2 度採らない)
- dir ソース: 新しいファイルだけドラフト化
- command ソース: 出力はドラフト / 失敗は 1 行のエラーとして他ソースに影響しない
- every_minutes スロットリング (due / not due)
- dry-run は何も書かない
- promote → Seed 化 (seed.md 骨組み + signal.md 同梱, meta が promoted 化, 二重promote禁止)
- dismiss / list_drafts
- config.to_dict/from_dict の roundtrip で collect 設定が消えない
"""
from __future__ import annotations

import datetime as dt
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from factory import collect as collect_mod
from factory.config import Config, WorkerConfig
from factory.init_scaffold import init_factory
from factory.state import Factory
from factory.util import save_json

HTML = ("<html><head><title>週次エラー報告</title></head><body>"
        "<h1>トップ10</h1><p>TypeError が 12 件。</p></body></html>")


def _cfg(tmp, sources) -> Config:
    cfg = Config(root=tmp)
    cfg.worker_order = ["mock"]
    cfg.workers = {"mock": WorkerConfig(type="mock")}
    cfg.collect = {"sources": sources}
    return cfg


def _src(**kw) -> dict:
    base = {"name": "s1", "type": "command", "cmd": "printf 'hello signal\\n'"}
    base.update(kw)
    return base


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        init_factory(Path(self.tmp), demo=True)
        self.f = Factory(Path(self.tmp))
        self.orig_http = collect_mod._http_get

    def tearDown(self):
        collect_mod._http_get = self.orig_http
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestSourcesValidation(_Base):
    def test_missing_name_raises(self):
        cfg = _cfg(self.tmp, [{"type": "url", "url": "https://x"}])
        with self.assertRaisesRegex(ValueError, "name"):
            collect_mod.sources_of(cfg)

    def test_missing_type_field(self):
        required = {"url": "url", "file": "path", "dir": "path", "command": "cmd"}
        for stype, key in required.items():
            s = {"name": "s-" + stype, "type": stype, key: "whatever"}  # 必須フィールドのみ → pop で消す
            s.pop(key)
            cfg = _cfg(self.tmp, [s])
            with self.assertRaisesRegex(ValueError, stype):
                collect_mod.sources_of(cfg)

    def test_unknown_type(self):
        cfg = _cfg(self.tmp, [_src(type="magic")])
        with self.assertRaisesRegex(ValueError, "url/file/dir/command"):
            collect_mod.sources_of(cfg)

    def test_ok_and_dict_form(self):
        cfg = _cfg(self.tmp, [_src()])
        self.assertEqual(len(collect_mod.sources_of(cfg)), 1)
        cfg2 = _cfg(self.tmp, [])
        cfg2.collect = {"sources": _src(name="only", type="file", path="/tmp/x")}
        self.assertEqual(collect_mod.sources_of(cfg2)[0]["name"], "only")


class TestCollectRun(_Base):
    def test_url_draft_and_dedupe(self):
        collect_mod._http_get = lambda url, timeout=30: ("text/html", HTML.encode())
        cfg = _cfg(self.tmp, [_src(name="blog", type="url", url="https://example.com/report")])
        res = collect_mod.run_collect(self.f, cfg)
        self.assertEqual(res["created"], 1)
        rows = collect_mod.list_drafts(self.f)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "new")
        self.assertEqual(rows[0]["title"], "週次エラー報告")
        ddir = collect_mod.drafts_dir(self.f) / rows[0]["id"]
        self.assertIn("TypeError", (ddir / "signal.md").read_text(encoding="utf-8"))
        self.assertTrue((ddir / "page.html").exists())
        # 2 回目: 同じ本文 → dup (新規なし)
        res2 = collect_mod.run_collect(self.f, cfg)
        self.assertEqual(res2["created"], 0)
        self.assertEqual(len(collect_mod.list_drafts(self.f)), 1)
        # 本文が変われば新規ドラフト
        collect_mod._http_get = lambda url, timeout=30: ("text/html", HTML.replace("12 件", "30 件").encode())
        res3 = collect_mod.run_collect(self.f, cfg)
        self.assertEqual(res3["created"], 1)

    def test_dir_source_picks_new_files(self):
        d = Path(self.tmp) / "exports"
        d.mkdir()
        cfg = _cfg(self.tmp, [_src(name="exp", type="dir", path=str(d))])
        (d / "a.log").write_text("error A\n", encoding="utf-8")
        res = collect_mod.run_collect(self.f, cfg)
        self.assertEqual(res["created"], 1)
        res2 = collect_mod.run_collect(self.f, cfg)  # 変化なし
        self.assertEqual(res2["created"], 0)
        (d / "b.log").write_text("error B\n", encoding="utf-8")
        res3 = collect_mod.run_collect(self.f, cfg)
        self.assertEqual(res3["created"], 1)

    def test_command_source(self):
        cfg = _cfg(self.tmp, [_src(name="cmd", cmd="printf 'crit: 500 errors'")])
        res = collect_mod.run_collect(self.f, cfg)
        self.assertEqual(res["created"], 1)
        # 失敗コマンドはエラー行になり、他ソースは継続する
        cfg2 = _cfg(self.tmp, [
            _src(name="bad", cmd="exit 3"),
            _src(name="good", cmd="printf 'ok'"),
        ])
        res2 = collect_mod.run_collect(self.f, cfg2)
        by = {r["source"]: r for r in res2["rows"]}
        self.assertEqual(by["bad"]["status"], "error")
        self.assertEqual(by["good"]["status"], "ok")

    def test_empty_command_output_is_not_error(self):
        cfg = _cfg(self.tmp, [_src(name="quiet", cmd="true")])
        res = collect_mod.run_collect(self.f, cfg)
        self.assertEqual(res["rows"][0]["status"], "empty")
        self.assertEqual(res["created"], 0)

    def test_every_minutes_throttle(self):
        cfg = _cfg(self.tmp, [_src(name="slow", every_minutes=60, cmd="printf 'x'")])
        res1 = collect_mod.run_collect(self.f, cfg)
        self.assertEqual(res1["created"], 1)
        res2 = collect_mod.run_collect(self.f, cfg)  # 直後 → not due
        self.assertEqual(res2["rows"][0]["status"], "skipped")
        self.assertIn("not due", res2["rows"][0]["note"])
        # last_ts を強制的に古くすれば回る
        st = collect_mod.load_state(self.f)
        st["sources"]["slow"]["last_ts"] = (
            dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=2)).isoformat()
        collect_mod.save_state(self.f, st)
        res3 = collect_mod.run_collect(self.f, cfg)
        self.assertEqual(res3["rows"][0]["status"], "ok")

    def test_dry_run_writes_nothing(self):
        collect_mod._http_get = lambda url, timeout=30: ("text/html", HTML.encode())
        cfg = _cfg(self.tmp, [_src(name="blog", type="url", url="https://example.com")])
        res = collect_mod.run_collect(self.f, cfg, dry_run=True)
        self.assertEqual(res["created"], 1)  # 見積もりは出る
        self.assertEqual(list(collect_mod.list_drafts(self.f)), [])  # 実際は書かない
        # state も変化していない (2 回目の dry-run でも見積もり 1)
        res2 = collect_mod.run_collect(self.f, cfg, dry_run=True)
        self.assertEqual(res2["created"], 1)

    def test_config_roundtrip_keeps_collect(self):
        cfg = _cfg(self.tmp, [_src(), _src(name="u2", type="url", url="https://x", every_minutes=15)])
        st = save_json(Path(self.tmp) / "_cfg.json", cfg.to_dict())
        cfg2 = Config.from_dict(cfg.to_dict())
        self.assertEqual(len(collect_mod.sources_of(cfg2)), 2)
        self.assertEqual(collect_mod.sources_of(cfg2)[1]["every_minutes"], 15)


class TestPromoteDismiss(_Base):
    def _one_draft(self):
        collect_mod._http_get = lambda url, timeout=30: ("text/html", HTML.encode())
        cfg = _cfg(self.tmp, [_src(name="blog", type="url", url="https://example.com/report")])
        collect_mod.run_collect(self.f, cfg)
        return collect_mod.list_drafts(self.f)[0]["id"]

    def test_promote_creates_seed_and_finalizes_meta(self):
        did = self._one_draft()
        sd = collect_mod.promote(self.f, did, seed_name="weekly-errors")
        self.assertEqual(sd.name, "weekly-errors")
        body = (sd / "seed.md").read_text(encoding="utf-8")
        self.assertIn("目的", body)
        self.assertIn("受け入れ基準", body)
        self.assertIn("TypeError", body)  # ドラフト本文が仮置きされる
        self.assertTrue((sd / "signal.md").exists())  # 収集本文・ページも同梱
        self.assertTrue((sd / "page.html").exists())
        meta = collect_mod._meta(collect_mod.drafts_dir(self.f) / did)
        self.assertEqual(meta["status"], "promoted")
        self.assertEqual(meta["seed"], "weekly-errors")
        kinds = [e["kind"] for e in self.f.state()["events"]]
        self.assertIn("draft_promoted", kinds)

    def test_double_promote_rejected(self):
        did = self._one_draft()
        collect_mod.promote(self.f, did)
        with self.assertRaisesRegex(collect_mod.CollectError, "promoted"):
            collect_mod.promote(self.f, did)

    def test_dismiss(self):
        did = self._one_draft()
        collect_mod.dismiss(self.f, did)
        meta = collect_mod._meta(collect_mod.drafts_dir(self.f) / did)
        self.assertEqual(meta["status"], "dismissed")
        with self.assertRaisesRegex(collect_mod.CollectError, "dismissed"):
            collect_mod.promote(self.f, did)  # 却下済みは promote できない
        # list からは status が見える
        rows = {r["id"]: r for r in collect_mod.list_drafts(self.f)}
        self.assertEqual(rows[did]["status"], "dismissed")


if __name__ == "__main__":
    unittest.main()
