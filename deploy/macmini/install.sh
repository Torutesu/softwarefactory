#!/bin/bash
# Mac mini 常駐セットアップ: supervisor (tick 常駐) と listener (Webhook) を launchd の
# LaunchAgent として登録する。macOS 標準の bash 3.2 で動くように書いている。
#
#   deploy/macmini/install.sh --root ~/factory [--interval 300] [--listen --port 8787 --bind 127.0.0.1]
#   deploy/macmini/install.sh --root ~/factory --print      # plist を表示するだけ (登録しない)
#   deploy/macmini/install.sh --uninstall
#
# LaunchAgent (ユーザーセッション) にするのは、claude / codex のサブスクログイン情報が
# ログインキーチェーンにあるため。再起動後も動かすには「自動ログイン」を有効にする。
set -euo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
ROOT=""
INTERVAL=300
LISTEN=0
PORT=8787
BIND=127.0.0.1
PRINT=0
UNINSTALL=0
LABEL_PREFIX="com.softwarefactory"
AGENTS="$HOME/Library/LaunchAgents"
LOGDIR="$HOME/Library/Logs/softwarefactory"

usage() { sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --root) ROOT="$2"; shift 2 ;;
    --interval) INTERVAL="$2"; shift 2 ;;
    --listen) LISTEN=1; shift ;;
    --port) PORT="$2"; shift 2 ;;
    --bind) BIND="$2"; shift 2 ;;
    --print) PRINT=1; shift ;;
    --uninstall) UNINSTALL=1; shift ;;
    -h|--help) usage 0 ;;
    *) echo "unknown option: $1" >&2; usage 1 ;;
  esac
done

uid="$(id -u)"

unload() {
  local label="$1"
  launchctl bootout "gui/$uid/$label" >/dev/null 2>&1 || true
  rm -f "$AGENTS/$label.plist"
}

if [ "$UNINSTALL" = 1 ]; then
  unload "$LABEL_PREFIX.supervisor"
  unload "$LABEL_PREFIX.listener"
  echo "LaunchAgent を削除しました (ログは $LOGDIR に残しています)"
  exit 0
fi

[ -n "$ROOT" ] || { echo "--root (factory init 済みのディレクトリ) を指定してください" >&2; exit 1; }
ROOT="$(cd "$ROOT" && pwd)"
[ -f "$ROOT/.factory/config.json" ] || { echo "$ROOT に .factory/config.json がありません (先に bin/factory init)" >&2; exit 1; }
if [ "$uid" = 0 ]; then
  # root では holdout の chmod 封印が効かない (root は権限を無視して読める)
  echo "root では実行しないでください (一般ユーザーで登録します)" >&2; exit 1
fi

# launchd の PATH は最小限なので、いまのシェルで見えているワーカー CLI の場所を引き継ぐ
path_dirs="/usr/bin:/bin:/usr/sbin:/sbin"
for c in claude codex opencode git python3; do
  p="$(command -v "$c" 2>/dev/null || true)"
  if [ -n "$p" ]; then
    d="$(dirname "$p")"
    case ":$path_dirs:" in *":$d:"*) ;; *) path_dirs="$d:$path_dirs" ;; esac
  elif [ "$c" = git ] || [ "$c" = python3 ]; then
    echo "$c が見つかりません" >&2; exit 1
  fi
done

# plist <label> <program args...> — 引数は <string> として並べる
plist() {
  local label="$1"; shift
  echo '<?xml version="1.0" encoding="UTF-8"?>'
  echo '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">'
  echo '<plist version="1.0"><dict>'
  echo "  <key>Label</key><string>$label</string>"
  echo '  <key>ProgramArguments</key><array>'
  for a in "$@"; do echo "    <string>$a</string>"; done
  echo '  </array>'
  echo "  <key>WorkingDirectory</key><string>$ROOT</string>"
  echo '  <key>EnvironmentVariables</key><dict>'
  echo "    <key>PATH</key><string>$path_dirs</string>"
  echo "    <key>HOME</key><string>$HOME</string>"
  echo '    <key>PYTHONUNBUFFERED</key><string>1</string>'
  echo '    <key>PYTHONDONTWRITEBYTECODE</key><string>1</string>'
  echo '  </dict>'
  echo '  <key>RunAtLoad</key><true/>'
  echo '  <key>KeepAlive</key><true/>'
  echo '  <key>ThrottleInterval</key><integer>60</integer>'
  # SIGTERM 後、子プロセスを止めてミッションを queued に戻すまでの猶予 (既定 20 秒)
  echo '  <key>ExitTimeOut</key><integer>45</integer>'
  echo '  <key>ProcessType</key><string>Background</string>'
  echo "  <key>StandardOutPath</key><string>$LOGDIR/${label##*.}.log</string>"
  echo "  <key>StandardErrorPath</key><string>$LOGDIR/${label##*.}.log</string>"
  echo '</dict></plist>'
}

# caffeinate -i: 実行中の idle スリープを防ぐ (sudo 不要)
sup_label="$LABEL_PREFIX.supervisor"
sup_plist="$(plist "$sup_label" /usr/bin/caffeinate -i "$REPO/bin/factory" supervisor --interval "$INTERVAL")"
lis_label="$LABEL_PREFIX.listener"
lis_plist="$(plist "$lis_label" "$REPO/deploy/macmini/run-listener.sh" --port "$PORT" --bind "$BIND")"

if [ "$PRINT" = 1 ]; then
  echo "$sup_plist"
  [ "$LISTEN" = 1 ] && echo "$lis_plist"
  exit 0
fi

mkdir -p "$AGENTS" "$LOGDIR"
install_one() {
  local label="$1" body="$2"
  unload "$label"
  printf '%s\n' "$body" > "$AGENTS/$label.plist"
  chmod 600 "$AGENTS/$label.plist"
  plutil -lint "$AGENTS/$label.plist" >/dev/null
  launchctl bootstrap "gui/$uid" "$AGENTS/$label.plist"
  echo "登録: $label  (log: $LOGDIR/${label##*.}.log)"
}

install_one "$sup_label" "$sup_plist"
if [ "$LISTEN" = 1 ]; then
  install_one "$lis_label" "$lis_plist"
else
  unload "$lis_label"
fi

cat <<MSG

次の確認:
  launchctl print gui/$uid/$sup_label | head -20
  tail -f $LOGDIR/supervisor.log
  $REPO/bin/factory worker ping           # (root=$ROOT で) サブスク CLI の生存確認
再起動後も動かすには: システム設定 > ユーザとグループ > 自動ログイン を有効にする
MSG
