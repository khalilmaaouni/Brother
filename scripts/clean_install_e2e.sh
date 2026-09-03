#!/bin/sh
# clean_install_e2e.sh: P0.5, the clean-install proof end to end.
#
# THE QUESTION THIS ANSWERS, and none of the other checks do: does a machine
# that has never seen Brother get a VERIFIED, INTEGRATED result from one
# plain outcome sentence, using only what `claude plugin install brother`
# actually delivers? bundle-install-smoke.sh proves the install RESOLVES;
# test_bundle_runtime.py proves the packaged launcher RUNS from a hand-copied
# runtime directory. Neither drives a real install, against a real target
# repository, through a launcher resolved from the real plugin cache, with no
# Brother checkout anywhere near either.
#
# Mirrors bundle-install-smoke.sh's own throwaway CLAUDE_CONFIG_DIR mechanism,
# and adds a throwaway HOME beside it (bundle-install-smoke.sh never needed
# one; this script's launcher-default-runs-root and the sibling brothermode
# tools/ resolution both read HOME, so proving a clean machine means HOME is
# clean too).
#
# Default mode is HERMETIC: a stub decomposer and a stub worker (the same
# DOOR_MODEL_CMD / MODEL_WORKER_CMD seam scripts/test_brother_run.py and
# scripts/product_acceptance.py already use), so this proves the SPINE
# without spending a real model call. `claude plugin install` itself still
# reaches the network for brothermode and brothersbe, since that resolution
# is the exact thing being proven; there is no local-only substitute for it.
#
# --live drops the stubs so the launcher falls back to its own environment
# defaults (the real `claude` CLI), for a founder-run proof.
#
# Exit 0: every ledger line is PASS or NO-DATA. Exit 1: at least one FAIL.
# Exit 2 BLOCKED: no claude binary, matching bundle-install-smoke.sh's own
# contract (a BLOCKED exit is not a pass).
# No em or en dashes.
set -u

say() { printf '%s\n' "clean-install-e2e: $*"; }

command -v claude >/dev/null 2>&1 || {
  say "BLOCKED: no claude binary on PATH; this proof needs a real client"
  exit 2
}

LIVE=0
[ "${1:-}" = "--live" ] && LIVE=1

REAL_HOME="$HOME"
ROOT=$(cd "$(dirname "$0")/.." && pwd)
SELF=$(cd "$(dirname "$0")" && pwd)/$(basename "$0")

WORK=$(mktemp -d "${TMPDIR:-/tmp}/brother-clean-install-e2e.XXXXXX") || {
  say "FAIL: mktemp"
  exit 1
}
trap 'rm -rf "$WORK"' EXIT

LEDGER="$WORK/ledger.txt"
: > "$LEDGER"
ledger() { printf '%s\n' "$1" >>"$LEDGER"; }

finish() {
  echo
  say "ledger"
  cat "$LEDGER"
  pass=$(grep -c '^PASS' "$LEDGER")
  fail=$(grep -c '^FAIL' "$LEDGER")
  nodata=$(grep -c '^NO-DATA' "$LEDGER")
  echo
  say "$pass pass, $fail fail, $nodata no-data"
  if [ "$fail" -gt 0 ]; then
    exit 1
  fi
  exit 0
}

HOME_DIR="$WORK/home"
CONFIG_DIR="$WORK/config"
TARGET="$WORK/target-repo"
mkdir -p "$HOME_DIR" "$CONFIG_DIR" "$TARGET"

# NEVER TOUCH THE REAL ~/.claude. Asserted before any install command runs,
# not assumed from having typed the right variable above.
case "$CONFIG_DIR" in
  "$WORK"/*) : ;;
  *)
    say "FAIL: CLAUDE_CONFIG_DIR ($CONFIG_DIR) is not inside the throwaway $WORK; refusing"
    exit 1
    ;;
esac
# LIVE MODE CARRIES THE USER'S CLI SESSION. A truly logged-out HOME makes
# the claude CLI print onboarding text instead of answers, which reached the
# door as non-JSON three times on the first live run. A real fresh machine
# is logged in by its user before Brother runs, so live mode models exactly
# that: the CLI's root config is copied into the sandbox HOME; plugins and
# everything else stay isolated. Hermetic mode stays fully logged out.
if [ "$LIVE" -eq 1 ] && [ -f "$REAL_HOME/.claude.json" ]; then
  cp "$REAL_HOME/.claude.json" "$HOME_DIR/.claude.json"
fi
export HOME="$HOME_DIR"
export CLAUDE_CONFIG_DIR="$CONFIG_DIR"

say "sandbox HOME:          $HOME"
say "sandbox CLAUDE_CONFIG: $CLAUDE_CONFIG_DIR"
say "target repo:           $TARGET"
say "mode:                  $([ "$LIVE" -eq 1 ] && echo live || echo hermetic-stubs)"

# the fresh target repository with a seed commit
(
  cd "$TARGET" || exit 1
  git init -q -b main
  git config user.email "clean-install-e2e@example.invalid"
  git config user.name "clean-install-e2e"
  printf 'seed\n' >seed.txt
  git add -A
  git commit -q -m "seed"
) || {
  say "FAIL: could not seed the throwaway target repository"
  exit 1
}

# ---------------------------------------------------------------------------
# INSTALL, exactly the mechanism scripts/bundle-install-smoke.sh already
# proves, reused rather than reinvented.
# ---------------------------------------------------------------------------
claude plugin marketplace add "$ROOT" >"$WORK/add.log" 2>&1
if [ $? -ne 0 ] || ! grep -q "Successfully added marketplace" "$WORK/add.log"; then
  cat "$WORK/add.log"
  ledger "FAIL   marketplace-add: claude plugin marketplace add did not succeed"
  finish
fi
ledger "PASS   marketplace-add"

claude plugin install brother@brother -y >"$WORK/install.log" 2>&1
if [ $? -ne 0 ] || ! grep -q "Successfully installed plugin" "$WORK/install.log"; then
  cat "$WORK/install.log"
  ledger "FAIL   bundle-install: claude plugin install brother@brother did not succeed"
  finish
fi
ledger "PASS   bundle-install"

# ---------------------------------------------------------------------------
# RESOLVE THE INSTALLED LAUNCHER, by the manifest, never a typed version.
# ---------------------------------------------------------------------------
WANT_VERSION=$(python3 -c "
import json, sys
print(json.load(open(sys.argv[1]))['version'])
" "$ROOT/bundle/.claude-plugin/plugin.json") || {
  ledger "FAIL   launcher-resolve: could not read the bundle's own promised version from bundle/.claude-plugin/plugin.json"
  finish
}

LAUNCHER_HITS=$(find "$CLAUDE_CONFIG_DIR/plugins/cache" -path "*/brother/*/runtime/brother-run" -type f 2>/dev/null)
LAUNCHER_COUNT=$(printf '%s\n' "$LAUNCHER_HITS" | grep -c .)
if [ "$LAUNCHER_COUNT" -ne 1 ]; then
  ledger "FAIL   launcher-resolve: expected exactly one installed launcher under \$CLAUDE_CONFIG_DIR/plugins/cache/*/brother/*/runtime/brother-run, found $LAUNCHER_COUNT"
  finish
fi
LAUNCHER=$(printf '%s\n' "$LAUNCHER_HITS" | head -1)

case "$LAUNCHER" in
  */"$WANT_VERSION"/runtime/brother-run) ;;
  *)
    ledger "FAIL   launcher-resolve: resolved launcher $LAUNCHER does not carry the manifest's promised version $WANT_VERSION"
    finish
    ;;
esac
ledger "PASS   launcher-resolve: $LAUNCHER (version $WANT_VERSION)"

# A FORCED BAD STATE, for this script's own test to drive: never fired by an
# ordinary run, only by scripts/test_clean_install_e2e.py sabotaging a real
# install to prove the ledger names the launcher rather than raising a stack
# trace.
if [ "${CLEAN_INSTALL_E2E_SABOTAGE:-}" = "delete-launcher" ]; then
  say "sabotage: deleting the installed launcher for a forced-bad-state proof"
  rm -f "$LAUNCHER"
fi
if [ ! -f "$LAUNCHER" ]; then
  ledger "FAIL   launcher-missing: the resolved launcher $LAUNCHER is not present; a clean install must ship a runnable launcher"
  finish
fi

# ---------------------------------------------------------------------------
# STUB THE MODEL SEAM (unless --live), the same DOOR_MODEL_CMD /
# MODEL_WORKER_CMD contract scripts/test_brother_run.py already exercises.
# ---------------------------------------------------------------------------
if [ "$LIVE" -eq 0 ]; then
  cat >"$WORK/decomposer.py" <<'PYEOF'
import json, sys
sys.stdin.read()
print(json.dumps([
    {"id": "CIE1", "objective": "prove the clean install integrates one unit",
     "done_check": "test -f cie1.txt", "writes": ["cie1.txt"], "deps": []},
]))
PYEOF
  cat >"$WORK/writer_model.py" <<'PYEOF'
import re, sys
prompt = sys.argv[-1] if len(sys.argv) > 1 else ""
m = re.search(r"Declared write scope: ([^\n]+)", prompt)
for path in (p.strip() for p in (m.group(1).split(",") if m else [])):
    if path:
        with open(path, "w") as fh:
            fh.write("written by the clean-install-e2e stub model\n")
print("stub model wrote: %s" % (m.group(1) if m else "(nothing declared)"))
PYEOF
  export DOOR_MODEL_CMD="$(command -v python3) $WORK/decomposer.py"
  export MODEL_WORKER_CMD="$(command -v python3) $WORK/writer_model.py"
  OUTCOME="a file proving the clean install integrated one unit exists"
else
  OUTCOME="${CLEAN_INSTALL_OUTCOME:-a file proving the clean install integrated one unit exists, through the real model}"
fi

# The loop's worker/verify/repair modules ship inside the INSTALLED
# brothermode plugin's own tools/ directory (a real dependency this install
# just pulled, never a developer's sibling checkout). loop_bridge.py's own
# BROTHER_RUNTIME_ROOT override exists exactly for a cache layout it does
# not otherwise guess (a versioned directory under this session's own
# CLAUDE_CONFIG_DIR); resolved by the manifest here too, never a typed
# version.
BM_TOOLS=$(find "$CLAUDE_CONFIG_DIR/plugins/cache" -maxdepth 4 -path "*/brothermode/*/tools" -type d 2>/dev/null | head -1)
if [ -n "$BM_TOOLS" ]; then
  export BROTHER_RUNTIME_ROOT
  BROTHER_RUNTIME_ROOT=$(dirname "$BM_TOOLS")
  ledger "PASS   runtime-root-resolve: $BROTHER_RUNTIME_ROOT"
else
  ledger "FAIL   runtime-root-resolve: no installed brothermode tools/ directory found under the plugin cache; the loop cannot run a worker without it"
fi

# ---------------------------------------------------------------------------
# THE RUN. Exactly one external command produces the delivery: the resolved
# launcher. This script never names door.py, loop_bridge.py, model_worker.py
# or any other internal engine module as something IT runs directly; that
# is the launcher's job once it is installed, not this proof's.
# ---------------------------------------------------------------------------
RUN_LOG="$WORK/run.log"
python3 "$LAUNCHER" "$OUTCOME" --cwd "$TARGET" >"$RUN_LOG" 2>&1
RUN_EXIT=$?
cat "$RUN_LOG"

if [ "$RUN_EXIT" -eq 0 ]; then
  ledger "PASS   exit-0"
else
  ledger "FAIL   exit-0: the launcher exited $RUN_EXIT"
fi

# The delivery report lists each integrated unit on its own line with the
# done_check that verified it (since 2026-08-31): "    CIE1  verified by: ...".
# Match that per-unit line, not the count header, which no longer carries the
# ids inline.
INTEGRATED_LINE=$(grep -E "^ +CIE1 +verified by:" "$RUN_LOG" | tail -1)
if [ -n "$INTEGRATED_LINE" ]; then
  ledger "PASS   delivery-report-names-units:$INTEGRATED_LINE"
else
  COUNT_LINE=$(grep "integrated ([0-9]*):" "$RUN_LOG" | tail -1)
  ledger "FAIL   delivery-report-names-units: the delivery report never named CIE1 as integrated, ${COUNT_LINE:-no integrated line in the launcher output}"
fi

GITLOG=$(cd "$TARGET" && git log --oneline)
case "$GITLOG" in
  *"Brother integrated CIE1 from lane/CIE1"*)
    ledger "PASS   git-log-has-units: the target repo's own git log carries CIE1's integration"
    ;;
  *)
    ledger "FAIL   git-log-has-units: the target repo's git log does not show CIE1 integrated -- $GITLOG"
    ;;
esac

# ---------------------------------------------------------------------------
# NO BROTHER CHECKOUT ANYWHERE THE RUN READS. Enumerated from
# bundle/runtime/RUNTIME-MANIFEST.json (the same closure bundle_runtime.py
# computes), never a hand-typed list, so a file added to the engine later is
# covered here without anyone updating this script.
# ---------------------------------------------------------------------------
MANIFEST_NAMES=$(python3 -c "
import json, sys
d = json.load(open(sys.argv[1]))
print(' '.join(f['path'] for f in d['files'] if f['path'].endswith('.py')))
" "$ROOT/bundle/runtime/RUNTIME-MANIFEST.json")
LEAKED=""
for name in $MANIFEST_NAMES; do
  hit=$(find "$TARGET" "$HOME_DIR" -name "$name" 2>/dev/null)
  [ -n "$hit" ] && LEAKED="$LEAKED $name"
done
if [ -n "$LEAKED" ]; then
  ledger "FAIL   no-checkout-leak: found Brother engine file(s) inside the target repo or temp HOME:$LEAKED"
else
  ledger "PASS   no-checkout-leak: neither the target repo nor the temp HOME holds a Brother checkout file"
fi

# ---------------------------------------------------------------------------
# NO INTERNAL COMMAND BEYOND THE LAUNCHER. A mechanical self-audit of THIS
# script's own source (never a claim taken on faith): no non-comment line
# names an internal engine module, so the only Brother executable this
# script ever runs is the resolved launcher.
# ---------------------------------------------------------------------------
FORBIDDEN='door\.py|loop_bridge\.py|model_worker\.py|graph_loop\.py|integrate\.py|claim_store\.py|work_record\.py|scope_audit\.py|worktree_lane\.py|brother_run\.py'  # SELF-CHECK-PATTERN-DEFINITION, excluded from its own scan below
if grep -Ev '^[[:space:]]*#|SELF-CHECK-PATTERN-DEFINITION' "$SELF" | grep -Eq "$FORBIDDEN"; then
  ledger "FAIL   no-internal-command: this script names an internal engine module directly, bypassing the installed launcher"
else
  ledger "PASS   no-internal-command: this script never names an internal engine module; the only Brother executable it runs is the resolved launcher"
fi

finish
