"""Worker Router: Claude Code / Codex / OpenCode / OpenRouter / Mock の切替とフォールバック。

- 用途別 (spec/holdout/dev/judge) の優先ワーカーを選び、予算超過や quota 系エラーで次へフォールバックする。
- 予算は「日次」と「rolling window (例: 5h)」の二段で判定する。
- resume 指定時は同一ワーカーへの割当時にセッション継続する。
- 純粋な HTTP チャット系 (OpenRouter) はテキスト用途 (spec/holdout/judge) に限る。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, Tuple

from ..config import WorkerConfig
from ..usage import Usage
from ..util import SHUTDOWN, which
from .base import AllWorkersFailed, Worker, WorkerResult
from .claude_code import ClaudeCodeWorker
from .codex import CodexWorker
from .opencode import OpenCodeWorker
from .openrouter import OpenRouterWorker

TYPE_MAP = {
    "claude_code": "claude",
    "claude": "claude",
    "codex": "codex",
    "opencode": "opencode",
    "openrouter": "openrouter",
    "mock": "mock",
}

# ワーカータイプが担える用途 (codex/claude/opencode はツール実行可=全用途)
PURPOSE_CAPS = {
    "claude": ("spec", "holdout", "dev", "judge"),
    "codex": ("spec", "holdout", "dev", "judge"),
    "opencode": ("spec", "holdout", "dev", "judge"),
    "mock": ("spec", "holdout", "dev", "judge"),
    "openrouter": ("spec", "holdout", "judge"),  # ツール不可なので実装 (dev) には使わない
}

ALL_PURPOSES = ("spec", "holdout", "dev", "judge")


class Router:
    def __init__(self, cfg, usage: Usage):
        self.cfg = cfg
        self.usage = usage

    # ------------------------------------------------------------------
    def worker_for(self, name: str) -> Optional[Worker]:
        wc = self.cfg.workers.get(name) or WorkerConfig(type=TYPE_MAP.get(name, name))
        t = TYPE_MAP.get(wc.type, wc.type)
        if t == "claude":
            return ClaudeCodeWorker(wc)
        if t == "codex":
            return CodexWorker(wc)
        if t == "opencode":
            return OpenCodeWorker(wc)
        if t == "openrouter":
            return OpenRouterWorker(wc)
        if t == "mock":
            from .mock import MockWorker

            return MockWorker(wc)
        return None

    def _mock_excluded(self) -> bool:
        use = getattr(self.cfg, "use_worker", "auto")
        if getattr(self.cfg, "allow_mock_fallback", False) or (use and use != "auto" and self._is_mock(use)):
            return False
        return any(self._type_of(n) in PURPOSE_CAPS and not self._is_mock(n) for n in self.cfg.worker_order)

    def _type_of(self, name: str) -> str:
        wc = (self.cfg.workers or {}).get(name)
        t = wc.type if wc else name
        return TYPE_MAP.get(t, t)

    def _is_mock(self, name: str) -> bool:
        return self._type_of(name) == "mock"

    def caps_for(self, name: str) -> tuple:
        w = self.worker_for(name)
        if w is None:
            return ()
        t = TYPE_MAP.get(w.cfg.type, w.cfg.type)
        return PURPOSE_CAPS.get(t, ALL_PURPOSES)

    def available_names(self) -> list:
        out = []
        for n in self.cfg.worker_order:
            w = self.worker_for(n)
            if w is None:
                continue
            t = TYPE_MAP.get(w.cfg.type, w.cfg.type)
            if t == "mock":
                out.append(n)  # 常時利用可
                continue
            if t == "openrouter":
                if os.environ.get("OPENROUTER_API_KEY", "").strip():
                    out.append(n)
                continue
            if not which(w.cfg.resolved_command()):
                continue  # CLI 未インストール → スキップ
            out.append(n)
        return out

    def candidates(self, purpose: str) -> list:
        order = list(self.cfg.worker_order)
        use = getattr(self.cfg, "use_worker", "auto")
        if use and use != "auto":
            order = [use] + [n for n in order if n != use]
        avail = set(self.available_names())
        base = [n for n in dict.fromkeys(order) if n in avail and purpose in self.caps_for(n)]
        # mock は prompt 中の mock-action (shell を含む) をそのまま実行する。本番で実ワーカーが
        # 枠切れになったときに mock へ落ちると、Seed 経由の外部テキストが host 上で実行され得る
        # うえ、何も実装しない「成功」になる。実ワーカーが 1 つでも設定されている構成では、
        # 明示選択 (use_worker=<mock>) か allow_mock_fallback のときだけ mock を候補に残す
        if self._mock_excluded():
            base = [n for n in base if not self._is_mock(n)]

        # 用途別ルーティング (Uber のコスト方程式: Price/token レバー):
        # テキスト生成系 (spec/holdout/judge) を安いワーカーに寄せ、dev はスキルフルな
        # ワーカーに任せる。purpose_workers が優先、指定が無ければ従来の judge_worker。
        preferred: list = []
        pw = (getattr(self.cfg, "purpose_workers", None) or {}).get(purpose)
        if not pw and purpose == "judge" and self.cfg.judge_worker:
            pw = [self.cfg.judge_worker]
        for n in (pw or []):
            if n in base:
                preferred.append(n)
        out = preferred + [n for n in base if n not in preferred]
        # bench (factory bench): 用途をベンチ variant に固定する。
        # フォールバックが働くと「どのワーカーで完了したか」が比較の中で変わってしまうため、
        # 対象用途は1候補 (該当 variant) に限定し、失敗はその variant の失敗として記録する。
        # dev 以外の比較 (purpose=spec の spec 品質比較など) は bench_lock_purpose で指定する。
        # judge はこの限りではない (全 variant 共通の器具として通常ルーティング)。
        lock_dev = purpose == "dev" and getattr(self.cfg, "bench_lock_dev", False)
        lock_any = purpose == getattr(self.cfg, "bench_lock_purpose", "")
        if lock_dev or lock_any:
            out = out[:1]
        return out

    # ------------------------------------------------------------------
    def run(self, purpose: str, cwd: Path, prompt: str, timeout: Optional[int] = None,
            resume: Optional[Tuple[str, str]] = None) -> Tuple[str, WorkerResult]:
        """resume: (worker名, session_id)。

        その worker の config.resume が有効で、候補の中にその worker が含まれるときだけ
        セッション継続する (フォールバックでワーカーが変わる場合は継続しない)。
        """
        cands = self.candidates(purpose)
        if not cands:
            raise AllWorkersFailed([WorkerResult(
                worker="(none)", ok=False,
                raw_tail="利用可能なワーカーがありません (用途=%s)" % purpose)])

        # 予算超過 (日次 or window) のワーカーは後ろへ (全員超過ならそのまま使う)
        def over(n: str) -> bool:
            wc = self.cfg.workers.get(n, WorkerConfig())
            return self.usage.budget_exceeded(n, wc) or self.usage.window_budget_exceeded(n, wc)

        under = [n for n in cands if not over(n)]
        order = under + [n for n in cands if n not in under]

        resume_worker = resume[0] if resume and getattr(
            self.cfg.workers.get(resume[0], WorkerConfig()), "resume", False) else None
        results = []
        for i, name in enumerate(order):
            w = self.worker_for(name)
            sid = resume[1] if (resume and name == resume_worker) else None
            res = w.run(prompt, cwd, timeout, resume=sid)
            self.usage.record(name, res.seconds, res.ok, purpose, res.tokens_in, res.tokens_out, res.cost_usd)
            if res.ok:
                return name, res
            results.append(res)
            # 残りの候補がなければ失敗で終了
            if i == len(order) - 1 or SHUTDOWN.is_set():
                break
            # quota/timeout/unknown いずれも次のワーカーへフォールバック
            print(f"[router] worker {name} failed ({res.fail_kind or 'unknown'}) -> 次のワーカーへ")
        raise AllWorkersFailed(results)
