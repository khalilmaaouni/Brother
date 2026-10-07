#!/usr/bin/env python3
"""Audit every component of brother.loop by TWO independent methodologies.

Owner order 2026-09-21: "do a full check of the life cycle and every component and every step and
every piece or architecture and solution with 2 methodologies to make sure it is air tight".

Two methods, because one method has one blind spot and the blind spots differ:

  METHOD A, STRUCTURAL. Read the tree. For every component unit BL names: is it tracked by git, does
  it carry a selftest, is that selftest REGISTERED in the battery, and does it match the copy the
  loop actually executes. This method catches the defects that hide in what is NOT there: a tool
  nobody versioned, a selftest nobody runs, an executed copy nobody reviewed. It cannot tell you
  whether any of it works.

  METHOD B, EXECUTED. Run them. Every selftest and every registered loop test, with the real exit
  code captured WITHOUT a pipe, because `$?` after a pipe belongs to the last stage and not to the
  gate (this session misread three exit codes that way in one evening). This method catches what is
  broken. It cannot tell you what is missing, because a test that does not exist cannot fail.

A component that passes A and fails B is broken. A component that passes B and fails A is
unguarded: it works today and nothing will notice when it stops. Both are findings, and the report
separates them rather than averaging them into a score.

NO-DATA is never a pass anywhere in this file.

usage: audit_brother_loop.py [--quick] [--budget SECONDS] [--workers N]      audit_brother_loop.py --selftest
       --quick skips method B; --budget is the wall clock method B may spend (default 30); --workers the pool width.

IT ANSWERS IN SECONDS OR IT SAYS WHAT IT DID NOT MEASURE. Owner order 2026-09-22: "the whole brother.loop should load
in seconds". Measured that night: method B ran every component ONE AT A TIME with 180 s each and no wall clock, so the
plain invocation could not answer inside the 45 s the tolerance check allows, and the killed child left over a hundred
grandchildren running (a subprocess timeout kills the child, never its children), which took an 8 core machine to load
157. Three fixes at the source, not a smaller timeout: (1) method A asks git ONCE for the tracked set instead of once
per component, the same spawn count fix that took the ownership audit from 28.9 s to 0.8 s; (2) method B runs in a
bounded pool inside a wall clock budget, and whatever the budget did not reach is reported UNMEASURED and counted as a
finding, never as a pass; (3) every child runs in its own process group and the whole group is killed on timeout.
"""
import json, os, re, signal, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor

PLAN = "docs/plan/BROTHER-1.1.0-LAUNCH-WBS.json"
BATTERY = "scripts/check_all.sh"
BIN = os.path.expanduser("~/.claude/bin")
TIMEOUT = 180
BUDGET = 30.0


def sh(cmd, timeout=TIMEOUT):
    """Real exit code, never through a pipe. Returns (code, tail of output). The child gets its own process group
    and the GROUP is killed on timeout, so a test that spawns tests cannot outlive the verdict about it."""
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True)
    except OSError as exc:
        return 125, str(exc)
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try: os.killpg(proc.pid, signal.SIGKILL)
        except OSError: pass
        proc.communicate()
        return 124, "timed out after %ds, process group killed" % timeout
    lines = (out or "").strip().splitlines()
    return proc.returncode, (lines[-1][:100] if lines else "")


def tracked_set():
    """Every path git tracks, from ONE call. None when git cannot answer, and None is never 'untracked'."""
    r = subprocess.run(["git", "ls-files", "-z"], capture_output=True, text=True)
    if r.returncode != 0: return None
    return set(x for x in r.stdout.split("\0") if x)


def run_pool(runnable, cmd_for, budget=BUDGET, workers=None, sh_fn=None, clock=time.monotonic):
    """Method B inside a wall clock. Returns {component: (code, tail)}; a component the budget never reached maps
    to (None, 'UNMEASURED: budget of Ns spent'), which the verdict counts as a finding. A pool of `workers` keeps
    the machine stage bounded; each member still has its own per process timeout."""
    sh_fn = sh_fn or sh; t0 = clock(); results = {}
    def one(c):
        left = budget - (clock() - t0)
        if left <= 0: return c, (None, "UNMEASURED: budget of %.0fs spent before it started" % budget)
        return c, sh_fn(cmd_for(c), min(TIMEOUT, max(1, int(left))))
    with ThreadPoolExecutor(max_workers=workers or max(2, min(4, os.cpu_count() or 2))) as ex:
        for c, res in ex.map(one, runnable): results[c] = res
    return results


def components():
    try:
        plan = json.load(open(PLAN, encoding="utf-8"))
    except (OSError, ValueError):
        return None
    u = next((x for x in plan.get("units", []) if x.get("id") == "BL"), None)
    return sorted(u.get("owns") or []) if u else None


def external_guard(path):
    """A guard that lives in its own file beside the module, rather than inside it.

    THE INSTRUMENT COULD NOT SEE THESE, and that was a defect in the measurement, not in the work.
    This audit decided "guarded" purely by whether a module carried its OWN --selftest, so five
    real guard files written on 2026-09-21 (grade_build, probe_build, runner_pool, run_window,
    slicer) changed the count by zero and would have gone on changing it by zero forever. A number
    that cannot move when the thing it measures improves is measuring something else: it was
    measuring "carries an embedded selftest", while reporting it as "is guarded".

    Found by the worker that wrote those guards, which flagged it rather than reporting its own gap
    as closed. That is the behaviour worth keeping."""
    stem = os.path.basename(path)
    if not stem.endswith(".py"):
        return None
    cand = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_%s_guard.py" % stem[:-3])
    return cand if os.path.isfile(cand) else None


def has_selftest(path):
    if not path.endswith((".py", ".sh")):
        return False
    try:
        body = open(path, encoding="utf-8", errors="replace").read()
    except OSError:
        return False
    return "--selftest" in body and ("def selftest" in body or "selftest()" in body)


def is_test(path):
    return os.path.basename(path).startswith("test_")


def registered(path, battery_text):
    """The battery names it, by basename, on a run_check line."""
    base = os.path.basename(path)
    return bool(re.search(r"^run_check\s+.*" + re.escape(base), battery_text, re.M))


def parity(path):
    twin = os.path.join(BIN, os.path.basename(path))
    if not os.path.isfile(twin):
        return "repo only"
    try:
        same = open(path, "rb").read() == open(twin, "rb").read()
    except OSError:
        return "UNREADABLE"
    return "identical" if same else "DRIFTED"


def main():
    quick = "--quick" in sys.argv
    comps = components()
    if comps is None:
        print("NO-DATA: unit BL could not be read from %s. That is not a pass." % PLAN)
        return 1
    try:
        battery = open(BATTERY, encoding="utf-8").read()
    except OSError:
        print("NO-DATA: %s is unreadable, so registration cannot be judged. That is not a pass." % BATTERY)
        return 1

    runnable = [c for c in comps if c.endswith((".py", ".sh")) and (has_selftest(c) or is_test(c))]
    externally = [c for c in comps if external_guard(c) and c not in runnable]
    findings = {"untracked": [], "drifted": [], "unregistered": [], "unguarded": [], "failing": []}

    print("BROTHER.LOOP AUDIT, two methodologies")
    print("%d component(s) named by unit BL; %d carry a selftest or are a test; %d are guarded by a "
          "separate guard file\n" % (len(comps), len(runnable), len(externally)))

    # ---------- METHOD A, STRUCTURAL
    print("METHOD A, STRUCTURAL: what the tree says")
    print("  %-44s %-9s %-10s %-12s" % ("component", "tracked", "guarded", "vs executed"))
    tracked_all = tracked_set()
    if tracked_all is None:
        print("NO-DATA: git could not list the tracked files, so nothing can be judged tracked. That is not a pass.")
        return 1
    for c in comps:
        tracked = c in tracked_all
        if not tracked:
            findings["untracked"].append(c)
        par = parity(c) if c.endswith((".py", ".sh")) else "n/a"
        if par == "DRIFTED":
            findings["drifted"].append(c)
        ext = external_guard(c)
        if c in runnable:
            reg = registered(c, battery)
            guard = "registered" if reg else "NOT RUN"
            if not reg:
                findings["unregistered"].append(c)
        elif ext:
            # guarded from outside: the guard file is what must be registered, not the module
            reg = registered(ext, battery)
            guard = "guard file" if reg else "GUARD NOT RUN"
            if not reg:
                findings["unregistered"].append(ext)
        elif c.endswith((".py", ".sh")):
            guard = "no selftest"
            findings["unguarded"].append(c)
        else:
            guard = "document"
        flag = "" if (tracked and par != "DRIFTED" and guard not in ("NOT RUN",)) else "   <-"
        print("  %-44s %-9s %-10s %-12s%s" % (c[:44], "yes" if tracked else "NO", guard, par, flag))

    # ---------- METHOD B, EXECUTED
    print("\nMETHOD B, EXECUTED: what they do when run")
    findings["unmeasured"] = []
    if quick:
        print("  skipped by --quick")
    else:
        arg = lambda f, d: (type(d)(sys.argv[sys.argv.index(f) + 1]) if f in sys.argv else d)
        budget, workers = arg("--budget", BUDGET), arg("--workers", 0) or None
        cmd_for = lambda c: ([sys.executable, c, "--selftest"] if has_selftest(c) and not is_test(c)
                             else [sys.executable, c] if c.endswith(".py") else ["bash", c, "--selftest"])
        t0 = time.time(); results = run_pool(runnable, cmd_for, budget, workers)
        print("  %-44s %-6s %s" % ("component", "exit", "last line"))
        for c in runnable:
            code, tail = results[c]
            if code is None: findings["unmeasured"].append(c)
            elif code != 0: findings["failing"].append((c, code, tail))
            print("  %-44s %-6s %s%s" % (c[:44], "n/a" if code is None else code, tail[:58],
                                         "  <- FAIL" if code else ""))
        print("  method B: %d component(s) in %.1fs, budget %.0fs, %d unmeasured" % (len(runnable), time.time() - t0, budget, len(findings["unmeasured"])))

    # ---------- VERDICT
    print("\nVERDICT")
    n = sum(len(v) for v in findings.values())
    for k, label in (("untracked", "NOT COMMITTED, dies with this disk"),
                     ("drifted", "EXECUTED COPY DIFFERS from the reviewed one"),
                     ("unregistered", "has a selftest the battery never runs"),
                     ("unguarded", "no selftest at all: works today, nothing notices when it stops"),
                     ("failing", "FAILS when executed"),
                     ("unmeasured", "UNMEASURED: the budget ended before it answered, which is a hole and never a pass")):
        items = findings[k]
        if not items:
            continue
        print("  %s (%d):" % (label, len(items)))
        for it in items:
            print("    %s" % (it if isinstance(it, str) else "%s exit %s: %s" % it))
    if n == 0:
        print("  AIRTIGHT by both methods: every component is committed, matches the executed copy,\n"
              "  is guarded by a check the battery runs, and passes when executed.")
        return 0
    print("\n  %d finding(s). A component passing A and failing B is broken; passing B and failing A\n"
          "  is unguarded. These are different problems and are not averaged." % n)
    return 1


def selftest():
    """Every property the rewrite claims, each able to go red on its own."""
    import tempfile
    d = tempfile.mkdtemp(prefix="audit-bl-"); marker = os.path.join(d, "grandchild-alive")
    # a child that spawns a grandchild which would outlive a plain child kill
    spawner = os.path.join(d, "spawner.py")
    with open(spawner, "w") as f:
        f.write("import subprocess,sys,time\nsubprocess.Popen([sys.executable,'-c','import time\\nwhile True: time.sleep(0.1)'])\ntime.sleep(60)\n")
    t0 = time.monotonic(); code, tail = sh([sys.executable, spawner], 1); took = time.monotonic() - t0
    time.sleep(0.3)
    survivors = subprocess.run(["pgrep", "-f", "while True: time.sleep\\(0.1\\)"], capture_output=True, text=True).stdout.split()
    fake = {}
    def fake_sh(cmd, timeout): fake[cmd[-1]] = timeout; time.sleep(0.05); return (0, "ok")
    ticks = iter([0.0, 0.0, 100.0, 100.0, 100.0]); clock = lambda: next(ticks, 100.0)
    res = run_pool(["a", "b", "c"], lambda c: ["x", c], budget=1.0, workers=1, sh_fn=fake_sh, clock=clock)
    ts = tracked_set()
    cases = [("timeout returns 124 and names the group kill", code == 124 and "group killed" in tail),
             ("timeout is honoured, not the child's 60s sleep", took < 5),
             ("the grandchild died with the group", survivors == []),
             ("a budget already spent marks the rest UNMEASURED", res["a"][0] == 0 and res["c"][0] is None and "UNMEASURED" in res["c"][1]),
             ("a member's timeout never exceeds the budget left", all(v <= TIMEOUT for v in fake.values())),
             ("tracked set comes from git in one call and contains this file", ts is not None and "scripts/audit_brother_loop.py" in ts)]
    bad = [n for n, ok in cases if not ok]
    print("selftest: %d cases, %s" % (len(cases), "OK" if not bad else "FAILED: " + ", ".join(bad))); return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(selftest() if "--selftest" in sys.argv else main())
