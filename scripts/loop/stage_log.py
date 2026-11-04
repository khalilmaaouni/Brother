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
        # errors="replace": a ledger line holding non UTF-8 bytes (a torn write) is one unreadable line, counted below,
        # not a UnicodeDecodeError that ends the read and loses every row after it (LOOP1.g self review, 2026-11-02).
        with open(path or LOG, encoding="utf-8", errors="replace") as fh:
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


# THE DELIVERY TRACE (LOOP1.g, closing LOOP1.c's gap, 2026-11-02). The pack counted LOOP1.c landed while no reader could
# say "this sub unit went through the loop". delivery_trace is that reader. It joins the loop's OWN two records and
# writes nothing: the stage ledger (brother-stages.jsonl: unit_runner's model and grade rows, one per transition) and the
# plan's structured landing_events (land_batch's landing, close_unit's closure). It invents no row. Named stages:
#   admitted  the last `model` enter that precedes the build that was graded (work arrived at the model stage)
#   built     the last `model` leave with ok True before that grade
#   graded    the last `grade` leave with ok True (a failed or verdictless grade is never a graded row)
#   landed    the plan's current landing event for the sub (plan_store.landing_facts: a revert or a torn record removes it)
#   closed    the sub's `closed` event, and only while it is landed
# THE TREE. A stage row carries `tree`: the 40 hex commit the landing event names, which plan_store defines as the BASE
# the sub unit was built and graded on (P1, 2026-10-31). The ledger rows themselves carry no commit, so admitted, built
# and graded take that base only once a landing exists; before it they say None, never a guess.
# ORDER. Rows come back in STAGE ORDER. Admitted, built and graded are ordered by their ledger stamps; the plan events
# carry no stamp, so landed and closed follow them by construction, not by a clock. FAIL DIRECTION: a missing, corrupt
# or unselectable record yields FEWER rows (a missing stage reads as not delivered), never a filled one, and never raises.
DELIVERY_STAGES = ("admitted", "built", "graded", "landed", "closed")


def _plan_units(plan_file):
    """The plan's unit list, or None with the reason on stderr. plan_file None is the selected plan (BROTHER_WBS, else the
    default), looked for under the launch directory first, then under the repository this module sits in."""
    here = os.path.dirname(os.path.abspath(__file__))
    try:
        if plan_file is None:
            if here not in sys.path:
                sys.path.insert(0, here)
            import plan_path
            rel = plan_path.selected()
            if not str(os.environ.get(plan_path.ENV, "")).strip():
                print("stage_log: %s is unset, so the default plan %s is read for landings" % (plan_path.ENV, rel), file=sys.stderr)
            plan_file = next((c for c in (os.path.join(os.getcwd(), rel), os.path.join(os.path.dirname(os.path.dirname(here)), rel))
                              if os.path.exists(c)), rel)
        with open(plan_file, encoding="utf-8") as fh:
            units = json.load(fh).get("units")
        if isinstance(units, list):
            return units
        print("stage_log: plan %s has no unit list; no landing is read from it" % plan_file, file=sys.stderr)
    except (OSError, ValueError, AttributeError) as exc:
        print("stage_log: plan unreadable (%s); no landing is read from it" % exc, file=sys.stderr)
    return None


def delivery_trace(unit, sub, stages_path=None, plan_file=None):
    """The stage rows of one sub unit's delivery through the loop, in stage order. Each row is
    {"stage", "unit", "sub", "at", "tree"}: `at` is the ledger stamp (None for a plan event), `tree` the landing's base
    commit or None before a landing exists. Pure reads of two records; see the block comment above for what each stage means."""
    if not all(isinstance(x, str) and x for x in (unit, sub)):
        return []   # a None would match every ledger row that carries no unit or sub
    mine = [r for r in load(stages_path) if r.get("unit") == unit and r.get("sub") == sub]

    def last(stage, event, before=None, ok=None):
        hit = [r for r in mine if r["stage"] == stage and r.get("event") == event and (before is None or r["at"] <= before)
               and (ok is None or r.get("ok") is ok)]
        return hit[-1] if hit else None

    graded = last("grade", "leave", ok=True)
    built = last("model", "leave", before=graded["at"], ok=True) if graded else None
    admitted = last("model", "enter", before=built["at"]) if built else None
    landed = closed = None
    units = _plan_units(plan_file)
    events = next((u.get("landing_events") for u in (units or []) if isinstance(u, dict) and u.get("id") == unit), None)
    if events is not None:
        here = os.path.dirname(os.path.abspath(__file__))
        if here not in sys.path:
            sys.path.insert(0, here)
        import plan_store
        landed = plan_store.landing_facts(events, unit, sub)
        landed = landed if landed["landed"] else None
        if landed:   # landing_facts passed, so every event is well formed: the highest closed one is the closure
            shut = [e for e in events if e["type"] == "closed" and e["unit"] == unit and e["sub"] == sub]
            closed = max(shut, key=lambda e: e["sequence"]) if shut else None
    tree = landed["commit"] if landed else None
    picked = (("admitted", admitted), ("built", built), ("graded", graded))
    out = [{"stage": s, "unit": unit, "sub": sub, "at": r["at"], "tree": tree} for s, r in picked if r]
    if landed:
        out.append({"stage": "landed", "unit": unit, "sub": sub, "at": None, "tree": landed["commit"]})
    if closed:
        out.append({"stage": "closed", "unit": unit, "sub": sub, "at": None, "tree": closed["commit"]})
    return out


# ONE CURRENT RUN STATE (LOOP1.f, cause RC11 of the 1.1.9 cut): a monitor read a replayed disk checkpoint as the
# current state, and its watchers expired while the job went on. Both came from ordering by wall clock and from
# reading age as an answer. A run event carries the run it belongs to and a SEQUENCE the writer assigns under a lock,
# so the highest sequence of THIS run is the current state, whatever its `at`, whatever the file order, and whatever
# a snapshot taken earlier said. An expired lease renews the monitor, then escalates once; it never ends the run.
RUN_LOG = os.path.expanduser("~/.claude/evidence/brother-run-state.jsonl")
RUN_STATES = ("RUNNING", "WAITING", "DONE", "FAILED", "NEEDS_INPUT")
RUN_FINAL = ("DONE", "FAILED")
RUN_LEASE_S = 1800      # how long the coordinator may stay quiet before its monitor is renewed (then escalated)
MONITOR_MARKS = ("renewed", "escalated")
# STAB1.h: a mark that is a REPORT, not a watch step. It says "no driver is alive and admission would start a unit",
# once per run. It keeps the state and the lease it copied and never touches renewed or escalated (current_run).
IDLE_MARK = "idle_with_work"
LOCK_WAIT_S = 30        # a writer that cannot take the lock in this long fails (the pass blocks) rather than hangs


def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and x == x and x not in (float("inf"), float("-inf"))


def _word(x):
    return isinstance(x, str) and bool(x) and not any(c.isspace() for c in x)


def _sha(x):
    """A full commit id, 40 or 64 hex characters. `HEAD` (an unborn branch's echo) or an empty read is not one."""
    return isinstance(x, str) and len(x) in (40, 64) and all(c in "0123456789abcdef" for c in x)


def run_row_ok(r):
    """True for a run event this module can trust: schema 1, a named run, a whole sequence, a known state, a candidate,
    a phase and a numeric lease. Anything else is skipped and COUNTED by the reader, never read as a state."""
    return (isinstance(r, dict) and r.get("schema") == 1 and _word(r.get("run"))
            and isinstance(r.get("sequence"), int) and not isinstance(r.get("sequence"), bool) and r["sequence"] >= 0
            and r.get("state") in RUN_STATES and _word(r.get("candidate")) and _word(r.get("phase"))
            and _num(r.get("lease_until")) and r.get("monitor", None) in (None,) + MONITOR_MARKS + (IDLE_MARK,))


def run_event(run, sequence, candidate, state, phase, lease_until, path=None, monitor=None):
    """Append one schema 1 run event and fsync it. Returns True only when the row is on disk.

    Never raises, like write(), BUT unlike a timing row a lost run event is not harmless: loop_pass.sh launches a stage
    only after this returned True (through the run-event verb), so progress is persisted BEFORE the stage starts."""
    row = {"schema": 1, "run": run, "sequence": sequence, "candidate": candidate, "state": state, "phase": phase,
           "lease_until": lease_until, "at": time.time()}
    if monitor is not None:
        row["monitor"] = monitor
    if not run_row_ok(row):
        print("stage_log: refused a malformed run event %r; nothing was written" % (row,), file=sys.stderr)
        return False
    try:
        os.makedirs(os.path.dirname(path or RUN_LOG), exist_ok=True)
        with open(path or RUN_LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
    except OSError as exc:
        print("stage_log: could not persist the run event to %s (%s)" % (path or RUN_LOG, exc), file=sys.stderr)
        return False
    return True


def load_run(path=None):
    """(rows, unreadable): every JSON object in the run log, file order. Validation is current_run's, so a row that is
    JSON but malformed is counted there; a line that is not JSON at all is counted here."""
    rows, bad = [], 0
    try:
        with open(path or RUN_LOG, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    bad += 1
    except OSError:
        return [], 0
    return rows, bad


def current_run(snapshot, events, now):
    """The current state of snapshot['run'] from a snapshot and that run's events. Pure: reads nothing, writes nothing.

    Keys: run, sequence, candidate, state, phase, lease_until, next_action, skipped, renewed, escalated.
    The highest sequence wins. A replayed, equal or lower sequence is ignored, so a stale event cannot roll a newer
    snapshot back and an old checkpoint cannot outrank newer progress. An event of another run is ignored. A
    malformed event is skipped and counted in `skipped`. The state comes ONLY from a row: age and expiry change
    next_action (renew_monitor, then escalate), never the state, so no checkpoint age or watcher exit reads as DONE."""
    snap = snapshot if isinstance(snapshot, dict) else {}
    base = dict((k, snap.get(k)) for k in ("schema", "run", "sequence", "candidate", "state", "phase", "lease_until"))
    base["schema"] = 1
    if not run_row_ok(base):
        return {"run": snap.get("run"), "sequence": None, "candidate": None, "state": None, "phase": None,
                "lease_until": None, "next_action": "no_data", "skipped": 0, "renewed": False, "escalated": False}
    cur = dict(base)
    renewed = snap.get("renewed") is True or snap.get("monitor") in MONITOR_MARKS
    escalated = snap.get("escalated") is True or snap.get("monitor") == "escalated"
    for e in sorted((e for e in events if run_row_ok(e)), key=lambda e: e["sequence"]):
        if e["run"] != cur["run"] or e["sequence"] <= cur["sequence"]:
            continue
        cur = dict((k, e[k]) for k in base)
        if e.get("monitor") is None:      # progress resets the watch; a monitor mark keeps the state it copied
            renewed = escalated = False
        elif e["monitor"] != IDLE_MARK:   # an idle report is neither a renewal nor an escalation
            renewed = True
            escalated = escalated or e["monitor"] == "escalated"
    skipped = sum(1 for e in events if not run_row_ok(e))
    state = cur["state"]
    if state in RUN_FINAL:
        action = "none"
    elif _num(now) and now > cur["lease_until"]:
        action = "escalate" if renewed else "renew_monitor"
    elif state == "WAITING":
        action = "wake_on:" + cur["phase"]
    elif state == "NEEDS_INPUT":
        action = "ask_owner"
    else:
        action = "wait_lease"
    del cur["schema"]
    cur.update(next_action=action, skipped=skipped, renewed=renewed, escalated=escalated)
    return cur


def _run_snapshot(rows, run):
    """The lowest sequence row of `run` stands as the snapshot for a reader holding only the log; None when absent."""
    mine = [r for r in rows if run_row_ok(r) and r["run"] == run]
    return min(mine, key=lambda r: r["sequence"]) if mine else None


def _locked(path, fn):
    """Run fn() holding an exclusive lock on the run log, so two writers can never hand out one sequence twice.
    A lock still held after LOCK_WAIT_S raises OSError: a stalled holder fails this writer, never hangs it."""
    import fcntl
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as lk:
        give_up = time.time() + LOCK_WAIT_S
        while True:
            try:
                fcntl.flock(lk.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.time() >= give_up:
                    raise OSError("the run log lock is still held after %d s" % LOCK_WAIT_S)
                time.sleep(0.05)
        try:
            return fn()
        finally:
            fcntl.flock(lk.fileno(), fcntl.LOCK_UN)


def record_progress(run, candidate, state, phase, now=None, lease_s=None, path=None):
    """The next event of `run`: sequence one above the highest on disk, under the log lock. (ok, sequence)."""
    p = path or RUN_LOG
    now = time.time() if now is None else now
    lease_s = RUN_LEASE_S if lease_s is None else lease_s

    def step():
        rows, _ = load_run(p)
        seqs = [r["sequence"] for r in rows if run_row_ok(r) and r["run"] == run]
        seq = (max(seqs) + 1) if seqs else 1
        return run_event(run, seq, candidate, state, phase, now + lease_s, p), seq
    try:
        return _locked(p, step)
    except OSError as exc:
        print("stage_log: could not lock the run log %s (%s)" % (p, exc), file=sys.stderr)
        return False, None


def driver_alive(pid_file):
    """True (a live pid), False (no file: no driver, or a pid that names no process) or None (the file holds no pid:
    UNKNOWN, never read as "no driver")."""
    try:
        with open(pid_file, encoding="utf-8") as fh:
            text = fh.read().strip()
    except FileNotFoundError:
        return False
    except OSError:
        return None  # sbe: allow-silent an unreadable pid file is UNKNOWN, and the caller reports nothing on None
    if not (text.isascii() and text.isdigit()) or int(text) < 1:
        return None
    try:
        os.kill(int(text), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True      # the process exists, it is only not ours
    except (OSError, OverflowError):
        return None  # sbe: allow-silent a pid the kernel cannot judge is UNKNOWN, and the caller reports nothing on None
    return True


def monitor_step(run, now=None, lease_s=None, path=None, driver_pid_file=None, admissible=None):
    """One watcher look at `run`. On a first expiry it renews the lease with a `renewed` mark; on a second it appends
    ONE `escalated` mark; after that it appends nothing however often it looks. It never writes DONE and never starts
    anything. STAB1.h: given both a driver pid file and a count of admissible units, a look that finds no driver alive
    and a count above zero appends ONE `idle_with_work` report per run (a look that wrote an expiry mark waits for the
    next one). Returns (current, appended): appended is the mark written this look, or None."""
    p = path or RUN_LOG
    now = time.time() if now is None else now
    lease_s = RUN_LEASE_S if lease_s is None else lease_s

    def step():
        rows, _ = load_run(p)
        snap = _run_snapshot(rows, run)
        cur = current_run(snap, rows, now)
        mark = None
        if cur["next_action"] == "renew_monitor":
            mark = "renewed"
        elif cur["next_action"] == "escalate" and not cur["escalated"]:
            mark = "escalated"
        elif (admissible and driver_pid_file is not None and cur["sequence"] is not None
              and cur["state"] not in RUN_FINAL and driver_alive(driver_pid_file) is False
              and not any(run_row_ok(r) and r["run"] == run and r.get("monitor") == IDLE_MARK for r in rows)):
            mark = IDLE_MARK
        if mark and not run_event(run, cur["sequence"] + 1, cur["candidate"], cur["state"], cur["phase"],
                                  cur["lease_until"] if mark == IDLE_MARK else now + lease_s, p, monitor=mark):
            return cur, None
        return cur, mark
    try:
        return _locked(p, step)
    except OSError as exc:
        print("stage_log: could not lock the run log %s (%s)" % (p, exc), file=sys.stderr)
        return current_run(None, [], now), None


RUN_FLAGS = {"run-event": ("--run", "--candidate", "--state", "--phase", "--lease-s", "--path"),
             "current": ("--run", "--path"),
             "monitor": ("--run", "--path", "--driver-pid-file", "--admissible")}


def _flags(verb, args):
    """{flag: value} read strictly by position, or None: every argument is a known flag followed by one value that is
    not itself a flag, each flag once. A run named "--phase" or a `--path` with no value can never shift the others."""
    if len(args) % 2:
        return None
    out = {}
    for name, value in zip(args[0::2], args[1::2]):
        if name not in RUN_FLAGS[verb] or name in out or value.startswith("--"):
            return None
        out[name] = value
    return out


def run_main(args):
    """run-event --run R --candidate C --state S --phase P [--lease-s N] [--path F]: exit 0 and one RUNSTATE persisted
    line only when the row is on disk; exit 1 when it is not; exit 2 on a malformed call. current --run R and
    monitor --run R [--driver-pid-file F --admissible N] print the current state as JSON (exit 0, or 2 when the run has no event)."""
    verb = args[0]
    opts = _flags(verb, args[1:])
    if opts is None:
        print("RUNSTATE NOT persisted: malformed call, %s takes only %s, each once with one value" % (verb, " ".join(RUN_FLAGS[verb])))
        return 2
    p, run = opts.get("--path"), opts.get("--run")
    if verb == "run-event":
        cand, state, phase = opts.get("--candidate"), opts.get("--state"), opts.get("--phase")
        try:
            lease = float(opts.get("--lease-s", RUN_LEASE_S))
        except ValueError:
            lease = None
        if not (_word(run) and _sha(cand) and state in RUN_STATES and _word(phase) and _num(lease) and lease > 0):
            print("RUNSTATE NOT persisted: malformed call (run %r, candidate %r, state %r, phase %r)" % (run, cand, state, phase))
            return 2
        ok, seq = record_progress(run, cand, state, phase, lease_s=lease, path=p)
        if not ok:
            print("RUNSTATE NOT persisted: the run log %s could not take the row" % (p or RUN_LOG))
            return 1
        print("RUNSTATE persisted run=%s sequence=%d state=%s phase=%s" % (run, seq, state, phase))
        return 0
    if not _word(run):
        print("NO-DATA: %s needs --run <name>" % verb)
        return 2
    if verb == "monitor":
        pid_file, n = opts.get("--driver-pid-file"), opts.get("--admissible")
        if (pid_file is None) != (n is None) or (n is not None and not (n.isascii() and n.isdigit())):
            print("NO-DATA: monitor takes --driver-pid-file with --admissible (a whole number), or neither")
            return 2
        cur, mark = monitor_step(run, path=p, driver_pid_file=pid_file, admissible=int(n) if n else None)
        cur["appended"] = mark
    else:
        rows, _ = load_run(p)
        cur = current_run(_run_snapshot(rows, run), rows, time.time())
    print(json.dumps(cur, sort_keys=True))
    return 2 if cur["next_action"] == "no_data" else 0


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


def _idle_case():
    """STAB1.h: an idle report is not a renewal (so the escalation sequence is intact), and a run that is DONE is
    never reported idle. Two mutations survived the frozen harness until this case: the mark renewing the run, and
    the final state check."""
    import shutil
    import tempfile
    sha = "a" * 40
    snap = {"schema": 1, "run": "r", "sequence": 1, "candidate": sha, "state": "RUNNING", "phase": "p", "lease_until": 100}
    idle = dict(snap, sequence=2, monitor=IDLE_MARK)
    cur = current_run(snap, [snap, idle], 50)
    d = tempfile.mkdtemp(prefix="stage-log-idle-")
    try:
        log, pid = os.path.join(d, "log.jsonl"), os.path.join(d, "none.pid")
        ok = record_progress("r", sha, "DONE", "p", path=log)[0]
        _cur, mark = monitor_step("r", path=log, driver_pid_file=pid, admissible=3)
        return (ok and cur["renewed"] is False and cur["state"] == "RUNNING" and cur["skipped"] == 0
                and current_run(snap, [snap, idle], 150)["next_action"] == "renew_monitor" and mark is None)
    finally:
        shutil.rmtree(d, True)


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
        ("an idle report neither renews the run nor reaches a finished one", _idle_case()),
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
    if args[:1] and args[0] in ("run-event", "current", "monitor"):
        return run_main(args)
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
