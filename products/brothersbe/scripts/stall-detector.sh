#!/bin/sh
# Thin launcher for the stall detector v2. The logic lives in
# tools/sbe_stall_detector.py (see docs/specs/2026-08-11-stall-detector-v2-design.md);
# this file only starts it detached with its diagnostics KEPT, never sent to
# /dev/null: the first-generation detector discarded stderr and reported a
# false stall all night on a find flag this host rejects.
#
# Usage: scripts/stall-detector.sh [repo-root] [extra detector flags...]
# Logs: <root>/.sbe/runtime/detector.log (stderr diagnostics + stdout alerts)
set -u
ROOT="${1:-$(cd "$(dirname "$0")/.." && pwd)}"
shift 2>/dev/null || true
LOGDIR="$ROOT/.sbe/runtime"
mkdir -p "$LOGDIR"
echo "stall-detector launcher: watching $ROOT, log at $LOGDIR/detector.log"
exec nohup python3 "$(dirname "$0")/../tools/sbe_stall_detector.py" \
    --root "$ROOT" "$@" >> "$LOGDIR/detector.log" 2>&1
