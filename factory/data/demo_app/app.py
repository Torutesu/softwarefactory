#!/usr/bin/env python3
"""デモ製品: メモAPI (stdlib http server)。

初期状態では health / GET /notes だけがあり、
「POST での作成」「バリデーション」「削除」は未実装 (Seed のミッション対象)。
"""
from __future__ import annotations

import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

NOTES: dict = {}  # id(int) -> {"id", "title", "body"}
_NEXT_ID = [1]
_PATH_NOTE = re.compile(r"^/notes/(\d+)$")


# ---------------------------------------------------------------------------
# store (pure logic)
# ---------------------------------------------------------------------------
def list_notes() -> list:
    return [NOTES[k] for k in sorted(NOTES)]


def get_note(note_id: int):
    return NOTES.get(note_id)


def create_note(title, body):
    """TODO: ミッションで実装する (バリデーション + 登録)。"""
    raise NotImplementedError("not implemented")


def delete_note(note_id: int) -> bool:
    """TODO: ミッションで実装する (削除)。"""
    return False


# ---------------------------------------------------------------------------
# HTTP layer
# ---------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            return self._json(200, {"ok": True})
        if parsed.path == "/notes":
            return self._json(200, {"notes": list_notes()})
        m = _PATH_NOTE.match(parsed.path)
        if m:
            note = get_note(int(m.group(1)))
            if note is None:
                return self._json(404, {"error": "not found"})
            return self._json(200, note)
        self._json(404, {"error": "not found"})

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path != "/notes":
            return self._json(404, {"error": "not found"})
        try:
            payload = json.loads(self._read_body() or "{}")
        except json.JSONDecodeError:
            return self._json(400, {"error": "invalid json"})
        try:
            note = create_note(payload.get("title"), payload.get("body"))
        except NotImplementedError:
            return self._json(501, {"error": "not implemented"})
        except (ValueError, TypeError) as e:
            return self._json(400, {"error": str(e)})
        return self._json(201, note)

    def do_DELETE(self):
        parsed = urlparse(self.path)
        m = _PATH_NOTE.match(parsed.path)
        if not m:
            return self._json(404, {"error": "not found"})
        if delete_note(int(m.group(1))):
            return self._json(200, {"ok": True})
        return self._json(404, {"error": "not found"})

    # ------------------------------------------------------------------
    def _read_body(self) -> bytes:
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def _json(self, code: int, obj) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # ログを静かに
        pass


def main():
    port = int(os.environ.get("FACTORY_PORT") or os.environ.get("PORT") or 8900)
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"notes api on 127.0.0.1:{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
