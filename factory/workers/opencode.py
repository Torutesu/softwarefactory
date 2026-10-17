"""OpenCode アダプタ (無料・代替)。

例: opencode run "<prompt>"

JSON 出力 (--format json 等を config.args に追加した場合) はテキスト/usage/
セッションIDをベストエフォートで抽出する。
"""
from __future__ import annotations

import json
from typing import Optional

from ..util import ProcResult
from .base import Worker, WorkerResult


class OpenCodeWorker(Worker):
    name = "opencode"

    def build_args(self, prompt: str, resume: Optional[str] = None) -> list:
        cmd = self.cfg.command or "opencode"
        args = [cmd, "run"]
        if resume:
            args += ["--session", str(resume)]
        if self.cfg.model:
            args += ["--model", self.cfg.model]
        args += list(self.cfg.args or [])
        args.append(prompt)
        return args

    def parse_output(self, pr: ProcResult) -> WorkerResult:
        res = WorkerResult(worker=self.name, ok=pr.code == 0)
        out = (pr.out or "").strip()
        parsed = self._maybe_json(out)
        if parsed is not None:
            text, session = self._extract(parsed)
            res.text = text or out
            res.session_id = session
        else:
            res.text = out
        if pr.code != 0:
            res.raw_tail = (pr.err or out)[-800:]
        return res

    # ------------------------------------------------------------------
    @staticmethod
    def _maybe_json(out: str):
        if not out or out[0] not in "{[":
            return None
        try:
            return json.JSONDecoder().raw_decode(out)[0]
        except json.JSONDecodeError:
            return None

    @staticmethod
    def _extract(data) -> tuple:
        """JSON 形出力から (text, session_id) を救い出す (形状に寛容に)。"""
        session_keys = ("sessionID", "session_id", "sessionId", "id")
        text_keys = ("content", "text", "message", "result", "output")

        def visit(node, depth=0):
            if depth > 4:
                return None, None
            if isinstance(node, str):
                return None, None
            if isinstance(node, list):
                texts = []
                for item in node:
                    t, s = visit(item, depth + 1)
                    if s:
                        return "\n".join(texts + [t]) if t else "\n".join(texts), s
                    texts.append(t or "")
                if any(texts):
                    return "\n".join(t for t in texts if t), None
                return None, None
            if isinstance(node, dict):
                session = next((node[k] for k in session_keys if node.get(k)), None)
                text = next((node[k] for k in text_keys if isinstance(node.get(k), str)), None)
                if session and text:
                    return text, str(session)
                for v in node.values():
                    t, s = visit(v, depth + 1)
                    if s or (t and not text):
                        return t or text, s or None
                return text, session if isinstance(session, str) else None
            return None, None

        return visit(data)
