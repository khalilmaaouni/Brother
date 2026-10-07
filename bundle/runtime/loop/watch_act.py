#!/usr/bin/env python3
"""The playbook behind the watch (P3 of the 2026-09-24 diagnosis): a watch class is turned into ONE deterministic check
that runs without a person, and its result is what the log carries. The orchestrator then reads a verdict, not a log.

usage (launch worktree root): watch_act.py <class> <reason> [--run-dir DIR]      watch_act.py --selftest
prints one line: ACTED <class>: <what the check found> | ESCALATE <class>: <why a person is needed> | NONE <class>: no automatic action
exit 0 acted or none, 1 escalate (the finding needs a hand), 3 NO-DATA (the check could not run)

WHY. On 2026-09-24 every ACT class of the night (six of them) was read, classified and acted on by hand at the cost of the
orchestrator's minutes and tokens; two of the six (READY-NOT-LANDING, PROBE-NEVER-APPROVES) were gate defects that a
replay of the gate on the clean tree, or of the canary set, names in one run. The check never fixes anything: a red
replay is a gate defect and is ESCALATED by name; a green replay means the warning is about the builds, not the gates,
and the lanes go on. Anything unknown is NONE, never a pass."""
import os, subprocess, sys, time

BIN = os.path.expanduser("~/.claude/bin")
DISCOVER = os.path.join("scripts", "plugin_runtime_fast_discover.py")


def replay_discover(run=None, cwd=None):
    """The landing's discover gate on the CLEAN tree: exit 0 means the gate is honest and READY builds are dropping on
    their own defects; non zero means the gate itself is red and every landing would be refused (2026-09-24 01:49)."""
    run = run or subprocess.run
    t0 = time.time()
    try:
        r = run([sys.executable, "-B", DISCOVER], capture_output=True, text=True, timeout=1800, cwd=cwd)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, "the discover gate could not run: %s" % str(exc)[:120]
    last = (r.stdout.strip().splitlines() or [""])[-1][:120]
    return r.returncode, "discover gate on the clean tree exit %d in %d s: %s" % (r.returncode, time.time() - t0, last)


def replay_canary(run=None, cwd=None):
    """The canary set (every fixture through every stage): exit 0 means the judging path approves a known good build,
    so a probe gate that never approves is judging the builds; non zero names the stage that changed."""
    run = run or subprocess.run
    try:
        r = run([sys.executable, "-B", os.path.join(BIN, "loop_canary.py")], capture_output=True, text=True, timeout=1800, cwd=cwd)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, "the canary could not run: %s" % str(exc)[:120]
    last = (r.stdout.strip().splitlines() or [""])[-1][:140]
    return r.returncode, last


PLAYBOOK = {"READY-NOT-LANDING": replay_discover, "PROBE-NEVER-APPROVES": replay_canary}


def act(cls, reason, run=None, cwd=None):
    """(line, exit code). The warning kind is read from the reason's own words ("new warning kind X")."""
    kind = reason.split("new warning kind ", 1)[1].split()[0] if "new warning kind " in reason else ""
    check = PLAYBOOK.get(kind)
    if cls != "warning" or not check:
        return "NONE %s: no automatic action for %s" % (cls, kind or reason[:80]), 0
    rc, detail = check(run=run, cwd=cwd)
    if rc is None: return "NO-DATA %s: %s" % (kind, detail), 3
    if rc == 0: return "ACTED %s: the gate is honest (%s); the warning is about the builds, lanes go on" % (kind, detail), 0
    return "ESCALATE %s: a gate defect, no landing can pass until it is read (%s)" % (kind, detail), 1


def main(argv=None):
    a = list(sys.argv[1:] if argv is None else argv)
    if a[:1] == ["--selftest"]: return selftest()
    if len(a) < 2: print("NO-DATA: usage: watch_act.py <class> <reason>"); return 3
    line, code = act(a[0], " ".join(a[1:]))
    print(line); return code


def selftest():
    """Whatever raises inside the eagerly built cases is reported as a FAILED verdict naming the
    exception, exit 1, never a bare traceback (scripts/test_selftests_report_rather_than_crash.py)."""
    try:
        return _selftest_body()
    except Exception as exc:                       # noqa: BLE001 - a verdict beats a traceback
        print("selftest: FAILED before it could finish, %s: %s"
              % (type(exc).__name__, str(exc)[:120]))
        return 1


def _selftest_body():
    calls = []
    def fake(rc, out="last line"):
        def run(argv, **k):
            calls.append(argv)
            class R: pass
            r = R(); r.returncode = rc; r.stdout = out + "\n"; r.stderr = ""; return r
        return run
    def boom(argv, **k): raise OSError("no such tool")
    cases = [("READY-NOT-LANDING with a green discover gate is ACTED, lanes go on", act("warning", "new warning kind READY-NOT-LANDING", run=fake(0))[1] == 0 and "ACTED" in act("warning", "new warning kind READY-NOT-LANDING", run=fake(0))[0]),
             ("READY-NOT-LANDING with a red discover gate is ESCALATED as a gate defect", act("warning", "new warning kind READY-NOT-LANDING", run=fake(1))[1] == 1),
             ("PROBE-NEVER-APPROVES replays the canary set", any("loop_canary.py" in " ".join(map(str, c)) for c in (calls.clear() or [])) or (act("warning", "new warning kind PROBE-NEVER-APPROVES", run=fake(0)) and any("loop_canary.py" in " ".join(map(str, c)) for c in calls))),
             ("a red canary is ESCALATED", act("warning", "new warning kind PROBE-NEVER-APPROVES", run=fake(1, "CANARY REFUSED at probe"))[1] == 1),
             ("a check that cannot run is NO-DATA, never a pass", act("warning", "new warning kind READY-NOT-LANDING", run=boom)[1] == 3),
             ("an unknown warning kind is NONE, exit 0, named", act("warning", "new warning kind POOL-DRAINING", run=fake(0)) == ("NONE warning: no automatic action for POOL-DRAINING", 0)),
             ("a non warning class is NONE", act("pool", "running 2 of 6 lanes at pass 3", run=fake(0))[1] == 0),
             ("the discover replay is the gate script itself", (act("warning", "new warning kind READY-NOT-LANDING", run=fake(0)) and any(DISCOVER in " ".join(map(str, c)) for c in calls)) is True)]
    bad = [n for n, ok in cases if not ok]
    print("selftest: %d cases, %s" % (len(cases), "OK" if not bad else "FAILED: " + ", ".join(bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
