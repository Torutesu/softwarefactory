"""無人運転の後片付け: ログのローテーション・保持期間 (gc)・バックアップ/リストア。

    factory gc [--dry-run]                  # 保持期間を過ぎた生成物を消す
    factory backup [--to DIR] [--keep N]    # 状態一式を tar.gz に (holdout の権限ごと)
    factory restore ARCHIVE [--force]       # バックアップから戻す

tick は 1 日 1 回 gc とバックアップを行う (config の retention / backup_keep)。
消すのは「再生成できる / 調査が終わった」ものだけ:
  - missions/<id>/reports/run-* (新しい順に reports_keep_runs 件残す。final.md は残す)
  - missions/<id>/prompts/ (prompts_days より古い・実行中でないミッション)
  - holdout/<id>/runs/run-* (reports_keep_runs 件残す。シナリオ本体は消さない)
  - collect の処理済みドラフト (drafts_days)
  - bench の結果 (bench_days)
  - .factory/*.log と launchd のログ (log_max_mb を超えたらローテーション)
mission.json / spec.md / build.json / final.md / holdout のシナリオ / config / state は消さない。
"""
from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import stat
import tarfile
from pathlib import Path
from typing import List, Optional

from .util import ensure_dir, load_json

DEFAULT_RETENTION = {
    "reports_keep_runs": 5,
    "prompts_days": 30,
    "drafts_days": 30,
    "bench_days": 90,
    "log_max_mb": 5,
    "log_keep": 3,
}
ACTIVE = ("queued", "spec", "holdout", "dev")


def retention_of(cfg) -> dict:
    r = dict(DEFAULT_RETENTION)
    r.update({k: v for k, v in (getattr(cfg, "retention", None) or {}).items() if k in DEFAULT_RETENTION})
    return r


# ---------------------------------------------------------------------------
# logs
# ---------------------------------------------------------------------------
def rotate_log(path: Path, max_bytes: int, keep: int, copytruncate: bool = False) -> bool:
    """path が max_bytes を超えていたら path.1 … path.<keep> に回す。

    copytruncate=True は「他プロセス (launchd) が開きっぱなしのファイル」用: 複製してから
    元を 0 バイトに切り詰める (O_APPEND で書かれているので書き込み位置は先頭に戻る)。
    """
    path = Path(path)
    try:
        if not path.is_file() or path.stat().st_size <= max_bytes:
            return False
    except OSError:
        return False
    keep = max(1, int(keep))
    for i in range(keep - 1, 0, -1):
        src = path.with_name(f"{path.name}.{i}")
        if src.exists():
            os.replace(src, path.with_name(f"{path.name}.{i + 1}"))
    first = path.with_name(f"{path.name}.1")
    if copytruncate:
        shutil.copy2(path, first)
        with open(path, "r+b") as fh:
            fh.truncate(0)
    else:
        os.replace(path, first)
    return True


def append_log(path: Path, line: str, max_bytes: int = 5 * 1024 * 1024, keep: int = 3) -> None:
    """1 行追記 (超えていれば先にローテーション)。ログ失敗で本処理は止めない。"""
    try:
        rotate_log(path, max_bytes, keep)
        ensure_dir(Path(path).parent)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line.rstrip("\n") + "\n")
    except OSError:
        pass


def launchd_log_dir() -> Path:
    return Path.home() / "Library" / "Logs" / "softwarefactory"


# ---------------------------------------------------------------------------
# gc
# ---------------------------------------------------------------------------
def _older_than(p: Path, days: int, now: float) -> bool:
    try:
        return now - p.stat().st_mtime > days * 86400
    except OSError:
        return False


def _prune_runs(d: Path, keep: int, dry_run: bool) -> List[str]:
    if not d.is_dir():
        return []
    runs = sorted((p for p in d.iterdir() if p.is_dir() and p.name.startswith("run-")), key=lambda p: p.name)
    victims = runs[:-keep] if keep > 0 else runs
    if not dry_run:
        for p in victims:
            shutil.rmtree(p, ignore_errors=True)
    return [str(p) for p in victims]


def gc(factory, cfg, dry_run: bool = False, now: Optional[float] = None) -> dict:
    import time

    now = now if now is not None else time.time()
    r = retention_of(cfg)
    removed: List[str] = []
    rotated: List[str] = []
    for m in factory.list_missions():
        mdir = factory.mission_dir(m.id)
        removed += _prune_runs(mdir / "reports", int(r["reports_keep_runs"]), dry_run)
        hruns = factory.holdout_dir_for(m.id) / "runs"
        try:
            removed += _prune_runs(hruns, int(r["reports_keep_runs"]), dry_run)
        except PermissionError:
            pass  # 実行中で封印されている
        if m.status not in ACTIVE:
            pdir = mdir / "prompts"
            if pdir.is_dir():
                for p in pdir.iterdir():
                    if p.is_file() and _older_than(p, int(r["prompts_days"]), now):
                        removed.append(str(p))
                        if not dry_run:
                            p.unlink(missing_ok=True)
    drafts = factory.state_dir / "drafts"
    if drafts.is_dir():
        for d in drafts.iterdir():
            meta = load_json(d / "meta.json", {}) or {}
            if meta.get("status") in ("promoted", "dismissed") and _older_than(d, int(r["drafts_days"]), now):
                removed.append(str(d))
                if not dry_run:
                    shutil.rmtree(d, ignore_errors=True)
    bench = factory.state_dir / "bench" / "results"
    if bench.is_dir():
        for p in bench.iterdir():
            if _older_than(p, int(r["bench_days"]), now):
                removed.append(str(p))
                if not dry_run:
                    if p.is_dir():
                        shutil.rmtree(p, ignore_errors=True)
                    else:
                        p.unlink(missing_ok=True)
    max_bytes = int(float(r["log_max_mb"]) * 1024 * 1024)
    logs = list(factory.state_dir.glob("*.log"))
    ld = launchd_log_dir()
    if ld.is_dir():
        logs += list(ld.glob("*.log"))
    for p in logs:
        if dry_run:
            try:
                if p.stat().st_size > max_bytes:
                    rotated.append(str(p))
            except OSError:
                pass
            continue
        if rotate_log(p, max_bytes, int(r["log_keep"]), copytruncate=(p.parent == ld)):
            rotated.append(str(p))
    return {"removed": removed, "rotated": rotated, "dry_run": dry_run}


# ---------------------------------------------------------------------------
# backup / restore
# ---------------------------------------------------------------------------
_BACKUP_MISSION_FILES = ("mission.json", "spec.md", "build.json", "reports/final.md")


def _backup_members(root: Path) -> List[Path]:
    out: List[Path] = []
    fdir = root / ".factory"
    if fdir.is_dir():
        for p in fdir.rglob("*"):
            if not p.is_file():
                continue
            rel = p.relative_to(fdir).parts
            if p.name.endswith(".lock") or rel[0] in ("locks", "backups"):
                continue
            if rel[0] == "drafts" and p.name not in ("meta.json", "signal.md"):
                continue
            out.append(p)
    mdir = root / "missions"
    if mdir.is_dir():
        for d in mdir.iterdir():
            for name in _BACKUP_MISSION_FILES:
                p = d / name
                if p.is_file():
                    out.append(p)
    hdir = root / "holdout"
    if hdir.is_dir():
        for p in hdir.rglob("*"):
            rel = p.relative_to(hdir)
            if p.is_file() and "runs" not in rel.parts[1:2]:
                out.append(p)
    seeds = root / "seeds"
    if seeds.is_dir():
        out += [p for p in seeds.rglob("*") if p.is_file()]
    return out


def backup(factory, dest_dir: Optional[Path] = None, keep: int = 7) -> Path:
    """状態一式を backups/factory-<ts>.tar.gz に書き、古いものを keep 件まで減らす。"""
    from . import holdout as holdout_mod

    root = Path(factory.root)
    dest = Path(dest_dir) if dest_dir else factory.state_dir / "backups"
    ensure_dir(dest)
    try:
        dest.chmod(0o700)
    except OSError:
        pass
    for m in factory.list_missions():
        if m.status not in ("spec", "holdout", "dev"):
            holdout_mod.unseal_if_stale(factory, m.id)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out = dest / f"factory-{stamp}.tar.gz"
    tmp = out.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh, tarfile.open(fileobj=fh, mode="w:gz") as tar:
        for p in _backup_members(root):
            try:
                tar.add(p, arcname=str(p.relative_to(root)), recursive=False)
            except (OSError, PermissionError):
                continue  # 実行中で封印された holdout 等は次回
        manifest = json.dumps({"created": stamp, "root": str(root)}).encode()
        info = tarfile.TarInfo("BACKUP.json")
        info.size = len(manifest)
        import io

        tar.addfile(info, io.BytesIO(manifest))
    os.replace(tmp, out)
    olds = sorted(dest.glob("factory-*.tar.gz"))
    for p in olds[:-max(1, keep)]:
        p.unlink(missing_ok=True)
    return out


def _safe_member(m: tarfile.TarInfo) -> bool:
    name = m.name
    parts = Path(name).parts
    return (not name.startswith("/") and ".." not in parts and (m.isfile() or m.isdir())
            and parts and parts[0] in (".factory", "missions", "holdout", "seeds", "BACKUP.json"))


def restore(root: Path, archive: Path, force: bool = False) -> List[str]:
    """バックアップを root に展開する。安全でないメンバー (絶対パス・.. ・リンク) は拒否。"""
    root = Path(root)
    if (root / ".factory" / "config.json").exists() and not force:
        raise SystemExit(f"{root} には既に config があります (上書きするなら --force)")
    restored = []
    with tarfile.open(archive, "r:gz") as tar:
        members = tar.getmembers()
        bad = [m.name for m in members if not _safe_member(m)]
        if bad:
            raise SystemExit("安全でないメンバーを含むため展開しません: " + ", ".join(bad[:5]))
        for m in members:
            if m.name == "BACKUP.json" or not m.isfile():
                continue
            dst = root / m.name
            ensure_dir(dst.parent)
            src = tar.extractfile(m)
            if src is None:
                continue
            with open(dst, "wb") as fh:
                shutil.copyfileobj(src, fh)
            os.chmod(dst, stat.S_IMODE(m.mode) or 0o600)
            restored.append(m.name)
    for sub in (".factory", "holdout"):
        try:
            (root / sub).chmod(0o700)
        except OSError:
            pass
    return restored
