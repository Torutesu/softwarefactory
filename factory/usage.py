"""使用量モニタリング:日次の呼び出し回数・トークン・コストを記録し、予算超過時にルーターへ伝える。

保存先: .factory/usage/YYYY-MM-DD.json
"""
from __future__ import annotations

import json
import tempfile
import os
from pathlib import Path
from typing import Optional

from .config import WorkerConfig
from .util import ensure_dir, load_json, save_json, today_local


class Usage:
    def __init__(self, state_dir: Path):
        self.dir = ensure_dir(state_dir / "usage")

    # ------------------------------------------------------------------
    def _file(self, day: str) -> Path:
        return self.dir / f"{day}.json"

    def record(
        self,
        worker: str,
        seconds: float,
        ok: bool,
        purpose: str = "dev",
        tokens_in: Optional[int] = None,
        tokens_out: Optional[int] = None,
        cost_usd: Optional[float] = None,
    ) -> None:
        day = today_local()
        path = self._file(day)
        d = load_json(path, {"workers": {}}) or {"workers": {}}
        w = d["workers"].setdefault(worker, {"calls": 0, "fails": 0, "seconds": 0.0})
        w["calls"] += 1
        if not ok:
            w["fails"] += 1
        w["seconds"] = round(w.get("seconds", 0.0) + seconds, 1)
        w.setdefault("calls_by_purpose", {})
        w["calls_by_purpose"][purpose] = w["calls_by_purpose"].get(purpose, 0) + 1
        for key, val in (("tokens_in", tokens_in), ("tokens_out", tokens_out), ("cost_usd", cost_usd)):
            if val is None:
                continue
            w[key] = w.get(key, 0) + val
        save_json(path, d)

    # ------------------------------------------------------------------
    def today(self) -> dict:
        return load_json(self._file(today_local()), {"workers": {}}) or {"workers": {}}

    def calls_today(self, worker: str) -> int:
        return int(self.today().get("workers", {}).get(worker, {}).get("calls", 0))

    def budget_exceeded(self, name: str, cfg: WorkerConfig) -> bool:
        limit = cfg.daily_call_limit
        if not limit or limit <= 0:
            return False
        return self.calls_today(name) >= limit

    def budget_status(self, name: str, cfg: WorkerConfig) -> str:
        if not cfg.daily_call_limit:
            return "unlimited"
        used = self.calls_today(name)
        return f"{used}/{cfg.daily_call_limit}"

    # ------------------------------------------------------------------
    def report(self, days: int = 7) -> str:
        """直近 days 日分の使用量レポート (markdown 表)。"""
        rows = []
        import datetime as dt

        for i in range(days):
            day = (dt.date.today() - dt.timedelta(days=i)).isoformat()
            d = load_json(self._file(day), {"workers": {}}) or {"workers": {}}
            for wname, w in (d.get("workers") or {}).items():
                rows.append(
                    "| {day} | {worker} | {calls} | {fails} | {sec} | {ti} | {to} | ${cost} |".format(
                        day=day,
                        worker=wname,
                        calls=w.get("calls", 0),
                        fails=w.get("fails", 0),
                        sec=round(w.get("seconds", 0.0)),
                        ti=w.get("tokens_in", "-"),
                        to=w.get("tokens_out", "-"),
                        cost=round(w.get("cost_usd", 0) or 0, 2),
                    )
                )
        header = "| 日付 | worker | 呼出 | 失敗 | 秒 | tok_in | tok_out | cost |\n|---|---|---|---|---|---|---|---|"
        return header + ("\n" + "\n".join(rows) if rows else "\n| (記録なし) | | | | | | | |")
