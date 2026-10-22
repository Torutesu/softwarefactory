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

# サブスク (ログイン済みプラン) を既定とする: codex == OpenAI (ChatGPT Plus/Pro/Team),
# openrouter は API キー制。mock は最後のフォールバック。
DEFAULT_WORKER_ORDER = ["claude_code", "codex", "opencode", "openrouter", "mock"]

# マージゲート: 変更の blast radius を 4 ティアに分け、ティアごとに自動マージの可否を決める。
#   mechanical  = テスト/ドキュメント/バージョン等の機械的変更
#   scoped      = 変更行数が少ない (既定 50 行以下) す。限定された修正
#   behavioral  = 製品の振る舞いに触れる差分 (大きな差分 / greenfield)
#   sensitive   = auth・決済・認証情報など領域センシティブなパスを含む
DEFAULT_MERGE_POLICY = {
    "mechanical": "auto",
    "scoped": "auto",
    "behavioral": "review",
    "sensitive": "review",
}
# sensitive 判定のパスパターン (部分一致・大文字小文字を無視)
DEFAULT_SENSITIVE_PATHS = [
    "auth", "credential", "password", "secret", "token",
    "payment", "billing", "checkout", "crypto", "oauth", "jwt", "session",
    "permission", "migrat",
]
# behavioral と scoped の境界 (変更行数)。dotnet/runtime のデータでは 1〜50 行の
# agent PR は 76〜80% 合流、大きな変更は失敗率が上がる (Firecrawl 記事参照)。
DEFAULT_SMALL_CHANGE_LINES = 50
# 変更されると CI 実行内容・ビルド・テスト判定そのものが変わるパス (glob, リポジトリ相対)。
# 自動マージで self-hosted runner にワークフローを持ち込ませない、テストを弱めさせないため sensitive 扱い
DEFAULT_PROTECTED_PATHS = [
    ".github/**", ".gitlab-ci.yml", ".circleci/**", ".buildkite/**",
    "**/conftest.py", "pytest.ini", "tox.ini", "noxfile.py", "setup.cfg", "setup.py",
    "pyproject.toml", "package.json", "Makefile", "**/Makefile", "Dockerfile", "**/Dockerfile",
    ".pre-commit-config.yaml", ".claude/**", ".factory/**", ".worktreeinclude", ".gitattributes",
]
# worktree は空の checkout なので、gitignore されがちな環境ファイルをコピーする
DEFAULT_WORKTREE_COPY = [".env", ".env.*", "config/secrets.json", "*.local"]


class ConfigError(Exception):
    """config.json が読めない / 検証エラー。CLI は終了コード 2 で止める。"""


@dataclass
class WorkerConfig:
    """worker種別ごとの設定。"""

    type: str = "claude"  # claude | codex | opencode | openrouter | mock
    command: str = ""  # 空なら type から決定 (openrouter は CLI なし)
    args: List[str] = field(default_factory=list)  # CLI 追加引数
    model: str = ""  # 例: sonnet / gpt-5.2 / anthropic/claude-sonnet-4.5
    timeout: int = 1800  # 秒
    daily_call_limit: int = 0  # 0 = 無制限 (サブスク枠の保護用)
    window_call_limit: int = 0  # 0 = 無制限 (rolling window の上限)
    window_minutes: int = 300  # rolling window の幅 (分)。サブスク系は 300 = 5h
    resume: bool = False  # セッション継続 (前反復の続きから)
    auth: str = "subscription"  # subscription (ログイン済みプラン・既定) | api_key
    allowed_tools: List[str] = field(default_factory=list)  # claude: --allowedTools
    disallowed_tools: List[str] = field(default_factory=list)  # claude: --disallowedTools
    env_passthrough: List[str] = field(default_factory=list)  # 秘密っぽい環境変数のうち、この CLI に渡すもの
    max_turns: int = 0  # claude: --max-turns (0 = 指定しない)

    def resolved_command(self) -> str:
        if self.command:
            return self.command
        return {
            "claude": "claude",
            "codex": "codex",
            "opencode": "opencode",
            "mock": "python3",
            "openrouter": "",  # CLI なし (HTTP 直呼び)
        }.get(self.type, self.type)


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
    use_worker: str = "auto"  # auto | claude_code | codex | opencode | openrouter | mock
    worker_order: List[str] = field(default_factory=lambda: list(DEFAULT_WORKER_ORDER))
    judge_worker: str = ""  # 空なら worker_order の先頭
    purpose_workers: Dict[str, List[str]] = field(default_factory=dict)  # 用途(spec/holdout/dev/judge) -> 優先ワーカー順
    max_attempts: int = 2
    judge_pass_score: int = 80
    autonomy_level: int = 1  # 1..4 (docs/design.md のファクトリークロック参照)
    autopilot_project: str = ""  # level>=3 の自動ミッション作成先 (未設定なら唯一の project)
    notify_url: str = ""  # tick の要約を POST する Webhook (空なら印刷のみ)
    tick_concurrency: int = 2  # tick が並列実行するミッション上限
    max_repair_rounds: int = 1  # カナリア失敗→修復の自動往復上限 (0 で修復しない)
    merge_policy: Dict[str, str] = field(
        default_factory=lambda: dict(DEFAULT_MERGE_POLICY))  # tier -> auto | review
    sensitive_paths: List[str] = field(
        default_factory=lambda: list(DEFAULT_SENSITIVE_PATHS))  # sensitive 判定パターン
    small_change_lines: int = DEFAULT_SMALL_CHANGE_LINES  # scoped/behavioral の境界行数
    intake_dup_threshold: float = 0.85  # intake 重複ゲートの類似度閾値 (0 で無効)
    worktree_copy: List[str] = field(
        default_factory=lambda: list(DEFAULT_WORKTREE_COPY))  # worktree へ複製する非追跡ファイル
    noprogress_iterations: int = 2  # 連続でこの回数、実差分ゼロの反復が続いたら打ち切り (0 で無効)
    require_reviewable: bool = True  # build.json の test もホールドアウトも無いミッションは dev に入らない
    judge_on_harness_fail: bool = False  # harness 失敗時も judge を呼ぶか (既定: 呼ばずに枠を節約)
    judge_required: bool = True  # judge の判定が得られないときは合格にしない (fail-closed)
    max_recover_attempts: int = 2  # 実行途中で落ちたミッションを tick が queued に戻す回数の上限
    judge_diff_chars: int = 4000  # judge に見せる実差分 (git diff) の上限文字数
    harness_env_passthrough: List[str] = field(default_factory=list)  # build/test/シナリオに渡す秘密っぽい環境変数
    allow_mock_fallback: bool = False  # use_worker=auto のとき mock を最後のフォールバックに使うか
    protected_paths: List[str] = field(
        default_factory=lambda: list(DEFAULT_PROTECTED_PATHS))  # 触ったら sensitive (CI/ビルド/テスト基盤)
    collect: Dict[str, object] = field(default_factory=dict)  # 定時収集設定 (sources 等の素通し dict)
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
            "purpose_workers": {k: list(v) for k, v in (self.purpose_workers or {}).items()},
            "max_attempts": self.max_attempts,
            "judge_pass_score": self.judge_pass_score,
            "autonomy_level": self.autonomy_level,
            "autopilot_project": self.autopilot_project,
            "notify_url": self.notify_url,
            "tick_concurrency": self.tick_concurrency,
            "max_repair_rounds": self.max_repair_rounds,
            "merge_policy": dict(self.merge_policy),
            "sensitive_paths": list(self.sensitive_paths),
            "small_change_lines": self.small_change_lines,
            "intake_dup_threshold": self.intake_dup_threshold,
            "worktree_copy": list(self.worktree_copy),
            "noprogress_iterations": self.noprogress_iterations,
            "require_reviewable": self.require_reviewable,
            "judge_on_harness_fail": self.judge_on_harness_fail,
            "judge_required": self.judge_required,
            "max_recover_attempts": self.max_recover_attempts,
            "judge_diff_chars": self.judge_diff_chars,
            "harness_env_passthrough": list(self.harness_env_passthrough),
            "allow_mock_fallback": self.allow_mock_fallback,
            "protected_paths": list(self.protected_paths),
            "collect": dict(self.collect),
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
                window_call_limit=int(raw.get("window_call_limit", 0)),
                window_minutes=int(raw.get("window_minutes", 300)),
                resume=bool(raw.get("resume", False)),
                auth=raw.get("auth", "subscription"),
                allowed_tools=list(raw.get("allowed_tools") or []),
                disallowed_tools=list(raw.get("disallowed_tools") or []),
                env_passthrough=list(raw.get("env_passthrough") or []),
                max_turns=int(raw.get("max_turns", 0) or 0),
            )
        return cls(
            root=data.get("root") or os.getcwd(),
            use_worker=data.get("use_worker", "auto"),
            worker_order=list(data.get("worker_order") or DEFAULT_WORKER_ORDER),
            judge_worker=data.get("judge_worker", ""),
            purpose_workers={k: list(v) for k, v in (data.get("purpose_workers") or {}).items() if v},
            max_attempts=int(data.get("max_attempts", 2)),
            judge_pass_score=int(data.get("judge_pass_score", 80)),
            autonomy_level=int(data.get("autonomy_level", 1)),
            autopilot_project=data.get("autopilot_project", ""),
            notify_url=data.get("notify_url", ""),
            tick_concurrency=int(data.get("tick_concurrency", 2)),
            max_repair_rounds=int(data.get("max_repair_rounds", 1)),
            merge_policy={**DEFAULT_MERGE_POLICY, **(data.get("merge_policy") or {})},
            sensitive_paths=(list(data["sensitive_paths"]) if data.get("sensitive_paths") is not None
                             else list(DEFAULT_SENSITIVE_PATHS)),
            small_change_lines=int(data.get("small_change_lines", DEFAULT_SMALL_CHANGE_LINES)),
            intake_dup_threshold=float(data.get("intake_dup_threshold", 0.85)),
            worktree_copy=(list(data["worktree_copy"]) if "worktree_copy" in data
                           else list(DEFAULT_WORKTREE_COPY)),
            noprogress_iterations=int(data.get("noprogress_iterations", 2)),
            require_reviewable=bool(data.get("require_reviewable", True)),
            judge_on_harness_fail=bool(data.get("judge_on_harness_fail", False)),
            judge_required=bool(data.get("judge_required", True)),
            max_recover_attempts=int(data.get("max_recover_attempts", 2)),
            judge_diff_chars=int(data.get("judge_diff_chars", 4000)),
            harness_env_passthrough=list(data.get("harness_env_passthrough") or []),
            allow_mock_fallback=bool(data.get("allow_mock_fallback", False)),
            protected_paths=(list(data["protected_paths"]) if data.get("protected_paths") is not None
                             else list(DEFAULT_PROTECTED_PATHS)),
            collect=dict(data.get("collect") or {}),
            projects=projects,
            workers=workers,
        )

    # ---------------------------------------------------------------
    @classmethod
    def load(cls, path: Path) -> "Config":
        """config.json を読んで検証する。不正なら ConfigError (全エラーを列挙)。"""
        from .configcheck import errors, validate

        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise ConfigError(f"{path}: JSON として読めません (line {e.lineno}: {e.msg})") from None
        except OSError as e:
            raise ConfigError(f"{path}: 読めません ({e})") from None
        errs = errors(validate(data))
        if errs:
            raise ConfigError(f"{path} に {len(errs)} 件のエラー:\n" + "\n".join(f"  {i}" for i in errs))
        try:
            return cls.from_dict(data)
        except (KeyError, TypeError, ValueError) as e:
            raise ConfigError(f"{path}: 値が不正です ({type(e).__name__}: {e})") from None

    @classmethod
    def find_file(cls) -> Optional[Path]:
        cur = Path(os.getcwd()).resolve()
        for base in (cur, *cur.parents):
            cand = base / ".factory" / "config.json"
            if cand.is_file():
                return cand
        return None

    @classmethod
    def discover(cls) -> Optional["Config"]:
        """カレントから .factory/config.json をさかのぼって探す (不正なら ConfigError)。"""
        cand = cls.find_file()
        if cand is None:
            return None
        cfg = cls.load(cand)
        cfg.root = str(cand.parent.parent)
        return cfg

    @classmethod
    def create(cls, root: str, demo: bool = False) -> "Config":
        root = str(Path(root).resolve())
        cfg = cls(root=root, workers={
            # サブスク (ログイン済みプラン) 既定。API キー課金にするなら auth を "api_key" へ。
            "claude_code": WorkerConfig(type="claude", auth="subscription",
                                        daily_call_limit=60, window_call_limit=30),
            "codex": WorkerConfig(type="codex", auth="subscription",
                                  daily_call_limit=40, window_call_limit=20),
            "opencode": WorkerConfig(type="opencode"),
            "openrouter": WorkerConfig(type="openrouter", auth="api_key"),
            "mock": WorkerConfig(type="mock"),
        })
        if demo:
            demo_src = Path(__file__).resolve().parent / "data" / "demo_app"
            seed_src = Path(__file__).resolve().parent / "data" / "demo_seed"
            proj_path = Path(root) / "products" / "demo_app"
            if not proj_path.exists():
                # バイトコードを製品リポジトリに入れない (並列ミッションのマージ競合の元)
                shutil.copytree(demo_src, proj_path,
                                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
                (proj_path / ".gitignore").write_text("__pycache__/\n*.pyc\n", encoding="utf-8")
            seed_dst = Path(root) / "seeds" / "demo-app"
            if not seed_dst.exists():
                shutil.copytree(seed_src, seed_dst,
                                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            cfg.projects["demo_app"] = ProjectConfig(name="demo_app", path="products/demo_app")
            cfg.use_worker = "mock"
        return cfg


def save(cfg: Config) -> Path:
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    cfg.state_file.write_text(json.dumps(cfg.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return cfg.state_file
