#!/usr/bin/env python3
"""Wait for a probe wave without waiting for its slowest call.

WHY (A/B/C test 2026-09-23, arm O): the probe stage took a median 605 s of a round's about 15 minutes. probe_wave
waited for EVERY job (proc.wait up to 660 s) although a lane is judged on whichever adversary answered: 24 of 56 probe
calls stalled to the 600 s deadline, while every successful call finished within 408 s (90 percent within 279 s).
The wave now ends as soon as every lane has at least one answer AND `enough` seconds have passed (300 by default,
covering 90 percent of successful calls), or at the hard cap for a lane with no answer at all. It never ends before
every lane has an answer unless the cap is reached: a lane with no probe is never judged clean on silence.
usage: wave_wait.py --selftest"""
import os, sys, time


def wait(proc, outs_by_lane, cap, enough, clock=time.time, sleep=time.sleep, exists=os.path.isfile):
    """Wait on proc (a Popen). Returns ('done'|'enough'|'cap', seconds). 'enough' and 'cap' mean stragglers remain:
    the caller kills proc. outs_by_lane: {lane: [probe output paths]} for lanes that have fresh jobs."""
    t0 = clock()
    while True:
        el = clock() - t0
        if proc.poll() is not None: return "done", el
        if el >= cap: return "cap", el
        if el >= enough and all(any(exists(p) for p in outs) for outs in outs_by_lane.values()): return "enough", el
        sleep(3)


def selftest():
    """Whatever raises inside the eagerly built cases is reported as a FAILED verdict naming the
    exception, exit 1, never a bare traceback (scripts/test_selftests_report_rather_than_crash.py)."""
    try:
        return _selftest_body()
    except Exception as exc:                       # noqa: BLE001 - a verdict beats a traceback
        print("selftest: FAILED before it could finish, %s: %s"
              % (type(exc).__name__, str(exc)[:120]))
        return 1


def _selftest_body():
    class P(object):
        def __init__(self, ends_at=None): self.ends_at = ends_at
        def poll(self): return 0 if (self.ends_at is not None and T[0] >= self.ends_at) else None
    T = [0.0]
    clock = lambda: T[0]
    def sleep(s): T[0] += s
    files = set()
    def run(proc, outs, cap=660, enough=300, answers=None):
        T[0] = 0.0; files.clear()
        def exists(p):
            return p in files or any(p == path and T[0] >= at for path, at in (answers or {}).items())
        return wait(proc, outs, cap, enough, clock, sleep, exists)
    outs = {"L1": ["a1", "b1"], "L2": ["a2", "b2"]}
    r1 = run(P(ends_at=120), outs)
    r2 = run(P(), outs, answers={"a1": 150, "a2": 170})
    r3 = run(P(), outs, answers={"a1": 150})
    r4 = run(P(), outs, answers={"a1": 350, "b2": 420})
    r5 = run(P(), {}, cap=660)
    cases = [("a wave whose calls all end returns done at once", r1[0] == "done" and r1[1] < 130),
             ("every lane answered: the wave ends at `enough`, not at the cap", r2[0] == "enough" and 300 <= r2[1] < 310),
             ("a lane with no answer holds the wave to the cap, never judged clean on silence", r3[0] == "cap" and r3[1] >= 660),
             ("answers after `enough` end the wave when the last lane answers", r4[0] == "enough" and 420 <= r4[1] < 430),
             ("no fresh lanes: nothing to wait for beyond `enough`", r5[0] == "enough" and 300 <= r5[1] < 310)]
    bad = [n for n, ok in cases if not ok]
    print("selftest: %d cases, %s" % (len(cases), "OK" if not bad else "FAILED: " + ", ".join(bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(selftest() if "--selftest" in sys.argv else 2)
