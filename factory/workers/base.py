"""Worker 共通の結果型と基底クラス。"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..util import ProcResult, run_cmd, scrubbed_env

QUOTA_PATTERNS = [
    r"usage limit",
    r"rate.?limit",
    r"limit reached",
    r"quota",
    r"credit balance",
    r"billing",
    r"too many requests",
    r"overloaded",
    r"\b429\b",
]

# auth="subscription" のときは CLI がログイン済みプラン (Claude Pro/Max, ChatGPT
# Plus/Pro など) を使うよう、課金向けの API キーを環境から外す (キーがあるとそちら優先で
# API 課金されてしまうため)。
AUTH_SCRUB_KEYS = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_API_TOKEN",
    "OPENAI_API_KEY",
)


# auth="api_key" のときに各 CLI が使う鍵。subscription の headless ログイン用トークン
# (Mac mini で Keychain を使わない運用) は auth に関係なく残す
API_KEY_ENV_BY_TYPE = {
    "claude": ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"),
    "codex": ("OPENAI_API_KEY",),
    "opencode": ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY"),
    "openrouter": ("OPENROUTER_API_KEY",),
}
SUBSCRIPTION_ENV_BY_TYPE = {
    "claude": ("CLAUDE_CODE_OAUTH_TOKEN",),
}


def child_env_for(worker_cfg, extra: Optional[dict] = None) -> dict:
    """ワーカー CLI 用の環境。秘密っぽい変数は既定で外し、必要なものだけ通す。

    - subscription (既定): API キーを外す (ログイン済みプランを使わせる。キーがあると API 課金に流れる)
    - api_key: その CLI 種別が使う鍵だけ残す
    - どちらでも FACTORY_LISTEN_SECRET / GH_TOKEN / SSH_AUTH_SOCK 等は渡さない (env_passthrough で個別に許可)
    """
    wtype = getattr(worker_cfg, "type", "")
    keep = list(getattr(worker_cfg, "env_passthrough", None) or [])
    keep += list(SUBSCRIPTION_ENV_BY_TYPE.get(wtype, ()))
    if getattr(worker_cfg, "auth", "subscription") == "api_key":
        keep += list(API_KEY_ENV_BY_TYPE.get(wtype, ()))
    env = scrubbed_env(passthrough=keep)
    if getattr(worker_cfg, "auth", "subscription") == "subscription":
        for k in AUTH_SCRUB_KEYS:
            if k not in (getattr(worker_cfg, "env_passthrough", None) or []):
                env.pop(k, None)
    if extra:
        env.update({k: str(v) for k, v in extra.items()})
    return env


@dataclass
class WorkerResult:
    worker: str
    ok: bool
    text: str = ""
    seconds: float = 0.0
    tokens_in: Optional[int] = None
    tokens_out: Optional[int] = None
    cost_usd: Optional[float] = None
    session_id: Optional[str] = None
    fail_kind: str = ""  # quota | timeout | unknown | ""
    raw_tail: str = ""

    @property
    def transient(self) -> bool:
        return self.fail_kind in ("quota", "timeout")


def classify_failure(stdout: str, stderr: str) -> str:
    """失敗の種類。stdout はモデルの本文 (例: 課金機能の実装なら "billing" を含む) なので
    末尾のエラー部分だけを見る。stderr は全体を見る。"""
    blob = (stderr or "") + "\n" + (stdout or "")[-600:]
    low = blob.lower()
    for p in QUOTA_PATTERNS:
        if re.search(p, low):
            return "quota"
    return "unknown"


class Worker:
    """非対話 CLI ワーカーの基底。サブクラスは build_args / parse_output を実装。

    resume: 前セッションの ID (config.resume が有効なときのみ使用)。
    """

    name = "base"

    def __init__(self, cfg):
        self.cfg = cfg

    def build_args(self, prompt: str, resume: Optional[str] = None) -> list:
        raise NotImplementedError

    def parse_output(self, pr: ProcResult) -> WorkerResult:
        raise NotImplementedError

    # ------------------------------------------------------------------
    def run(self, prompt: str, cwd: Path, timeout: Optional[int] = None, resume: Optional[str] = None) -> WorkerResult:
        timeout = timeout or self.cfg.timeout
        args = self.build_args(prompt, resume=resume)
        pr = run_cmd(args, cwd=cwd, timeout=timeout, env=child_env_for(self.cfg))
        res = self.parse_output(pr)
        res.seconds = pr.seconds
        res.worker = self.name
        if pr.timed_out:
            res.ok = False
            res.fail_kind = "timeout"
            res.raw_tail = (pr.out or pr.err)[-800:]
        if not res.ok and not res.fail_kind:
            res.fail_kind = classify_failure(pr.out, pr.err)
        return res


class AllWorkersFailed(RuntimeError):
    def __init__(self, results: list):
        detail = "; ".join(
            f"{r.worker}:{r.fail_kind or 'unknown'}" for r in results
        )
        super().__init__("全ワーカーが失敗しました: " + detail)
        self.results = results
