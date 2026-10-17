"""設定 (config) の読み書き。

単一JSONファイル (.factory/config.json) を唯一の真実として扱う。
Worker (Claude Code / OpenCode / Mock) の切り替え・予算もここで行う。
"""
from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

DEFAULT_TEST_CMD = "python3 -m unittest discover -s tests -p 'test_*.py' -t ."
DEFAULT_MAIN_BRANCH = "main"

DEFAULT_WORKER_ORDER = ["claude_code", "opencode", "mock"]


@dataclass
class WorkerConfig:
    """worker種別ごとの設定。"""

    type: str = "claude"  # claude | opencode | mock
    command: str = ""  # 空なら type から決定
    args: List[str] = field(default_factory=list)  # CLI 追加引数
    model: str = ""  # 例: sonnet / anthropic/claude-sonnet-4-5
    timeout: int = 1800  # 秒
    daily_call_limit: int = 0  # 0 = 無制限 (サブスク枠の保護用)

    def resolved_command(self) -> str:
        if self.command:
            return self.command
        return {"claude": "claude", "opencode": "opencode", "mock": "python3"}.get(self.type, self.type)


@dataclass
class ProjectConfig:
    """製品リポジトリ1件分の登録情報。"""

    name: str
    path: str  # リポジトリのルート (root からの相対 or 絶対パス)
    main_branch: str = DEFAULT_MAIN_BRANCH
    test_cmd: str = DEFAULT_TEST_CMD

    @property
    def holdout_dir(self) -> str:
        # ホールドアウトは必ずコードベース外 (ファクトリ側) に置く
        return "holdout"


@dataclass
class Config:
    root: str  # ファクトリ全体の作業ルート (絶対パス)
    use_worker: str = "auto"  # auto | claude_code | opencode | mock
    worker_order: List[str] = field(default_factory=lambda: list(DEFAULT_WORKER_ORDER))
    judge_worker: str = ""  # 空なら worker_order の先頭
    max_attempts: int = 2
    judge_pass_score: int = 80
    autonomy_level: int = 1  # 1..4 (docs/design.md 参照)
    projects: Dict[str, ProjectConfig] = field(default_factory=dict)
    workers: Dict[str, WorkerConfig] = field(default_factory=dict)

    # ---------------------------------------------------------------
    @property
    def state_dir(self) -> Path:
        return Path(self.root) / ".factory"

    @property
    def state_file(self) -> Path:
        return self.state_dir / "config.json"

    # ---------------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "use_worker": self.use_worker,
            "worker_order": self.worker_order,
            "judge_worker": self.judge_worker,
            "max_attempts": self.max_attempts,
            "judge_pass_score": self.judge_pass_score,
            "autonomy_level": self.autonomy_level,
            "root": self.root,
            "projects": {k: vars(v) for k, v in self.projects.items()},
            "workers": {k: vars(v) for k, v in self.workers.items()},
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Config":
        projects = {}
        for name, raw in (data.get("projects") or {}).items():
            projects[name] = ProjectConfig(
                name=raw.get("name", name),
                path=raw["path"],
                main_branch=raw.get("main_branch", DEFAULT_MAIN_BRANCH),
                test_cmd=raw.get("test_cmd", DEFAULT_TEST_CMD),
            )
        workers = {}
        for name, raw in (data.get("workers") or {}).items():
            workers[name] = WorkerConfig(
                type=raw.get("type", name),
                command=raw.get("command", ""),
                args=list(raw.get("args") or []),
                model=raw.get("model", ""),
                timeout=int(raw.get("timeout", 1800)),
                daily_call_limit=int(raw.get("daily_call_limit", 0)),
            )
        return cls(
            root=data.get("root") or os.getcwd(),
            use_worker=data.get("use_worker", "auto"),
            worker_order=list(data.get("worker_order") or DEFAULT_WORKER_ORDER),
            judge_worker=data.get("judge_worker", ""),
            max_attempts=int(data.get("max_attempts", 2)),
            judge_pass_score=int(data.get("judge_pass_score", 80)),
            autonomy_level=int(data.get("autonomy_level", 1)),
            projects=projects,
            workers=workers,
        )

    # ---------------------------------------------------------------
    @classmethod
    def discover(cls) -> Optional["Config"]:
        """カレントから .factory/config.json をさかのぼって探す。"""
        cur = Path(os.getcwd()).resolve()
        for base in (cur, *cur.parents):
            cand = base / ".factory" / "config.json"
            if cand.is_file():
                cfg = cls.from_dict(json.loads(cand.read_text(encoding="utf-8")))
                cfg.root = str(base)
                return cfg
        return None

    @classmethod
    def create(cls, root: str, demo: bool = False) -> "Config":
        root = str(Path(root).resolve())
        cfg = cls(root=root, workers={
            "claude_code": WorkerConfig(type="claude"),
            "opencode": WorkerConfig(type="opencode"),
            "mock": WorkerConfig(type="mock"),
        })
        if demo:
            demo_src = Path(__file__).resolve().parent / "data" / "demo_app"
            seed_src = Path(__file__).resolve().parent / "data" / "demo_seed"
            proj_path = Path(root) / "products" / "demo_app"
            if not proj_path.exists():
                shutil.copytree(demo_src, proj_path)
            seed_dst = Path(root) / "seeds" / "demo-app"
            if not seed_dst.exists():
                shutil.copytree(seed_src, seed_dst)
            cfg.projects["demo_app"] = ProjectConfig(name="demo_app", path="products/demo_app")
            cfg.use_worker = "mock"
        return cfg


def save(cfg: Config) -> Path:
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    cfg.state_file.write_text(json.dumps(cfg.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return cfg.state_file
