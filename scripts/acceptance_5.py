#!/usr/bin/env python3
"""Acceptance test for capability area 5: terminal cancellation and hung
command recovery (G1-M3.8 of docs/plan/READINESS-ROADMAP-2026-08-29.json,
node G1-M3, following the template G1-M3.3 left behind).

Area 5's own definition (docs/plan/CAPABILITY-AREAS.json): a contributor
runs a long or hanging shell command through the tool, then cancels it and
confirms the terminal and the session both return to a usable state. It
fails when cancellation leaves the terminal wedged, the session hangs
waiting on the dead process, or the next command inherits stale state.

THE REAL MACHINERY UNDER TEST is bm_worker_spawn.SpawningWorker, the exact
adapter scripts/loop_bridge.py uses to run every worker command (its
LaneWorker wraps SpawningWorker directly, and loop_bridge.load_parts()
loads it from the same sibling tools checkout every other acceptance
script in this suite depends on for its worker plumbing). SpawningWorker's
own module docstring names the failure this area is about: "an adapter
with NO timeout is the thing that turns one hung worker into a session
that never ends." Its run() wraps subprocess.run(..., timeout=...): on
TimeoutExpired, subprocess.run kills the child and waits on it BEFORE
raising, and SpawningWorker reads that as a clean "unavailable" status
rather than propagating the exception into the caller.

loop_bridge.py's own CLI does not expose that timeout (its LaneWorker
always builds SpawningWorker with the class's 900-second default), so this
test builds SpawningWorker directly, through the SAME dynamic module
resolution loop_bridge.load_parts() uses (the sibling tools checkout, the
BROTHER_TOOLS-style override, NO-DATA if it is absent), rather than a
hardcoded import: this is the exact module loop_bridge would load, never a
path invented for this area alone.

REAL PROCESS, NOT A MOCK: the hung command is a real shell script that
execs into a real `sleep`, so cancelling it is a real kill of a real PID,
checked with a real os.kill(pid, 0) probe, not a return-value assumption.

Exit contract, matching the estate's other acceptance scripts:
  0  PASS      the hung process was actually terminated (confirmed by PID
               probe, not merely by subprocess.run returning), and a
               second, well-behaved worker run right after completed
               normally and quickly through the same mechanism
  1  FAIL      the hung process was still alive after cancellation, or the
               session did not recover to run the next command
  2  NO-DATA   the sibling tools checkout (bm_worker_spawn) is not present
               in this environment

Usage: python3 scripts/acceptance_5.py [--explain] [--calibrate]
--calibrate forces this test red using a real, well-known limitation of
subprocess timeouts rather than a mock: the worker backgrounds a second,
fully-detached process (a subshell that spawns it and exits immediately,
orphaning it) before hanging in the foreground itself. Cancellation kills
the foreground process subprocess.run is actually watching, but the
detached grandchild survives, exactly the mechanical shape of "cancellation
leaves the terminal wedged" / "the next command inherits stale state".
Passes only if this test correctly reads that surviving process as FAIL,
and always cleans it up afterwards regardless of the verdict.

origin: invoked directly as its own CLI (main(), below, `python3
scripts/acceptance_5.py [--explain] [--calibrate]`, this file's own line
46), by a human or a CI runner checking capability area 5. It is also
reached through scripts/acceptance.py's run_area() (subprocess.run() of
this script path on `python3 scripts/acceptance.py --area 5`, run_area
around lines 55-61), and through scripts/product_acceptance.py's own
calibration delegation: CALIBRATE_DELEGATES maps area "8" to
"acceptance_5.py" and _calibrate_via_mechanism_twin() subprocess.run()s it
with --calibrate (product_acceptance.py, lines 1093-1094 and 1097-1108),
because area 8 has no product-path way to disable the safety net area 5
proves (product_acceptance.py's own comment, lines 1088-1092).
scripts/test_acceptance.py also drives it, as a test harness.

PRODUCER: this module is the sole producer of the files it writes. The
_write() helper (lines 113-116) is used by build_hanging_worker() (line
137), build_escaping_worker() (line 151) and build_wellbehaved_worker()
(line 166) to write the scripted shell worker files and their PID files.
All of these live inside the tempfile.TemporaryDirectory opened at line
184 and are deleted when that with-block exits; nothing else in this repo
writes through this module's helper.
"""
import argparse
import os
import signal
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))

#: Short enough that the honest run and the calibration both finish in a
#: couple of seconds, long enough that spawning a shell and execing into
#: sleep reliably completes first even on a loaded machine.
SPAWN_TIMEOUT_SECONDS = 1.0
TIME_BUDGET_SECONDS = 30.0

TEMPLATE = """area 5 template addition to G1-M3.3's shape:
  - the real machinery is one layer below loop_bridge.py's own CLI, because
    the CLI does not expose the knob (the spawn timeout) this area needs to
    vary; reaching one layer down for the real, adjustable mechanism is
    preferred over inventing a CLI flag that would not exist for a
    contributor either
  - "the process was actually cancelled" is checked by PID, not by trusting
    a clean-looking return value: subprocess.run's own timeout handling
    already waits on the child it kills, so the honest run's assertion is
    almost free, and the calibration exists to prove the assertion is not
    vacuous
  - the forced bad state is a REAL OS behaviour (a detached grandchild
    process escaping the killed process's lineage), not a fake return value,
    matching area 3's rule that a fallback mechanism under test must be one
    every other script here already trusts
  - any process this test deliberately leaks for the calibration is killed
    in a finally block regardless of verdict, because a test that proves a
    hang can escape must not itself leave one running on the machine that
    ran it
What areas 1 through 4's shape got wrong that this corrects: nothing did.
What this area adds for the next ones: when the CLI a contributor would
actually type does not expose the mechanism an area needs, test the real
mechanism one layer down and say so, rather than reporting NO-DATA for a
capability that demonstrably exists just below the surface."""


def _load_spawn_module():
    """The same dynamic resolution loop_bridge.py uses, so this test
    exercises exactly the module loop_bridge would load. Returns
    (module, "") or (None, NO-DATA reason)."""
    sys.path.insert(0, HERE)
    try:
        import loop_bridge
    except ImportError as exc:
        return None, "could not import scripts/loop_bridge.py: %s" % exc
    parts, problem = loop_bridge.load_parts()
    if parts is None:
        return None, problem
    return parts["spawn"], ""


def _write(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.chmod(path, 0o755)


def _pid_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _kill_if_alive(pid):
    if pid and _pid_alive(pid):
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:  # sbe: allow-silent best-effort teardown of a test child that may have exited between the aliveness check and the kill; nothing is recorded from it
            pass


def build_hanging_worker(tmp):
    """A real process that hangs: writes its own PID (unchanged across
    exec) then execs into `sleep`, so the process subprocess.run kills is
    the exact process the PID probe checks."""
    worker = os.path.join(tmp, "hang.sh")
    pidfile = os.path.join(tmp, "hang.pid")
    _write(worker,
          "#!/bin/sh\n"
          "cat >/dev/null\n"
          "echo $$ > %s\n"
          "exec sleep 9999\n" % pidfile)
    return worker, pidfile


def build_escaping_worker(tmp):
    """--calibrate only: a real detached grandchild that survives the
    foreground process being killed. The subshell backgrounds `sleep` and
    exits immediately, orphaning it (reparented away from this script's
    process group), before the foreground itself also hangs."""
    worker = os.path.join(tmp, "escape.sh")
    pidfile = os.path.join(tmp, "escape.pid")
    _write(worker,
          "#!/bin/sh\n"
          "cat >/dev/null\n"
          "(sleep 9999 & echo $! > %s)\n"
          "exec sleep 9999\n" % pidfile)
    return worker, pidfile


def build_wellbehaved_worker(tmp):
    """A fast, well-behaved worker producing a readable answer, so the
    'session recovers' half of this area is checked against a real second
    process, not merely the absence of a crash."""
    worker = os.path.join(tmp, "ok.sh")
    _write(worker,
          "#!/bin/sh\n"
          "cat >/dev/null\n"
          "printf '{\"worker_claim\": \"ok\", \"artifacts\": []}'\n")
    return worker


def _run(explain, escape):
    spawn, problem = _load_spawn_module()
    if spawn is None:
        return 2, "NO-DATA: %s" % problem

    start = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="acceptance-5-") as tmp:
        if escape:
            hung_script, pidfile = build_escaping_worker(tmp)
        else:
            hung_script, pidfile = build_hanging_worker(tmp)

        leaked_pid = None
        try:
            hung = spawn.SpawningWorker([hung_script], cwd=tmp,
                                        timeout=SPAWN_TIMEOUT_SECONDS)
            result = hung.run({})
            elapsed_cancel = time.monotonic() - start

            if explain:
                print(TEMPLATE)

            if result.get("status") != "unavailable":
                return 1, ("FAIL: the hung command was expected to be read as "
                           "unavailable after %.1fs; got status=%r instead"
                           % (SPAWN_TIMEOUT_SECONDS, result.get("status")))

            pid = None
            if os.path.isfile(pidfile):
                with open(pidfile, encoding="utf-8") as fh:
                    pid = int(fh.read().strip() or 0) or None
            leaked_pid = pid

            if pid and _pid_alive(pid):
                return 1, ("FAIL: cancellation was reported (status=%s) but "
                           "PID %d is still alive %.2fs later: the process was "
                           "not actually terminated, which is the wedged-"
                           "terminal failure this area is about"
                           % (result.get("status"), pid, elapsed_cancel))
            leaked_pid = None  # confirmed dead, nothing to clean up

            # "The session returns to a usable state": a second, well-behaved
            # worker runs right after, through the same mechanism, and must
            # complete normally and quickly, not inherit any stale state from
            # the cancelled one.
            ok_script = build_wellbehaved_worker(tmp)
            second_start = time.monotonic()
            ok = spawn.SpawningWorker([ok_script], cwd=tmp,
                                      timeout=SPAWN_TIMEOUT_SECONDS)
            second = ok.run({})
            second_elapsed = time.monotonic() - second_start
            total_elapsed = time.monotonic() - start

            if second.get("status") != "returned":
                return 1, ("FAIL: cancellation cleaned up correctly, but the "
                           "next command through the same mechanism read "
                           "status=%r instead of a normal answer, which is "
                           "exactly 'the next command inherits stale state'"
                           % second.get("status"))
            if second_elapsed > SPAWN_TIMEOUT_SECONDS:
                return 1, ("FAIL: the next command took %.2fs, at or over the "
                           "cancelled command's own %.1fs timeout, suggesting "
                           "it was still waiting on the dead process"
                           % (second_elapsed, SPAWN_TIMEOUT_SECONDS))
            if total_elapsed > TIME_BUDGET_SECONDS:
                return 1, ("FAIL: cancel-then-recover took %.2fs, over the "
                           "%.0fs budget" % (total_elapsed, TIME_BUDGET_SECONDS))
            return 0, ("PASS: a hung process (pid %s) was actually terminated "
                       "%.2fs after a %.1fs timeout, and a second worker "
                       "through the same mechanism returned normally in "
                       "%.2fs: the session recovered to a usable state"
                       % (pid, elapsed_cancel, SPAWN_TIMEOUT_SECONDS, second_elapsed))
        finally:
            _kill_if_alive(leaked_pid)


def run(explain=False):
    return _run(explain, escape=False)


def calibrate():
    """G1-M3.8.2: force this test red once. A detached grandchild process
    escapes the killed foreground process (a real OS behaviour, not a
    mock), and this test passes its own calibration only if it correctly
    reads the surviving process as a failure. The escaped process is always
    killed afterwards, whatever this returns."""
    code, evidence = _run(explain=False, escape=True)
    if code == 1 and "still alive" in evidence:
        return 0, ("PASS: calibration used a real detached grandchild that "
                   "escapes the killed foreground process, and this test "
                   "correctly read the survivor as failed (%s): a green "
                   "reading of this test means something" % evidence)
    if code == 2:
        return 1, ("FAIL: calibration could not run at all (%s), so nothing "
                   "was proven about this test's ability to fail" % evidence)
    return 1, ("FAIL: calibration could not force this test red (got %s): a "
               "green reading of this test would be decoration" % evidence)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Acceptance test for capability area 5: terminal "
                    "cancellation and hung command recovery.")
    parser.add_argument("--explain", action="store_true",
                        help="also print the template this area leaves behind")
    parser.add_argument("--calibrate", action="store_true",
                        help="prove this test can fail, instead of running it")
    args = parser.parse_args(argv)
    if args.calibrate:
        code, evidence = calibrate()
    else:
        code, evidence = run(explain=args.explain)
    print(evidence)
    return code


if __name__ == "__main__":
    sys.exit(main())
