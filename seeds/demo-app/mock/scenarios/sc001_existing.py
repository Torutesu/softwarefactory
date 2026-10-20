"""SC-001: 既存機能の退行チェック (health / 一覧 / 詳細 404)。"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

PORT = os.environ["FACTORY_PORT"]
BASE = f"http://127.0.0.1:{PORT}"


def main() -> int:
    r = urllib.request.urlopen(BASE + "/health", timeout=10)
    assert r.status == 200, r.status
    assert json.loads(r.read().decode())["ok"] is True

    r = urllib.request.urlopen(BASE + "/notes", timeout=10)
    assert r.status == 200, r.status
    assert "notes" in json.loads(r.read().decode())

    try:
        urllib.request.urlopen(BASE + "/notes/999999", timeout=10)
        print("unknown note should be 404")
        return 1
    except urllib.error.HTTPError as e:
        assert e.code == 404, e.code
    print("SC-001 OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
