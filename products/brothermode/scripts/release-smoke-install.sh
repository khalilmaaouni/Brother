#!/bin/sh
# The public-install smoke: the release step no release may skip.
# Proves, with the real Claude Code client, that a stranger's install is
# two boring commands, that what installs is what this tree ships, and
# that uninstalling leaves their settings clean.
#
# Runs the whole flow inside a throwaway CLAUDE_CONFIG_DIR, so the machine
# it runs on is never touched. No sign-in is needed: marketplace add and
# plugin install are local mechanics.
#
# Default source is THIS TREE (path mode), so the gate proves the release
# candidate before it is pushed. Pass --github to prove the public path
# after the push instead.
#
# Exit 0 PASSED. Exit 1 FAILED (a step broke: the release stops). Exit 2
# BLOCKED: no claude binary on this machine; the release checklist in
# docs/RELEASE.md forbids proceeding until it is run somewhere real,
# because continuous integration has no Claude client and cannot carry
# this proof. A BLOCKED exit is not a pass. No em or en dashes.
set -u

say() { printf '%s\n' "release-smoke-install: $*"; }
fail() { say "FAILED: $*"; exit 1; }

command -v claude >/dev/null 2>&1 || { say "BLOCKED: no claude binary on PATH; run on a machine with Claude Code installed"; exit 2; }

ROOT=$(cd "$(dirname "$0")/.." && pwd)
SRC="$ROOT"
[ "${1:-}" = "--github" ] && SRC="khalilmaaouni/BrotherModeUp"

WORK=$(mktemp -d "${TMPDIR:-/tmp}/bm-install-smoke.XXXXXX") || fail "mktemp"
CLAUDE_CONFIG_DIR="$WORK/config"; export CLAUDE_CONFIG_DIR
mkdir -p "$CLAUDE_CONFIG_DIR"
trap 'rm -rf "$WORK"' EXIT

say "sandbox: $CLAUDE_CONFIG_DIR"
say "source: $SRC"

claude plugin marketplace add "$SRC" >"$WORK/add.log" 2>&1 || { cat "$WORK/add.log"; fail "marketplace add"; }
grep -q "Successfully added marketplace" "$WORK/add.log" || { cat "$WORK/add.log"; fail "add gave no success line"; }

claude plugin install brothermode@brothermode-marketplace >"$WORK/install.log" 2>&1 || { cat "$WORK/install.log"; fail "plugin install"; }
grep -q "Successfully installed plugin" "$WORK/install.log" || { cat "$WORK/install.log"; fail "install gave no success line"; }

VERSION_FILE=$(cat "$ROOT/VERSION")
claude plugin list >"$WORK/list.log" 2>&1 || fail "plugin list"
grep -q "brothermode@brothermode-marketplace" "$WORK/list.log" || { cat "$WORK/list.log"; fail "installed plugin missing from list"; }
grep -q "Version: $VERSION_FILE" "$WORK/list.log" || { cat "$WORK/list.log"; fail "installed version does not match VERSION ($VERSION_FILE)"; }

claude plugin details brothermode >"$WORK/details.log" 2>&1 || fail "plugin details"
HOOK_COUNT=$(python3 "$ROOT/tools/bm_project_facts.py" --field hook_count)
grep -q "Hooks ($HOOK_COUNT)" "$WORK/details.log" || { cat "$WORK/details.log"; fail "hook group count is not $HOOK_COUNT"; }
grep -q "Skills (" "$WORK/details.log" || fail "no skills registered"

claude plugin uninstall brothermode >"$WORK/uninstall.log" 2>&1 || { cat "$WORK/uninstall.log"; fail "uninstall"; }
claude plugin list >"$WORK/list2.log" 2>&1 || fail "plugin list after uninstall"
grep -q "brothermode@" "$WORK/list2.log" && { cat "$WORK/list2.log"; fail "plugin still listed after uninstall"; }

python3 - "$CLAUDE_CONFIG_DIR/settings.json" <<'PYEOF' || fail "settings not clean after uninstall"
import json, sys
s = json.load(open(sys.argv[1]))
assert s.get("enabledPlugins") == {}, "enabledPlugins not empty: %r" % s.get("enabledPlugins")
PYEOF

say "PASSED: add, install (version $VERSION_FILE, $HOOK_COUNT hook groups), uninstall clean"
exit 0
