"""fault_barrier: one blocking synchronization point, shared.

REPAIR C1 (2026-09-09 adversarial review of lane/continuity): claim_store.py,
integrate.py and loop_bridge.py each carried a byte-for-byte copy of the same
function, gated only by an environment variable a production run could carry
by accident. Duplicated blocking code inside three shipped runtime modules is
exactly the kind of thing that turns into a stuck production process the day
someone's environment leaks BROTHER_FAULT_BARRIER into a real run. This module
is the one function the three now import instead.

WHY IT CAN NEVER ACT IN PRODUCTION, three independent gates, all required:

  1. BROTHER_FAULT_BARRIER must name this exact boundary. Never a user knob.

  2. BROTHER_FAULT_LAB=1 must be set. This is a dedicated marker only
     scripts/fault_lab.py itself ever sets, in the one place it builds a
     scenario's environment. M-6, FINDING (2026-09-10): gate 2 used to
     check the stub worker seam instead (MODEL_WORKER_CMD or
     DOOR_MODEL_CMD set), on the theory that only a test run sets it, but
     brother_run.py's own session_units_are_yours documents MODEL_WORKER_CMD
     as a production capability (a coding session naming its own worker),
     so a real run CAN carry it and that gate would then guard nothing.
     BROTHER_FAULT_LAB has no such legitimate production use.

  3. STARTED and RELEASE must resolve under the system temp directory
     (tempfile.gettempdir(), compared by realpath so a symlinked temp root
     still matches). A path pointing anywhere else is refused WITHOUT
     WRITING: this function never touches a file outside temp, whatever the
     two env vars name.

BOUNDED, NEVER HANGS FOREVER: the poll waits at most TIMEOUT_SECONDS for
RELEASE to appear, then raises TimeoutError rather than either hanging
indefinitely or silently falling through and letting the caller proceed as
though release had actually happened (the previous shape: a deadline that
just stopped polling and returned, no different from having reached RELEASE).

Mirrors the barrier-file idiom scripts/fault_lab.py's own stub workers
already use: touch a STARTED marker, then poll a RELEASE marker rather than
sleep a fixed duration, so a caller controls exactly when this process may
proceed past this point.

Python 3, standard library only.
"""
import os
import sys
import tempfile
import time

#: Small and fixed: this is a test synchronization primitive, not a
#: production timeout knob, so it is never read from the environment.
TIMEOUT_SECONDS = 60.0

#: M-6: the dedicated marker gate 2 checks, set ONLY by scripts/fault_lab.py
#: (in run_env(), the one place it builds a scenario's environment), never
#: by any production code path. Replaces the old stub-worker-seam check
#: (MODEL_WORKER_CMD / DOOR_MODEL_CMD): those are a documented PRODUCTION
#: capability (brother_run.py's session_units_are_yours), so their presence
#: proved nothing about whether this was a real run.
FAULT_LAB_ENV_VAR = "BROTHER_FAULT_LAB"


def _fault_lab_active(env):
    return (env.get(FAULT_LAB_ENV_VAR) or "").strip() == "1"


def _under_system_temp(path):
    """True when realpath(path) is, or is inside, realpath(gettempdir())."""
    temp_root = os.path.realpath(tempfile.gettempdir())
    real = os.path.realpath(path)
    return real == temp_root or real.startswith(temp_root + os.sep)


def wait(name, env=None):
    """Block until BROTHER_FAULT_BARRIER_RELEASE appears, or raise.

    A no-op (returns immediately) unless ALL of: `name` matches
    BROTHER_FAULT_BARRIER exactly, BROTHER_FAULT_LAB=1 is set, and both
    STARTED and RELEASE are given and resolve under the system temp
    directory. `env` defaults to the real process environment; a test may
    pass a dict instead."""
    env = os.environ if env is None else env
    if env.get("BROTHER_FAULT_BARRIER") != name:
        return
    if not _fault_lab_active(env):
        return
    started = env.get("BROTHER_FAULT_BARRIER_STARTED")
    release = env.get("BROTHER_FAULT_BARRIER_RELEASE")
    if not started or not release:
        return
    if not _under_system_temp(started) or not _under_system_temp(release):
        print("fault_barrier: refusing barrier %r, STARTED/RELEASE must "
              "resolve under the system temp directory (%s); nothing was "
              "written" % (name, tempfile.gettempdir()), file=sys.stderr)
        return
    # FINDING 9 (2026-09-10 security review): the containment check above
    # only proves the REALPATH resolved under the system temp directory at
    # the moment it was checked; a plain open(started, "w") still follows
    # a symlink placed at `started` between that check and this write, and
    # under a world-writable temp directory anything can plant one. Opening
    # with O_NOFOLLOW refuses to follow a symlink at the marker path itself
    # rather than trusting the earlier realpath check to still hold.
    try:
        fd = os.open(started, os.O_CREAT | os.O_WRONLY | os.O_TRUNC |
                     os.O_NOFOLLOW, 0o600)
    except OSError as exc:
        print("fault_barrier: refusing barrier %r, could not open %s "
              "safely: %s" % (name, started, exc), file=sys.stderr)
        return
    try:
        os.write(fd, str(os.getpid()).encode("utf-8"))
    finally:
        os.close(fd)
    deadline = time.time() + TIMEOUT_SECONDS
    while time.time() < deadline:
        if os.path.exists(release):
            return
        time.sleep(0.02)
    raise TimeoutError(
        "fault_barrier: boundary %r was never released within %.0fs" %
        (name, TIMEOUT_SECONDS))
