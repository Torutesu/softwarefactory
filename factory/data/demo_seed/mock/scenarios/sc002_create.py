"""SC-002: メモ作成 (FR-1, FR-2)。

usage: sc002_create.py ok|bad-empty|bad-long|bad-type
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

PORT = os.environ["FACTORY_PORT"]
BASE = f"http://127.0.0.1:{PORT}"


def post(payload) -> tuple:
    req = urllib.request.Request(
        BASE + "/notes",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {"raw": raw}


def main() -> int:
    case = sys.argv[1]
    if case == "ok":
        status, note = post({"title": "demo", "body": "b1"})
        assert status == 201, (status, note)
        assert isinstance(note.get("id"), int) and note["id"] >= 1, note
        assert note["title"] == "demo", note
        # 一覧に反映されていること
        with urllib.request.urlopen(BASE + "/notes", timeout=10) as r:
            titles = [n["title"] for n in json.loads(r.read().decode())["notes"]]
        assert "demo" in titles, titles
    elif case == "bad-empty":
        status, body = post({"title": "   ", "body": ""})
        assert status == 400, (status, body)
        assert "error" in body, body
    elif case == "bad-long":
        status, body = post({"title": "x" * 101, "body": ""})
        assert status == 400, (status, body)
    elif case == "bad-type":
        status, body = post({"title": 123, "body": ""})
        assert status == 400, (status, body)
    else:
        print("unknown case:", case)
        return 1
    print("SC-002 %s OK" % case)
    return 0


if __name__ == "__main__":
    sys.exit(main())
