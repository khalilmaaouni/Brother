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
  CHILD SLOT COUNT. BROTHER_HEAVY_SLOT_CHILD_SLOTS, when set, is the LOCAL_SLOTS the child sees (empty: none),
  for a caller that queues on fewer slots than the work inside may use (session_relay's gate queues on one).
  Without it a grader inside such a gate tries slot 0 only, which its own parent holds, and waits out the bound.
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
  that itself runs k things side by side (since GATE1, 2026-11-01, the fast
  gate takes weight 1 and runs alone through EXCLUSIVE below). All or nothing: a pass that finds fewer than
  k free takes none, so two weighted waiters can never each hold half of what
  they need and block each other. k above the pool size is capped at it.
  EXCLUSIVE (GATE1, 2026-11-01). A caller with LOCAL_SLOTS=4 saw four slots and took slot 1 while a whole gate
  queued with LOCAL_SLOTS=1 held slot 0. So every admitted run also holds <slot dir>/exclusive.lock SHARED, and a
  run with BROTHER_HEAVY_SLOT_EXCLUSIVE=1 (the whole gate) holds it EXCLUSIVE: nothing else runs beside it,
  whatever slot count its caller used. An exclusive waiter leaves <slot dir>/exclusive-wait/<pid>, and a shared
  admission holds back while any such pid is alive, so a stream of small runs cannot starve the gate.
  HOLDER. An admitted or unqueued run also gets BROTHER_HEAVY_SLOT_HOLDER, the pid of its direct parent (this
  wrapper, or the keeper below), so a nested caller can tell a live holder from a HELD value inherited from one
  that has ended (required_fast.sh refuses the second). BROTHER_HEAVY_SLOT_HELD keeps its own format:
  hermetic_test_check.py reads the slots in it.
  DEADLINE (GATE1, 2026-11-01). BROTHER_HEAVY_SLOT_DEADLINE=s, on a run that holds its slots, is the age past
  which the holder is stale: the child runs in its own session, and at s seconds this wrapper logs the event
  (pid and age, on stderr and as a "stale" line in waits.jsonl), ends the child's whole process group (TERM, then
  KILL after DEADLINE_GRACE_S) and returns 2, NO-DATA, never a pass. The slots free when it returns. Unset, a
  run has no deadline, as before. A caller that waits with no bound (BROTHER_HEAVY_SLOT_WAIT=inf) sets one, so a
  hung holder cannot hold the line for ever. Such a run starts under a KEEPER (keep(), --keep-slot) that holds the
  lock descriptors in the run's session: a kill of this wrapper's group cannot free the locks while the run lives.

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
CHILD_COUNT_ENV = "BROTHER_HEAVY_SLOT_CHILD_SLOTS"
DIR_ENV = "BROTHER_SLOT_DIR"
CEILING_ENV = "BROTHER_LOAD_CEILING"
LOAD_FILE_ENV = "BROTHER_LOAD_READING_FILE"
NICE_ENV = "BROTHER_HEAVY_SLOT_NICE"
HOLDER_ENV = "BROTHER_HEAVY_SLOT_HOLDER"
DEADLINE_ENV = "BROTHER_HEAVY_SLOT_DEADLINE"
DEADLINE_GRACE_S = 10
EXCLUSIVE_ENV = "BROTHER_HEAVY_SLOT_EXCLUSIVE"
KEEP_FLAG = "--keep-slot"   # internal: heavy_slot.py re-entered as the keeper of a run with a deadline
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


def record_wait(directory, waited_s, outcome, k, n, **extra):
    """One line per admission in <slot dir>/waits.jsonl, so ACC7's flip condition (median wait over
    10 minutes buys isolated runners) is measured rather than guessed. A line that cannot be written
    is said and skipped: the record never delays or blocks the run."""
    line = json.dumps(dict({"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "waited_s": round(waited_s, 1),
                            "outcome": outcome, "slots": k, "of": n}, **extra), sort_keys=True)
    try:
        with open(os.path.join(directory, "waits.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError as exc:
        say("wait record not written (%s); the run goes ahead" % exc)


def exclusive_waiters(directory, skip=None):
    """Pids of live exclusive waiters (exclusive-wait/<pid>); a dead one's marker is removed."""
    folder = os.path.join(directory, "exclusive-wait")
    alive = []
    try:
        names = os.listdir(folder)
    except OSError:
        return alive
    for name in names:
        if not name.isdigit() or int(name) == skip:
            continue
        try:
            os.kill(int(name), 0)
            alive.append(int(name))
        except OSError:
            try:
                os.unlink(os.path.join(folder, name))
            except OSError:
                pass  # sbe: allow-silent another waiter removed the same dead marker
    return alive


def take_machine_lock(directory, exclusive):
    """exclusive.lock, SHARED for an ordinary run and EXCLUSIVE for a whole gate, without blocking: the open
    handle, or None when a whole gate holds it (or, for an ordinary run, one is waiting for it)."""
    if not exclusive and exclusive_waiters(directory):
        return None
    handle = open(os.path.join(directory, "exclusive.lock"), "a+")
    try:
        fcntl.flock(handle, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def acquire(env):
    """Returns (held, machine_lock): the held [(index, handle), ...] and the exclusive.lock handle once enough
    slots are free and the machine lock is had, or ([], None) when the command must run unqueued (bound reached
    or directory unusable)."""
    n = slot_count(env)
    k = slot_weight(env, n)
    directory = slot_dir(env)
    bound = wait_bound(env)
    ceiling = load_ceiling(env)
    exclusive = env.get(EXCLUSIVE_ENV, "") == "1"
    state = {}
    if not env.get(LOAD_FILE_ENV):
        state["ticks"] = resource_gate.cpu_ticks()   # the first measured reading needs a second one to compare with
        time.sleep(POLL_SECONDS)
    start = time.monotonic()
    announced = None
    marker = None
    try:
        while True:
            held, lock = wait_pass(directory, n, k, exclusive)
            if held is None:
                return [], None
            if exclusive and marker is None and not held:
                marker = os.path.join(directory, "exclusive-wait", str(os.getpid()))
                os.makedirs(os.path.dirname(marker), exist_ok=True)
                open(marker, "w").close()
            if held:
                load1, basis, window_s = machine_load(env, state)
                recent = recent_starts(directory, n, skip={i for i, _ in held}, window_s=window_s)
                if load1 is not None and load1 + recent + k <= ceiling:
                    write_labels(held)
                    record_wait(directory, time.monotonic() - start, "admitted", k, n)
                    if announced:
                        say("%d of %d slots taken after %.0f s" % (k, n, time.monotonic() - start))
                    return held, lock
                for _, handle in held:
                    handle.close()
                lock.close()
                why = ("load unreadable, so the machine is not assumed idle" if load1 is None else
                       "%s %.2f + %d run(s) admitted in the last %.0f s + %d would pass the ceiling %g"
                       % (basis, load1, recent, window_s, k, ceiling))
                if announced != "load":
                    say("waiting under the load ceiling (up to %.0f s): %s" % (bound, why))
                    announced = "load"
            elif lock is False and announced != "machine":
                say("waiting for the machine (up to %.0f s): %s" % (
                    bound, "a whole gate is waiting for it or holds it" if not exclusive else
                    "runs are still holding it; this whole gate runs alone"))
                announced = "machine"
            elif lock is not False and announced != "slots":
                say("waiting for %d of %d slots (up to %.0f s). Holders: %s"
                    % (k, n, bound, "; ".join(holders(directory, n))))
                announced = "slots"
            if time.monotonic() - start >= bound:
                say("no admission after %.0f s; running UNQUEUED (a busy machine is not a verdict)" % bound)
                record_wait(directory, time.monotonic() - start, "unqueued", k, n)
                return [], None
            time.sleep(POLL_SECONDS)
    finally:
        if marker:
            try:
                os.unlink(marker)
            except OSError:
                pass  # sbe: allow-silent the marker is advisory; a dead pid's marker is pruned by the next reader


def wait_pass(directory, n, k, exclusive):
    """One pass: (held, lock) on success; ([], None) when slots are taken; ([], False) when the slots were free
    but the machine lock was not; (None, None) when the directory is unusable (said)."""
    try:
        held = try_acquire(directory, n, k, label=False)
    except OSError as exc:
        say("slot directory %s unusable (%s); running UNQUEUED" % (directory, exc))
        return None, None
    if not held:
        return [], None
    try:
        lock = take_machine_lock(directory, exclusive)
    except OSError as exc:
        for _, handle in held:
            handle.close()
        say("machine lock in %s unusable (%s); running UNQUEUED" % (directory, exc))
        return None, None
    if lock is None:
        for _, handle in held:
            handle.close()
        return [], False
    return held, lock


def deadline_of(env):
    """BROTHER_HEAVY_SLOT_DEADLINE as positive seconds, or None (unset or unusable, said)."""
    raw = env.get(DEADLINE_ENV, "")
    if not raw:
        return None
    try:
        s = float(raw)
    except ValueError:
        s = 0.0
    if not s > 0:
        say("%s=%r is not a positive number; this run has no deadline" % (DEADLINE_ENV, raw))
        return None
    return s


def keep(argv, env):
    """The keeper (GATE1 reviews, 2026-11-02): `--keep-slot <deadline> <slot dir> -- <command>`. It runs in its own
    session holding the slot and machine lock descriptors it was handed, starts the run in a process group of its
    own (the run gets none of the descriptors, so a leaked grandchild never pins them), forwards signals to that
    group, and ENFORCES THE DEADLINE ITSELF: killing the wrapper's group leaves keeper and run alive, and the keeper
    still ends a hung run at its age, records it stale and returns 2, so the locks never outlive the deadline.
    KNOWN LIMIT: a SIGKILL sent to the keeper alone orphans the run and frees the locks; the run then reads FAIL
    (137) at its caller, never PASS. macOS has no parent death signal, so closing it needs the run to watch its
    parent."""
    try:
        sep = argv.index("--")
        deadline = float(argv[0])
        directory = argv[1] if sep > 1 else ""
        cmd = argv[sep + 1:]
    except (ValueError, IndexError):
        say("keeper: expected <deadline> <slot dir> -- <command>, got %r" % (argv[:4],))
        return 2
    if not cmd or not deadline > 0:
        say("keeper: no command or no positive deadline")
        return 2
    env = dict(env)
    env[HOLDER_ENV] = str(os.getpid())

    def own_group():
        os.setpgid(0, 0)
        for s in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            signal.signal(s, signal.SIG_DFL)
    try:
        child = subprocess.Popen(cmd, env=env, close_fds=True, preexec_fn=own_group)
    except OSError as exc:
        say("cannot start %r: %s" % (cmd[0], exc))
        return 127

    def signal_run(signum):
        try:
            os.killpg(child.pid, signum)
        except OSError:
            pass  # sbe: allow-silent the run's group already ended; wait() below collects it

    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, lambda signum, _frame: signal_run(signum))
    started = time.monotonic()
    try:
        code = child.wait(timeout=deadline)
    except subprocess.TimeoutExpired:
        age = time.monotonic() - started
        say("STALE holder: pid %d has run %.0f s, past the deadline of %.0f s; its run is NO-DATA and its slot "
            "is freed" % (child.pid, age, deadline))
        if directory:
            record_wait(directory, 0.0, "stale", 0, 0, pid=child.pid, age_s=round(age, 1))
        signal_run(signal.SIGTERM)
        try:
            child.wait(timeout=DEADLINE_GRACE_S)
        except subprocess.TimeoutExpired:
            signal_run(signal.SIGKILL)
            child.wait()
        signal_run(signal.SIGKILL)   # a leader that left on TERM can leave members of its group running
        return 2
    return 128 - code if code < 0 else code


def run_child(cmd, env, nice=0, deadline=None, directory=None, keep_fds=()):
    def lower_priority():
        if nice:
            os.nice(nice)
    if deadline is not None:   # the keeper holds the locks and enforces the deadline (keep())
        cmd = [sys.executable, os.path.abspath(__file__), KEEP_FLAG, repr(deadline), directory or "", "--"] + list(cmd)
    try:
        child = subprocess.Popen(cmd, env=env, preexec_fn=lower_priority if nice else None,
                                 start_new_session=deadline is not None,
                                 pass_fds=tuple(keep_fds) if deadline is not None else ())
    except OSError as exc:
        say("cannot start %r: %s" % (cmd[0], exc))
        return 127

    def forward(signum, _frame):
        try:
            child.send_signal(signum)   # the keeper, when there is one, passes it on to the run's group
        except OSError:
            pass  # sbe: allow-silent the child already exited; wait() below collects it

    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, forward)
    code = child.wait()
    return 128 - code if code < 0 else code


def child_env(env):
    """The child's environment: the queue's slot count replaced by CHILD_COUNT_ENV when a caller set it, and the
    queue's own wait bound, deadline and exclusive mode dropped (GATE1: the gate's queue settings are not its
    rows' to inherit)."""
    out = dict(env)
    for key in (WAIT_ENV, DEADLINE_ENV, EXCLUSIVE_ENV):
        out.pop(key, None)
    if CHILD_COUNT_ENV in out:
        count = out.pop(CHILD_COUNT_ENV)
        if count:
            out[COUNT_ENV] = count
        else:
            out.pop(COUNT_ENV, None)
    return out


def main(argv, env=None):
    env = dict(os.environ if env is None else env)
    cmd = argv[1:]
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd:
        say("usage: heavy_slot.py <command> [args...]")
        return 2
    if cmd[0] == KEEP_FLAG:
        return keep(cmd[1:], env)
    if env.get(HELD_ENV) or env.get(SWITCH_ENV, "").lower() == "off":
        return run_child(cmd, child_env(env))
    held, lock = acquire(env)
    env[HELD_ENV] = ",".join(str(i) for i, _ in held) or "unqueued"
    env[HOLDER_ENV] = str(os.getpid())
    try:
        fds = [handle.fileno() for _, handle in held] + ([lock.fileno()] if lock is not None else [])
        return run_child(cmd, child_env(env), nice=nice_level(env),
                         deadline=deadline_of(env) if held else None, directory=slot_dir(env), keep_fds=fds)
    finally:
        if lock is not None:
            lock.close()
        for _, handle in held:
            try:
                handle.seek(0)
                handle.truncate()  # a free slot names nobody: "Holders:" never lists the dead
            except OSError:
                pass  # sbe: allow-silent the label is informational; closing releases the lock
            handle.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
