#!/bin/bash
# THE LOOP'S OWNERSHIP LEASE. Nothing but the loop, and the owner, may touch its worktree.
# usage: loop_guard.sh acquire <pid> | renew [<pid>] | release [<pid>] | release --force | check | owner
#
# WHY IT EXISTS, and it cost the owner 1h42m on 2026-09-21. A second session edited this worktree while the
# loop was running. The loop's landing stage correctly refused to commit changes it could not attribute, exited
# BLOCKED, and stopped. That refusal was RIGHT. What was wrong is that an outsider could reach the tree at all,
# and that the resulting stop reached nobody: it was written to a log at 17:22 and read at 19:04.
#
# A lease is not security against a determined actor on the same machine. It is protection against the real
# failure mode, which is a well meaning parallel session writing the same tree by accident. It is checkable, it
# names its holder, and it expires, so a crashed loop does not lock the tree forever.
#
# WHO MAY CHANGE A LEASE, added 2026-09-21 after an adversarial review found acquire() hardened and every other
# verb left open. `release` was `rm -f` with no ownership check, so any process on the machine freed a live
# lease in one command and then took it, and `renew` took no pid at all, so any process kept a stranger's lease
# alive forever. An atomic acquire in front of an unauthenticated release is a door with a good lock and no
# frame. Every verb that CHANGES the lease now names the pid it is acting for, and the default is $PPID, the
# real pid of the shell that invoked this script, so an honest caller passes nothing and a foreign caller
# cannot pretend. Break glass is `release --force`, deliberate and loud, never the default.
set -u
# THE PATH IS A SEAM, like TTL and SKEW immediately below, and for the same reason.
# Measured 2026-09-21: the race test drove this hardcoded path, so it read and wrote the
# REAL machine lease. It left a fixture behind carrying pid=1, which is launchd and
# therefore never looks dead, so the next run raced a holder that could not be reclaimed.
# A test that cannot name its own lease has no choice but to spend the operator's.
# $HOME, not a tilde: a tilde inside ${VAR:-...} is not expanded by the shell.
LEASE=${LOOP_LEASE:-$HOME/.claude/brother-or-dispatch-state/loop.lease}
TTL=${LOOP_LEASE_TTL:-900}          # reported as the age a lease is expected to stay under; NOT an eviction
SKEW=${LOOP_LEASE_SKEW:-120}        # same tolerance loop_heartbeat.py allows before a future stamp is a fault
mkdir -p "$(dirname "$LEASE")"

now() { date +%s; }
read_field() { [ -f "$LEASE" ] && grep -E "^$1=" "$LEASE" 2>/dev/null | head -1 | cut -d= -f2- || true; }

# ALIVE or DEAD, and an unknown answer is ALIVE. `kill -0` alone cannot tell "gone" from "not mine": for a
# process owned by another uid it fails with EPERM, and the old code read that failure as death, so a lease
# held by a LIVE loop under a different uid was judged stale and stolen. Measured 2026-09-21 on this machine:
# `kill -0 1` exits 1 while `ps -p 1 -o pid=` exits 0 and prints 1, so pid 1 read as dead. `ps` answers for
# every uid, and it separates the two cases by exit code: 0 found, 1 no such process, anything else means ps
# itself could not answer. Only a positive "no such process" is death here, because refusing a free lease
# costs one relaunch while stealing a live one costs two drivers writing one tree.
alive() {
  case "${1:-}" in ''|*[!0-9]*) return 0;; esac    # an unreadable pid is never declared dead
  kill -0 "$1" 2>/dev/null && return 0
  ps -p "$1" -o pid= >/dev/null 2>&1
  case "$?" in
    0) return 0;;                                  # ps found it
    1) return 1;;                                  # ps positively reports no such process
    *) return 0;;                                  # ps could not answer: unknown reads ALIVE
  esac
}

# A PID THAT EXISTS IS NOT YOUR HOLDER. Raised by adversarial review 2026-09-22 and it is right: once
# eviction is gated on liveness alone, a holder that genuinely died and whose pid the OS then RECYCLED
# onto an unrelated process is protected FOREVER, because that unrelated process is alive. Pids on this
# machine wrap (`sysctl kern.maxproc`), and every pid is reused after a reboot, so this is not exotic.
# WHAT TIES THE LEASE TO THE PROCESS: the holder's START TIME, read from ps at acquire and stored in the
# lease. A recycled pid is a different process and started at a different moment.
# WHAT IT COSTS, stated: one extra `ps` per decision; `lstart` has one second granularity, so a recycled
# pid that started in the same second as the original is still not distinguished; and a lease written
# before this field existed carries no start, which degrades to bare pid existence. Every one of those
# unknowns fails toward ALIVE, which refuses, because that costs a relaunch and the other way costs the
# tree. Start time was chosen over a token because a token in a world readable file proves only that the
# reader could read it, and over a boot id because a reboot changes every start time anyway.
proc_start() {
  case "${1:-}" in ''|*[!0-9]*) return 0;; esac
  # Unquoted command substitution inside echo: word splitting collapses ps's column padding and
  # every run of internal spaces to one, which is the canonical form worktree_sentry.py also writes.
  # Measured 2026-09-22: without it the shell stored "Mon Sep 22  3:14:15 2026" and python compared
  # "Mon Sep 22 3:14:15 2026", so a LIVE holder read as a recycled pid and its lease was taken.
  echo $(ps -p "$1" -o lstart= 2>/dev/null)
}

# The real liveness question, and the only one acquire and check may ask.
holder_live() {   # <pid> <start recorded in the lease, possibly empty>
  alive "$1" || return 1
  [ -z "${2:-}" ] && return 0                      # nothing recorded: pid existence is all we have
  S=$(proc_start "$1")
  [ -z "$S" ] && return 0                          # ps could not answer: unknown reads ALIVE
  [ "$S" = "$2" ]                                  # a different start means the pid was RECYCLED
}

# A STAMP FROM THE FUTURE IS A FORGERY, NOT A FRESH LEASE. The lease is a plain file at a fixed path and
# anything on the machine can write it. A planted `pid=<any live pid>` with a stamp hours ahead made every
# driver refuse forever, because every age test is `now - at` and a negative age is younger than any limit, so
# the poison never aged out. Our own writer always stamps `now`, so a stamp beyond the clock skew was not
# written by an honest holder. loop_heartbeat.py classify() takes the same position on a future pulse and for
# the same reason: a wrong clock or a hand edited file is a reason to look, never to relax.
future_stamp() {
  case "${1:-}" in ''|*[!0-9]*) return 1;; esac
  [ $(( $1 - $(now) )) -gt "$SKEW" ]
}

case "${1:-check}" in
  acquire)
    # ATOMIC, because the previous version was not and that is the whole job. It read the lease,
    # decided, then wrote: a check then act race with no atomic create anywhere. Probed 2026-09-21
    # by racing eight concurrent acquires against a free lease: EIGHT of eight printed ACQUIRED and
    # none was refused. A mutual exclusion primitive that grants itself to every caller at once is
    # not a weak lock, it is no lock, and it fails at precisely the moment it exists for, which is
    # two drivers starting together.
    #
    # `set -o noclobber` makes `>` fail when the file already exists, and that test and create is
    # one operation in the kernel, so exactly one racer can win it. The subshell keeps noclobber
    # from leaking into the rest of the script.
    PID="${2:?acquire needs the owning pid}"
    write_lease() {
      printf 'pid=%s\nat=%s\nstart=%s\nhost=%s\nbranch=%s\n' "$PID" "$(now)" "$(proc_start "$PID")" \
        "$(hostname -s)" \
        "$(git -C "${BROTHER_LAUNCH_WORKTREE:-$PWD}" branch --show-current 2>/dev/null)"
    }
    # THE RECLAIM GATE, added 2026-09-22 after the reclaim path was measured racing. noclobber makes
    # CREATION atomic and NOTHING ELSE. Eight racers against a lease naming ONE dead holder printed
    # ACQUIRED 3, 3, 2, 3, 2 over five trials, never once 1: A and B both read dead D, A unlinks and
    # creates, B then runs the rm it had ALREADY decided on, deleting A's NEW lease, and creates its
    # own. Both report success. The old race probe never saw this because it only ever raced a FREE
    # lease, which exercises the fast path and never the steal path.
    #
    # WHAT THE FILESYSTEM ACTUALLY GIVES YOU: one atomic exclusive create, spelled `>` under
    # noclobber or `mkdir`. That is all. POSIX has no compare-and-unlink and no rename-if-unchanged,
    # so read, decide and unlink CANNOT be made one operation. They are therefore SERIALISED behind
    # an atomic exclusive create of this directory, so at most one process on the machine is ever
    # deciding to unlink, and it re-verifies death inside.
    # WHAT THAT COSTS, named rather than hidden: a process SIGKILLed between the mkdir and the rmdir
    # leaves the gate behind and every later acquire is refused until `release --force` clears it.
    # That is the deliberate direction. A wedged gate costs relaunches; a gate that freed itself on a
    # timer, or on a liveness guess about its own owner, would be this same read-decide-unlink race
    # one level down, and that costs the tree.
    GATE="$LEASE.reclaim"
    ATTEMPT=0
    while [ "$ATTEMPT" -lt 3 ]; do
      ATTEMPT=$((ATTEMPT + 1))
      if ( set -o noclobber; write_lease > "$LEASE" ) 2>/dev/null; then
        echo "ACQUIRED loop lease for pid ${PID}"; exit 0
      fi
      # The create lost, so a lease file exists, or existed a microsecond ago. Decide whether its
      # holder is real, and NEVER steal on an unreadable answer.
      HOLDER=$(read_field pid); STAMP=$(read_field at); START=$(read_field start)
      case "$STAMP" in *[!0-9]*) STAMP="";; esac   # a corrupt stamp is unreadable, never "old"
      if [ "$HOLDER" = "$PID" ]; then
        write_lease > "$LEASE"; echo "ACQUIRED loop lease for pid ${PID} (already held by it)"; exit 0
      fi
      # AN EMPTY READ IS A RACE, NOT AN ABANDONMENT. This was the cascade that kept the lock
      # broken after the create was made atomic: a racer reading in the instant between another
      # racer's rm and its create sees no holder, concludes the lease is abandoned, removes it
      # too, and wins a create it should have lost. Six of eight still acquired. Reading nothing
      # means look again, never take.
      if [ -z "$HOLDER" ]; then sleep 0.05; continue; fi
      # LIVENESS IS ASKED FIRST, AND A LIVE HOLDER IS NEVER EVICTED FOR ANY REASON AT ALL.
      # Measured 2026-09-22: the future stamp check used to run BEFORE this one and unlink on its
      # own authority, so moving this machine's clock backwards by more than SKEW made an honest
      # live holder's lease look planted, and acquire took the tree from a running process. A
      # backwards clock is a reason to look, never a licence to evict. The two causes of a future
      # stamp, a clock that moved and a file somebody planted, are INDISTINGUISHABLE on disk, so
      # the decision is made on the one fact that is checkable, which is whether the named process
      # is running. Poison recovery is not lost: a planted stamp naming a DEAD pid is still cleared
      # below. Poison naming a LIVE pid now refuses loudly and points at break glass, and that is
      # the trade: automatic recovery from a hand planted line is given up so that no clock fault
      # can ever put two drivers on one tree.
      if holder_live "$HOLDER" "$START"; then
        if future_stamp "$STAMP"; then
          echo "REFUSED: the lease names LIVE pid ${HOLDER} and is stamped $(( STAMP - $(now) ))s in the FUTURE. Either this machine's clock moved backwards or the file was planted, and a live holder is not evicted either way. Use 'release --force' if that process is genuinely gone." >&2
          exit 1
        fi
        # A LIVE HOLDER WITH AN UNREADABLE STAMP IS REFUSED, NOT STOLEN. A half written lease has
        # its pid line and not yet its at line, and the old code read that missing stamp as "past
        # the ttl" and stole the lease from a process that was still writing it. Every unknown here
        # fails towards refusing, because a refused start costs one relaunch and a double start
        # costs two drivers writing one tree.
        if [ -z "$STAMP" ]; then
          echo "REFUSED: the lease names live pid ${HOLDER} and its stamp is not readable yet"; exit 1
        fi
        # A LIVE HOLDER IS NEVER EVICTED, HOWEVER OLD, and this is the correction that made the
        # whole unit honest. The TTL is 900s and loop_until.sh renews ONCE per pass, before the
        # pass runs, while land_batch allows a single build 3000s and a hermetic check 2400s. A
        # legitimate long pass therefore outlived its own lease, the holder read as past the TTL,
        # and a second driver acquired: two drivers on one worktree, which is the exact failure
        # this file exists to prevent, reached by the file's own rule. scripts/writer_lock.py
        # already holds the correct position for the same reason, that a slow landing is not a
        # stuck one, and this now matches it: only a positively DEAD holder is taken over. A TTL
        # cannot distinguish slow from stuck, and guessing wrong in the eviction direction costs
        # the tree while guessing wrong in the refusing direction costs one relaunch.
        echo "REFUSED: the loop lease is held by live pid ${HOLDER}, $(( $(now) - STAMP ))s old"; exit 1
      fi
      # POSITIVELY DEAD is the ONLY condition that authorises an unlink, and the unlink happens
      # inside the gate, never here. A crashed driver must still never lock the tree for the night.
      if ! mkdir "$GATE" 2>/dev/null; then sleep 0.05; continue; fi
      (
        trap 'rmdir "$GATE" 2>/dev/null' EXIT
        # Re-read INSIDE the gate. The holder read above is a fact about a moment that has passed,
        # and acting on it is the whole defect. Everything is decided again from the file as it is
        # now, with nobody else able to unlink while we look.
        H2=$(read_field pid); S2=$(read_field start)
        if [ -z "$H2" ]; then exit 3; fi                          # still a race, never an abandonment
        if [ "$H2" != "$PID" ] && holder_live "$H2" "$S2"; then exit 3; fi    # taken by a live holder while we queued
        rm -f "$LEASE"
        # noclobber HERE TOO, because the gate excludes other RECLAIMERS and not the fast path at
        # the top of this loop. A racer whose plain create lands in the instant after that rm holds
        # the lease honestly, and must not then be silently overwritten by us.
        ( set -o noclobber; write_lease > "$LEASE" ) 2>/dev/null || exit 3
        exit 0
      )
      if [ "$?" -eq 0 ]; then
        echo "ACQUIRED loop lease for pid ${PID} (reclaimed from dead pid ${HOLDER})"; exit 0
      fi
      sleep 0.05
    done
    echo "REFUSED: could not take the lease in ${ATTEMPT} attempts; another driver keeps winning it"; exit 1;;
  renew)
    # RENEW IS THE OWNER'S VERB. It took no pid at all, so any process on the machine refreshed any
    # lease, which meant a dead holder's lease could be kept alive forever by a stranger and the
    # recovery path below could never fire. The pid defaults to $PPID, the shell that invoked this
    # script, so loop_until.sh keeps calling `renew` with no argument and still proves who it is.
    PID="${2:-$PPID}"
    [ -f "$LEASE" ] || { echo "NO-DATA: no lease to renew"; exit 2; }
    HOLDER=$(read_field pid)
    if [ "$HOLDER" != "$PID" ]; then
      echo "REFUSED: the lease is held by pid ${HOLDER:-unknown}, not by ${PID}; renew refreshes only your own lease"; exit 1
    fi
    sed -i '' "s/^at=.*/at=$(now)/" "$LEASE" 2>/dev/null || { echo "NO-DATA: the lease could not be rewritten"; exit 2; }
    echo "renewed"; exit 0;;
  release)
    # RELEASE WAS `rm -f` WITH NO OWNERSHIP CHECK AT ALL. Demonstrated 2026-09-21: one command from
    # any process freed a live lease, and the next command took it. An atomic acquire is worth
    # nothing when the exit door is unlocked, so release now names the pid it acts for, defaulting
    # to $PPID exactly as renew does.
    if [ "${2:-}" = "--force" ]; then
      # BREAK GLASS. Deliberate, explicit and loud on stderr, for a lease whose holder is genuinely
      # stuck and cannot release it itself. It is a separate word rather than a fallback because a
      # break glass path that fires on its own is just the old unauthenticated release again.
      HOLDER=$(read_field pid)
      echo "BREAK-GLASS: forcing the release of the loop lease held by pid ${HOLDER:-unknown}. If that process is still running it is now UNPROTECTED and may be joined by a second driver." >&2
      # The reclaim gate goes too. It is the one thing acquire will never free on its own, by
      # design, so break glass is its only recovery and break glass must actually reach it.
      rm -f "$LEASE"; rmdir "$LEASE.reclaim" 2>/dev/null; echo "released (FORCED)"; exit 0
    fi
    PID="${2:-$PPID}"
    [ -f "$LEASE" ] || { echo "released"; exit 0; }   # nothing held: releasing is idempotent
    HOLDER=$(read_field pid)
    if [ -z "$HOLDER" ]; then
      echo "REFUSED: a lease file exists and its holder cannot be read, so it is not provably yours; use 'release --force' if it is genuinely stuck"; exit 1
    fi
    if [ "$HOLDER" != "$PID" ]; then
      echo "REFUSED: the loop lease is held by pid ${HOLDER}, not by ${PID}; use 'release --force' if it is genuinely stuck"; exit 1
    fi
    rm -f "$LEASE"; echo "released"; exit 0;;
  owner)
    read_field pid; exit 0;;
  check)
    HOLDER=$(read_field pid); STAMP=$(read_field at); START=$(read_field start)
    case "$STAMP" in *[!0-9]*) STAMP="";; esac
    if [ -z "$HOLDER" ]; then echo "FREE: no loop lease held"; exit 0; fi
    # LIVENESS FIRST, in the same order acquire decides, or check is a lie. Measured 2026-09-22:
    # a LIVE holder whose stamp read as future was reported STALE here while acquire was stealing
    # it there, so the two halves of the unit agreed on a verdict that was wrong in both.
    if ! holder_live "$HOLDER" "$START"; then
      if future_stamp "$STAMP"; then
        echo "STALE: the lease names dead pid ${HOLDER} and is stamped $(( STAMP - $(now) ))s in the FUTURE, which no honest holder writes"; exit 3
      fi
      if [ -z "$STAMP" ]; then echo "STALE: lease names dead pid ${HOLDER}, age unknown (the stamp is not readable)"; exit 3; fi
      echo "STALE: lease names dead pid ${HOLDER} ($(( $(now) - STAMP ))s old)"; exit 3
    fi
    if future_stamp "$STAMP"; then
      echo "HELD by LIVE pid ${HOLDER}, stamped $(( STAMP - $(now) ))s in the FUTURE; the clock moved backwards or the file was planted, and a live holder is not evictable either way"; exit 1
    fi
    if [ -z "$STAMP" ]; then echo "HELD by pid ${HOLDER}, age unknown (the stamp is not readable)"; exit 1; fi
    AGE=$(( $(now) - STAMP ))
    # HELD, whatever the age. check must report the same policy acquire enforces, or it is a lie
    # that reads STALE about a lease no acquire will ever take.
    if [ "$AGE" -ge "$TTL" ]; then echo "HELD by live pid ${HOLDER}, ${AGE}s old, past the ${TTL}s ttl but ALIVE, so it is not evictable"; exit 1; fi
    echo "HELD by pid ${HOLDER}, ${AGE}s old"; exit 1;;
  *) echo "usage: loop_guard.sh acquire <pid> | renew [<pid>] | release [<pid>] | release --force | check | owner"; exit 2;;
esac
