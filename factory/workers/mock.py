"""Mock Worker: Claude/OpenCode CLI が無くてもパイプラインを通すための決定的ワーカー。

- prompt 中の ```mock-action ブロック (JSON) を解析し、文字列パッチを適用する。
- 実装の契約 (prompt 入力 → コード変更出力) は本物のワーカーと同一なので、
  パイプライン全体 (spec → holdout → dev → harness → judge) の結合テストに使える。
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Optional

from ..util import ProcResult, run_shell
from .base import Worker, WorkerResult

MOCK_BLOCK = re.compile(r"```mock-action\s*\n(.*?)\n```", re.S)


class MockWorker(Worker):
    name = "mock"

    def build_args(self, prompt: str, resume: Optional[str] = None) -> list:
        cmd = self.cfg.command or "true"
        return [cmd]

    def parse_output(self, pr: ProcResult) -> WorkerResult:  # pragma: no cover - 未使用
        return WorkerResult(worker=self.name, ok=True, text="")

    # ------------------------------------------------------------------
    def run(self, prompt: str, cwd: Path, timeout=None, resume=None) -> WorkerResult:
        t0 = time.time()
        actions = [json.loads(m.group(1)) for m in MOCK_BLOCK.finditer(prompt)]
        logs = []

        # mock 用の答えキー (seed 同梱) が mission dir に置いてあれば使う
        actions_file = Path(cwd).parent / "mock_actions.json"
        if actions_file.is_file():
            try:
                data = json.loads(actions_file.read_text(encoding="utf-8"))
                actions.extend(data.get("actions", []))
            except json.JSONDecodeError:
                logs.append("[mock] mock_actions.json is invalid")
        for act in actions:
            kind = act.get("action", "patch")
            if kind == "patch":
                path = Path(cwd) / act["file"]
                if not path.exists():
                    logs.append(f"[mock] missing file: {act['file']}")
                    continue
                text = path.read_text(encoding="utf-8")
                find, replace = act["find"], act.get("replace", "")
                if find not in text:
                    logs.append(f"[mock] pattern not found in {act['file']}: {find!r}")
                    continue
                path.write_text(text.replace(find, replace, int(act.get("count", 1))), encoding="utf-8")
                logs.append(f"[mock] patched {act['file']}: {find[:40]!r} -> {replace[:40]!r}")
            elif kind == "write":
                path = Path(cwd) / act["file"]
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(act["content"], encoding="utf-8")
                logs.append(f"[mock] wrote {act['file']}")
            elif kind == "copy":
                src = Path(act["src"])
                dst = Path(cwd) / act["dst"]
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
                logs.append(f"[mock] copied {src.name} -> {act['dst']}")
            elif kind == "shell":
                r = run_shell(act["cmd"], cwd=cwd, timeout=act.get("timeout", 120))
                logs.append(f"[mock] shell({act['cmd'][:60]}...) -> {r.code}")
            else:
                logs.append(f"[mock] unknown action: {kind}")
        text = "\n".join(logs) or "[mock] no actions found in prompt"

        # 実装 Worker と同じ契約: 変更をコミットする
        pr = run_shell("git add -A && git commit -m 'feat: factory mock implementation' || true", cwd=cwd, timeout=60)
        text += f"\n[mock] commit -> code {pr.code}"

        return WorkerResult(
            worker=self.name,
            ok=True,
            text=text,
            seconds=time.time() - t0,
        )
