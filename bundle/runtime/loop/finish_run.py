#!/usr/bin/env python3
"""The FINISHER: after the loop has exited, debug and fix what did not land and bring it to a quality landing.

Owner, 2026-09-22: the final check outside the loop "should also fix and debug to save as much of the work as
possible and have quality landing", and "It is a role too to define at intake". docs/plan/loop-roles.json names it
`finisher`; this is its tool. It runs ONLY when nothing of the loop is alive, and it never bypasses a landing gate.

usage (repo root): finish_run.py --dry                     what would happen to every unlanded build, nothing written
                   finish_run.py --model <name> [--max N]   one repair round per candidate with that model, then land
                   finish_run.py --selftest
Per unlanded sub unit, newest build that passed the grader (salvage.py's list):
  probes CLEAN and applies          -> READY (salvage.promote), landed by land_batch with every gate
  probes DIRTY, or STALE            -> a finisher brief: the build, the findings or the preflight reason, the real
                                       files; ONE answer from the finisher model; graded by grade_build; the cached
                                       probes re-executed by probe_build; CLEAN -> READY and landed; else recorded
  probes NO-DATA                    -> REGRADE: a fresh grade, then by BROTHER_FINISHER_REGRADE_PROBES: off (default,
                                       the owner's 2026-09-27 rule) promotes for the landing gates; crash re-executes
                                       the cached sets and holds an executed CRASH; strict promotes only a CLEAN
                                       re-execution. An unknown value is NOT SAVED.
Everything is bounded (one round, --max candidates, 1900 s per child command) and every outcome is printed as a
table. A child that hangs or cannot start is a failed grade or an unreadable probe set, never the end of the pass.
Fail direction: an unreadable input, a model that does not answer, a grade that is not PASS or a probe run that
prints no counts all read as NOT SAVED with the reason, never as READY. The finisher brief carries the build and the spec, which the
roles file classes as private content, so the router's wire check decides which finisher models may receive it.
"""
import glob, json, os, re, subprocess, sys, time
# EVERY ROW REACHES THE LOG AS IT IS DECIDED. Redirected to a file, Python buffers stdout until exit, so a 45 minute
# finisher showed a 0 byte log and read as hung (2026-09-22 17:4x). Line buffering makes the log the live record.
try:
    sys.stdout.reconfigure(line_buffering=True)
except (AttributeError, ValueError):
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)   # THIS file's own directory: the copies installed beside it
import salvage  # noqa: E402
import repair_advisor as RA  # noqa: E402
import land_batch  # noqa: E402  (the lander's own build_name: a repair must carry a name its gate reads)


def candidates_of(runs, plan, scope, preflight, mode):
    subs = salvage.all_subs(plan, scope) - salvage.landed_subs(plan)
    cands = salvage.plan_view(salvage.candidates(runs, subs), preflight, mode, runs)
    seen, out = set(), []
    for c in cands:   # newest per sub unit only; older ones are duplicates by construction
        if c["sub"] in seen or c["action"].startswith("leave: newest run") or "landing gates refused" in c["action"]:
            continue
        seen.add(c["sub"]); out.append(c)
    return out


REGRADE_PROBE_MODES = ("off", "crash", "strict")


def regrade_probe_mode(env=None):
    """BROTHER_FINISHER_REGRADE_PROBES as a mode: 'off' when unset or empty (today's rule), the stripped lower cased word
    when it is one of REGRADE_PROBE_MODES, None for anything else, which the caller refuses (never read as a mode)."""
    v = (os.environ if env is None else env).get("BROTHER_FINISHER_REGRADE_PROBES", "").strip().lower()
    if not v: return "off"
    return v if v in REGRADE_PROBE_MODES else None


def run_cmd(argv, cwd, timeout=1900):
    """The finisher's one child runner: (exit code, stdout + stderr). A child that hangs past the timeout is (124, TIMEOUT)
    and one that cannot start is (127, NO-DATA), so the grade and probe rules read either as a failure and the pass goes
    on (FX-43: before, the exception escaped and one hung probe ended the whole pass with no table and no landing).
    subprocess.run kills the direct child on timeout; a grandchild in its own session is out of scope, as before."""
    try:
        r = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, "TIMEOUT after %s s: %s" % (timeout, os.path.basename(argv[1] if len(argv) > 1 else argv[0]))
    except OSError as exc:
        return 127, "NO-DATA: could not start %s: %s" % (argv[0], type(exc).__name__)
    return r.returncode, r.stdout + r.stderr


def plan_for(c):
    """The finisher's decision for one candidate: PROMOTE, REGRADE, REPAIR or LEAVE with the reason."""
    if c["action"] == "PROMOTE": return "PROMOTE", "clean and applies"
    if c["probes"] == "DIRTY" and c["applies"] == "APPLIES": return "REPAIR", "probes found defects"
    if c["applies"].startswith("STALE"): return "REPAIR", c["applies"]
    # ONE PROBE RULE WITH THE RUNNER (2026-09-27): a silent probe does not block; a fresh grade, then the landing gates decide.
    # BROTHER_FINISHER_REGRADE_PROBES (FX-43, default off keeps that rule) may re-execute the cached sets first, in finish()
    if c["probes"] == "NO-DATA" and c["applies"] == "APPLIES": return "REGRADE", "the probe stage never answered"
    return "LEAVE", c["action"]


def findings_of(c):
    rd = os.path.dirname(os.path.dirname(c["build"]))
    try:
        with open(os.path.join(rd, "probes", "findings", c["sub"] + ".txt"), encoding="utf-8") as f: return f.read()[:6000]
    except OSError:
        return ""


def prebrief_of(build_path):
    """The advisor's brief cached by the runner when the sub unit parked (<run>/FINISHER-BRIEF.md), or '' when absent
    or unreadable: the finisher then asks the advisor itself, as before."""
    try:
        run = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(build_path))))
        with open(os.path.join(run, "FINISHER-BRIEF.md"), encoding="utf-8") as fh: return fh.read().strip()
    except OSError: return ""


def brief(c, why, spec_text, files):
    return ("House laws: unknown, corrupt or missing input is REFUSED, never the safe case. No person or client names. No long dashes.\n\n"
            "You are the FINISHER for sub unit %s. A worker build passed the sandbox grader and then did NOT land. Reason: %s.\n"
            "Return ONE complete JSON build (edits, tests as {path, find, replace} or {path, new_file_content}, done_check, mutations of 4 or more, unknowns) that is THIS build "
            "with the smallest change that answers the reason. Change nothing else. Every find must occur exactly once in the file as shown.\n\n"
            "THE REASON IN DETAIL:\n%s\n\nTHE SPECIFICATION:\n%s\n\nTHE BUILD:\n%s\n\nTHE FILES AS THEY ARE NOW:\n%s\n"
            % (c["sub"], why, findings_of(c) or why, spec_text[:40000], open(c["build"], encoding="utf-8").read()[:60000], files[:60000]))


def files_of(build_json):
    out = []
    for e in (build_json.get("edits") or []) + (build_json.get("tests") or []):
        p = e.get("path")
        if p and os.path.isfile(p) and p not in [o[0] for o in out]:
            with open(p, encoding="utf-8", errors="replace") as f: out.append((p, f.read()))
    return "\n".join("=== %s\n%s" % (p, t[:20000]) for p, t in out)


def grade(build_path, run):
    rc, out = run([sys.executable, "-B", os.path.join(HERE, "grade_build.py"), build_path], None)
    last = (out.strip().splitlines() or ["no output"])[-1]
    return bool(re.match(r"^PASS\b", last)) and rc == 0, last[:120]


class _NoAnswer(object):
    """FX-11.5: an attempt no model answered. finish reads ok, answer and detail, so the row is NOT SAVED with detail."""
    def __init__(self, model, detail):
        self.model, self.ok, self.answer, self.detail, self.seconds, self.status = model, False, "", detail, 0.0, "REFUSED_BY_GATE"


def finisher_attempt(m, prompt, mc, env=None):
    """FX-11.5 (REQ-FX11-13): one finisher call. mc is the model_call module (a fake in the tests).

    BROTHER_BREAKER off: today's single call_one on the model given, and the roles file is not read. On: the finisher's
    role chain (the model given first, then the roles file's named members) walked by mc.call; a chain the roles file
    refuses calls nothing and reads `roles: <reason>`; no member answering reads `capacity: <reason>`, so the row is
    NOT SAVED with the reason and the engine can pause and resume."""
    if not isinstance(m, str) or not m.strip():
        raise ValueError("finisher_attempt wants a model name")
    if not isinstance(prompt, str):
        raise ValueError("finisher_attempt wants the brief as text")
    if mc is None or isinstance(mc, (str, bytes, bool, int, float)):
        raise ValueError("finisher_attempt wants the model_call module")
    if env is not None and not isinstance(env, dict):
        raise ValueError("finisher_attempt wants an environment mapping or None")
    import loop_roles
    if not loop_roles._breaker_on(dict(os.environ) if env is None else env):
        return mc.call_one(m, prompt, "build", "public", timeout=900)
    chain, why = loop_roles.call_chain("finisher", choice=m, env=env)
    if not chain:
        return _NoAnswer(m, why)
    a = mc.call(prompt, "build", "public", timeout=900, expect="json", chain=chain)
    if a is not None and a.ok:
        return a
    detail = str(a.detail) if a is not None else "no attempt came back"
    if not detail.startswith("capacity:"):
        detail = "capacity: no member of %s answered (%s)" % ("/".join(chain), detail[:300])
    return _NoAnswer(a.model if a is not None else m, detail)


def reexecute(sub, build_path, spec_path, run, cache_root=None, wave=True):
    """Re-execute the cached probe set for this sub unit against a build: (CLEAN|DIRTY|NO-DATA, detail). wave=False (the
    REGRADE path) never starts probe_wave on an empty cache: no paid call and no write to the shared cache."""
    root = cache_root or os.environ.get("BROTHER_PROBE_CACHE") or os.path.expanduser("~/.claude/evidence/probe-cache")
    probes = sorted(glob.glob(os.path.join(root, sub, "*", "*.json")))
    if not probes and not wave:
        return "NO-DATA", "no cached probe set for %s; the regrade does not write one" % sub
    if not probes:
        # THE FINISHER CAN PROBE (owner 2026-09-22 23:2x, row 4): 7 of 12 candidates were lost this afternoon to an empty cache.
        # probe_wave writes the probe set for this sub unit into the cache on its first run; then the re-execution proceeds.
        rd = os.path.dirname(os.path.dirname(build_path))
        run([sys.executable, os.path.join(HERE, "probe_wave.py"), rd, os.path.join(rd, "probes-finisher"), "^%s$" % re.escape(sub)], None)
        probes = sorted(glob.glob(os.path.join(root, sub, "*", "*.json")))
    if not probes: return "NO-DATA", "no cached probe set for %s, and probe_wave wrote none" % sub
    # AN OBSERVED CRASH ALWAYS WINS (finding B3, 2026-09-27): probe_build exits 1 exactly when it saw a crash, and this
    # loop kept a set's result only at exit 0, so one clean set masked another set's crash and the build went READY.
    # A finding line is read whatever the exit code; a set with no finding counts as clean only at exit 0 with its
    # counts line, and any other answer is unreadable, which makes the whole verdict NO-DATA, never CLEAN (a set that
    # saw a crash is unreadable too, and DIRTY is answered before NO-DATA).
    ran, dirt, unread = 0, [], []
    for p in probes:
        rc, out = run([sys.executable, os.path.join(HERE, "probe_build.py"), build_path, p], None)
        m = re.search(r"^PROBES\s+(\d+) run: (\d+) CRASH, (\d+) WRONG-ACCEPT\?", out, re.M)
        # ONE PROBE RULE WITH THE RUNNER (2026-09-27): only an executed CRASH is a defect; a WRONG-ACCEPT? line is the red
        # team's reading of the spec, an advisory note, exactly as unit_runner.probe_outcome decides
        found = [l[:160] for l in out.splitlines() if l.startswith("CRASH")]
        dirt += found
        if m and rc == 0: ran += int(m.group(1))
        else: unread.append("%s exit %s" % (os.path.relpath(p, root), rc))
    if dirt: return "DIRTY", "%d finding(s): %s" % (len(dirt), dirt[0])
    if unread: return "NO-DATA", "%d probe set(s) gave no readable result: %s" % (len(unread), ", ".join(unread[:3]))
    return ("CLEAN", "%d probe(s) ran, none dirty" % ran) if ran else ("NO-DATA", "no cached probe ran against this build")


def repair_path(sub, build_path):
    """Where the finisher writes its repair of a build: <run>/finisher/out/<land_batch.build_name(sub)>. The lander reads
    the run's STATUS three levels above a build, as for a worker build, and only a name build_name writes (finding B8,
    2026-09-27: <sub>-finisher-build.json was held at the gate every time). Outside every round*/out folder, so no grader
    lane or runner pool glob takes the repair for a worker variant of that round."""
    run = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(build_path))))
    return os.path.join(run, "finisher", "out", land_batch.build_name(sub))


def finish(cands, model, call, run, spec_of, max_n=20, land=None, promote=None, now=None):
    """The whole pass over the candidates. Every side effect is injected; returns the rows of the table."""
    rows, ready = [], []
    for c in cands[:max_n]:
        what, why = plan_for(c)
        if what in ("PROMOTE", "REGRADE"):
            # NO HISTORICAL PASS IS TRUSTED (2026-09-27): a promotion regrades the build on the current tree first
            ok, last = grade(c["build"], run)
            if not ok: rows.append((c["sub"], "NOT SAVED", "a fresh grade failed: %s" % last)); continue
            if what == "REGRADE":
                # FX-43: the switch decides whether the cached probe sets run before a REGRADE is promoted (default off)
                mode = regrade_probe_mode()
                if mode is None:
                    rows.append((c["sub"], "NOT SAVED", "NO-DATA: BROTHER_FINISHER_REGRADE_PROBES=%s is not off, crash or strict"
                                 % os.environ.get("BROTHER_FINISHER_REGRADE_PROBES", "")[:20])); continue
                if mode != "off":
                    verdict, detail = reexecute(c["sub"], c["build"], spec_of.get(c["sub"], ""), run, wave=False)
                    if verdict == "DIRTY":
                        rows.append((c["sub"], "NOT SAVED", "probes after the regrade: DIRTY, %s" % detail)); continue
                    if verdict not in ("CLEAN", "NO-DATA"):
                        rows.append((c["sub"], "NOT SAVED", "NO-DATA: the probe re-execution answered %s, not CLEAN, DIRTY or NO-DATA"
                                     % str(verdict)[:20])); continue
                    if verdict == "NO-DATA" and mode == "strict":
                        rows.append((c["sub"], "NOT SAVED", "probes after the regrade: NO-DATA, %s" % detail)); continue
                    if verdict == "NO-DATA":
                        why = "%s, then %s: no answer, so the landing gates decide (owner 2026-09-27)" % (why, detail)
                    else:
                        why = "%s, then %s" % (why, detail)
            refused = promote(c)
            if refused: rows.append((c["sub"], "NOT SAVED", "promotion refused: %s" % refused)); continue
            ready.append(c["build"]); rows.append((c["sub"], "READY", why)); continue
        if what == "LEAVE":
            rows.append((c["sub"], "NOT SAVED", why)); continue
        spec_path = spec_of.get(c["sub"], "")
        if what == "REPAIR":
            try:
                b = json.load(open(c["build"], encoding="utf-8")); spec_text = open(spec_path, encoding="utf-8").read()
            except (OSError, ValueError) as exc:
                rows.append((c["sub"], "NOT SAVED", "unreadable build or spec (%s)" % type(exc).__name__)); continue
            text = brief(c, why, spec_text, files_of(b))
            # THE REPAIR ADVISOR HEADS THE FINISHER'S REPAIR BRIEF TOO (owner 2026-09-22 23:2x): three edits from a Claude model at
            # high, DeepSeek makes them; a candidate already passed the grader once, so the targeted repair has the best prior here.
            _adv = prebrief_of(c["build"])   # the runner's park time pre-brief first (owner 2026-09-23), the live advisor otherwise
            if _adv: print("PREBRIEF %s: cached advice used" % c["sub"], flush=True)
            elif RA.enabled(c["sub"]):
                _adv = RA.advise(why, json.dumps(b)[:20000], spec_text[:8000])
            if _adv: text = _adv + "\n" + text
            try:
                a = call(model, text)
            except Exception as exc:   # a refusal at the wire, or a transport that raises, is a row, never a crash of the whole pass
                rows.append((c["sub"], "NOT SAVED", "refused at the wire: %s" % str(exc)[:100])); continue
            if not a.ok or not a.answer.strip():
                rows.append((c["sub"], "NOT SAVED", "no answer from %s: %s" % (model, (a.detail or "")[:80]))); continue
            out = repair_path(c["sub"], c["build"])
            try:
                json.loads(a.answer)
                os.makedirs(os.path.dirname(out), exist_ok=True)
                with open(out, "w", encoding="utf-8") as f: f.write(a.answer)
            except ValueError:
                rows.append((c["sub"], "NOT SAVED", "%s did not return a JSON build" % model)); continue
            ok, last = grade(out, run)
            if not ok: rows.append((c["sub"], "NOT SAVED", "repair failed the grader: %s" % last)); continue
            target = out
        else:
            target = c["build"]
        verdict, detail = reexecute(c["sub"], target, spec_path, run)
        if verdict == "DIRTY":
            rows.append((c["sub"], "NOT SAVED", "probes after the %s: %s, %s" % (what.lower(), verdict, detail))); continue
        # A REFUSED PROMOTION IS NOT SAVED (2026-09-27): salvage.promote returns the reason it left STATUS alone (a landing's
        # word won, or a newer run speaks for the sub unit), and a build it did not mark READY is never handed to the lander.
        refused = promote(dict(c, build=target, checker="finisher"))
        if refused: rows.append((c["sub"], "NOT SAVED", "promotion refused: %s" % refused)); continue
        ready.append(target); rows.append((c["sub"], "READY", "%s, then %s" % (why, detail)))
    landed = land(ready) if ready and land else ""
    return rows, ready, landed


def main():
    if "--selftest" in sys.argv: return selftest()
    a = sys.argv[1:]; dry = "--dry" in a
    if not dry:
        import loop_hold as _LH; _LH.gate(where="finish_run")   # the hold reaches this paid route too; --dry buys nothing
    opt = lambda n, d=None: a[a.index(n) + 1] if n in a and a.index(n) + 1 < len(a) else d
    if subprocess.run(["bash", os.path.join(HERE, "stop_loop.sh"), "--dry"], capture_output=True).returncode != 0:
        print("FINISH REFUSED: something of the loop is alive; the finisher runs only after the loop has exited"); return 1
    try:
        plan = json.load(open(salvage.PLAN, encoding="utf-8")); import grade_build, check_wave
    except Exception as exc:
        print("FINISH NO-DATA: plan or tools unreadable (%s)" % type(exc).__name__); return 3
    spec_of = {s: u["spec"] for u in plan["units"] for s in (u.get("sub_units") or []) if u.get("spec")}
    cands = candidates_of(salvage.runs_dir(), plan, os.environ.get("BROTHER_SCOPE"), grade_build.preflight, check_wave.checker_mode())
    if dry or not opt("--model"):
        print("FINISHER DRY: %d candidate(s)" % len(cands))
        for c in cands: print("  %-8s %-8s %s" % ((c["sub"],) + plan_for(c)))
        if not opt("--model"): print("give --model <finisher model from the intake> to act"); return 0
        return 0
    import model_call
    # THE BRIEF IS PUBLIC AFTER THE SCREEN, like the worker and checker briefs (same spec, same build, same real files the
    # workers already receive): a brief carrying a private term is refused here and never sent.
    def call(m, prompt):
        if grade_build.private_hits(prompt): raise RuntimeError("brief withheld: it carries a private term")
        return finisher_attempt(m, prompt, model_call)   # FX-11.5: today's call under BROTHER_BREAKER off, the role chain under on
    run = run_cmd   # bounded: a hung or unstartable child is a row, never the end of the pass (FX-43)
    land_rc = []
    def land(builds):
        r = subprocess.run([sys.executable, os.path.join(HERE, "land_batch.py")] + builds, capture_output=True, text=True, timeout=3600)
        land_rc.append(r.returncode)
        return r.stdout.strip().splitlines()[-1] if r.stdout.strip() else "land_batch printed nothing"
    rows, ready, landed = finish(cands, opt("--model"), call, run, spec_of, int(opt("--max", "20")), land=land, promote=salvage.promote)
    for r in rows: print("  %-8s %-9s %s" % r)
    print("FINISHER: %d candidate(s), %d READY, landing: %s" % (len(rows), len(ready), landed or "nothing to land"))
    # A REFUSED LANDING IS NOT A FINISHED PASS (finding B7, 2026-09-27): this returned 0 after land_batch refused, so a
    # caller reading the exit code saw promoted builds as landed. land_batch's own non-zero code is passed through.
    return next((c for c in land_rc if c), 0)


def selftest():
    try: return _selftest_body()
    except Exception as exc:
        print("selftest: FAILED before it could finish, %s: %s" % (type(exc).__name__, str(exc)[:120])); return 1


def _selftest_body():
    import tempfile
    d = tempfile.mkdtemp(prefix="finish-"); spec = os.path.join(d, "U.md"); open(spec, "w").write("# U\n")
    def cand(sub, probes, applies="APPLIES", action=None):
        rd = os.path.join(d, sub + "-010000", "round0"); os.makedirs(os.path.join(rd, "out"), exist_ok=True); os.makedirs(os.path.join(rd, "probes", "findings"), exist_ok=True)
        b = os.path.join(rd, "out", sub + "-r0-build.json"); json.dump({"edits": []}, open(b, "w"))
        if probes == "DIRTY": open(os.path.join(rd, "probes", "findings", sub + ".txt"), "w").write("CRASH x TypeError\n")
        return {"sub": sub, "build": b, "run": os.path.dirname(rd), "probes": probes, "checker": "none", "applies": applies, "action": action or ("PROMOTE" if probes == "CLEAN" and applies == "APPLIES" else "leave: probes " + probes)}
    cache = os.path.join(d, "cache"); os.makedirs(os.path.join(cache, "D", "k")); json.dump({"probe_script": "x"}, open(os.path.join(cache, "D", "k", "deepseek.json"), "w"))
    os.environ["BROTHER_PROBE_CACHE"] = cache
    class A:
        def __init__(s, ok, ans, det=""): s.ok, s.answer, s.detail = ok, ans, det
    good_call = lambda m, p: A(True, json.dumps({"edits": [], "tests": [], "mutations": []}))
    def runner(answers):
        def run(argv, cwd):
            for key, (rc, out) in answers.items():
                if key in " ".join(argv): return rc, out
            return 0, ""
        return run
    clean_answers = {"grade_build": (0, "PASS x"), "probe_build": (0, "PROBES   5 run: 0 CRASH, 0 WRONG-ACCEPT?, 1 REFUSED, 4 RETURNED, 0 NO-DATA")}
    clean = runner(clean_answers)
    promoted = []; landed = []
    fin = lambda cands, call=good_call, run=clean, **kw: finish(cands, "m", call, run, {"D": spec, "C": spec, "S": spec, "N": spec, "L": spec}, promote=lambda c: promoted.append(c["sub"]), land=lambda b: landed.append(b) or "LANDED x", **kw)
    def moded(value, fn):   # BROTHER_FINISHER_REGRADE_PROBES set (or unset, None) for one case, the caller's value restored
        old = os.environ.pop("BROTHER_FINISHER_REGRADE_PROBES", None)
        if value is not None: os.environ["BROTHER_FINISHER_REGRADE_PROBES"] = value
        try: return fn()
        finally:
            os.environ.pop("BROTHER_FINISHER_REGRADE_PROBES", None)
            if old is not None: os.environ["BROTHER_FINISHER_REGRADE_PROBES"] = old
    seen = []
    def seeing(answers):
        r = runner(answers)
        return lambda argv, cwd: seen.append(" ".join(argv)) or r(argv, cwd)
    crashed = {"grade_build": (0, "PASS x"), "probe_build": (1, "CRASH y TypeError\nPROBES   1 run: 1 CRASH, 0 WRONG-ACCEPT?")}
    rows, ready, ld = fin([cand("C", "CLEAN")])
    cases = [("a clean build that applies is promoted and landed", rows[0][1] == "READY" and promoted == ["C"] and landed and ld == "LANDED x"),
             ("a dirty build gets one repair, is graded, reprobed with the cached set, and lands when clean", fin([cand("D", "DIRTY")])[0][0][1] == "READY"),
             ("a repair the grader refuses is NOT SAVED with the grader's line", "repair failed the grader" in fin([cand("D", "DIRTY")], run=runner({"grade_build": (1, "FAIL tests pass without the code")}))[0][0][2]),
             ("a repair that is still dirty on the cached probes is NOT SAVED", "DIRTY" in fin([cand("D", "DIRTY")], run=runner({"grade_build": (0, "PASS x"), "probe_build": (0, "CRASH y TypeError\nPROBES   3 run: 1 CRASH, 0 WRONG-ACCEPT?, 0 REFUSED, 2 RETURNED, 0 NO-DATA")}))[0][0][2]),
             ("a model that does not answer is NOT SAVED with its reason, never READY", "no answer from m: timeout" in fin([cand("D", "DIRTY")], call=lambda m, p: A(False, "", "timeout"))[0][0][2]),
             ("an empty answer at exit zero is NOT SAVED too", "no answer from" in fin([cand("D", "DIRTY")], call=lambda m, p: A(True, "  "))[0][0][2]),
             ("owner 2026-09-27, the default with the switch unset: a candidate whose probes never answered is regraded and handed to the landing gates, never re-probed", moded(None, lambda: fin([cand("D", "NO-DATA")], run=runner({"grade_build": (0, "PASS x"), "probe_build": (1, "PROBES   5 run: 0 CRASH, 0 WRONG-ACCEPT?")})))[0][0][1] == "READY"),
             ("a candidate whose fresh grade fails is NOT SAVED, whatever its old PASS said", "a fresh grade failed" in fin([cand("D", "NO-DATA")], run=runner({"grade_build": (1, "FAIL tests pass without the code")}))[0][0][2]),
             ("a call that raises (refused at the wire) is a NOT SAVED row, never a crash", "refused at the wire" in fin([cand("D", "DIRTY")], call=lambda m, p: (_ for _ in ()).throw(RuntimeError("deepseek may receive public at most")))[0][0][2]),
             ("a model that returns prose instead of a build is NOT SAVED", "did not return a JSON build" in fin([cand("D", "DIRTY")], call=lambda m, p: A(True, "sure, here is"))[0][0][2]),
             ("under crash, a NO-DATA probe build is re-executed with the cached set and lands when clean", moded("crash", lambda: fin([cand("D", "NO-DATA")], run=seeing(clean_answers)))[0][0][1] == "READY" and any("probe_build" in s for s in seen)),
             ("under crash, a regrade whose cached set executed a crash is NOT SAVED", "probes after the regrade: DIRTY" in moded("crash", lambda: fin([cand("D", "NO-DATA")], run=runner(crashed)))[0][0][2]),
             ("under strict, a regrade whose cached set never answered is NOT SAVED", "probes after the regrade: NO-DATA" in moded("strict", lambda: fin([cand("D", "NO-DATA")], run=runner({"grade_build": (0, "PASS x"), "probe_build": (0, "no counts")})))[0][0][2]),
             ("an unknown switch value is NOT SAVED and names the value", "BROTHER_FINISHER_REGRADE_PROBES=banana" in moded("banana", lambda: fin([cand("D", "NO-DATA")]))[0][0][2]),
             ("a red team reading alone (WRONG-ACCEPT?) is not a defect in the finisher either", reexecute("D", "/x/b.json", "", runner({"probe_build": (0, "WRONG-ACCEPT? f x RETURNED 1\nPROBES   3 run: 0 CRASH, 1 WRONG-ACCEPT?")}), cache_root=cache)[0] != "DIRTY"),
             ("a stale build is repaired against the current files", plan_for(cand("S", "CLEAN", applies="STALE: edit 1 find text not in target", action="leave: does not apply"))[0] == "REPAIR"),
             ("a build the gate holds is left", plan_for(cand("L", "CLEAN", action="leave: checker is a gate and said FIX"))[0] == "LEAVE"),
             ("--max bounds the work", len(fin([cand("C", "CLEAN"), cand("D", "DIRTY")], max_n=1)[0]) == 1),
             ("the finisher brief carries the reason, the spec and the build", "FINISHER for sub unit D" in brief(cand("D", "DIRTY"), "probes found defects", "# U", "") and "CRASH x TypeError" in brief(cand("D", "DIRTY"), "w", "# U", ""))]

    import tempfile as _tf; _pd = _tf.mkdtemp(prefix="fr-pre-"); _pb = os.path.join(_pd, "D1.1-000001", "round2", "out", "D1.1-r0-build.json"); os.makedirs(os.path.dirname(_pb))
    open(_pb, "w").write("{}"); open(os.path.join(_pd, "D1.1-000001", "FINISHER-BRIEF.md"), "w").write("1. do x\n")
    cases += [("a park time pre-brief beside the run is read for its build", prebrief_of(_pb) == "1. do x"),
              ("no pre-brief is an empty string, never a guess", prebrief_of(os.path.join(_pd, "none", "round0", "out", "b.json")) == "")]
    me = os.path.abspath(__file__)
    r = subprocess.run([sys.executable, "-B", me, "--dry"], capture_output=True, text=True, cwd=os.path.dirname(os.path.dirname(HERE)))
    want = {"FINISHER DRY": 0, "FINISH REFUSED": 1, "FINISH NO-DATA": 3}   # an export copy has no plan: NO-DATA there is the right answer
    cases += [("the entry point --dry answers one of its three verdicts with the matching exit code and writes nothing", any(r.stdout.startswith(k) and r.returncode == v for k, v in want.items()))]
    bad = [n for n, good in cases if not good]
    print("selftest: %d cases, %s" % (len(cases), "OK" if not bad else "FAILED: " + ", ".join(bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
