"""状態管理:ミッションは missions/<id>/mission.json、全体の索引は .factory/state.json。

- ミッション単位の完全な状態は mission.json に書く (単一の真実)。
- state.json は索引・シグナル・イベントログを保持する。
- 排他制御は fcntl ロック (.factory/locks/<id>.lock) で行う。
"""
from __future__ import annotations

import datetime as dt
import re
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from .util import load_json, locked, now_utc_iso, quarantine_corrupt, save_json

STATUSES = ["queued", "spec", "holdout", "dev", "done", "failed", "blocked"]
# dev 以降の実行は spec / holdout がそろっている必要がある


@dataclass
class Iteration:
    n: int
    worker: str
    started_utc: str
    seconds: float = 0.0
    status: str = "running"  # running|passed|failed
    score: Optional[int] = None
    report: str = ""
    note: str = ""
    tokens_in: Optional[int] = None  # 実装ワーカーの入力トークン (成果単価の計測用)
    tokens_out: Optional[int] = None
    cost_usd: Optional[float] = None  # ワーカーが自己報告したコスト (claude CLI 等)
    changed: Optional[bool] = None  # ワーカー呼出の前後で作業ツリーが変わったか (noprogress 検知)

    def to_dict(self) -> dict:
        return vars(self)


@dataclass
class Mission:
    id: str
    title: str
    kind: str = "greenfield"  # greenfield | update
    project: str = ""
    seed: str = ""  # seeds/<name>
    repo: str = ""  # root からの相対 or 絶対パス
    status: str = "queued"
    risk: str = "low"  # low | high
    priority: int = 5
    max_iterations: int = 3
    pass_score: int = 80
    model: str = ""  # ミッション単位のモデル上書き (空なら config のまま)
    session: dict = field(default_factory=dict)  # {"worker":..., "session_id":...} セッション継続用
    cost_usd: Optional[float] = None  # 実装ワーカー呼出の累計コスト (judge/spec は usage 側に集計)
    merged_utc: str = ""  # main へマージした時刻 (空 = 未マージ)。notes の文字列では判定しない
    merged_tier: str = ""
    repair_of: str = ""  # カナリア修復ミッションの親 ID
    recover_count: int = 0  # 孤児 (実行途中で落ちた) から queued に戻した回数
    halt_reason: str = ""  # 打ち切り理由: noprogress | max_iterations | all_workers_failed | unreviewable | holdout_leak | holdout_tamper | holdout_failed | judge_unavailable | crash | interrupted | shutdown | recovered | crash_loop
    created_utc: str = field(default_factory=now_utc_iso)
    updated_utc: str = field(default_factory=now_utc_iso)
    notes: str = ""
    iterations: List[Iteration] = field(default_factory=list)

    # ------------------------------------------------------------------
    def to_dict(self) -> dict:
        d = vars(self).copy()
        d["iterations"] = [it.to_dict() for it in self.iterations]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Mission":
        """未知のキーは無視する (新しい版が書いた mission.json を古い版でも読めるように)。"""
        d = dict(d)
        it_fields = set(Iteration.__dataclass_fields__)
        iters = [Iteration(**{k: v for k, v in it.items() if k in it_fields})
                 for it in (d.pop("iterations", None) or []) if isinstance(it, dict)]
        m = cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
        m.iterations = iters
        return m


_MISSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,80}$")
# 旧形式 (notes に書いていた頃) の互換判定。行頭一致のみ (外部入力の notes 行には現れない形)
_LEGACY_MERGED_RE = re.compile(r"^merged to main \(tier: [a-z]+\)\s*$", re.M)
_LEGACY_REPAIR_RE = re.compile(r"^auto-created by canary repair-of:(\S+)\s*$", re.M)


def valid_mission_id(mid: str) -> bool:
    return bool(mid) and bool(_MISSION_ID_RE.match(mid)) and ".." not in mid


def is_merged(m: "Mission") -> bool:
    return bool(m.merged_utc) or bool(_LEGACY_MERGED_RE.search(m.notes or ""))


def mark_merged(m: "Mission", tier: str) -> None:
    m.merged_utc = now_utc_iso()
    m.merged_tier = tier
    m.notes = (m.notes + "\n" if m.notes else "") + f"merged to main (tier: {tier})"


def repair_parent(m: "Mission") -> str:
    if m.repair_of:
        return m.repair_of
    mt = _LEGACY_REPAIR_RE.search(m.notes or "")
    return mt.group(1) if mt and valid_mission_id(mt.group(1)) else ""


def one_line(s: str, limit: int = 200) -> str:
    """外部入力を notes 等に埋め込む前に 1 行化して切り詰める。"""
    return " ".join(str(s or "").split())[:limit]


def new_mission_id() -> str:
    return dt.datetime.now().strftime("M%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)


def mission_cost_usd(m: "Mission") -> Optional[float]:
    """ミッションの実装ワーカー呼出の累計コスト (自己報告値の和。無ければ None)。"""
    vals = [it.cost_usd for it in m.iterations if it.cost_usd is not None]
    return round(sum(vals), 4) if vals else (m.cost_usd if m.cost_usd is not None else None)


class Factory:
    """ルートディレクトリに紐づくファクトリ操作の窓口。"""

    def __init__(self, root: Path):
        self.root = Path(root).resolve()

    # ---------- paths -------------------------------------------------
    @property
    def missions_dir(self) -> Path:
        return self.root / "missions"

    @property
    def holdout_dir(self) -> Path:
        return self.root / "holdout"

    @property
    def seeds_dir(self) -> Path:
        return self.root / "seeds"

    @property
    def repos_dir(self) -> Path:
        return self.root / "repos"

    @property
    def state_dir(self) -> Path:
        return self.root / ".factory"

    @property
    def state_file(self) -> Path:
        return self.state_dir / "state.json"

    def mission_dir(self, mid: str) -> Path:
        if not valid_mission_id(mid):
            raise ValueError(f"invalid mission id: {mid!r}")
        return self.missions_dir / mid

    def worktree(self, mid: str) -> Path:
        return self.mission_dir(mid) / "work"

    def spec_file(self, mid: str) -> Path:
        return self.mission_dir(mid) / "spec.md"

    def build_file(self, mid: str) -> Path:
        return self.mission_dir(mid) / "build.json"

    def holdout_dir_for(self, mid: str) -> Path:
        if not valid_mission_id(mid):
            raise ValueError(f"invalid mission id: {mid!r}")
        return self.holdout_dir / mid

    def reports_dir(self, mid: str) -> Path:
        return self.mission_dir(mid) / "reports"

    def run_dir(self, mid: str) -> Path:
        ts = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        d = self.mission_dir(mid) / "reports" / ("run-" + ts)
        d.mkdir(parents=True, exist_ok=True)
        return d

    # ---------- state -------------------------------------------------
    def state(self) -> dict:
        st = load_json(self.state_file, None)
        if st is None:
            # 壊れていたら退避してから空で始める (退避せずに次の save で上書きすると履歴が消える)
            moved = quarantine_corrupt(self.state_file)
            if moved:
                print(f"[state] 壊れた state.json を {moved.name} に退避しました", flush=True)
            st = {}
        if not isinstance(st, dict):
            st = {}
        st.setdefault("version", 1)
        st.setdefault("missions", {})
        st.setdefault("signals", [])
        st.setdefault("events", [])
        return st

    def save_state(self, st: dict) -> None:
        save_json(self.state_file, st)

    def _mutate_state(self, fn) -> None:
        """state.json を読んで変更して戻す RMW を flock で直列化 (並列実行のロスト防止)。"""
        with locked(self.state_file):
            st = self.state()
            fn(st)
            self.save_state(st)

    def index_mission(self, m: Mission) -> None:
        def fn(st: dict):
            st.setdefault("missions", {})[m.id] = {
                "id": m.id,
                "title": m.title,
                "status": m.status,
                "project": m.project,
                "seed": m.seed,
                "updated_utc": m.updated_utc,
            }

        self._mutate_state(fn)

    def event(self, kind: str, detail: str = "") -> None:
        def fn(st: dict):
            st.setdefault("events", []).append({"ts": now_utc_iso(), "kind": kind, "detail": detail[:500]})
            st["events"] = st["events"][-1000:]

        self._mutate_state(fn)

    def add_signal(self, seed: str, source: str, mission: str = "", detail: str = "") -> None:
        def fn(st: dict):
            st.setdefault("signals", []).append({
                "ts": now_utc_iso(),
                "seed": seed,
                "source": source,
                "mission": mission,
                "detail": detail[:300],
            })
            st["signals"] = st["signals"][-2000:]

        self._mutate_state(fn)

    # ---------- missions ----------------------------------------------
    def create_mission(
        self,
        title: str,
        kind: str = "greenfield",
        seed: str = "",
        repo: str = "",
        risk: str = "low",
        priority: int = 5,
        max_iterations: int = 3,
        pass_score: int = 80,
        model: str = "",
        notes: str = "",
    ) -> Mission:
        mid = new_mission_id()
        mdir = self.mission_dir(mid)
        for sub in ("prompts", "logs", "reports"):
            (mdir / sub).mkdir(parents=True, exist_ok=True)
        m = Mission(
            id=mid,
            title=title,
            kind=kind,
            seed=seed,
            repo=repo,
            risk=risk,
            priority=priority,
            max_iterations=max_iterations,
            pass_score=pass_score,
            model=model,
            notes=notes,
        )
        self.save_mission(m)
        self.index_mission(m)
        self.event("mission_created", f"{mid} {title}")
        return m

    def save_mission(self, m: Mission) -> None:
        m.updated_utc = now_utc_iso()
        save_json(self.mission_dir(m.id) / "mission.json", m.to_dict())
        self.index_mission(m)

    def get_mission(self, mid: str) -> Optional[Mission]:
        if not valid_mission_id(mid):
            return None
        p = self.mission_dir(mid) / "mission.json"
        d = load_json(p)
        if d is None and p.is_file():
            print(f"[state] {p} を読めません (JSON 不正)。手で直すか削除してください", flush=True)
            return None
        if not isinstance(d, dict) or not d:
            return None
        try:
            return Mission.from_dict(d)
        except (TypeError, ValueError) as e:
            print(f"[state] {p} の形式が不正です: {e}", flush=True)
            return None

    def list_missions(self) -> List[Mission]:
        out = []
        if self.missions_dir.exists():
            for d in sorted(self.missions_dir.iterdir()):
                if (d / "mission.json").is_file():
                    out.append(self.get_mission(d.name))
        return [m for m in out if m]

    # ---------- lock ---------------------------------------------------
    def lock(self, mid: str):
        return FileLock(self.state_dir / "locks" / f"{mid}.lock")


class FileLock:
    """fcntl ベースのブロッキングなしロック。取得できない場合は例外。"""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.fd = None

    def __enter__(self):
        import fcntl

        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fd = open(self.path, "w")
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.fd.close()
            self.fd = None
            raise RuntimeError("mission is locked by another process: %s" % self.path) from None
        return self

    def __exit__(self, *exc):
        import fcntl

        if self.fd:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
            self.fd.close()
            self.fd = None
        return False
