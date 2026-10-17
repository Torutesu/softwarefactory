"""Webhook リスナー: 外部シグナル → Seed 化 (必要なら Mission 自動作成/実行)。

    factory listen --port 8787 [--bind 127.0.0.1] [--project demo_app] [--auto-create] [--auto-run]

POST /signals
    {"title": "...", "body": "...", "source": "...", "files": {"name": "text"}}

GET /health → {"ok": true}
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

from .ingest import create_from_payload
from .state import Factory
from .util import now_utc_iso


class Listener:
    def __init__(self, factory: Factory, project: str = "", auto_create: bool = False, auto_run: bool = False, cfg=None):
        self.factory = factory
        self.project = project
        self.auto_create = auto_create
        self.auto_run = auto_run
        self.cfg = cfg
        self.log_path = factory.state_dir / "listener.log"

    def log(self, msg: str) -> None:
        line = f"[{now_utc_iso()}] {msg}"
        print(line, flush=True)
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass

    # ------------------------------------------------------------------
    def handle_signal(self, payload: dict) -> dict:
        title = str(payload.get("title") or "untitled signal")
        body = str(payload.get("body") or "")
        source = str(payload.get("source") or "webhook")
        files = {k: str(v) for k, v in (payload.get("files") or {}).items()}
        seed_dir = create_from_payload(self.factory, title, body, source, files)
        self.log(f"seed created: {seed_dir.name} (source={source})")
        out = {"ok": True, "seed": seed_dir.name}

        if not self.auto_create:
            return out

        project = payload.get("project") or self.project
        if not project:
            self.log("auto-create skipped: project 指定なし")
            out["mission"] = None
            return out
        repo = self.cfg.projects[project].path if project in (self.cfg.projects or {}) else None
        if not repo:
            self.log(f"auto-create skipped: project {project} 未登録")
            out["mission"] = None
            return out
        m = self.factory.create_mission(
            title=f"AUTO {title}",
            kind=str(payload.get("kind") or "update"),
            seed=seed_dir.name,
            repo=repo,
            priority=int(payload.get("priority") or 5),
            notes=f"auto-created from {source}",
        )
        self.factory.event("signal_auto_mission", f"{m.id} (seed={seed_dir.name})")
        self.log(f"mission auto-created: {m.id}")
        out["mission"] = m.id

        if self.auto_run:
            def _run():
                try:
                    from .loop import run_mission

                    run_mission(self.factory, m.id, cfg=self.cfg)
                    self.log(f"auto-run finished: {m.id}")
                except Exception as e:  # noqa
                    self.log(f"auto-run failed: {m.id}: {e}")

            threading.Thread(target=_run, daemon=True).start()
        return out


def serve(factory: Factory, port: int = 8787, bind: str = "127.0.0.1",
          project: str = "", auto_create: bool = False, auto_run: bool = False, cfg=None) -> None:
    listener = Listener(factory, project=project, auto_create=auto_create, auto_run=auto_run, cfg=cfg)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path in ("/health", "/signals/health"):
                return self._json(200, {"ok": True, "auto_create": auto_create, "project": project})
            return self._json(404, {"error": "not found"})

        def do_POST(self):
            if self.path not in ("/signals", "/signal"):
                return self._json(404, {"error": "not found"})
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b"{}"
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                return self._json(400, {"error": "invalid json"})
            try:
                out = listener.handle_signal(payload)
            except Exception as e:  # noqa
                listener.log(f"error: {e}")
                return self._json(500, {"error": str(e)})
            return self._json(200, out)

        def _json(self, code, obj):
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # 静かに
            pass

    server = ThreadingHTTPServer((bind, port), Handler)
    print(f"信号受付中: http://{bind}:{port}/signals  (Ctrl-C で終了)", flush=True)
    print(f"  health:  http://{bind}:{port}/health", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
