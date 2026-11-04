#!/bin/sh
# merge_window.sh: the merge window as one command (unit A2.e; REGISTER.md SPEED AND COST item 3: merges by hand took
# M1 28 min and M2 19 min, measured, in four steps a script can do).
#
# usage, from the root of a CLEAN checkout on the branch that receives the merges:
#   BROTHER_WBS=docs/plan/<plan>.json sh scripts/merge_window.sh UNIT=REF [UNIT=REF ...]
#
# In order: (1) merge each REF (a landed commit or branch) with its own merge commit; (2) regenerate the generated
# files this tree ships (bundle_runtime.py, system_doc.py, each product's checksums.sh, the plan generator named by
# MW_PLAN_GEN or the 1.1.7 one) and commit what they changed; (3) run each unit's done check on python3 and on
# MW_PY39 (default /usr/bin/python3), screened by plan_lint.screen_done_check exactly as the closer screens it;
# (4) close each unit with scripts/close_unit.py (A1: a skip is PARTIAL) and commit the plan; (5) print one line per
# unit: the merged sha, both interpreters' exit codes, DONE or why not. The gate starts only from the PASS line.
#
# exit 0: PASS, every unit DONE and green on both interpreters. 1: a unit not DONE or a done check red or not run
# (NO-DATA is never a pass). 2: refused before any merge (no units, a malformed or repeated UNIT=REF, a REF git cannot
# resolve, a unit the plan does not hold, a dirty tree, a detached HEAD), or a merge conflict (that merge is aborted,
# the merges before it stay committed and are named, nothing after it runs).
#
# GENERATED FILES NEVER CONFLICT (unit MERGE1.b). When a merge conflicts and EVERY conflicted path is a generated file
# (SYSTEM.md, bundle/runtime/*, bundle/hooks/hooks.json, bundle/cursor-hooks/hooks.json, bundle/codex-skills/*, and
# products/<p>/CHECKSUMS.sha256 for a top level product), the window does not textually resolve them: it puts OUR side
# (the branch receiving the merge) back only as a parseable baseline for the generators, because a file holding conflict
# markers cannot be read; the generators then rewrite the output from the merged sources, so no side is taken as an
# answer. Where it could matter, bundle_runtime.py carries the stamp fields source_revision and source_describe forward
# from the baseline when the hashes did not change; --check ignores those two fields by design, so the side chosen is
# not observable by any check and is a named limit. Everything every generator wrote is then staged (the merge commit is
# exactly the tree the checks approved, and a tree still dirty or holding an untracked file aborts), both --check calls
# and each product's own verify-install.sh must pass, and the merge is committed. Conflicted paths are read NUL
# separated and compared as literal strings, never as shell globs. Any other conflicted path, a mixed
# conflict, a missing or failing generator, a red check or a dirty result aborts the merge exactly like any conflict
# (exit 2, fail closed). A REFUSED MERGE NEVER DISCARDS WORK THE WINDOW DID NOT CREATE: the window starts only on a clean
# checkout under its lock, and a refusal undoes (merge --abort, then the generated leftovers) only while HEAD is still the
# commit before that merge and nothing but generated paths changed since; otherwise it changes nothing, prints
# MERGE-STOPPED with the paths, and exits 2. The regenerated tree is a NEW tree: a gate record is keyed by its exact tree
# (merge_gate.py verify), so none is reused for it; the window itself reads no gate record.
set -u
PY39=${MW_PY39:-/usr/bin/python3}
TAB=$(printf '\t'); KEEP_IFS=$IFS
say() { printf '%s\n' "$*"; }
refuse() { say "REFUSED: $*"; exit 2; }

[ $# -gt 0 ] || refuse "name at least one UNIT=REF"
[ -n "${BROTHER_WBS:-}" ] || refuse "set BROTHER_WBS to the plan the units belong to"
[ -f "$BROTHER_WBS" ] || refuse "the plan $BROTHER_WBS is not a file here"
git symbolic-ref -q HEAD >/dev/null || refuse "HEAD is detached; check out the branch that receives the merges"
# THE LOCK (none existed before this unit): one window per repository at a time, a directory under the common git dir so
# every worktree of the clone shares it. mkdir is atomic; a holder whose pid is dead is stale and reclaimed; a live holder
# is waited for MW_LOCK_WAIT seconds (default 60), then the window REFUSES (exit 2, nothing touched). Released on exit.
# ponytail: pid reuse could keep a dead holder looking alive until the wait runs out, which refuses, never merges.
LOCKD=$(cd "$(git rev-parse --git-common-dir)" 2>/dev/null && pwd)/merge-window.lock
waited=0
until mkdir "$LOCKD" 2>/dev/null; do
  holder=$(cat "$LOCKD/pid" 2>/dev/null)
  if [ -n "$holder" ] && ! kill -0 "$holder" 2>/dev/null; then rm -f "$LOCKD/pid"; rmdir "$LOCKD" 2>/dev/null; continue; fi
  [ "$waited" -lt "${MW_LOCK_WAIT:-60}" ] 2>/dev/null \
    || refuse "another merge window holds $LOCKD (pid ${holder:-unknown}); waited ${waited}s, nothing was touched"
  sleep 1; waited=$((waited + 1))
done
printf '%s\n' "$$" > "$LOCKD/pid"
trap 'rm -f "$LOCKD/pid"; rmdir "$LOCKD" 2>/dev/null' EXIT
trap 'exit 143' TERM
trap 'exit 130' INT
# the tree must be clean: any uncommitted or untracked change is the owner's, and the window never touches it
[ -z "$(git status --porcelain)" ] || refuse "the tree is not clean; the window commits only what it merges and regenerates"

units=""
for pair in "$@"; do
  unit=${pair%%=*}; ref=${pair#*=}
  case "$pair" in *=*) ;; *) refuse "'$pair' is not UNIT=REF";; esac
  case "$unit" in ''|*[!A-Za-z0-9._-]*) refuse "'$unit' is not a unit id";; esac
  [ -n "$ref" ] || refuse "$unit names no REF"
  case " $units " in *" $unit "*) refuse "$unit is named twice";; esac
  git rev-parse -q --verify "$ref^{commit}" >/dev/null || refuse "$unit: git cannot resolve $ref to a commit"
  python3 -c 'import json, sys
plan = json.load(open(sys.argv[1]))
sys.exit(0 if any(u.get("id") == sys.argv[2] for u in plan.get("units", [])) else 1)' "$BROTHER_WBS" "$unit" \
    || refuse "$unit is not a unit of $BROTHER_WBS"
  units="$units $unit"
done

# generated paths, and the generator that owns each one. Matched as literal strings by case: a name like SYSTEM.m[d] is
# data, never a pattern, and a product checksum counts only at products/<one name>/CHECKSUMS.sha256
is_generated() {
  case "$1" in
    SYSTEM.md|bundle/runtime/*|bundle/hooks/hooks.json|bundle/cursor-hooks/hooks.json|bundle/codex-skills/*) return 0;;
    products/*/CHECKSUMS.sha256) rest=${1#products/}; [ "$1" = "products/${rest%%/*}/CHECKSUMS.sha256" ]; return $?;;
  esac
  return 1
}
owner_of() {
  case "$1" in
    SYSTEM.md) [ -f scripts/system_doc.py ] ;;
    bundle/*) [ -f scripts/bundle_runtime.py ] && [ -d bundle ] ;;
    products/*/CHECKSUMS.sha256) p=${1%/CHECKSUMS.sha256}; [ -f "$p/scripts/checksums.sh" ] ;;
    *) false ;;
  esac
}
# regen_core: bundle_runtime.py, system_doc.py, each product's checksums.sh; 0, or 1 with GEN_MSG naming the failure
GEN_MSG=""
gen_step() { say "REGENERATE $*"; "$@" >/dev/null 2>&1 || { GEN_MSG="the generator '$*' exited $?"; return 1; }; }
regen_core() {
  [ -f scripts/bundle_runtime.py ] && [ -d bundle ] && { gen_step python3 scripts/bundle_runtime.py || return 1; }
  [ -f scripts/system_doc.py ] && [ -f SYSTEM.md ] && { gen_step python3 scripts/system_doc.py || return 1; }
  for sums in products/*/scripts/checksums.sh; do
    [ -f "$sums" ] || continue
    product=${sums%/scripts/checksums.sh}
    # AGENTS.md: run from the product root after git add, naming the file to write (no argument only prints), and stage
    # again, because the script rewrites on disk
    (cd "$product" && git add -A . && sh scripts/checksums.sh CHECKSUMS.sha256 >/dev/null 2>&1 && git add -A .) \
      || { GEN_MSG="$sums exited non zero"; return 1; }
  done
  return 0
}
# verify_products: each product's own verify-install.sh (no argument, from the product root) over the regenerated and
# staged tree; a product whose checksums.sh exists but whose verify-install.sh does not is red, never skipped
verify_products() {
  for sums in products/*/scripts/checksums.sh; do
    [ -f "$sums" ] || continue
    product=${sums%/scripts/checksums.sh}
    [ -f "$product/scripts/verify-install.sh" ] || { GEN_MSG="$product has no scripts/verify-install.sh to check its manifest"; return 1; }
    (cd "$product" && sh scripts/verify-install.sh >/dev/null 2>&1) || { GEN_MSG="$product/scripts/verify-install.sh failed after regeneration"; return 1; }
  done
  return 0
}
# resolve_generated: called inside a conflicted merge; 0 only when every conflicted path was generated, regenerated,
# checked, staged with everything the generators wrote, and committed, else 1 with WHY set (the caller aborts the merge)
WHY=""
resolve_generated() {
  # NUL separated, then one path per line; a path holding a newline would make the two counts differ, and is refused
  conflicted=$(git diff -z --name-only --diff-filter=U 2>/dev/null | tr '\0' '\n')
  [ -n "$conflicted" ] || { WHY="git failed without a conflicted path"; return 1; }
  [ "$(printf '%s\n' "$conflicted" | wc -l)" -eq "$(git diff --name-only --diff-filter=U 2>/dev/null | wc -l)" ] \
    || { WHY="a conflicted path holds a newline or a quote"; return 1; }
  while IFS= read -r path; do
    is_generated "$path" || { WHY="$path is not a generated file"; return 1; }
    owner_of "$path" || { WHY="no generator here owns $path"; return 1; }
  done <<EOF
$conflicted
EOF
  # baseline: our side only so the generators read parseable files; they overwrite it and --check proves the result
  while IFS= read -r path; do
    git checkout --ours -- "$path" >/dev/null 2>&1 || { WHY="$path could not be reset before regeneration"; return 1; }
  done <<EOF
$conflicted
EOF
  regen_core || { WHY="$GEN_MSG"; return 1; }
  git add -A >/dev/null 2>&1 || { WHY="the regenerated files could not be staged"; return 1; }
  [ -f scripts/bundle_runtime.py ] && [ -d bundle ] && { python3 scripts/bundle_runtime.py --check >/dev/null 2>&1 \
    || { WHY="bundle_runtime.py --check failed after regeneration"; return 1; }; }
  [ -f scripts/system_doc.py ] && [ -f SYSTEM.md ] && { python3 scripts/system_doc.py --check >/dev/null 2>&1 \
    || { WHY="system_doc.py --check failed after regeneration"; return 1; }; }
  verify_products || { WHY="$GEN_MSG"; return 1; }
  # the commit must hold exactly what was checked: nothing modified or untracked may be left outside the index
  [ -z "$(git diff --name-only 2>/dev/null)$(git ls-files --others --exclude-standard 2>/dev/null)" ] \
    || { WHY="the checks left files outside the merge commit"; return 1; }
  git commit -q --no-edit >/dev/null 2>&1 || { WHY="the merge commit could not be made"; return 1; }
  return 0
}

# wt_tree: the tree of the whole checkout (tracked and untracked, not ignored) without touching the real index
wt_tree() {
  gd=$(git rev-parse --git-dir) || return 1
  tmpi=$(mktemp "${TMPDIR:-/tmp}/mwidx.XXXXXX") || return 1
  cp "$gd/index" "$tmpi" 2>/dev/null
  GIT_INDEX_FILE=$tmpi git add -A >/dev/null 2>&1 && GIT_INDEX_FILE=$tmpi git write-tree 2>/dev/null
  rc=$?; rm -f "$tmpi"; return $rc
}
# undo_refused PRE B0: take a refused merge back WITHOUT discarding anything the window did not create. The window created
# only (a) the merge in progress and (b) what the generators wrote, which are generated paths. So it undoes only if HEAD is
# still PRE and every path that changed since B0 (the checkout's tree right after the merge failed) is a generated path;
# otherwise it changes nothing and says why in UNDO (return 1). Undo: merge --abort, then restore or remove the generated
# paths that are left. A concurrent actor writing a GENERATED path is indistinguishable from a generator and is undone.
UNDO=""
undo_refused() {
  pre=$1; b0=$2
  [ -n "$b0" ] || { UNDO="the checkout could not be fingerprinted"; return 1; }
  [ "$(git rev-parse HEAD 2>/dev/null)" = "$pre" ] || { UNDO="HEAD is no longer $pre, someone else moved it"; return 1; }
  now=$(wt_tree) || { UNDO="the checkout could not be fingerprinted"; return 1; }
  foreign=$(git diff-tree -r --no-renames --name-only "$b0" "$now" 2>/dev/null | while IFS= read -r changed; do
    is_generated "$changed" || printf '%s ' "$changed"; done)
  [ -z "$foreign" ] || { UNDO="these paths changed since the window began and are not generated files: $foreign"; return 1; }
  if [ -f "$(git rev-parse --git-path MERGE_HEAD)" ]; then git merge --abort >/dev/null 2>&1; fi
  git diff --name-only 2>/dev/null | while IFS= read -r changed; do
    if is_generated "$changed"; then GIT_LITERAL_PATHSPECS=1 git checkout HEAD -- "$changed" >/dev/null 2>&1; fi; done
  git ls-files --others --exclude-standard 2>/dev/null | while IFS= read -r changed; do
    if is_generated "$changed"; then rm -f -- "$changed"; fi; done
  [ "$(git rev-parse HEAD 2>/dev/null)" = "$pre" ] && [ -z "$(git status --porcelain 2>/dev/null)" ] \
    || { UNDO="the undo left the checkout different from $pre"; return 1; }
  return 0
}

# (1) MERGE, in the order named
merged=""
for pair in "$@"; do
  unit=${pair%%=*}; ref=${pair#*=}
  pre=$(git rev-parse HEAD)
  if ! git merge -q --no-ff --no-edit -m "merge window: $unit from $ref" "$ref" >/dev/null 2>&1; then
    b0=$(wt_tree)
    if resolve_generated; then
      say "$unit GENERATED-CONFLICT $ref: regenerated, checked and committed"
    else
      if undo_refused "$pre" "$b0"; then
        say "$unit MERGE-CONFLICT $ref: aborted; merged before it:${merged:- none}; not auto-resolved: $WHY"
      else
        say "$unit MERGE-STOPPED $ref: NOT undone, the merge is left as it is: $UNDO; the merge was refused because: $WHY; read git status, then git merge --abort yourself"
      fi
      exit 2
    fi
  fi
  merged="$merged $unit"
  eval "sha_$(printf %s "$unit" | tr '.-' '__')=\$(git rev-parse --short=12 HEAD)"
done

# (2) REGENERATE what this tree ships (a generator runs where its output lives; a red generator stops the window, the
# merges stay committed), then commit the generated files only when they changed
regen_core || { say "FAIL: $GEN_MSG; the merges stay committed, nothing was closed"; exit 1; }
gen=${MW_PLAN_GEN:-docs/plan/specs/1.1.7/plan_v117.py}
[ -f "$gen" ] && { gen_step python3 "$gen" || { say "FAIL: $GEN_MSG; the merges stay committed, nothing was closed"; exit 1; }; }
if [ -n "$(git status --porcelain)" ]; then
  git add -A && git commit -q -m "merge window:$units: the generated files regenerated after the merges"
fi

# (3) THE DONE CHECKS on both interpreters, and (4) THE CLOSE, one line per unit
pass=1
for unit in $units; do
  steps=$(python3 -c 'import json, os, sys
sys.path.insert(0, os.path.join("scripts", "loop"))
from plan_lint import screen_done_check
plan = json.load(open(sys.argv[1]))
unit = next(u for u in plan["units"] if u.get("id") == sys.argv[2])
steps = screen_done_check((unit.get("done_check") or "").strip())
print("\n".join("\t".join(s) for s in steps) if steps else "")' "$BROTHER_WBS" "$unit") || steps=""
  codes=""
  for py in python3 "$PY39"; do
    label=py3; [ "$py" = python3 ] || label=py3.9
    if [ -z "$steps" ]; then rc="NO-DATA(no executable done check)"
    elif ! command -v "$py" >/dev/null 2>&1; then rc="NO-DATA($py missing)"
    else
      rc=0
      # one screened argv per line, tab separated, python3 as its first word; the interpreter column replaces it
      while IFS="$TAB" read -r first rest; do
        # shellcheck disable=SC2086
        set -f; IFS="$TAB"; set -- $rest; IFS=$KEEP_IFS; set +f
        "$py" "$@" >/dev/null 2>&1 || { rc=$?; break; }
      done <<EOF
$steps
EOF
    fi
    codes="$codes $label=$rc"
    [ "$rc" = 0 ] || pass=0
  done
  out=$(python3 scripts/close_unit.py "$unit" 2>&1)
  line=$(printf '%s\n' "$out" | grep -E "^$unit +(DONE|NOT CLOSED|NEEDS_INPUT|not )" | tail -1)
  # DONE only on the closer's own token: the unit id (stripped as a literal string, never a glob or a regex), its spaces,
  # then "DONE: " at the START of what is left. A line that merely CONTAINS " DONE: " (a quoted check line such as
  # "NOT CLOSED: ... NO-DATA: x DONE: y") is a spoof; NEEDS_INPUT, NOT CLOSED, "not closable" and anything else read NOT DONE.
  rest=${line#"$unit"}; rest=${rest#"${rest%%[! ]*}"}
  case "$rest" in "DONE: "*) verdict="DONE";; *) verdict="NOT DONE: ${line#"$unit"}"; pass=0;; esac
  [ -n "$line" ] || verdict="NOT DONE: close_unit said: $(printf '%s\n' "$out" | tail -1)"
  eval "sha=\${sha_$(printf %s "$unit" | tr '.-' '__')}"
  say "$unit merged $sha$codes $verdict"
done
if [ -n "$(git status --porcelain)" ]; then
  git add -A && git commit -q -m "merge window:$units: closed by scripts/close_unit.py"
fi
if [ "$pass" = 1 ]; then
  say "PASS: merge window,$units DONE at $(git rev-parse --short=12 HEAD); start the gate (sh scripts/gate_pair.sh)"
  exit 0
fi
say "FAIL: merge window,$units: not every unit is DONE and green on both interpreters; the gate does not start"
exit 1
