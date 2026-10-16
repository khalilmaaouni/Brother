#!/bin/sh
# gate_pair.sh: the whole gate (sh scripts/required_fast.sh) on the exact committed tree under BOTH
# interpreters at the same time (unit SP6, owner order 2026-10-16). Run one after the other the pair took
# 15 m 14 s on 2026-10-16; side by side it costs about one run.
#
#   sh scripts/gate_pair.sh [--keep-worktree]
#
# python3 on PATH runs the gate in this worktree; the second interpreter (/usr/bin/python3) runs it in a
# detached sibling worktree at the same commit, under <git common dir>/gate-pair/<worktree name>-<cksum>,
# because required_fast.sh rewrites docs/plan/READINESS-BOARD.html and two runs never share a tree. Each run
# gets REQUIRED_FAST_JOBS=2 (the heavy slot weight, so the pair fits scripts/heavy_slot.py's cores/2 pool)
# and its own TMPDIR. The sibling is removed afterwards unless --keep-worktree.
#
# Prints one line per interpreter: GATE py3.X rc=N rows=N bad=[VERDICT name;...]. Exit 0 only when both
# gates exited 0 with no FAIL, NO-DATA or SKIPPED row; 1 on any red; 2 (NO-DATA, never a pass) when the tree
# is dirty, an interpreter is missing or answers the same version as the other, or a run printed no rows.
#
# Test seams only: GATE_PAIR_GATE (run in place of `sh scripts/required_fast.sh`), GATE_PAIR_PY39 (the
# second interpreter, an absolute path). Footprint: one mktemp directory under $TMPDIR holding both outputs
# and both gate TMPDIRs, kept as evidence and named on the first line; one sibling worktree.
# ponytail: a TERM kills the two direct children only; heavy_slot's own child may outlive it.

usage="usage: sh scripts/gate_pair.sh [--keep-worktree]"
keep=0
for a in "$@"; do
  case "$a" in
    --keep-worktree) keep=1 ;;
    -h|--help) echo "$usage"; exit 0 ;;
    *) echo "GATE REFUSED: unknown argument $a; $usage" >&2; exit 2 ;;
  esac
done

refuse() { echo "GATE REFUSED: $*" >&2; exit 2; }

cd "$(dirname "$0")/.." || refuse "cannot enter the worktree root"
root=$(pwd -P)
gate_cmd=${GATE_PAIR_GATE:-sh scripts/required_fast.sh}
py39=${GATE_PAIR_PY39:-/usr/bin/python3}
case "$py39" in /*) ;; *) refuse "GATE_PAIR_PY39 must be an absolute path, got '$py39'" ;; esac

sha=$(git rev-parse --verify -q HEAD) || refuse "no commit at HEAD in $root"
dirty=$(git status --porcelain) || refuse "git status failed in $root"
if [ -n "$dirty" ]; then
  echo "GATE REFUSED: tree not clean in $root, so the gate would not run on the commit it names:" >&2
  printf '%s\n' "$dirty" | head -20 >&2
  exit 2
fi

probe='import sys; print("%d.%d" % sys.version_info[:2])'
va=$(python3 -c "$probe" 2>/dev/null)
vb=$("$py39" -c "$probe" 2>/dev/null)
case "$va" in [0-9]*.[0-9]*) ;; *) refuse "python3 on PATH did not answer its version" ;; esac
case "$vb" in [0-9]*.[0-9]*) ;; *) refuse "second interpreter $py39 did not answer its version" ;; esac
[ "$va" != "$vb" ] || refuse "python3 and $py39 are both $va, so the pair would prove one interpreter twice"

common=$(cd "$(git rev-parse --git-common-dir)" && pwd -P) || refuse "cannot read the git common dir"
key="$(basename "$root")-$(printf '%s' "$root" | cksum | cut -d' ' -f1)"
mkdir -p "$common/gate-pair" || refuse "cannot create $common/gate-pair"
sib="$common/gate-pair/$key"
lock="$sib.lock"

# One gate_pair per worktree at a time: a second would checkout -f under the first one's running gate.
if ! mkdir "$lock" 2>/dev/null; then
  holder=$(cat "$lock/pid" 2>/dev/null)
  if [ -z "$holder" ] || kill -0 "$holder" 2>/dev/null; then
    refuse "another gate_pair holds $lock (pid ${holder:-unknown}); if none runs, remove that directory"
  fi
  rm -rf "$lock" && mkdir "$lock" 2>/dev/null || refuse "could not take over the stale lock $lock"
fi
echo $$ > "$lock/pid"
pa=""; pb=""
trap 'rm -rf "$lock"' EXIT
trap '[ -n "$pa" ] && kill "$pa" 2>/dev/null; [ -n "$pb" ] && kill "$pb" 2>/dev/null; exit 143' INT TERM

if [ -e "$sib" ]; then
  git worktree list --porcelain | grep -qxF "worktree $sib" \
    || refuse "$sib exists but is not a worktree of this repository; move it away"
else
  err=$(git worktree add -q -f --detach "$sib" "$sha" 2>&1) || refuse "could not create $sib: $err"
fi
err=$(git -C "$sib" checkout -q --detach -f "$sha" 2>&1) || refuse "could not check out $sha in $sib: $err"
left=$(git -C "$sib" status --porcelain)
[ -z "$left" ] || refuse "the sibling $sib carries files outside the commit ($(printf '%s' "$left" | head -3 | tr '\n' ' ')); it is never cleaned by force, move it away"

scratch=$(mktemp -d "${TMPDIR:-/tmp}/gate-pair.XXXXXX") || refuse "mktemp failed under ${TMPDIR:-/tmp}"
mkdir "$scratch/tmp-py$va" "$scratch/tmp-py$vb" "$scratch/bin-py$vb" \
  && ln -s "$py39" "$scratch/bin-py$vb/python3" || refuse "could not lay out $scratch"
oa="$scratch/py$va.txt"; ob="$scratch/py$vb.txt"
echo "gate_pair: sha $sha; py$va in $root; py$vb in $sib; full outputs in $scratch"

(cd "$root" && export REQUIRED_FAST_JOBS=2 TMPDIR="$scratch/tmp-py$va" && exec sh -c "$gate_cmd") > "$oa" 2>&1 &
pa=$!
(cd "$sib" && export REQUIRED_FAST_JOBS=2 TMPDIR="$scratch/tmp-py$vb" PATH="$scratch/bin-py$vb:$PATH" \
   && exec sh -c "$gate_cmd") > "$ob" 2>&1 &
pb=$!
wait "$pa"; ra=$?
wait "$pb"; rb=$?
pa=""; pb=""

board=docs/plan/READINESS-BOARD.html
for t in "$root" "$sib"; do
  if git -C "$t" ls-files --error-unmatch "$board" >/dev/null 2>&1; then
    git -C "$t" checkout -q -- "$board" || echo "NOTE: could not restore $board in $t"
  fi
done

worst=0
for v in "$va $ra $oa" "$vb $rb $ob"; do
  set -- $v
  rows=$(grep -cE '^(PASS|FAIL|NO-DATA|SKIPPED) ' "$3" 2>/dev/null) || rows=0
  bad=$(grep -E '^(FAIL|NO-DATA|SKIPPED) ' "$3" 2>/dev/null | awk '{print $1" "$4}' | paste -sd';' -)
  echo "GATE py$1 rc=$2 rows=$rows bad=[$bad]"
  if [ "$rows" -eq 0 ]; then
    echo "NO-DATA: py$1 printed no gate rows (full output: $3)"
    worst=2
  elif [ "$2" -ne 0 ] || [ -n "$bad" ]; then
    [ "$worst" -eq 2 ] || worst=1
  fi
done

if [ "$keep" -eq 0 ]; then
  git worktree remove "$sib" 2>/dev/null || echo "NOTE: kept $sib (git refused a plain removal); the next run reuses it"
fi
exit "$worst"
