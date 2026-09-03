"""progress_deadline: alive is not advancing, and output is not progress.

The one piece of watchdog that survived every objection raised against building
a supervisor, and it survived because it is not one: it calls no model, makes no
judgement, and answers a single question. Is this worker advancing, or merely
running?

THE FAILURE IT EXISTS FOR, measured here rather than imagined. On 2026-08-29 a
session went seventy two minutes between commits while making tool calls
continuously. Every liveness signal read healthy. It was producing measurement,
reports and forensics while three decomposed and ready nodes waited, and the
person who noticed was the founder.

WHAT THE FIELD DOES, from four frameworks' own documentation:

  * One agent framework flags "agent monologue", three or more consecutive agent
    messages with no user input AND NO PROGRESS, as one of five stuck patterns.
    That is exactly the failure above, and it is the only content-based detector
    found in the research rather than a bare counter.
  * A coding agent deliberately caps COST rather than STEPS, reasoning that step
    counts vary about fivefold across model families while dollar cost is
    comparable. Its default is a per-instance spend ceiling.
  * A multi-agent framework pairs a mechanical counter with a content predicate,
    and branches on whether a human is reachable: hard stop when nobody is
    there, escalate when somebody is.
  * The host this runs on publishes NO built-in repetition detector, and its own
    guidance is to track tool-call sequences across hook invocations and persist
    the state externally. So building this is the documented path rather than an
    invention.

AND THE HONEST WARNING FROM THAT SAME RESEARCH: one framework's execution
timeout was reported in its own issue tracker as a parameter that did not
actually enforce anything. A documented limit and an enforced one are different
things, which is why this module ships with a test that drives the deadline
rather than a docstring that asserts it.

WHAT COUNTS AS PROGRESS, and the exclusions matter more than the inclusions.
Only DURABLE events advance the counter: a commit, a completed check, a task
state that moved, an artifact confirmed on a remote. Deliberately NOT counted:
stdout, tokens spent, tool calls, CPU time, or a heartbeat. A worker can emit
prose indefinitely while accomplishing nothing, and every one of those signals
would have called that seventy two minutes healthy.

IT NEVER KILLS ANYTHING. It returns a verdict and a reason. Whether a stalled
worker should be interrupted, redirected, or left alone is a judgement, and the
whole argument for this module is that the cheap mechanical part must not be
tangled with the expensive judgement part.

Python 3, standard library only. No network.
"""
import argparse
import json
import subprocess
import sys
import time

ADVANCING = "ADVANCING"
STALLED = "STALLED"
UNKNOWN = "UNKNOWN"

EXIT_ADVANCING = 0
EXIT_STALLED = 1
EXIT_UNKNOWN = 2

#: How long without a durable event before STALLED. Twenty minutes, chosen from
#: the measured failure rather than from taste: the seventy two minute gap would
#: have been caught at twenty, while this estate's longest legitimate silent
#: stretch is a test battery of about ten. A shorter deadline fires on that
#: battery, and a watchdog that cries during a normal build gets switched off.
DEFAULT_DEADLINE_SECONDS = 20 * 60

#: Borrowed from the framework that caps spend rather than steps: step counts
#: vary about fivefold across model families while cost is comparable. Spend
#: with no progress is the sharper signal, because it is the one that says work
#: is being PAID FOR and not delivered. Zero disables the dimension.
DEFAULT_SPEND_CEILING = 0

#: The event kinds that advance the counter. Each is durable: it outlives the
#: process that produced it, which is what makes it evidence rather than
#: activity.
DURABLE_EVENTS = ("commit", "check_passed", "state_changed", "remote_verified")


def commit_times(window_seconds, cwd=None, runner=None):
    """Commit timestamps inside the window, or (None, problem).

    Git is the cheapest durable progress counter this estate already keeps: a
    commit is by definition work that survived the session that made it, and it
    needs no new instrumentation anywhere."""
    runner = runner or (lambda cmd, **kw: subprocess.run(
        cmd, capture_output=True, text=True, cwd=cwd, timeout=20))
    try:
        proc = runner(["git", "log", "--format=%ct",
                       "--since=%d seconds ago" % int(window_seconds)])
    except Exception as exc:  # noqa: BLE001
        # sbe: allow-silent the failure becomes UNKNOWN, which is not a pass
        return None, "could not read git history: %s" % exc
    if proc.returncode != 0:
        return None, "git log failed: %s" % (proc.stderr or "").strip()[:160]
    return [int(x) for x in (proc.stdout or "").split() if x.isdigit()], ""


def verdict(events, now, deadline=DEFAULT_DEADLINE_SECONDS, ready_work=0,
            spend=0, spend_ceiling=DEFAULT_SPEND_CEILING):
    """(verdict, reason). Pure, so every branch is testable without a clock.

    `ready_work` does not change whether a worker is stalled, but it changes
    whether anybody should care, and the reason says which: a worker with
    nothing to do is resting, not stuck.
    """
    if events is None:
        return UNKNOWN, ("no progress record could be read, so nothing is known "
                         "about whether this worker is advancing. Never having "
                         "reported is not the same as doing well")

    if not events:
        if ready_work:
            return STALLED, ("no durable progress in the last %d minute(s) while "
                             "%d unit(s) sit ready. Tool calls and output do not "
                             "count as progress"
                             % (deadline // 60, ready_work))
        return UNKNOWN, ("no durable progress in the last %d minute(s), and no "
                         "ready work is waiting, so this may simply be rest "
                         "rather than a stall" % (deadline // 60))

    idle = now - max(events)
    if idle > deadline:
        return STALLED, ("%d minute(s) since the last durable progress event%s. "
                         "A worker can emit prose indefinitely while "
                         "accomplishing nothing, so tool calls, tokens and "
                         "output are deliberately not counted"
                         % (int(idle // 60),
                            (", with %d unit(s) ready" % ready_work)
                            if ready_work else ""))

    # SPEND WITHOUT PROGRESS, the second dimension. Advancing on time can still
    # be wrong: money is going out and one commit an hour is not delivery.
    if spend_ceiling and spend > spend_ceiling:
        return STALLED, ("advancing on time (%d event(s)) but %s spent against a "
                         "ceiling of %s: work is being paid for faster than it "
                         "is being delivered"
                         % (len(events), spend, spend_ceiling))

    return ADVANCING, ("%d durable event(s) in the window, the last %d minute(s) "
                       "ago" % (len(events), int(idle // 60)))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--deadline", type=int, default=DEFAULT_DEADLINE_SECONDS)
    ap.add_argument("--ready", type=int, default=0,
                    help="units waiting, which decides rest from stall")
    ap.add_argument("--spend", type=int, default=0)
    ap.add_argument("--spend-ceiling", type=int, default=DEFAULT_SPEND_CEILING)
    ap.add_argument("--cwd")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))

    # A window wider than the deadline, so an event just OUTSIDE the deadline is
    # still seen and reported by age rather than looking like no events at all.
    events, problem = commit_times(args.deadline * 3, cwd=args.cwd)
    v, reason = verdict(events, time.time(), args.deadline, args.ready,
                        args.spend, args.spend_ceiling)
    if events is None and problem:
        reason = problem
    if args.json:
        print(json.dumps({"verdict": v, "reason": reason,
                          "events": len(events or [])}, sort_keys=True))
    else:
        print("%s: %s" % (v, reason))
    return {ADVANCING: EXIT_ADVANCING, STALLED: EXIT_STALLED}.get(v, EXIT_UNKNOWN)


if __name__ == "__main__":
    sys.exit(main())
