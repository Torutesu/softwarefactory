"""OpenAI Codex CLI アダプタ (ChatGPT Plus/Pro/Team サブスク既定)。

例: codex exec --dangerously-bypass-approvals-and-sandbox --skip-git-repo-check "<prompt>"

- auth="subscription" (既定): OPENAI_API_KEY を環境から外し、ChatGPT ログインの枠を使う
- auth="api_key"            : 環境の OPENAI_API_KEY をそのまま使う (API 課金)
- config.args への追加 (--json 等) で挙動を調整できる
"""
from __future__ import annotations

import json
from typing import Optional

from ..util import ProcResult
from .base import Worker, WorkerResult


class CodexWorker(Worker):
    name = "codex"

    def build_args(self, prompt: str, resume: Optional[str] = None) -> list:
        cmd = self.cfg.command or "codex"
        args = [cmd, "exec"]
        if resume:
            # codex exec resume <session> — 非対応バージョンでは失敗 → Router がフォールバック
            args += ["resume", str(resume)]
        if self.cfg.model:
            args += ["--model", self.cfg.model]
        # 非対話で全面許可 (社内専用の前提; ワークスペース外も書ける)
        args.append("--dangerously-bypass-approvals-and-sandbox")
        # worktree (.git ファイル) でも止まらないように
        args.append("--skip-git-repo-check")
        args += list(self.cfg.args or [])
        args.append(prompt)
        return args

    def parse_output(self, pr: ProcResult) -> WorkerResult:
        res = WorkerResult(worker=self.name, ok=pr.code == 0)
        out = (pr.out or "").strip()
        text, session = self._parse_jsonl(out)
        res.text = text or out
        res.session_id = session
        if pr.code != 0:
            res.raw_tail = (pr.err or out)[-800:]
        return res

    @staticmethod
    def _parse_jsonl(out: str):
        """--json 指定時の JSONL イベントから (テキスト, session_id) を救い出す。

        通常出力 (プレーンテキスト) の場合は引数が取れないので (None, None) を返し、
        呼び出し側が生テキストを使う。
        """
        if not out or not out.lstrip().startswith("{"):
            return None, None
        texts = []
        session = None
        for line in out.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(ev, dict):
                continue
            session = ev.get("session_id") or session
            # さまざまな形状に寛容: {"msg": {"text": ...}}, {"text": ...}, {"message": "..."}
            msg = ev.get("msg")
            if isinstance(msg, dict) and isinstance(msg.get("text"), str):
                texts.append(msg["text"])
            elif isinstance(ev.get("text"), str):
                texts.append(ev["text"])
            elif isinstance(ev.get("message"), str):
                texts.append(ev["message"])
        return ("\n".join(t for t in texts if t) or None), (str(session) if session else None)
