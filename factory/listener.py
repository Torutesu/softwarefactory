"""Webhook リスナー: 外部シグナル → Seed 化 (必要なら Mission 自動作成/実行)。

    factory listen --port 8787 [--bind 127.0.0.1] [--project demo_app] [--auto-create] [--auto-run]
                   [--secret S | env FACTORY_LISTEN_SECRET]

POST /signals
    {"title": "...", "body": "...", "source": "...", "files": {"name": "text"}}
    認証 (secret 設定時は必須。どちらか):
      X-Hub-Signature-256: sha256=<HMAC-SHA256(secret, body) hex>   (GitHub 互換; X-Factory-Signature も可)
      Authorization: Bearer <secret>

GET /health → {"ok": true}

受け取った本文は最終的に「確認なしで動く Worker」への prompt になる。ループバック以外で
待ち受けるときは secret を必須とし、本文サイズにも上限を設ける。
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

from .ingest import create_from_payload
from .state import Factory
from .util import now_utc_iso

MAX_BODY_BYTES = 1_000_000
SECRET_ENV = "FACTORY_LISTEN_SECRET"


def is_loopback(bind: str) -> bool:
    if bind == "localhost":
        return True
    try:
        return ipaddress.ip_address(bind).is_loopback
    except ValueError:
        return False


def verify_request(secret: str, headers, raw: bytes) -> bool:
    """secret 未設定なら常に True。設定時は HMAC 署名か Bearer のどちらかが一致すること。"""
    if not secret:
        return True
    sig = headers.get("X-Hub-Signature-256") or headers.get("X-Factory-Signature") or ""
    if sig:
        expected = "sha256=" + hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig.strip(), expected)
    auth = headers.get("Authorization") or ""
    if auth.startswith("Bearer "):
        return hmac.compare_digest(auth[len("Bearer "):].strip(), secret)
    return False


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


def make_server(factory: Factory, port: int = 8787, bind: str = "127.0.0.1",
                project: str = "", auto_create: bool = False, auto_run: bool = False, cfg=None,
                secret: Optional[str] = None, max_body: int = MAX_BODY_BYTES) -> ThreadingHTTPServer:
    secret = secret if secret is not None else os.environ.get(SECRET_ENV, "")
    if not secret and not is_loopback(bind):
        raise SystemExit(f"エラー: --bind {bind} (ループバック以外) では --secret か {SECRET_ENV} が必須です")
    if not secret and auto_run:
        print(f"[listen] 警告: secret なしで --auto-run です。同じマシン上の任意のプロセスがミッションを"
              f"実行できます ({SECRET_ENV} の設定を推奨)", flush=True)
    listener = Listener(factory, project=project, auto_create=auto_create, auto_run=auto_run, cfg=cfg)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path in ("/health", "/signals/health"):
                return self._json(200, {"ok": True, "auto_create": auto_create, "project": project})
            return self._json(404, {"error": "not found"})

        def do_POST(self):
            if self.path not in ("/signals", "/signal"):
                return self._json(404, {"error": "not found"})
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return self._json(400, {"error": "invalid content-length"})
            if n < 0:
                return self._json(400, {"error": "invalid content-length"})
            if n > max_body:
                return self._json(413, {"error": f"body too large (max {max_body} bytes)"})
            raw = self.rfile.read(n) if n else b"{}"
            if not verify_request(secret, self.headers, raw):
                listener.log(f"unauthorized signal from {self.client_address[0]}")
                return self._json(401, {"error": "unauthorized"})
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                return self._json(400, {"error": "invalid json"})
            if not isinstance(payload, dict):
                return self._json(400, {"error": "payload must be an object"})
            try:
                out = listener.handle_signal(payload)
            except ValueError as e:
                return self._json(400, {"error": str(e)})
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

    return ThreadingHTTPServer((bind, port), Handler)


def serve(factory: Factory, port: int = 8787, bind: str = "127.0.0.1",
          project: str = "", auto_create: bool = False, auto_run: bool = False, cfg=None,
          secret: Optional[str] = None) -> None:
    server = make_server(factory, port=port, bind=bind, project=project, auto_create=auto_create,
                         auto_run=auto_run, cfg=cfg, secret=secret)
    auth = "認証あり" if (secret or os.environ.get(SECRET_ENV)) else "認証なし (ループバックのみ)"
    print(f"信号受付中: http://{bind}:{port}/signals  [{auth}] (Ctrl-C で終了)", flush=True)
    print(f"  health:  http://{bind}:{port}/health", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
