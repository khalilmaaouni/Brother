#!/usr/bin/env python3
"""The CHECKER stage: one independent read of every READY build BEFORE the landing tool may see it.
usage (repo root of the launch worktree): BROTHER_CHECKER=opus check_wave.py <build.json ...>      check_wave.py --selftest

Owner order 2026-09-22 for the 1.1.0 overnight run: workers build (DeepSeek), a checker reads (Opus), the orchestrator
decides. Until this file the pass handed every READY build straight to land_batch, so "checker" was a word with no stage.

Per build: skip when a verdict about these EXACT bytes already exists and is LAND or FIX (a NO-DATA is retried, because
no answer is not an answer). Else assemble the red team brief (review_brief.py: the whole unit spec plus the build JSON),
call the named model ONCE through model_call.call_one (no failover on purpose: an answer from a model nobody named is not
this checker's verdict), take the LAST "VERDICT:" line, and write <build>.check.json {model, sha256, verdict, seconds,
reasons} with the full text beside it. FIX FIRST also rewrites the run's STATUS from READY to the QUARANTINE shape
land_batch.py uses, so the sub unit is offered to a fresh runner round CARRYING the checker's numbered fixes (unit_runner
previous_reason() reads that word) and is never re-offered unchanged.

A checker can only VETO. It never lands anything: the sandbox grader, the executed probes and the gates still decide PASS
(role map law: a prose verifier never decides PASS). Failure direction: no answer, another model, no VERDICT line, an
unreadable spec or build, all write NO-DATA, which land_batch.checker_hold refuses to land and the next pass retries."""
import datetime, fcntl, hashlib, json, os, re, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
PLAN = "docs/plan/BROTHER-1.1.0-LAUNCH-WBS.json"; SPECS = "docs/plan/specs"
# The verdict word may be qualified ("VERDICT unchanged: FIX FIRST" was a real 347 s answer read as NO-DATA on
# 2026-09-22 because this demanded a bare colon). Up to 40 characters may sit between VERDICT and the ruling.
VERDICT_RE = re.compile(r"VERDICT\b[^A-Za-z]{0,40}?(?:[A-Za-z]+[^A-Za-z]{1,10}){0,3}?(LAND AS IS|FIX FIRST)", re.I)
# A line that BEGINS with the verdict word is the reviewer's ruling; the same words mid sentence (the brief's own
# instruction echoed back) are not. Executed attack 2026-09-22: "VERDICT: LAND AS IS" followed by an echo of the
# instruction line read as FIX under a last-match rule. Anchored lines are read first; only when there is none is
# the whole text searched.
VERDICT_LINE_RE = re.compile(r"^\s*\**\s*VERDICT\b[^A-Za-z]{0,40}?(?:[A-Za-z]+[^A-Za-z]{1,10}){0,3}?(LAND AS IS|FIX FIRST)", re.I | re.M)
MAX_NODATA = 3
# A NEGATED RULING IS NO RULING (audit 2026-09-22: "VERDICT: do not LAND AS IS" parsed as LAND). Any negation token
# between the verdict word and the ruling makes the answer NO-DATA, which holds the build; never the opposite ruling.
NEGATION_RE = re.compile(r"\b(not|never|cannot|can't|don't|do not|no)\b", re.I)
# THE CHECKER IS A SHADOW UNTIL IT IS CALIBRATED (2026-09-22). It was wired as a blocking gate at 02:41 with no
# calibration, and over the night it ruled 17 times: 14 FIX, 3 NO-DATA, 0 LAND. Upstream yields did not move (grader
# 37 to 40 percent, probes 25 to 37), yet landings went from about 56 in the 60 hours before it to ZERO after it. A
# prose reviewer asked for fixes always finds one; the estate's own laws already say a prose verifier never decides
# PASS and that no second opinion is promoted to a gate without a calibration table over 50 real decisions. So the
# default is "shadow": the checker still runs and its verdict and reasons are still written beside the build for
# calibration, but it never quarantines, never holds a landing and never spends a repair round. PASS is decided where
# it always was: the grader on two Pythons, the executed probes, the landing fuzz and the battery. "gate" restores
# the blocking behaviour byte for byte. An unknown value reads as shadow AND says so on stderr: shadow is the side on
# which the deterministic gates still decide, and a typo must never silently turn a blocking gate on or off unseen.
# This is the ONE definition; land_batch.checker_hold and unit_runner both ask this function.
MODES = ("off", "shadow", "gate")   # off is the default since plan E (2026-10-01): an unproven checker spends and decides nothing
def checker_mode(env=None, calibration=None):
    """gate only when BROTHER_CHECKER_MODE says so AND the judge calibrator has granted it to this checker (owner order
    2026-09-23: a judge earns authority by measurement). An ungranted gate reads as shadow and says so on stderr."""
    e = os.environ if env is None else env
    v = (e.get("BROTHER_CHECKER_MODE") or "").strip().lower()
    if v == "gate":
        import judge_calibrate as JC
        a = JC.authority("checker:%s" % (e.get("BROTHER_CHECKER") or "unknown"), calibration or e.get("BROTHER_JUDGE_CALIBRATION") or JC.OUT)
        if a != "gate":
            sys.stderr.write("check_wave: gate asked for checker %r but the calibrator says %s; the checker is off\n" % (e.get("BROTHER_CHECKER"), a)); return "off"
    if v in MODES: return v
    if v: sys.stderr.write("check_wave: BROTHER_CHECKER_MODE=%r is not off, shadow or gate; the checker is off\n" % v)
    return "off"


def _knob(name, default):
    v = os.environ.get(name, ""); return int(v) if v.isdigit() and int(v) > 0 else default


def sha(path):
    with open(path, "rb") as f: return hashlib.sha256(f.read()).hexdigest()


def sub_of(path):
    m = re.match(r"(.+)-r\d+-build\.json$", os.path.basename(path)); return m.group(1) if m else ""   # land_batch.sub_of, verbatim


def unit_of(sub, plan):
    for u in (plan or {}).get("units", []):
        if sub in (u.get("sub_units") or []): return u
    return None


def status_path(build):
    """<run>/STATUS for <run>/roundN/out/<build>: the same three parents land_batch.py walks."""
    return os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(build))), "STATUS")


JEV_CRITERIA = {"LAND": "the build meets its spec section, its tests prove the guards named there, and nothing in the review names a defect that would fail a landing gate",
                "FIX": "the build is close and the review names concrete, bounded defects a repair round can close",
                "REJECT": "the build misreads the spec, weakens a gate, or the review names a defect no repair round can close"}


def jev_verdict(brief_text, review_text, runner=None, timeout=300):
    """A TYPED verdict beside the prose one (owner 2026-09-22: the checker never approves on either model; a reviewer asked
    what to fix always finds something). Jev answers one choice question, LAND, FIX or REJECT against stated criteria, with
    the brief and the review as its state. Returns {"choice", "confidence"} or None (NO-DATA, never a guessed choice).
    Shadow only: it is recorded for calibration and decides nothing.
    jev_decide runs from model_router.code_root(), the frozen candidate (U3, B5-08), never the landing tree's copy;
    a code root that is refused (a proof phase with no BROTHER_CODE_ROOT) runs nothing and is NO-DATA."""
    try:
        import model_router as _MR   # HERE, this file's own directory, is on sys.path
    except ImportError:   # sbe: allow-silent None is this function's NO-DATA answer; the verdict is shadow only
        return None
    try:
        tool = os.path.join(_MR.code_root(), "scripts", "jev_decide.py")
    except _MR.Refused:   # sbe: allow-silent a refused code root runs nothing and is NO-DATA, never the landing tree's copy
        return None
    payload = {"state": (brief_text or "")[:50000] + "\n\nTHE REVIEWER'S TEXT:\n" + (review_text or "")[:10000],
               "questions": {"verdict": {"type": "choice", "instructions": "Would this build land as it is? Choose the one word that fits.", "criteria": JEV_CRITERIA}}}
    try:
        r = (runner or subprocess.run)([sys.executable, "-B", tool, "--family", "checker"], input=json.dumps(payload),
                                       capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0 or not r.stdout.strip(): return None
    try:
        recs = [json.loads(l) for l in r.stdout.splitlines() if l.strip()]
    except ValueError:   # sbe: allow-silent a result that is not JSON is no verdict; the caller reads None as NO-DATA, never as a pass
        return None
    for d in recs:
        if isinstance(d, dict) and d.get("id") == "verdict" and d.get("answer") in JEV_CRITERIA:
            c = d.get("confidence")
            return {"choice": d["answer"], "confidence": c if isinstance(c, (int, float)) and not isinstance(c, bool) else None}
    return None


def parse_verdict(text):
    """('LAND'|'FIX'|'NO-DATA', reasons). The LAST verdict line wins, because a reviewer that reasons its way from
    one to the other means the later one. Reasons are the text after a FIX verdict, whitespace collapsed, capped."""
    lines = list(VERDICT_LINE_RE.finditer(text or ""))
    if lines:
        # the LAST anchored line is the ruling, and within that line the LAST ruling wins (a reviewer that reasons
        # its way from one to the other on the same line means the later one)
        ruling = lines[-1]                       # ONE name for the chosen line, so a wrong choice cannot half apply
        start = ruling.start(); end = (text or "").find("\n", ruling.end()); end = len(text) if end < 0 else end   # the match itself may span a newline
        hits = list(VERDICT_RE.finditer(text[start:end])); last = hits[-1]
        if NEGATION_RE.search(last.group(0)): return "NO-DATA", "the verdict line is negated: %r" % last.group(0)[:80]
        tail = text[start + last.end():] if last.group(1).upper() == "FIX FIRST" else ""
        return ("LAND", "") if last.group(1).upper() == "LAND AS IS" else ("FIX", re.sub(r"\s+", " ", tail).strip()[:600] or "FIX FIRST with no numbered fixes")
    hits = list(VERDICT_RE.finditer(text or ""))
    if not hits: return "NO-DATA", "no VERDICT line in the answer"
    last = hits[-1]
    if NEGATION_RE.search(last.group(0)): return "NO-DATA", "the verdict line is negated: %r" % last.group(0)[:80]
    if last.group(1).upper() == "LAND AS IS": return "LAND", ""
    return "FIX", re.sub(r"\s+", " ", text[last.end():]).strip()[:600] or "FIX FIRST with no numbered fixes"


def quarantine_note(checker, reasons, prev):
    return "QUARANTINE checker %s FIX FIRST: %s | previous: %s\n" % (checker, reasons, (prev or "").strip() or "none")


WAVE_MIN_CALL_S = 30   # a review with less of the wave left than this is not started
# FX-11.5 (REQ-FX11-17): THE CHECKER'S ONE RETRY under BROTHER_BREAKER on. A wire failure retries the SAME model; a
# capacity status retries the next NAMED member of the checker's chain or nothing, never an unnamed model (a verdict
# from a model nobody named is not a verdict); every other status, PROVIDER_REFUSED and an answer among them, retries
# nothing. OPEN stands for a call refused at admission because the member's breaker key is open (REFUSED_BY_GATE
# whose detail starts "capacity:").
RETRY_SAME = ("EMPTY", "MALFORMED", "TIMEOUT")
RETRY_NEXT = ("LIMIT", "OVERLOAD", "AUTH", "TRANSPORT_DOWN", "OPEN")


def checker_chain(checker, env=None):
    """FX-11.5 (REQ-FX11-13): the checker's named chain, BROTHER_CHECKER first; [] when the roles file refuses it."""
    if not isinstance(checker, str) or not checker.strip():
        raise ValueError("checker_chain wants the checker's model name")
    if env is not None and not isinstance(env, dict):
        raise ValueError("checker_chain wants an environment mapping or None")
    import loop_roles
    return loop_roles.call_chain("checker", choice=checker, env=env)[0]


def retry_target(chain, first, status):
    """FX-11.5 (REQ-FX11-17): the model the one retry goes to, or None for no retry. Pure."""
    if not isinstance(chain, list) or not chain or any(not isinstance(n, str) or not n for n in chain):
        raise ValueError("retry_target wants the named chain as a list of model names")
    if not isinstance(first, str) or first not in chain:
        raise ValueError("retry_target wants the member that was called, from the chain")
    if not isinstance(status, str):
        raise ValueError("retry_target wants a status word")
    if status in RETRY_SAME:
        return first
    if status in RETRY_NEXT:
        i = chain.index(first)
        return chain[i + 1] if i + 1 < len(chain) else None
    return None


def check_one(build, checker, plan, runner=None, timeout=900, specs=SPECS, mode=None, end=None):
    """The verdict record for one build; written beside the build and returned. Never raises for a wire failure."""
    vp = build + ".check.json"; digest = sha(build)
    mode = mode or checker_mode()
    attempts = 0
    try:
        with open(vp, encoding="utf-8") as f: old = json.load(f)
        if isinstance(old, dict) and old.get("sha256") == digest and old.get("model") == checker:
            if old.get("verdict") in ("LAND", "FIX"): return dict(old, cached=True)
            attempts = old.get("attempts", 0) if isinstance(old.get("attempts"), int) else 0
    except (OSError, ValueError): pass
    rec = {"model": checker, "sha256": digest, "verdict": "NO-DATA", "seconds": 0.0, "reasons": "", "attempts": attempts + 1,
           "at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"), "mode": mode}
    sub = sub_of(build); u = unit_of(sub, plan)
    # THE UNIT'S OWN spec FIELD FIRST, the file its builder was briefed from (2026-09-30: every FX unit keeps its spec
    # under docs/plan/specs/fixes/, so the guessed <specs>/<id>.md was missing and every FX build read "no spec":
    # the checker ruled 7 times in one run and approved none, 3 of them NO-DATA for exactly this). The guess stays
    # the fallback for a unit whose field is absent or names no file.
    own = u.get("spec") if u else None
    spec = own if isinstance(own, str) and own and os.path.isfile(own) else (os.path.join(specs, "%s.md" % u["id"]) if u else "")
    if not (sub and u and os.path.isfile(spec)):
        rec["reasons"] = "no spec for %r (unit %s)" % (sub, u["id"] if u else "unknown")
    else:
        brief = build + ".check-brief.md"
        r = subprocess.run([sys.executable, os.path.join(HERE, "review_brief.py"), spec, sub, build, brief], capture_output=True, text=True)
        if r.returncode != 0 or not os.path.isfile(brief):
            rec["reasons"] = "brief not assembled: %s" % ((r.stderr or r.stdout).strip().splitlines() or ["no output"])[0][:120]
        else:
            import model_call as M, model_router as R
            with open(brief, encoding="utf-8") as f: prompt = f.read()
            # THE CHECKER BRIEF IS THE WORKER BRIEF'S CONTENT: the spec and the build, which every round already
            # sends to a third party AS PUBLIC after the private term screen. Classing it PRIVATE here refused every
            # bridge and codex checker while sending the same bytes to the workers (measured 2026-09-22). The same
            # screen decides: a brief carrying a private term is NO-DATA and never sent; a clean one is public.
            import grade_build as G
            import loop_roles
            a = None; on = loop_roles._breaker_on(dict(os.environ)); chain = [checker]   # a plain dict: the mode reader refuses os.environ
            if G.private_hits(prompt):
                rec["reasons"] = "brief withheld: it carries a private term, so no checker may receive it"; prompt = None
            elif on:
                # FX-11.5: under the breaker the checker walks its NAMED chain only; a chain the roles file refuses makes
                # no call and says why
                chain = checker_chain(checker)
                if not chain:
                    rec["reasons"] = loop_roles.call_chain("checker", choice=checker)[1] or "roles: the checker chain is empty"; prompt = None
                    chain = [checker]
            kw = {"shadow": True} if on and mode == "shadow" else {}   # a shadow checker trips only its own key [adv a6]
            try:
                # THE WAVE'S END BOUNDS EVERY CALL (2026-09-27: a pass waited 30 min inside the checker because the wave
                # budget was only read between builds, while one review may take 480 s plus a 960 s retry)
                _left = lambda want: want if end is None else int(min(want, end - time.monotonic()))
                if prompt is not None and _left(timeout) < WAVE_MIN_CALL_S:
                    rec["reasons"] = "wave budget spent before this review could start; retried next pass"; prompt = None
                if prompt is not None: a = M.call_one(chain[0], prompt, "grade", R.PUBLIC, timeout=_left(timeout), runner=runner, **kw)
                # RETRY ONCE AT DOUBLE BUDGET, never identically (the four fixes law for every outside model call).
                # A transient wire failure otherwise holds the build as NO-DATA, and once every runner is idle
                # a pass that lands nothing ends the whole night with exit 45.
                again = None
                if a is not None and not a.ok:
                    again = checker if not on else retry_target(chain, chain[0], "OPEN" if a.status == "REFUSED_BY_GATE" and str(a.detail).startswith("capacity:") else a.status)
                if again is not None and _left(timeout * 2) >= WAVE_MIN_CALL_S:
                    a2 = M.call_one(again, prompt, "grade", R.PUBLIC, timeout=_left(timeout * 2), runner=runner, **kw)
                    rec["retried"] = True
                    if a2.ok: a = a2
            except R.Refused as exc:
                a = None; rec["reasons"] = "refused at the wire: %s" % str(exc)[:160]
            if a is not None:
                rec["seconds"] = round(a.seconds, 1)
                if on and not a.ok and a.status == "PROVIDER_REFUSED": rec["reasons"] = "provider refused the grade prompt"   # REQ-FX11-11: NO-DATA, never PASS or FAIL
                elif not a.ok or a.model not in chain: rec["reasons"] = "no answer from %s: %s" % (checker, a.detail[:160])
                else:
                    with open(build + ".check.md", "w", encoding="utf-8") as f: f.write(a.answer)
                    rec["verdict"], rec["reasons"] = parse_verdict(a.answer)
                    rec["jev"] = jev_verdict(prompt, a.answer)   # typed shadow verdict, recorded beside the prose one
    # NO-DATA IS BOUNDED (review 2026-09-22): unbounded, a build whose checker never answers is re-checked at full
    # price every pass and holds its whole unit out of the pool forever. After MAX_NODATA attempts the sub unit
    # goes back to a fresh runner with the reason, exactly as a FIX does.
    exhausted = rec["verdict"] == "NO-DATA" and rec["attempts"] >= MAX_NODATA
    if mode == "gate" and (rec["verdict"] == "FIX" or exhausted):   # shadow records the ruling and touches nothing
        st = status_path(build); why = rec["reasons"] if rec["verdict"] == "FIX" else "no verdict after %d attempts: %s" % (rec["attempts"], rec["reasons"])
        # READ AND REPLACE UNDER THE RUNS ROOT'S LOCK (RR lane C, 2026-09-27): the READY was read, then replaced by a plain
        # truncating open, so a landing's QUARANTINE written in between was overwritten and a reader could see an empty
        # file. Every STATUS writer takes <runs>/.status.lock; the word is read inside it and replaced by temp plus rename.
        try:
            with open(os.path.join(os.path.dirname(os.path.dirname(st)), ".status.lock"), "a") as lock:   # outside every run folder, so no run's mtime moves
                fcntl.flock(lock, fcntl.LOCK_EX)   # released when the file closes
                with open(st, encoding="utf-8") as f: prev = f.read()
                if prev.split()[:1] == ["READY"]:
                    tmp = st + ".tmp-%d" % os.getpid()
                    with open(tmp, "w", encoding="utf-8") as f: f.write(quarantine_note(checker, why, prev))
                    os.replace(tmp, st)
        except OSError as exc: rec["note"] = "could not quarantine %s (%s)" % (st, exc)
    with open(vp, "w", encoding="utf-8") as f: json.dump(rec, f, indent=1)   # written AFTER the quarantine so the note is never lost
    return rec


def main():
    if "--selftest" in sys.argv: return selftest()
    import loop_hold as _LH; _LH.gate(where="check_wave")   # the hold reaches this paid route too (review item 1, 2026-09-26)
    checker = os.environ.get("BROTHER_CHECKER", "").strip(); builds = [os.path.abspath(a) for a in sys.argv[1:] if not a.startswith("--")]
    if not checker: print("NO-DATA: BROTHER_CHECKER names no model, so nothing was checked and nothing may land"); return 2
    if not builds: print(__doc__); return 2
    try:
        with open(PLAN, encoding="utf-8") as f: plan = json.load(f)
    except (OSError, ValueError) as exc: print("NO-DATA: plan unreadable (%s)" % exc); return 2
    # Two knobs, both bounded (review 2026-09-22): a per build timeout and a whole wave budget, so the checker can
    # never hold the landing lane for N times fifteen minutes. A build the budget does not reach stays NO-DATA and
    # the next pass tries it again.
    per_build, budget = _knob("CHECK_TIMEOUT", 480), _knob("CHECK_WAVE_BUDGET", 1500); t0 = time.monotonic()
    for b in builds:
        if time.monotonic() - t0 > budget:
            print("CHECK   %-10s SKIPPED  wave budget of %ds spent; retried next pass" % (sub_of(b) or os.path.basename(b), budget)); continue
        rec = check_one(b, checker, plan, timeout=per_build, end=t0 + budget)
        print("CHECK   %-10s %-8s %6.1fs %s%s%s" % (sub_of(b) or os.path.basename(b), rec["verdict"], rec.get("seconds", 0.0),
                                                 "(cached) " if rec.get("cached") else "", (rec.get("reasons") or "")[:110],
                                                 (" | " + rec["note"]) if rec.get("note") else ""))
    return 0


def selftest():
    """Exercise the real guards with local fixtures and stub both model transports."""
    from unittest.mock import patch
    typed_verdict = jev_verdict
    def typed_runner(*args, **kwargs):
        return subprocess.CompletedProcess(args, 0, json.dumps({"id": "verdict", "answer": "LAND", "confidence": 0.8}), "")
    def offline_jev(brief, review, runner=None, timeout=300):
        return typed_verdict(brief, review, runner=runner or typed_runner, timeout=timeout)
    try:
        with tempfile.TemporaryDirectory(prefix="check-wave-") as d:
            home = os.path.join(d, "home"); os.mkdir(home)
            # No caller registry, dispatch policy, ledger, effort knob or private list is test input.
            env = {k: v for k, v in os.environ.items() if not k.startswith("BROTHER_")}
            env.update(HOME=home, PYTHONDONTWRITEBYTECODE="1", BROTHER_MODEL_REGISTRY=os.path.join(d, "models.json"))
            with open(os.path.join(home, ".brothersbe-private-names"), "w", encoding="utf-8") as f:
                f.write("checkwave-fixture-private-token\n")
            with open(env["BROTHER_MODEL_REGISTRY"], "w", encoding="utf-8") as f:
                json.dump({"models": {
                    "opus": {"id": "fixture-opus", "transport": "claude", "privacy": "private", "quality": {"grade": 1}, "cost": 1},
                    "deepseek": {"id": "fixture-deepseek", "transport": "bridge", "privacy": "public", "quality": {"grade": 1}, "cost": 1}}}, f)
            with patch.dict(os.environ, env, clear=True):
                import model_call as M, model_router as R
                with patch.object(R, "REGISTRY", None), patch.object(R, "KINDS", ()), \
                     patch.object(M, "_run", side_effect=AssertionError("selftest attempted a real model call")), \
                     patch(__name__ + ".jev_verdict", side_effect=offline_jev):
                    return _selftest_body(d)
    except Exception as exc:
        print("selftest: FAILED before it could finish, %s: %s" % (type(exc).__name__, str(exc)[:120])); return 1


def _selftest_body(d):
    os.environ["BROTHER_CHECKER_MODE"] = "gate"   # every case below this line is about the gate; the shadow cases pass mode= themselves
    os.environ["BROTHER_JUDGE_CALIBRATION"] = os.path.join(d, "gate-cal.json")
    os.environ["BROTHER_CHECKER"] = "opus"   # calibrate the same checker the gate cases call
    with open(os.environ["BROTHER_JUDGE_CALIBRATION"], "w", encoding="utf-8") as f:
        json.dump({"judges": {"checker:opus": {"authority": "gate"}}}, f)
    run = os.path.join(d, "D4.c-1"); out = os.path.join(run, "round2", "out"); os.makedirs(out)
    b = os.path.join(out, "D4.c-r1-build.json"); specs = os.path.join(d, "specs"); os.makedirs(specs)
    with open(b, "w", encoding="utf-8") as f: f.write('{"edits": []}')
    with open(os.path.join(specs, "D4.md"), "w", encoding="utf-8") as f: f.write("# D4\nREQ 1: refuse None\n")
    with open(os.path.join(run, "STATUS"), "w", encoding="utf-8") as f: f.write("READY %s (round 2)\n" % b)
    plan = {"units": [{"id": "D4", "sub_units": ["D4.c"]}]}
    def runner_saying(text, rc=0):
        # The first-party transport requests a JSON result; the bridge emits plain text.
        return lambda argv, stdin, timeout: {"stdout": json.dumps({"result": text, "is_error": False}) if "--output-format" in argv else text,
                                             "stderr": "", "returncode": rc}
    def no_call(*args, **kwargs):
        raise AssertionError("the checker must not be called")
    def status():
        with open(os.path.join(run, "STATUS"), encoding="utf-8") as f: return f.read()
    def verdict_file():
        with open(b + ".check.json", encoding="utf-8") as f: return json.load(f)
    _ok = lambda ans, conf: (lambda *a, **k: subprocess.CompletedProcess(a, 0, json.dumps({"id": "verdict", "answer": ans, "confidence": conf}), ""))
    _bad = lambda *a, **k: subprocess.CompletedProcess(a, 45, "", "NO-DATA")
    _other = lambda *a, **k: subprocess.CompletedProcess(a, 0, json.dumps({"id": "verdict", "answer": "MAYBE"}), "")
    _junk = lambda *a, **k: subprocess.CompletedProcess(a, 0, "not json", "")
    _cal = os.path.join(d, "modes-cal.json")
    with open(_cal, "w", encoding="utf-8") as f:
        json.dump({"judges": {"checker:deepseek": {"authority": "gate"}, "checker:opus": {"authority": "shadow"}}}, f)
    cases = [("gate is honoured only for a checker the calibrator granted it to", checker_mode({"BROTHER_CHECKER_MODE": "gate", "BROTHER_CHECKER": "deepseek"}, _cal) == "gate"
              and checker_mode({"BROTHER_CHECKER_MODE": "gate", "BROTHER_CHECKER": "opus"}, _cal) == "off" and checker_mode({"BROTHER_CHECKER_MODE": "gate", "BROTHER_CHECKER": "deepseek"}, os.path.join(d, "none")) == "off"),
("jev_verdict: a typed LAND with its confidence is returned", jev_verdict("b", "r", runner=_ok("LAND", 0.8)) == {"choice": "LAND", "confidence": 0.8}),
             ("jev_verdict: a FIX with a non numeric confidence keeps the choice, confidence None", jev_verdict("b", "r", runner=_ok("FIX", "high")) == {"choice": "FIX", "confidence": None}),
             ("jev_verdict: a refused call, an answer outside the three words, or junk is None, never a guess", jev_verdict("b", "r", runner=_bad) is None and jev_verdict("b", "r", runner=_other) is None and jev_verdict("b", "r", runner=_junk) is None),
             ("LAND parses", parse_verdict("... VERDICT: LAND AS IS")[0] == "LAND"),
             ("FIX parses with reasons", parse_verdict("VERDICT: FIX FIRST\n1. add a None guard")[1] == "1. add a None guard"),
             ("the last verdict wins", parse_verdict("VERDICT: LAND AS IS ... on reflection VERDICT: FIX FIRST 1. x")[0] == "FIX"),
             ("no verdict line is NO-DATA", parse_verdict("looks fine to me")[0] == "NO-DATA"),
             ("a qualified verdict word still parses", parse_verdict("VERDICT unchanged: FIX FIRST, with fix 1")[0] == "FIX"),
             ("a negated LAND is NO-DATA, never LAND", parse_verdict("VERDICT: do not LAND AS IS")[0] == "NO-DATA"),
             ("a negated LAND mid text is NO-DATA too", parse_verdict("so the verdict cannot be LAND AS IS")[0] == "NO-DATA"),
             ("a negated FIX is NO-DATA, never LAND", parse_verdict("VERDICT: not FIX FIRST")[0] == "NO-DATA"),
             ("an echoed instruction after the ruling does not overturn it", parse_verdict("VERDICT: LAND AS IS\n(the brief said: end with VERDICT: LAND AS IS, or VERDICT: FIX FIRST)")[0] == "LAND"),
             ("a newline after the colon still parses", parse_verdict("VERDICT:\nFIX FIRST\n1. x")[0] == "FIX"),
             ("a bold markdown verdict line parses", parse_verdict("**VERDICT: FIX FIRST**\n1. x")[0] == "FIX"),
             ("the last anchored line wins over an earlier one", parse_verdict("VERDICT: FIX FIRST\n1. x\n\nafter re-running: VERDICT: LAND AS IS\nVERDICT: LAND AS IS")[0] == "LAND"),
             ("a verdict mentioned mid sentence forty chars away does not", parse_verdict("the VERDICT depends on many things we discussed at length before: LAND AS IS")[0] == "NO-DATA"),
             ("empty text is NO-DATA", parse_verdict("")[0] == "NO-DATA"),
             ("status path walks three parents", status_path(b) == os.path.join(run, "STATUS"))]
    r = check_one(b, "opus", plan, runner=runner_saying("VERDICT: FIX FIRST\n1. add a None guard"), specs=specs)
    cases += [("FIX is recorded with this build's sha", r["verdict"] == "FIX" and r["sha256"] == sha(b) and verdict_file()["verdict"] == "FIX"),
              ("FIX quarantines the READY status with the reasons", status().startswith("QUARANTINE checker opus FIX FIRST: 1. add a None guard | previous: READY")),
              ("FIX is served from the record on a second call", check_one(b, "opus", plan, runner=no_call, specs=specs).get("cached") is True),
              ("the typed shadow verdict is recorded through its stub", r.get("jev") == {"choice": "LAND", "confidence": 0.8})]
    with open(os.path.join(run, "STATUS"), "w", encoding="utf-8") as f: f.write("READY %s (round 2)\n" % b)
    with open(b, "w", encoding="utf-8") as f: f.write('{"edits": [1]}')   # new bytes: the old FIX no longer applies
    r = check_one(b, "opus", plan, runner=runner_saying("VERDICT: LAND AS IS"), specs=specs)
    cases += [("new bytes are re-checked, LAND recorded", r["verdict"] == "LAND" and not r.get("cached") and verdict_file()["sha256"] == sha(b)),
              ("LAND leaves the status READY", status().startswith("READY")),
              ("the answer text is kept beside the build", os.path.isfile(b + ".check.md"))]
    with open(b, "w", encoding="utf-8") as f: f.write('{"edits": [2]}')
    calls = []
    def flaky(argv, stdin, timeout):
        calls.append(timeout); return runner_saying("VERDICT: LAND AS IS", rc=0 if len(calls) > 1 else 1)(argv, stdin, timeout)
    r = check_one(b, "opus", plan, runner=flaky, specs=specs)
    cases += [("one wire failure is retried once at double budget and the retry's answer counts", r["verdict"] == "LAND" and r.get("retried") is True and len(calls) == 2 and calls[1] == 2 * calls[0])]
    with open(b, "w", encoding="utf-8") as f: f.write('{"edits": [3]}')
    r = check_one(b, "opus", plan, runner=runner_saying("VERDICT: LAND AS IS", rc=1), specs=specs)
    cases += [("a wire failure is NO-DATA, never LAND", r["verdict"] == "NO-DATA" and "no answer from opus" in r["reasons"]),
              ("NO-DATA leaves the status READY for a retry", status().startswith("READY")),
              ("NO-DATA is retried, not served from the record", check_one(b, "opus", plan, runner=runner_saying("no verdict here"), specs=specs).get("cached") is None),
              ("an answer without a verdict line is NO-DATA", verdict_file()["verdict"] == "NO-DATA"),
              ("a build in no plan unit is NO-DATA with the reason", check_one(b, "opus", {"units": []}, runner=runner_saying("VERDICT: LAND AS IS"), specs=specs)["reasons"].startswith("no spec")),
              ("the quarantine note carries the previous status", quarantine_note("opus", "1. x", "READY /b") == "QUARANTINE checker opus FIX FIRST: 1. x | previous: READY /b\n")]
    with open(b, "w", encoding="utf-8") as f: f.write('{"edits": [9]}')
    with open(os.path.join(run, "STATUS"), "w", encoding="utf-8") as f: f.write("READY %s (round 2)\n" % b)
    seen = [check_one(b, "opus", plan, runner=runner_saying("nothing here"), specs=specs)["attempts"] for _ in range(MAX_NODATA)]
    cases += [("NO-DATA attempts are counted across calls", seen == list(range(1, MAX_NODATA + 1))),
              ("the third NO-DATA quarantines the READY status with the count", status().startswith("QUARANTINE checker opus FIX FIRST: no verdict after %d attempts" % MAX_NODATA)),
              ("a quarantine the file system refuses is recorded in the verdict, never dropped",
               (lambda r: r.get("note", "").startswith("could not quarantine"))(check_one(os.path.join(d, "nodir", "D4.c-r9-build.json"), "opus", plan, runner=runner_saying("VERDICT: FIX FIRST 1. x"), specs=specs) if (os.makedirs(os.path.join(d, "nodir"), exist_ok=True) or open(os.path.join(d, "nodir", "D4.c-r9-build.json"), "w").close() or True) else None))]
    with open(b, "w", encoding="utf-8") as f: f.write('{"edits": [11]}')
    with open(os.path.join(run, "STATUS"), "w", encoding="utf-8") as f: f.write("READY %s (round 2)\n" % b)
    r = check_one(b, "opus", plan, runner=runner_saying("VERDICT: FIX FIRST\n1. x"), specs=specs, mode="shadow")
    cases += [("shadow records a FIX with its reasons and its mode", r["verdict"] == "FIX" and r["reasons"] == "1. x" and verdict_file()["mode"] == "shadow"),
              ("shadow FIX leaves the status READY", status().startswith("READY")),
              ("unset mode is off", checker_mode({}) == "off"),
              ("an empty mode is off", checker_mode({"BROTHER_CHECKER_MODE": " "}) == "off"),
              ("an explicit shadow stays shadow, for a calibration run", checker_mode({"BROTHER_CHECKER_MODE": "shadow"}) == "shadow"),
              ("gate is read case blind", checker_mode({"BROTHER_CHECKER_MODE": "GATE", "BROTHER_CHECKER": "deepseek", "BROTHER_JUDGE_CALIBRATION": _cal}) == "gate"),
              ("an unknown mode is off, never gate", checker_mode({"BROTHER_CHECKER_MODE": "gaet"}) == "off")]
    with open(b, "w", encoding="utf-8") as f: f.write('{"edits": [12]}')
    for _ in range(MAX_NODATA): check_one(b, "opus", plan, runner=runner_saying("nothing here"), specs=specs, mode="shadow")
    cases += [("shadow NO-DATA, however often, leaves the status READY", status().startswith("READY"))]
    fx_run = os.path.join(d, "FX9.a-1"); fx_out = os.path.join(fx_run, "round1", "out"); os.makedirs(fx_out)
    fx_b = os.path.join(fx_out, "FX9.a-r1-build.json"); fx_spec = os.path.join(d, "specs", "fixes", "FX9.md"); os.makedirs(os.path.dirname(fx_spec))
    with open(fx_b, "w", encoding="utf-8") as f: f.write('{"edits": []}')
    with open(fx_spec, "w", encoding="utf-8") as f: f.write("# FX9\n#### FX9.a one\nREQ 1: refuse None\n")
    with open(os.path.join(fx_run, "STATUS"), "w", encoding="utf-8") as f: f.write("READY %s (round 1)\n" % fx_b)
    fx_plan = {"units": [{"id": "FX9", "sub_units": ["FX9.a"], "spec": fx_spec}]}
    r = check_one(fx_b, "opus", fx_plan, runner=runner_saying("VERDICT: LAND AS IS"), specs=specs, mode="shadow")
    cases += [("a unit whose spec field names a file outside <specs>/<id>.md is checked from that file, never NO-DATA", r["verdict"] == "LAND")]
    with open(fx_b, "w", encoding="utf-8") as f: f.write('{"edits": [1]}')   # new bytes, so the recorded LAND does not answer
    cases += [("the same unit with no spec field and no <specs>/<id>.md is still NO-DATA with the reason",
               check_one(fx_b, "opus", {"units": [{"id": "FX9", "sub_units": ["FX9.a"]}]}, runner=runner_saying("VERDICT: LAND AS IS"), specs=specs, mode="shadow")["reasons"].startswith("no spec"))]
    # THE BRIEF IS PUBLIC CONTENT AFTER THE SCREEN, like the worker brief (2026-09-22): a bridge or codex checker is allowed
    with open(b, "w", encoding="utf-8") as f: f.write('{"edits": [21]}')
    seen = []
    r = check_one(b, "deepseek", plan, runner=lambda argv, stdin, timeout: (seen.append(argv) or {"stdout": "VERDICT: LAND AS IS", "stderr": "", "returncode": 0}), specs=specs, mode="shadow")
    cases += [("a bridge checker receives the screened brief and its LAND is recorded", r["verdict"] == "LAND" and seen)]
    with open(b, "w", encoding="utf-8") as f: f.write('{"edits": [22]}')
    # The synthetic term comes from this selftest's HOME, never the caller's private list.
    with open(os.path.expanduser("~/.brothersbe-private-names"), encoding="utf-8") as f:
        term = next((l.strip() for l in f if l.strip() and not l.startswith("#")), "")
    with open(os.path.join(specs, "D4.md"), "a", encoding="utf-8") as f: f.write("\nnever send this: %s\n" % term)
    r = check_one(b, "deepseek", plan, runner=no_call, specs=specs, mode="shadow")
    cases += [("a brief carrying a private term is withheld: NO-DATA with the reason, no call made", r["verdict"] == "NO-DATA" and "withheld" in r["reasons"])]
    with open(os.path.join(specs, "D4.md"), "w", encoding="utf-8") as f: f.write("# D4\nREQ 1: refuse None\n")
    with open(b, "w", encoding="utf-8") as f: f.write('{"edits": [23]}')
    r = check_one(b, "opus", plan, runner=lambda *a: {"stdout": "VERDICT: LAND AS IS", "stderr": "", "returncode": 0}, specs=specs)
    cases += [("plain text from a JSON transport is NO-DATA, never LAND", r["verdict"] == "NO-DATA" and "not the JSON result" in r["reasons"])]
    # THE WAVE'S END BOUNDS EVERY CALL (2026-09-27: a pass waited 30 min inside the checker)
    with open(b, "w", encoding="utf-8") as f: f.write('{"edits": [31]}')
    seen_t = []
    r = check_one(b, "opus", plan, runner=lambda argv, stdin, timeout: (seen_t.append(timeout), runner_saying("VERDICT: LAND AS IS")(argv, stdin, timeout))[1],
                  specs=specs, timeout=480, end=time.monotonic() + 100)
    cases += [("a review gets only what is left of the wave, never its own full budget", r["verdict"] == "LAND" and bool(seen_t) and seen_t[0] <= 100 + 30)]
    with open(b, "w", encoding="utf-8") as f: f.write('{"edits": [32]}')
    r = check_one(b, "opus", plan, runner=no_call, specs=specs, timeout=480, end=time.monotonic() + 10)
    cases += [("no review starts with less of the wave left than WAVE_MIN_CALL_S", r["verdict"] == "NO-DATA" and "wave budget spent" in r["reasons"])]
    with open(b, "w", encoding="utf-8") as f: f.write('{"edits": [33]}')
    calls2 = []
    def flaky2(argv, stdin, timeout):
        calls2.append(timeout); return runner_saying("VERDICT: LAND AS IS", rc=0 if len(calls2) > 1 else 1)(argv, stdin, timeout)
    r = check_one(b, "opus", plan, runner=flaky2, specs=specs, timeout=150, end=time.monotonic() + 200)
    cases += [("the retry at double budget is bounded by the wave too", len(calls2) == 2 and calls2[1] <= 200 + 30 and calls2[1] < 2 * calls2[0])]
    # THE ENTRY POINT PASSES THE WAVE'S END: main() with only the review itself stubbed
    import loop_hold
    from unittest.mock import patch as _patch
    plan_path = os.path.join(d, "plan.json")
    with open(plan_path, "w", encoding="utf-8") as f: json.dump(plan, f)
    got, me = {}, sys.modules[__name__]
    with _patch.object(loop_hold, "gate", lambda **k: None), \
         _patch.dict(os.environ, {"BROTHER_CHECKER": "opus", "CHECK_WAVE_BUDGET": "120"}), \
         _patch.object(sys, "argv", ["check_wave.py", b]), _patch.object(me, "PLAN", plan_path), \
         _patch.object(me, "check_one", lambda build, checker, plan, **kw: (got.update(kw), {"verdict": "LAND", "seconds": 0.0})[1]):
        rc_main = main()
    cases += [("main() hands every review the wave's end", rc_main == 0 and isinstance(got.get("end"), float)
               and 0 < got["end"] - time.monotonic() <= 120)]
    with open(b, "w", encoding="utf-8") as f: f.write('{"edits": [34]}')   # fresh bytes: nothing recorded to serve
    os.remove(os.path.expanduser("~/.brothersbe-private-names"))
    r = check_one(b, "opus", plan, runner=no_call, specs=specs)
    cases += [("a missing private list withholds the brief without calling the checker", r["verdict"] == "NO-DATA" and "withheld" in r["reasons"])]
    bad = [n for n, good in cases if not good]
    print("selftest: %d cases, %s" % (len(cases), "OK" if not bad else "FAILED: " + ", ".join(bad))); return 1 if bad else 0


if __name__ == "__main__": sys.exit(main())
