"""macOS `sandbox-exec` による隔離 (config `sandbox`: off | harness | all)。

同一ユーザーで動く限り、chmod の封印は「戻せる」し holdout を「読む」ことも止められない。
macOS ではカーネルのサンドボックス (Seatbelt) で、実装ワーカーと harness が読めない場所を作る:

- harness (build/test/run とシナリオ): holdout 全体・.factory・~/.ssh 等の認証情報・Keychain を
  読み書き不可。シナリオは 0700 の一時ディレクトリのスナップショットから動くので影響しない
- all: 加えて dev ワーカー (claude/codex/opencode) にも同じ制約 (Keychain は CLI のログインに要るので除く)。
  spec / holdout 生成ワーカーは holdout に書くので対象外

既定は許可 (allow default) で、拒否リストだけを足す。CLI のキャッシュ・ログイン・ネットワークは壊さない。
macOS 以外 (sandbox-exec が無い) では隔離せずに実行し、doctor が警告する。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import List

MODES = ("off", "harness", "all")
SANDBOX_EXEC = "/usr/bin/sandbox-exec"
_HOME_SECRETS = (".ssh", ".aws", ".gnupg", ".config/gh", ".docker", ".kube", ".netrc", ".git-credentials")


def available() -> bool:
    return sys.platform == "darwin" and os.access(SANDBOX_EXEC, os.X_OK)


def mode_of(cfg) -> str:
    m = str(getattr(cfg, "sandbox", "off") or "off").lower()
    return m if m in MODES else "off"


def _q(path: str) -> str:
    return '"' + path.replace("\\", "\\\\").replace('"', '\\"') + '"'


def deny_paths(cfg, scope: str) -> List[str]:
    root = Path(getattr(cfg, "root", ".") or ".")
    home = Path.home()
    paths = [root / "holdout", root / ".factory"]
    paths += [home / p for p in _HOME_SECRETS]
    if scope == "harness":
        paths.append(home / "Library" / "Keychains")
    out = []
    for p in paths:
        rp = os.path.realpath(str(p))  # macOS は /var → /private/var 等。実体で書かないと効かない
        if rp not in out:
            out.append(rp)
    return out


def profile(cfg, scope: str) -> str:
    rules = "\n".join(f"(deny file-read* file-write* (subpath {_q(p)}))" for p in deny_paths(cfg, scope))
    return f"(version 1)\n(allow default)\n{rules}\n"


def prefix(cfg, scope: str) -> List[str]:
    """コマンドの前に付ける引数 (隔離しないなら [])。scope: "harness" | "worker"。"""
    mode = mode_of(cfg)
    if mode == "off" or (scope == "worker" and mode != "all"):
        return []
    if not available():
        return []
    return [SANDBOX_EXEC, "-p", profile(cfg, scope)]


def status(cfg) -> str:
    mode = mode_of(cfg)
    if mode == "off":
        return "off"
    return f"{mode} ({'有効' if available() else 'sandbox-exec が無いため無効 (macOS のみ)'})"
