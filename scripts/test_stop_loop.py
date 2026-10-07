#!/usr/bin/env python3
"""scripts/loop/stop_loop.sh stops EVERYTHING the loop started, including children that run in their own session.

Measured 2026-09-22 08:06: the driver and all runners were stopped and 15 or_fanout and or_ask processes lived on,
because unit_runner starts its fan out with start_new_session and the kill site named unit_runner alone. This test
builds that exact family from fake scripts in a temp dir and drives the real stop_loop.sh at it. STOP_LOOP_ONLY scopes
every kill to the temp dir, so a test run can never touch a real loop on this machine.
The temp dir is also the stop's HOME: a loop tool is the loop's only when it belongs to a run of this loop (its program
in $HOME/.claude/bin, or its environment naming a run under $HOME/.claude/evidence/loop-runs, the rule
scripts/test_stop_run_identity.py covers), so the family's tools live in <d>/.claude/bin, the pass's code root helpers
carry BROTHER_RUN_DIR under that runs root, and the stop request the script writes lands in the fixture, never in the
real ~/.claude/evidence beside a live driver.
Run: python3 scripts/test_stop_loop.py
"""
import os, shutil, signal, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
STOP = os.path.join(HERE, "loop", "stop_loop.sh")
RUN_VARS = ("BROTHER_RUN_DIR", "BROTHER_RUNS_ROOT")   # the driver exports both to all it starts; a fixture sets its own
# ONE ROOT PER RUN (resource law; 2026-09-30 06:32, 45 stop-loop-driver-* folders were left in the system temp folder):
# every fixture folder is made under it and main() removes it, so a run leaves nothing behind.
RUN_ROOT = None
SLEEPER = "import time\ntime.sleep(120)\n"
RUNNER = ("import subprocess, sys, time, os\n"
          "d = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))\n"
          "p = subprocess.Popen([sys.executable, os.path.join(d, 'core.or_fanout.py')], start_new_session=True)\n"
          "open(os.path.join(d, 'fanout.pid'), 'w').write(str(p.pid))\ntime.sleep(120)\n")
FANOUT = ("import subprocess, sys, time, os\n"
          "d = os.path.dirname(os.path.abspath(__file__))\n"
          "p = subprocess.Popen([sys.executable, os.path.join(d, 'bin', 'or_ask.py')])\n"
          "open(os.path.join(d, 'ask.pid'), 'w').write(str(p.pid))\ntime.sleep(120)\n")


def alive(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    # a killed child of THIS process stays a zombie until reaped: reap it, then ask again
    try:
        os.waitpid(pid, os.WNOHANG)
    except OSError:
        pass
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def fixture_env(home, **extra):
    env = {k: v for k, v in os.environ.items() if k not in RUN_VARS}
    env["HOME"] = home
    env.update(extra)
    return env


def family(with_driver=False):
    d = tempfile.mkdtemp(prefix="stop-loop-", dir=RUN_ROOT)   # the fixture HOME; its .claude/bin is where the loop's tools live
    root = os.path.join(d, ".claude"); os.makedirs(os.path.join(root, "bin")); os.makedirs(os.path.join(root, "evidence"))
    for rel, src in (("bin/unit_runner.py", RUNNER), ("core.or_fanout.py", FANOUT), ("bin/or_ask.py", SLEEPER), ("bin/loop_until.sh", "#!/bin/bash\nsleep 120\n")):
        with open(os.path.join(root, rel), "w") as f: f.write(src)
    env = fixture_env(d)
    pids = {"runner": subprocess.Popen([sys.executable, os.path.join(root, "bin", "unit_runner.py")], env=env, start_new_session=True).pid}
    # THE PASS'S DETACHED HELPERS, launched the way loop_pass.sh launches them: <tree>/scripts/probe_round.py and
    # <tree>/scripts/diag_round.py, by absolute path so a stop scoped to one tree finds them (A/B/C 2026-09-23: a pattern naming only bin/ let them outlive
    # every stop and spend on stale candidates across arm boundaries), carrying the run the driver exports to everything it starts
    os.makedirs(os.path.join(d, "scripts"), exist_ok=True)
    run = os.path.join(root, "evidence", "loop-runs", "run-t")
    for helper in ("probe_round", "diag_round"):
        with open(os.path.join(d, "scripts", helper + ".py"), "w") as f: f.write("import time\ntime.sleep(120)\n")
        pids[helper] = subprocess.Popen([sys.executable, "-B", os.path.join(d, "scripts", helper + ".py")], cwd=d,
                                        env=dict(env, BROTHER_RUN_DIR=run), start_new_session=True).pid
    if with_driver:
        pids["driver"] = subprocess.Popen(["bash", os.path.join(root, "bin", "loop_until.sh")], env=env, start_new_session=True).pid
    for name in ("fanout", "ask"):
        p = os.path.join(root, name + ".pid")
        for _ in range(100):
            if os.path.isfile(p) and open(p).read().strip(): break
            time.sleep(0.05)
        pids[name] = int(open(p).read())
    return d, pids


def _count(text):
    import re
    m = re.search(r"STOP INCOMPLETE: (\d+) process", text)
    return int(m.group(1)) if m else 0


def stop(only, *args, extra_env=None):
    """the real stop, with the fixture dir as its HOME (so its .claude/bin is the loop's bin) and as its kill scope"""
    env = fixture_env(only, STOP_LOOP_ONLY=only)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(["bash", STOP] + list(args), capture_output=True, text=True, env=env, timeout=60)


def driver_only(body):
    """A lone driver (matches DRIVER_RX by living at the fixture home's .claude/bin/loop_until.sh), scoped by its own
    tempdir path so STOP_LOOP_ONLY=d can never reach a real loop. No runner is started: these fixtures test only the
    driver's own TERM-then-grace-then-KILL wait, never the rest of the family."""
    d = tempfile.mkdtemp(prefix="stop-loop-driver-", dir=RUN_ROOT)
    os.makedirs(os.path.join(d, ".claude", "bin")); os.makedirs(os.path.join(d, ".claude", "evidence"))
    script = os.path.join(d, ".claude", "bin", "loop_until.sh")
    with open(script, "w") as f: f.write(body)
    p = subprocess.Popen(["bash", script], env=fixture_env(d), start_new_session=True)
    return d, p


def _slow_clean_body(marker):
    # A driver's real end (proof-ending, settle, runner stop, end clock, end snapshot, receipt) measured 9.8-10.0s at
    # load about 10; 14s stands in for that clean end taking longer than the old 12s wait but well under a 30s grace.
    return "#!/bin/bash\ntrap 'sleep 14; : > \"%s\"; exit 0' TERM\nwhile :; do sleep 1; done\n" % marker


def _stuck_body():
    return "#!/bin/bash\ntrap '' TERM\nwhile :; do sleep 1; done\n"


def main():
    global RUN_ROOT
    RUN_ROOT = tempfile.mkdtemp(prefix="stop-loop-run-")
    cases, spawned = [], []
    try:
        d, p = family(); spawned += p.values()
        other, q = family(); spawned += q.values()
        dry = stop(d, "--dry")
        cases += [("--dry kills nothing", all(alive(x) for x in p.values())),
                  ("--dry exits 1 and names what is alive", dry.returncode == 1 and _count(dry.stdout) >= 3 and "core.or_fanout" in dry.stdout)]   # at least: the system Python's launcher shows each process twice
        r = stop(d)
        cases += [("the runner is stopped", not alive(p["runner"])),
                  ("its fan out, started in its OWN session, is stopped too", not alive(p["fanout"])),
                  ("the fan out's bridge call is stopped too", not alive(p["ask"])),
                  ("the pass's reprobe helper, started as scripts/probe_round.py, is stopped too", not alive(p["probe_round"])),
                  ("the pass's diagnose helper, started as scripts/diag_round.py, is stopped too", not alive(p["diag_round"])),
                  ("it says STOPPED and exits 0 only when nothing is left", r.returncode == 0 and "STOPPED: nothing of the loop is alive" in r.stdout),
                  ("a family outside the scope is untouched", all(alive(x) for x in q.values()))]
        cases += [("--dry on a stopped family exits 0", stop(d, "--dry").returncode == 0)]
        stop(other)
        d2, p2 = family(with_driver=True); spawned += p2.values()
        r2 = stop(d2, "--runners-only")
        cases += [("--runners-only leaves the driver alive", alive(p2["driver"]) and not alive(p2["runner"]) and not alive(p2["fanout"])),
                  ("and still exits 0, because the driver is not its business", r2.returncode == 0)]
        r3 = stop(d2)
        cases += [("the default stops the driver as well", not alive(p2["driver"]) and r3.returncode == 0)]
        # A driver's clean end after TERM (proof-ending, settle, runner stop, end clock, end snapshot, receipt) can run
        # past the old 12s wait; this one takes about 14s and must survive to write its own marker, never be KILLed
        # mid-cleanup (STOP_DRIVER_GRACE-2026-09-27).
        marker = os.path.join(tempfile.mkdtemp(prefix="stop-loop-marker-", dir=RUN_ROOT), "clean-exit")
        d3, p3 = driver_only(_slow_clean_body(marker)); spawned.append(p3.pid)
        r4 = stop(d3)
        cases += [("a driver whose clean end takes about 14s past TERM is not killed early", not alive(p3.pid) and os.path.exists(marker)
                   and r4.returncode == 0 and "STOPPED" in r4.stdout)]
        # A driver that never exits, however long it is given, is still KILLed once its (here shortened, so the test
        # stays fast) grace elapses, and the elapsed wall time must reflect the OVERRIDE, never the 30s default.
        d4, p4 = driver_only(_stuck_body()); spawned.append(p4.pid)
        t1 = time.time(); r5 = stop(d4, extra_env={"STOP_DRIVER_GRACE": "4"}); dt5 = time.time() - t1
        cases += [("a driver that never exits is killed once its (overridden, short) grace elapses", not alive(p4.pid)
                   and r5.returncode == 0 and "STOPPED" in r5.stdout and 6 <= dt5 < 12)]
    finally:
        for pid in spawned:
            try: os.kill(pid, signal.SIGKILL)
            except OSError: pass
        shutil.rmtree(RUN_ROOT, ignore_errors=True)   # after every fixture process is killed
    cases += [("the run leaves no fixture folder behind", not os.path.exists(RUN_ROOT))]
    bad = [n for n, good in cases if not good]
    print("selftest: %d cases, %s" % (len(cases), "OK" if not bad else "FAILED: " + ", ".join(bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
