"""マージゲート: 変更の blast radius 分類とポリシー、エージェント成果物の帰属。

    factory/<id> ブランチの差分を 4 ティアに分類し、ティアごとのポリシーで
    自動マージの可否を決める (Firecrawl: How to Build an AI Software Factory, Stage 5)。

    | ティア | 例 | ポリシー (既定) |
    |---|---|---|
    | mechanical | 依存バンプ / lint 修正 / テスト移行 / docs | 自動チェックでマージ可 |
    | scoped     | 50 行以下のバグ修正・新テスト | 同上 |
    | behavioral | 製品の振る舞いに触れる / 大きめの差分 / greenfield | 人間レビュー |
    | sensitive  | auth・決済・認証情報など領域センシティブなパス | 領域オーナーのレビュー |

帰属 (attribution): エージェント支援のコミット/PR には `Assisted-by:` を付ける。
`Signed-off-by` は法的保証なのでエージェントは付けない (Linux kernel の 2026 方針に準拠)。
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Optional

from . import gitx
from .config import Config
from .state import Mission

TIERS = ("mechanical", "scoped", "behavioral", "sensitive")

# これらだけで構成される差分は「機械的」扱い (末尾マッチ / ディレクトリ先頭マッチ)
_META_DIR_PREFIXES = ("tests/", "docs/", ".github/", "changelog", "locale/")
_META_SUFFIXES = (".md", ".rst", ".txt", ".cfg", ".toml", ".lock")
_META_NAMES = {"version", "changelog", "license", "notice", ".gitignore", ".gitattributes"}

# ティアごとのレビュー目安 (PR 本文と escalate メッセージで使う)
REVIEW_SUGGESTION = {
    "mechanical": "自動チェック (build/test/シナリオ) で可",
    "scoped": "人間レビュー 1 名",
    "behavioral": "人間レビュー 2 名を推奨 (製品の振る舞いに触れる差分)",
    "sensitive": "領域オーナーのレビュー必須 (auth/決済等)",
}


def _is_meta(path: str) -> bool:
    pl = path.lower().replace("\\", "/")
    name = pl.rsplit("/", 1)[-1]
    return (pl.startswith(_META_DIR_PREFIXES)
            or pl.endswith(_META_SUFFIXES)
            or name in _META_NAMES)


def classify(cfg: Config, repo, branch: str, m: Mission) -> dict:
    """factory/<id> ブランチの差分からティアを分類する。

    戻り値: {"tier", "files", "lines", "paths", "sensitive_matches", "review"}
    ブランチが無い (worktree 未作成 or 既に掃除済み) / git 操作に失敗した場合は
    例外を出さず tier="unknown" を返す (呼び出し側は unknown を review 扱いに = fail-safe)。
    """
    try:
        if not gitx.branch_exists(repo, branch):
            raise LookupError("branch not found")
        ch = gitx.branch_changes(repo, branch)
    except Exception:  # noqa — 分類できないときは人間に任せる
        return {"tier": "unknown", "files": 0, "lines": 0, "paths": [],
                "sensitive_matches": [], "review": REVIEW_SUGGESTION["sensitive"]}
    paths = [p for p in ch["paths"] if p]
    patterns = [p for p in (getattr(cfg, "sensitive_paths", None) or []) if p]
    sensitive = sorted({p for p in paths
                        if any(re.search(pat, p, re.IGNORECASE) for pat in patterns)})
    small = max(0, int(getattr(cfg, "small_change_lines", 50) or 0))
    if sensitive:
        tier = "sensitive"
    elif m.kind == "greenfield" or ch["lines"] > small:
        tier = "behavioral"
    elif paths and all(_is_meta(p) for p in paths):
        tier = "mechanical"
    else:
        tier = "scoped"
    return {
        "tier": tier,
        "files": ch["files"],
        "lines": ch["lines"],
        "paths": paths,
        "sensitive_matches": sensitive,
        "review": REVIEW_SUGGESTION.get(tier, REVIEW_SUGGESTION["scoped"]),
    }


def describe(cls: dict) -> str:
    """分類結果の 1 行サマリ (escalate / merge 表示用)。"""
    bits = [f"{cls.get('files', 0)}ファイル/{cls.get('lines', 0)}行"]
    sm = cls.get("sensitive_matches") or []
    if sm:
        bits.append("sensitive: " + ", ".join(sm[:3]) + ("..." if len(sm) > 3 else ""))
    return " ".join(bits)


def policy_for(cfg: Config, tier: str) -> str:
    """ティアのマージポリシー ('auto' | 'review')。unknown は必ず review。"""
    if tier not in TIERS:
        return "review"
    return str((cfg.merge_policy or {}).get(tier, "review")).lower()


def workers_of(m: Mission) -> str:
    """実行履歴からワーカー 使用状況を 'claude_code×2, mock×1' 形式で集計する。"""
    counts = Counter(it.worker or "unknown" for it in m.iterations)
    return ", ".join(f"{w}×{n}" for w, n in counts.most_common()) or "unknown"


def attribution(m: Mission) -> str:
    """マージコミット / PR に付ける Assisted-by トレーラの中身。"""
    return f"SoftwareFactory {m.id} (workers: {workers_of(m)})"


def merge_message(m: Mission) -> str:
    """squash マージのコミットメッセージ (帰属トレーラ付き)。"""
    return (f"factory: {m.id} {m.title}\n\n"
            f"Workers: {workers_of(m)}\n"
            f"Assisted-by: {attribution(m)}\n"
            f"(Signed-off-by は付与していません: マージ判断の責任はレビューする人間にあります)")


def pr_blurb(cls: Optional[dict], m: Mission) -> str:
    """PR 本文に載せる ティア + 帰属 の節。分類できなければ帰属だけ返す。"""
    lines = ["## マージゲート", ""]
    if cls and cls.get("tier"):
        lines += [
            f"- ティア: **{cls['tier']}** ({describe(cls)})",
            f"- 推奨レビュー: {cls.get('review') or REVIEW_SUGGESTION['scoped']}",
            "",
        ]
    lines += [
        "## 帰属 (attribution)",
        "",
        f"- `Assisted-by: {attribution(m)}`",
        "- この変更は AI ワーカーが作成しました。`Signed-off-by` は付与していません "
        "(マージ判断の責任はレビューする人間にあります)。",
    ]
    return "\n".join(lines)
