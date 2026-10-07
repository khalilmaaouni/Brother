#!/usr/bin/env python3
"""Per stage arrival and departure times, so the bottleneck is computed instead of argued about.

usage as a library:  stage_log.enter("model", sub); stage_log.leave("model", sub, ok=True)
usage as a report:   python3 -B stage_log.py [--since-hours 24]
usage as a check:    python3 -B stage_log.py --selftest

WHY. On 2026-09-21 this framework spent a full day organised around a serial writer it had never measured. Two
outside reviewers did the arithmetic and the writer turned out to be about 16 percent utilised: idle five
sixths of the time, starving rather than congested. The whole disagreement existed because no stage recorded
when work ARRIVED at it and when it LEFT. Utilisation is arrivals multiplied by mean service time, and neither
term was on disk anywhere.

The same gap makes two other questions unanswerable: which check to run first (that is rejection rate over
cost, and cost is a duration nobody recorded) and whether a change helped (eight things changed the same
night, and with no per stage timing not one of them can be attributed).

THE GRAIN. One row per stage transition per sub unit. Not per pass, which is what the loop journal already
records at board level, and not per run folder, whose mtime is the time of the LAST write rather than of any
particular event. Epoch seconds, never a formatted local time, because a clock string that wraps at midnight
has already cost this estate a night of stale reads.

FAILS TOWARD SILENCE, NEVER TOWARD STOPPING. An unwritable journal loses a measurement; a journal that raises
loses the build. Every write is wrapped and every read of a malformed row skips it."""
import json
import os
import sys
import time

LOG = os.path.expanduser("~/.claude/evidence/brother-stages.jsonl")
STAGES = ("spec", "brief", "model", "grade", "probe", "land", "close")


def write(row, path=None):
    """Append one event. Never raises: a bookkeeping failure must not end a build that otherwise worked."""
    try:
        os.makedirs(os.path.dirname(path or LOG), exist_ok=True)
        with open(path or LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
    except OSError as exc:
        print("stage_log: could not append to %s (%s); this row is lost, the build is not" % (path or LOG, exc), file=sys.stderr)
    return row


def enter(stage, sub=None, unit=None, path=None, now=None):
    """Work arrived at this stage. The pair (enter, leave) is what makes a duration; an enter with no leave is
    an unfinished or crashed unit of work and the reader reports it as such rather than guessing a duration."""
    return write({"at": now if now is not None else time.time(), "stage": stage, "event": "enter",
                  "sub": sub, "unit": unit, "pid": os.getpid()}, path)


def leave(stage, sub=None, unit=None, ok=None, note=None, path=None, now=None):
    """Work left this stage. `ok` is the verdict where the stage has one, which is what turns the event stream
    into a funnel: count in, count out, yield, and the cost of everything that died here."""
    return write({"at": now if now is not None else time.time(), "stage": stage, "event": "leave",
                  "sub": sub, "unit": unit, "ok": ok, "note": (note or "")[:200], "pid": os.getpid()}, path)


def load(path=None):
    """Every readable row, oldest first. A corrupt line is skipped, never guessed at."""
    out, skipped = [], 0
    try:
        with open(path or LOG, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    skipped += 1
                    continue
                if isinstance(r, dict) and r.get("stage") and isinstance(r.get("at"), (int, float)):
                    out.append(r)
    except OSError:
        return []
    if skipped:
        print("stage_log: %d unreadable line(s) in %s skipped, named here rather than guessed at" % (skipped, path or LOG), file=sys.stderr)
    return sorted(out, key=lambda r: r["at"])


def _usable(r):
    """True for a row this module can read: a dict with a stage and a NUMERIC timestamp.

    ONE GUARD, THREE READERS. durations() had this check inline and funnel() had none, so the same
    malformed row was silently skipped by one reader and raised KeyError out of the other, against
    the module docstring's promise to fail toward silence. Measured 2026-09-21: funnel() on a row
    with no stage raised KeyError, and on a non dict raised TypeError. A guard copied into two of
    three callers is a guard the third will be missing, so it lives here and every reader routes
    through it."""
    return isinstance(r, dict) and bool(r.get("stage")) and isinstance(r.get("at"), (int, float))


def durations(rows):
    """{stage: [seconds, ...]} by pairing each enter with the next leave of the SAME stage, sub unit and process.

    An enter with no matching leave contributes NOTHING rather than a duration measured to now: a crashed or
    still running item has no service time yet, and inventing one biases exactly the number this module exists
    to get right."""
    open_at, out = {}, {}
    for r in rows:
        # A ROW WITH A NON NUMERIC TIMESTAMP IS DROPPED HERE, not by the caller. load() already
        # filters these, but durations() and utilisation() are PUBLIC entry points and a caller
        # holding rows from anywhere else reached a TypeError on a string `at`, against this
        # module's own stated contract of failing toward silence. The one external caller had
        # already written the guard for us, in loop_report.py, which is the signature of a defect
        # living in the callee: when every caller must repeat the same check, the check belongs
        # one level down. Found by an adversarial probe, 2026-09-21.
        if not _usable(r):
            continue
        # BY PID TOO (finding 9, 2026-09-27): two processes on one stage and sub unit overwrote each other's enter,
        # so 110 busy seconds read as 10, and loop_report's own yield (already paired by pid) disagreed with it.
        key = (r["stage"], r.get("sub"), r.get("pid"))
        if r.get("event") == "enter":
            open_at[key] = r["at"]
        elif r.get("event") == "leave" and key in open_at:
            d = r["at"] - open_at.pop(key)
            # A NEGATIVE DURATION IS DROPPED, never recorded. A clock that goes backwards, an NTP
            # step or a hand edited ledger all produce one, and a negative service time would drag
            # the mean this module exists to compute towards zero, which reads as a fast stage.
            if d >= 0:
                out.setdefault(r["stage"], []).append(d)
    return out


def utilisation(rows, wall=None):
    """{stage: (n, busy_seconds, mean_seconds, rho)} where rho is busy over wall clock.

    THE NUMBER THAT SETTLES A BOTTLENECK ARGUMENT. Below about 0.7 a stage is not the constraint whatever its
    architecture suggests. For a stage that can run several items at once this is the aggregate, so a rho above
    1 means concurrency rather than impossibility, and it is reported rather than clamped."""
    d = durations(rows)
    if wall is None:
        # the same source fix: compute the window from rows that actually carry a numeric stamp,
        # so a single malformed row cannot raise out of a reporting function.
        ats = [r["at"] for r in rows if _usable(r)]
        wall = (max(ats) - min(ats)) if len(ats) > 1 else 0.0
    out = {}
    for stage, xs in d.items():
        busy = sum(xs)
        out[stage] = (len(xs), busy, busy / len(xs) if xs else 0.0, (busy / wall) if wall > 0 else 0.0)
    return out


def funnel(rows):
    """{stage: (entered, left_ok, left_bad, yield)} so the arrow where work dies is visible.
    A leave with ok=None is counted as neither: a stage with no verdict has no yield, and pretending it passed
    is how a population of unknowns composes into a false pass."""
    out = {}
    for r in rows:
        if not _usable(r):
            continue
        s = r["stage"]
        e = out.setdefault(s, [0, 0, 0])
        if r.get("event") == "enter":
            e[0] += 1
        elif r.get("event") == "leave":
            if r.get("ok") is True:
                e[1] += 1
            elif r.get("ok") is False:
                e[2] += 1
    return {s: (a, b, c, (b / (b + c)) if (b + c) else None) for s, (a, b, c) in out.items()}


def _write_side_case():
    """enter() writes a NUMERIC `at` stamp. The whole module rests on this one key.

    WHY IT IS A CASE AT ALL: every other case here hand builds its rows, so enter() was never
    called and dropping the `at` key from it SURVIVED a mutation sweep on 2026-09-21 with the
    selftest printing 22 cases OK. Without that stamp load() discards the row, there is no
    duration, no mean service time and no rho: the entire subject of this module."""
    import tempfile
    p = os.path.join(tempfile.mkdtemp(), "w.jsonl")
    enter("model", "A", path=p, now=100.0)
    rows = load(p)
    return len(rows) == 1 and rows[0].get("at") == 100.0


def _verdict_case():
    """leave() writes the ok verdict to disk. Separate from the stamp case on purpose: a fixture
    that would fail for either reason proves neither, and this estate lost a night to exactly that
    (a failure fixture with a non zero exit AND an empty body, each guard masking the other)."""
    import tempfile
    p = os.path.join(tempfile.mkdtemp(), "v.jsonl")
    leave("grade", "A", ok=False, path=p, now=200.0)
    rows = load(p)
    return len(rows) == 1 and rows[0].get("ok") is False


def _note_cap_case():
    """leave() caps the note at 200 characters. A note is operator text of unbounded length and
    this journal is appended on every stage transition of every sub unit."""
    import tempfile
    p = os.path.join(tempfile.mkdtemp(), "n.jsonl")
    leave("probe", "A", ok=True, note="x" * 5000, path=p, now=300.0)
    return len(load(p)[0].get("note", "")) == 200


def _unwritable_case():
    """write() on an unwritable path loses the measurement and returns, never raises.

    The module docstring promises exactly this: an unwritable journal loses a measurement, a
    journal that raises loses the build. enter() and leave() sit on seven production call sites in
    unit_runner.py, so a raise here ends a build over bookkeeping. A path UNDER A REGULAR FILE is
    used rather than a permission trick, because a sweep that must run as any user cannot rely on
    root not existing."""
    import tempfile
    fd, f = tempfile.mkstemp()
    os.close(fd)
    try:
        wrote = write({"at": 1.0, "stage": "model"}, os.path.join(f, "nope.jsonl"))
    except OSError:
        wrote = None      # no bare boolean literal in this case: a sweep flips one and the case
    return wrote is not None      # then passes whatever write() did. The verdict is the value seen.


def _sort_case():
    """Write rows to a real file NEWEST FIRST and assert load() returns them oldest first."""
    import tempfile
    p = os.path.join(tempfile.mkdtemp(), "s.jsonl")
    with open(p, "w", encoding="utf-8") as f:
        for at in (300, 100, 200):
            f.write(json.dumps({"at": at, "stage": "model", "event": "enter", "sub": "A"}) + "\n")
    return [r["at"] for r in load(p)] == [100, 200, 300]


def _corrupt_case():
    """A real truncated line, a real non object and a real row missing its stage, beside one good
    row. Only the good row may survive."""
    import tempfile
    p = os.path.join(tempfile.mkdtemp(), "c.jsonl")
    with open(p, "w", encoding="utf-8") as f:
        f.write('{"at": 1, "stage": "model", "event": "ent\n')          # truncated mid write
        f.write('[1, 2, 3]\n')                                          # valid JSON, not an object
        f.write(json.dumps({"at": "banana", "stage": "model"}) + "\n")  # unusable timestamp
        f.write(json.dumps({"stage": "model", "event": "enter"}) + "\n")  # no timestamp at all
        f.write(json.dumps({"at": 9, "stage": "model", "event": "enter", "sub": "A"}) + "\n")
    rows = load(p)
    return len(rows) == 1 and rows[0]["at"] == 9


def _blank_line_case():
    """A blank line is spacing, not a corrupt row: it is skipped without being counted as unreadable, so the stderr
    count a reader acts on stays the number of rows really lost (a survivor of the 2026-09-27 lane G sweep)."""
    import io, tempfile
    p = os.path.join(tempfile.mkdtemp(), "b.jsonl")
    with open(p, "w", encoding="utf-8") as f:
        f.write(json.dumps({"at": 1, "stage": "model", "event": "enter", "sub": "A"}) + "\n\n")
    err, real = io.StringIO(), sys.stderr
    sys.stderr = err
    try:
        rows = load(p)
    finally:
        sys.stderr = real
    return len(rows) == 1 and "unreadable" not in err.getvalue()


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
    R = [{"at": 100.0, "stage": "model", "event": "enter", "sub": "A.1"},
         {"at": 160.0, "stage": "model", "event": "leave", "sub": "A.1", "ok": True},
         {"at": 160.0, "stage": "grade", "event": "enter", "sub": "A.1"},
         {"at": 190.0, "stage": "grade", "event": "leave", "sub": "A.1", "ok": False},
         {"at": 200.0, "stage": "model", "event": "enter", "sub": "B.1"},
         {"at": 260.0, "stage": "model", "event": "leave", "sub": "B.1", "ok": True},
         {"at": 300.0, "stage": "model", "event": "enter", "sub": "C.1"}]          # never left
    d = durations(R)
    u = utilisation(R)
    f = funnel(R)
    # THE TWO SILENCES THIS MODULE USED TO HAVE (lint 2026-09-22): a corrupt line was skipped without a
    # word, and an unwritable log lost its row without a word. Both are now named on stderr.
    import io, tempfile
    _tmp = tempfile.mkdtemp()
    _bad = os.path.join(_tmp, "bad.jsonl")
    with open(_bad, "w", encoding="utf-8") as _fh:
        _fh.write('{"at": 1, "stage": "model", "event": "enter"}\nNOT JSON\n')
    _err, _real_err = io.StringIO(), sys.stderr
    sys.stderr = _err
    try:
        _readback = load(_bad)
        write({"at": 2, "stage": "model"}, path=os.path.join(_bad, "x.jsonl"))   # parent is a file: unwritable
    finally:
        sys.stderr = _real_err
    _errtext = _err.getvalue()
    cases = [
        ("a corrupt line is skipped and the good row kept", len(_readback) == 1),
        ("the skipped line is named on stderr, never silent", "1 unreadable line(s)" in _errtext),
        ("an unwritable log names itself on stderr and does not raise", "could not append" in _errtext),
        ("a pair becomes a duration", d["model"] == [60.0, 60.0]),
        ("an enter with no leave contributes no duration", len(d["model"]) == 2),
        ("durations are per stage", d["grade"] == [30.0]),
        ("mean service time is right", abs(u["model"][2] - 60.0) < 1e-9),
        ("busy time is the sum", abs(u["model"][1] - 120.0) < 1e-9),
        ("rho is busy over wall", abs(u["model"][3] - 120.0 / 200.0) < 1e-9),
        ("a stage that ran once still reports", u["grade"][0] == 1),
        ("the funnel counts entries", f["model"][0] == 3),
        ("the funnel counts passes and failures", f["grade"][1] == 0 and f["grade"][2] == 1),
        ("yield is passes over verdicts", f["model"][3] == 1.0 and f["grade"][3] == 0.0),
        ("a stage with no verdict has yield None",
         funnel([{"at": 1.0, "stage": "x", "event": "leave"}])["x"][3] is None),
        ("an empty stream yields nothing rather than raising", durations([]) == {} and funnel([]) == {}),
        ("a leave before any enter is ignored, never negative",
         durations([{"at": 5.0, "stage": "m", "event": "leave", "sub": "Z"}]) == {}),
        # THE CASE THE FIRST VERSION OF THIS SELFTEST MISSED, and it is the production case. With parallel
        # lanes, many sub units sit in the model stage at once, so their enter and leave events INTERLEAVE.
        # Pairing on stage alone then matches one sub unit's enter with another's leave and every duration is
        # fiction. The original fixture ran one sub unit at a time, so a mutation removing `sub` from the
        # pairing key SURVIVED: the check could not fail, which by Law 10 means it was not a check.
        ("interleaved sub units in one stage are paired by SUB, not by arrival order",
         durations([{"at": 0.0, "stage": "model", "event": "enter", "sub": "A"},
                    {"at": 10.0, "stage": "model", "event": "enter", "sub": "B"},
                    {"at": 20.0, "stage": "model", "event": "leave", "sub": "B", "ok": True},
                    {"at": 100.0, "stage": "model", "event": "leave", "sub": "A", "ok": True}])
         == {"model": [10.0, 100.0]}),
        ("and the wrong pairing would have produced a different answer, so the case can fail",
         durations([{"at": 0.0, "stage": "model", "event": "enter", "sub": "A"},
                    {"at": 10.0, "stage": "model", "event": "enter", "sub": "B"},
                    {"at": 20.0, "stage": "model", "event": "leave", "sub": "B", "ok": True},
                    {"at": 100.0, "stage": "model", "event": "leave", "sub": "A", "ok": True}])["model"]
         != [20.0, 90.0]),
        # FINDING 9, 2026-09-27: two PROCESSES on one stage and sub unit (a rerun beside a straggler) overwrote each
        # other's enter, so 110 busy seconds read as 10 and a busy stage looked idle. Paired by pid too, as
        # loop_report.stage_yield already was.
        ("two processes on one stage and sub unit are paired by pid",
         durations([{"at": 0.0, "stage": "model", "event": "enter", "sub": "A", "pid": 1},
                    {"at": 10.0, "stage": "model", "event": "enter", "sub": "A", "pid": 2},
                    {"at": 20.0, "stage": "model", "event": "leave", "sub": "A", "pid": 2, "ok": True},
                    {"at": 100.0, "stage": "model", "event": "leave", "sub": "A", "pid": 1, "ok": True}])
         == {"model": [10.0, 100.0]}),
        # This case was literally `True` until 2026-09-21: a test that cannot fail, wearing the
        # name of a property nobody checked. Deleting the sort in load() turned nothing red. It
        # now writes rows out of order to a real file and asserts they come back ordered.
        ("rows out of order are sorted by time, not by file order", _sort_case()),
        # This case was named "a corrupt line is skipped" while it loaded a MISSING FILE, so it
        # exercised the OSError path and never met a corrupt row at all. Deleting the row filter
        # in load() turned nothing red. It now writes a real half line and a real non object.
        ("a corrupt line is skipped", _corrupt_case()),
        ("a blank line is skipped and never counted as unreadable", _blank_line_case()),
        ("a row with a non numeric timestamp is dropped, never raised",
         durations([{"at": "banana", "stage": "model", "event": "enter", "sub": "A"}]) == {}),
        ("utilisation survives a non numeric timestamp",
         utilisation([{"at": "banana", "stage": "model", "event": "enter", "sub": "A"}]) == {}),
        ("a negative duration is dropped, never counted",
         durations([{"at": 100, "stage": "model", "event": "enter", "sub": "A"},
                    {"at": 40, "stage": "model", "event": "leave", "sub": "A"}]) == {}),
        ("a single instant window does not divide by zero",
         utilisation([{"at": 5, "stage": "model", "event": "enter", "sub": "A"},
                      {"at": 5, "stage": "model", "event": "leave", "sub": "A"}])["model"][3] == 0.0),
        ("every named stage is a real one", all(s in STAGES for s in ("model", "grade", "probe", "land"))),
        # RHO ABOVE 1 IS CONCURRENCY AND IS REPORTED, NEVER CLAMPED. This module's own docstring
        # says so, and until 2026-09-21 nothing checked it: wrapping the rho expression in
        # min(1.0, ...) SURVIVED with the selftest printing 22 cases OK. Clamping is the worst
        # possible failure direction here, because it turns the one reading that PROVES a stage
        # runs several items at once into the reading that says it is exactly saturated, and the
        # estate then reorganises around a serial writer it has no evidence is serial. Two sub
        # units, each busy the whole 100 second window: busy 200 over wall 100 is rho 2.
        ("a rho above 1 is reported as concurrency, never clamped to 1",
         utilisation([{"at": 0.0, "stage": "model", "event": "enter", "sub": "A"},
                      {"at": 0.0, "stage": "model", "event": "enter", "sub": "B"},
                      {"at": 100.0, "stage": "model", "event": "leave", "sub": "A", "ok": True},
                      {"at": 100.0, "stage": "model", "event": "leave", "sub": "B", "ok": True}])["model"][3] == 2.0),
        ("enter() writes the `at` stamp every duration is derived from", _write_side_case()),
        ("leave() writes the ok verdict the funnel is built from", _verdict_case()),
        ("leave() caps an unbounded note at 200 characters", _note_cap_case()),
        ("an unwritable journal loses the measurement, never the build", _unwritable_case()),
        # A THIRD EVENT KIND MUST NOT BE READ AS A VERDICT. The leave branch was an `elif event ==
        # "leave"`, and turning it into a bare `else` SURVIVED: every fixture carried only enter
        # and leave, so nothing noticed that any other event with ok=True would be counted as a
        # pass. The day someone adds an "abort" or "requeue" event, that is a silent yield lie.
        ("only a leave event carries a verdict; any other event counts as neither",
         funnel([{"at": 1.0, "stage": "x", "event": "abort", "ok": True}])["x"] == (0, 0, 0, None)),
        # funnel() had NO malformed row guard while durations() had one inline, so the same row was
        # skipped by one reader and raised KeyError out of the other. Measured 2026-09-21: a row
        # with no stage raised KeyError and a non dict raised TypeError. Three shapes in one
        # fixture, and no shape can mask another: whichever guard is missing, its shape raises and
        # this case goes red.
        ("funnel skips a malformed row rather than raising, like durations does",
         funnel([{"at": 1.0, "event": "leave", "ok": True},
                 "not a dict even",
                 {"at": 2.0, "stage": "m", "event": "leave", "ok": True}]) == {"m": (0, 1, 0, 1.0)}),
    ]
    bad = [n for n, ok in cases if not ok]
    print("selftest: %d cases, %s" % (len(cases), "OK" if not bad else "FAILED: " + ", ".join(bad)))
    return 1 if bad else 0


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if "--selftest" in args:
        return selftest()
    since = float(args[args.index("--since-hours") + 1]) * 3600 if "--since-hours" in args else None
    rows = load()
    if since:
        cut = time.time() - since
        rows = [r for r in rows if r["at"] >= cut]
    if not rows:
        print("NO-DATA: no stage event has been recorded yet; nothing may be concluded about any bottleneck")
        return 2
    wall = rows[-1]["at"] - rows[0]["at"]
    print("STAGES  %d event(s) over %.2f h" % (len(rows), wall / 3600.0))
    print("\n%-8s %6s %10s %12s %8s" % ("stage", "n", "mean s", "busy h", "rho"))
    for s, (n, busy, mean, rho) in sorted(utilisation(rows, wall).items(), key=lambda kv: -kv[1][3]):
        flag = "  <- the constraint" if rho >= 0.7 else ""
        print("%-8s %6d %10.1f %12.2f %8.2f%s" % (s, n, mean, busy / 3600.0, rho, flag))
    print("\n%-8s %8s %8s %8s %8s" % ("stage", "in", "pass", "fail", "yield"))
    for s, (a, b, c, y) in funnel(rows).items():
        print("%-8s %8d %8d %8d %8s" % (s, a, b, c, ("%.0f%%" % (100 * y)) if y is not None else "no verdict"))
    print("\nA stage below rho 0.7 is not your bottleneck, whatever its architecture suggests.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
