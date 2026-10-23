#!/usr/bin/env python3
"""The run's watcher: one process armed beside the driver that says a run needs a human, once per cause.

WHY (measured 2026-10-19 and 2026-10-20): a run sat HELD on the same unchanged tree for fourteen passes and nobody was
told, because the driver's own alarms fire on terminal states and a HELD is not one. This reads what the driver already
writes, its log and its heartbeat, and raises exactly two kinds of alarm:

  heartbeat  the heartbeat rule (loop_heartbeat.classify, never a second copy of it) reads ALARM for THIS run's pulse
  held       the same HELD cause (a `HELD    <sub> <why>` row or a `DEPLOY  HELD | <detail>` row of the driver log)
             is still there on two consecutive passes: a hold that persists, not one that cleared

Each cause writes ONE alarm record into the run folder (watch-alarm-NNN.json, atomic) and is announced ONCE through a
desktop notification, as loop_until.sh's announce does. It cannot wake a Claude session; a session reading the run
folder's alarm records is still the one that acts.

FAIL DIRECTIONS. A missing, torn or foreign heartbeat is ignored and the watcher keeps reading (a pulse from ANOTHER
run's log is not this run's alarm: the driver's first minute still shows the last run's pulse). A missing or unreadable
log is no causes. A gone driver ends the watcher with exit 0: it never holds the driver's exit. A run folder that cannot
be written is exit 2, before the first pass when it is already unwritable, and after the announcement when it breaks
mid run (the record is the durable half, the announcement still goes out).

usage: run_watch.py --pid N --log DRIVER_LOG --heartbeat PATH --run-dir DIR [--interval SECONDS]
exit:  0 the driver is gone   2 a refused argument or an unwritable run folder
"""
import argparse, datetime, json, math, os, re, subprocess, sys, tempfile, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import loop_heartbeat as _hb  # noqa: E402  (the one heartbeat rule: this module reads it, never copies it)

HELD_ROW = re.compile(r"^HELD {2,}(\S+)(?: +(.*))?$")
DEPLOY_ROW = re.compile(r"^DEPLOY +HELD \|(?: +(.*))?$")
INTERVAL_S = 30.0
#: THE WATCHER'S OWN STALENESS BOUND, in minutes. loop_heartbeat's own bounds (25 minutes of silence, a 12 minute STARTED
#: grace) fit a loop that is cheap to look at, not a watcher that pages a human: a first pass was measured at 75 minutes
#: (the 2026-10-19 run), a landing pass runs the whole gate, and each of those is a LIVE driver. 150 is twice the measured
#: pass. Past it, a non terminal pulse is a wedge, not a slow pass. Terminal states, an unreadable stamp, a stamp from the
#: future and a garbage counter still read ALARM at once: only the AGE rule is widened.
WATCH_BOUND_MINUTES = 150


def _verdict(rec):
    """(state, why) of the heartbeat rule for one record, with this watcher's bound; None when the record cannot be
    judged (a state that is not a string, a field the rule cannot read): that is NO-DATA, never a crash."""
    if rec is None:
        return _hb.classify(None)
    try:
        if not isinstance(rec.get("state", ""), str):
            return None
        state, why = _hb.classify(rec, stale_minutes=WATCH_BOUND_MINUTES)
        if state == "ALARM" and rec.get("state") == "STARTED":
            age = _hb.age_minutes(rec)       # a first pass is STARTED until it ends: slow is not wedged, up to the bound
            if age is not None and -_hb.CLOCK_SKEW_MINUTES <= age <= WATCH_BOUND_MINUTES:
                return "PRODUCING", "the first pass is %d minutes in, inside the %d minute bound" % (age, WATCH_BOUND_MINUTES)
        return state, why
    except Exception:   # sbe: allow-silent a pulse this rule cannot read is NO-DATA, named by the caller; the watcher keeps going
        return None


def classify(rec):
    """The state word for one heartbeat record: loop_heartbeat's own rule with the watcher's age bound, or NO-DATA."""
    verdict = _verdict(rec)
    return "NO-DATA" if verdict is None else verdict[0]


def read_heartbeat(path):
    """The heartbeat record at path, or None for a missing, unreadable, torn or non dict file (never raises)."""
    if not isinstance(path, str):
        return None
    return _hb.read(path)


def last_run_lines(lines):
    """The lines from the LAST `RUN START` row onward (all of them when there is none): a launch in the same minute appends
    to an older run's log, and an older run's holds are not this run's. The one slicer: loop_report's from_last_run reads it."""
    starts = [i for i, line in enumerate(lines) if line.startswith("RUN START")]
    return lines[starts[-1]:] if starts else lines


def held_causes(log_path):
    """The cause of every HELD row and every DEPLOY HELD row of the driver log, in log order (duplicates kept).
    A HELD row is `<sub> <why>`, whitespace squeezed; one naming no why reads `<sub> UNEXPLAINED`. A DEPLOY row reads
    `deploy: <detail>`, `deploy: UNEXPLAINED` when it names none. Only the last run of the log is read (last_run_lines). A missing, unreadable or non text log is no causes."""
    if not isinstance(log_path, str):
        return []
    try:
        with open(log_path, encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return []
    out = []
    for line in last_run_lines(lines):
        line = line.rstrip()
        m = HELD_ROW.match(line)
        if m:
            why = " ".join((m.group(2) or "").split())
            out.append("%s %s" % (m.group(1), why or "UNEXPLAINED"))
            continue
        m = DEPLOY_ROW.match(line)
        if m:
            out.append("deploy: %s" % (" ".join((m.group(1) or "").split()) or "UNEXPLAINED"))
    return out


def _pid_alive(pid):
    """True only when a process with this pid exists. A bool, a non int or a pid that names no one process is False."""
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True            # it exists and is not ours to signal
    except OSError:
        return False
    return True


def _notify(title, msg):
    """A desktop banner AND a detached alert window, as loop_until.sh's announce() does (Focus swallows a banner and
    osascript still exits 0, 2026-09-22); never raises. Quotes and backslashes are dropped, the text is cut to 200
    characters. The alert is started with a trailing `&` inside its own shell, so it never holds this watcher."""
    text = re.sub(r'["\\]', "", str(msg))[:200]
    head = re.sub(r'["\\]', "", str(title))
    banner = 'display notification "%s" with title "%s"' % (text, head)
    alert = 'display alert "%s" message "%s" as critical' % (head, text)
    quiet = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
    try:
        subprocess.run(["osascript", "-e", banner], check=True, **quiet)
        subprocess.run(["sh", "-c", 'osascript -e "$1" >/dev/null 2>&1 &', "sh", alert], check=True, **quiet)
    except (OSError, subprocess.SubprocessError):   # a non zero exit (CalledProcessError) is a SubprocessError too
        pass   # sbe: allow-silent an announcement that cannot be shown must never end the watcher; the alarm record is the durable half


def _write_record(run_dir, seq, kind, cause, detail, driver_pid, log_path):
    """watch-alarm-NNN.json, written atomically. Raises OSError when the folder cannot take it."""
    rec = {"schema": "run-watch-alarm-v1", "seq": seq, "kind": kind, "cause": cause, "detail": detail,
           "at": time.time(), "driver_pid": driver_pid, "log": log_path}
    final = os.path.join(run_dir, "watch-alarm-%03d.json" % seq)
    fd, tmp = tempfile.mkstemp(prefix=".watch-alarm-", dir=run_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(rec, fh, indent=1)
        os.replace(tmp, final)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass   # sbe: allow-silent the temp file may be gone with its folder; the OSError re-raised below is the verdict
        raise
    return final


def _refusal(driver_pid, log_path, run_dir, interval):
    """Why this watch cannot start, or ''. The run folder is made when absent and must take a file."""
    if isinstance(driver_pid, bool) or not isinstance(driver_pid, int) or driver_pid <= 0:
        return "the driver pid %r is not a positive whole number" % (driver_pid,)
    if isinstance(interval, bool) or not isinstance(interval, (int, float)) or not math.isfinite(interval) or interval <= 0:
        return "the interval %r is not a positive number of seconds" % (interval,)
    if not isinstance(log_path, str) or os.path.isdir(log_path):
        return "the driver log %r is not a file path" % (log_path,)
    if not isinstance(run_dir, str) or not run_dir:
        return "the run folder %r is not a path" % (run_dir,)
    try:
        os.makedirs(run_dir, exist_ok=True)
        fd, probe = tempfile.mkstemp(prefix=".watch-probe-", dir=run_dir)
        os.close(fd)
        os.unlink(probe)
    except OSError as exc:
        return "the run folder %s cannot be written (%s)" % (run_dir, type(exc).__name__)
    return ""


def _stamped_after(rec, started):
    """True when the record's own stamp is later than `started` (whole seconds): written by the driver that is running now."""
    try:
        return datetime.datetime.strptime(rec["stamp"], "%Y-%m-%d %H:%M:%S") > started
    except (KeyError, TypeError, ValueError):
        return False


def _own_pulse(rec, log_path, started, seen):
    """The heartbeat record when it is THIS run's, else None. It is this run's when it names this run's log; one that names
    another log is another run's. One that names no log (the pass pulse carries none) is this run's only when this watcher
    has already seen a pulse naming its log (`seen`, a one item dict), or the record is stamped after this watcher began:
    a record from the run before, still on disk in the driver's first minute, is neither. A torn file is None."""
    if rec is None:
        return None
    named = rec.get("log")
    if named in (None, ""):
        return rec if seen.get("own") or _stamped_after(rec, started) else None
    if isinstance(named, str) and os.path.abspath(named) == os.path.abspath(log_path):
        seen["own"] = True
        return rec
    return None


def watch(driver_pid, log_path, heartbeat_path, run_dir, interval=INTERVAL_S, notify=None, sleep=time.sleep):
    """Watch until the driver is gone. Returns 0 then, 2 for a refused argument or an unwritable run folder (said on
    stderr). notify(title, message) and sleep(seconds) are seams; the defaults are a desktop notification and time.sleep.

    A HEARTBEAT ALARM IS AN EPISODE: announced once when it begins, and re announced only after a read that is not ALARM
    ended it (a healthy pass, then a wedge hours later, is a second episode, never the first one's silence). A HELD cause
    is announced once. When the driver is gone the heartbeat is read once more: anything but a clean end (FINISHED, DEADLINE
    or STOPPED without end_failed) is announced, since a SIGKILLed driver writes nothing and leaves its last pulse behind."""
    why = _refusal(driver_pid, log_path, run_dir, interval)
    if why:
        sys.stderr.write("run_watch: refused: %s\n" % why)
        return 2
    notify = notify or _notify
    started = datetime.datetime.now().replace(microsecond=0)
    seen = {}
    announced, previous, seq = set(), set(), 0
    alarm_open, episode = False, 0

    def raise_alarm(kind, cause, detail):
        """Record then announce one cause. False when the run folder could not take the record (announced anyway)."""
        nonlocal seq
        announced.add(cause)        # before the write: a record that fails is not retried and re-announced forever
        seq += 1
        try:
            _write_record(run_dir, seq, kind, cause, detail, driver_pid, log_path)
        except OSError as exc:
            notify("brother.loop watch", "%s (and the alarm record could not be written: %s)" % (cause, type(exc).__name__))
            sys.stderr.write("run_watch: the run folder %s cannot be written (%s)\n" % (run_dir, type(exc).__name__))
            return False
        notify("brother.loop watch", cause)
        return True

    while True:
        if not _pid_alive(driver_pid):
            rec = _own_pulse(read_heartbeat(heartbeat_path), log_path, started, seen)
            state = rec.get("state") if rec is not None else None
            if isinstance(state, str) and not alarm_open and not (state in _hb.GOOD_TERMINAL and rec.get("end_failed") is not True):
                cause = "driver gone while its heartbeat said %s (stamped %s)" % (state or "nothing", rec.get("stamp"))
                if not raise_alarm("driver-gone", cause, "no clean end was recorded: a killed driver writes nothing"):
                    return 2
            return 0
        causes = set(held_causes(log_path))
        due = []        # (kind, cause, detail) in the order they are raised this pass
        rec = _own_pulse(read_heartbeat(heartbeat_path), log_path, started, seen)
        if rec is not None:          # a missing, torn, foreign or unreadable pulse is ignored, never an alarm
            if classify(rec) == "ALARM":
                if not alarm_open:
                    alarm_open = True
                    episode += 1
                    due.append(("heartbeat", "heartbeat ALARM while %s (episode %d)" % (rec.get("state") or "unknown", episode),
                                _verdict(rec)[1]))
            elif classify(rec) != "NO-DATA":
                alarm_open = False   # a read that is not ALARM ends the episode: the next wedge is announced again
        for held in sorted(causes & previous):
            due.append(("held", held, "the same HELD cause on two consecutive passes"))
        previous = causes
        for kind, cause, detail in due:
            if cause in announced:
                continue
            if not raise_alarm(kind, cause, detail):
                return 2
        sleep(interval)


def main(argv=None):
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--pid", type=int, required=True)
    ap.add_argument("--log", required=True)
    ap.add_argument("--heartbeat", required=True)
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--interval", type=float, default=INTERVAL_S)
    try:
        a = ap.parse_args(sys.argv[1:] if argv is None else argv)
    except SystemExit:
        return 2
    return watch(a.pid, a.log, a.heartbeat, a.run_dir, a.interval)


if __name__ == "__main__":
    sys.exit(main())
