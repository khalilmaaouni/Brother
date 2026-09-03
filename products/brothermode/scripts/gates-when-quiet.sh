#!/bin/bash
# Wait for this machine to be idle enough to trust a measurement, then run both
# repositories' gate batteries in sequence and leave the verdicts on disk.
#
# WHY THIS EXISTS. On 2026-08-17 four hardenings landed in both runners (a
# credential sandbox, a Python 3.9 floor shim, receipt signing, and a load
# ceiling) and NONE of them had been proven end to end, because the machine sat
# between load 72 and 273 for hours under two iOS simulators belonging to other
# sessions. Each wrapper was proven in isolation and they compose, but the
# battery is the real check, and the new load ceiling correctly refuses to
# measure anything on a saturated machine. That left the proof owed with no
# session awake to collect it.
#
# So this is the collector. It is deliberately dumb: it waits, it runs, it
# records, and it never posts a status or pushes anything. Reporting stays with
# the sanctioned runner invoked by a human or a session that can read the result.
#
# SEQUENCE, NOT PARALLEL. Two batteries at once is what produced load 243 and
# three killed runs in the session that wrote this. One suite at a time is a
# standing cap in both projects and it is honoured here.
#
# USAGE
#   nohup bash scripts/gates-when-quiet.sh > /dev/null 2>&1 &
#   then read the log named below.
set -u

OUT="${GATES_QUIET_LOG:-$HOME/Documents/BrotherArchive/gates-when-quiet.log}"
mkdir -p "$(dirname "$OUT")"
CORES="$(sysctl -n hw.ncpu 2>/dev/null || echo 8)"
CEIL="${GATES_QUIET_CEIL:-$((CORES * 2))}"   # stricter than the runners' own 4x:
                                             # this is unattended, so it waits
                                             # for genuinely quiet rather than
                                             # merely acceptable.
MAX_WAIT_MIN="${GATES_QUIET_MAX_WAIT:-360}"  # give up after 6 hours rather than
                                             # spin forever on a machine that
                                             # never settles.

say() { echo "[$(date -u +%FT%TZ)] $*" >> "$OUT"; }

say "waiting for load below $CEIL on $CORES cores (giving up after ${MAX_WAIT_MIN}m)"
waited=0
while :; do
  L="$(uptime | sed 's/.*averages*: *//' | awk '{print int($1)}')"
  [ -z "$L" ] && L=999
  [ "$L" -lt "$CEIL" ] && break
  if [ "$waited" -ge "$MAX_WAIT_MIN" ]; then
    say "GAVE UP: load still $L after ${waited}m. Nothing ran. This is NO-DATA,"
    say "not a pass: the batteries were never measured."
    exit 2
  fi
  sleep 60
  waited=$((waited + 1))
done
say "settled at load $L after ${waited}m"

# The two repositories, as $HOME-relative defaults rather than one operator's
# absolute paths: this file is tracked in a public repository, and an absolute
# path here published the account name of whoever wrote it. Override either
# one when your checkouts live somewhere else.
for repo in "${GATES_QUIET_SBE_REPO:-$HOME/Documents/BrotherSBE}" \
            "${GATES_QUIET_MODE_REPO:-$HOME/Documents/BrotherModeUp}"; do
  name="$(basename "$repo")"
  cd "$repo" || { say "$name: could not enter, SKIPPED"; continue; }
  if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    say "$name: SKIPPED, tracked files are modified. A battery run against a tree"
    say "$name: somebody is still editing produces confident wrong signals."
    continue
  fi
  sha="$(git rev-parse --short=12 HEAD)"
  say "$name: starting battery at $sha"
  start=$SECONDS
  bash scripts/local-gates.sh --no-post >> "$OUT" 2>&1
  code=$?
  say "$name: battery exit $code after $((SECONDS - start))s at $sha"
  if [ "$code" -eq 0 ]; then
    say "$name: GREEN. The four hardenings are now proven end to end at $sha."
  else
    say "$name: NOT GREEN at $sha. Read the receipt and the log above before"
    say "$name: concluding anything: exit 2 can also mean the run REFUSED to"
    say "$name: measure (load, dirty tree, poison file), which is not a red battery."
  fi
done
say "done. Nothing was posted and nothing was pushed, by design."
