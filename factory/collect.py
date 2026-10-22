"""定時シグナル収集 (Collect)。

cron / supervisor から `factory collect` を回す前提の「巡回取込」:

- config.collect.sources に列挙したソース (url / file / dir / command) を順に見て、
  変化した分だけ .factory/drafts/<id>/ に**ドラフト**を作る (Seed はまだ作らない)。
- 本文は sha256 でハッシュ化し、既に見たハッシュは採らない (重複取り込みなし)。
- every_minutes を設定したソースは前回実行から指定分経過するまでスキップ —
  cron を数分間隔で回しても各ソースは自分の間隔でのみ動く。
- 人間がドラフトの seed.md を確定したら `factory collect --promote <id>` で Seed 化
  (--project 付ければミッション作成、--run で実行まで)。promote は必ず人間の操作。

URL / command / SNS / エラートラッカー / 社内エクスポートの出力など、
「外にあるシグナル」を問わず text に入るものなら同じ枠で集める。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import secrets
import urllib.request
from pathlib import Path
from typing import List, Optional, Tuple

from .ingest import _extract_text, UA
from .util import ensure_dir, load_json, locked, now_utc_iso, run_shell, save_json

DRAFTS_MAX_CHARS = 200_000  # 1 件のドラフトに保存する本文上限
SEEN_MAX_PER_SOURCE = 500  # 重複判定の既視ハッシュ保持上限 (source ごと)


# ------------------------------------------------------------------------------
# config
def sources_of(cfg) -> List[dict]:
    """cfg.collect["sources"] を検証して飾りの無い list[dict] で返す。"""
    raw = (getattr(cfg, "collect", {}) or {}).get("sources") or []
    if isinstance(raw, dict):  # 1 ソースだけを dict で書く書式も許す
        raw = [raw]
    out: List[dict] = []
    for i, s in enumerate(raw):
        if not isinstance(s, dict):
            raise ValueError(f"collect.sources[{i}] は object で書いてください")
        stype = s.get("type", "").strip()
        name = (s.get("name") or "").strip()
        if not name:
            raise ValueError(f"collect.sources[{i}] に name がありません")
        if stype not in ("url", "file", "dir", "command"):
            raise ValueError(f"collect.sources[{i}] ({name}): type は url/file/dir/command のどれか")
        if stype == "url" and not (s.get("url") or "").strip():
            raise ValueError(f"collect.sources[{i}] ({name}): url を指定してください")
        if stype == "file" and not (s.get("path") or "").strip():
            raise ValueError(f"collect.sources[{i}] ({name}): path を指定してください")
        if stype == "dir" and not (s.get("path") or "").strip():
            raise ValueError(f"collect.sources[{i}] ({name}): path を指定してください")
        if stype == "command" and not (s.get("cmd") or "").strip():
            raise ValueError(f"collect.sources[{i}] ({name}): cmd を指定してください")
        out.append(s)
    return out


# ------------------------------------------------------------------------------
# drafts dir / state
def drafts_dir(factory) -> Path:
    return Path(factory.root) / ".factory" / "drafts"


def state_path(factory) -> Path:
    return drafts_dir(factory) / "state.json"


def load_state(factory) -> dict:
    st = load_json(state_path(factory), {}) or {}
    st.setdefault("sources", {})
    return st


def save_state(factory, st: dict) -> None:
    ensure_dir(state_path(factory).parent)
    with locked(state_path(factory)):
        save_json(state_path(factory), st)


def new_draft_id() -> str:
    return "D" + dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)


# ------------------------------------------------------------------------------
# 取得
def _http_get(url: str, timeout: int = 30) -> Tuple[str, bytes]:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.headers.get("Content-Type", ""), resp.read()


def _read_text(raw: bytes) -> str:
    return raw.decode("utf-8", errors="replace")


def content_hash(text: str) -> str:
    norm = "\n".join(ln.strip() for ln in text.splitlines() if ln.strip())
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def _extract_title_from_html(raw: bytes, fallback: str) -> str:
    import re

    html = _read_text(raw)
    m = re.search(r"(?is)<title[^>]*>(.*?)</title>", html)
    return m.group(1).strip() if m else fallback


def fetch_source(factory, src: dict) -> List[dict]:
    """ソースを一度読み、list[{title, text, origin, extra(tb は None か bytes)}] を返す。

    失敗は CollectError にする (呼び出し側がソース単位の失敗として記録し継続)。
    """
    stype = src["type"]
    max_chars = int(src.get("max_chars") or DRAFTS_MAX_CHARS)
    if stype == "url":
        url = (src.get("url") or "").strip()
        ctype, raw = _http_get(url)
        if "html" in ctype or not ctype:
            text = _extract_text(_read_text(raw))[:max_chars]
            title = (src.get("title") or _extract_title_from_html(raw, url))[:120]
            return [{"title": title, "text": text, "origin": url, "extra": raw, "extra_name": "page.html"}]
        return [{"title": url[:120], "text": f"(非HTML: {ctype} のため素材同梱のみ)", "origin": url,
                 "extra": raw, "extra_name": "content.bin"}]
    if stype == "file":
        p = Path(src["path"])
        if not p.is_file():
            raise CollectError(f"{p} が存在しません")
        raw = p.read_bytes()
        text = _read_text(raw)[:max_chars]
        return [{"title": (src.get("title") or p.name)[:120], "text": text, "origin": str(p),
                 "extra": None, "extra_name": ""}]
    if stype == "dir":
        d = Path(src["path"])
        if not d.is_dir():
            raise CollectError(f"{d} が存在しません")
        items = []
        for p in sorted(d.iterdir()):
            if p.name.startswith(".") or not p.is_file():
                continue
            if p.stat().st_size > 20 * 1024 * 1024:  # 20MB 上限 (巡回対象の異常膨張対策)
                continue
            items.append({"title": p.name[:120], "text": _read_text(p.read_bytes())[:max_chars],
                          "origin": str(p), "extra": p.read_bytes(), "extra_name": p.name})
        return items
    if stype == "command":
        r = run_shell((src.get("cmd") or "").strip(), cwd=Path(factory.root), timeout=int(src.get("timeout") or 120))
        if r.code != 0:
            raise CollectError(f"command が code={r.code} で失敗: {(r.err or r.out).strip()[:200]}")
        if not (r.out or "").strip():
            return []  # 出力ゼロは「今回はシグナルなし」として正常扱い
        label = (src.get("title") or "command output")[:120]
        return [{"title": label, "text": r.out[:max_chars], "origin": (src.get("cmd") or "")[:200],
                 "extra": None, "extra_name": ""}]
    raise CollectError(f"未知の type: {stype}")


class CollectError(Exception):
    pass


def is_due(src: dict, sst: dict, now: Optional[dt.datetime] = None) -> Tuple[bool, str]:
    """every_minutes スロットリングの判定。"""
    every = int(src.get("every_minutes") or 0)
    if every <= 0:
        return True, ""
    now = now or dt.datetime.now(dt.timezone.utc)
    last = sst.get("last_ts") or ""
    if not last:
        return True, ""
    try:
        last_ts = dt.datetime.fromisoformat(last.replace("Z", "+00:00"))  # py3.9 は Z 接尾を解釈しない
    except ValueError:
        return True, ""
    if last_ts.tzinfo is None:
        last_ts = last_ts.replace(tzinfo=dt.timezone.utc)
    elapsed = (now - last_ts).total_seconds() / 60.0
    if elapsed < every:
        return False, f"not due (every {every}min, last {last_ts:%H:%MZ})"
    return True, ""


# ------------------------------------------------------------------------------
# run
def run_collect(factory, cfg, source: str = "", dry_run: bool = False) -> dict:
    """全ソースを一周してドラフト化。結果 rows を返す (CLI が描画)。"""
    srcs = sources_of(cfg)
    if source:
        srcs = [s for s in srcs if s.get("name") == source]
        if not srcs:
            raise CollectError(f"ソースが見つかりません: {source}")
    st = load_state(factory)
    rows = []
    created = 0
    for src in srcs:
        name = src["name"]
        sst = st["sources"].setdefault(name, {"last_ts": "", "seen": {}})
        sst.setdefault("seen", {})
        due, due_note = is_due(src, sst)
        if not due:
            rows.append({"source": name, "type": src["type"], "status": "skipped",
                         "new": 0, "dup": 0, "note": due_note})
            continue
        new = dup = 0
        note = ""
        try:
            items = fetch_source(factory, src)
        except Exception as e:  # ソース単位の失敗は 1 行にして続きへ
            rows.append({"source": name, "type": src["type"], "status": "error",
                         "new": 0, "dup": 0, "note": str(e)[:200]})
            sst["last_ts"] = now_utc_iso()
            continue
        if not items:
            rows.append({"source": name, "type": src["type"], "status": "empty",
                         "new": 0, "dup": 0, "note": "出力なし"})
            sst["last_ts"] = now_utc_iso()
            continue
        for it in items:
            h = content_hash(it["text"])
            if h in sst["seen"]:
                dup += 1
                continue
            if dry_run:
                new += 1
                continue
            d = write_draft(factory, name, src["type"], it)
            if len(sst["seen"]) >= SEEN_MAX_PER_SOURCE:
                oldest = sorted(sst["seen"].items(), key=lambda kv: kv[1].get("ts", ""))
                for k, _ in oldest[: len(oldest) - SEEN_MAX_PER_SOURCE + 1]:
                    del sst["seen"][k]
            sst["seen"][h] = {"draft": d[0], "ts": now_utc_iso()}
            new += 1
        sst["last_ts"] = now_utc_iso()
        if new:
            created += new
        rows.append({"source": name, "type": src["type"], "status": "ok",
                     "new": new, "dup": dup, "note": note})
    if not dry_run:
        save_state(factory, st)
    try:
        if created and not dry_run:
            factory.event("collect", f"{created} draft(s) new")
    except Exception:
        pass
    return {"ts": now_utc_iso(), "dry_run": dry_run, "created": created, "rows": rows}


def write_draft(factory, source_name: str, stype: str, item: dict) -> Tuple[str, Path]:
    """1 件のドラフトを作る (signal.md + meta.json [+ 元データ])。"""
    did = new_draft_id()
    d = drafts_dir(factory) / did
    ensure_dir(d)
    body = (
        f"# Signal: {item['title']}\n\n"
        f"- source: {source_name} ({stype})\n"
        f"- origin: {item['origin']}\n"
        f"- collected: {now_utc_iso()}\n"
        f"- draft: {did}\n\n"
        f"(人間がここに目的と受け入れ基準を書き足してから "
        f"`factory collect --promote {did}` で Seed 化する)\n\n"
        f"## 収集本文\n\n{item['text']}\n"
    )
    (d / "signal.md").write_text(body, encoding="utf-8")
    (d / "meta.json").write_text(json.dumps({
        "id": did, "source": source_name, "type": stype, "title": item["title"],
        "origin": item["origin"], "collected_utc": now_utc_iso(),
        "hash": content_hash(item["text"]), "status": "new",
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    if item.get("extra") is not None:
        # 元データの名前で signal.md / meta.json を上書きさせない、ドラフト外へも書かせない
        name = Path(str(item.get("extra_name") or "original")).name
        if name in ("signal.md", "meta.json", "", ".", ".."):
            name = "original-" + (name.strip(".") or "data")
        blob = d / name
        if isinstance(item["extra"], bytes):
            blob.write_bytes(item["extra"])
        else:
            blob.write_text(str(item["extra"]), encoding="utf-8")
    return did, d


# ------------------------------------------------------------------------------
# promote / dismiss / list
def _meta(d: Path) -> dict:
    return load_json(d / "meta.json", {}) or {}


def promote(factory, draft_id: str, seed_name: str = "", title: str = "") -> Path:
    """ドラフトを Seed に昇格 (seed.md は人間が編集するための骨組みを作る)。"""
    d = drafts_dir(factory) / draft_id
    if not d.is_dir():
        raise CollectError(f"ドラフトがありません: {draft_id}")
    meta = _meta(d)
    if meta.get("status", "new") != "new":
        raise CollectError(f"このドラフトは既に {meta.get('status')} 済み: {draft_id}")
    signal = (d / "signal.md").read_text(encoding="utf-8")
    # 収集本文だけ抜き出す (ヘッダを落として Seed 側に渡す)
    marker = "## 収集本文"
    material = signal.split(marker, 1)[1].strip() if marker in signal else signal
    slug_seed = seed_name or meta.get("title") or draft_id
    from .ingest import _unique_name, write_seed_dir

    slug = _unique_name(factory, slug_seed)
    body = (
        f"# Seed: {title or meta.get('title') or slug}\n\n"
        f"- 取込元: {meta.get('origin')}\n"
        f"- ドラフト: {draft_id} (collected {meta.get('collected_utc')})\n\n"
        f"## 目的 (ドラフトから仮置き — ここを確定させるのが人間の仕事)\n\n"
        f"{material[:1200]}\n\n"
        f"## 受け入れ基準 (人間が書く)\n\n- \n"
        f"## 材料\n\n詳細は同梱の `signal.md` を参照。\n"
    )
    extra = {"signal.md": signal}
    blob = None
    for cand in sorted(d.iterdir()):
        if cand.name in ("signal.md", "meta.json"):
            continue
        blob = cand
        break
    if blob is not None and blob.is_file():
        extra[blob.name] = blob.read_bytes()
    sd = write_seed_dir(factory, slug, title or meta.get("title") or slug, body, extra)
    meta["status"] = "promoted"
    meta["seed"] = sd.name
    (d / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        factory.event("draft_promoted", f"{draft_id} -> seeds/{sd.name}")
    except Exception:
        pass
    return sd


def dismiss(factory, draft_id: str) -> None:
    d = drafts_dir(factory) / draft_id
    if not d.is_dir():
        raise CollectError(f"ドラフトがありません: {draft_id}")
    meta = _meta(d)
    if meta.get("status", "new") != "new":
        raise CollectError(f"このドラフトは既に {meta.get('status')} 済み: {draft_id}")
    meta["status"] = "dismissed"
    (d / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def list_drafts(factory) -> List[dict]:
    ddir = drafts_dir(factory)
    rows = []
    if not ddir.is_dir():
        return rows
    for d in sorted(ddir.iterdir()):
        if not d.is_dir() or d.name.startswith("state"):
            continue
        meta = _meta(d)
        if not meta:
            continue
        rows.append({
            "id": meta.get("id", d.name), "status": meta.get("status", "new"),
            "source": meta.get("source", ""), "title": meta.get("title", "")[:60],
            "collected": meta.get("collected_utc", ""),
            "seed": meta.get("seed", ""),
        })
    rows.sort(key=lambda r: r["collected"], reverse=True)
    return rows
