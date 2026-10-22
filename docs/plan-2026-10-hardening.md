# 品質・セキュリティ強化計画 (2026-10)

全体テスト・監査 (セキュリティ / バグ・品質 / 機能ギャップの 3 系統) の結果と、実装・QA・本番反映までの計画。
ステータス列は実装の進捗に合わせて更新する。

## 0. 出発点 (main `d98c006` 時点)

| 指標 | 値 |
|---|---|
| テスト | 141 件 OK |
| 行カバレッジ | 69% (cli 24% / pr 0% / canary 43% / workers.claude_code 46% / opencode 54% / base 61% / listener 67%) |
| lint (ruff E9,F,B) | 25 件 (未定義名 `ProcResult`、不正エスケープ、未使用 import など) |

ゴール: 下記の Critical/High をすべて解消、テスト追加でカバレッジ 85% 以上、
`factory selftest` による e2e QA を CI と Mac mini 導入時に回せる状態で main に反映する。

## 1. セキュリティ

前提: 実装ワーカーは確認なしモードで動き、harness はワーカーが書いたコード (build/test/run) を
host 上で同じユーザー権限で実行する。したがって「ワーカー/そのコードが読めるもの・書けるもの」と
「外部入力 (webhook・収集・Seed) がどこまで届くか」が境界になる。

| ID | 重大度 | 内容 | 対策 | 状態 |
|---|---|---|---|---|
| S1 | Critical | ワーカーのコードが検証の **前に** holdout シナリオを書き換えられる (test コマンドで `../../../holdout/*/*.json` を改変 → 全シナリオ通過)。chmod の封印も同一 uid なら戻せる | dev 前に指紋を `.factory/holdout_manifest/` に記録。検証時は「ワーカーのコードを動かす前に」照合 → 0700 の一時ディレクトリへスナップショット → シナリオはそこから実行。検証中は holdout を封印し、事後にもう一度照合。不一致は `holdout_tamper` で停止 | 実装済み |
| S2 | High | 反復レポート (`missions/<id>/reports/run-*/iter-N.md`) に失敗シナリオのコマンドと出力が載る。worktree の親なのでワーカーが読める | 失敗詳細は `holdout/<id>/runs/` (dev 中は封印) にのみ書き、mission 側は ID/タイトル/判定のみ | 実装済み |
| S3 | High | harness (build/test/run/シナリオ) に `os.environ` が丸ごと渡る (API キー・`GH_TOKEN`・`FACTORY_LISTEN_SECRET` 等) | 名前で秘密を判定して外す `scrubbed_env`。必要なものだけ `harness_env_passthrough` で許可 | 実装済み |
| S4 | High | ワーカー CLI にも関係ない秘密が渡る (subscription でも `OPENROUTER_API_KEY`・`GH_TOKEN`・listener secret・`SSH_AUTH_SOCK`) | 既定で外し、CLI 種別ごとの必要鍵 (api_key 時) と `CLAUDE_CODE_OAUTH_TOKEN` (subscription) のみ通す。個別は `env_passthrough` | 実装済み |
| S5 | High | 本番構成で実ワーカーが枠切れ → `mock` にフォールバック → prompt 中の `mock-action` (shell) が実行される。webhook の title に埋めれば host でコマンド実行 | 実ワーカーが設定された構成では mock を候補から外す (`use_worker=mock` か `allow_mock_fallback: true` のときのみ) | 実装済み |
| S6 | High | 「マージ済み」「修復の親」を notes の部分文字列で判定。listener は `notes=f"auto-created from {source}"` に外部の `source` を入れるため、偽装でマージ済み扱い・修復深度の改変が可能 | `Mission.merged_utc/merged_tier/repair_of` の構造化フィールドへ。旧データは行頭一致の厳密 regex のみ互換。外部文字列は `one_line()` で 1 行化 | 実装済み |
| S7 | High | マージゲートで `.github/workflows/*`・`conftest.py`・`pyproject.toml`・`Makefile` 等が mechanical/scoped → 自動マージ → self-hosted runner で任意ワークフロー実行・テスト基盤の改変 | `protected_paths` (既定: CI/ビルド/テスト基盤) に触れたら sensitive。テストファイルの削除・バイナリ/巨大ファイルは behavioral 以上 | 予定 |
| S8 | Medium | listener: secret 無しのループバック運用でブラウザからの `text/plain` POST (CSRF) と DNS rebinding を受ける / `files` で `seed.md`・`mock/*` (答えキー) を差し替え可 / 深い JSON で 500 / 検証前に Seed を作る / ソケットにタイムアウト無し | `Content-Type: application/json` 必須、secret 無し時は Host をループバック名に限定、予約パス拒否、全入力検証の後に副作用、`RecursionError`→400、ソケット timeout | 予定 |
| S9 | Medium | mission id がパスに直結 (`repair-of:../../x` → `missions/../../x`) | `valid_mission_id` を `mission_dir`/`holdout_dir_for`/`get_mission` で強制 | 実装済み |
| S10 | Medium | manifest 未記録時は現状を自動で正とする (ワーカーが manifest を消すと改ざん後が正になる) | dev 開始後 (iteration≥1) は `unrecorded` を tamper 扱い。記録は dev 前と `holdout regen` のみ | 予定 |
| S11 | Low | `.factory/` (config・state・usage・listener.log) と holdout がデフォルト umask | `.factory/` と `holdout/` を 0700 で作成 | 予定 |

残るリスク (今回は文書化のみ): 同一 uid で動く限り、ワーカーは holdout を「読む」ことはできる
(検知は出力のパス出現と指紋照合)。完全な隔離は macOS `sandbox-exec` / 別ユーザー実行が必要
(ロードマップ: sandbox 項目)。

## 2. バグ (監査で再現確認済み)

| ID | 重大度 | 内容 | 対策 |
|---|---|---|---|
| B1 | High | `run_cmd` のタイムアウト時、`TimeoutExpired.stdout` が bytes のまま → 各ワーカーの parse と `_shell` が TypeError でミッションごと落ちる | bytes を decode |
| B2 | High | baseline の無い repo で `run-all --parallel` → baseline コミット二重化・unborn branch の worktree が再利用され永久に失敗 | repo 準備〜`worktree add` を repo 単位の flock で直列化。再利用時に `rev-parse HEAD` を確認 |
| B3 | High | squash merge の競合後 `merge --abort` が効かず main repo が `UU` のまま → 以後の merge/revert が全部失敗 (tick 自動出荷が詰まる) | 失敗時 `reset --merge`、merge 前に main repo の clean を必須に |
| B4 | High | 壊れたシナリオ JSON が黙って無視され `scenarios 0/0` で PASS (100 点) / 型違いで AttributeError / `generate()` 失敗を無視 | 読めないシナリオは失敗シナリオとして数える、型を強制、生成失敗で停止 |
| B5 | High | loop 途中の例外でミッションが `dev` のまま放置 (tick は queued しか拾わない) | `_run_locked` を try/except で `failed/crash` に。tick に孤児 (`spec/holdout/dev` かつロック可能) の回収ステップ |
| B6 | High | `expect_exit` が無視される (常に exit 0 を期待) | `code == expect_exit` |
| B7 | High | tick 要約を 480 文字で切って保存 → dashboard の needs_human が常に 0 | needs_human を別イベントとして切らずに保存 |
| B8 | High | usage の日別ファイルはローカル日付・window 判定は UTC 日付 → JST 等で 5h 窓の枠保護が効かない / 24h 超の窓を数え損ねる | 窓の範囲から読むべき日付ファイルを列挙 |
| B9 | Medium | `init --demo` が `__pycache__`/`*.pyc` をコミット → 並列ミッションのマージ競合 | copytree の ignore と `.gitignore` 生成 |
| B10 | Medium | 既存ファクトリで `init` を再実行すると config が初期化される | `--force` 無しでは拒否 |
| B11 | Medium | 壊れた `state.json` が黙って初期化される / 壊れた `mission.json` でミッションが消える | 退避 (`.corrupt-<ts>`) してから初期化、イベント記録 |
| B12 | Medium | `Mission.from_dict` が未知キーで TypeError (新旧バージョン混在で status/tick が落ちる) | dataclass のフィールドだけ採る |
| B13 | Medium | judge の next_signals を state に追記する箇所がロック外 | `_mutate_state` 経由 |
| B14 | Medium | `run_mission` がロック前に読んだ古い Mission で実行 | ロック内で読み直す |
| B15 | Medium | 並列ミッションのポート衝突 (固定式フォールバック + check-then-bind) | OS 採番 (port 0) |
| B16 | Medium | タイムアウトした build/test/ワーカーの孫プロセスが残る / health 待ち中の Ctrl-C でサービスが残る | `start_new_session` + `killpg`、finally の範囲を広げる |
| B17 | Medium | canary が無関係な後続コミットの破損でもミッションを revert/修復 / revert 失敗でも修復ミッションを作る / 古い canary worktree を再利用 | revert 成功時のみ reseed、canary worktree は毎回作り直し、`main_branch` を使う |
| B18 | Medium | ミッションの `--model` が全ワーカーに適用 → フォールバック先で不正モデル名 | 先頭 (dev の第一候補) のワーカー種別にだけ適用 |
| B19 | Medium | judge 失敗 (枠切れ・JSON 不正) で harness 結果だけで合格になる (fail-open) | `judge_required` (既定 true) で judge 不在は不合格・リトライ対象 |
| B20 | Medium | quota 判定の正規表現が stdout 全体 (LLM の普通の文章) にかかる | stderr と終了コード非 0 時のみ、かつ行頭/明示的なエラーパターン |
| B21 | Medium | `run_cmd` が FileNotFoundError しか捕まえない (E2BIG・PermissionError で落ちる) | `OSError` を捕まえて失敗結果に |
| B22–B28 | Low | `worker ping` の終了コード / `pr` の終了コードと IndexError / CLI の未処理例外 (collect・bench・ingest・壊れた config・autopilot_project) / collect のファイル名衝突 / PNG の不正入力 / `max_turns`・`main_branch` 未使用 / 失敗ミッションの merge | 個別に修正、`main()` で既知例外を exit 2 |

## 3. 追加機能 (価値/工数の順)

| ID | 機能 | 受け入れ条件 (mock で検証) | 工数 |
|---|---|---|---|
| F1 | 孤児ミッションの回収 (`step_recover` + `mission reset`) | `dev` のまま残ったミッションが次の tick で queued に戻り完走。ロック中は触らない。3 回連続で `failed/crash_loop` | S |
| F2 | supervisor / listener の graceful shutdown (SIGTERM) | SIGTERM で exit 0、実行中ミッションは反復の区切りで queued/shutdown、holdout は封印解除済み | S |
| F3 | config の検証 (`factory config check`) | 型・範囲・列挙・未知キー (did-you-mean)・worker 名の整合。エラーがあれば supervisor は起動しない | S |
| F4 | `factory doctor` | root 実行・CLI/ログイン・git・空き容量・holdout 権限・孤児・listener secret を検査し、0/1/2 で終了 | M |
| F5 | judge に実際の差分 (`git diff` 要約) を渡す | 変更ゼロで「完了」と言う worker → prompt に「差分なし」。自己申告は参考扱い | S |
| F6 | needs_human の重複排除と通知 | 同じエスカレーションは 1 回だけ通知、解消時に resolved 通知、`factory needs-human` で一覧 | S/M |
| F7 | `factory selftest` (e2e QA) | 一時ディレクトリで init→run→merge gate→merge→canary→tick 冪等性を mock で通し PASS 表を出す。stub CLI で実 CLI を呼ばない | S |
| F8 | 一時停止スイッチと config の毎 tick 再読込 | `.factory/PAUSE` で tick が何もしない、config 変更が次の tick で効く | S |

次段 (今回は計画のみ): spec のスコープと差分の突き合わせ、ログローテーション・保持期間、バックアップ、
マージ前の最新 main での再検証、ミッション DAG、プロジェクト別クォータ、枠の予測、バージョニング、
macOS sandbox。

## 4. テスト計画

- 監査のテストギャップ一覧を、上記の修正ごとに回帰テストとして追加 (各 B/S に最低 1 件)。
- 追加の重点: cli の全サブコマンドの異常入力、pr.py、canary の revert 分岐、各ワーカーの
  parse_output (正常・前置きログ・壊れた JSON・エラー)、Router のフォールバック、listener の入力検証、
  PNG の不正入力、usage の TZ 境界。
- 本物の CLI を呼ばないことを保証する stub (exit 97) を PATH 先頭に置いた全件実行を QA の必須項目にする。
- 目標: 行カバレッジ 85% 以上、ruff (E9,F,B) クリーン。

## 5. QA と本番反映

1. `python3 -m unittest discover -s tests -t .` 全件 (stub CLI 付き) と coverage レポート
2. `ruff check factory tests --select E9,F,B`
3. `factory selftest` (F7) — CI にも追加
4. CLI スモーク: すべてのサブコマンドを demo ファクトリで実行し、トレースバックが出ないこと
5. PR → レビュー → main へ squash merge (= 本番反映)。Mac mini 側は `git pull` 後に
   `factory doctor` → `deploy/macmini/install.sh` を再実行
