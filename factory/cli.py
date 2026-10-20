"""Factory CLI: `python -m factory <subcommand>`。

主要コマンド:
  init        -- ファクトリを初期化 (--demo でデモ一式)
  status      -- ミッション一覧 (--watch でダッシュボード)
  signals     -- 取り込んだシグナル一覧
  seed add    -- Seed を追加
  ingest      -- 素材/URL を Seed 化 (+ --run でミッション作成~実行まで一括)
  collect     -- 定時シグナル収集 (cron で巡回 → ドラフト化 → 人間が promote)
  listen      -- Webhook リスナー (POST /signals → Seed 化 / ミッション自動化)
  mission new -- ミッションを作成 / list -- 一覧
  run         -- ミッションを自律ループで回す
  run-all     -- 待ち行列のミッションを順に回す (--parallel N で並列)
  validate    -- 検証のみ実行 (worker を呼ばない)
  report      -- 最終レポートを表示
  merge       -- worktree ブランチを main に squash マージ
  canary      -- マージ済みを main で再検証 (--fix で revert + 再ミッション)
  pr          -- gh で PR 作成 (なければ下書き)
  holdout     -- シナリオの list / regen (陳腐化リフレッシュ)
  compare-img -- 2枚の PNG 類似度 (UI ベンチ比較の補助)
  usage       -- 使用量レポート
  bench       -- 社内 SWE ベンチ (ワーカー×タスクの cost/品質比較 → purpose_workers の根拠)
  tick        -- ファクトリークロック 1巡 (--dry-run で計画表示)
  supervisor  -- tick を常駐実行 (自律運転)
  worktrees   -- たまった worktree の掃除 (完了/bench 対象, 失敗は残す)
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
    show_bench = getattr(args, "all", False)
    watch = getattr(args, "watch", 0) or 0
    if watch:
        import time

        while True:
            print("\033[2J\033[H", end="", flush=True)
            print("factory dashboard (Ctrl-C で終了)  " + time.strftime("%Y-%m-%d %H:%M:%S"))
            print()
            _print_status(f, show_bench=show_bench)
            _print_dashboard(f, cfg)
            time.sleep(watch)
    else:
        _print_status(f, show_bench=show_bench)


def cmd_worker_ping(args) -> None:
    cfg, _ = _require_cfg()
    ok = True
    for name in cfg.worker_order:
        wc = cfg.workers.get(name) or WorkerConfig(type=name)
        if wc.type == "mock":
            print(f"[ok] {name}: mock (常時利用可)")
            continue
        if wc.type == "openrouter":
            import os

            if os.environ.get("OPENROUTER_API_KEY", "").strip():
                print(f"[ok] {name}: OPENROUTER_API_KEY 設定済 (model={wc.model or 'openrouter/auto'}, auth=api_key)")
            else:
                print(f"[ng] {name}: OPENROUTER_API_KEY 未設定 (spec/holdout/judge 用途に使用可)")
                ok = False
            continue
        cmd = wc.resolved_command()
        path = which(cmd)
        if not path:
            print(f"[ng] {name}: `{cmd}` が見つかりません (auth={wc.auth})")
            ok = False
            continue
        r = run_cmd([path, "--version"], timeout=30)
        ver = (r.out or r.err).strip().splitlines()[:1]
        print(f"[ok] {name}: `{cmd}` {ver[0] if ver else ''} (auth={wc.auth})")
    if not ok:
        print("→ 未インストール/未設定の worker があります (.factory/config.json の worker_order を確認)。", file=sys.stderr)


def cmd_tick(args) -> None:
    cfg, f = _require_cfg()
    from .supervisor import tick

    tick(f, cfg, dry_run=args.dry_run)


def cmd_supervisor(args) -> None:
    cfg, f = _require_cfg()
    from .supervisor import supervise

    supervise(f, cfg, interval=max(5, args.interval))


def cmd_worktrees(args) -> None:
    """worktree の生涯管理: 完了/bench ミッションの worktree を掃除する入口。

    失敗ミッションの worktree は調査用に既定では残す (--force-failed で消す)。
    main worktree やファクトリ管理外の worktree には触れない。
    """
    cfg, f = _require_cfg()
    from . import gitx

    def repo_of(m):
        repo = Path(m.repo) if Path(m.repo).is_absolute() else f.root / m.repo
        return repo

    repo_cache: dict = {}
    rows = []
    for m in f.list_missions():
        repo = repo_of(m)
        if not repo.exists():
            continue
        listing = repo_cache.setdefault(repo.resolve(), gitx.worktrees(repo.resolve()))
        if not listing:
            continue
        wt = f.worktree(m.id)
        actual = {w["path"].resolve() for w in listing}
        if not any(wt.resolve() == p or wt.resolve() in p.parents for p in actual):
            continue  # 関連 worktree が存在しない (掃除済み)
        removable = (m.status == "done" or m.kind == "bench" or args.force_failed)
        rows.append((m, repo.resolve(), removable))

    if not rows:
        print("掃除対象の worktree はありません")
        return
    changed = False
    for m, repo_path, removable in rows:
        if removable and not args.dry_run:
            try:
                gitx.remove_worktree(repo_path, f.worktree(m.id), force=True)
                f.event("worktree_pruned", m.id)
                print(f"[削除] {m.id} status={m.status} kind={m.kind}/{m.title[:28]}")
                changed = True
                continue
            except Exception as e:  # noqa — 1 ミッション失敗で止めない
                print(f"[失敗] {m.id}: 削除に失敗 {e}", file=sys.stderr)
                continue
        tag = "保留" if not removable else "(dry-run)"
        hint = "(失敗ミッションは調査用に残す: --force-failed で消せる)" if not removable else ""
        print(f"[{tag}] {m.id} status={m.status} kind={m.kind}/{m.title[:28]} {hint}")
    if args.dry_run:
        print("(dry-run: 変更していません)", file=sys.stderr)
    elif changed:
        print("worktree を掃除しました")


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
    f.add_signal(name, "manual:seed-add")
    print(f"seed added: {dst}")


def _resolve_repo(cfg: Config, project: str, repo_arg: str) -> str:
    if project:
        pc = cfg.projects.get(project)
        if not pc:
            raise SystemExit(f"エラー: project {project} が未登録です")
        return pc.path
    if repo_arg:
        return repo_arg
    raise SystemExit("エラー: --project NAME か --repo PATH のどちらかを指定してください")


def cmd_mission(args) -> None:
    cfg, f = _require_cfg()
    if args.action == "list":
        cmd_status(args)
        return
    # new
    if not args.title:
        print("usage: factory mission new <title> --project NAME|--repo PATH", file=sys.stderr)
        sys.exit(2)
    repo = _resolve_repo(cfg, args.project, args.repo)
    m = f.create_mission(
        title=args.title,
        kind=args.kind,
        seed=args.seed,
        repo=repo,
        risk=args.risk,
        priority=args.priority,
        max_iterations=args.iterations,
        pass_score=args.pass_score,
        model=args.model,
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
    if (getattr(args, "parallel", 0) or 0) <= 1:
        for m in ms:
            print(f"=== run {m.id} ({m.title}) ===")
            try:
                run_mission(f, m.id, confirm=args.confirm, cfg=cfg)
            except SystemExit as e:
                print(f"[skip] {e}")
            except Exception as e:
                print(f"[error] {m.id}: {e}")
        return

    from concurrent.futures import ThreadPoolExecutor, as_completed

    n = min(args.parallel, len(ms))
    print(f"=== run-all parallel: {n} worker threads, {len(ms)} missions ===")

    def one(mid: str) -> str:
        try:
            m2 = run_mission(f, mid, confirm=args.confirm, cfg=cfg)
            return f"[done] {mid}: {m2.status}"
        except SystemExit as e:
            return f"[skip] {mid}: {e}"
        except Exception as e:
            return f"[error] {mid}: {e}"

    with ThreadPoolExecutor(max_workers=n) as ex:
        futs = {ex.submit(one, m.id): m.id for m in ms}
        for fut in as_completed(futs):
            print(fut.result(), flush=True)


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
    from . import gitx, mergegate

    m = f.get_mission(args.mission)
    if not m:
        raise SystemExit(f"mission not found: {args.mission}")
    repo_path = Path(m.repo).resolve() if Path(m.repo).is_absolute() else f.root / m.repo
    wt = f.worktree(m.id)
    dirty = gitx.status_porcelain(wt)
    if dirty:
        print("worktree に未コミットの変更があります。まずワーカーにコミットさせてください:", file=sys.stderr)
        print(dirty, file=sys.stderr)
        sys.exit(1)
    # --- Merge gate: blast radius を表示してから判断させる ----------------
    cls = mergegate.classify(cfg, repo_path, f"factory/{m.id}", m)
    tier = cls["tier"]
    print(f"マージゲート: tier={tier} ({mergegate.describe(cls)})")
    print(f"推奨レビュー: {cls['review']}")
    if tier in ("behavioral", "sensitive") or m.risk != "low":
        print("⚠ この差分は製品の振る舞い/センシティブ領域に触れます --yes は人間の判断として記録されます")
    if not args.yes:
        r = input(f"{m.id} の worktree を main に squash マージします。よろしいですか? [y/N] ")
        if r.strip().lower() != "y":
            print("中止しました")
            return
    r = gitx.merge_squash(repo_path, f"factory/{m.id}", mergegate.merge_message(m))
    if r.code != 0:
        print(f"マージ失敗: {r.err or r.out}", file=sys.stderr)
        sys.exit(1)
    gitx.remove_worktree(repo_path, wt)
    m.status = "done"
    m.notes = (m.notes + "\n" if m.notes else "") + f"merged to main (tier: {tier})"
    f.save_mission(m)
    f.event("mission_merged", m.id)
    print(f"merged: {m.id}")


def cmd_usage(args) -> None:
    cfg, f = _require_cfg()
    u = Usage(f.state_dir)
    print(u.report(days=args.days))
    if args.waste:
        print()
        print(u.waste_report(factory=f, days=args.days))


def cmd_bench(args) -> None:
    cfg, f = _require_cfg()
    from . import bench as bench_mod

    if args.init:
        for p in bench_mod.init_sample_tasks(f):
            print(f"sample task: {p}")
        print("→ `factory bench --dry-run` で計画を確認してから実行してください")
        return
    if args.history:
        for line in bench_mod.history(f):
            print(line)
        return
    ids = [s.strip() for s in (args.tasks or "").split(",") if s.strip()] or None
    ws = [s.strip() for s in (args.workers or "").split(",") if s.strip()] or None
    res = bench_mod.run_bench(f, cfg, workers=ws, task_ids=ids, repeat=args.repeat,
                              max_iters=args.max_iters, dry_run=args.dry_run)
    print()
    print(bench_mod.render_markdown(res))


def _print_status(f: Factory, show_bench: bool = False) -> None:
    from .state import mission_cost_usd

    ms = f.list_missions()
    bench_ms = [m for m in ms if m.kind == "bench"]
    ms = [m for m in ms if m.kind != "bench"]  # bench 専用ミッションは bench コマンド側で見る
    if not ms and not bench_ms:
        print("ミッションなし。`factory mission new` で作成してください。")
        return
    print(f"{'ID':<22} {'STATUS':<9} {'ITER':<4} {'SCORE':<5} {'COST':<8} {'RISK':<5} TITLE")

    def row(m, tag: str = "") -> None:
        score = m.iterations[-1].score if m.iterations and m.iterations[-1].score is not None else "-"
        cost = mission_cost_usd(m)
        cost_s = f"${cost:.2f}" if cost is not None else "-"
        halt = f"[{m.halt_reason}] " if m.halt_reason and m.status != "done" else ""
        print(f"{m.id:<22} {m.status:<9} {len(m.iterations):<4} {str(score):<5} {cost_s:<8} {m.risk:<5} {tag}{halt}{m.title}")

    for m in ms:
        row(m)
    if show_bench and bench_ms:
        for m in bench_ms:
            row(m, tag="[bench] ")
    elif bench_ms:
        print(f"(注: bench ミッション {len(bench_ms)} 件を省略 — `factory bench --history` / "
              "`factory report <id>` で確認、--all で表示)")
    # 目刻りの一行サマリ (--watch では詳細パネルに続く)
    pending = [m for m in ms if m.status == "done" and "merged to main" not in (m.notes or "")]
    nh = _needs_human_backlog(f)
    if pending or nh:
        print(f"(review 待ち {len(pending)} / needs_human {len(nh)} 詳細: status --watch)")


def _needs_human_backlog(f: Factory) -> list:
    """最新 tick イベントに積まれた needs_human の滞留リスト。"""
    import json as _json

    for e in reversed(f.state().get("events") or []):
        if e.get("kind") == "tick":
            try:
                return (_json.loads(e.get("detail") or "{}").get("needs_human") or [])
            except _json.JSONDecodeError:
                return []
    return []


def _print_dashboard(f: Factory, cfg: Config) -> None:
    """status --watch 用の詳細パネル: usage 窓 / review 待ちティア / canary / needs_human / drafts。

    すべて実測 (events, usage, git) を見る自己申告なしの表示。git の分類に失敗した
    ミッションは unknown ティア (fail-safe で人間に寄る) で数える。
    """
    from .usage import Usage

    print("\n── workers (usage) ──")
    u = Usage(cfg.state_dir)
    today = u.today()
    for name in cfg.worker_order:
        wc = cfg.workers.get(name) or WorkerConfig(type=name)
        fails = ((today.get("workers") or {}).get(name) or {}).get("fails", 0)
        line = f"  {name:<14} {u.budget_status(name, wc)}"
        if fails:
            line += f"  fail今日={fails}"
        print(line)

    print("\n── review 待ち (done 未マージ) ──")
    from . import mergegate

    pend = [m for m in f.list_missions()
            if m.kind != "bench" and m.status == "done"
            and "merged to main" not in (m.notes or "")]
    if not pend:
        print("  なし")
    tiers: dict = {}
    for m in pend:
        repo = Path(m.repo) if Path(m.repo).is_absolute() else f.root / m.repo
        t = mergegate.classify(cfg, repo, f"factory/{m.id}", m).get("tier", "unknown")
        tiers.setdefault(t, []).append(m.id)
    for t in ("sensitive", "behavioral", "unknown", "scoped", "mechanical"):
        if t in tiers:
            ids = tiers[t]
            print(f"  [{t:<10}] {len(ids)}  {', '.join(ids[:4])}" + (" …" if len(ids) > 4 else ""))

    print("\n── canary ──")
    last = next((e for e in reversed(f.state().get("events") or [])
                 if e.get("kind") in ("canary_pass", "canary_fail")), None)
    if not last:
        print("  実績なし")
    else:
        print(f"  last: {last['kind']} {str(last.get('ts', ''))[:16]} ({str(last.get('detail'))[:40]})")

    print("\n── needs_human 滞留 ──")
    nh = _needs_human_backlog(f)
    if not nh:
        print("  なし")
    else:
        for x in nh[:5]:
            print(f"  - {str(x)[:110]}")
        if len(nh) > 5:
            print(f"  … 他 {len(nh) - 5} 件")

    print("\n── collect 未処理ドラフト ──")
    try:
        from .collect import list_drafts

        rows = [r for r in list_drafts(f) if r["status"] == "new"]
        print(f"  {len(rows)} 件" + ("" if not rows else
              f"  (最新: {rows[0]['id']} {rows[0]['title'][:40]})"))
    except Exception:  # noqa — ダッシュボードは収集未設定でも壊れない
        print("  -")


def cmd_signals(args) -> None:
    cfg, f = _require_cfg()
    st = f.state()
    signals = st.get("signals") or []
    missions = st.get("missions") or {}
    if not signals:
        print("シグナルなし。`factory listen` で Webhook 受付、`factory ingest` で手動取込。")
        return
    print(f"{'SEED':<28} {'SOURCE':<12} MISSION")
    for s in signals:
        print(f"{str(s.get('seed', '')):<28} {str(s.get('source', '')):<12} {str(s.get('mission') or '-')}")


def cmd_compare_img(args) -> None:
    from .harness.png import similarity

    try:
        score = similarity(args.image_a, args.image_b, grid=args.grid)
    except Exception as e:
        print(f"エラー: {e}", file=sys.stderr)
        sys.exit(2)
    print(f"similarity: {score:.4f} ({'似ている' if score >= args.ratio else '似ていない'} / 閾値 {args.ratio})")


def cmd_ingest(args) -> None:
    cfg, f = _require_cfg()
    from .ingest import ingest_dir, ingest_url

    src = args.src
    if src.startswith(("http://", "https://")):
        seed_dir = ingest_url(f, src, name=args.name, title=args.title)
        f.add_signal(seed_dir.name, "url:" + src[:80])
    else:
        seed_dir = ingest_dir(f, Path(args.src), name=args.name)
        f.add_signal(seed_dir.name, "local:" + src[:60])
    print(f"seed ingested: {seed_dir}")
    if not (args.project or args.repo):
        print("→ mission は未作成 (--project NAME / --repo PATH を付けると自動作成)")
        return
    repo = _resolve_repo(cfg, args.project, args.repo)
    m = f.create_mission(
        title=args.title or seed_dir.name,
        kind=args.kind,
        seed=seed_dir.name,
        repo=repo,
        risk=args.risk,
        priority=args.priority,
        max_iterations=args.iterations,
        pass_score=args.pass_score,
        model=args.model,
        notes=args.notes or "ingested",
    )
    print(f"mission created: {m.id} ({m.status})")
    if args.run:
        _resolve_worker_override(cfg, args.worker)
        m2 = run_mission(f, m.id, confirm=args.confirm, iterations=args.iterations, cfg=cfg)
        print(f"status: {m2.status}")


def cmd_collect(args) -> None:
    cfg, f = _require_cfg()
    from . import collect as collect_mod

    if args.list:
        srcs = collect_mod.sources_of(cfg)
        print(f"sources: {len(srcs)} (config: collect.sources)")
        for s in srcs:
            target = s.get("url") or s.get("path") or s.get("cmd") or ""
            every = f", every {s.get('every_minutes')}min" if s.get("every_minutes") else ""
            print(f"  - {s.get('name')} [{s.get('type')}] {target[:70]}{every}")
        rows = collect_mod.list_drafts(f)
        print(f"\ndrafts: {len(rows)}")
        for r in rows:
            print(f"  {r['id']}  [{r['status']:>8}] {r['source']:<12} {r['title']}"
                  + (f" -> seeds/{r['seed']}" if r["seed"] else ""))
        return

    if args.promote:
        sd = collect_mod.promote(f, args.promote, seed_name=args.name, title=args.title)
        print(f"seed promoted: {sd}")
        if args.project or args.repo:
            repo = _resolve_repo(cfg, args.project, args.repo)
            m = f.create_mission(
                title=args.title or sd.name, kind=args.kind, seed=sd.name, repo=repo,
                risk=args.risk, priority=args.priority,
                notes="collect promote",
            )
            print(f"mission created: {m.id} ({m.status})")
            if args.run:
                _resolve_worker_override(cfg, args.worker)
                m2 = run_mission(f, m.id, confirm=args.confirm, iterations=args.iterations, cfg=cfg)
                print(f"status: {m2.status}")
        else:
            print("→ seed.md の「目的」「受け入れ基準」を人間が確定させてください")
        return

    if args.dismiss:
        collect_mod.dismiss(f, args.dismiss)
        print(f"dismissed: {args.dismiss}")
        return

    res = collect_mod.run_collect(f, cfg, source=args.source, dry_run=args.dry_run)
    print(f"collect {'(dry-run)' if res['dry_run'] else ''} — 新規ドラフト {res['created']} 件")
    for r in res["rows"]:
        print(f"  - {r['source']} [{r['type']}] {r['status']}  new={r['new']} dup={r['dup']}"
              + (f"  ({r['note']})" if r.get("note") else ""))
    new_rows = [r for r in collect_mod.list_drafts(f) if r["status"] == "new"]
    if new_rows and not res["dry_run"]:
        print(f"\n未処理ドラフト {len(new_rows)} 件 → 人間が確認して:")
        for r in new_rows[:8]:
            print(f"  {r['id']}  {r['source']:<12} {r['title']}")
        print("  promote: factory collect --promote <id> [--project X --run] / 却下: --dismiss <id>")


def cmd_listen(args) -> None:
    cfg, f = _require_cfg()
    from .listener import serve

    serve(
        f,
        port=args.port,
        bind=args.bind,
        project=args.project,
        auto_create=args.auto_create,
        auto_run=args.auto_run,
        cfg=cfg,
        secret=args.secret,
    )


def cmd_holdout(args) -> None:
    cfg, f = _require_cfg()
    from .holdout import generate, scenario_files

    m = f.get_mission(args.mission)
    if not m:
        raise SystemExit(f"mission not found: {args.mission}")
    hdir = f.holdout_dir_for(m.id)
    if args.action == "list":
        files = scenario_files(f, m.id)
        print(f"holdout: {hdir}")
        for p in files:
            print(f" - {Path(p).name}")
        if not files:
            print(" (シナリオなし)")
        return

    # regen: 既存シナリオを掃除して再生成 (陳腐化した仕様のリフレッシュ)
    if m.status == "done" and not args.force:
        raise SystemExit("done ミッションのシナリオ再生成は --force を付けてください")
    removed = 0
    if hdir.exists():
        for p in sorted(hdir.iterdir()):
            if p.is_file() and (p.name.startswith("SC-") or p.suffix in (".json", ".py")):
                p.unlink()
                removed += 1
    print(f"[holdout] {removed} ファイルを削除 → 再生成します")
    from .usage import Usage
    from .workers.router import Router

    ok = generate(f, m, Router(cfg, Usage(f.state_dir)))
    if not ok:
        print("[holdout] 生成に失敗しました (log/ワーカー出力を確認)", file=sys.stderr)
        sys.exit(1)
    for p in scenario_files(f, m.id):
        print(f" generated: {Path(p).name}")


def cmd_canary(args) -> None:
    cfg, f = _require_cfg()
    from .canary import run_canary

    result = run_canary(f, args.mission, cfg=cfg, fix=args.fix, run_complete=args.run_complete)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_pr(args) -> None:
    cfg, f = _require_cfg()
    from .pr import create_pr

    create_pr(f, args.mission, base=args.base)


# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="factory", description="社内ソフトウェアファクトリー オーケストレータ")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("init", help="ファクトリを初期化")
    sp.add_argument("root", nargs="?", default=".", help="ファクトリルート (デフォルト: カレント)")
    sp.add_argument("--demo", action="store_true", help="デモ (サンプル製品リポジトリ) も作る")
    sp.set_defaults(fn=cmd_init)

    sp = sub.add_parser("status", help="ミッション一覧")
    sp.add_argument("--watch", type=int, nargs="?", const=2, default=None,
                    help="指定秒ごとに再描画するダッシュボード (既定 2 秒)")
    sp.add_argument("--all", action="store_true", help="bench 専用ミッションも表示")
    sp.set_defaults(fn=cmd_status)

    sp = sub.add_parser("bench", help="社内 SWE ベンチ: ワーカー×タスクの cost/品質を比較")
    sp.add_argument("--tasks", default="", help="タスクID (カンマ区切り。省略で全タスク)")
    sp.add_argument("--workers", default="", help="ワーカー名 (カンマ区切り。省略で利用可能な全ワーカー)")
    sp.add_argument("--repeat", type=int, default=1, help="各組合せの反復回数 (分散を見る)")
    sp.add_argument("--max-iters", type=int, default=None, help="タスクの反復上限を上書き")
    sp.add_argument("--dry-run", action="store_true", help="実行せず planned matrix を表示")
    sp.add_argument("--init", action="store_true", help="サンプルタスクを .factory/bench/tasks/ に書き出す")
    sp.add_argument("--history", action="store_true", help="保存済みベンチ結果の一覧を表示")
    sp.set_defaults(fn=cmd_bench)

    sp = sub.add_parser("signals", help="取り込んだシグナルの一覧")
    sp.set_defaults(fn=cmd_signals)

    sp = sub.add_parser("compare-img", help="2枚の PNG の類似度を表示")
    sp.add_argument("image_a")
    sp.add_argument("image_b")
    sp.add_argument("--grid", type=int, default=64, help="比較グリッド幅 (既定 64)")
    sp.add_argument("--ratio", type=float, default=0.85, help="似ている/いないの閾値表示用")
    sp.set_defaults(fn=cmd_compare_img)

    sp = sub.add_parser("ingest", help="素材/URL を Seed 化し、指定すればミッションまで作成")
    sp.add_argument("src", help="ディレクトリ/ファイル/URL (http://, https://)")
    sp.add_argument("--name", default="", help="seed 名 (省略時は自動)")
    sp.add_argument("--title", default="", help="タイトル (URL 取込時の初期タイトルにも使用)")
    sp.add_argument("--kind", default="greenfield", choices=["greenfield", "update"])
    sp.add_argument("--project", default="")
    sp.add_argument("--repo", default="")
    sp.add_argument("--risk", default="low", choices=["low", "high"])
    sp.add_argument("--priority", type=int, default=5)
    sp.add_argument("--iterations", type=int, default=3)
    sp.add_argument("--pass-score", type=int, default=80)
    sp.add_argument("--model", default="")
    sp.add_argument("--notes", default="")
    sp.add_argument("--run", action="store_true", help="作成後に即実行")
    sp.add_argument("--worker", default="auto")
    sp.add_argument("--confirm", action="store_true")
    sp.set_defaults(fn=cmd_ingest)

    sp = sub.add_parser("listen", help="Webhook リスナー起動 (Seed 自動化)")
    sp.add_argument("--port", type=int, default=8787)
    sp.add_argument("--bind", default="127.0.0.1")
    sp.add_argument("--project", default="", help="--auto-create 時の既定プロジェクト")
    sp.add_argument("--auto-create", action="store_true", help="シグナル受信でミッションを自動作成")
    sp.add_argument("--auto-run", action="store_true", help="さらに自動実行 (--auto-create 必要)")
    sp.add_argument("--secret", default=None,
                    help="署名/Bearer 検証の共有シークレット (既定: env FACTORY_LISTEN_SECRET)。"
                         "ループバック以外で待ち受けるときは必須")
    sp.set_defaults(fn=cmd_listen)

    sp = sub.add_parser("collect", help="定時シグナル収集 → ドラフト化 (cron 向け)")
    sp.add_argument("--source", default="", help="このソースだけ収集する")
    sp.add_argument("--dry-run", action="store_true", help="ドラフトを作らず取得結果だけ確認")
    sp.add_argument("--list", action="store_true", help="収集せずソースとドラフトの一覧を表示")
    sp.add_argument("--promote", default="", metavar="DRAFT_ID", help="ドラフトを Seed 化する")
    sp.add_argument("--dismiss", default="", metavar="DRAFT_ID", help="ドラフトを却下する")
    sp.add_argument("--name", default="", help="promote 時の seed 名")
    sp.add_argument("--title", default="")
    sp.add_argument("--project", default="")
    sp.add_argument("--repo", default="")
    sp.add_argument("--kind", default="update", help="promote 時の mission kind")
    sp.add_argument("--risk", default="low")
    sp.add_argument("--priority", type=int, default=5)
    sp.add_argument("--iterations", type=int, default=3)
    sp.add_argument("--pass-score", type=int, default=80)
    sp.add_argument("--model", default="")
    sp.add_argument("--notes", default="")
    sp.add_argument("--worker", default="")
    sp.add_argument("--confirm", action="store_true")
    sp.add_argument("--run", action="store_true", help="promote+--project 時にミッション実行まで")
    sp.set_defaults(fn=cmd_collect)

    sp = sub.add_parser("holdout", help="holdout シナリオの list / regen")
    sp.add_argument("action", choices=["list", "regen"])
    sp.add_argument("mission")
    sp.add_argument("--force", action="store_true", help="done ミッションの regen を許可")
    sp.set_defaults(fn=cmd_holdout)

    sp = sub.add_parser("canary", help="マージ済みミッションを main で再検証")
    sp.add_argument("mission")
    sp.add_argument("--fix", action="store_true", help="失敗時に revert + 再ミッション作成")
    sp.add_argument("--run-complete", action="store_true", help="--fix で作った再ミッションを即実行")
    sp.set_defaults(fn=cmd_canary)

    sp = sub.add_parser("pr", help="gh で PR 作成 (なければ下書き)")
    sp.add_argument("mission")
    sp.add_argument("--base", default="", help="base ブランチ (既定: リポジトリ既定)")
    sp.set_defaults(fn=cmd_pr)

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
    sp.add_argument("--model", default="", help="ミッション単位のモデル上書き (例: sonnet)")
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
    sp.add_argument("--parallel", type=int, default=1, help="並列実行数 (既定 1)")
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
    sp.add_argument("--waste", action="store_true", help="無駄の兆候 (セッション分析) も表示")
    sp.set_defaults(fn=cmd_usage)

    sp = sub.add_parser("tick", help="ファクトリークロック 1巡 (取込→実行→出荷→カナリア)")
    sp.add_argument("--dry-run", action="store_true", help="実行せず計画だけ表示")
    sp.set_defaults(fn=cmd_tick)

    sp = sub.add_parser("supervisor", help="tick を常駐で回し続ける")
    sp.add_argument("--interval", type=int, default=300, help="tick 間隔秒 (既定 300)")
    sp.set_defaults(fn=cmd_supervisor)

    sp = sub.add_parser("worktrees", help="worktree の掃除 (完了/bench 分を対象, 失敗は残す)")
    sp.add_argument("--dry-run", action="store_true", help="削除せず対象だけ表示")
    sp.add_argument("--force-failed", action="store_true", help="失敗ミッションの worktree も削除")
    sp.set_defaults(fn=cmd_worktrees)

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
