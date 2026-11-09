#!/usr/bin/env bash
# 代替フォント Outfit (SIL Open Font License) を取得して Resources/Fonts に置く。
# 無くてもアプリは動く (システムの丸ゴシック系にフォールバック)。
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p Resources/Fonts
curl -fL -o Resources/Fonts/Outfit-Variable.ttf "https://github.com/google/fonts/raw/main/ofl/outfit/Outfit%5Bwght%5D.ttf"
curl -fL -o Resources/Fonts/OFL.txt "https://github.com/google/fonts/raw/main/ofl/outfit/OFL.txt"
echo "OK: Resources/Fonts/Outfit-Variable.ttf"
