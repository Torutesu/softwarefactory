"""Factory CLI: `python -m factory <subcommand>`。

主要コマンド:
  init        -- ファクトリを初期化 (--demo でデモ一式)
  status      -- ミッション一覧
  seed add    -- Seed を追加
  mission new -- ミッションを作成 / list -- 一覧
  run         -- ミッションを自律ループで回す
  run-all     -- 待ち行列のミッションを順に回す
  validate    -- 検証のみ実行 (worker を呼ばない)
  report      -- 最終レポートを表示
  merge       -- worktree ブランチを main に squash マージ
  usage       -- 使用量レポート
  worker ping -- CLI の疎通確認
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from .config import Config, WorkerConfig, save as save_config, DEFAULT_WORKER_ORDER
from .init_scaffold import init_factory
from .loop import run_mission
from .state import Factory
from .usage import Usage
from .util import run_cmd, which


def _require_cfg() -> "tuple[Config, Factory]":
    cfg = Config.discover()
    if not cfg:
        print("エラー: .factory/config.json が見つかりません。先に `factory init` を実行してください。", file=sys.stderr)
        sys.exit(2)
    return cfg, Factory(Path(cfg.root))


def _resolve_worker_override(cfg: Config, name: str) -> None:
    if name and name != "auto":
        cfg.use_worker = name
        cfg.worker_order = [name] + [w for w in DEFAULT_WORKER_ORDER if w != name]


# ---------------------------------------------------------------------------
def cmd_init(args) -> None:
    init_factory(Path(args.root).resolve(), demo=args.demo)


def cmd_status(args) -> None:
    cfg, f = _require_cfg()
    ms = f.list_missions()
    if not ms:
        print("ミッションなし。`factory mission new` で作成してください。")
        return
    print(f"{'ID':<22} {'STATUS':<9} {'ITER':<4} {'SCORE':<5} {'RISK':<5} TITLE")
    for m in ms:
        score = m.iterations[-1].score if m.iterations and m.iterations[-1].score is not None else "-"
        print(f"{m.id:<22} {m.status:<9} {len(m.iterations):<4} {str(score):<5} {m.risk:<5} {m.title}")


def cmd_worker_ping(args) -> None:
    cfg, _ = _require_cfg()
    ok = True
    for name in cfg.worker_order:
        wc = cfg.workers.get(name) or WorkerConfig(type=name)
        if wc.type == "mock":
            print(f"[ok] {name}: mock (常時利用可)")
            continue
        cmd = wc.resolved_command()
        path = which(cmd)
        if not path:
            print(f"[ng] {name}: `{cmd}` が見つかりません")
            ok = False
            continue
        r = run_cmd([path, "--version"], timeout=30)
        ver = (r.out or r.err).strip().splitlines()[:1]
        print(f"[ok] {name}: `{cmd}` {ver[0] if ver else ''}")
    if not ok:
        print("→ 未インストールの CLI があります (.factory/config.json の worker_order を確認)。", file=sys.stderr)


def cmd_seed(args) -> None:
    cfg, f = _require_cfg()
    if args.action != "add":
        print("usage: factory seed add <src> [--name NAME]", file=sys.stderr)
        sys.exit(2)
    src = Path(args.src).resolve()
    if not src.exists():
        print(f"エラー: {src} が存在しません", file=sys.stderr)
        sys.exit(2)
    name = args.name or src.stem
    dst = f.seeds_dir / name
    if dst.exists():
        print(f"エラー: {dst} は既に存在します", file=sys.stderr)
        sys.exit(2)
    if src.is_dir():
        shutil.copytree(src, dst, ignore=shutil.ignore_patterns(".git", ".DS_Store"))
    else:
        dst.mkdir(parents=True)
        shutil.copy2(src, dst / src.name)
    readme = dst / "seed.md"
    if not readme.exists():
        readme.write_text(f"# Seed: {name}\n\n(ここに目的・参照材料・受け入れ基準を書いてください)\n", encoding="utf-8")
    print(f"seed added: {dst}")


def cmd_mission(args) -> None:
    cfg, f = _require_cfg()
    if args.action == "list":
        cmd_status(args)
        return
    # new
    if not args.title:
        print("usage: factory mission new <title> --project NAME|--repo PATH", file=sys.stderr)
        sys.exit(2)
    if args.project:
        pc = cfg.projects.get(args.project)
        if not pc:
            print(f"エラー: project {args.project} が未登録です", file=sys.stderr)
            sys.exit(2)
        repo = pc.path
    elif args.repo:
        repo = args.repo
    else:
        print("エラー: --project NAME か --repo PATH のどちらかを指定してください", file=sys.stderr)
        sys.exit(2)
    m = f.create_mission(
        title=args.title,
        kind=args.kind,
        seed=args.seed,
        repo=repo,
        risk=args.risk,
        priority=args.priority,
        max_iterations=args.iterations,
        pass_score=args.pass_score,
        notes=args.notes,
    )
    print(f"mission created: {m.id} ({m.status})")


def cmd_run(args) -> None:
    cfg, f = _require_cfg()
    _resolve_worker_override(cfg, args.worker)
    m = run_mission(f, args.mission, confirm=args.confirm, iterations=args.iterations, cfg=cfg)
    print(f"status: {m.status}")


def cmd_run_all(args) -> None:
    cfg, f = _require_cfg()
    _resolve_worker_override(cfg, args.worker)
    ms = [m for m in f.list_missions() if m.status == "queued"]
    ms.sort(key=lambda m: (m.priority, m.id))
    if not ms:
        print("実行待ちのミッションはありません")
        return
    for m in ms:
        print(f"=== run {m.id} ({m.title}) ===")
        try:
            run_mission(f, m.id, confirm=args.confirm, cfg=cfg)
        except SystemExit as e:
            print(f"[skip] {e}")
        except Exception as e:
            print(f"[error] {m.id}: {e}")


def cmd_validate(args) -> None:
    cfg, f = _require_cfg()
    from .harness.judge import run_judge
    from .harness.runner import evaluate
    from .workers.router import Router

    m = f.get_mission(args.mission)
    if not m:
        raise SystemExit(f"mission not found: {args.mission}")
    repo_path = Path(m.repo).resolve() if Path(m.repo).is_absolute() else f.root / m.repo
    wt = f.worktree(m.id) if f.worktree(m.id).exists() else repo_path
    hr = evaluate(f, m, wt)
    print(json.dumps(hr.to_dict(), ensure_ascii=False, indent=2))
    if args.judge:
        judge_data = run_judge(f, m, hr, "(validate コマンド)", Router(cfg, Usage(f.state_dir)))
        print("judge:", json.dumps(judge_data, ensure_ascii=False) if judge_data else "なし")


def cmd_report(args) -> None:
    cfg, f = _require_cfg()
    p = f.reports_dir(args.mission) / "final.md"
    if not p.exists():
        print(f"レポートがありません: {p}", file=sys.stderr)
        sys.exit(2)
    print(p.read_text(encoding="utf-8"))


def cmd_merge(args) -> None:
    cfg, f = _require_cfg()
    from . import gitx

    m = f.get_mission(args.mission)
    if not m:
        raise SystemExit(f"mission not found: {args.mission}")
    if not args.yes:
        r = input(f"{m.id} の worktree を main に squash マージします。よろしいですか? [y/N] ")
        if r.strip().lower() != "y":
            print("中止しました")
            return
    repo_path = Path(m.repo).resolve() if Path(m.repo).is_absolute() else f.root / m.repo
    wt = f.worktree(m.id)
    dirty = gitx.status_porcelain(wt)
    if dirty:
        print("worktree に未コミットの変更があります。まずワーカーにコミットさせてください:", file=sys.stderr)
        print(dirty, file=sys.stderr)
        sys.exit(1)
    r = gitx.merge_squash(repo_path, f"factory/{m.id}", f"factory: {m.id} {m.title}")
    if r.code != 0:
        print(f"マージ失敗: {r.err or r.out}", file=sys.stderr)
        sys.exit(1)
    gitx.remove_worktree(repo_path, wt)
    m.status = "done"
    m.notes = (m.notes + "\n" if m.notes else "") + "merged to main"
    f.save_mission(m)
    f.event("mission_merged", m.id)
    print(f"merged: {m.id}")


def cmd_usage(args) -> None:
    cfg, f = _require_cfg()
    print(Usage(f.state_dir).report(days=args.days))


# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="factory", description="社内ソフトウェアファクトリー オーケストレータ")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("init", help="ファクトリを初期化")
    sp.add_argument("root", nargs="?", default=".", help="ファクトリルート (デフォルト: カレント)")
    sp.add_argument("--demo", action="store_true", help="デモ (サンプル製品リポジトリ) も作る")
    sp.set_defaults(fn=cmd_init)

    sp = sub.add_parser("status", help="ミッション一覧")
    sp.set_defaults(fn=cmd_status)

    sp = sub.add_parser("worker", help="worker 操作")
    sp.add_argument("action", choices=["ping"])
    sp.set_defaults(fn=cmd_worker_ping)

    sp = sub.add_parser("seed", help="seed 操作")
    sp.add_argument("action", choices=["add"])
    sp.add_argument("src")
    sp.add_argument("--name", default="")
    sp.set_defaults(fn=cmd_seed)

    sp = sub.add_parser("mission", help="mission 操作")
    sp.add_argument("action", choices=["new", "list"])
    sp.add_argument("title", nargs="?", default="")
    sp.add_argument("--kind", default="greenfield", choices=["greenfield", "update"])
    sp.add_argument("--seed", default="", help="seeds/ 配下の seed 名")
    sp.add_argument("--project", default="", help="config に登録済みのプロジェクト名")
    sp.add_argument("--repo", default="", help="リポジトリパス (project 未指定時)")
    sp.add_argument("--risk", default="low", choices=["low", "high"])
    sp.add_argument("--priority", type=int, default=5)
    sp.add_argument("--iterations", type=int, default=3)
    sp.add_argument("--pass-score", type=int, default=80)
    sp.add_argument("--notes", default="")
    sp.set_defaults(fn=cmd_mission)

    sp = sub.add_parser("run", help="ミッションを実行")
    sp.add_argument("mission")
    sp.add_argument("--worker", default="auto")
    sp.add_argument("--confirm", action="store_true", help="高リスクミッションの実行許可")
    sp.add_argument("--iterations", type=int, default=None)
    sp.set_defaults(fn=cmd_run)

    sp = sub.add_parser("run-all", help="queued ミッションを順に実行")
    sp.add_argument("--worker", default="auto")
    sp.add_argument("--confirm", action="store_true")
    sp.set_defaults(fn=cmd_run_all)

    sp = sub.add_parser("validate", help="検証のみ (worker 呼び出しなし)")
    sp.add_argument("mission")
    sp.add_argument("--judge", action="store_true", help="LLM ジャッジも実行")
    sp.set_defaults(fn=cmd_validate)

    sp = sub.add_parser("report", help="最終レポート表示")
    sp.add_argument("mission")
    sp.set_defaults(fn=cmd_report)

    sp = sub.add_parser("merge", help="worktree を main に squash マージ")
    sp.add_argument("mission")
    sp.add_argument("--yes", action="store_true")
    sp.set_defaults(fn=cmd_merge)

    sp = sub.add_parser("usage", help="使用量レポート")
    sp.add_argument("--days", type=int, default=7)
    sp.set_defaults(fn=cmd_usage)

    return p


def main(argv=None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.fn(args)
    except KeyboardInterrupt:
        print("\n中断しました")
        sys.exit(130)


if __name__ == "__main__":
    main()
