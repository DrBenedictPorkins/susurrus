#!/usr/bin/env bash
# Capture audio from BlackHole and stream raw 16kHz mono float32 PCM
# over TCP to the transcription server.
#
# Usage:
#   ./capture.sh [host] [port]
#   CAPTURE_DEVICE="BlackHole 16ch" ./capture.sh
#
# Discover the device name with:
#   ffmpeg -f avfoundation -list_devices true -i "" 2>&1 | grep -i blackhole
set -euo pipefail

HOST="${1:-localhost}"
PORT="${2:-8000}"
DEVICE="${CAPTURE_DEVICE:-BlackHole 2ch}"

echo "[capture] device='${DEVICE}'  →  ${HOST}:${PORT}"

exec ffmpeg \
    -hide_banner -loglevel warning \
    -fflags nobuffer -flags low_delay \
    -f avfoundation -audio_buffer_size 50 \
    -i ":${DEVICE}" \
    -ac 1 -ar 16000 -f f32le \
    "tcp://${HOST}:${PORT}"
