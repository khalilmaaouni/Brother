#!/usr/bin/env python3
"""E46: the wait is never silent again.

THE COMPLAINT THIS CLOSES, measured rather than imagined (run 5 critic 2,
stumble 4, docs/plan/evad-gauntlet/run-2026-09-03/RUN-3-FABLE.md): three real
runs of 499, 1517 and 2245 seconds each printed ONE line when the wait began
and then nothing at all until integration. Thirty seven minutes of silence
looks exactly like a hung process, and a person watching it cannot tell the
two apart without opening a log they were never meant to need.

So: one line every `interval` seconds naming each live unit, the worker
running it, its elapsed seconds against the bound, and the last phase that
unit reached; plus one line the moment a unit changes phase. Elapsed seconds
are MEASURED from a monotonic clock, never estimated, and the bound is the
per-attempt limit the caller already prints on the intent screen, so this
never invents a duration to comfort anybody.

WHY sys.__stderr__ AND NOT sys.stderr. brother_run.run_loop wraps its one
blocking call into loop_bridge inside contextlib.redirect_stdout and
redirect_stderr, so for the whole of exactly the wait this exists to narrate,
sys.stdout and sys.stderr are StringIO buffers read back after the round ends.
A heartbeat written to sys.stderr would be swallowed by the very capture it
was added to escape. sys.__stderr__ is the interpreter's own original stream
and no redirect touches it. A caller (or a test) that wants the lines
elsewhere passes `stream`.

OFF IS A REAL SETTING: interval 0 (or negative) disables BOTH lines, which is
what brother_run's --quiet gives a person who wants the old silence back.
"""
import os
import sys
import threading
import time

#: One line a minute while a worker runs. Long enough that a normal run is not
#: noisy, short enough that a person deciding whether to kill a process never
#: waits long for the next word.
DEFAULT_INTERVAL_SECONDS = 60.0

#: Overrides the default without a flag, for a caller (a hook, a wrapper
#: script) that cannot add one.
INTERVAL_ENV_VAR = "BROTHER_HEARTBEAT_SECONDS"

#: The one live heartbeat, set by start() and cleared by stop(). A module
#: global rather than a parameter because the code that knows the phases
#: (loop_bridge.run_node) sits three call layers below the code that knows the
#: run (brother_run.run_loop), and threading a heartbeat through main(), run()
#: and run_node() would change three signatures and every stub that stands in
#: for them. current() below is what those layers call.
_ACTIVE = None

#: What current() answers when no run is narrating: a heartbeat whose interval
#: is 0, so every call on it is a no-op that prints nothing and starts no
#: thread. One class, no null-object twin to keep in sync.
_SILENT = None


def interval_from_env(env=None, default=DEFAULT_INTERVAL_SECONDS):
    """The heartbeat interval in seconds, read from INTERVAL_ENV_VAR.

    A value that is not a number is REFUSED IN WORDS on stderr and the default
    is used: a mistyped environment variable must not take a delivery down,
    and must not silently disable the narration either."""
    raw = (env if env is not None else os.environ).get(INTERVAL_ENV_VAR)
    if raw is None or not str(raw).strip():
        return float(default)
    try:
        return float(str(raw).strip())
    except ValueError:
        print("run_heartbeat: %s=%r is not a number of seconds, so the "
              "default of %.0fs is used" % (INTERVAL_ENV_VAR, raw, default),
              file=sys.stderr)
        return float(default)


class Heartbeat(object):
    """One line per interval while units are live, one line per phase change.

    Mirrors claim_store.BackgroundRenewal deliberately: same daemon thread on
    an Event timer, same start()/stop() pair around one blocking wait, so the
    two things guarding that wait are read the same way.

    Every write is guarded: a closed or unwritable stream disables the
    heartbeat for the rest of the run rather than raising inside a thread
    nobody is watching, because narration failing must never end a delivery.
    """

    def __init__(self, interval=None, stream=None, bound_seconds=None,
                 clock=time.monotonic):
        self.interval = (DEFAULT_INTERVAL_SECONDS if interval is None
                         else float(interval))
        self.bound_seconds = bound_seconds
        self._stream = stream
        self._clock = clock
        self._units = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._writable = True

    # -- the surface the engine calls --------------------------------------

    def phase(self, unit_id, phase, worker=None):
        """Record that `unit_id` reached `phase` and say so once."""
        if self.interval <= 0:
            return
        now = self._clock()
        with self._lock:
            state = self._units.get(unit_id)
            if state is None:
                state = {"worker": worker or "unknown", "started": now}
                self._units[unit_id] = state
            elif worker:
                state["worker"] = worker
            state["phase"] = phase
            line = self._line("brother_run: now", unit_id, state, now)
        self._write(line)

    def done(self, unit_id, phase="finished"):
        """`unit_id` left the live set: say so once and stop counting it."""
        if self.interval <= 0:
            return
        now = self._clock()
        with self._lock:
            state = self._units.pop(unit_id, None)
            if state is None:
                return
            state["phase"] = phase
            line = self._line("brother_run: now", unit_id, state, now)
        self._write(line)

    # -- the timer ---------------------------------------------------------

    def start(self):
        """Begin narrating. Off (interval 0) starts no thread at all."""
        global _ACTIVE
        _ACTIVE = self
        if self.interval > 0 and self._thread is None:
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()
        return self

    def _loop(self):
        while not self._stop.wait(self.interval):
            for line in self.tick():
                self._write(line)

    def tick(self):
        """The lines one beat would print, without printing them. Public so a
        test can drive the message rather than the timer."""
        if self.interval <= 0:
            return []
        now = self._clock()
        with self._lock:
            if not self._units:
                return ["brother_run: still working: no piece of work has "
                        "started yet"]
            return [self._line("brother_run: still working:", uid, st, now)
                    for uid, st in sorted(self._units.items())]

    def stop(self, timeout=5.0):
        """Stop narrating. Safe to call when start() never ran."""
        global _ACTIVE
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None
        if _ACTIVE is self:
            _ACTIVE = None
        return self

    # -- writing -----------------------------------------------------------

    def _line(self, prefix, unit_id, state, now):
        elapsed = int(round(now - state.get("started", now)))
        bound = ""
        if (isinstance(self.bound_seconds, (int, float))
                and self.bound_seconds > 0):
            bound = " of at most %ds" % int(self.bound_seconds)
        return ("%s %s, worker %s, %ds%s, %s"
                % (prefix, unit_id, state.get("worker") or "unknown", elapsed,
                   bound, state.get("phase") or "no phase recorded"))

    def _write(self, text):
        if not self._writable:
            return
        stream = self._stream or sys.__stderr__ or sys.stderr
        if stream is None:
            self._writable = False
            return
        try:
            stream.write(text + "\n")
            stream.flush()
        except (OSError, ValueError):
            # Said nowhere, because the only stream this had is the one that
            # just failed. Disabled for the rest of the run rather than
            # raising once per beat inside a daemon thread nobody reads.
            self._writable = False


def current():
    """The live heartbeat, or a silent one. Never None, so a caller in the
    middle of the engine never has to branch on whether a run is narrating."""
    global _SILENT
    if _ACTIVE is not None:
        return _ACTIVE
    if _SILENT is None:
        _SILENT = Heartbeat(interval=0)
    return _SILENT


def worker_name(worker):
    """A short, printable name for whatever object the dispatcher is using as
    a worker. Never raises and never empty: a heartbeat that cannot name the
    worker still names the unit and the phase, which is the part a person
    waiting actually needs."""
    for attr in ("name", "worker_name"):
        value = getattr(worker, attr, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    argv = getattr(worker, "_argv", None)
    if isinstance(argv, (list, tuple)) and argv:
        return os.path.basename(str(argv[-1])) or type(worker).__name__
    return type(worker).__name__
