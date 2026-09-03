#!/bin/sh
# The one-install proof for the Brother bundle, P4 of the execution grid.
#
# It answers ONE question the umbrella's whole Stage 0 argument rests on:
# does `claude plugin install brother@brother` actually deliver BOTH leaves,
# or does the bundle manifest declare dependencies the host never resolves?
#
# That question is measured here rather than assumed, because the README, the
# marketplace and the bundle manifest all describe a one-command install and
# none of them proves one. A dependency list is a claim about a resolver.
#
# Mirrors BrotherModeUp/scripts/release-smoke-install.sh deliberately: same
# throwaway CLAUDE_CONFIG_DIR, same exit contract, same refusal to treat a
# missing client as a pass.
#
# Default source is THIS TREE (path mode), which proves the release candidate
# before it is pushed. Pass --github to prove the published path instead.
#
# Exit 0 PASSED. Exit 1 FAILED. Exit 2 BLOCKED (no claude binary): a BLOCKED
# exit is NOT a pass, for the same reason the sibling script says so.
# No em or en dashes.
set -u

say()  { printf '%s\n' "bundle-install-smoke: $*"; }
fail() { say "FAILED: $*"; exit 1; }

# NO-DATA IS NEVER A PASS, and this is a PROOF command: a stage that measured
# nothing cannot be composed into the final PASSED sentence (EVAD run 5
# trial 6 caught exactly that composition). Each unmeasured stage records its
# name here and the composed verdict at the bottom refuses.
NODATA_STAGES=""
nodata() { NODATA_STAGES="$NODATA_STAGES $1"; say "NO-DATA: $2"; }

# A stage can PASS while proving less than the whole surface (the
# registration check below proves discovery, never behaviour). That scope
# limit is real and true, never a NO-DATA, but a reader of only the final
# PASSED line has read it as an unqualified pass before now (docs honesty
# audit, 2026-09-03). Each such stage appends its own caveat here, and the
# composed verdict at the bottom carries every one of them forward on the
# same line, never leaving one to a say() a reader can miss.
CAVEATS=""
caveat() { CAVEATS="$CAVEATS ($1)"; }

command -v claude >/dev/null 2>&1 || {
  say "BLOCKED: no claude binary on PATH; this proof needs a real client"
  exit 2
}

ROOT=$(cd "$(dirname "$0")/.." && pwd)
SRC="$ROOT"
[ "${1:-}" = "--github" ] && SRC="https://github.com/khalilmaaouni/Brother.git"

WORK=$(mktemp -d "${TMPDIR:-/tmp}/brother-bundle-smoke.XXXXXX") || fail "mktemp"
CLAUDE_CONFIG_DIR="$WORK/config"; export CLAUDE_CONFIG_DIR
mkdir -p "$CLAUDE_CONFIG_DIR"
trap 'rm -rf "$WORK"' EXIT

say "sandbox: $CLAUDE_CONFIG_DIR"
say "source:  $SRC"

# The versions the umbrella promises, read from its own manifest rather than
# typed, so this script cannot drift from what it is proving.
#
# THE MANIFEST MUST COME FROM THE SAME PLACE AS THE INSTALL. In --github mode
# an earlier version of this script read the promise from the LOCAL tree while
# installing from the remote, so it compared a local claim against a remote
# delivery. That shape PASSES over a published surface that is still wrong, and
# it did on 2026-08-24: the remote advertised brothersbe 3.4.1 and delivered
# 3.4.2, and a local fix made the comparison agree before the fix was pushed.
# A check whose two halves come from different places is not checking the thing
# it names.
MANIFEST="$ROOT/.claude-plugin/marketplace.json"
if [ "$SRC" != "$ROOT" ]; then
  RAW="https://raw.githubusercontent.com/khalilmaaouni/Brother/main/.claude-plugin/marketplace.json"
  curl -sfL "$RAW" -o "$WORK/marketplace.json" \
    || fail "could not fetch the PUBLISHED manifest from $RAW; refusing to fall back to the local one, because that is the false-pass this check exists to avoid"
  MANIFEST="$WORK/marketplace.json"
  say "promises read from: the published manifest at main"
else
  say "promises read from: this tree"
fi

read_promise() {
  python3 -c "
import json,sys
d=json.load(open(sys.argv[1]))
m=[p['version'] for p in d['plugins'] if p['name']==sys.argv[2]]
if not m:
    sys.exit('no plugin named ' + sys.argv[2] + ' in the manifest')
print(m[0])
" "$MANIFEST" "$1"
}

WANT_MODE=$(read_promise brothermode) || fail "could not read the promised brothermode version"
WANT_SBE=$(read_promise brothersbe)  || fail "could not read the promised brothersbe version"
say "umbrella promises brothermode $WANT_MODE and brothersbe $WANT_SBE"

claude plugin marketplace add "$SRC" >"$WORK/add.log" 2>&1 || {
  cat "$WORK/add.log"; fail "marketplace add"; }
grep -q "Successfully added marketplace" "$WORK/add.log" || {
  cat "$WORK/add.log"; fail "add gave no success line"; }

claude plugin install brother@brother >"$WORK/install.log" 2>&1 || {
  cat "$WORK/install.log"; fail "bundle install"; }
grep -q "Successfully installed plugin" "$WORK/install.log" || {
  cat "$WORK/install.log"; fail "install gave no success line"; }

claude plugin list >"$WORK/list.log" 2>&1 || fail "plugin list"

# THE ACTUAL QUESTION. The bundle ships no code of its own; it exists only to
# pull both leaves in one command. If the host does not resolve its declared
# dependencies, the one-install claim is false and every surface repeating it
# is wrong, so this is reported as a FAIL rather than softened.
MISSING=""
grep -q "brothermode@" "$WORK/list.log" || MISSING="$MISSING brothermode"
grep -q "brothersbe@"  "$WORK/list.log" || MISSING="$MISSING brothersbe"

if [ -n "$MISSING" ]; then
  cat "$WORK/list.log"
  say "the bundle installed but the host did not resolve:$MISSING"
  fail "one-install is not true: brother@brother does not deliver both leaves"
fi

grep -q "Version: $WANT_MODE" "$WORK/list.log" || {
  cat "$WORK/list.log"
  fail "brothermode resolved to a version the umbrella does not promise ($WANT_MODE)"; }
grep -q "Version: $WANT_SBE" "$WORK/list.log" || {
  cat "$WORK/list.log"
  fail "brothersbe resolved to a version the umbrella does not promise ($WANT_SBE)"; }

# EVERY capability the bundle ships must be proven to REGISTER, not merely to
# exist on disk. Written after 2026-08-28, when a new commands/brother.md was
# reported as shipped while it registered nothing at all, and the first check
# written for it looked for a "Commands (" section this listing never prints,
# so that check could never have proven anything either. Two rules follow and
# both are enforced below rather than remembered:
#   1. The list is ENUMERATED from what is on disk, never hardcoded, so a
#      capability added later cannot ship without its own proof.
#   2. The assertion reads the listing's own ENTRIES, never the whole file:
#      the plugin is itself named brother, so a bare grep matches its own
#      name and proves nothing.
# This listing has no Commands section; a plugin's commands/ entries are
# reported inside the Skills list beside its skills, measured on the installed
# brothermode whose fifteen commands/ files all appear there.
EXPECTED=""
for f in "$ROOT"/bundle/commands/*.md; do
  [ -e "$f" ] || continue
  EXPECTED="$EXPECTED $(basename "$f" .md)"
done
for d in "$ROOT"/bundle/skills/*/; do
  [ -d "$d" ] || continue
  EXPECTED="$EXPECTED $(basename "$d")"
done

if [ -n "$(echo "$EXPECTED" | tr -d ' ')" ]; then
  claude plugin details brother >"$WORK/details.log" 2>&1 || fail "plugin details"
  ENTRIES=$(grep -E "^ *Skills \(" "$WORK/details.log" | sed 's/^ *Skills ([0-9]*) *//' \
            | tr ',' '\n' | sed 's/^ *//;s/ *$//')
  PROVEN=0
  for name in $EXPECTED; do
    echo "$ENTRIES" | grep -qx "$name" || {
      cat "$WORK/details.log"
      fail "the bundle ships $name but no entry of that name registered"; }
    PROVEN=$((PROVEN + 1))
  done
  # WHAT THIS PROVES, AND WHAT IT DOES NOT, calibrated 2026-08-28 rather than
  # assumed: a file placed in bundle/commands/ registers under its basename
  # even with no frontmatter at all, so this assertion proves DISCOVERY and
  # nothing more. It fails when a shipped capability is missing from the
  # listing; it cannot tell a working command from a broken one. The measure
  # that would is a live firing in a real session, which no script here can
  # stand in for, and it stays owed rather than implied by this green line.
  say "every shipped capability is discovered by a clean install: $PROVEN of $PROVEN ($(echo $EXPECTED)). Discovery only; whether each one behaves when invoked is NOT asserted here"
  caveat "discovery only: presence of $PROVEN entries asserted, behaviour not"
else
  nodata registration "the bundle ships no commands or skills, so nothing to assert about registration"
fi

# R11 CLAUSE TWO: the INSTALLED surface must match what the manifest says one
# install produces. Everything above proves the two entries THIS repository
# ships; it says nothing about the thirty and fourteen that brothermode and
# brothersbe contribute to the same install, which is most of the product.
#
# Written 2026-08-29 after finding that clause two was unassertable in
# principle: the only target count on the board was the surface ceiling, and
# the ceiling counts four trees while the umbrella ships three, so it was never
# a statement about what an install delivers. bundle/MANIFEST.json is.
#
# It compares NAMES and not just a count. A count passes when one entry is
# renamed and another added, which is precisely the drift an install check
# should catch.
if [ -f "$ROOT/bundle/MANIFEST.json" ]; then
  for PLUGIN in $(python3 -c "import json;print(' '.join(json.load(open('$ROOT/bundle/MANIFEST.json'))['shipped_plugins']))"); do
    claude plugin details "$PLUGIN" >"$WORK/details-$PLUGIN.log" 2>&1 || {
      cat "$WORK/details-$PLUGIN.log"
      fail "the manifest ships $PLUGIN but a clean install cannot describe it"; }
  done
  MANIFEST_VERDICT=$(python3 "$ROOT/scripts/check_installed_surface.py" \
      --manifest "$ROOT/bundle/MANIFEST.json" --details-dir "$WORK" 2>&1) || {
    echo "$MANIFEST_VERDICT"
    fail "the installed surface does not match bundle/MANIFEST.json"; }
  say "$MANIFEST_VERDICT"
else
  nodata manifest "no bundle/MANIFEST.json, so what one install must produce is not written down and clause two of R11 was not checked"
fi

# REGISTRATION IS NOT EXECUTION. Everything above proves the installed
# surface's NAMES; none of it proves that /brother's own BUILD IT route,
# scripts/brother_run.py packaged at bundle/runtime/brother-run, actually
# runs once installed. Found by name here, never a hardcoded cache path,
# because the host's own cache layout is not this script's business.
LAUNCHER=$(find "$CLAUDE_CONFIG_DIR" -name "brother-run" -type f 2>/dev/null | head -1)
if [ -z "$LAUNCHER" ]; then
  nodata runtime "no bundle/runtime/brother-run found anywhere under the installed config; either the bundle ships no packaged engine (regenerate it with scripts/bundle_runtime.py) or it did not install"
else
  [ -x "$LAUNCHER" ] || fail "the installed launcher at $LAUNCHER is not executable"

  STUB=$(mktemp -d "${TMPDIR:-/tmp}/brother-runtime-stub.XXXXXX") || fail "mktemp for runtime stub"
  TARGET="$STUB/target"
  mkdir -p "$TARGET"
  git -C "$TARGET" init -q -b main
  git -C "$TARGET" config user.email a@b.c
  git -C "$TARGET" config user.name t
  printf 'base\n' > "$TARGET/base.txt"
  git -C "$TARGET" add -A
  git -C "$TARGET" commit -q -m R0 >/dev/null

  cat > "$STUB/decomposer.py" <<'PYEOF'
import json, sys
sys.stdin.read()
print(json.dumps([{"id": "S1", "objective": "create a file",
                   "done_check": "test -f smoke.txt",
                   "writes": ["smoke.txt"], "deps": []}]))
PYEOF
  cat > "$STUB/writer_model.py" <<'PYEOF'
import re, sys
prompt = sys.argv[-1] if len(sys.argv) > 1 else ""
m = re.search(r"Declared write scope: ([^\n]+)", prompt)
for path in (p.strip() for p in (m.group(1).split(",") if m else [])):
    if path:
        open(path, "w").write("written by the stub model\n")
print("stub model wrote: %s" % (m.group(1) if m else "(nothing declared)"))
PYEOF

  # cwd here is the stub directory itself, NOT any Brother checkout: the
  # installed launcher's whole point is working from an arbitrary directory
  # pointed at an arbitrary --cwd with no Brother source nearby.
  RUNTIME_OUT=$(cd "$STUB" && DOOR_MODEL_CMD="python3 $STUB/decomposer.py" \
      MODEL_WORKER_CMD="python3 $STUB/writer_model.py" \
      python3 "$LAUNCHER" "a file exists" --cwd "$TARGET" --runs-root "$STUB" 2>&1)
  RUNTIME_CODE=$?
  if [ "$RUNTIME_CODE" -ne 0 ] || [ ! -f "$TARGET/smoke.txt" ]; then
    echo "$RUNTIME_OUT"
    rm -rf "$STUB"
    fail "the installed launcher did not integrate a stub outcome end to end"
  fi
  say "the installed launcher ($LAUNCHER) integrated a stub outcome end to end: registration is not execution, and this proves execution too"
  rm -rf "$STUB"
fi

claude plugin uninstall brother >"$WORK/uninstall.log" 2>&1 || {
  cat "$WORK/uninstall.log"; fail "uninstall"; }
claude plugin list >"$WORK/list2.log" 2>&1 || fail "plugin list after uninstall"
grep -q "brother@brother" "$WORK/list2.log" && {
  cat "$WORK/list2.log"; fail "bundle still listed after uninstall"; }

if [ -n "$NODATA_STAGES" ]; then
  fail "stage(s)$NODATA_STAGES reported NO-DATA, and a proof command cannot compose an unmeasured stage into a pass"
fi
say "PASSED: one command installed the bundle plus brothermode $WANT_MODE and brothersbe $WANT_SBE, uninstall clean$CAVEATS"
exit 0
