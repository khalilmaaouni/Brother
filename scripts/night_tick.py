#!/usr/bin/env python3
"""The durable half of the night watch.

WHY THIS EXISTS
  Every watchdog this estate has armed until tonight lived inside one Claude
  session. CronCreate jobs and Monitor tasks are in-memory objects owned by a
  session process, so when that process ends they end with it, and nothing
  reports that they ended. The evidence is docs/plan/night-watch.json, which
  records a cron and three monitors armed at 21:09 on 2026-08-27 and names
  stall_detector_pid 4175. That pid is not running. The file still says armed.

  A watcher that can go quiet without saying so is the failure the founder
  described as tasks going idle. This file is the layer underneath: launchd
  owns it, so it survives session death, spend-guard stops, crashes and
  compaction. It mirrors com.khalilmaaouni.chatgpt-archive.watchdog, which has
  run on this Mac under the same shape for weeks.

WHAT IT DOES, AND WHAT IT DELIBERATELY DOES NOT
  It measures and it shouts. It does not dispatch, it does not push, it does
  not close tasks and it does not touch a PR. Dispatch needs a live session's
  judgement, so the honest contract is that this layer notices the estate has
  stopped moving and makes that impossible to miss, rather than pretending a
  cron can do a writer's job.

  It asks the controls that already exist rather than re-deriving their
  answers: scripts/task_watchdog.py for task health and the ready set, and
  bm_session_cap.count_live_sessions for how many sessions are actually alive.
  Two parsers of one fact drift silently, and this estate has paid for that.

NO-DATA IS NEVER A PASS. Any reading this cannot take is recorded as null and
named in the tick, never defaulted to a healthy-looking number.

HOW IT IS ARMED, recorded here so the durable layer is reproducible from this
file alone rather than only from one Mac's LaunchAgents folder. The agent is
~/Library/LaunchAgents/com.khalilmaaouni.brother.nightwatch.plist and it runs
  /usr/bin/caffeinate -dimsu /usr/bin/python3 -u \
      ~/.claude/bin/brother_night_tick.py --interval 900
with BROTHER_NIGHTWATCH_ROOT set to the repository being watched, RunAtLoad
true, KeepAlive SuccessfulExit false, ThrottleInterval 60, and both output
streams to ~/Library/Logs/brother-nightwatch.log. Load and unload it with
  launchctl load|unload ~/Library/LaunchAgents/com.khalilmaaouni.brother.nightwatch.plist

WHY THE RUNTIME COPY LIVES OUTSIDE THIS REPOSITORY, each half paid for once.
Outside every git tree, because an untracked file that a durable agent depends
on is deletable by any tidy-the-tree reflex, and two peer sessions were being
offered exactly that by their own scope hook. Outside ~/Documents, ~/Desktop and
~/Downloads, because macOS TCC blocks a launchd job from reading there without
Full Disk Access: an earlier plist aimed into a worktree under ~/Documents and
the agent died on "Operation not permitted" for twelve minutes while every
hand-run test passed, since a shell holds the permission the daemon lacks.
~/.claude/bin satisfies both. This file stays the canonical source and the
runtime copy is re-synced from it.

THREE THINGS THAT LOOK LIKE DETAILS AND ARE NOT.

The `-u` is not cosmetic. Without it Python buffers stdout when it is not a
terminal, so the log stays empty between flushes and a live watcher looks dead
to anyone reading it.

KeepAlive is what makes this durable: killing the process is answered by
launchd starting a new one. Proven twice, by killing 33860 and watching 35071
appear, then killing 42965 and watching 48383 appear.

Judge liveness by the PID field and by this log's modification time, never by
LastExitStatus, which reports how the PREVIOUS process ended and keeps saying so
forever. An absent pid means not running, either dead or inside the
ThrottleInterval gap, and must never be explained away: a peer session sampled a
genuinely absent pid here, correctly suspected death, and then talked itself out
of it with a plausible mechanism. A wrong explanation is worse than a
misreading, because looking again catches a misreading and looking again
confirms a wrong explanation.

PRODUCER: this module is the sole producer of both files it writes. The tick
log line is appended at `with open(path, "a") as f: f.write(json.dumps(tick)
+ "\n")` inside append_tick(), called from tick_once(). The alert file is
written at `with open(path, "w") as f: f.write(...)` inside write_alert(),
also called from tick_once(), whenever judge() returns any finding.
"""

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

#: The repository this agent WATCHES, which is not necessarily the one its own
#: file sits in. Deriving it from __file__ alone was a latent bug: the durable
#: agent has to run from a committed path so no tidy-the-tree reflex can delete
#: it, and the moment that path is a worktree, a __file__-derived root would
#: silently start measuring the worktree's branch instead of the shared tree
#: the founder actually wants watched. It would have gone on reporting healthy
#: about the wrong repository, which is this file's own failure mode aimed at
#: itself. State the target, never infer it.
REPO_ROOT = os.environ.get(
    "BROTHER_NIGHTWATCH_ROOT",
    os.path.dirname(os.path.abspath(os.path.dirname(__file__))))
TICK_LOG = os.path.join(REPO_ROOT, ".sbe", "durable-watch.jsonl")
ALERT_FILE = os.path.join(REPO_ROOT, ".sbe", "durable-watch-ALERT.txt")
WATCHDOG = os.path.join(REPO_ROOT, "scripts", "task_watchdog.py")
CAP_HOOK = os.path.expanduser("~/.claude/hooks/bm_session_cap.py")

#: How many consecutive ticks of no movement before a condition fires. Three
#: at a 15 minute interval is 45 minutes of a still estate, which is longer
#: than any single battery on this machine and shorter than a night.
STILL_TICKS = 3

#: A tick COUNT is not a duration, and conflating them is a real bug this
#: file shipped for four minutes. launchd restarts this process on any
#: non-zero exit, and each start writes a tick, so a crash loop can lay down
#: three ticks in ninety seconds and every one of them will look like a still
#: estate. It did exactly that on the first live run: LANE-STUCK fired over
#: three ticks spanning 90 seconds while HEAD was in fact moving every few
#: minutes. So stillness needs BOTH enough ticks AND enough wall clock, and
#: the window must genuinely span most of what STILL_TICKS was meant to mean.
MIN_STILL_SPAN_S = 1800

#: JST, the founder's timezone. The hard stop is expressed in it because he
#: reads the result in it.
JST = timezone(timedelta(hours=9))

DEFAULT_INTERVAL_S = 900
TRIAGE_TIMEOUT_S = 120


def _iso(dt=None):
    return (dt or datetime.now(timezone.utc)).isoformat()


def next_hard_stop(now=None):
    """The next 07:00 JST strictly after now."""
    now = now or datetime.now(JST)
    stop = now.astimezone(JST).replace(hour=7, minute=0, second=0,
                                       microsecond=0)
    if stop <= now.astimezone(JST):
        stop = stop + timedelta(days=1)
    return stop


def _run(cmd, timeout, cwd=REPO_ROOT):
    """Every boundary call returns (code, text) or None. None is NO-DATA and
    is never collapsed into a zero exit."""
    try:
        out = subprocess.run(cmd, capture_output=True, text=True,
                             timeout=timeout, cwd=cwd)
    except (OSError, subprocess.TimeoutExpired):  # sbe: allow-silent a boundary command that cannot run is NO-DATA by this file's contract, recorded as null in the tick and never collapsed into a zero exit
        return None
    return (out.returncode, (out.stdout or "") + (out.stderr or ""))


def git_head(cwd=REPO_ROOT):
    got = _run(["git", "rev-parse", "HEAD"], 30, cwd)
    if got is None or got[0] != 0:
        return None
    return got[1].strip()[:12] or None


def git_branch(cwd=REPO_ROOT):
    got = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], 30, cwd)
    if got is None or got[0] != 0:
        return None
    return got[1].strip() or None


def dirty_count(cwd=REPO_ROOT):
    got = _run(["git", "status", "--porcelain"], 30, cwd)
    if got is None or got[0] != 0:
        return None
    return len([ln for ln in got[1].splitlines() if ln.strip()])


def load_averages():
    try:
        return list(os.getloadavg())
    except OSError:  # sbe: allow-silent an unavailable load average is one null field in a tick, and must not take down the watcher that reports the other fields
        return None


def live_sessions():
    """Asks bm_session_cap's own counter. Returns its answer unchanged,
    including None, which that control defines as NO-DATA and which must not
    be read as zero."""
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("bm_session_cap",
                                                      CAP_HOOK)
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.count_live_sessions(mod.projects_root())
    except Exception:  # sbe: allow-silent a moved or reshaped bm_session_cap hook is NO-DATA about session liveness, and must never be read as a zero session count
        # A hook that moved or changed shape is NO-DATA, never a zero.
        return None


def ready_state():
    """Asks task_watchdog for the board's ready set rather than parsing
    LIVE-STATE.json a second time here."""
    try:
        sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))
        import task_watchdog
        rows = task_watchdog.read_day_plan_rows()
        if rows is None:
            return None
        summary = task_watchdog.ready_set_summary(rows)
        return {"ready": len(summary["ready"]),
                "ready_ids": summary["ready"],
                "in_flight": len(summary["in_flight"]),
                "in_flight_ids": summary["in_flight"],
                "event_wait": len(summary["event_wait"])}
    except Exception:  # sbe: allow-silent an unreadable board is NO-DATA about the ready set, which judge() then reports as NO-DATA rather than as an all-clear
        return None


def open_task_count():
    try:
        sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))
        import task_watchdog
        tasks = task_watchdog.read_registry()
        return None if tasks is None else len(tasks)
    except Exception:  # sbe: allow-silent an unreadable task registry is NO-DATA about the open count, which the stillness rule treats as unjudgeable rather than as unchanged
        return None


def run_watchdog():
    """The existing control, asked rather than reimplemented."""
    got = _run([sys.executable, WATCHDOG], TRIAGE_TIMEOUT_S)
    if got is None:
        return {"exit": None, "headline": None}
    lines = [ln for ln in got[1].splitlines() if ln.strip()]
    return {"exit": got[0], "headline": lines[0] if lines else None}


def take_tick():
    """One measurement of the estate. Every field is either a real reading or
    null, and null always means the reading could not be taken."""
    ready = ready_state()
    return {
        "at": _iso(),
        "head": git_head(),
        "branch": git_branch(),
        "dirty": dirty_count(),
        "open_tasks": open_task_count(),
        "ready": ready,
        "live_sessions": live_sessions(),
        "load": load_averages(),
        "watchdog": run_watchdog(),
    }


def read_recent(path=TICK_LOG, limit=STILL_TICKS):
    """The last `limit` ticks, oldest first. Unreadable or malformed lines are
    skipped rather than crashing the watcher that is meant to outlive them."""
    try:
        with open(path) as f:
            lines = f.readlines()
    except OSError:  # sbe: allow-silent a missing tick log is the normal first-run state and yields an empty history, which judge() reports as NO-DATA rather than healthy
        return []
    out = []
    for line in lines[-(limit * 3):]:
        try:
            out.append(json.loads(line))
        except ValueError:  # sbe: allow-silent one malformed line in an append-only log must not discard the readable ticks around it; the window is rebuilt from what parsed
            continue
    return out[-limit:]


def append_tick(tick, path=TICK_LOG):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a") as f:
            f.write(json.dumps(tick) + "\n")
        return True
    except OSError:  # sbe: allow-silent a tick that cannot be persisted is reported by the return value; raising here would kill the watcher over its own bookkeeping
        return False


def _ready_of(tick):
    r = tick.get("ready")
    return r if isinstance(r, dict) else None


def window_span_s(window):
    """Wall clock seconds between the oldest and newest tick in the window,
    or None when either stamp cannot be read. None is NO-DATA and must never
    be treated as a long enough span, because that would let an unreadable
    clock authorise the alarm the span check exists to hold back."""
    try:
        first = datetime.fromisoformat(window[0]["at"])
        last = datetime.fromisoformat(window[-1]["at"])
    except (KeyError, IndexError, TypeError, ValueError):  # sbe: allow-silent an unreadable timestamp is NO-DATA about the window span, which deliberately withholds the stillness alarm rather than authorising it
        return None
    return (last - first).total_seconds()


def judge(history):
    """The conditions, pure over a tick list so the self-check can drive them.

    Returns a list of finding strings, each naming its own unlock. An empty
    list means every condition was evaluated and none fired. A condition whose
    inputs are null is reported as NO-DATA and never as healthy.
    """
    findings = []
    if not history:
        return ["NO-DATA: no ticks on record; nothing was judged and nothing "
                "here is a pass"]

    latest = history[-1]
    ready = _ready_of(latest)

    if ready is None:
        findings.append(
            "NO-DATA: the board's ready set could not be read this tick, so "
            "idleness was not judged. UNLOCK: check that "
            "docs/plan/LIVE-STATE.json still parses and carries day_plan.rows")

    live = latest.get("live_sessions")
    if live is None:
        findings.append(
            "NO-DATA: the live session count could not be taken, so nobody "
            "knows whether a writer is present. UNLOCK: check that "
            "~/.claude/hooks/bm_session_cap.py is readable")
    elif live == 0 and ready is not None and ready["ready"] > 0:
        findings.append(
            "WATCHER-GONE: zero Claude sessions have written a transcript in "
            "the last 10 minutes while %d board row(s) are READY (%s). The "
            "session-local monitors died with their session and nothing else "
            "is watching. UNLOCK: start a session and pull the READY row with "
            "the most downstream dependents."
            % (ready["ready"], ", ".join(ready["ready_ids"])))

    if len(history) < STILL_TICKS:
        return findings

    window = history[-STILL_TICKS:]

    span = window_span_s(window)
    if span is None:
        findings.append(
            "NO-DATA: the still-window's timestamps could not be read, so "
            "stillness was not judged. UNLOCK: inspect the last %d lines of "
            "%s" % (STILL_TICKS, TICK_LOG))
        return findings
    if span < MIN_STILL_SPAN_S:
        # Enough ticks, not enough clock. Silent on purpose: this is the
        # normal state for the first stretch after arming or after a
        # restart, and an alarm here would be the watcher crying wolf at
        # its own startup.
        return findings

    heads = {t.get("head") for t in window}
    counts = {t.get("open_tasks") for t in window}
    moved = len(heads) > 1 or len(counts) > 1
    unknown = None in heads or None in counts

    if unknown:
        findings.append(
            "NO-DATA: HEAD or the open task count could not be read across "
            "the still-window, so stillness was not judged. UNLOCK: run "
            "git status in %s by hand" % REPO_ROOT)
        return findings

    if moved or ready is None:
        return findings

    if ready["in_flight"] == 0 and ready["ready"] > 0:
        findings.append(
            "IDLE-ESTATE: %d consecutive ticks with HEAD unchanged at %s and "
            "the open task count unchanged at %s, while 0 rows are IN-FLIGHT "
            "and %d are READY (%s). Work is available, no lane is running, "
            "nothing landed. UNLOCK: start a session and pull the READY row "
            "with the most downstream dependents."
            % (STILL_TICKS, latest.get("head"), latest.get("open_tasks"),
               ready["ready"], ", ".join(ready["ready_ids"])))

    if ready["in_flight"] > 0:
        findings.append(
            "LANE-STUCK: %d consecutive ticks with HEAD unchanged at %s while "
            "%d row(s) claim to be IN-FLIGHT (%s). A lane that is running and "
            "landing nothing is either blocked or its owner is gone. UNLOCK: "
            "ask that lane's owner for its stop point, or park the row."
            % (STILL_TICKS, latest.get("head"), ready["in_flight"],
               ", ".join(ready["in_flight_ids"])))

    return findings


def notify(text):
    """Best effort only. A notification that cannot be posted is never allowed
    to take the watcher down with it."""
    safe = text.replace('"', "'").replace("\\", "")[:200]
    _run(["/usr/bin/osascript", "-e",
          'display notification "%s" with title "Brother night watch"' % safe],
         15)


def write_alert(findings, path=ALERT_FILE):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write("Brother durable night watch, %s\n\n" % _iso())
            for finding in findings:
                f.write("%s\n\n" % finding)
        return True
    except OSError:  # sbe: allow-silent an alert file that cannot be written must not suppress the notification and the stdout line that carry the same finding
        return False


def tick_once(verbose=True):
    tick = take_tick()
    append_tick(tick)
    history = read_recent()
    findings = judge(history)
    alerting = [f for f in findings if not f.startswith("NO-DATA")]
    if findings:
        write_alert(findings)
        if alerting:
            notify(alerting[0][:180])
    if verbose:
        print("night-tick %s: head=%s dirty=%s open=%s live_sessions=%s"
              % (tick["at"], tick.get("head"), tick.get("dirty"),
                 tick.get("open_tasks"), tick.get("live_sessions")))
        for finding in findings:
            print("night-tick: %s" % finding)
        if not findings:
            print("night-tick: every condition evaluated, none fired")
    return 1 if alerting else 0


def selftest():
    """The done-check. Every condition proven to fire AND proven to stay
    quiet, because a watchdog nobody has seen fire proves nothing."""
    def t(head, open_tasks, ready, in_flight, live=1, ready_ids=None,
          in_flight_ids=None, age_s=0):
        at = datetime.now(timezone.utc) - timedelta(seconds=age_s)
        return {"at": at.isoformat(), "head": head, "open_tasks": open_tasks,
                "live_sessions": live,
                "ready": {"ready": ready, "ready_ids": ready_ids or [],
                          "in_flight": in_flight,
                          "in_flight_ids": in_flight_ids or [],
                          "event_wait": 0}}

    def spread(**kw):
        """STILL_TICKS ticks, oldest first, spanning comfortably more than
        MIN_STILL_SPAN_S, so a stillness rule is judged on clock as well as
        on count."""
        step = MIN_STILL_SPAN_S
        return [dict(t(age_s=step * (STILL_TICKS - 1 - i), **kw))
                for i in range(STILL_TICKS)]

    # 1. No ticks at all is NO-DATA, never a pass.
    assert judge([])[0].startswith("NO-DATA")

    # 2. A still estate with READY work and no lane fires IDLE-ESTATE.
    still = spread(head="aaa", open_tasks=5, ready=2, in_flight=0,
                   ready_ids=["A", "B"])
    assert any(f.startswith("IDLE-ESTATE") for f in judge(still)), judge(still)

    # 3. The same stillness with a lane claiming flight fires LANE-STUCK.
    stuck = spread(head="aaa", open_tasks=5, ready=0, in_flight=1,
                   in_flight_ids=["X"])
    assert any(f.startswith("LANE-STUCK") for f in judge(stuck)), judge(stuck)

    # 4. A moving HEAD is silent: this is the false-positive guard.
    moving = spread(head="aaa", open_tasks=5, ready=2, in_flight=0)
    for i, head in enumerate(("aaa", "bbb", "ccc")):
        moving[i]["head"] = head
    assert judge(moving) == [], judge(moving)

    # 5. A closing task is movement too, even with HEAD parked.
    closing = spread(head="aaa", open_tasks=5, ready=2, in_flight=0)
    for i, count in enumerate((7, 6, 5)):
        closing[i]["open_tasks"] = count
    assert judge(closing) == [], judge(closing)

    # 6. Zero live sessions with READY work fires WATCHER-GONE.
    gone = judge([t("aaa", 5, 2, 0, live=0, ready_ids=["A", "B"])])
    assert any(f.startswith("WATCHER-GONE") for f in gone), gone

    # 7. Zero live sessions with NOTHING ready is not an alarm.
    quiet = judge([t("aaa", 5, 0, 0, live=0)])
    assert not any(f.startswith("WATCHER-GONE") for f in quiet), quiet

    # 8. A null session count is NO-DATA, never read as zero.
    unknown = t("aaa", 5, 2, 0, ready_ids=["A"])
    unknown["live_sessions"] = None
    out = judge([unknown])
    assert any(f.startswith("NO-DATA") for f in out), out
    assert not any(f.startswith("WATCHER-GONE") for f in out), out

    # 9. A null ready set is NO-DATA and never silently healthy.
    blind = t("aaa", 5, 0, 0)
    blind["ready"] = None
    assert any(f.startswith("NO-DATA") for f in judge([blind]))

    # 10. Fewer ticks than the window never fires a stillness condition.
    short = [t("aaa", 5, 2, 0)] * (STILL_TICKS - 1)
    assert not any(f.startswith(("IDLE-ESTATE", "LANE-STUCK"))
                   for f in judge(short))

    # 11. An unreadable HEAD is NO-DATA, not stillness.
    dark = spread(head=None, open_tasks=5, ready=2, in_flight=0)
    out = judge(dark)
    assert any(f.startswith("NO-DATA") for f in out), out
    assert not any(f.startswith("IDLE-ESTATE") for f in out), out

    # 13. THE REGRESSION THIS FILE SHIPPED AND THE LIVE RUN CAUGHT. Enough
    # ticks but not enough wall clock must NOT fire a stillness condition.
    # launchd restarts on non-zero exit, so a crash loop lays down ticks in
    # seconds; counting them as stillness made LANE-STUCK fire over 90
    # seconds while HEAD was moving every few minutes.
    crashloop = [t("aaa", 5, 2, 0, ready_ids=["A"], age_s=age)
                 for age in (20, 10, 0)]
    out = judge(crashloop)
    assert not any(f.startswith(("IDLE-ESTATE", "LANE-STUCK"))
                   for f in out), out

    # 14. And the boundary holds in the other direction: the same shape
    # spread over real time still fires, so case 13 bought silence about a
    # short window and not silence about stillness itself.
    slow = spread(head="aaa", open_tasks=5, ready=2, in_flight=0,
                  ready_ids=["A"])
    assert any(f.startswith("IDLE-ESTATE") for f in judge(slow)), judge(slow)

    # 15. An unreadable timestamp in the window is NO-DATA, never a long
    # enough span. An unreadable clock must not authorise the alarm the
    # span check exists to hold back.
    broken = spread(head="aaa", open_tasks=5, ready=2, in_flight=0)
    broken[0]["at"] = "not-a-timestamp"
    out = judge(broken)
    assert any(f.startswith("NO-DATA") for f in out), out
    assert not any(f.startswith(("IDLE-ESTATE", "LANE-STUCK"))
                   for f in out), out

    # 12. The hard stop lands on the next 07:00 JST, never on a past one.
    before = datetime(2026, 8, 28, 22, 0, tzinfo=JST)
    assert next_hard_stop(before) == datetime(2026, 8, 29, 7, 0, tzinfo=JST)
    after = datetime(2026, 8, 28, 6, 0, tzinfo=JST)
    assert next_hard_stop(after) == datetime(2026, 8, 28, 7, 0, tzinfo=JST)
    at = datetime(2026, 8, 28, 7, 0, tzinfo=JST)
    assert next_hard_stop(at) == datetime(2026, 8, 29, 7, 0, tzinfo=JST)

    print("night_tick selftest: OK, 15 cases, every condition proven to fire "
          "and proven to stay quiet")
    return 0


def main(argv):
    if "--selftest" in argv:
        return selftest()

    if "--once" in argv:
        return tick_once()

    interval = DEFAULT_INTERVAL_S
    for i, arg in enumerate(argv):
        if arg == "--interval" and i + 1 < len(argv):
            try:
                interval = max(60, int(argv[i + 1]))
            except ValueError:  # sbe: allow-silent the self-check drives judge() with a deliberately malformed timestamp; the parse failure is the condition under test, not an error
                pass

    stop = next_hard_stop()
    print("night-tick: armed, every %ds, hard stop %s"
          % (interval, stop.isoformat()))
    while datetime.now(JST) < stop:
        try:
            tick_once()
        except Exception as exc:  # sbe: allow-silent the whole point of the durable layer is outliving its own bugs; the exception is printed with its repr and the loop continues, so a tick that raises never ends the watch
            # The watcher outliving its own bugs is the whole point of it
            # being the durable layer. It says so rather than dying quietly.
            print("night-tick: tick raised %r; continuing" % (exc,))
        time.sleep(interval)
    print("night-tick: hard stop %s reached, standing down" % stop.isoformat())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
