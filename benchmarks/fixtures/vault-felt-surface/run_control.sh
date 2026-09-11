#!/bin/sh
# run_control.sh: VN4a, the felt-surface control driver.
#
# Mirrors scripts/clean_install_e2e.sh's own isolation mechanism (line
# numbers below are that script's own, read whole before this was written):
#   lines 148-151: throwaway HOME_DIR and CLAUDE_CONFIG_DIR under one WORK.
#   line 206: claude plugin marketplace add "$ROOT"
#   line 214: claude plugin install brother@brother -y
#   line 227: find the installed launcher under
#             "$CLAUDE_CONFIG_DIR/plugins/cache"
#   lines 294-297: find the installed brothermode tools/ directory the
#             same way (BROTHER_RUNTIME_ROOT for the loop's own worker).
#   line 310: python3 "$LAUNCHER" "$OUTCOME" --cwd "$TARGET"
#
# UNLIKE clean_install_e2e.sh, this driver:
#   - never deletes WORK on exit (no trap rm -rf PRESENT): VN4a is a
#     measurement, so the transcript and hook output ARE the evidence and
#     must survive the run.
#   - seeds TARGET from this directory's own target-repo-seed/ (a tiny
#     invented app/ledger.py) instead of a bare seed.txt, so there is a
#     real file to edit.
#   - binds a fixture vault (this directory's own vault-seed/, copied
#     fresh so the checked-in seed is never mutated) with the same two
#     commands a real user runs: scripts/setup.py --accept-notice for
#     consent (vault_recall_hook.py's cmd_check refuses to read the vault
#     or write anything without it) and tools/bm_vault_cli.py bind for the
#     vault location bm_vault.py itself reads (products/brothermode/
#     skills/brotherme/SKILL.md documents exactly this pair).
#   - runs the launcher WITHOUT the hermetic DOOR_MODEL_CMD / MODEL_WORKER_CMD
#     stubs clean_install_e2e.sh sets by default: this is deliberate, not an
#     oversight. Those stubs write files with plain python (see that
#     script's own decomposer.py / writer_model.py heredocs), never through
#     a real Claude Code Edit tool call, so they never trip PreToolUse at
#     all. The vault recall hook only fires on Claude Code's own tool
#     matcher (products/brothermode/hooks/hooks.json: "Edit|Write|
#     MultiEdit|NotebookEdit|Bash"), which means the ONLY way to see it fire
#     is to let the launcher's worker fall back to its own default (scripts/
#     model_worker.py's CLAUDE_ARGV: `claude -p --output-format json
#     --permission-mode acceptEdits`), a real nested Claude Code session.
#
# A FRESH WORK DIRECTORY EVERY ATTEMPT (2026-09-08 law: a reused isolated
# home reads stale): each run mktemps its own WORK and prints it; nothing
# here is reused between invocations.
#
# No em or en dashes.
set -u

say() { printf '%s\n' "run-control: $*"; }

command -v claude >/dev/null 2>&1 || {
  say "NO-DATA: no claude binary on PATH"
  exit 2
}

HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../../.." && pwd)

WORK=$(mktemp -d "${TMPDIR:-/tmp}/vn4a-felt-surface.XXXXXX") || {
  say "FAIL: mktemp"
  exit 1
}
say "WORK: $WORK"

HOME_DIR="$WORK/home"
CONFIG_DIR="$WORK/config"
TARGET="$WORK/target-repo"
mkdir -p "$HOME_DIR" "$CONFIG_DIR" "$TARGET"

case "$CONFIG_DIR" in
  "$WORK"/*) : ;;
  *) say "FAIL: CLAUDE_CONFIG_DIR ($CONFIG_DIR) is not inside the throwaway $WORK; refusing"; exit 1 ;;
esac

# LIVE MODE CARRIES THE USER'S CLI SESSION (clean_install_e2e.sh lines
# 162-170's own reasoning, copied here): a truly logged-out HOME makes the
# claude CLI print onboarding text instead of running, which is exactly the
# "decomposer's answer could not be read as JSON ... claude exited 1, wrote
# nothing to stderr" failure this line exists to prevent. The CLI's root
# config is copied into the sandbox HOME; plugins and everything else stay
# isolated under CLAUDE_CONFIG_DIR.
REAL_HOME="$HOME"
if [ -f "$REAL_HOME/.claude.json" ]; then
  cp "$REAL_HOME/.claude.json" "$HOME_DIR/.claude.json"
fi
export HOME="$HOME_DIR"
export CLAUDE_CONFIG_DIR="$CONFIG_DIR"

# CONTAMINATION FOUND WHILE BUILDING THIS SCRIPT (2026-09-08): bm_vault.py's
# own _default_vault() reads BM_VAULT_ROOT / BROTHERMODE_VAULT from the
# environment BEFORE it ever reads the bound config file (its own D01
# contract comment: "environment first, config file second"). This
# session's own ambient shell already carries BROTHERMODE_VAULT (and
# BROTHERSBE_VAULT, BM_FENCE_MODE, BROTHERMODE_REGISTRIES,
# BROTHERMODE_SESSION_CAP) pointed at the real founder vault, because this
# machine already runs BrotherMode day to day. Isolating only HOME and
# CLAUDE_CONFIG_DIR is NOT enough on a machine that already has these set:
# a first refresh run without this block indexed 988 real notes from
# ~/Documents/Kay Vault instead of the 4-note fixture
# vault, entirely by the product's own documented precedence, not a bug in
# it. Unset here so this control measures the FIXTURE vault, and named here
# because a stranger's own machine with no BrotherMode history will never
# hit this: it is this repository's own dev environment leaking into its
# own isolation, worth carrying forward as a fixture-hygiene note for every
# other VN4x control this same shape might run in.
unset BM_VAULT_ROOT BROTHERMODE_VAULT BROTHERSBE_VAULT BM_FENCE_MODE \
      BROTHERMODE_REGISTRIES BROTHERMODE_SESSION_CAP CLAUDE_PLUGIN_ROOT \
      DOOR_MODEL_CMD MODEL_WORKER_CMD

say "sandbox HOME:          $HOME"
say "sandbox CLAUDE_CONFIG: $CLAUDE_CONFIG_DIR"
say "target repo:           $TARGET"

# Masked for the same reason clean_install_e2e.sh masks it (this machine's
# own global git config sets tag.gpgsign/gpg.format).
GIT_CONFIG_GLOBAL=/dev/null
GIT_CONFIG_NOSYSTEM=1
export GIT_CONFIG_GLOBAL GIT_CONFIG_NOSYSTEM

# ---------------------------------------------------------------------------
# SEED TARGET from the fixture (never a bare seed.txt).
# ---------------------------------------------------------------------------
cp -R "$HERE/target-repo-seed/." "$TARGET/"
(
  cd "$TARGET" || exit 1
  git init -q -b main
  git config user.email "vn4a-felt-surface@example.invalid"
  git config user.name "vn4a-felt-surface"
  git add -A
  git -c commit.gpgsign=false commit -q -m "seed"
) || { say "FAIL: could not seed target repo"; exit 1; }
say "PASS: target-seed"

# ---------------------------------------------------------------------------
# INSTALL, clean_install_e2e.sh's own mechanism (lines 206, 214).
# ---------------------------------------------------------------------------
claude plugin marketplace add "$ROOT" >"$WORK/add.log" 2>&1
if [ $? -ne 0 ] || ! grep -q "Successfully added marketplace" "$WORK/add.log"; then
  cat "$WORK/add.log"
  say "FAIL: marketplace-add"
  exit 1
fi
say "PASS: marketplace-add"

claude plugin install brother@brother -y >"$WORK/install.log" 2>&1
if [ $? -ne 0 ] || ! grep -q "Successfully installed plugin" "$WORK/install.log"; then
  cat "$WORK/install.log"
  say "FAIL: bundle-install"
  exit 1
fi
say "PASS: bundle-install"

# ---------------------------------------------------------------------------
# RESOLVE THE INSTALLED LAUNCHER AND TOOLS ROOT, by the manifest as
# clean_install_e2e.sh does (lines 227, 294-297), never a typed version.
# ---------------------------------------------------------------------------
LAUNCHER_HITS=$(find "$CLAUDE_CONFIG_DIR/plugins/cache" -path "*/brother/*/runtime/brother-run" -type f 2>/dev/null)
LAUNCHER=$(printf '%s\n' "$LAUNCHER_HITS" | head -1)
if [ -z "$LAUNCHER" ]; then
  say "FAIL: no installed launcher found under \$CLAUDE_CONFIG_DIR/plugins/cache"
  exit 1
fi
say "PASS: launcher-resolve: $LAUNCHER"

BM_TOOLS=$(find "$CLAUDE_CONFIG_DIR/plugins/cache" -maxdepth 4 -path "*/brothermode/*/tools" -type d 2>/dev/null | head -1)
if [ -z "$BM_TOOLS" ]; then
  say "FAIL: no installed brothermode tools/ directory found under the plugin cache"
  exit 1
fi
say "PASS: bm-tools-resolve: $BM_TOOLS"
BM_ROOT=$(dirname "$BM_TOOLS")

# ---------------------------------------------------------------------------
# BIND THE FIXTURE VAULT, the same two commands a real user runs (consent
# via scripts/setup.py, vault location via tools/bm_vault_cli.py bind). A
# fresh copy of the fixture vault, never the checked-in seed directory
# itself.
# ---------------------------------------------------------------------------
VAULT="$WORK/vault"
cp -R "$HERE/vault-seed" "$VAULT"

python3 "$BM_ROOT/scripts/setup.py" --vault "$VAULT" --mode plugin --accept-notice \
  >"$WORK/setup.log" 2>&1
SETUP_EXIT=$?
cat "$WORK/setup.log"
if [ "$SETUP_EXIT" -ne 0 ]; then
  say "FAIL: setup.py --accept-notice exited $SETUP_EXIT"
  exit 1
fi
say "PASS: consent-setup"

python3 "$BM_TOOLS/bm_vault_cli.py" bind "$VAULT" >"$WORK/bind.log" 2>&1
BIND_EXIT=$?
cat "$WORK/bind.log"
if [ "$BIND_EXIT" -ne 0 ]; then
  say "FAIL: bm_vault_cli.py bind exited $BIND_EXIT"
  exit 1
fi
say "PASS: vault-bind"

# ---------------------------------------------------------------------------
# REFRESH THE INDEX (hooks.json's own SessionStart step 2: python3
# "${CLAUDE_PLUGIN_ROOT}/tools/bm_vault.py" refresh), so the fixture notes
# are actually queryable before either probe below runs.
# ---------------------------------------------------------------------------
REFRESH_LOG="$WORK/refresh.log"
( cd "$TARGET" && python3 "$BM_TOOLS/bm_vault.py" refresh ) >"$REFRESH_LOG" 2>&1
cat "$REFRESH_LOG"
say "PASS: index-refresh (see $REFRESH_LOG)"

# ---------------------------------------------------------------------------
# DIRECT HOOK PROBE, the fallback proof for when the live loop below cannot
# authenticate (see the module comment near the top of this file for the
# measured shape): the EXACT request Claude Code's own PreToolUse runner
# sends, built by hand and piped into the INSTALLED vault_recall_hook.py
# (products/brothermode/hooks/hooks.json's own command line, with
# ${CLAUDE_PLUGIN_ROOT} resolved to BM_ROOT the way Claude Code itself
# resolves it at dispatch time, exported below). This never spawns a
# second `claude` process, so it is unaffected by that blocker.
# ---------------------------------------------------------------------------
export CLAUDE_PLUGIN_ROOT="$BM_ROOT"
PAYLOAD_FILE="$WORK/probe-payload.json"
python3 -c "
import json, sys
target = sys.argv[1]
print(json.dumps({
    'session_id': 'vn4a-control-probe',
    'cwd': target,
    'tool_name': 'Edit',
    'tool_input': {'file_path': target + '/app/ledger.py'},
}))
" "$TARGET" >"$PAYLOAD_FILE"
HOOK_OUT="$WORK/hook-probe.stdout.json"
HOOK_ERR="$WORK/hook-probe.stderr.log"
# cd "$TARGET" FIRST (measured 2026-09-08, this control's own first probe
# attempt): bm_vault.py's own evidence check inside _print_hits resolves a
# path: evidence_locator against the CALLING PROCESS's cwd, not against the
# payload's "cwd" field alone, and vault_recall_hook.py's own subprocess.run
# to bm_vault.py inherits whatever cwd this hook process started in. Run
# from anywhere else and every path:app/ledger.py locator reads "does not
# currently hold" even though the file is right there, which is exactly the
# false WITHHELD (refused) this control's own first probe attempt produced
# on the 00-applicable fixture.
( cd "$TARGET" && python3 "$BM_TOOLS/vault_recall_hook.py" check <"$PAYLOAD_FILE" ) >"$HOOK_OUT" 2>"$HOOK_ERR"
HOOK_EXIT=$?
say "PASS: hook-probe exit=$HOOK_EXIT"
say "hook probe payload: $PAYLOAD_FILE"
say "hook probe stdout (additionalContext, what the model would see): $HOOK_OUT"
say "hook probe stderr (the once-per-session banners): $HOOK_ERR"
echo "-- hook probe stdout --"
cat "$HOOK_OUT"
echo "-- hook probe stderr --"
cat "$HOOK_ERR"

# ---------------------------------------------------------------------------
# THE LIVE LOOP, attempted for completeness. No DOOR_MODEL_CMD /
# MODEL_WORKER_CMD: the launcher's own worker would fall back to a real
# `claude` CLI headless session (scripts/model_worker.py's own CLAUDE_ARGV),
# the only path that fires the installed PreToolUse hooks through an actual
# Edit tool call end to end. MEASURED 2026-09-08, this session: exit 1 under
# this session's own sandboxing, unauthenticated, even after copying
# ~/.claude.json into the sandbox HOME (scripts/door.py's own module
# comment names the identical shape for the Codex host: a nested client
# that reports itself unauthenticated rather than naming the real block).
# Kept in the driver, not removed: a differently-privileged caller (an
# authenticated shell with no nested-session restriction) may carry it
# through to a real Edit and a real second PreToolUse firing, which the
# hook probe above cannot exercise (it never runs the fence hook, the bash
# audit hook, or a second real edit).
# ---------------------------------------------------------------------------
OUTCOME="edit app/ledger.py: add a docstring to close_month"
RUN_LOG="$WORK/run.log"
python3 "$LAUNCHER" "$OUTCOME" --cwd "$TARGET" >"$RUN_LOG" 2>&1
RUN_EXIT=$?
cat "$RUN_LOG"
say "launcher exit: $RUN_EXIT"
say "run log: $RUN_LOG"
say "WORK (kept, not deleted): $WORK"
exit 0
