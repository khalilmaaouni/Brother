#!/usr/bin/env python3
"""The loop's pulse, written where something OUTSIDE the loop can read it.

Why this exists, in the owner's words on 2026-09-21: "for hours I waited to know the result when
the cycle was blocked", and "we cannot waste hours and money like this". Both complaints are the
same missing thing. The driver already raised alarms, but only into a log and a file at a path
nobody was watching, and only when it reached a terminal state. A loop that dies quietly, or that
runs passes producing nothing, looked exactly like a loop that was working.

So this file answers one question for any reader, with no knowledge of the loop's internals:

    is the loop alive, and is it producing anything?

Three states, and the unknown case is never the good one:

    PRODUCING  the loop is alive and work landed recently
    QUIET      alive, but N passes have landed nothing; suspicious, not yet wrong
    ALARM      a terminal state was raised, OR the pulse is stale, OR it cannot be read

A missing, corrupt, or unreadable heartbeat reads ALARM, never OK. A silent death and a healthy
run must not look alike, and of the two mistakes, crying wolf costs a glance while the other cost
an evening.

usage:
  loop_heartbeat.py write --state S --reason R [--passes N] [--log P] [--landed N] [--end-failed]
  loop_heartbeat.py check [--stale-minutes M] [--quiet-passes N]   exit 0 PRODUCING, 1 ALARM, 2 QUIET
  loop_heartbeat.py banner                                          one HTML div for the progress board
  loop_heartbeat.py --selftest
"""
import argparse, datetime, json, os, sys

HB = os.path.expanduser("~/.claude/evidence/LOOP-HEARTBEAT.json")
STALE_MINUTES = 25          # a pass is minutes, not hours: 25 without a pulse means something died
QUIET_PASSES = 3            # three passes landing nothing is the shape of the 22-pass no-op run
START_GRACE_MINUTES = 12    # STARTED but no completed pass after this long means it died at birth
CLOCK_SKEW_MINUTES = 2      # tolerance before a future stamp is treated as a broken clock
# REFUSED is a terminal state like any other: a driver that would not start has ended, and the
# reader needs to know that as urgently as one that died mid run. It was added 2026-09-21 after
# the audit found all four pre-start refusals exiting silently.
# THE ONE LIST OF ENDS (audit E5, 2026-09-27): BUDGET, DISK and STOPPED were missing, so a driver that had stopped on
# them read "PRODUCING: alive" at exit 0. Every state loop_until.sh raises must be here, and the ends it announces as
# normal must be GOOD_TERMINAL: scripts/test_loop_until_ends.py reads the driver and fails on a state added there only.
TERMINAL = {"FINISHED", "STALLED", "BLOCKED", "UNPRODUCTIVE", "DEADLINE", "UNFUNDED", "BUDGET", "DISK", "STOPPED",
            "INTERRUPTED", "REFUSED"}
GOOD_TERMINAL = {"FINISHED", "DEADLINE", "STOPPED"}    # a normal end is not an emergency; an owner stop is his own act


def now():
    return datetime.datetime.now()


def write(path, state, reason, passes=0, log="", landed=None, end_failed=False):
    """end_failed: the driver's end itself failed (a runner stop, a pass fault, the ending, the snapshot or the receipt),
    so even a normal end state reads ALARM (audit E2, E4)."""
    prev = read(path) or {}
    # `landed` is how many builds landed in THIS pass, not a running total. Callers are shell
    # scripts, and asking a shell to carry a cumulative counter across processes is how counters
    # drift; the file already remembers, so let it do the adding.
    n = int(landed) if landed not in (None, "") else 0
    rec = {"state": state, "reason": reason, "passes": int(passes), "log": log,
           "stamp": now().strftime("%Y-%m-%d %H:%M:%S"),
           "landed_total": int(prev.get("landed_total") or 0) + n}
    if end_failed:
        rec["end_failed"] = True
    # passes since anything last landed: the productivity signal, carried forward across writes
    if n > 0:
        rec["passes_since_landing"] = 0
        rec["last_landing_stamp"] = rec["stamp"]
    else:
        rec["passes_since_landing"] = int(prev.get("passes_since_landing") or 0) + (1 if state == "PASS" else 0)
        rec["last_landing_stamp"] = prev.get("last_landing_stamp") or "never"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rec, f, indent=1)
    os.replace(tmp, path)       # atomic: a reader never sees half a pulse
    return rec


def read(path):
    try:
        with open(path, encoding="utf-8") as f:
            r = json.load(f)
        return r if isinstance(r, dict) else None
    except (OSError, ValueError):
        return None


def age_minutes(rec, ref=None):
    try:
        t = datetime.datetime.strptime(rec["stamp"], "%Y-%m-%d %H:%M:%S")
    except (KeyError, TypeError, ValueError):
        return None
    return ((ref or now()) - t).total_seconds() / 60.0


def _int(value, default=None):
    """int() that cannot crash the caller. A heartbeat field is written by a shell script and read
    by the only tool that can say the loop is sick, so a garbage value must degrade to a verdict,
    never to a traceback. Probed 2026-09-21: passes_since_landing of "banana" raised ValueError out
    of classify() and took the whole health check with it."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def classify(rec, stale_minutes=STALE_MINUTES, quiet_passes=QUIET_PASSES, ref=None):
    """Return (state, one line). Anything unreadable or unknown is ALARM, never OK."""
    if rec is None:
        return "ALARM", "no heartbeat could be read: the loop may never have started, or its pulse was deleted"
    state = rec.get("state") or ""
    age = age_minutes(rec, ref)
    if age is None:
        return "ALARM", "the heartbeat carries no readable timestamp, so its age cannot be judged"
    if state in TERMINAL:
        if rec.get("end_failed") is True:
            return "ALARM", "the loop ended as %s and its end FAILED: %s" % (state, rec.get("reason") or "")
        if state in GOOD_TERMINAL:
            return "PRODUCING", "the loop ended normally as %s: %s" % (state, rec.get("reason") or "")
        return "ALARM", "the loop ended as %s: %s" % (state, rec.get("reason") or "")
    # A pulse from the FUTURE is not a healthy pulse. Probed 2026-09-21: a stamp six hours ahead
    # read PRODUCING, and would keep reading PRODUCING forever, because every staleness test is
    # `age > limit` and a negative age passes it. Clock skew between machines, a hand edited file
    # and a wrong timezone all produce exactly this, and each is a reason to look, not to relax.
    if age < -CLOCK_SKEW_MINUTES:
        return "ALARM", ("the heartbeat is stamped %d minutes in the FUTURE: a clock is wrong or the file was "
                         "written by hand, and staleness cannot be judged until it is fixed" % abs(age))
    # STARTED means the driver announced itself and has not yet finished a pass. That is normal for
    # a minute and alarming after several: a loop SIGKILLed right after its start note would
    # otherwise read PRODUCING for the full staleness window, which is the silent death this whole
    # file exists to catch. The trap covers INT, TERM and HUP; it cannot cover KILL.
    if state == "STARTED" and age > START_GRACE_MINUTES:
        return "ALARM", ("the loop announced STARTED %d minutes ago and has not completed one pass "
                         "(grace %d): it was killed before doing any work, or it is wedged" % (age, START_GRACE_MINUTES))
    if age > stale_minutes:
        return "ALARM", ("no pulse for %d minutes (limit %d): the loop is not running and raised nothing, "
                         "which is the silent death case" % (age, stale_minutes))
    # ABSENT and GARBAGE are different facts. A record written before anything landed legitimately
    # carries no counter, and treating that as an alarm cried wolf on every fresh loop. A counter
    # that is PRESENT but not a number means the writer is broken, and that is worth a human.
    if "passes_since_landing" not in rec:
        since = 0
    else:
        since = _int(rec["passes_since_landing"], None)
        if since is None:
            return "ALARM", "passes_since_landing is present but not a number (%r), so the writer is broken" % (
                rec["passes_since_landing"],)
    if since >= quiet_passes:
        return "QUIET", "alive, but %d consecutive pass(es) landed nothing; last landing %s" % (
            since, rec.get("last_landing_stamp") or "never")
    return "PRODUCING", "alive, %d pass(es) run, last landing %s" % (
        int(rec.get("passes") or 0), rec.get("last_landing_stamp") or "never")


COLOR = {"PRODUCING": ("#0E7A6F", "loop healthy"), "QUIET": ("#B07A0E", "loop producing nothing"),
         "ALARM": ("#A3231B", "loop needs a human")}


def banner(rec, **kw):
    state, why = classify(rec, **kw)
    color, headline = COLOR[state]
    return ('<div style="border-left:6px solid %s;background:%s14;padding:12px 16px;margin:12px 0;'
            'font-family:Seravek,system-ui,sans-serif">'
            '<strong style="color:%s">%s: %s</strong><br><span style="color:#141B22">%s</span>'
            '<br><span style="color:#5a6672;font-size:0.85em">heartbeat %s</span></div>') % (
        color, color, color, state, headline, why,
        (rec or {}).get("stamp") or "never written")


def _roundtrip_landed(path=None):
    """write() twice with 2 landings each: the total is 4, and the quiet counter resets on a landing."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:     # removed afterwards: a selftest leaves nothing in the temp folder
        path = path or os.path.join(d, "hb.json")
        write(path, "PASS", "", landed=2)
        write(path, "PASS", "")                     # a pass that landed nothing
        r = write(path, "PASS", "", landed=2)
    return r["landed_total"] == 4 and r["passes_since_landing"] == 0


def _roundtrip_end_failed():
    """write(end_failed=True) reads back ALARM through the CLI's own path; a later write without it is not failed."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "hb.json")
        a = write(path, "DEADLINE", "x", end_failed=True)
        b = write(path, "STARTED", "next run")
    return a.get("end_failed") is True and classify(a)[0] == "ALARM" and "end_failed" not in b


def selftest():
    """A selftest that RAISES has not reported a verdict, it has abandoned the question.

    MEASURED 2026-09-21 on this exact file: injecting a raise into one case expression killed
    this function with a traceback at EXIT 1, which is the same exit code an honest failure
    returns. A pipeline reading the code and a human reading the text then describe the same run
    differently, and the human gets a stack trace where a verdict belongs.

    The cases below are built EAGERLY, so one bad expression takes the whole run with it. Turning
    every case into a lambda would fix that too, but it is a large diff whose own risk is a
    transcription error in a case nobody would then notice was wrong. Wrapping the body is four
    lines, carries no such risk, and covers the NEXT case someone adds here without its author
    doing anything: whatever escapes is reported as a FAILED case naming the exception, and the
    exit code still says 1, so the verdict and the code agree in every direction.
    """
    try:
        return _selftest_body()
    except Exception as exc:                       # noqa: BLE001 - a verdict beats a traceback
        print("selftest: FAILED before it could finish, %s: %s"
              % (type(exc).__name__, str(exc)[:120]))
        return 1


def _selftest_body():
    ref = datetime.datetime(2026, 9, 21, 20, 0, 0)
    fresh = ref.strftime("%Y-%m-%d %H:%M:%S")
    old = (ref - datetime.timedelta(minutes=90)).strftime("%Y-%m-%d %H:%M:%S")
    cases = [
        ("no heartbeat is ALARM, never OK", classify(None, ref=ref)[0] == "ALARM"),
        ("unreadable stamp is ALARM", classify({"state": "PASS", "stamp": "nonsense"}, ref=ref)[0] == "ALARM"),
        ("stale pulse is ALARM", classify({"state": "PASS", "stamp": old}, ref=ref)[0] == "ALARM"),
        ("fresh and landing is PRODUCING", classify({"state": "PASS", "stamp": fresh, "passes_since_landing": 0}, ref=ref)[0] == "PRODUCING"),
        ("three quiet passes is QUIET", classify({"state": "PASS", "stamp": fresh, "passes_since_landing": 3}, ref=ref)[0] == "QUIET"),
        ("two quiet passes is not yet QUIET", classify({"state": "PASS", "stamp": fresh, "passes_since_landing": 2}, ref=ref)[0] == "PRODUCING"),
        ("BLOCKED is ALARM even when fresh", classify({"state": "BLOCKED", "stamp": fresh}, ref=ref)[0] == "ALARM"),
        ("UNPRODUCTIVE is ALARM", classify({"state": "UNPRODUCTIVE", "stamp": fresh}, ref=ref)[0] == "ALARM"),
        ("FINISHED is not an emergency", classify({"state": "FINISHED", "stamp": fresh}, ref=ref)[0] == "PRODUCING"),
        ("DEADLINE is not an emergency", classify({"state": "DEADLINE", "stamp": fresh}, ref=ref)[0] == "PRODUCING"),
        ("a terminal state beats staleness", classify({"state": "BLOCKED", "stamp": old}, ref=ref)[0] == "ALARM"),
        ("banner names the state", "ALARM" in banner(None, ref=ref)),
        ("landed is per pass and accumulates", _roundtrip_landed()),
        ("a future stamp is ALARM, not healthy",
         classify({"state": "PASS", "stamp": (ref + datetime.timedelta(hours=6)).strftime("%Y-%m-%d %H:%M:%S")}, ref=ref)[0] == "ALARM"),
        ("a stamp one minute ahead is tolerated",
         classify({"state": "PASS", "stamp": (ref + datetime.timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")}, ref=ref)[0] == "PRODUCING"),
        ("STARTED and then killed is ALARM",
         classify({"state": "STARTED", "stamp": (ref - datetime.timedelta(minutes=20)).strftime("%Y-%m-%d %H:%M:%S")}, ref=ref)[0] == "ALARM"),
        ("STARTED a moment ago is not yet ALARM",
         classify({"state": "STARTED", "stamp": fresh}, ref=ref)[0] == "PRODUCING"),
        ("a garbage counter is ALARM, never a crash",
         classify({"state": "PASS", "stamp": fresh, "passes_since_landing": "banana"}, ref=ref)[0] == "ALARM"),
        ("an ABSENT counter is normal, not an alarm",
         classify({"state": "PASS", "stamp": fresh}, ref=ref)[0] == "PRODUCING"),
        ("a refused start is an ALARM, never silence",
         classify({"state": "REFUSED", "stamp": fresh}, ref=ref)[0] == "ALARM"),
        # E5 (2026-09-27): every end the driver can write is terminal; a stopped driver never reads alive
        ("a BUDGET end is ALARM, never alive", classify({"state": "BUDGET", "stamp": fresh}, ref=ref)[0] == "ALARM"),
        ("a DISK end is ALARM, never alive", classify({"state": "DISK", "stamp": fresh}, ref=ref)[0] == "ALARM"),
        ("an owner STOPPED end is a normal end, not alive",
         classify({"state": "STOPPED", "stamp": fresh}, ref=ref) == ("PRODUCING", "the loop ended normally as STOPPED: ")),
        ("a normal end whose end FAILED is ALARM",
         classify({"state": "DEADLINE", "stamp": fresh, "end_failed": True}, ref=ref)[0] == "ALARM"
         and classify({"state": "DEADLINE", "stamp": fresh, "end_failed": False}, ref=ref)[0] == "PRODUCING"),
        ("a failed end is written and the next write clears it", _roundtrip_end_failed()),
    ]
    bad = [n for n, ok in cases if not ok]
    print("selftest: %d cases, %s" % (len(cases), "OK" if not bad else "FAILED: " + ", ".join(bad)))
    return 1 if bad else 0


def main():
    if "--selftest" in sys.argv:
        return selftest()
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("cmd", choices=["write", "check", "banner"])
    ap.add_argument("--state", default="PASS"); ap.add_argument("--reason", default="")
    ap.add_argument("--passes", default="0"); ap.add_argument("--log", default="")
    ap.add_argument("--landed", default=None)
    ap.add_argument("--end-failed", action="store_true")
    ap.add_argument("--stale-minutes", type=int, default=STALE_MINUTES)
    ap.add_argument("--quiet-passes", type=int, default=QUIET_PASSES)
    ap.add_argument("--path", default=HB)
    a = ap.parse_args()
    kw = {"stale_minutes": a.stale_minutes, "quiet_passes": a.quiet_passes}
    if a.cmd == "write":
        rec = write(a.path, a.state, a.reason, a.passes, a.log, a.landed, a.end_failed)
        print("heartbeat %s %s" % (rec["state"], rec["stamp"])); return 0
    rec = read(a.path)
    if a.cmd == "banner":
        print(banner(rec, **kw)); return 0
    state, why = classify(rec, **kw)
    print("%s: %s" % (state, why))
    return {"PRODUCING": 0, "ALARM": 1, "QUIET": 2}[state]


if __name__ == "__main__":
    sys.exit(main())
