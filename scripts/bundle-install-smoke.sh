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

# A LEAF EXCLUDED AS NOT YET PUBLISHED is a DIFFERENT thing from the NODATA
# STAGES above, and it is kept in its own bucket on purpose. A stage in
# NODATA_STAGES means this proof measured nothing about a question it was
# supposed to answer, so the run cannot compose into a PASSED sentence. A
# leaf that cannot be fetched from GitHub because its path was exported
# after the last tag is not that: everything else this script proves still
# stands, the leaf is named honestly as NO-DATA, and it is left out of every
# assertion that would otherwise need it, never counted as a pass and never
# turned into a reason to fail the whole run.
EXCLUDED_LEAVES=""
excluded_leaf() { EXCLUDED_LEAVES="$EXCLUDED_LEAVES $1"; say "NO-DATA: $2"; }

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

# THE LEAVES ARE READ GENERICALLY, never typed by name, so a leaf added later
# (like brotherds) is picked up automatically. A "leaf" here is any
# marketplace entry sourced as a git-subdir under products/; the bundle's own
# entry (source path "bundle") is not a leaf, it is the thing being installed.
LEAVES_FILE="$WORK/leaves.txt"
python3 -c "
import json,sys
d=json.load(open(sys.argv[1]))
for p in d['plugins']:
    src = p.get('source') or {}
    path = src.get('path','')
    if src.get('source') == 'git-subdir' and path.startswith('products/'):
        print('%s\t%s\t%s\t%s\t%s' % (p['name'], p['version'], src.get('url',''), src.get('ref',''), path))
" "$MANIFEST" > "$LEAVES_FILE" || fail "could not read the umbrella's promised leaves from $MANIFEST"
[ -s "$LEAVES_FILE" ] || fail "no products/ leaves found in $MANIFEST; nothing to prove"

# EACH LEAF IS PROBED BEFORE ANYTHING ELSE ASSUMES IT IS INSTALLABLE. A
# leaf's install source is ALWAYS the published GitHub repo at a tag, in BOTH
# path mode and --github mode: only the MARKETPLACE add differs between the
# two modes, never a plugin's own git-subdir source. So a leaf whose path was
# only just exported into this tree (brotherds ahead of its first tag) cannot
# be installed from a clean client in either mode, and no local edit changes
# that: the leaf is UNPROVABLE here, not broken. Proof it is unprovable is
# cheap and needs no full clone: `git ls-remote` proves the ref exists at
# all, and only if it does, a single-tag shallow fetch plus `ls-tree` proves
# the declared path exists inside that ref.
: > "$WORK/leaves-published.txt"
while IFS="$(printf '\t')" read -r LNAME LVER LURL LREF LPATH; do
  [ -n "$LNAME" ] || continue

  if ! git ls-remote --exit-code "$LURL" "refs/tags/$LREF" >/dev/null 2>&1; then
    excluded_leaf "$LNAME" \
      "$LNAME not yet published at $LREF (tag not found on $LURL); provable only by the post-publish run (--github) after the cut"
    continue
  fi

  PROBE_DIR="$WORK/probe-$LNAME"
  mkdir -p "$PROBE_DIR"
  if git init -q "$PROBE_DIR" >/dev/null 2>&1 \
     && git -C "$PROBE_DIR" fetch -q --depth 1 "$LURL" "refs/tags/$LREF" >/dev/null 2>&1 \
     && git -C "$PROBE_DIR" ls-tree --name-only FETCH_HEAD "$LPATH" 2>/dev/null | grep -q .
  then
    printf '%s\t%s\n' "$LNAME" "$LVER" >> "$WORK/leaves-published.txt"
    say "umbrella promises $LNAME $LVER"
  else
    excluded_leaf "$LNAME" \
      "$LNAME not yet published at $LREF ($LPATH not found in that tag); provable only by the post-publish run (--github) after the cut"
  fi
done < "$LEAVES_FILE"

[ -s "$WORK/leaves-published.txt" ] || fail "every leaf the manifest names is unprovable at its ref; nothing left to install-test"

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
# pull every PUBLISHED leaf in one command. If the host does not resolve its
# declared dependencies, the one-install claim is false and every surface
# repeating it is wrong, so this is reported as a FAIL rather than softened.
# A leaf excluded above as unprovable is never asserted here: it was never
# claimed installable in the first place.
MISSING=""
while IFS="$(printf '\t')" read -r LNAME LVER; do
  [ -n "$LNAME" ] || continue
  grep -q "$LNAME@" "$WORK/list.log" || MISSING="$MISSING $LNAME"
done < "$WORK/leaves-published.txt"

if [ -n "$MISSING" ]; then
  cat "$WORK/list.log"
  say "the bundle installed but the host did not resolve:$MISSING"
  fail "one-install is not true: brother@brother does not deliver every published leaf"
fi

while IFS="$(printf '\t')" read -r LNAME LVER; do
  [ -n "$LNAME" ] || continue
  grep -q "Version: $LVER" "$WORK/list.log" || {
    cat "$WORK/list.log"
    fail "$LNAME resolved to a version the umbrella does not promise ($LVER)"; }
done < "$WORK/leaves-published.txt"

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
  caveat_run=1
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
#
# A leaf excluded above (not yet published) is passed to check_installed_
# surface.py via --skip, one flag per excluded name: the manifest ships its
# entries, but a clean install cannot describe a leaf that cannot be fetched,
# so it is left out of the comparison by name rather than either asserted or
# silently dropped.
if [ -f "$ROOT/bundle/MANIFEST.json" ]; then
  SKIP_ARGS=""
  for PLUGIN in $(python3 -c "import json;print(' '.join(json.load(open('$ROOT/bundle/MANIFEST.json'))['shipped_plugins']))"); do
    case " $EXCLUDED_LEAVES " in
      *" $PLUGIN "*)
        say "NO-DATA: $PLUGIN excluded from the installed-surface comparison, not installable before publish"
        SKIP_ARGS="$SKIP_ARGS --skip $PLUGIN"
        continue
        ;;
    esac
    claude plugin details "$PLUGIN" >"$WORK/details-$PLUGIN.log" 2>&1 || {
      cat "$WORK/details-$PLUGIN.log"
      fail "the manifest ships $PLUGIN but a clean install cannot describe it"; }
  done
  MANIFEST_VERDICT=$(python3 "$ROOT/scripts/check_installed_surface.py" \
      --manifest "$ROOT/bundle/MANIFEST.json" --details-dir "$WORK" $SKIP_ARGS 2>&1) || {
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

# THE CAVEAT this script has always carried (registration proves discovery,
# never behaviour), plus, when any leaf was excluded above, the fact that
# this run proved less than every leaf the manifest names. Neither is
# softened into the word PASSED without saying so.
CAVEATS=""
[ -n "${caveat_run:-}" ] && CAVEATS="$CAVEATS (discovery only: presence of $PROVEN entries asserted, behaviour not)"
if [ -n "$EXCLUDED_LEAVES" ]; then
  CAVEATS="$CAVEATS (excluded, not yet publishable, never proven and never counted as a pass:$EXCLUDED_LEAVES)"
fi

WANT_SUMMARY=$(awk -F'\t' '{printf "%s%s %s", (NR>1?", ":""), $1, $2}' "$WORK/leaves-published.txt")
say "PASSED: one command installed the bundle plus $WANT_SUMMARY, uninstall clean$CAVEATS"
exit 0
