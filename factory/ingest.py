"""Signal/Seed の取り込み (Ingest)。

- ローカル素材: ディレクトリ/ファイルを seeds/<name>/ にコピー
- URL: urllib で取得し、HTML → テキストを抽出して seed.md に保存
- Webhook (listener.py): 外部システムからの POST を Seed 化
"""
from __future__ import annotations

import datetime as dt
import re
import shutil
import urllib.request
from pathlib import Path
from typing import Optional

from .util import slugify

UA = "SoftwareFactory/0.1 (internal)"


def _extract_text(html: str) -> str:
    """HTML から素朴に可読テキストを抽出する。"""
    html = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html)
    html = re.sub(r"(?s)<!--.*?-->", " ", html)
    text = re.sub(r"(?i)<br\s*/?>", "\n", html)
    text = re.sub(r"(?i)</(p|div|li|h[1-6]|tr)>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"&quot;", '"', text)
    lines = [ln.strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def _unique_name(factory, base: str) -> str:
    name = slugify(base)
    candidate = name
    i = 2
    while (factory.seeds_dir / candidate).exists():
        candidate = f"{name}-{i}"
        i += 1
    return candidate


def write_seed_dir(factory, name: str, title: str, body: str, extra_files: Optional[dict] = None) -> Path:
    d = factory.seeds_dir / name
    # Webhook 由来の名前で Seed 外 (../ や絶対パス) に書かせない。書き込み前に全件検証し、
    # 不正があれば Seed ディレクトリ自体を作らない (中途半端な Seed を intake に拾わせない)
    root = d.resolve()
    targets = []
    for rel, content in (extra_files or {}).items():
        p = (d / rel).resolve()
        if p == root or root not in p.parents:
            raise ValueError(f"seed 外へのパスは書き込めません: {rel!r}")
        targets.append((p, content))
    d.mkdir(parents=True, exist_ok=True)
    seed_md = d / "seed.md"
    if not seed_md.exists():
        seed_md.write_text(body, encoding="utf-8")
    for p, content in targets:
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            p.write_bytes(content)
        else:
            p.write_text(content, encoding="utf-8")
    return d


def ingest_dir(factory, src: Path, name: str = "") -> Path:
    """ローカルの素材 (ディレクトリ/ファイル) を seeds/<name>/ に取り込む。"""
    src = Path(src).resolve()
    if not src.exists():
        raise SystemExit(f"エラー: {src} が存在しません")
    name = _unique_name(factory, name or src.stem)
    dst = factory.seeds_dir / name
    if src.is_dir():
        shutil.copytree(src, dst, ignore=shutil.ignore_patterns(".git", ".DS_Store"))
    else:
        dst.mkdir(parents=True)
        shutil.copy2(src, dst / src.name)
    readme = dst / "seed.md"
    if not readme.exists():
        readme.write_text(f"# Seed: {name}\n\n(ここに目的・参照材料・受け入れ基準を書いてください)\n", encoding="utf-8")
    return dst


def ingest_url(factory, url: str, name: str = "", title: str = "", max_chars: int = 200_000) -> Path:
    """URL を取得して Seed 化する (SNS・記事・バグ報告の URL 等を汎用に取り込む)。"""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as resp:
        content_type = resp.headers.get("Content-Type", "")
        raw = resp.read()
    name = _unique_name(factory, name or title or url)
    if "html" in content_type or not content_type:
        html = raw.decode("utf-8", errors="replace")
        text = _extract_text(html)[:max_chars]
        title = title or re.search(r"(?is)<title[^>]*>(.*?)</title>", html)
        title = (title.group(1).strip() if title else name)
        body = (
            f"# Seed: {title}\n\n"
            f"- 取込元: {url}\n"
            f"- 取込日時: {dt.datetime.now(dt.timezone.utc).isoformat()}\n\n"
            f"## 取得内容 (テキスト抽出)\n\n{text}\n"
        )
        extra = {"page.html": raw}
    else:
        body = (
            f"# Seed: {title or name}\n\n"
            f"- 取込元: {url}\n"
            f"- Content-Type: {content_type}\n"
            f"- 取込日時: {dt.datetime.now(dt.timezone.utc).isoformat()}\n\n"
            f"(バイナリ/非HTML のため本文なし。素材は同梱)\n"
        )
        extra = {"content.bin": raw}
    return write_seed_dir(factory, name, title or name, body, extra)


def create_from_payload(factory, title: str, body: str, source: str = "webhook", files: Optional[dict] = None) -> Path:
    """Webhook ポスト {title, body} を Seed 化する。"""
    title = (title or "untitled signal").strip()
    name = _unique_name(factory, title)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%MZ")
    md = (
        f"# {title}\n\n"
        f"- source: {source}\n"
        f"- received: {stamp}\n\n"
        f"{body.strip() or '(本文なし — 目的と受け入れ基準を追記してください)'}\n"
    )
    data = {"seed.md": md}
    data.update(files or {})
    d = write_seed_dir(factory, name, title, md, data)
    try:
        factory.add_signal(name, source)
    except Exception:  # state 記録はベストエフォート
        pass
    return d
