"""`factory selftest`: 一時ディレクトリで全パイプラインを mock で通す e2e QA。

    factory selftest [--keep]

導入直後・更新直後の Mac mini で「サブスク枠を使わずに」配線が正しいかを確かめる。
本物のワーカー CLI (claude / codex / opencode) は失敗するスタブで PATH から隠すので、
誤って呼ばれても枠は減らない (呼ばれたらそのステージは FAIL)。

ステージ: init → config check → mission run → holdout (封印解除・指紋) → merge gate →
tick (自動マージ + カナリア) → tick 冪等性 → needs_human → worktree 掃除
"""
from __future__ import annotations

import contextlib
import os
import shutil
import stat
import tempfile
import time
from pathlib import Path
from typing import Callable, List, Tuple

STUB_CLIS = ("claude", "codex", "opencode")


@contextlib.contextmanager
def _guarded_env(stub_dir: Path):
    """本物の CLI を隠すスタブと、外部に出ない環境 (API キー・通知先なし)。"""
    calls = stub_dir / "calls.log"
    for c in STUB_CLIS:
        p = stub_dir / c
        p.write_text(f"#!/bin/sh\necho {c} >> '{calls}'\necho 'selftest: real CLI must not run' >&2\nexit 97\n",
                     encoding="utf-8")
        p.chmod(0o755)
    saved = {k: os.environ.get(k) for k in ("PATH", "OPENROUTER_API_KEY")}
    os.environ["PATH"] = f"{stub_dir}{os.pathsep}{os.environ.get('PATH', '')}"
    os.environ.pop("OPENROUTER_API_KEY", None)
    try:
        yield calls
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def run(keep: bool = False, verbose: bool = False) -> Tuple[bool, List[Tuple[str, bool, str]], Path]:
    from . import gitx, mergegate
    from . import holdout as holdout_mod
    from .config import Config
    from .configcheck import errors, validate
    from .init_scaffold import init_factory
    from .loop import run_mission
    from .state import Factory, is_merged
    from .supervisor import open_needs_human, tick

    root = Path(tempfile.mkdtemp(prefix="factory-selftest-"))
    stub_dir = Path(tempfile.mkdtemp(prefix="factory-selftest-stubs-"))
    results: List[Tuple[str, bool, str]] = []
    ctx: dict = {}

    def stage(name: str, fn: Callable[[], str]) -> bool:
        t0 = time.time()
        try:
            if verbose:
                detail = fn()
            else:
                with open(os.devnull, "w") as devnull, contextlib.redirect_stdout(devnull):
                    detail = fn()
            ok = True
        except AssertionError as e:
            ok, detail = False, str(e) or "assertion failed"
        except Exception as e:  # noqa — ステージの失敗として記録する
            ok, detail = False, f"{type(e).__name__}: {e}"
        results.append((name, ok, f"{detail} ({time.time() - t0:.1f}s)"))
        return ok

    with _guarded_env(stub_dir) as calls:
        def s_init():
            init_factory(root, demo=True)
            cfg = Config.create(str(root), demo=True)
            cfg.autonomy_level = 2
            ctx["cfg"], ctx["f"] = cfg, Factory(root)
            return str(root)

        def s_config():
            issues = validate(ctx["cfg"].to_dict())
            assert not errors(issues), "; ".join(map(str, errors(issues)))
            return f"{len(issues)} warning(s)"

        def s_run():
            cfg, f = ctx["cfg"], ctx["f"]
            m = f.create_mission("selftest: メモAPI", kind="update", seed="demo-app",
                                 repo=cfg.projects["demo_app"].path)
            m2 = run_mission(f, m.id, cfg=cfg)
            ctx["mid"] = m.id
            assert m2.status == "done", f"status={m2.status} halt={m2.halt_reason}"
            return f"{m.id} done in {len(m2.iterations)} iteration(s), score {m2.iterations[-1].score}"

        def s_holdout():
            f, mid = ctx["f"], ctx["mid"]
            mode = stat.S_IMODE(f.holdout_dir_for(mid).stat().st_mode)
            assert mode != 0, "holdout が封印されたまま"
            state = holdout_mod.verify_manifest(f, mid)
            assert state == "ok", f"manifest={state}"
            assert not list(f.reports_dir(mid).rglob("*.md")) or all(
                "FACTORY_HOLDOUT_DIR" not in p.read_text(encoding="utf-8") for p in f.reports_dir(mid).rglob("*.md")
            ), "mission レポートにシナリオのコマンドが出ています"
            return f"mode {oct(mode)}, manifest ok"

        def s_gate():
            cfg, f, mid = ctx["cfg"], ctx["f"], ctx["mid"]
            m = f.get_mission(mid)
            repo = Path(cfg.root) / m.repo
            cls = mergegate.classify(cfg, repo, f"factory/{mid}", m)
            assert cls["tier"] in ("mechanical", "scoped"), f"tier={cls['tier']} ({mergegate.describe(cls)})"
            return f"tier={cls['tier']} ({mergegate.describe(cls)})"

        def s_tick():
            cfg, f, mid = ctx["cfg"], ctx["f"], ctx["mid"]
            out = tick(f, cfg)
            dl = out.get("deliver") or {}
            assert mid in (dl.get("merged") or []), f"merged={dl.get('merged')} escalate={dl.get('escalate')}"
            canary = [c for c in dl.get("canary") or [] if isinstance(c, dict) and c.get("mission") == mid]
            assert canary and canary[0]["status"] == "pass", f"canary={dl.get('canary')}"
            assert is_merged(f.get_mission(mid))
            return "merged + canary pass"

        def s_idempotent():
            out = tick(ctx["f"], ctx["cfg"])
            ran = (out.get("execute") or {}).get("ran") or []
            merged = (out.get("deliver") or {}).get("merged") or []
            assert not ran and not merged, f"ran={ran} merged={merged}"
            return "2 回目の tick は無活動"

        def s_needs_human():
            items = open_needs_human(ctx["f"])
            assert not items, "; ".join(e["text"] for e in items)
            return "0 件"

        def s_worktree():
            cfg, f, mid = ctx["cfg"], ctx["f"], ctx["mid"]
            m = f.get_mission(mid)
            repo = Path(cfg.root) / m.repo
            assert not f.worktree(mid).exists(), "worktree が残っています"
            assert gitx.tracked_dirty(repo) == "", "main repo に未コミット変更"
            return "worktree 掃除済み / main clean"

        def s_no_real_cli():
            used = calls.read_text(encoding="utf-8").split() if calls.exists() else []
            assert not used, f"本物の CLI が呼ばれました: {used}"
            return "0 回"

        for name, fn in (("init", s_init), ("config check", s_config), ("mission run", s_run),
                         ("holdout isolation", s_holdout), ("merge gate", s_gate),
                         ("tick: merge + canary", s_tick), ("tick idempotent", s_idempotent),
                         ("needs_human", s_needs_human), ("worktree cleanup", s_worktree),
                         ("no real CLI calls", s_no_real_cli)):
            if not stage(name, fn) and name in ("init", "mission run"):
                break  # 以降のステージは前提が崩れている

    shutil.rmtree(stub_dir, ignore_errors=True)
    ok = all(r[1] for r in results) and len(results) >= 10
    if not keep:
        for d in root.rglob("*"):
            try:
                if d.is_dir() and not os.access(d, os.R_OK | os.X_OK):
                    d.chmod(0o755)
            except OSError:
                pass
        shutil.rmtree(root, ignore_errors=True)
    return ok, results, root


def main(keep: bool = False, verbose: bool = False) -> int:
    ok, results, root = run(keep=keep, verbose=verbose)
    for name, passed, detail in results:
        print(f" {'PASS' if passed else 'FAIL'}  {name:<22} {detail}")
    print(f"\nselftest: {'PASS' if ok else 'FAIL'}" + (f"  (作業ディレクトリ: {root})" if keep else ""))
    return 0 if ok else 1
