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
set -u
PY39=${MW_PY39:-/usr/bin/python3}
TAB=$(printf '\t'); KEEP_IFS=$IFS
say() { printf '%s\n' "$*"; }
refuse() { say "REFUSED: $*"; exit 2; }

[ $# -gt 0 ] || refuse "name at least one UNIT=REF"
[ -n "${BROTHER_WBS:-}" ] || refuse "set BROTHER_WBS to the plan the units belong to"
[ -f "$BROTHER_WBS" ] || refuse "the plan $BROTHER_WBS is not a file here"
git symbolic-ref -q HEAD >/dev/null || refuse "HEAD is detached; check out the branch that receives the merges"
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

# (1) MERGE, in the order named
merged=""
for pair in "$@"; do
  unit=${pair%%=*}; ref=${pair#*=}
  if ! git merge -q --no-ff --no-edit -m "merge window: $unit from $ref" "$ref" >/dev/null 2>&1; then
    git merge --abort >/dev/null 2>&1
    say "$unit MERGE-CONFLICT $ref: aborted; merged before it:${merged:- none}"
    exit 2
  fi
  merged="$merged $unit"
  eval "sha_$(printf %s "$unit" | tr '.-' '__')=\$(git rev-parse --short=12 HEAD)"
done

# (2) REGENERATE what this tree ships (a generator runs where its output lives; a red generator stops the window, the
# merges stay committed), then commit the generated files only when they changed
regen() { say "REGENERATE $*"; "$@" >/dev/null 2>&1 || { say "FAIL: the generator '$*' exited $?; the merges stay committed, nothing was closed"; exit 1; }; }
[ -f scripts/bundle_runtime.py ] && [ -d bundle ] && regen python3 scripts/bundle_runtime.py
[ -f scripts/system_doc.py ] && [ -f SYSTEM.md ] && regen python3 scripts/system_doc.py
for sums in products/*/scripts/checksums.sh; do
  [ -f "$sums" ] || continue
  product=${sums%/scripts/checksums.sh}
  # AGENTS.md: run from the product root after git add, and stage again, because the script rewrites on disk
  (cd "$product" && git add -A . && sh scripts/checksums.sh >/dev/null && git add -A .) \
    || { say "FAIL: $sums exited non zero; the merges stay committed, nothing was closed"; exit 1; }
done
gen=${MW_PLAN_GEN:-docs/plan/specs/1.1.7/plan_v117.py}
[ -f "$gen" ] && regen python3 "$gen"
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
  line=$(printf '%s\n' "$out" | grep -E "^$unit +(DONE|NOT CLOSED|not )" | tail -1)
  case "$line" in *" DONE: "*) verdict="DONE";; *) verdict="NOT DONE: ${line#"$unit"}"; pass=0;; esac
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
