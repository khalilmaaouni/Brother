#!/usr/bin/env python3
"""The orchestrator's watch: one health verdict from the newest driver log, mechanical, every poll.
usage: loop_watch.py [--log <loop-until log>] [--lanes N]        loop_watch.py --selftest
       loop_watch.py --idle [--run-dir D] [--heartbeat F] [--pool P] [--stage-log S]     (STAB1.l: the look from OUTSIDE the driver)
Prints ONE line: HEALTHY <summary> (exit 0) | ACT <class>: <reason> (exit 1) | NO-DATA <why> (exit 3).
Owner 2026-09-22 21:3x ("find a way to be more proactive ... with a proper SOP and workflow"): a stall was found two hours
late because detection waited for a wake. The wait now polls this verdict every 20 s and breaks on ACT; the SOP names the
playbook per class. Facts are read from the PULSE, BUDGET, WARN, ALARM and LOOP lines the driver already writes."""
import os, re, sys
import math
import json

PULSE_RE = re.compile(r"^PULSE pass (\d+) \| ([0-9.]+) h \| landed (\d+) \| grade ok (\d+)/(\d+) .*?\| parked (\d+) of (\d+) \| running (\d+)", re.M)
BUDGET_RE = re.compile(r"^BUDGET  this run: spent ([0-9.]+) of ([0-9.]+) USD \| remaining ([0-9.]+) USD", re.M)
READY_RE = re.compile(r"^READY   (\d+) to land", re.M)
WARN_RE = re.compile(r"^WARN ([A-Z-]+):", re.M)
TERMINAL_RE = re.compile(r"^(ALARM [A-Z-]+|LOOP (BLOCKED|UNFUNDED|BUDGET|DEADLINE|FINISHED|STALLED|INTERRUPTED))", re.M)
KNOWN_KINDS = ("GATE-NEVER-APPROVES", "GRADER-NEVER-APPROVES", "CHECKER-NEVER-APPROVES", "PROBE-NEVER-APPROVES", "BEHIND-BASE-RATE")


LEDGER = os.path.expanduser("~/.claude/evidence/unit-ledger.jsonl")


def base_rate(ledger_path=LEDGER, last=200, floor_n=100):
    """The grader's pass rate over the last `last` PASS or FAIL rows of the unit ledger, or None under floor_n rows or an
    unreadable ledger (the constant quarter then stands alone)."""
    rows = []
    try:
        with open(ledger_path, encoding="utf-8") as fh:
            for line in fh:
                try: r = json.loads(line)
                except ValueError: continue
                if isinstance(r, dict) and r.get("grade") in ("PASS", "FAIL"): rows.append(r["grade"] == "PASS")
    except OSError: return None
    rows = rows[-last:]
    base = (sum(rows) / len(rows)) if len(rows) >= floor_n else None
    return None if base is not None and base >= 0.95 else base   # a record that never fails is not a process to control (review 2026-09-23)


def quality_limit(base, n, target=0.25):
    """THE CONTROL LIMIT (fix 3, 2026-09-22): the owner's quarter is a TARGET; the process's own limit is its base rate
    minus two sigma of a rate measured over n rulings, and the ACT threshold is the larger of the two, so the limit never
    falls below the target and a process running well above it is caught on its own history. No base: the target."""
    try:
        if base is None or not (0.0 <= float(base) <= 1.0) or int(n) <= 0: return float(target)
        return max(float(target), float(base) - 2.0 * math.sqrt(float(base) * (1.0 - float(base)) / int(n)))
    except (TypeError, ValueError): return float(target)


FRAME_RE = re.compile(r'^\s*File "([^"]+)", line (\d+), in (\S+)', re.M)
EXC_RE = re.compile(r"^([A-Za-z_][\w.]*(?:Error|Exception|Expired|Exit|Interrupt|Refused)\b.*)$", re.M)
REFUSAL_RES = (re.compile(r"^(START REFUSED \[[^\]]+\].*)$", re.M),
               re.compile(r"^(runner pool NOT refilled: .*)$", re.M),
               re.compile(r"^(\S+ exit=[1-9]\d*.*)$", re.M))
HELD_EXIT_RE = re.compile(r"^(nothing landed.*\n)land_batch exit=[1-9]\d*.*$", re.M)
FIX = {   # what the session does about each cause class, without asking (owner 2026-09-30: "signals to what to fix by yourself")
    "crash": "fix the raising function at its source (the frame named), add a test that goes red without the fix, deploy, relaunch",
    "refused": "read the refusing step's own reason, clear it at its source (never around the gate), relaunch",
    "money": "the run budget is nearly spent: ask the owner for a raise in his words, never raise it unasked",
    "hold": "a unit is held by rule (stage, dependency or spec under 9): repair its spec or land its predecessor",
    "pool": "read runner_pool --dry for each skip reason; fix the first reason that repeats",
    "quality": "read the grader refusal histogram since the last landing; put the top reason first in the next brief",
    "yield": "nothing lands: read the last land_batch log and the checker verdicts, fix the first refusal at its source",
}


def cause(text):
    """(class, line) naming WHY the loop stopped or refuses, from the log text, or ("", "") when nothing names it.
    A crash names the innermost frame inside the repository or the deployed bin (standard library frames are skipped)
    and the exception line, so the fix starts at the right file (2026-09-30: an uncaught TimeoutExpired in land_batch.sh
    stopped the loop and the watch printed only "ALARM UNPRODUCTIVE")."""
    if not isinstance(text, str): return "", ""
    tb = text.rfind("Traceback (most recent call last):")
    if tb >= 0:
        block = text[tb:]
        frames = [f for f in FRAME_RE.findall(block) if "/lib/python" not in f[0] and "/python3." not in f[0]]
        exc = EXC_RE.findall(block)
        where = ("%s:%s in %s" % (os.path.basename(frames[-1][0]), frames[-1][1], frames[-1][2])) if frames else "an unnamed frame"
        return "crash", "%s at %s" % ((exc[-1] if exc else "an exception")[:160], where)
    # A HOLD IS NOT A REFUSAL, the same rule loop_pass.sh applies before the refill: land_batch exits 1 with "nothing
    # landed" when every build was held by the checker or a STATUS, and the pool still refills. Measured 2026-09-30
    # 18:1x: the watch printed "CAUSE refused: land_batch exit=1" on a healthy pass, pointing the owner at a gate.
    scan = HELD_EXIT_RE.sub("", text)
    for r in REFUSAL_RES:
        m = r.findall(scan)
        if m: return "refused", m[-1][:200]
    if "budget cap reached" in text: return "money", "the runner budget cap is reached"
    held = re.findall(r"^\S+\s+\S+\s+skip: (\S+ held: .*)$", text, re.M)
    if held: return "hold", held[-1][:200]
    return "", ""


def explain(text, **kw):
    """The --explain answer: the verdict, then CAUSE and FIX lines when a cause is named."""
    w, c, r = verdict(text, **kw)
    lines = ["%s %s%s" % (w, (c + ": ") if c else "", r)]
    k, line = cause(text)
    if k:
        lines += ["CAUSE %s: %s" % (k, line), "FIX   %s" % FIX[k]]
    elif c in FIX:
        lines += ["FIX   %s" % FIX[c]]
    return "\n".join(lines)


def verdict(text, lanes=6, seen_kinds=(), driver_alive=True, no_landing_limit_h=1.5, budget_floor_share=0.10, base=None):
    """(word, class, reason) from the log text. Every unknown is NO-DATA, never HEALTHY."""
    if not isinstance(text, str) or not text.strip(): return "NO-DATA", "", "empty log"
    t = TERMINAL_RE.search(text)
    if t:
        k, line = cause(text)
        return "ACT", "terminal", t.group(0) + (" | cause %s: %s" % (k, line) if k else "")
    if not driver_alive:
        k, line = cause(text)
        return "ACT", "driver", "the driver process is gone with no terminal line" + (" | cause %s: %s" % (k, line) if k else "")
    pulses = PULSE_RE.findall(text)
    if not pulses: return "NO-DATA", "", "no PULSE line yet"
    p, hours, landed, gok, gall, parked, touched, running = (float(x) for x in pulses[-1])
    kinds = [k for k in WARN_RE.findall(text) if k not in seen_kinds and k not in KNOWN_KINDS]
    if kinds: return "ACT", "warning", "new warning kind %s" % kinds[-1]
    b = BUDGET_RE.findall(text)
    if b:
        spent, budget, remaining = (float(x) for x in b[-1])
        if budget > 0 and remaining <= budget * budget_floor_share: return "ACT", "money", "remaining %.2f of %.2f USD" % (remaining, budget)
    # QUALITY BEFORE PARKING (owner 2026-09-22 21:4x, "why did not we catch it before the warning"): the grader's pass rate
    # falls long before units park; under a quarter after forty rulings is the signal, and the playbook's READ step is the
    # refusal histogram since the last landing.
    if gall >= 40 and gok / gall < quality_limit(base, gall): return "ACT", "quality", "grader passes %d of %d (under the limit %.2f%s): read the refusal histogram and fix the brief" % (gok, gall, quality_limit(base, gall), ", base rate %.2f" % base if base is not None else "")
    if touched >= 2 and parked / touched >= 0.5: return "ACT", "pool", "parked %d of %d touched (refused or exhausted rounds)" % (parked, touched)
    if p >= 3 and running < max(1, lanes // 2): return "ACT", "pool", "running %d of %d lanes at pass %d" % (running, lanes, p)
    ready = READY_RE.findall(text)
    if landed == 0 and hours >= no_landing_limit_h and (not ready or int(ready[-1]) == 0):
        return "ACT", "yield", "0 landed and 0 READY after %.1f h" % hours
    return "HEALTHY", "", "pass %d | %.1f h | landed %d | running %d | parked %d of %d" % (p, hours, landed, running, parked, touched)


def newest_run_dir(root):
    """The newest run-* directory under the runs root (loop_until.sh make_run_dir names them run-<date>-<time>-<pid>, so the
    name sorts by start), or None. Reads only: it creates nothing."""
    try:
        names = sorted(n for n in os.listdir(root) if re.fullmatch(r"run-\d{8}-\d{6}-\d+", n) and os.path.isdir(os.path.join(root, n)))
    except OSError:   # sbe: allow-silent an unreadable runs root is None and idle_look prints NO-DATA naming it
        return None
    return os.path.join(root, names[-1]) if names else None


def _pool_count(pool):
    """(n, why): the pool's DRY look, `started N`, the same look loop_pass.sh takes (STAB1.j). n is None with the reason when
    the look fails, times out or prints no count: NO-DATA, never zero."""
    import subprocess
    try:
        r = subprocess.run([sys.executable, "-B", pool, "--dry"], capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, "the pool's dry look did not run (%s)" % type(exc).__name__
    m = re.findall(r"^started (\d+) \|", r.stdout, re.M)
    if r.returncode != 0 or not m:
        return None, "the pool's dry look gave no count (exit %d)" % r.returncode
    return int(m[-1]), ""


def idle_look(run_dir, heartbeat, pool, stage_log=None):
    """THE IDLE-WITH-WORK LOOK FROM OUTSIDE THE DRIVER (STAB1.l). Inside the pass it cannot be seen: run_pass waits on the
    pass, so at the pass's monitor look the driver is always alive, and a driver that dies between passes starts no pass at
    all. This reader runs in its own process and asks loop_heartbeat's own rule (read, classify, TERMINAL; never a second
    staleness rule) whether the driver is alive. Not alive AND the pool would start a unit: stage_log's monitor verb (the one
    writer) appends the run's idle_with_work row, and the answer is ACT (exit 1). The production caller is brother_night_tick.py (scheduled, armed by BROTHER_STALL_PLAN), which turns
    that answer into an IDLE-WITH-WORK finding for the alert file and the notification; no session wait polls --idle. Alive:
    only the lease look, no dry look. A PAUSED heartbeat is the owner's pause, not idleness. Returns (word, line): HEALTHY,
    ACT or NO-DATA. A heartbeat that cannot be read counts as not alive (classify says ALARM); a pool look or a monitor that
    cannot answer is NO-DATA, never HEALTHY."""
    import json, subprocess
    here = os.path.dirname(os.path.abspath(__file__))
    stage_log = stage_log or os.path.join(here, "stage_log.py")
    if here not in sys.path: sys.path.insert(0, here)
    import loop_heartbeat as lh
    if not run_dir:
        return "NO-DATA", "no run directory was named and none exists under the runs root"
    run, logf, pidf = os.path.basename(os.path.normpath(run_dir)), os.path.join(run_dir, "run-state.jsonl"), os.path.join(run_dir, "driver.pid")
    if not os.path.isfile(logf):   # the monitor verb would create it empty; a look creates nothing
        return "NO-DATA", "run %s kept no state (%s does not exist), so there is no run to report idle" % (run, logf)
    rec = lh.read(heartbeat)
    try:
        hb_state, why = lh.classify(rec)
    except (TypeError, ValueError):   # a field of the wrong type (a list for the state) is a broken pulse, which is not alive
        hb_state, why = "ALARM", "the heartbeat has a field of the wrong type, so it cannot be judged"
    state = (rec or {}).get("state")
    dead = (hb_state == "ALARM" or state in lh.TERMINAL) and state != "PAUSED"
    cmd = [sys.executable, "-B", stage_log, "monitor", "--run", run, "--path", logf]
    n = None
    if dead:
        n, nwhy = _pool_count(pool)
        if n is None:
            return "NO-DATA", "the driver heartbeat says it is not alive (%s) but %s, so whether work is waiting is unknown" % (why[:100], nwhy)
        # The heartbeat is the authority here, so the monitor is handed a pid file known to be absent (driver_alive: False)
        # rather than driver.pid, which names a process the heartbeat has just called dead (wedged, or a reused pid).
        cmd += ["--driver-pid-file", pidf + ".absent", "--admissible", str(n)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
    try:
        cur = json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        cur = None
    if r.returncode not in (0, 2) or not isinstance(cur, dict):
        return "NO-DATA", "the run monitor gave no answer (exit %d): %s" % (r.returncode, (r.stdout + r.stderr).strip()[-120:])
    if cur.get("sequence") is None:
        return "NO-DATA", "run %s has no state row in %s, so there is no run to report idle" % (run, logf)
    if dead and n and cur.get("state") not in ("DONE", "FAILED"):
        wrote = cur.get("appended") == "idle_with_work"
        return "ACT", "idle: run %s, the driver is not alive (%s) and %d unit(s) are admissible; %s" % (
            run, why[:100], n, "idle_with_work row written" if wrote else "the run state already holds its report or a lease mark")
    return "HEALTHY", "run %s, driver %s, state %s" % (run, "not alive, no unit admissible or the run is over" if dead else "alive", cur.get("state"))


def main():
    if "--selftest" in sys.argv: return selftest()
    a = sys.argv[1:]
    opt = lambda n, d: a[a.index(n) + 1] if n in a and a.index(n) + 1 < len(a) else d
    if "--idle" in a:
        here = os.path.dirname(os.path.abspath(__file__))
        if here not in sys.path: sys.path.insert(0, here)
        import loop_heartbeat as _lh   # the one heartbeat reader; its HB path is the default pulse
        rd = opt("--run-dir", None) or newest_run_dir(os.environ.get("BROTHER_RUNS_ROOT") or os.path.expanduser("~/.claude/evidence/loop-runs"))
        w, line = idle_look(rd, opt("--heartbeat", _lh.HB), opt("--pool", os.path.expanduser("~/.claude/bin/runner_pool.py")), opt("--stage-log", None))
        # ONE line: a heartbeat reason, a run name or a tool's output with a line break (U+2028, U+0085, \n) must not start
        # a second line that a reader would take for another verdict
        print("%s %s" % (w, " ".join(line.split()))); return {"HEALTHY": 0, "ACT": 1}.get(w, 3)
    log = opt("--log", None)
    if not log:
        log = newest_driver_log(os.path.expanduser("~/.claude/evidence"))
    try:
        with open(log, encoding="utf-8") as f: text = f.read()
    except (OSError, TypeError):
        print("NO-DATA no readable driver log"); return 3
    alive = True
    try:
        os.kill(driver_pid(opt("--pid-file", None)), 0)
    except (OSError, ValueError, TypeError):
        alive = False
    seen = tuple(x for x in opt("--seen", "").split(",") if x)
    kw = dict(lanes=int(opt("--lanes", "6")), seen_kinds=seen, driver_alive=alive, base=base_rate())
    w, c, r = verdict(text, **kw)
    if "--explain" in a:
        print("LOG   %s" % log); print(explain(text, **kw))
    else:
        print("%s %s%s" % (w, (c + ": ") if c else "", r))
    return {"HEALTHY": 0, "ACT": 1}.get(w, 3)


DRIVER_LOG_RE = re.compile(r"^loop-until-(?:\d{8}-)?\d{4}\.log$")   # dated since 2026-09-26 (finding 13); HHMM before


def newest_driver_log(evidence):
    """The newest log the DRIVER wrote (loop_until.sh names it loop-until-HHMM.log), or None. A launcher's wrapper log
    (day-run.sh writes loop-until-day-HHMM.log, a few start lines and no PULSE) shares the driver's mtime to the second;
    on 2026-09-24 the watch read the wrapper and rang NO-DATA "no PULSE line yet" while the driver was at pass 6."""
    try:
        names = [n for n in os.listdir(evidence) if DRIVER_LOG_RE.match(n)]
    except OSError:   # sbe: allow-silent no readable log is None and main() prints NO-DATA no readable driver log
        return None
    paths = sorted((os.path.join(evidence, n) for n in names), key=os.path.getmtime)
    return paths[-1] if paths else None


def driver_pid(pid_file=None, owner=None):
    """The driver's pid: the named pid file when given, else the LOOP LEASE's holder, which the driver acquires at start.
    Until 2026-09-23 the default was a dated literal (loop-run-2026-09-22/driver.pid), so every run started from another
    folder read a dead pid and rang ACT driver while the driver ran (14:18, Phase 2). None when neither answers."""
    if pid_file:
        with open(os.path.expanduser(pid_file)) as f: return int(f.read().strip())
    if owner is None:
        import subprocess
        r = subprocess.run(["bash", os.path.join(os.path.dirname(os.path.abspath(__file__)), "loop_guard.sh"), "owner"], capture_output=True, text=True, timeout=30)
        owner = r.stdout if r.returncode == 0 else ""
    s = (owner or "").strip()
    return int(s) if s.isdigit() else None


def selftest():
    ok = "PULSE pass 5 | 0.3 h | landed 1 | grade ok 3/9 | probe ok 1/2 | checker LAND 0/0 (no answer 0) | parked 1 of 6 | running 5 | PRODUCING\nBUDGET  this run: spent 1.00 of 80.00 USD | remaining 79.00 USD | deadline 22:30\nREADY   1 to land\n"
    cases = [("healthy facts are HEALTHY", verdict(ok)[0] == "HEALTHY"),
             ("a terminal line is ACT terminal", verdict(ok + "ALARM DEADLINE: x\n")[1] == "terminal"),
             ("a dead driver with no terminal line is ACT driver", verdict(ok, driver_alive=False)[1] == "driver"),
             ("a new warning kind is ACT warning; a known or seen kind is not", verdict(ok + "WARN READY-NOT-LANDING: x\n")[1] == "warning" and verdict(ok + "WARN BEHIND-BASE-RATE: x\n")[0] == "HEALTHY" and verdict(ok + "WARN POOL-DRAINING: x\n", seen_kinds=("POOL-DRAINING",))[0] == "HEALTHY"),
             ("the control limit is the target when the base is unknown, the base minus two sigma when that is higher, never lower than the target",
              quality_limit(None, 40) == 0.25 and abs(quality_limit(0.6, 40) - (0.6 - 2 * math.sqrt(0.6 * 0.4 / 40))) < 1e-9 and quality_limit(0.126, 40) == 0.25 and quality_limit("x", 40) == 0.25),
             ("a run at 35 percent is ACT quality against a base rate of 60 percent and HEALTHY against no base",
              verdict(ok.replace("grade ok 3/9", "grade ok 14/40"), base=0.6)[1] == "quality" and verdict(ok.replace("grade ok 3/9", "grade ok 14/40"))[0] == "HEALTHY"),
             ("a grader pass rate under a quarter after forty rulings is ACT quality; the same rate under forty is not", verdict(ok.replace("grade ok 3/9", "grade ok 9/40"))[1] == "quality" and verdict(ok.replace("grade ok 3/9", "grade ok 9/39"))[0] == "HEALTHY"),
             ("budget under a tenth is ACT money", verdict(ok.replace("remaining 79.00", "remaining 7.00"))[1] == "money"),
             ("half the touched parked is ACT pool", verdict(ok.replace("parked 1 of 6", "parked 3 of 6"))[1] == "pool"),
             ("running under half the lanes after pass 3 is ACT pool", verdict(ok.replace("running 5", "running 2"))[1] == "pool"),
             ("no landing and no READY after the limit is ACT yield", verdict(ok.replace("landed 1", "landed 0").replace("0.3 h", "2.0 h").replace("READY   1", "READY   0"))[1] == "yield"),
             ("no landing but a READY build is not yield", verdict(ok.replace("landed 1", "landed 0").replace("0.3 h", "2.0 h"))[0] == "HEALTHY"),
             ("an empty log or no pulse is NO-DATA, never HEALTHY", verdict("")[0] == "NO-DATA" and verdict("LOOP UNTIL 22:30\n")[0] == "NO-DATA" and verdict(None)[0] == "NO-DATA")]
    import tempfile
    _pf = tempfile.NamedTemporaryFile("w", suffix=".pid", delete=False); _pf.write("4242\n"); _pf.close()
    cases += [("a named pid file wins over the lease", driver_pid(_pf.name, owner="999") == 4242),
              ("with no pid file the lease holder is the driver", driver_pid(None, owner="18354\n") == 18354),
              ("no lease holder is None, never a guessed pid", driver_pid(None, owner="") is None and driver_pid(None, owner="FREE") is None)]
    # THE ENTRY POINT under a throwaway HOME: the driver's log with a pulse, and a launcher's wrapper log written a
    # second later with none. main() must read the driver's.
    import shutil, subprocess
    home = tempfile.mkdtemp(prefix="loop-watch-"); evd = os.path.join(home, ".claude", "evidence"); os.makedirs(evd)
    try:
        drv, wrap = os.path.join(evd, "loop-until-2213.log"), os.path.join(evd, "loop-until-day-2213.log")
        with open(drv, "w") as f: f.write(ok)
        with open(wrap, "w") as f: f.write("LOOP UNTIL 05:00 | gap 120s | log %s\n" % drv)
        os.utime(drv, (1000, 1000)); os.utime(wrap, (1001, 1001))
        r = subprocess.run([sys.executable, "-B", os.path.abspath(__file__), "--lanes", "6", "--pid-file", _pf.name],
                           capture_output=True, text=True, timeout=60, env=dict(os.environ, HOME=home))
        picked = newest_driver_log(evd)
    finally:
        shutil.rmtree(home, ignore_errors=True)
    cases += [("main reads the driver's log, never a launcher's newer wrapper log with no pulse",
               picked == drv and "no PULSE" not in r.stdout and r.stdout.split(" ")[0] in ("HEALTHY", "ACT"))]
    # CAUSE AND FIX (2026-09-30): the real shape of the 14:32 stop, then one condition per cause class
    crash = (ok + 'Traceback (most recent call last):\n  File "/h/.claude/bin/land_batch.py", line 1143, in closing_pass\n'
             '    r = sh_(cmd)\n  File "/h/.local/share/uv/python/cpython-3.13/lib/python3.13/subprocess.py", line 556, in run\n'
             "    stdout = x\nsubprocess.TimeoutExpired: Command 'close_unit.py ACC2' timed out after 1800 seconds\n"
             "land_batch exit=1\nrunner pool NOT refilled: landing refused, tree needs a decision first\n"
             "ALARM UNPRODUCTIVE: a pass landed nothing\n")
    k, line = cause(crash); ex = explain(crash)
    cases += [("a crash names its exception and the innermost repository frame, skipping the standard library",
               k == "crash" and "TimeoutExpired" in line and "land_batch.py:1143 in closing_pass" in line),
              ("the terminal verdict carries the cause, and --explain adds the CAUSE and FIX lines",
               "cause crash" in verdict(crash)[2] and "\nCAUSE crash:" in ex and "\nFIX   fix the raising function" in ex),
              ("a start refusal is cause refused", cause(ok + "START REFUSED [tree]: 1 changed path(s)\n")[0] == "refused"),
              ("a refused landing with no traceback names the refusal line", cause(ok + "land_batch exit=1\n") == ("refused", "land_batch exit=1")),
              ("a held landing (nothing landed, exit 1) is not a refusal", cause(ok + "nothing landed | log x\nland_batch exit=1\n") == ("", "")),
              ("a refusal after a held pass still names the refusal", cause(ok + "nothing landed | log x\nland_batch exit=1\nREFUSED: gates red\nland_batch exit=1\n") == ("refused", "land_batch exit=1")),
              ("a spent runner budget is cause money", cause(ok + "FX-07 FX-07.3  skip: budget cap reached (0 new this pass)\n")[0] == "money"),
              ("a held unit is cause hold", cause(ok + "RL4   RL4.a    skip: RL4.a held: unit RL4 is execution stage S4\n")[0] == "hold"),
              ("a healthy log names no cause and adds no FIX line", cause(ok) == ("", "") and "FIX" not in explain(ok)),
              ("a dead driver carries the cause too", "cause crash" in verdict(crash.replace("ALARM UNPRODUCTIVE: a pass landed nothing\n", ""), driver_alive=False)[2])]
    bad = [n for n, good in cases if not good]
    print("selftest: %d cases, %s" % (len(cases), "OK" if not bad else "FAILED: " + ", ".join(bad))); return 1 if bad else 0


if __name__ == "__main__": sys.exit(main())
