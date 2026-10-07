#!/bin/sh
# gate_merge_seq.sh [list]: gate every pinned head in list order, before the
# owner arrives.
#
# For each "<number> <sha>" line this merges the pinned head into a scratch
# worktree taken at hub main, runs `sh scripts/required_fast.sh` from that
# worktree, and records ONE signed ledger row through `merge_gate.py gate`. Gating is a READ: this tool
# never writes main.
#
# The gate script's own hash is recorded at hub main AND at the candidate
# tree, so a row whose gate script differs from main's can be refused later.
#
# Every default path comes from this script's own directory or from the
# clone's git common dir. Nothing here points into a home folder.
set -u
# EXPORTED FUNCTIONS IMPORTED BY /bin/sh (bash 3.2) OUTRANK BUILTINS: drop any for the names this tool relies on before
# anything runs (review round 21, 2026-10-04). unset is a special builtin, which no function can replace.
# '[' is no valid identifier in posix mode (measured, review round 22), so posix is left for that one unset; unset, set and
# builtin go first while posix mode still makes the special builtins outrank any function.
unset -f unset set builtin 2>/dev/null; set +o posix 2>/dev/null
unset -f '[' true false cd pwd command printf test echo read export shift exit eval exec trap umask type hash wait 2>/dev/null
set -o posix 2>/dev/null || true
# PINNED TOOLS (review round 19, 2026-10-04): python3 and git by absolute system path, so a PATH entry ahead of them
# cannot answer for the gate. Functions, so every later call (and every subshell) goes through them.
PY=/usr/bin/python3; GIT=/usr/bin/git
{ [ -x "$PY" ] && [ -x "$GIT" ]; } || { echo "NO-DATA: /usr/bin/python3 or /usr/bin/git is missing"; exit 2; }
python3() { "$PY" "$@"; }
git() { "$GIT" "$@"; }
# HERE from shell builtins only (review round 20: a dirname earlier on PATH could point HERE, and with it the gate, the
# clone and the fixture marker, at a directory it controls)
case "$0" in */*) _here_dir="${0%/*}" ;; *) _here_dir=. ;; esac
HERE="$(cd "$_here_dir" && pwd -P)"
PATH=/usr/bin:/bin:/usr/sbin:/sbin; export PATH   # every lookup below is the system's (review round 20)
PY_GATE="$HERE/merge_gate.py"
LIST="${1:-}"
[ -n "$LIST" ] || { echo "NO-DATA: no list given"; exit 2; }
[ -r "$LIST" ] || { echo "NO-DATA: no list at $LIST"; exit 2; }

CLONE="${MERGE_CLONE:-$(cd "$HERE/.." && git rev-parse --show-toplevel 2>/dev/null || true)}"
[ -n "$CLONE" ] || { echo "NO-DATA: cannot find the clone; set MERGE_CLONE"; exit 2; }
REMOTE="${MERGE_REMOTE:-hub}"
BRANCH="${MERGE_BASE_BRANCH:-main}"

if [ "${MERGE_GATE_DRY_RUN:-0}" != "1" ]; then
  reason="$(python3 "$PY_GATE" refuse-env 2>&1)" && guard_rc=0 || guard_rc=$?
  if [ "${guard_rc:-0}" -ne 0 ]; then
    echo "NO-DATA: steered environment refused: $reason"
    exit 2
  fi
fi

# MG1.c: this script is the FOREGROUND WORKER merge_precompute.start_background spawns detached (and the one a person
# runs by hand). Every guard above runs first; then the process becomes the Python worker, which gates each pin in list
# order on the chained tree (main plus PR 1, then plus PR 2), skips a tree that already has a valid PASS row, stops at
# the first FAIL, restarts from a moved main, and writes its progress beside the ledger. Rows are written only through
# `merge_gate.py gate` (merge_gate.run_gate): this tool types no rc. The gate script's hash at hub main and at the
# candidate (the main_gate_sha and cand_gate_sha of the old TSV row, which was
#   printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' tree rc when pr head main_gate_sha cand_gate_sha
# and which merge_gate.py import_legacy_tsv reads and verify_row always refuses) now live in the row as gate_sha256,
# refused as GATE-EDITED when scripts/required_fast.sh differs from main's.
PY_PRE="$HERE/merge_precompute.py"
[ -r "$PY_PRE" ] || { echo "NO-DATA: no worker at $PY_PRE"; exit 2; }
exec "$PY" "$PY_PRE" worker "$CLONE" "$LIST"
