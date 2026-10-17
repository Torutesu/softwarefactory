"""SC-004: 連番IDと複数作成 (FR-1)。連続した2件の作成で id が1ずつ増えること。"""
from __future__ import annotations

import json
import os
import sys
import urllib.request

PORT = os.environ["FACTORY_PORT"]
BASE = f"http://127.0.0.1:{PORT}"


def main() -> int:
    ids = []
    for i in range(2):
        req = urllib.request.Request(
            BASE + "/notes",
            data=json.dumps({"title": f"seq-{i}", "body": ""}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            assert r.status == 201, r.status
            ids.append(json.loads(r.read().decode())["id"])
    assert ids[1] == ids[0] + 1, ids
    print("SC-004 OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
