#!/bin/sh
# run_installed.sh: VN4c, the felt-surface proof at the INSTALLED hook level.
#
# Sibling of run_control.sh (read it first: this file copies its isolation,
# its ambient-variable unset block and its hand-built PreToolUse payload
# verbatim in shape). ONE deliberate difference, and it is the point of this
# unit:
#
#   run_control.sh installs through `claude plugin marketplace add` +
#   `claude plugin install`, and VN4b MEASURED that path serving a STALE
#   cached 3.4.4 bundle: a content change landed on a branch without a
#   version bump never reached the installed copy, so the run exercised old
#   code while believing it exercised new code.
#
#   This driver instead points BM_TOOLS at bundle/runtime/hooks/brothermode,
#   the mirrored installed layout inside this tree, whose freshness is
#   decided mechanically by `python3 scripts/bundle_runtime.py --check`
#   rather than by a cache key. Same file bytes the plugin ships, no cache
#   in between, no disk cost of a full install.
#
# Read-only against the product: nothing here writes under products/ or
# bundle/. Every mutable path lives under one mktemp WORK.
#
# No em or en dashes.
set -u

say() { printf '%s\n' "run-installed: $*"; }

HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../../.." && pwd)
INSTALLED="$ROOT/bundle/runtime/hooks/brothermode"
SOURCE="$ROOT/products/brothermode"

[ -f "$INSTALLED/tools/vault_recall_hook.py" ] || { say "FAIL: no installed hook at $INSTALLED"; exit 1; }

WORK=$(mktemp -d "${TMPDIR:-/tmp}/vn4c-installed.XXXXXX") || { say "FAIL: mktemp"; exit 1; }
say "WORK: $WORK"

HOME_DIR="$WORK/home"; CONFIG_DIR="$WORK/config"; TARGET="$WORK/target-repo"
RUN_DIR="$WORK/run"
mkdir -p "$HOME_DIR" "$CONFIG_DIR" "$TARGET" "$RUN_DIR"

case "$CONFIG_DIR" in
  "$WORK"/*) : ;;
  *) say "FAIL: CLAUDE_CONFIG_DIR ($CONFIG_DIR) is not inside the throwaway $WORK; refusing"; exit 1 ;;
esac

# Ambient contamination block, run_control.sh's own (2026-09-08): bm_vault.py
# reads BROTHERMODE_VAULT from the environment BEFORE the bound config file,
# and this machine's day to day shell carries it pointed at the real founder
# vault. Isolating HOME alone indexed 988 real notes once already.
unset BM_VAULT_ROOT BROTHERMODE_VAULT BROTHERSBE_VAULT BM_FENCE_MODE \
      BROTHERMODE_REGISTRIES BROTHERMODE_SESSION_CAP CLAUDE_PLUGIN_ROOT \
      BROTHER_PLUGIN_ROOT DOOR_MODEL_CMD MODEL_WORKER_CMD

export HOME="$HOME_DIR"
export CLAUDE_CONFIG_DIR="$CONFIG_DIR"
export BROTHER_CONFIG_DIR="$CONFIG_DIR"
export BM_HOOK_OUTCOMES="$WORK/hook-outcomes.jsonl"
export BROTHER_RUN_DIR="$RUN_DIR"
export GIT_CONFIG_GLOBAL=/dev/null
export GIT_CONFIG_NOSYSTEM=1

say "sandbox HOME:             $HOME"
say "sandbox CLAUDE_CONFIG:    $CLAUDE_CONFIG_DIR"
say "sandbox BROTHER_RUN_DIR:  $BROTHER_RUN_DIR"
say "sandbox BM_HOOK_OUTCOMES: $BM_HOOK_OUTCOMES"
say "installed product root:   $INSTALLED"

cp -R "$HERE/target-repo-seed/." "$TARGET/"
(
  cd "$TARGET" || exit 1
  git init -q -b main
  git config user.email "vn4c-felt-surface@example.invalid"
  git config user.name "vn4c-felt-surface"
  git add -A
  git -c commit.gpgsign=false commit -q -m "seed"
) || { say "FAIL: could not seed target repo"; exit 1; }
say "PASS: target-seed"

VAULT="$WORK/vault"
cp -R "$HERE/vault-seed" "$VAULT"

python3 "$INSTALLED/scripts/setup.py" --vault "$VAULT" --mode plugin --accept-notice \
  >"$WORK/setup.log" 2>&1
say "consent-setup exit: $?  (log $WORK/setup.log)"

python3 "$INSTALLED/tools/bm_vault_cli.py" bind "$VAULT" >"$WORK/bind.log" 2>&1
say "vault-bind exit: $?  (log $WORK/bind.log)"

( cd "$TARGET" && python3 "$INSTALLED/tools/bm_vault.py" refresh ) >"$WORK/refresh.log" 2>&1
say "index-refresh exit: $?"
cat "$WORK/refresh.log"

PAYLOAD="$WORK/probe-payload"
python3 -c "
import json, sys
t = sys.argv[1]
print(json.dumps({'session_id': 'SESSION_PLACEHOLDER', 'cwd': t, 'tool_name': 'Edit',
                  'tool_input': {'file_path': t + '/app/ledger.py'}}))
" "$TARGET" >"$PAYLOAD.template"

probe() {
  label="$1"; product_root="$2"; session="$3"
  sed "s/SESSION_PLACEHOLDER/$session/" "$PAYLOAD.template" >"$WORK/payload-$label.json"
  # cd "$TARGET" FIRST: run_control.sh's own measured cwd fix. bm_vault.py's
  # evidence check resolves a path: locator against the CALLING process's cwd.
  ( cd "$TARGET" && BM_TOOLS="$product_root" python3 "$product_root/tools/vault_recall_hook.py" check \
      <"$WORK/payload-$label.json" ) >"$WORK/hook-$label.stdout.json" 2>"$WORK/hook-$label.stderr.log"
  say "hook-probe $label exit=$?"
  echo "-- hook $label stderr (the human channel) --"
  cat "$WORK/hook-$label.stderr.log"
  echo "-- hook $label additionalContext (the model channel) --"
  python3 -c "
import json, sys
raw = open(sys.argv[1], encoding='utf-8').read().strip()
if not raw:
    print('(empty stdout: the hook emitted no additionalContext)')
else:
    print(json.loads(raw)['hookSpecificOutput']['additionalContext'])
" "$WORK/hook-$label.stdout.json"
}

say "=== PROBE 1: the INSTALLED copy (bundle/runtime/hooks/brothermode) ==="
probe installed "$INSTALLED" vn4c-installed

say "=== PROBE 2: the SOURCE copy (products/brothermode), same payload, same run dir ==="
probe source "$SOURCE" vn4c-source

say "=== JOURNAL after both probes ==="
if [ -f "$RUN_DIR/journal.jsonl" ]; then
  cat "$RUN_DIR/journal.jsonl"
else
  say "NO journal.jsonl at $RUN_DIR"
fi

say "=== TOOL PATH: bm_vault.py check --limit 10 (above the hook's hardcoded limit 2) ==="
( cd "$TARGET" && python3 "$INSTALLED/tools/bm_vault.py" check --paths ledger.py --limit 10 ) \
  >"$WORK/tool-check.log" 2>&1
say "tool-check exit: $?"
cat "$WORK/tool-check.log"

say "=== WRITE NOTICE: intake capture into a writable vault ==="
( cd "$TARGET" && python3 "$INSTALLED/tools/bm_vault_intake.py" capture --vault "$VAULT" \
    --by vn4c-installed --title "VN4c write notice probe" \
    "GAUNTLET-POISON-LL3-FIXTURE-DO-NOT-COPY-INTO-A-REAL-VAULT vn4c probe: close_month in app/ledger.py must be called with the period already closed." ) \
  >"$WORK/write-ok.log" 2>&1
say "capture (writable) exit: $?"
cat "$WORK/write-ok.log"

say "=== WRITE NOTICE FAILURE PATH: the same capture into a read-only vault ==="
RO="$WORK/vault-readonly"
cp -R "$HERE/vault-seed" "$RO"
mkdir -p "$RO/00-Inbox"
chmod -R a-w "$RO"
( cd "$TARGET" && python3 "$INSTALLED/tools/bm_vault_intake.py" capture --vault "$RO" \
    --by vn4c-installed --title "VN4c write notice failure probe" \
    "GAUNTLET-POISON-LL3-FIXTURE-DO-NOT-COPY-INTO-A-REAL-VAULT vn4c failure probe: this write must not persist." ) \
  >"$WORK/write-fail.log" 2>&1
say "capture (read-only) exit: $?"
cat "$WORK/write-fail.log"
chmod -R u+w "$RO"

say "WORK (kept): $WORK"
exit 0
