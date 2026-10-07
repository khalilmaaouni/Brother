#!/usr/bin/env python3
"""Run one stage command with a wall clock and a process group, so a wedged stage can never hold a runner all night.
usage: imported by unit_runner.py;   python3 scripts/loop/bounded.py --selftest

Measured 2026-09-22 (end to end review before the unsupervised run): unit_runner ran the grader and the probe wave
through subprocess.run with NO timeout, and grade_build waited for a machine slot with no bound, so one wedged
sandbox kept its unit out of the pool and kept loop_done from ever finishing. Every stage now has a ceiling, the
child runs in its own session, and the whole group is killed at the ceiling (a child-only kill leaves grandchildren,
which took this machine to load 157 the same night). The timeout is reported as a normal stage result, exit 124,
so the runner's own round logic treats it as a rejected round and moves on."""
import os, signal, subprocess, sys, time


def run_bounded(argv, stdout_path, timeout, env=None):
    """Exit code of argv, 124 on timeout (group killed), 125 when it could not start. stdout+stderr go to the file."""
    try:
        out = open(stdout_path, "a")
    except OSError:
        return 125                                   # a log that cannot be opened is reported, never raised (audit 2026-09-22)
    try:
        with out:
            proc = subprocess.Popen(argv, stdout=out, stderr=subprocess.STDOUT, start_new_session=True, env=env)
    except OSError as exc:
        try:
            with open(stdout_path, "a") as out2: out2.write("FAIL stage could not start: %s\n" % exc)
        except OSError: pass
        return 125
    try:
        return proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        try: os.killpg(proc.pid, signal.SIGKILL)
        except OSError: pass
        proc.wait()
        with open(stdout_path, "a") as out: out.write("FAIL stage timed out after %ds, process group killed\n" % timeout)
        return 124


def knob(name, default):
    v = os.environ.get(name, "")
    return int(v) if v.isdigit() and int(v) > 0 else default


def selftest():
    try: return _selftest_body()
    except Exception as exc:
        print("selftest: FAILED before it could finish, %s: %s" % (type(exc).__name__, str(exc)[:120])); return 1


def _selftest_body():
    import tempfile
    d = tempfile.mkdtemp(prefix="bounded-"); log = os.path.join(d, "log"); sp = os.path.join(d, "sp.py")
    with open(sp, "w") as f:
        f.write("import subprocess,sys,time\nsubprocess.Popen([sys.executable,'-c','import time\\nwhile True: time.sleep(0.05)'])\nprint('started', flush=True)\ntime.sleep(60)\n")
    t0 = time.monotonic(); rc = run_bounded([sys.executable, sp], log, 1); took = time.monotonic() - t0; time.sleep(0.3)
    left = subprocess.run(["pgrep", "-f", "while True: time.sleep\\(0.05\\)"], capture_output=True, text=True).stdout.split()
    with open(log) as f: text = f.read()
    ok = run_bounded([sys.executable, "-c", "print('fine')"], log, 5)
    cases = [("timeout returns 124", rc == 124), ("timeout honoured within seconds", took < 5),
             ("the grandchild died with the group", left == []),
             ("the log names the timeout and keeps the child's output", "timed out after 1s" in text and "started" in text),
             ("a normal exit is passed through", ok == 0),
             ("a command that cannot start is 125, never a raise", run_bounded(["/nonexistent/bin/x"], log, 5) == 125),
             ("a log path that cannot be opened is 125, never a raise", run_bounded([sys.executable, "-c", "pass"], os.path.join(d, "nodir", "log"), 5) == 125),
             ("knob: digits only, else default", knob("BOUNDED_SELFTEST_KNOB", 7) == 7)]
    bad = [n for n, good in cases if not good]
    print("selftest: %d cases, %s" % (len(cases), "OK" if not bad else "FAILED: " + ", ".join(bad))); return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(selftest() if "--selftest" in sys.argv else 2)
