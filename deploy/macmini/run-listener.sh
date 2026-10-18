#!/bin/bash
# launchd から listener を起動するラッパー。共有シークレットは plist に平文で置かず、
# ログインキーチェーンから読む:
#   security add-generic-password -a "$USER" -s softwarefactory-listen -w '<secret>'
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
if [ -z "${FACTORY_LISTEN_SECRET:-}" ]; then
  FACTORY_LISTEN_SECRET="$(security find-generic-password -a "$USER" -s softwarefactory-listen -w 2>/dev/null || true)"
fi
export FACTORY_LISTEN_SECRET
exec "$REPO/bin/factory" listen "$@"
