#!/usr/bin/env python3
"""One line per pass that says whether the run is PRODUCING, and a warning the moment it is not. Never stops the run.

WHY (measured 2026-09-22). An unattended run made 40 passes over 2 h 48 min and landed nothing. Every signal needed
to see that in the first hour was already on disk: the checker had ruled 5 times and approved none, builds were
reaching READY and none landed, and sub units were being parked as EXHAUSTED one by one. Nothing put those numbers
side by side, so the first sign a human got was the final UNPRODUCTIVE alarm. loop_heartbeat.classify has a quiet
pass rule, but nothing calls it while a run is alive, and three quiet passes is twelve minutes while one build takes
longer than that, so it could only cry wolf.

usage: pass_pulse.py --start <epoch> --passes <n> [--log <driver log>]      (called by loop_until.sh after every pass)
       pass_pulse.py --selftest
Appends one row to ~/.claude/evidence/LOOP-PULSE.md, prints `PULSE ...` and any `WARN ...` lines, and rewrites
~/.claude/evidence/LOOP-WARN.txt (removed when there is nothing to warn about). Exit code is ALWAYS 0 for a pulse:
a reporter that can end the night is a second way for the night to end. What it cannot read it prints as NO-DATA and
never counts as healthy. Roots are overridable for tests: PULSE_EVIDENCE.
"""
import glob, json, os, re, subprocess, sys, time

GATE_MIN = 5          # decisions before an ADVISORY gate (checker, probe) that approved nothing is called out
# THE GRADER IS SAMPLED HARDEST FIRST (owner 2026-09-22, "this warning comes up a lot"): its first five rulings of a run are
# round 0 builds with no repair feedback, and it approved 46 percent over whole runs (44 of 96, 18 of 39) while ringing at
# pass 2 of every run on 0 of 5. Twenty rulings reach past round 0; each gate is named so the kinds are never confused.
GRADE_MIN = 20
GATE_KIND = {"checker": "CHECKER-NEVER-APPROVES", "probe": "PROBE-NEVER-APPROVES", "grade": "GRADER-NEVER-APPROVES"}
READY_MIN = 3         # builds that reached READY with nothing landed
PARKED_SHARE = 0.5    # share of touched sub units parked as EXHAUSTED or WITHHELD
LATE_HOURS = 2.0      # hours with no landing before the base rate comparison speaks
LOSS_MIN, LOSS_SHARE = 20, 0.25   # attempts before the top loss is called out, and the share that calls it
# WHERE THE RUN'S ATTEMPTS DIE, RANKED, EVERY PASS (owner 2026-09-27: find the causes of 80 percent of the problem
# yourself, with urgency). Measurement only: it blocks nothing. Each cause names who fixes it.
LOSS_ROUTE = {
    "time": "calls ran out of time: patience learns from it now; if it stays on top, the deadline or the forced effort is wrong",
    "run": "our own refusals (money, drain, stop): not the model; read the run's money and restarts",
    "model": "the model or its provider failed (no answer, bad format, substitute): the model picker moves seats away",
    "contract": "refused before any test ran: fix the contract or the screen at the source",
    "proof": "tests proved nothing: check whether the behaviour already exists, then diagnose",
    "suite": "suite red: diagnose the first failing test; spec repair if no build can meet it"}
CONFIG_ROUTE = ("a program does not know its model (CONFIG): no attempt was spent and the sub unit waits; run loop_intake.py "
                "prepare, whose reachability proof names the role, model, program and version, and remove or upgrade a stale pin")


def ev():
    return os.environ.get("PULSE_EVIDENCE") or os.path.expanduser("~/.claude/evidence")


def stage_counts(path, start):
    """{stage: [passed, failed, unknown]} for leave events at or after start; None when the file cannot be read."""
    out = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:   # sbe: allow-silent a line that is not JSON is skipped; the file is a log of many writers and one bad line must not blind the pulse
                    continue
                if not isinstance(r, dict) or r.get("event") != "leave":
                    continue
                at = r.get("at")
                if isinstance(at, str):
                    try:
                        at = time.mktime(time.strptime(at[:19], "%Y-%m-%dT%H:%M:%S"))
                    except ValueError:   # sbe: allow-silent an unparseable timestamp skips that event; it cannot be placed in this run's window
                        continue
                if not isinstance(at, (int, float)) or at < start:
                    continue
                row = out.setdefault(str(r.get("stage")), [0, 0, 0])
                row[0 if r.get("ok") is True else 1 if r.get("ok") is False else 2] += 1
    except OSError:   # sbe: allow-silent an unreadable stage file returns None, which the pulse prints as NO-DATA and never counts as healthy
        return None
    return out


def checker_counts(runs, start):
    """{'LAND': n, 'FIX': n, 'NO-DATA': n} over verdict files written at or after start."""
    out = {"LAND": 0, "FIX": 0, "NO-DATA": 0}
    for p in glob.glob(os.path.join(runs, "*", "round*", "out", "*.check.json")):
        try:
            if os.path.getmtime(p) < start:
                continue
            with open(p, encoding="utf-8") as f:
                v = json.load(f)
        except (OSError, ValueError):
            out["NO-DATA"] += 1
            continue
        k = v.get("verdict") if isinstance(v, dict) else None
        out[k if k in out else "NO-DATA"] += 1
    return out


def status_counts(runs, start):
    """First STATUS word of the NEWEST run of each sub unit touched at or after start: {'READY': n, 'EXHAUSTED': n, ...}."""
    newest = {}
    for d in glob.glob(os.path.join(runs, "*-*")):
        m = re.match(r"^(.+)-(\d{6})$", os.path.basename(d))
        if not m or not os.path.isdir(d):
            continue
        try:
            mt = os.path.getmtime(d)
        except OSError:   # sbe: allow-silent an unreadable run folder mtime skips that folder; it cannot be dated
            continue
        if mt >= start and (m.group(1) not in newest or mt > newest[m.group(1)][0]):
            newest[m.group(1)] = (mt, d)
    out = {}
    for _, d in newest.values():
        try:
            with open(os.path.join(d, "STATUS"), encoding="utf-8") as f:
                word = (f.read().split() or ["EMPTY"])[0]
        except OSError:
            word = "RUNNING"
        out[word] = out.get(word, 0) + 1
    return out


def landed_in_log(log):
    """Sub units named on the driver log's LANDED lines; None when the log cannot be read."""
    try:
        with open(log, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:   # sbe: allow-silent an unreadable driver log returns None, printed as NO-DATA
        return None
    subs = []
    for line in re.findall(r"^LANDED\s+([^|\n]*)\|", text, re.M):
        subs += [s for s in re.split(r"[,\s]+", line.strip()) if s]
    return subs


def base_rate(start, runner=subprocess.run):
    """Landing commits per hour over the 72 h before start, from git; None when git cannot say."""
    try:
        r = runner(["git", "log", "--since=@%d" % int(start - 72 * 3600), "--until=@%d" % int(start), "--format=%s"],
                   capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return len([s for s in r.stdout.splitlines() if re.search(r"\blands?:", s)]) / 72.0


def losses(runs, start):
    """{cause: attempts} over every build attempt in rounds touched at or after start: a failed call by who failed it
    (time, run, model), a graded build by its verdict (pass, or its refusal class). A build nobody graded (a sibling
    passed first, or the round was cut) is not a loss and is not counted."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # this directory's copy of both readers
    import unit_ledger, worker_mix, breaker
    out = {}
    for rd in glob.glob(os.path.join(runs, "*-*", "round*")):
        try:
            if os.path.getmtime(rd) < start: continue
            with open(os.path.join(rd, "results.json"), encoding="utf-8") as fh: recs = json.load(fh)
        except (OSError, ValueError):   # sbe: allow-silent a round with no readable results made no counted attempt
            continue
        for r in recs if isinstance(recs, list) else []:
            if not isinstance(r, dict) or not isinstance(r.get("id"), str): continue
            if not r.get("ok"):
                e = str(r.get("error") or "")
                # CONFIG FIRST (2026-09-30): a program that does not know its model is neither the model's loss nor ours to
                # retry. It is its own cause, reported beside the losses and left out of their denominator (loss_report);
                # the raw text is read too, so a record written before CONFIG_WAIT existed is classified the same way.
                cause = ("config" if r.get("config") is True or e.startswith("CONFIG_WAIT") or breaker.is_config(e)
                         else "run" if e.startswith(worker_mix.RUN_REFUSALS) else "time" if worker_mix.timed_out(e) else "model")
            else:
                try:
                    with open(os.path.join(rd, "grades", r["id"] + ".txt"), encoding="utf-8", errors="replace") as fh:
                        grade, why = unit_ledger.grade_of(fh.read())
                except OSError:   # sbe: allow-silent built but never graded: a sibling passed first or the round was cut
                    continue
                if grade not in ("PASS", "FAIL"): continue
                cause = "pass" if grade == "PASS" else unit_ledger.fail_class(why).lower()
            out[cause] = out.get(cause, 0) + 1
    return out


def loss_report(counts):
    """(one LOSSES line, a WARN line or None) for {cause: attempts}. Pure. The top loss is called out once LOSS_MIN
    attempts exist and it holds LOSS_SHARE of them."""
    config = counts.get("config", 0)
    counts = {k: v for k, v in counts.items() if k != "config"}   # CONFIG is never in the denominator: no attempt was spent
    n = sum(counts.values())
    held = (" | CONFIG %d call(s) held, not counted: %s" % (config, CONFIG_ROUTE)) if config else ""
    if not n:
        return ("LOSSES  NO-DATA: no build attempt in this run yet" + held), (("WARN CONFIG: " + CONFIG_ROUTE) if config else None)
    ranked = sorted(((v, k) for k, v in counts.items() if k != "pass"), reverse=True)
    parts = ["pass %d%%" % round(100.0 * counts.get("pass", 0) / n)] + ["%s %d%%" % (k, round(100.0 * v / n)) for v, k in ranked]
    line = "LOSSES  %d attempts: %s" % (n, " | ".join(parts)) + held
    if not ranked:
        return line, (("WARN CONFIG: " + CONFIG_ROUTE) if config else None)
    top_n, top = ranked[0]
    line += " | top: %s, %s" % (top, LOSS_ROUTE.get(top, "unknown cause"))
    warn = ("WARN TOP-LOSS: %s holds %d of %d attempts (%d%%): %s" % (top, top_n, n, round(100.0 * top_n / n), LOSS_ROUTE.get(top, "unknown cause"))
            if n >= LOSS_MIN and top_n >= LOSS_SHARE * n else None)
    if config and warn is None:
        warn = "WARN CONFIG: " + CONFIG_ROUTE
    return line, warn


def warnings(f):
    """The warning lines for one set of facts. Pure: every threshold is decided here and nowhere else."""
    out = []
    for name, row in (("checker", f.get("checker")), ("probe", f.get("probe")), ("grade", f.get("grade"))):
        if row is None:
            continue
        ok, decided = row
        if decided >= (GRADE_MIN if name == "grade" else GATE_MIN) and ok == 0:
            out.append("WARN %s: %s ruled %d times this run and approved 0. A gate that never says yes is broken or miscalibrated; look at it now." % (GATE_KIND[name], name, decided))
    ready, landed = f.get("ready_seen"), f.get("landed")
    if ready is not None and landed == 0 and ready >= READY_MIN:
        out.append("WARN READY-NOT-LANDING: %d build(s) reached READY this run and 0 landed. The break is between READY and the landing gates." % ready)
    parked, touched = f.get("parked"), f.get("touched")
    if parked is not None and touched and parked >= 2 and parked / float(touched) >= PARKED_SHARE:
        out.append("WARN POOL-DRAINING: %d of %d sub units touched this run are parked (EXHAUSTED or WITHHELD). The pool never retries them without a new fact, so the run ends when the rest park." % (parked, touched))
    hours, rate = f.get("hours"), f.get("base_rate")
    if landed == 0 and hours is not None and rate is not None and hours >= LATE_HOURS and rate * hours >= 1.0:
        out.append("WARN BEHIND-BASE-RATE: 0 landed in %.1f h; the 72 h before this run landed %.2f an hour, so about %d were expected by now." % (hours, rate, int(rate * hours)))
    return out


def gather(start, log, now=None):
    now = time.time() if now is None else now
    runs = os.path.join(ev(), "unit-runs")
    st = stage_counts(os.path.join(ev(), "brother-stages.jsonl"), start)
    ck = checker_counts(runs, start)
    sc = status_counts(runs, start)
    landed = landed_in_log(log) if log else None
    pair = lambda name: None if st is None else (st.get(name, [0, 0, 0])[0], st.get(name, [0, 0, 0])[0] + st.get(name, [0, 0, 0])[1])
    probe = pair("probe")
    return {"hours": (now - start) / 3600.0, "grade": pair("grade"), "probe": probe,
            "checker": (ck["LAND"], ck["LAND"] + ck["FIX"]), "checker_nodata": ck["NO-DATA"],
            "ready_seen": None if probe is None else probe[0], "landed": None if landed is None else len(landed),
            "parked": sc.get("EXHAUSTED", 0) + sc.get("WITHHELD", 0), "touched": sum(sc.values()),
            "running": sc.get("RUNNING", 0), "base_rate": base_rate(start)}


def show(pair):
    return "NO-DATA" if pair is None else "%d/%d" % pair


USD_PER_LANDING_CAP = float(os.environ.get("BROTHER_USD_PER_LANDING_CAP", "2.00"))   # the owner's bar, 2026-09-24
COST_MIN_LANDINGS = 3   # a ratio over fewer landings is noise


#: what the ratio is NOT (owner, 2026-10-03): this run's spend over this run's landings. A build landed in this run may have
#: been paid for in an earlier one (CV1.a and HP1.a read usd NO-DATA, built before the restart), and retries and rework
#: are not joined, so it is a partial run statistic; outcomes per dollar stay NO-DATA until those costs are joined.
PARTIAL = "partial: run spend over run landings, not cost per outcome"


def usd_per_landing(spent, landed):
    """(text, warning or None). NO-DATA until the driver hands a spend and at least one landing exists; a WARN once
    COST_MIN_LANDINGS landings average over the cap. H6 of the hardening plan: the metric the owner set is printed on
    every pulse, never derived after the night."""
    if spent is None or landed is None or landed <= 0:
        return "usd/landing NO-DATA", None
    per = spent / landed
    warn = None
    if landed >= COST_MIN_LANDINGS and per > USD_PER_LANDING_CAP:
        warn = ("WARN COST-PER-LANDING: %.2f USD over %d landing(s), above the %.2f cap the owner set (%s)"
                % (per, landed, USD_PER_LANDING_CAP, PARTIAL))
    return "usd/landing %.2f (%s)" % (per, PARTIAL), warn


BREAKER_OPEN_RE = re.compile(r"^BREAKER \S+ open\b")   # one open key: "BREAKER <key> open until HH:MM (<status>)" or a CONFIG hold


def breaker_lines(run_start, env=None):
    """(lines, warns) for the BREAKER part of the pulse (FX-11.7, REQ-FX11-22). Never raises: a reporter never ends the
    run. Off says off and reads nothing; an unknown switch value, an unreadable state, a bad argument or any failure
    inside is NO-DATA, never closed. Every open key and every quarantine alarm is also a WARN."""
    if isinstance(run_start, bool) or not isinstance(run_start, (int, float)) or run_start != run_start or run_start in (float("inf"), float("-inf")):
        return (["BREAKER NO-DATA: the run start is not a number"], [])
    if env is not None and not isinstance(env, dict):
        return (["BREAKER NO-DATA: the environment is not a mapping"], [])
    raw = (os.environ if env is None else env).get("BROTHER_BREAKER")
    if raw is not None and not isinstance(raw, str):
        return (["BREAKER NO-DATA: BROTHER_BREAKER is not text"], [])
    low = raw.strip().lower() if raw else "off"
    if low == "off":
        return (["BREAKER off"], [])
    if low != "on":
        return (["BREAKER NO-DATA: BROTHER_BREAKER=%s is not on or off; read as off" % " ".join(raw.split())[:60]], [])
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        if here not in sys.path:
            sys.path.insert(0, here)
        import breaker
    except ImportError as exc:   # no breaker beside the pulse: NO-DATA, never closed
        return (["BREAKER NO-DATA: %s" % type(exc).__name__], [])
    try:
        got = breaker.summary(run_start, env)[0]
        lines = [x for x in got if isinstance(x, str)] if isinstance(got, list) else []
    except Exception as exc:   # a reporter never raises: the breaker line says it could not be read
        return (["BREAKER NO-DATA: %s" % type(exc).__name__], [])
    if not lines:
        return (["BREAKER NO-DATA: the breaker summary said nothing"], [])
    warns = []
    for ln in lines:
        if ln.startswith("BREAKER ALARM: "):
            warns.append("WARN BREAKER-ALARM: " + ln[len("BREAKER ALARM: "):])
        elif BREAKER_OPEN_RE.match(ln):
            warns.append("WARN BREAKER-OPEN: " + ln[len("BREAKER "):] + "; only the role on that key moves to its next named model or waits")
    return (lines, warns)


def pulse(start, passes, log, spent=None):
    f = gather(start, log)
    warns = warnings(f)
    cost_text, cost_warn = usd_per_landing(spent, f["landed"])
    if cost_warn: warns = list(warns) + [cost_warn]
    line = ("PULSE pass %s | %.1f h | landed %s | grade ok %s | probe ok %s | checker LAND %s (no answer %d) | parked %d of %d | running %d | %s | %s"
            % (passes, f["hours"], "NO-DATA" if f["landed"] is None else f["landed"], show(f["grade"]), show(f["probe"]),
               show(f["checker"]), f["checker_nodata"], f["parked"], f["touched"], f["running"], cost_text,
               "PRODUCING" if f["landed"] and not cost_warn else ("%d WARNING(S)" % len(warns) if warns else "no landing yet")))
    print(line)
    try:
        loss_line, loss_warn = loss_report(losses(os.path.join(ev(), "unit-runs"), start))
    except Exception as exc:   # a reporter never ends the night: the ranking says it could not be read
        loss_line, loss_warn = "LOSSES  NO-DATA: %s" % type(exc).__name__, None
    print(loss_line)
    if loss_warn: warns = list(warns) + [loss_warn]
    try:
        brk_lines, brk_warns = breaker_lines(start)
    except Exception as exc:   # the breaker line never ends the pulse
        brk_lines, brk_warns = ["BREAKER NO-DATA: %s" % type(exc).__name__], []
    for b in brk_lines:
        print(b)
    if brk_warns: warns = list(warns) + list(brk_warns)
    for w in warns:
        print(w)
    try:
        md = os.path.join(ev(), "LOOP-PULSE.md")
        new = not os.path.exists(md)
        with open(md, "a", encoding="utf-8") as fh:
            if new:
                fh.write("# Loop pulse: one row per pass. A WARN row means look now; the run itself is never stopped by this file.\n\n")
            fh.write("- %s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), line))
            for b in brk_lines:
                fh.write("  - %s\n" % b)
            for w in warns:
                fh.write("  - %s\n" % w)
        wp = os.path.join(ev(), "LOOP-WARN.txt")
        if warns:
            with open(wp, "w", encoding="utf-8") as fh:
                fh.write("%s pass %s\n%s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), passes, "\n".join(warns)))
        elif os.path.exists(wp):
            os.remove(wp)
    except OSError as exc:
        print("PULSE NO-DATA: could not write the pulse files (%s)" % type(exc).__name__)
    return 0


def selftest():
    # A CASE THAT RAISES STILL ANSWERS THE QUESTION: a verdict line and exit 1, never a bare traceback, because the
    # pipeline reads the code and the human reads the line, and the two must say the same thing.
    try: return _selftest_body()
    except Exception as exc:
        print("selftest: FAILED before it could finish, %s: %s" % (type(exc).__name__, str(exc)[:120])); return 1


def _selftest_body():
    import tempfile
    healthy = {"hours": 3.0, "grade": (5, 12), "probe": (4, 9), "checker": (3, 6), "ready_seen": 4, "landed": 2, "parked": 1, "touched": 8, "base_rate": 0.8}
    def only(change, tag):   # ONE condition per fixture: the healthy facts with one thing wrong
        w = warnings(dict(healthy, **change)); return len(w) == 1 and tag in w[0]
    cases = [("healthy facts warn about nothing", warnings(healthy) == []),
             ("a checker that approved none of five is called out", only({"checker": (0, 5)}, "CHECKER-NEVER-APPROVES: checker")),
             ("a grader that approved none of nineteen is NOT called out: round 0 is the hardest sample", warnings(dict(healthy, grade=(0, 19))) == []),
             ("a grader that approved none of twenty is called out under its own name", only({"grade": (0, 20)}, "GRADER-NEVER-APPROVES: grade")),
             ("four decisions are too few to call", warnings(dict(healthy, checker=(0, 4))) == []),
             ("a probe stage that approved none is called out", only({"probe": (0, 6), "ready_seen": 0}, "PROBE-NEVER-APPROVES: probe")),
             ("READY builds with nothing landed is called out", only({"landed": 0, "hours": 0.5}, "READY-NOT-LANDING")),
             ("half the touched sub units parked is called out", only({"parked": 4}, "POOL-DRAINING")),
             ("one parked sub unit of two is not a drain", warnings(dict(healthy, parked=1, touched=2)) == []),
             ("no landing after two hours against a real base rate is called out", only({"landed": 0, "ready_seen": 0}, "BEHIND-BASE-RATE")),
             ("an unknown base rate stays silent rather than guessing", warnings(dict(healthy, landed=0, ready_seen=0, base_rate=None)) == []),
             ("unreadable stages are skipped, never read as approving", warnings(dict(healthy, grade=None, probe=None, ready_seen=None)) == [])]
    d = tempfile.mkdtemp(prefix="pass-pulse-"); runs = os.path.join(d, "unit-runs"); start = time.time() - 3 * 3600
    for i, (sub, word) in enumerate((("A.1", "EXHAUSTED after 5"), ("B.1", "EXHAUSTED after 5"), ("C.1", "READY /x"))):
        out = os.path.join(runs, "%s-0%d0000" % (sub, i + 1), "round0", "out"); os.makedirs(out)
        with open(os.path.join(runs, "%s-0%d0000" % (sub, i + 1), "STATUS"), "w") as fh: fh.write(word + "\n")
        for j in range(2):
            with open(os.path.join(out, "%s-r%d-build.json.check.json" % (sub, j)), "w") as fh: json.dump({"verdict": "FIX"}, fh)
    with open(os.path.join(d, "brother-stages.jsonl"), "w") as fh:
        for ok in (True, True, True, False):
            fh.write(json.dumps({"event": "leave", "stage": "probe", "ok": ok, "at": time.strftime("%Y-%m-%dT%H:%M:%S")}) + "\n")
        fh.write("not json\n")
    # WHERE ATTEMPTS DIED: A.1 one contract refusal and one stall; B.1 one pass and one run refusal; C.1 two stalls and
    # one build nobody graded (not a loss, not counted): 6 counted attempts, time on top at 3
    for sub, recs in (("A.1-010000", [("A.1-r0", True, "FAIL: safety screen: x.py imports y"), ("A.1-r1", False, "STALLED_AFTER_DEADLINE")]),
                      ("B.1-020000", [("B.1-r0", True, "PASS"), ("B.1-r1", False, "BudgetExceeded: over the cap")]),
                      ("C.1-030000", [("C.1-r0", False, "STALLED_AFTER_DEADLINE"), ("C.1-r1", False, "TimeoutExpired: x"), ("C.1-r2", True, None)])):
        rd = os.path.join(runs, sub, "round0"); os.makedirs(os.path.join(rd, "grades"), exist_ok=True)
        with open(os.path.join(rd, "results.json"), "w") as fh:
            json.dump([dict(id=i, ok=ok, **({} if ok else {"error": g})) for i, ok, g in recs], fh)
        for i, ok, g in recs:
            if ok and g:
                with open(os.path.join(rd, "grades", i + ".txt"), "w") as fh: fh.write(g + "\nexit=%d\n" % (0 if g == "PASS" else 1))
    log = os.path.join(d, "driver.log")
    with open(log, "w") as fh: fh.write("READY   1 to land\nLANDED   | units with every sub unit landed: none | log\n")
    # THE ENTRY POINT, as the driver runs it: a subprocess, the real argv, a temp evidence root.
    r = subprocess.run([sys.executable, "-B", os.path.abspath(__file__), "--start", str(int(start)), "--passes", "9", "--log", log],
                       capture_output=True, text=True, env=dict(os.environ, PULSE_EVIDENCE=d))
    warn_file = os.path.join(d, "LOOP-WARN.txt")
    cases += [("the entry point exits 0 even while warning", r.returncode == 0),
              ("it prints one PULSE line with the counts it read", "PULSE pass 9" in r.stdout and "checker LAND 0/6" in r.stdout and "probe ok 3/4" in r.stdout and "parked 2 of 3" in r.stdout and "landed 0" in r.stdout),
              ("it warns that the checker never approves", "CHECKER-NEVER-APPROVES: checker ruled 6" in r.stdout),
              ("it warns that READY builds are not landing", "READY-NOT-LANDING: 3 build" in r.stdout),
              ("it warns that the pool is draining", "POOL-DRAINING: 2 of 3" in r.stdout),
              ("the warning file and the pulse row are written", os.path.isfile(warn_file) and "pass 9" in open(os.path.join(d, "LOOP-PULSE.md")).read()),
              ("LOSSES counts every attempt by who killed it, and ranks time first", "LOSSES  6 attempts: pass 17% | time 50% | run 17% | contract 17% | top: time" in r.stdout),
              ("a build nobody graded is not a loss", losses(runs, start).get("pass") == 1 and sum(losses(runs, start).values()) == 6)]
    line, warn = loss_report({"pass": 15, "time": 5})
    cases += [("the top loss is called out at a quarter of twenty attempts", warn is not None and "WARN TOP-LOSS: time holds 5 of 20" in warn),
              ("under twenty attempts it stays quiet", loss_report({"pass": 14, "time": 5})[1] is None),
              ("under a quarter it stays quiet", loss_report({"pass": 16, "time": 4})[1] is None),
              ("passes alone rank nothing and warn nothing", loss_report({"pass": 30}) == ("LOSSES  30 attempts: pass 100%", None)),
              ("no attempt is NO-DATA, never zero percent", loss_report({})[0].startswith("LOSSES  NO-DATA"))]
    with open(log, "w") as fh: fh.write("LANDED  C.1 | units with every sub unit landed: none | log\n")
    for p in glob.glob(os.path.join(runs, "*", "round0", "out", "*.check.json")): os.remove(p)
    for sub in ("A.1-010000", "B.1-020000"):
        with open(os.path.join(runs, sub, "STATUS"), "w") as fh: fh.write("READY /x\n")
    r2 = subprocess.run([sys.executable, "-B", os.path.abspath(__file__), "--start", str(int(start)), "--passes", "10", "--log", log],
                        capture_output=True, text=True, env=dict(os.environ, PULSE_EVIDENCE=d))
    cases += [("H6: no spend or no landing prints usd/landing NO-DATA", usd_per_landing(None, 3)[0].endswith("NO-DATA") and usd_per_landing(4.0, 0)[0].endswith("NO-DATA") and usd_per_landing(4.0, None)[1] is None),
              ("H6: the ratio prints and warns only from three landings over the cap", usd_per_landing(9.0, 3) == ("usd/landing 3.00 (%s)" % PARTIAL, "WARN COST-PER-LANDING: 3.00 USD over 3 landing(s), above the %.2f cap the owner set (%s)" % (USD_PER_LANDING_CAP, PARTIAL))
               and usd_per_landing(9.0, 2)[1] is None and usd_per_landing(3.0, 3)[1] is None),
              ("H6: the entry point reads --spent and puts the figure on the line", "usd/landing 1.50" in subprocess.run([sys.executable, "-B", os.path.abspath(__file__), "--start", str(int(start)), "--passes", "11", "--log", log, "--spent", "1.5"],
                                                                                                                        capture_output=True, text=True, env=dict(os.environ, PULSE_EVIDENCE=d)).stdout)]
    cases += [("a run that landed says PRODUCING and clears the warning file", "PRODUCING" in r2.stdout and "WARN" not in r2.stdout and not os.path.exists(warn_file)),
              ("an unreadable log is NO-DATA, never zero", landed_in_log(os.path.join(d, "absent.log")) is None),
              ("an unreadable stage file is None, never empty", stage_counts(os.path.join(d, "absent.jsonl"), 0) is None)]
    rd = os.path.join(runs, "D.1-040000", "round0"); os.makedirs(rd)
    with open(os.path.join(rd, "results.json"), "w") as fh:
        json.dump([{"id": "D.1-r%d" % i, "ok": False, "error": "STALLED_AFTER_DEADLINE"} for i in range(14)], fh)
    r3 = subprocess.run([sys.executable, "-B", os.path.abspath(__file__), "--start", str(int(start)), "--passes", "12", "--log", log],
                        capture_output=True, text=True, env=dict(os.environ, PULSE_EVIDENCE=d))
    cases += [("the entry point prints the top loss warning once it holds a quarter of twenty attempts",
               "WARN TOP-LOSS: time holds 17 of 20 attempts (85%)" in r3.stdout)]
    bad = [n for n, good in cases if not good]
    print("selftest: %d cases, %s" % (len(cases), "OK" if not bad else "FAILED: " + ", ".join(bad)))
    return 1 if bad else 0


def main():
    if "--selftest" in sys.argv:
        return selftest()
    a = sys.argv[1:]
    def opt(name, default=""):
        return a[a.index(name) + 1] if name in a and a.index(name) + 1 < len(a) else default
    try:
        start = float(opt("--start", ""))
    except ValueError:
        print("PULSE NO-DATA: --start is not a number, so nothing can be counted for this run")
        return 0
    try:
        spent_opt = opt("--spent", "")
        try:
            spent = float(spent_opt) if spent_opt != "" else None
        except ValueError:
            spent = None   # an unreadable spend prints NO-DATA, never a number
        return pulse(start, opt("--passes", "?"), opt("--log"), spent)
    except Exception as exc:   # a reporter never ends the night: say what broke and let the run continue
        print("PULSE NO-DATA: the pulse itself failed (%s: %s)" % (type(exc).__name__, str(exc)[:120]))
        return 0


if __name__ == "__main__":
    sys.exit(main())
