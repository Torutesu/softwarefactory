#!/usr/bin/env bash
# 画面録画からフレームを切り出す (app-clone Skill 用)。
#
# 使い方:
#   extract_frames.sh <video> <out_dir> [--scene 0.08] [--interval 1] [--width 1280]
#   extract_frames.sh <video> <out_dir> --start 12.3 --end 13.1 [--fps 60]   # 区間の全フレーム (アニメーション解析)
#
# 出力:
#   <out_dir>/scene/scene-XXXX.png      画面変化 (遷移) を検出したフレーム
#   <out_dir>/interval/t-XXXX.png       一定間隔のフレーム
#   <out_dir>/range/f-XXXX.png          --start/--end 指定時の連番フレーム
#   <out_dir>/index.md                  フレームと動画内時刻の対応表
set -euo pipefail

usage() { sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 1; }
[ $# -ge 2 ] || usage

video=$1; out=$2; shift 2
scene=0.08; interval=1; width=1280; start=""; end=""; fps=""
while [ $# -gt 0 ]; do
  case "$1" in
    --scene) scene=$2; shift 2 ;;
    --interval) interval=$2; shift 2 ;;
    --width) width=$2; shift 2 ;;
    --start) start=$2; shift 2 ;;
    --end) end=$2; shift 2 ;;
    --fps) fps=$2; shift 2 ;;
    -h|--help) usage ;;
    *) echo "unknown option: $1" >&2; usage ;;
  esac
done

command -v ffmpeg >/dev/null || { echo "ffmpeg が必要です (brew install ffmpeg / apt-get install ffmpeg)" >&2; exit 2; }
[ -f "$video" ] || { echo "動画が見つかりません: $video" >&2; exit 2; }
mkdir -p "$out"

# 0 = 縮小しない (色・余白の計測用に等倍で欲しいとき)
if [ "$width" = "0" ]; then scale="null"; else scale="scale='min($width,iw)':-2"; fi

probe() { ffprobe -v error -select_streams v:0 -show_entries "stream=$1" -of csv=p=0 "$video" | head -1; }
info_res=$(probe width,height | tr ',' 'x')
info_fps=$(probe r_frame_rate)
info_dur=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$video" || echo "?")

index="$out/index.md"
{
  echo "# Frames: $(basename "$video")"
  echo
  echo "- 解像度: $info_res / フレームレート: $info_fps / 長さ: ${info_dur}s"
  echo
} > "$index"

# 時刻つき出力: showinfo の pts_time をファイル名順に対応付ける
extract() {  # $1=filter $2=outpattern $3=prefix
  local log
  log=$(mktemp)
  ffmpeg -hide_banner -loglevel info -nostdin -y "${seek[@]}" -i "$video" \
    -vf "$1,$scale,showinfo" -fps_mode vfr "$2" 2>"$log" || { cat "$log" >&2; rm -f "$log"; exit 3; }
  local times
  mapfile -t times < <(grep -o 'pts_time:[0-9.]*' "$log" | cut -d: -f2)
  rm -f "$log"
  local i=0 f
  for f in "$(dirname "$2")"/"$3"-*.png; do
    [ -e "$f" ] || continue
    local t=${times[$i]:-?}
    if [ -n "$start" ] && [ "$t" != "?" ]; then t=$(awk "BEGIN{printf \"%.3f\", $t+$start}"); fi
    echo "| \`${f#"$out"/}\` | ${t}s |" >> "$index"
    i=$((i+1))
  done
  echo "$i"
}

seek=()
if [ -n "$start" ]; then
  [ -n "$end" ] || { echo "--start には --end も指定してください" >&2; exit 1; }
  dur=$(awk "BEGIN{print $end-$start}")
  seek=(-ss "$start" -t "$dur")
  mkdir -p "$out/range"
  rate_filter="null"; [ -n "$fps" ] && rate_filter="fps=$fps"
  { echo "## 区間 ${start}s–${end}s"; echo; echo "| frame | time |"; echo "|---|---|"; } >> "$index"
  n=$(extract "$rate_filter" "$out/range/f-%04d.png" f)
  echo "range: $n frames -> $out/range"
else
  mkdir -p "$out/scene" "$out/interval"
  { echo "## シーン変化 (threshold=$scene)"; echo; echo "| frame | time |"; echo "|---|---|"; } >> "$index"
  # 先頭フレームは常に含める
  n1=$(extract "select='eq(n\,0)+gt(scene\,$scene)'" "$out/scene/scene-%04d.png" scene)
  { echo; echo "## 一定間隔 (${interval}s)"; echo; echo "| frame | time |"; echo "|---|---|"; } >> "$index"
  n2=$(extract "fps=1/$interval" "$out/interval/t-%04d.png" t)
  echo "scene: $n1 frames, interval: $n2 frames -> $out"
fi
echo "index: $index"
