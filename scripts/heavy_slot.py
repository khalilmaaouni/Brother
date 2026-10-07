"""heavy_slot: heavy work waits its turn for a machine slot instead of piling on.

THE FAILURE THIS CLOSES, measured 2026-09-26 on this machine. The pre-merge
gate (scripts/required_fast.sh) summed 535 s on Sep 22 and 1,640 to 2,292 s on
Sep 26. The code had not slowed down: the same checks re-run alone on the Sep 26
tree took 21 s (integrate) and 33 s (bundle mirror) against 94 s and 282 s
inside that day's gate, and EVERY check, related or not, was 3 to 9 times slower
at once. Load averages of 81 to 215 on 8 cores are on record. Every session ran
its gates on top of every other session's gates, graders and batteries, and the
machine thrashed. Admission code existed (resource_gate.py refuses,
machine_reservation.py leases one holder), but neither gate called it, and a
refusal is the wrong answer for a gate anyway: the caller needs the verdict, so
the right answer is to WAIT IN LINE.

THE RULE: a heavy command takes one of N machine slots before it starts and
gives it back when it ends. The slots are file locks in ONE directory shared by
every session on the machine, the same directory and file names the loop's
grader already uses (local-slots/slot-<i>, LOCAL_SLOTS), so gates and graders
queue in one line rather than two. A lock dies with its process, so a crashed or
killed holder frees its slot by itself: nothing to reap.

USE: python3 scripts/heavy_slot.py <command> [args...]
The command runs as a child with BROTHER_HEAVY_SLOT_HELD set, and its exit code
is returned unchanged.

CONTINGENCY AND EDGES, named here because this sits in front of every gate:
  NESTED. A gate that runs another gate (check_all.sh, a test that executes the
  real script) must not wait for a second slot behind itself. When
  BROTHER_HEAVY_SLOT_HELD is already set, the command runs at once.
  OFF. BROTHER_HEAVY_SLOT=off runs the command at once, for a machine that
  wants the old behaviour or a harness that queues on its own.
  SLOT COUNT. LOCAL_SLOTS when it is a positive integer, else half the cores
  (at least 1). An unreadable value falls back to the default and says so.
  LOAD CEILING (owner, 2026-09-27: "optimize it to perfection to not be a hog"). A free slot is not enough: the
  run starts only when the cores measured busy (kernel CPU ticks, the busiest of the last three seconds; load1 only
  where ticks cannot be read, because on this M3 load1 read 5.10 with 3.4 to 4.5 cores busy) plus the runs admitted
  in the last 15 s (60 s on load1) plus its own weight stays at or under the
  ceiling, every core but resource_gate.CORE_RESERVE (6 on this 8 core M3). load1 is a one minute average, so a run
  admitted seconds ago is not in it yet; each admission writes its start time into its slot label and the gate counts
  those, which is what stops a burst of waiters all seeing the same low figure. The lock is taken before the load is
  read and the label written only once admitted, so a waiter that backs off leaves no trace a count could mistake
  for a start. An unreadable load waits, never reads as idle. BROTHER_LOAD_CEILING may lower the ceiling, never
  raise it. BROTHER_LOAD_READING_FILE is the test seam: a file holding the load1 figure to read instead.
  PRIORITY. An admitted run starts at nice BROTHER_HEAVY_SLOT_NICE (default 5, 0 to 19): under contention the
  person's own programs win, while Brother's runs still get every idle cycle (a mild nice, never the background
  QoS class that starved a critical check on 2026-09-26).
  NO SLOT WITHIN THE BOUND. BROTHER_HEAVY_SLOT_WAIT seconds (default 3600).
  After that the command runs UNQUEUED and says so on stderr. This is a speed
  control, not a correctness control: refusing would turn a busy machine into
  a missing verdict, the exact confusion resource_gate.py was written against.
  So liveness wins, loudly.
  SLOT DIRECTORY UNUSABLE (cannot create, cannot open a lock file). Runs
  unqueued, loudly, for the same reason.
  SIGNALS. SIGTERM, SIGINT and SIGHUP sent to this wrapper are passed to the
  child, and the wrapper then waits for it, so a pid kill still stops the work
  and the slot is released only when the work has actually ended.
  COMMAND NOT FOUND. Exit 127, the shell's own code for it.
  WEIGHT. BROTHER_HEAVY_SLOT_WEIGHT=k takes k slots at once, for a command
  that itself runs k things side by side (the fast gate runs its checks
  REQUIRED_FAST_JOBS at a time). All or nothing: a pass that finds fewer than
  k free takes none, so two weighted waiters can never each hold half of what
  they need and block each other. k above the pool size is capped at it.

Python 3, standard library only. No network.
"""
import fcntl
import json
import os
import re
import signal
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import brother_paths  # noqa: E402
import resource_gate  # noqa: E402

HELD_ENV = "BROTHER_HEAVY_SLOT_HELD"
SWITCH_ENV = "BROTHER_HEAVY_SLOT"
WAIT_ENV = "BROTHER_HEAVY_SLOT_WAIT"
COUNT_ENV = "LOCAL_SLOTS"
WEIGHT_ENV = "BROTHER_HEAVY_SLOT_WEIGHT"
DIR_ENV = "BROTHER_SLOT_DIR"
CEILING_ENV = "BROTHER_LOAD_CEILING"
LOAD_FILE_ENV = "BROTHER_LOAD_READING_FILE"
NICE_ENV = "BROTHER_HEAVY_SLOT_NICE"
DEFAULT_NICE = 5
RECENT_S = 60.0          # the load1 fallback: a one minute average hides a new run for about a minute
RECENT_TICKS_S = 15.0    # the measured signal: a new run shows within seconds, so only its ramp up is uncovered
BUSY_WINDOW = 3          # samples, one per poll: the ceiling is judged on the busiest of the last three seconds
DEFAULT_WAIT = 3600
POLL_SECONDS = 1.0


def say(msg):
    sys.stderr.write("heavy_slot: %s\n" % msg)
    sys.stderr.flush()


def slot_count(env):
    default = max(1, (os.cpu_count() or 2) // 2)
    raw = env.get(COUNT_ENV, "")
    if not raw:
        return default
    try:
        n = int(raw)
    except ValueError:
        n = 0
    if n < 1:
        say("%s=%r is not a positive integer; using %d" % (COUNT_ENV, raw, default))
        return default
    return n


def slot_weight(env, n):
    raw = env.get(WEIGHT_ENV, "")
    if not raw:
        return 1
    try:
        k = int(raw)
    except ValueError:
        k = 0
    if k < 1:
        say("%s=%r is not a positive integer; using 1" % (WEIGHT_ENV, raw))
        return 1
    if k > n:
        say("weight %d is more than the %d slots; taking all %d" % (k, n, n))
        return n
    return k


def wait_bound(env):
    raw = env.get(WAIT_ENV, "")
    try:
        return max(0.0, float(raw)) if raw else float(DEFAULT_WAIT)
    except ValueError:
        say("%s=%r is not a number; using %d" % (WAIT_ENV, raw, DEFAULT_WAIT))
        return float(DEFAULT_WAIT)


def load_ceiling(env):
    """Every core but resource_gate.CORE_RESERVE, at least 1. BROTHER_LOAD_CEILING may lower it, never raise it."""
    top = float(max(1, (os.cpu_count() or 2) - resource_gate.CORE_RESERVE))
    raw = env.get(CEILING_ENV, "")
    if not raw:
        return top
    try:
        c = float(raw)
    except ValueError:
        c = 0.0
    if not c > 0:
        say("%s=%r is not a positive number; using %g" % (CEILING_ENV, raw, top))
        return top
    if c > top:
        say("%s=%s is above the ceiling %g (every core but %d); using %g"
            % (CEILING_ENV, raw, top, resource_gate.CORE_RESERVE, top))
        return top
    return c


def read_load(env):
    """load1, or None when it cannot be read (the caller waits: unreadable is never idle)."""
    path = env.get(LOAD_FILE_ENV)
    try:
        if path:
            with open(path) as fh:
                return float(fh.read().strip())
        return os.getloadavg()[0]
    except (OSError, ValueError, AttributeError):
        return None


def recent_starts(directory, n, skip=(), now=None, window_s=RECENT_S):
    """Runs admitted in the last window_s seconds, read from the start time in each slot's label."""
    now = time.time() if now is None else now
    count = 0
    for i in range(n):
        if i in skip:
            continue
        try:
            with open(os.path.join(directory, "slot-%d" % i)) as fh:
                label = fh.readline()
        except OSError:
            continue  # sbe: allow-silent a slot never used has no label and so no start to count
        m = re.match(r"pid \d+ start (\d+(?:\.\d+)?):", label)
        if m and now - float(m.group(1)) < window_s:
            count += 1
    return count


def busy_between(prev, cur, cores):
    """Cores busy between two resource_gate.cpu_ticks() readings, or None when they cannot be compared."""
    if not prev or not cur or cur[1] <= prev[1]:
        return None
    return (cur[0] - prev[0]) / float(cur[1] - prev[1]) * cores


def machine_load(env, state):
    """(cores in use, what it was read from, how long a new run stays uncovered). The seam file wins; then the
    cores measured busy from kernel ticks, the busiest of the last BUSY_WINDOW polls; then load1. state carries the
    previous tick reading and the window between polls."""
    if env.get(LOAD_FILE_ENV):
        return read_load(env), "load1", RECENT_S
    cur = resource_gate.cpu_ticks()
    busy = busy_between(state.get("ticks"), cur, os.cpu_count() or 1)
    state["ticks"] = cur
    if busy is not None:
        window = state.setdefault("window", [])
        window.append(busy)
        del window[:-BUSY_WINDOW]
        return max(window), "cores busy", RECENT_TICKS_S
    return read_load(env), "load1", RECENT_S


def nice_level(env):
    raw = env.get(NICE_ENV, "")
    if not raw:
        return DEFAULT_NICE
    try:
        v = int(raw)
    except ValueError:
        v = -1
    if not 0 <= v <= 19:
        say("%s=%r is not 0 to 19; using %d" % (NICE_ENV, raw, DEFAULT_NICE))
        return DEFAULT_NICE
    return v


def slot_dir(env):
    return env.get(DIR_ENV) or brother_paths.config_path("evidence", "local-slots", env=env)


def try_acquire(directory, n, k=1, label=True):
    """One non-blocking pass over the slots. Returns [(index, open file), ...]
    holding exactly k slots, or [] having released any it took. Raises OSError
    when the directory itself is unusable."""
    os.makedirs(directory, exist_ok=True)
    held = []
    for i in range(n):
        if len(held) == k:
            break
        handle = open(os.path.join(directory, "slot-%d" % i), "a+")
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            continue
        held.append((i, handle))
    if len(held) < k:
        for _, handle in held:
            handle.close()
        return []
    if label:
        write_labels(held)
    return held


def write_labels(held):
    for _, handle in held:
        try:
            handle.seek(0)
            handle.truncate()
            handle.write("pid %d start %d: %s\n" % (os.getpid(), time.time(), " ".join(sys.argv[1:])[:200]))
            handle.flush()
        except OSError:
            pass  # sbe: allow-silent the holder label is informational; the lock is what counts


def holders(directory, n):
    names = []
    for i in range(n):
        try:
            with open(os.path.join(directory, "slot-%d" % i)) as fh:
                label = fh.readline().strip()
        except OSError:
            label = ""
        names.append(label or "slot-%d (holder unlabelled)" % i)
    return names


def record_wait(directory, waited_s, outcome, k, n):
    """One line per admission in <slot dir>/waits.jsonl, so ACC7's flip condition (median wait over
    10 minutes buys isolated runners) is measured rather than guessed. A line that cannot be written
    is said and skipped: the record never delays or blocks the run."""
    line = json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "waited_s": round(waited_s, 1),
                       "outcome": outcome, "slots": k, "of": n}, sort_keys=True)
    try:
        with open(os.path.join(directory, "waits.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError as exc:
        say("wait record not written (%s); the run goes ahead" % exc)


def acquire(env):
    """Returns the held [(index, handle), ...] once enough slots are free, or []
    when the command must run unqueued (bound reached or directory unusable)."""
    n = slot_count(env)
    k = slot_weight(env, n)
    directory = slot_dir(env)
    bound = wait_bound(env)
    ceiling = load_ceiling(env)
    state = {}
    if not env.get(LOAD_FILE_ENV):
        state["ticks"] = resource_gate.cpu_ticks()   # the first measured reading needs a second one to compare with
        time.sleep(POLL_SECONDS)
    start = time.monotonic()
    announced = None
    while True:
        try:
            held = try_acquire(directory, n, k, label=False)
        except OSError as exc:
            say("slot directory %s unusable (%s); running UNQUEUED" % (directory, exc))
            return []
        if held:
            load1, basis, window_s = machine_load(env, state)
            recent = recent_starts(directory, n, skip={i for i, _ in held}, window_s=window_s)
            if load1 is not None and load1 + recent + k <= ceiling:
                write_labels(held)
                record_wait(directory, time.monotonic() - start, "admitted", k, n)
                if announced:
                    say("%d of %d slots taken after %.0f s" % (k, n, time.monotonic() - start))
                return held
            for _, handle in held:
                handle.close()
            why = ("load unreadable, so the machine is not assumed idle" if load1 is None else
                   "%s %.2f + %d run(s) admitted in the last %.0f s + %d would pass the ceiling %g"
                   % (basis, load1, recent, window_s, k, ceiling))
            if announced != "load":
                say("waiting under the load ceiling (up to %.0f s): %s" % (bound, why))
                announced = "load"
        elif announced != "slots":
            say("waiting for %d of %d slots (up to %.0f s). Holders: %s"
                % (k, n, bound, "; ".join(holders(directory, n))))
            announced = "slots"
        if time.monotonic() - start >= bound:
            say("no admission after %.0f s; running UNQUEUED (a busy machine is not a verdict)" % bound)
            record_wait(directory, time.monotonic() - start, "unqueued", k, n)
            return []
        time.sleep(POLL_SECONDS)


def run_child(cmd, env, nice=0):
    def lower_priority():
        if nice:
            os.nice(nice)
    try:
        child = subprocess.Popen(cmd, env=env, preexec_fn=lower_priority if nice else None)
    except OSError as exc:
        say("cannot start %r: %s" % (cmd[0], exc))
        return 127

    def forward(signum, _frame):
        try:
            child.send_signal(signum)
        except OSError:
            pass  # sbe: allow-silent the child already exited; wait() below collects it

    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, forward)
    code = child.wait()
    return 128 - code if code < 0 else code


def main(argv, env=None):
    env = dict(os.environ if env is None else env)
    cmd = argv[1:]
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd:
        say("usage: heavy_slot.py <command> [args...]")
        return 2
    if env.get(HELD_ENV) or env.get(SWITCH_ENV, "").lower() == "off":
        return run_child(cmd, env)
    held = acquire(env)
    env[HELD_ENV] = ",".join(str(i) for i, _ in held) or "unqueued"
    try:
        return run_child(cmd, env, nice=nice_level(env))
    finally:
        for _, handle in held:
            try:
                handle.seek(0)
                handle.truncate()  # a free slot names nobody: "Holders:" never lists the dead
            except OSError:
                pass  # sbe: allow-silent the label is informational; closing releases the lock
            handle.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
