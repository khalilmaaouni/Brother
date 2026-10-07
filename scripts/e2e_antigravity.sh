#!/bin/sh
# L1b.7 run and write log entry point.
#
# Default mode is --sandbox; --real is run once by a collaborator outside
# the repository. Both write the same JSON Lines format with the
# environment field named per entry. Calls L1b.1 through L1b.6 through
# run_e2e.run_sandbox and run_e2e.write_log.

set -eu

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
PLUGIN="$ROOT/bundle/.antigravity-plugin"
MODE="sandbox"
LOG="${BROTHER_E2E_LOG:-}"

while [ "$#" -gt 0 ]; do
  case "$1" in
    --sandbox) MODE="sandbox"; shift ;;
    --real) MODE="real"; shift ;;
    --plugin) PLUGIN="$2"; shift 2 ;;
    --log) LOG="$2"; shift 2 ;;
    --help|-h)
      echo "usage: e2e_antigravity.sh [--sandbox|--real] [--plugin PATH] [--log PATH]"
      exit 0
      ;;
    *)
      echo "no_data: unknown argument $1" 1>&2
      exit 2
      ;;
  esac
done

if [ ! -d "$PLUGIN" ]; then
  echo "no_data: plugin path not found: $PLUGIN" 1>&2
  exit 2
fi

if [ -z "$LOG" ]; then
  LOG="$ROOT/docs/architecture/ANTIGRAVITY-E2E-TEST-LOG-run.jsonl"
fi

cd "$ROOT"
PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}" exec python3 -B -c '
import json, sys
from tests.e2e.antigravity import run_e2e
plugin, log_path, mode = sys.argv[1], sys.argv[2], sys.argv[3]
# Raw bytes live beside the log, so the log verifies wherever it is checked
# out; a run that fired no event writes nothing, so it cannot overwrite a
# recorded log with an empty one.
result = run_e2e.run_sandbox(plugin, environment=mode, run_dir=log_path + ".raw")
if result.get("events"):
    run_e2e.write_log(result, log_path)
sys.stdout.write(json.dumps({"environment": result.get("environment"), "ok": bool(result.get("ok")), "event_count": len(result.get("events", [])), "log": log_path if result.get("events") else None, "reason": result.get("reason", "")}) + "\n")
sys.exit(0 if result.get("ok") else 1)
' "$PLUGIN" "$LOG" "$MODE"
