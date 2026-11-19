#!/usr/bin/env bash
# clip-watcher: record a live Twitch stream, detect chat spikes, cut 9:16 clips, caption them.
# Usage: ./watch.sh <twitch_channel> [--min N] [--factor X] [--bucket S] [--model NAME]
set -euo pipefail
cd "$(dirname "$0")"

[[ $# -ge 1 && "$1" != -* ]] || { sed -n '2,3p' "$0" | sed 's/^# //'; exit 1; }

missing=()
for c in ffmpeg streamlink whisper python3; do command -v "$c" >/dev/null || missing+=("$c"); done
if ((${#missing[@]})); then
  echo "Missing: ${missing[*]}" >&2
  echo "Install: ffmpeg (apt/brew install ffmpeg), then: pip install streamlink openai-whisper" >&2
  exit 1
fi

mkdir -p rec clips
exec python3 -I clip_watcher.py "$@"
