"""OpenRouter アダプタ (API キー制 / 純粋な LLM 呼び出し)。

Chat Completions API へ直接 POST する (urllib, 標準ライブラリのみ)。
ツール実行ができないため、Router は spec/holdout/judge (テキスト生成用途) にのみ割当てる。
モデル例: anthropic/claude-sonnet-4.5, openai/gpt-5.2, google/gemini-2.5-pro

必要な環境変数: OPENROUTER_API_KEY
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

from .base import Worker, WorkerResult, classify_failure

API_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "openrouter/auto"


class OpenRouterWorker(Worker):
    name = "openrouter"

    def build_args(self, prompt: str, resume: Optional[str] = None) -> list:  # pragma: no cover - CLI 非対応
        raise NotImplementedError("openrouter は HTTP 直呼び (run をオーバーライド)")

    def run(self, prompt: str, cwd: Path, timeout: Optional[int] = None, resume: Optional[str] = None) -> WorkerResult:
        import os

        timeout = timeout or self.cfg.timeout
        key = os.environ.get("OPENROUTER_API_KEY", "").strip()
        if not key:
            return WorkerResult(
                worker=self.name, ok=False,
                raw_tail="OPENROUTER_API_KEY が未設定です (export してください)",
                fail_kind="unknown",
            )
        payload = {
            "model": self.cfg.model or DEFAULT_MODEL,
            "messages": [{"role": "user", "content": prompt}],
        }
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://internal.factory.local",
            "X-Title": "SoftwareFactory (internal)",
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(API_URL, data=data, headers=headers, method="POST")
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", errors="replace")
            except Exception:  # noqa
                pass
            return WorkerResult(
                worker=self.name, ok=False, seconds=time.time() - t0,
                raw_tail=f"HTTP {e.code}: {body[-400:]}",
                fail_kind=classify_failure("", body),
            )
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            return WorkerResult(
                worker=self.name, ok=False, seconds=time.time() - t0,
                raw_tail=str(e), fail_kind="timeout" if isinstance(e, TimeoutError) else "unknown",
            )
        except json.JSONDecodeError as e:
            return WorkerResult(worker=self.name, ok=False, seconds=time.time() - t0, raw_tail=str(e))

        res = WorkerResult(worker=self.name, ok=True, seconds=time.time() - t0)
        try:
            res.text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            res.text = json.dumps(data, ensure_ascii=False)[:4000]
            res.ok = False
            res.raw_tail = "choices/message の形状が想定外"
            return res
        usage = data.get("usage") or {}
        res.tokens_in = usage.get("prompt_tokens")
        res.tokens_out = usage.get("completion_tokens")
        res.cost_usd = usage.get("cost") if isinstance(usage.get("cost"), (int, float)) else None
        res.session_id = str(data.get("id")) if data.get("id") else None
        return res
