#!/bin/bash
# Stop everything the loop started, and PROVE nothing is left. The one place the kill pattern lives.
#
# WHY (measured 2026-09-22 08:06). The driver and every runner had been stopped, and 15 processes were still alive and
# still spending: or_fanout and its or_ask bridge calls, some from a run two restarts earlier. The full pattern existed,
# but only inside loop_until.sh's DEADLINE branch; the restart script and a hand stop each carried a narrower copy that
# named unit_runner alone. unit_runner starts its fan out, and bounded.py starts grade, probe and check, each in its
# OWN session so a timeout can kill them without killing the runner, which also means killing the runner's group does
# not reach them. A control that lives in one caller is lost by every other caller, so every kill site calls this file.
#
# usage: stop_loop.sh [--runners-only]     default also stops the driver (TERM, so its trap frees lease and claim)
#        stop_loop.sh --drain [secs]        PAUSE and wait for runners to leave at their checkpoints; kills nothing
#        stop_loop.sh --dry                 kills NOTHING: lists what is alive, exit 1 when anything is, 0 when nothing is
#        stop_loop.sh --pause "<why>"       kills NOTHING: writes ~/.claude/evidence/LOOP-PAUSE.txt; the driver runs no new pass
#        stop_loop.sh --resume              removes that file; the next pass runs from the state on disk
#        stop_loop.sh [--reason "<why>"]    before a driver is signalled, "<pid> <why>" goes into LOOP-STOP-REQUEST.txt, so
#                                           the driver ends STOPPED with that reason instead of INTERRUPTED (finding 19)
# exit 0 only when NOTHING matching is alive afterwards; exit 1 names what survived. STOP_LOOP_ONLY=<text> limits the
# kill to command lines containing that text: the test uses it so a test run can never touch a real loop.
set -u
set -o pipefail
# AN ARGUMENT THIS SCRIPT DOES NOT KNOW IS REFUSED BEFORE ANYTHING IS STOPPED. --help, -h and --dry-run used to fall
# through to the default, which stops the driver and every runner of a live loop (the sixth time a help flag acted
# on this estate). Help prints the header and exits 0; anything else unknown exits 2 having touched nothing.
case "${1:-}" in
  -h|--help) sed -n '2,19p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
  ""|--runners-only|--drain|--pause|--resume|--dry|--reason) ;;
  *) echo "stop_loop.sh: unknown argument '$1': nothing was stopped. Run it with --help for the usage." >&2; exit 2 ;;
esac
# Seconds given to a driver's own clean end (proof-ending, settle, runners stopped, end clock, end snapshot, receipt)
# after TERM before a KILL. Measured 2026-09-27: that clean end takes 9.8-10.0s at load about 10, so the old 12s wait
# could KILL mid end and lose the receipt or the end snapshot. Overridable only so tests can set it small; a real stop
# always uses the default.
STOP_DRIVER_GRACE="${STOP_DRIVER_GRACE:-30}"
PAUSE_FILE=~/.claude/evidence/LOOP-PAUSE.txt
case "${1:-}" in
  --pause)  printf '%s\n' "${2:-paused by hand} at $(date '+%Y-%m-%d %H:%M:%S %Z')" > "$PAUSE_FILE"; echo "PAUSED: the driver runs no new pass while $PAUSE_FILE exists; runners in flight finish their round"; exit 0;;
  --resume) if [ -e "$PAUSE_FILE" ]; then rm -f "$PAUSE_FILE"; echo "RESUMED: the next pass runs from the state on disk"; else echo "not paused: no $PAUSE_FILE"; fi; exit 0;;
esac
# WHICH processes are the loop's is decided in ONE place, loop_procs.py, over ONE snapshot (finding 1b): a loop tool by its
# name AND only when it belongs to a run of this loop (its program in $HOME/.claude/bin, or its own environment naming a
# BROTHER_RUN_DIR under the runs root; 2026-09-29 18:00 a test fixture's copy of unit_runner.py read as a live runner and
# the deadline stop printed STOP INCOMPLETE), a generic model CLI (claude -p, codex exec, or_ask, or_fanout) only when its
# parent chain or process group leads to such a tool. An unrelated session's model CLI, a loop tool of no run of this
# loop, and an unknown owner, are never signalled.
HERE=$(cd "$(dirname "$0")" && pwd)
ONLY="${STOP_LOOP_ONLY:-}"
DRY=0; case " $* " in *" --dry "*) DRY=1;; esac
KIND=all; [ "${1:-}" = "--runners-only" ] && KIND=runners
procs() {                       # procs <driver|runners|all>: "pid command" lines; nonzero when ownership cannot be read
  python3 "$HERE/loop_procs.py" "$1" --only "$ONLY" --self "$$"
}
alive_same() {                  # stdin "pid command" lines whose pid still runs the SAME command (a reused pid is never hit)
                                 # SO28 finding 9: ps -p exits nonzero with NOTHING on any stream when the pid is
                                 # simply gone (the ordinary, expected case, dropped in silence, never an error); ANY
                                 # OTHER nonzero outcome (ps itself printed something, on stdout or stderr) means ps
                                 # could not be trusted for this pid and must never be read as "gone" instead. The
                                 # old `ps ... | sed ...` pipeline reported sed's own exit code, always 0, which
                                 # masked exactly this distinction; merging streams here removes the pipe entirely.
  bad=0
  while read -r p c; do
    [ -n "$p" ] || continue
    now=$(ps -ww -o command= -p "$p" 2>&1)
    rc=$?
    if [ "$rc" -ne 0 ]; then
      [ -z "$now" ] && continue    # gone: not an error
      bad=1; continue              # ps itself failed for this pid: unreadable
    fi
    now=$(printf '%s' "$now" | sed 's/^ *//;s/ *$//')
    [ -n "$now" ] && [ "$now" = "$c" ] && printf '%s %s\n' "$p" "$c"
  done
  [ "$bad" = 0 ]
}
signal_lines() {                # signal_lines <SIG> <lines>: the group first, ONLY when the pid itself leads that
                                 # group (SO28 finding 4: p's own pgid, read fresh here rather than trusted from an
                                 # earlier snapshot, must equal p; otherwise p's pgid names some OTHER, unrelated
                                 # group by numeric coincidence, and a negative-pid kill would signal a sibling in it
                                 # that was never selected, instead of or besides p). Any pid whose own pgid cannot
                                 # be read this way falls back to the plain, single-pid form, never a guess.
  [ "$DRY" = 1 ] && return 0
  printf '%s\n' "$2" | awk 'NF{print $1}' | while read -r p; do
    pg=$(ps -o pgid= -p "$p" 2>/dev/null | tr -d ' ')
    if [ -n "$pg" ] && [ "$pg" = "$p" ]; then
      kill "-$1" -- "-$p" 2>/dev/null || kill "-$1" "$p" 2>/dev/null
    else
      kill "-$1" "$p" 2>/dev/null
    fi
  done
}
# DRAIN, NEVER KILL, FOR A PLANNED STOP (plan E step 1, 2026-10-01). 106 of the 134 live "STALLED dead runner" runs on
# this machine were marked within 30 minutes of a driver start: a deploy or restart killed runners mid round. A runner
# already stops cleanly at its next hold_gate when the pause file exists (status PAUSED, outputs kept, re-seated on
# resume), so a planned stop pauses and WAITS for that instead of signalling. Nothing is killed here, ever: a drain that
# runs out of time says DRAIN INCOMPLETE, names what is still alive and leaves the loop PAUSED for the caller to decide.
# usage: stop_loop.sh --drain [max seconds, default 1800] [--reason "<why>"]
if [ "${1:-}" = "--drain" ]; then
  case "${2:-}" in ''|--*) MAX=1800;; *) MAX="$2";; esac
  case "$MAX" in *[!0-9]*|'') echo "NO-DATA: --drain takes whole seconds, got '$MAX'; nothing was paused"; exit 2;; esac
  WHY="drain"; _prev=""; for _a in "$@"; do [ "$_prev" = "--reason" ] && WHY="$_a"; _prev="$_a"; done
  printf '%s\n' "$WHY at $(date '+%Y-%m-%d %H:%M:%S %Z')" > "$PAUSE_FILE" || { echo "NO-DATA: the pause file could not be written; nothing was paused"; exit 2; }
  waited=0
  while :; do
    if ! LEFT=$(procs runners); then echo "NO-DATA: the runners could not be read; the loop is PAUSED and nothing was signalled"; exit 2; fi
    [ -z "$(printf '%s' "$LEFT" | tr -d '[:space:]')" ] && { echo "DRAINED: no runner is alive after ${waited}s; the loop is PAUSED; nothing was killed"; exit 0; }
    [ "$waited" -ge "$MAX" ] && { echo "DRAIN INCOMPLETE after ${waited}s: still alive:"; printf '%s\n' "$LEFT" | sed 's/^/  /'; echo "the loop is PAUSED; nothing was killed"; exit 1; }
    sleep 5 & wait $!; waited=$((waited + 5))
  done
fi
# Taken BEFORE any signal: once the driver dies its children are reparented and their chain to it is gone, so the set
# selected now is kept and re-checked (same pid, same command) at every later step.
if ! INITIAL=$(procs "$KIND"); then echo "NO-DATA: the loop's processes could not be read; nothing was signalled"; exit 2; fi
owned_now() {                   # SO28 finding 5: the FIRST read already fails closed (just above); a LATER read
                                 # failing must refuse exactly the same way. `procs "$KIND" || true` used to mask
                                 # this one, letting the script proceed on stale data and print STOPPED while blind.
                                 # Callers check this function's own exit status; a caller that does not is the bug.
  if ! later=$(procs "$KIND"); then
    return 1
  fi
  { printf '%s\n' "$INITIAL"; printf '%s\n' "$later"; } | alive_same | sort -u
}
REASON="stopped by stop_loop.sh"
_prev=""; for _a in "$@"; do [ "$_prev" = "--reason" ] && REASON="$_a"; _prev="$_a"; done
if [ "$KIND" = all ]; then
  DRIVERS=$(procs driver) || { echo "NO-DATA: the driver could not be read; nothing was signalled"; exit 2; }
  if [ "$DRY" != 1 ]; then
    printf '%s\n' "$DRIVERS" | awk 'NF{print $1, r}' r="$REASON" > ~/.claude/evidence/LOOP-STOP-REQUEST.txt 2>/dev/null \
      || echo "NOTE: the stop request could not be written; the driver will end INTERRUPTED rather than STOPPED"
  fi
  signal_lines TERM "$DRIVERS"
  if [ "$DRY" != 1 ]; then
    waited=0
    while [ "$waited" -lt "$STOP_DRIVER_GRACE" ]; do
      [ -z "$(printf '%s\n' "$DRIVERS" | alive_same)" ] && break
      sleep 2; waited=$((waited + 2))
    done
  fi
  signal_lines KILL "$(printf '%s\n' "$DRIVERS" | alive_same)"
fi
if ! NOW1=$(owned_now); then echo "NO-DATA: a later process-table read failed; nothing more was signalled"; exit 2; fi
signal_lines TERM "$NOW1"
[ "$DRY" = 1 ] || sleep 3
if ! NOW2=$(owned_now); then echo "NO-DATA: a later process-table read failed; nothing more was signalled"; exit 2; fi
signal_lines KILL "$NOW2"
[ "$DRY" = 1 ] || sleep 1
if ! LEFT_LINES=$(owned_now); then echo "NO-DATA: a later process-table read failed; STOPPED cannot be confirmed"; exit 2; fi
LEFT=$(printf '%s\n' "$LEFT_LINES" | awk 'NF' | wc -l | tr -d ' ')
if [ "$LEFT" -ne 0 ]; then
  echo "STOP INCOMPLETE: $LEFT process(es) still alive:"; printf '%s\n' "$LEFT_LINES" | awk 'NF' | cut -c1-400; exit 1
fi
echo "STOPPED: nothing of the loop is alive"; exit 0
