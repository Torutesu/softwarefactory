"""SC-003: メモ削除 (FR-3, FR-4)。

作成→削除→GET で 404、存在しない id の削除は 404。
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

PORT = os.environ["FACTORY_PORT"]
BASE = f"http://127.0.0.1:{PORT}"


def main() -> int:
    # 1) 作成
    req = urllib.request.Request(
        BASE + "/notes",
        data=json.dumps({"title": "to-delete", "body": "x"}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        assert r.status == 201, r.status
        nid = json.loads(r.read().decode())["id"]

    # 2) 削除
    req = urllib.request.Request(BASE + f"/notes/{nid}", method="DELETE")
    with urllib.request.urlopen(req, timeout=10) as r:
        assert r.status == 200, r.status
        assert json.loads(r.read().decode())["ok"] is True

    # 3) GET で 404
    try:
        urllib.request.urlopen(BASE + f"/notes/{nid}", timeout=10)
        print("deleted note should be 404")
        return 1
    except urllib.error.HTTPError as e:
        assert e.code == 404, e.code

    # 4) 存在しない id の削除は 404
    req = urllib.request.Request(BASE + "/notes/999999", method="DELETE")
    try:
        urllib.request.urlopen(req, timeout=10)
        print("unknown delete should be 404")
        return 1
    except urllib.error.HTTPError as e:
        assert e.code == 404, e.code

    print("SC-003 OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
