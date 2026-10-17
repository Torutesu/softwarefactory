"""使用量モニタリング: 日次リミット + 時間窓 (サブスクの様な rolling window) の二段管理。

保存先: .factory/usage/YYYY-MM-DD.json

記録内容 (workerごと):
  calls / fails / seconds / tokens / cost
  ts_list: 呼び出し時刻のリスト (rolling window 計算用, 直近 500 件)
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Optional

from .config import WorkerConfig
from .util import ensure_dir, load_json, save_json, today_local

DEFAULT_WINDOW_MINUTES = 300  # サブスク系の 5 時間窓を想定
MAX_TS = 2000


class Usage:
    def __init__(self, state_dir: Path):
        self.dir = ensure_dir(state_dir / "usage")

    # ------------------------------------------------------------------
    def _file(self, day: str) -> Path:
        return self.dir / f"{day}.json"

    def _day_dict(self, day: str) -> dict:
        return load_json(self._file(day), {"workers": {}}) or {"workers": {}}

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
        d = self._day_dict(day)
        w = d["workers"].setdefault(worker, {"calls": 0, "fails": 0, "seconds": 0.0})
        w["calls"] += 1
        if not ok:
            w["fails"] += 1
        w["seconds"] = round(w.get("seconds", 0.0) + seconds, 1)
        w.setdefault("calls_by_purpose", {})
        w["calls_by_purpose"][purpose] = w["calls_by_purpose"].get(purpose, 0) + 1
        ts = dt.datetime.now(dt.timezone.utc).isoformat()
        w.setdefault("ts_list", [])
        w["ts_list"] = (w["ts_list"] + [ts])[-MAX_TS:]
        for key, val in (("tokens_in", tokens_in), ("tokens_out", tokens_out), ("cost_usd", cost_usd)):
            if val is None:
                continue
            w[key] = w.get(key, 0) + val
        save_json(self._file(day), d)

    # ------------------------------------------------------------------
    def today(self) -> dict:
        return self._day_dict(today_local())

    def calls_today(self, worker: str) -> int:
        return int(self.today().get("workers", {}).get(worker, {}).get("calls", 0))

    def budget_exceeded(self, name: str, cfg: WorkerConfig) -> bool:
        limit = cfg.daily_call_limit
        if not limit or limit <= 0:
            return False
        return self.calls_today(name) >= limit

    def window_budget_exceeded(self, name: str, cfg: WorkerConfig, now: Optional[dt.datetime] = None) -> bool:
        """rolling window (直先 cfg.window_minutes 分) の呼び出し数が上限 超?"""
        limit = cfg.window_call_limit
        if not limit or limit <= 0:
            return False
        now = now or dt.datetime.now(dt.timezone.utc)
        minutes = cfg.window_minutes or DEFAULT_WINDOW_MINUTES
        cutoff = now - dt.timedelta(minutes=minutes)
        count = 0
        for back in range(2):  # 今日 + 昨日
            day = (now.date() - dt.timedelta(days=back)).isoformat()
            w = self._day_dict(day).get("workers", {}).get(name, {})
            for ts in w.get("ts_list", []):
                try:
                    t = dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
                except ValueError:
                    continue
                if t.tzinfo is None:
                    t = t.replace(tzinfo=dt.timezone.utc)
                if t >= cutoff:
                    count += 1
        return count >= limit

    def budget_status(self, name: str, cfg: WorkerConfig) -> str:
        parts = []
        if cfg.daily_call_limit:
            parts.append(f"day {self.calls_today(name)}/{cfg.daily_call_limit}")
        if cfg.window_call_limit:
            parts.append(f"win {self.window_calls(name, cfg)}/{cfg.window_call_limit}")
        return ", ".join(parts) if parts else "unlimited"

    def window_calls(self, name: str, cfg: WorkerConfig, now: Optional[dt.datetime] = None) -> int:
        now = now or dt.datetime.now(dt.timezone.utc)
        minutes = cfg.window_minutes or DEFAULT_WINDOW_MINUTES
        cutoff = now - dt.timedelta(minutes=minutes)
        count = 0
        for back in range(2):
            day = (now.date() - dt.timedelta(days=back)).isoformat()
            w = self._day_dict(day).get("workers", {}).get(name, {})
            for ts in w.get("ts_list", []):
                try:
                    t = dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
                except ValueError:
                    continue
                if t.tzinfo is None:
                    t = t.replace(tzinfo=dt.timezone.utc)
                if t >= cutoff:
                    count += 1
        return count

    # ------------------------------------------------------------------
    def report(self, days: int = 7) -> str:
        """直近 days 日分の使用量レポート (markdown 表)。"""
        rows = []
        for i in range(days):
            day = (dt.date.today() - dt.timedelta(days=i)).isoformat()
            d = self._day_dict(day)
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
