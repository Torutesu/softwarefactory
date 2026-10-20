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
from .state import mission_cost_usd
from .util import ensure_dir, load_json, locked, save_json, today_local

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
        # 読んで→書くの区間全体を flock (run-all --parallel / supervisor 並列時のロスト防止)
        with locked(self._file(day)):
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

    def _file(self, day: str) -> Path:
        return self.dir / f"{day}.json"

    def _day_dict(self, day: str) -> dict:
        return load_json(self._file(day), {"day": day, "workers": {}}) or {"day": day, "workers": {}}

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

    def usage_ratio(self, name: str, cfg: WorkerConfig, now: Optional[dt.datetime] = None) -> float:
        """日次 / rolling 窓 のうち「より消費が進んだ方」の使用率 (0.0〜1.0+)。

        上限未設定のワーカーは 0.0。tick の予算 nudge (50% / 80%) に使う
        (Uber の spend tiers: 50/80/100% で通知、ただし硬い上限ではなく接近情報)。
        """
        ratios = []
        if cfg.daily_call_limit:
            ratios.append(self.calls_today(name) / float(cfg.daily_call_limit))
        if cfg.window_call_limit:
            ratios.append(self.window_calls(name, cfg, now) / float(cfg.window_call_limit))
        return max(ratios) if ratios else 0.0

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
        """直近 days 日分の使用量レポート (markdown 表) + 期間合計。"""
        rows = []
        ttl = {"calls": 0, "fails": 0, "sec": 0.0, "tokens_in": 0, "tokens_out": 0, "cost": 0.0}
        for i in range(days):
            day = (dt.date.today() - dt.timedelta(days=i)).isoformat()
            d = self._day_dict(day)
            for wname, w in (d.get("workers") or {}).items():
                calls, fails = w.get("calls", 0), w.get("fails", 0)
                ti = w.get("tokens_in") or 0
                to = w.get("tokens_out") or 0
                cost = w.get("cost_usd") or 0
                ttl["calls"] += calls
                ttl["fails"] += fails
                ttl["sec"] += w.get("seconds", 0.0)
                ttl["tokens_in"] += ti
                ttl["tokens_out"] += to
                ttl["cost"] += cost
                rows.append(
                    "| {day} | {worker} | {calls} | {fails} | {sec} | {ti} | {to} | ${cost} |".format(
                        day=day,
                        worker=wname,
                        calls=calls,
                        fails=fails,
                        sec=round(w.get("seconds", 0.0)),
                        ti=ti or "-",
                        to=to or "-",
                        cost=round(cost, 2),
                    )
                )
        header = "| 日付 | worker | 呼出 | 失敗 | 秒 | tok_in | tok_out | cost |\n|---|---|---|---|---|---|---|---|"
        out = header + ("\n" + "\n".join(rows) if rows else "\n| (記録なし) | | | | | | | |")
        out += ("\n\n**期間合計** ({}日): 呼出 {} / 失敗 {} / 秒 {} / tok_in {} / tok_out {} / "
                "${}".format(days, ttl["calls"], ttl["fails"], round(ttl["sec"]),
                             ttl["tokens_in"], ttl["tokens_out"], round(ttl["cost"], 2)))
        return out

    # ------------------------------------------------------------------
    def unit_economics(self, days: int = 7) -> dict:
        """ワーカー単位の単価統計 (cost per 1,000 calls / tokens per call)。"""
        agg = {}
        for i in range(days):
            day = (dt.date.today() - dt.timedelta(days=i)).isoformat()
            for wname, w in (self._day_dict(day).get("workers") or {}).items():
                a = agg.setdefault(wname, {"calls": 0, "fails": 0, "tokens_in": 0, "tokens_out": 0, "cost": 0.0})
                a["calls"] += w.get("calls", 0)
                a["fails"] += w.get("fails", 0)
                a["tokens_in"] += w.get("tokens_in") or 0
                a["tokens_out"] += w.get("tokens_out") or 0
                a["cost"] += w.get("cost_usd") or 0
        out = {}
        for wname, a in agg.items():
            calls = a["calls"] or 1
            out[wname] = {
                "calls": a["calls"],
                "fails": a["fails"],
                "tokens_per_call_in": round(a["tokens_in"] / calls) if a["tokens_in"] else None,
                "tokens_per_call_out": round(a["tokens_out"] / calls) if a["tokens_out"] else None,
                "cost_per_1k_calls": round(a["cost"] * 1000 / calls, 2) if a["cost"] else None,
                "cost": round(a["cost"], 2),
            }
        return out

    def waste_report(self, factory=None, days: int = 7) -> str:
        """セッション分析 (簡易版): 記録から「怖い無駄の兆候」を拾って列挙する。

        検出する兆候 (Uber の session analysis dashboard の一節に相当):
        - 失敗率の高いワーカー (quota 系エラーによる無駄な実行)
        - 前日比で cost が急増 (量ではなく単価が変わった) のワーカー
        - 反復上限ギリギリで合格したミッション (judge/revise が多用されている)
        - 失敗したミッション (成果物ゼロで消費した budget)
        """
        lines = ["## 無駄の兆候 (セッション分析)"]
        hints: list = []

        unit = self.unit_economics(days)
        for wname, u in unit.items():
            if u["calls"] >= 3 and u["fails"] >= max(2, int(u["calls"] * 0.2)):
                hints.append(f"- `{wname}`: 失敗率 {u['fails']}/{u['calls']} — quota/timeout は予算を直接燃やす")
        per_day_cost = {}
        for i in range(days):
            day = (dt.date.today() - dt.timedelta(days=i)).isoformat()
            for wname, w in (self._day_dict(day).get("workers") or {}).items():
                per_day_cost.setdefault(wname, {})[day] = w.get("cost_usd") or 0
        for wname, byday in per_day_cost.items():
            vals = [v for _, v in sorted(byday.items(), reverse=True)]
            if len(vals) >= 3 and vals[0] > 0 and sum(vals[1:]) > 0:
                base = sum(vals[1:]) / 2.0
                if vals[0] > base * 1.5:
                    hints.append(f"- `{wname}`: cost が直近2日平均の {vals[0] / base:.1f} 倍 — "
                                 "量ではなくモデル選択/単価が動いていないか確認 (purpose_workers / --model)")
        if unit and not hints:
            hints.append("- 大きな兆候なし (この窓で見える範囲)")
        lines += hints

        if factory is not None:
            ms = factory.list_missions()
            failed = [m for m in ms if m.status == "failed"]
            capped = [m for m in ms if m.status == "done" and len(m.iterations) >= max(1, m.max_iterations)]
            n_done = len([m for m in ms if m.status in ("done", "failed")])
            if n_done:
                avg_it = round(sum(len(m.iterations) for m in ms if m.iterations) / n_done, 1)
                lines += ["", "### ミッション単位 (成果単価の眺め)"]
                lines.append(f"- 反復平均 {avg_it} / 失敗 {len(failed)} / 上限ギリギリ合格 {len(capped)}")
                if failed:
                    lines.append(f"  - 失敗ミッション (成果物なしで消費): {', '.join(m.id for m in failed[:5])}"
                                 + ("..." if len(failed) > 5 else ""))
                if capped:
                    lines.append(f"  - 反復上限で合格 (revise ループが効いている疑い): {', '.join(m.id for m in capped[:5])}"
                                 + ("..." if len(capped) > 5 else ""))
                costs = [mission_cost_usd(m) for m in ms]
                costs = [c for c in costs if c is not None]
                if costs:
                    lines.append(f"- 実装コスト合計 (自己報告値): ${round(sum(costs), 2)} "
                                 f"— 稼働 {sum(1 for c in costs if c)} ミッション、1ミッション平均 ${round(sum(costs) / len(costs), 2)}")
        return "\n".join(lines)
