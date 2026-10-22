# Mac mini で回す (常駐ファクトリー + CI runner)

1 台の Mac mini に「ファクトリー本体 (supervisor / listener)」と「GitHub Actions の
self-hosted runner」を同居させる構成。クラウドの従量課金を次の 3 点で外す:

| 費目 | クラウド既定 | Mac mini 構成 |
|---|---|---|
| LLM 呼出 | API キーの従量課金 | `auth: "subscription"` (既定) で Claude Pro/Max・ChatGPT のログイン済み枠を使う。枠は `daily_call_limit` / `window_call_limit` で保護 |
| CI | GitHub ホスト runner の分課金 (private repo) | self-hosted runner (`.github/workflows/ci.yml` の既定 `[self-hosted, macOS, factory]`) |
| 常駐 | VM / コンテナの時間課金 | launchd の LaunchAgent。電気代のみ |

加えて、オーケストレータ側で呼出回数自体を減らす設定が既定で入っている:

- `judge_on_harness_fail: false` — build/test/シナリオが落ちた反復は合格し得ないので judge を呼ばない
- `noprogress_iterations: 2` — 変化のない反復を続けて枠を空費しない
- `require_reviewable: true` — 検証手段のないミッションは dev に入らない
- CI: docs/Markdown だけの変更では走らない (`paths-ignore`)、同一ブランチの古い実行は取り消す (`concurrency`)

## 1. 前提

- 一般ユーザーで運用する (root だと holdout の権限封印が効かない)。
- `git` / `python3` (3.9 以上。Xcode Command Line Tools 同梱で可)
- ワーカー CLI をインストールしてログイン: `claude login` / `codex login` (使うものだけ)
- スリープ対策: supervisor は `caffeinate -i` 経由で起動するので実行中はスリープしない。
  再起動・停電後も自動で戻すには:
  - システム設定 > ユーザとグループ > **自動ログイン** を有効 (LaunchAgent はログイン後に起動し、
    サブスク CLI のログイン情報はログインキーチェーンにあるため)
  - システム設定 > エネルギー > **停電後に自動的に起動** を有効

## 2. ファクトリー本体を常駐させる

```bash
git clone https://github.com/Torutesu/softwarefactory ~/softwarefactory
~/softwarefactory/bin/factory selftest          # 配線の e2e QA (mock のみ・サブスク枠は使わない)
~/softwarefactory/bin/factory init ~/factory
cd ~/factory && ~/softwarefactory/bin/factory doctor   # root/CLI/ログイン/git/空き容量/config を診断

# supervisor (tick を 300 秒ごと) を LaunchAgent に登録
~/softwarefactory/deploy/macmini/install.sh --root ~/factory --interval 300

# 内容だけ確認したいとき (登録しない)
~/softwarefactory/deploy/macmini/install.sh --root ~/factory --listen --print
```

- ログ: `~/Library/Logs/softwarefactory/supervisor.log`
- 状態: `launchctl print gui/$(id -u)/com.softwarefactory.supervisor`
- 停止・削除: `deploy/macmini/install.sh --uninstall`
- launchd の PATH は最小限なので、install 時にシェルで見えている `claude` / `codex` /
  `opencode` / `git` / `python3` のディレクトリを plist に書き込む。CLI を入れ直したら
  install をもう一度実行する。
- 自律度は `.factory/config.json` の `autonomy_level` で段階的に上げる (README 参照)。
  config は毎 tick 読み直すので再起動は不要。検証エラーのある config は無視して直前の設定で続く
- install.sh は登録前に `factory doctor` を実行し、エラー (exit 2) なら登録しない
- 緊急停止: `touch ~/factory/.factory/PAUSE` (tick が何もしなくなる)。再開は削除するだけ
- 停止 (`launchctl bootout` / 再起動 / ログアウト) は SIGTERM。実行中のワーカーと build/test は
  その場で止め、途中のミッションは queued に戻る (次の起動で再実行)。電源断で残ったものも
  次の tick が回収する (`halt_reason=recovered`)

### 更新 (本番反映後の Mac mini 側)

```bash
cd ~/softwarefactory && git pull
bin/factory selftest && (cd ~/factory && ~/softwarefactory/bin/factory doctor)
deploy/macmini/install.sh --root ~/factory   # plist の更新と再起動 (実行中のミッションは queued に戻る)
```

### Webhook (listener) も常駐させる場合

共有シークレットはログインキーチェーンに置く (plist に平文で書かない):

```bash
security add-generic-password -a "$USER" -s softwarefactory-listen -w "$(openssl rand -hex 32)"
~/softwarefactory/deploy/macmini/install.sh --root ~/factory --listen --port 8787 --bind 127.0.0.1
```

- 送信側は `Authorization: Bearer <secret>` か、GitHub 互換の
  `X-Hub-Signature-256: sha256=<HMAC-SHA256(secret, body)>` を付ける。
- 外部から受けるときも Mac mini のポートを直接開けず、Tailscale / Cloudflare Tunnel 等の
  トンネル越しに `127.0.0.1` へ流すのが安全。`--bind` をループバック以外にする場合は
  secret が必須 (未設定だと起動しない)。

## 3. CI の self-hosted runner を登録する

1. GitHub のリポジトリ > Settings > Actions > Runners > **New self-hosted runner** (macOS, ARM64)
   の手順でダウンロード・`./config.sh` を実行。ラベルに **`factory`** を追加する
   (ワークフローは `[self-hosted, macOS, factory]` を要求する)。
2. 常駐化: runner ディレクトリで `./svc.sh install && ./svc.sh start` (LaunchAgent として登録される)。
3. 動作確認: Actions タブから `ci` を **Run workflow** (workflow_dispatch)。

注意:

- CI は失敗するスタブを `claude` / `codex` / `opencode` として PATH の先頭に置く。テストが
  誤って本物を呼んでもサブスク枠は減らない (テストは mock ワーカーで完結している)。
- フォークからの PR は self-hosted では実行しない (`if:` で除外)。runner 上のログイン情報と
  ファイルを第三者のコードから守るため。リポジトリを public にする場合も同じ設定で良い。
- runner を止めている間は、リポジトリ変数 `CI_RUNS_ON` に `"ubuntu-latest"` (引用符込みの
  JSON) を入れると GitHub ホストに戻る。`["self-hosted","macOS","factory"]` 等の配列も指定可。
- 同じ Mac mini で factory のミッション実行と CI が重なると CPU を取り合う。`tick_concurrency`
  (既定 2) はメモリ 16GB なら 2、24GB 以上なら 3 程度が目安。
