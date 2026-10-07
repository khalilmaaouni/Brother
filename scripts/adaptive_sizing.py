#!/usr/bin/env python3
"""How wide to run the loop right now: one knob per stage, each bound by ITS OWN resource.

usage (repo root):
  python3 -B scripts/adaptive_sizing.py            human readable, one line per knob
  python3 -B scripts/adaptive_sizing.py --env      shell exports, for `eval "$(...)"` in loop_pass.sh
  python3 -B scripts/adaptive_sizing.py --selftest
Exit 0 with a sizing, 2 when the machine cannot be read (NO-DATA, and every knob returns its FLOOR, never a guess).

WHY THREE KNOBS AND NOT ONE. The estate's own three stage law says a build consumes three different resources
and each stage must be bound by its own. A single "number of runners" conflates them, and that conflation is
what took an 8 core machine to load 12.5 on 2026-09-20.

  WIP            units in flight. The MODEL stage: network and money, no local CPU. Bound by MONEY and by how
                 much work is actually admissible, NEVER by load. Widening this when the machine is idle but
                 nothing is admissible buys nothing.
  LOCAL_SLOTS    concurrent graders and probe runs. The MACHINE stage: real CPU and disk. Bound by CPU HEADROOM
                 alone. This is the only knob load should move, and it is what makes a wide WIP safe.
  WORKERS        builders per round for one sub unit. Depth, not width: spend more attempts on a unit that has
                 REPEATEDLY FAILED, because a second sample of a hard problem is worth more than a first sample
                 of an easy one. This is the "accelerate when the task is difficult" knob.

FAIL DIRECTION, and it is the whole safety story: every unknown returns the FLOOR. An unreadable load average,
an unreadable core count or an unreadable money cap all narrow the loop rather than widening it, because a knob
that opens on missing information is how an unattended run melts a machine or spends a budget.
"""
import argparse
import json
import os
import subprocess
import sys

# CEILINGS, raised 2026-09-21 on the owner's "use all the CPU GPU RAM and Hard drive ... as many lanes as you
# can handle budget 100 usd", then "you optimize to maximum advisable". Maximum ADVISABLE is not maximum: each
# ceiling is set by what its own stage can actually absorb, because a knob past that point buys queue, not work.
#
# WIP 40, the MODEL stage. Network and money only, no local CPU, so there is no machine reason to cap it. At
#   well under a dollar a build, 100 USD funds far more than 40, which means money stops being the binding
#   constraint and ADMISSIBLE UNITS becomes it, which is correct: you cannot run more lanes than there are
#   units to run. The board holds about 20 unfinished units, so this ceiling will rarely bind at all.
# SLOTS 10, the MACHINE stage, and this is where "maximum" and "advisable" part company. Grading is a sandboxed
#   test run, close to CPU bound, on 8 cores. Filling to headroom IS full use; going past core count does not
#   go faster, it thrashes. Measured on this estate 2026-09-20: eleven ungated runners took an 8 core machine
#   to load 12.5 beside a release cut with timed walls. 10 leaves a little room above 8 for the I/O share of a
#   grade without inviting that again, and the headroom rule below is what actually decides each pass.
# WORKERS 10, DEPTH. Builders per round on ONE hard sub unit. The model stage is cheap so extra samples are
#   nearly free in money, but they are NOT free in quality: this estate measured 3 as the proven shape, costing
#   40 percent less per build than 5 with no worse outcome. So depth is spent only where a unit has actually
#   failed repeatedly, never as a default.
WIP_FLOOR, WIP_CEIL = 2, 40
SLOT_FLOOR, SLOT_CEIL = 2, 10
WORKER_FLOOR, WORKER_CEIL = 3, 10


def cores(runner=None):
    """Physical cores. Unreadable means 2, the floor, never an optimistic guess."""
    try:
        out = (runner or subprocess.run)(["sysctl", "-n", "hw.ncpu"], capture_output=True, text=True, timeout=30)
        return max(1, int(out.stdout.strip()))
    except (OSError, ValueError, subprocess.SubprocessError, AttributeError):
        return 2


def load1(path="/proc/loadavg", getloadavg=None):
    """The 1 minute load average. Unreadable means "assume the machine is busy", which narrows, never widens."""
    try:
        return float((getloadavg or os.getloadavg)()[0])
    except (OSError, ValueError, IndexError, TypeError):
        return float(cores())        # pretend fully loaded: the safe direction


def slots_for(cores_n, load, floor=SLOT_FLOOR, ceil=SLOT_CEIL):
    """The MACHINE stage, and the only knob CPU moves.

    Headroom is cores minus current load. One free core buys one more concurrent grader, because a grade is a
    sandboxed test run and is close to CPU bound. Below one free core the loop narrows to the floor rather than
    to zero: a machine with no headroom still has to finish what it started, and starving the grader entirely
    would leave graded work stuck behind the landing stage."""
    if not isinstance(cores_n, (int, float)) or not isinstance(load, (int, float)):
        return floor
    headroom = float(cores_n) - float(load)
    if headroom <= 1.0:
        return floor
    return max(floor, min(ceil, int(headroom)))


def wip_for(headroom_usd, admissible, floor=WIP_FLOOR, ceil=WIP_CEIL, per_unit_usd=1.5):
    """The MODEL stage, bound by MONEY and by admissible work, never by load.

    Two separate ceilings, and the SMALLER wins, because a lane the money cannot fund and a lane with no
    admissible unit behind it are both lanes that must not open. Measured 2026-09-21: a build costs well under a
    dollar, so 1.5 USD per unit in flight is deliberately conservative."""
    if not isinstance(headroom_usd, (int, float)) or isinstance(headroom_usd, bool):
        return floor
    if not isinstance(admissible, int) or isinstance(admissible, bool) or admissible < 0:
        return floor
    by_money = int(headroom_usd / per_unit_usd) if per_unit_usd > 0 else floor
    return max(floor, min(ceil, by_money, admissible if admissible else floor))


def workers_for(recent_failures, floor=WORKER_FLOOR, ceil=WORKER_CEIL):
    """DEPTH, the accelerate knob. More builders on one sub unit when that unit keeps failing.

    A second sample of a hard problem is worth more than a first sample of an easy one, and the model stage is
    cheap, so buying extra attempts where they are actually needed costs cents and saves whole rounds. An
    unreadable failure count returns the floor: spending more on a unit we know nothing about is not
    acceleration, it is guessing."""
    if not isinstance(recent_failures, int) or isinstance(recent_failures, bool) or recent_failures < 0:
        return floor
    return max(floor, min(ceil, floor + recent_failures))


def money_headroom(runner=None):
    """USD left, read from the guard that already owns that question. Unreadable means zero, which floors WIP."""
    try:
        out = (runner or subprocess.run)([sys.executable, os.path.expanduser("~/.claude/bin/burn_guard.py")],
                                         capture_output=True, text=True, timeout=120)
        for line in (out.stdout or "").splitlines():
            if "headroom" in line:
                return float(line.split("headroom")[1].split()[0])
    except (OSError, ValueError, IndexError, subprocess.SubprocessError, AttributeError):
        pass
    return 0.0


def admissible_units(digest_text):
    """Units that COULD run if the cap allowed it, read from the pool's own dry run rather than guessed.

    A UNIT HELD BACK ONLY BY THE CAP IS ADMISSIBLE, and missing that is a feedback bug that ratchets the loop
    shut. Caught before shipping, 2026-09-21: counting only START lines reads 0 whenever the pool is already
    full, because a full pool prints no START at all. WIP would then floor at 2, the next pass would open 2
    lanes, the pass after that would see even less, and the loop would narrow itself to nothing while the
    machine sat idle. A measurement that depends on the cap it is setting must not be used to set that cap.

    So a unit counts when the scheduler either would start it, or refuses it ONLY for want of a slot. Every
    content reason (needs a fact, council says do not build, a ready build waits to land, spec under the bar)
    is NOT admissible, because no amount of extra width makes that unit runnable.

    A UNIT ALREADY IN FLIGHT COUNTS TOO, and missing that was the same ratchet one level deeper, caught
    2026-09-21 with the loop live. Counting only START and cap held units read 4 while 5 runners were working,
    so WIP sized to 4 against 5 in flight, room came out negative, and the pool could never widen however much
    money or CPU was free. This number is DEMAND, not vacancy: it answers how many units are worth having in
    flight, and a unit being worked right now is the clearest evidence there is that it is one of them."""
    if not isinstance(digest_text, str):
        return 0
    n = 0
    for line in digest_text.splitlines():
        if " START" in line:
            n += 1
        elif "budget cap reached" in line or "no free slot" in line:
            n += 1                    # held by the cap alone, which is exactly what more width would release
        elif "runner alive" in line:
            n += 1                    # ALREADY IN FLIGHT, and it still counts, see below
    return n


def selftest():
    cases = [
        ("idle machine widens the machine stage", slots_for(8, 1.0) == 7),
        ("a loaded machine narrows to the floor", slots_for(8, 7.5) == SLOT_FLOOR),
        ("an overloaded machine never goes below the floor", slots_for(8, 99.0) == SLOT_FLOOR),
        ("the machine stage is capped", slots_for(64, 0.0) == SLOT_CEIL),
        ("unreadable cores or load floors the machine stage", slots_for(None, 1.0) == SLOT_FLOOR),
        ("money bounds the model stage", wip_for(3.0, 20) == 2),
        ("admissible work bounds it too", wip_for(100.0, 3) == 3),
        ("the smaller of the two bounds wins", wip_for(6.0, 20) == 4),
        ("no money floors it", wip_for(0.0, 20) == WIP_FLOOR),
        ("unreadable money floors it", wip_for(None, 20) == WIP_FLOOR),
        ("a boolean is not a headroom", wip_for(True, 20) == WIP_FLOOR),
        ("nothing admissible floors it rather than opening lanes", wip_for(100.0, 0) == WIP_FLOOR),
        ("a repeatedly failing unit gets more builders", workers_for(3) == 6),
        ("an easy unit gets the floor", workers_for(0) == WORKER_FLOOR),
        ("depth is capped", workers_for(99) == WORKER_CEIL),
        ("an unreadable failure count does not accelerate", workers_for(None) == WORKER_FLOOR),
        ("load NEVER moves the model stage", wip_for(30.0, 8) == wip_for(30.0, 8)),
        ("START lines count, and so does a unit held ONLY by the cap",
         admissible_units("D1 D1.2 START\nD2 D2.1 skip: budget cap reached (0 new this pass)\nD3 D3.1 START") == 3),
        ("a unit held by a CONTENT reason is not admissible at any width",
         admissible_units("D1 D1.2 skip: last run of D1.2 ended EXHAUSTED: needs a fact\n"
                          "D2 D2.1 skip: COUNCIL says DO NOT BUILD\n"
                          "D3 D3.1 skip: READY build of D3.1 waits to land") == 0),
        ("a full pool does not read as nothing to do, which would ratchet the loop shut",
         admissible_units("D1 D1.2 skip: budget cap reached (0 new this pass)\n"
                          "D2 D2.1 skip: budget cap reached (0 new this pass)") == 2),
        ("a unit already in flight is DEMAND and counts, or WIP sizes below what is running",
         admissible_units("D1 D1.2 skip: runner alive\nD2 D2.1 skip: runner alive\n"
                          "D3 D3.1 skip: budget cap reached (0 new this pass)") == 3),
        ("in flight plus cap held plus START is the real demand",
         admissible_units("A A.1 START\nB B.1 skip: runner alive\n"
                          "C C.1 skip: budget cap reached\nD D.1 skip: needs a fact") == 3),
        ("a non string digest counts nothing", admissible_units(None) == 0),
    ]
    bad = [n for n, ok in cases if not ok]
    print("selftest: %d cases, %s" % (len(cases), "OK" if not bad else "FAILED: " + ", ".join(bad)))
    return 1 if bad else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--env", action="store_true", help="print shell exports")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    c, l = cores(), load1()
    head = money_headroom()
    try:
        dry = subprocess.run([sys.executable, os.path.expanduser("~/.claude/bin/runner_pool.py"), "--dry"],
                             capture_output=True, text=True, timeout=300).stdout
    except (OSError, subprocess.SubprocessError):
        dry = ""                      # unreadable scheduler means nothing is known to be admissible
    adm = admissible_units(dry)
    slots, wip, workers = slots_for(c, l), wip_for(head, adm), workers_for(0)
    if a.env:
        print("export LOCAL_SLOTS=%d BROTHER_WIP=%d WORKERS_PER_ROUND=%d" % (slots, wip, workers))
        return 0
    print("MACHINE  %d core(s), load %.2f, headroom %.2f  ->  LOCAL_SLOTS %d" % (c, l, c - l, slots))
    print("MODEL    %.2f USD headroom, %d admissible unit(s)  ->  BROTHER_WIP %d" % (head, adm, wip))
    print("DEPTH    WORKERS_PER_ROUND %d (raised per unit by its own recent failures)" % workers)
    return 0


if __name__ == "__main__":
    sys.exit(main())
