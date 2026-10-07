#!/usr/bin/env python3
"""THE JUDGE CALIBRATOR (owner order 2026-09-23 00:2x): every model judge earns authority by measurement, never by being
switched on. It replays each recorded checker verdict (<build>.check.json: LAND or FIX) against the landing outcome of the
build it judged, prints precision and recall per judge, and writes ~/.claude/evidence/judge-calibration.json, which
check_wave.checker_mode reads: a judge is granted "gate" only above the bar, and stays "shadow" otherwise.
usage (repo root): judge_calibrate.py [--runs DIR] [--plan FILE] [--out FILE] [--council FILE] | --selftest
OUTCOME of a build, from the run folder's STATUS and the plan: GOOD when the run reached READY and its sub unit reads landed
in the unit's evidence; BAD when the run's STATUS word is QUARANTINE (refused at landing); UNKNOWN otherwise, and an unknown
never counts. A judge with no known outcome is NO-DATA, never a pass. Deterministic judges are not scored: they decide."""
import glob, json, os, re, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import plan_store  # noqa: E402  (the one landed test: plan_store.sub_landed, finding 2 of 2026-09-27)
RUNS = os.path.expanduser("~/.claude/evidence/unit-runs"); PLAN = "docs/plan/BROTHER-1.1.0-LAUNCH-WBS.json"
OUT = os.path.expanduser("~/.claude/evidence/judge-calibration.json"); COUNCIL = os.path.expanduser("~/.claude/evidence/spec-council.json")
BAR_PRECISION, BAR_RULINGS = 0.8, 20


def _json(p):
    try:
        with open(p, encoding="utf-8") as fh: return json.load(fh)
    except (OSError, ValueError): return None


def landed_subs(plan):
    out = set()
    for u in (plan or {}).get("units", []) or []:
        if not isinstance(u, dict): continue
        ev = u.get("evidence") or ""
        out.update(s for s in (u.get("sub_units") or []) if plan_store.sub_landed(s, ev))
    return out


def outcomes(runs_dir, plan):
    """{run folder: GOOD | BAD | UNKNOWN}."""
    landed = landed_subs(plan); out = {}
    for run in glob.glob(os.path.join(runs_dir, "*-*/")):
        run = run.rstrip("/"); sub = re.sub(r"-\d{6}$", "", os.path.basename(run))
        try:
            with open(os.path.join(run, "STATUS"), encoding="utf-8") as fh: word = (fh.read().split() or [""])[0]
        except OSError: word = ""
        out[run] = "BAD" if word == "QUARANTINE" else "GOOD" if word == "READY" and sub in landed else "UNKNOWN"
    return out


def verdicts(runs_dir):
    """[(judge, run folder, verdict)] from every <build>.check.json; a verdict outside LAND and FIX is skipped."""
    out = []
    for f in glob.glob(os.path.join(runs_dir, "*-*", "round*", "out", "*.check.json")):
        d = _json(f)
        if not isinstance(d, dict) or d.get("verdict") not in ("LAND", "FIX"): continue
        run = os.path.dirname(os.path.dirname(os.path.dirname(f)))
        out.append(("checker:%s" % (d.get("model") or "unknown"), run, d["verdict"]))
    return out


def score(verdicts_, outcomes_):
    """{judge: {rulings, known, tp, fp, fn, precision, recall, authority}}: FIX predicts BAD."""
    by = {}
    for judge, run, v in verdicts_:
        s = by.setdefault(judge, {"rulings": 0, "known": 0, "tp": 0, "fp": 0, "fn": 0, "tn": 0})
        s["rulings"] += 1; o = outcomes_.get(run, "UNKNOWN")
        if o == "UNKNOWN": continue
        s["known"] += 1
        if v == "FIX": s["tp" if o == "BAD" else "fp"] += 1
        else: s["fn" if o == "BAD" else "tn"] += 1
    for s in by.values():
        pf = s["tp"] + s["fp"]; bad = s["tp"] + s["fn"]
        s["precision"] = round(s["tp"] / pf, 3) if pf else None; s["recall"] = round(s["tp"] / bad, 3) if bad else None
        s["authority"] = ("NO-DATA" if s["known"] == 0 else "gate" if s["known"] >= BAR_RULINGS and (s["precision"] or 0) >= BAR_PRECISION else "shadow")
    return by


def council_score(council, plan):
    """Informational: council state per unit against the unit's own state (DONE with evidence reads good)."""
    out = {"units": 0, "held_then_done": 0, "clear_then_done": 0}
    for u in (plan or {}).get("units", []) or []:
        st = (council or {}).get(u.get("id"), {}).get("state") if isinstance(council, dict) else None
        if not st: continue
        out["units"] += 1
        if u.get("state") == "DONE" and (u.get("evidence") or "").strip():
            out["held_then_done" if st in ("FIX-FIRST", "DO-NOT-BUILD") else "clear_then_done"] += 1
    return out


def run(runs_dir=RUNS, plan_path=PLAN, out_path=OUT, council_path=COUNCIL, quiet=False):
    plan = _json(plan_path) or {}; sc = score(verdicts(runs_dir), outcomes(runs_dir, plan)); cs = council_score(_json(council_path), plan)
    rec = {"judges": sc, "council": cs, "bar": {"precision": BAR_PRECISION, "rulings": BAR_RULINGS}}
    try:
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path + ".tmp", "w", encoding="utf-8") as fh: json.dump(rec, fh, indent=1)
        os.replace(out_path + ".tmp", out_path)
    except OSError as exc:
        print("CALIB   NO-DATA: calibration not written (%s); every judge stays shadow" % type(exc).__name__); return 3
    if not quiet:
        for j, s in sorted(sc.items()):
            print("CALIB   %-20s rulings %3d known %3d precision %-5s recall %-5s -> %s" % (j, s["rulings"], s["known"], s["precision"], s["recall"], s["authority"]))
        print("CALIB   council: %d unit(s) ruled, %d held then DONE, %d clear then DONE" % (cs["units"], cs["held_then_done"], cs["clear_then_done"]))
        if not sc: print("CALIB   NO-DATA: no checker verdict on disk; nothing to calibrate")
    return 0 if sc else 3


def authority(judge, path=OUT):
    """The calibration file's word for this judge: gate, shadow, or NO-DATA when the file or the judge is absent."""
    d = _json(path)
    try: return d["judges"][judge]["authority"]
    except (TypeError, KeyError): return "NO-DATA"


def selftest():
    d = tempfile.mkdtemp(prefix="calib-"); runs = os.path.join(d, "runs")
    def run_dir(name, word, verdict=None, model="deepseek"):
        r = os.path.join(runs, name); os.makedirs(os.path.join(r, "round0", "out"), exist_ok=True)
        with open(os.path.join(r, "STATUS"), "w") as fh: fh.write(word + " x\n")
        if verdict:
            with open(os.path.join(r, "round0", "out", "b-r0-build.json.check.json"), "w") as fh: json.dump({"model": model, "verdict": verdict}, fh)
    run_dir("D1.1-000001", "READY", "FIX"); run_dir("D1.2-000002", "READY", "LAND"); run_dir("D1.3-000003", "QUARANTINE", "FIX"); run_dir("D1.4-000004", "EXHAUSTED", "FIX")
    run_dir("D1.5-000005", "READY", "LAND", model="opus"); run_dir("D1.6-000006", "READY", "BOGUS")
    plan = {"units": [{"id": "D1", "state": "DONE", "sub_units": ["D1.1", "D1.2", "D1.3", "D1.4", "D1.5"], "evidence": "D1.1 landed; D1.2 landed; D1.5 landed"}]}
    pp = os.path.join(d, "plan.json"); json.dump(plan, open(pp, "w")); out = os.path.join(d, "cal.json")
    cp = os.path.join(d, "council.json"); json.dump({"D1": {"state": "FIX-FIRST"}}, open(cp, "w"))
    rc = run(runs, pp, out, cp, quiet=True); cal = _json(out)["judges"]; ds = cal["checker:deepseek"]
    cases = [("outcomes: READY and landed is GOOD, QUARANTINE is BAD, the rest UNKNOWN", outcomes(runs, plan)[os.path.join(runs, "D1.1-000001")] == "GOOD" and outcomes(runs, plan)[os.path.join(runs, "D1.3-000003")] == "BAD" and outcomes(runs, plan)[os.path.join(runs, "D1.4-000004")] == "UNKNOWN"),
             ("a FIX on a landed build is a false positive, a FIX on a quarantined build a true one, an unknown outcome counts nowhere", ds["tp"] == 1 and ds["fp"] == 1 and ds["known"] == 3 and ds["rulings"] == 4),
             ("precision and recall are computed, and under the bar the judge stays shadow", ds["precision"] == 0.5 and ds["recall"] == 1.0 and ds["authority"] == "shadow"),
             ("a judge is scored per model; a verdict outside LAND and FIX is skipped", cal["checker:opus"]["rulings"] == 1 and "checker:unknown" not in cal),
             ("the bar grants gate only at 20 known rulings and precision 0.8 or more", score([("j", "r%d" % i, "FIX") for i in range(20)], {"r%d" % i: "BAD" if i < 17 else "GOOD" for i in range(20)})["j"]["authority"] == "gate"
              and score([("j", "r%d" % i, "FIX") for i in range(19)], {"r%d" % i: "BAD" for i in range(19)})["j"]["authority"] == "shadow"),
             ("no known outcome is NO-DATA, never a pass", score([("j", "r1", "FIX")], {})["j"]["authority"] == "NO-DATA"),
             ("authority() reads the file and answers NO-DATA for an absent judge or file", authority("checker:deepseek", out) == "shadow" and authority("nobody", out) == "NO-DATA" and authority("x", os.path.join(d, "none")) == "NO-DATA"),
             ("the council is scored informationally against DONE units", _json(out)["council"] == {"units": 1, "held_then_done": 1, "clear_then_done": 0}),
             ("an empty runs dir exits NO-DATA", run(os.path.join(d, "none"), pp, out, cp, quiet=True) == 3 and rc == 0)]
    bad = [n for n, ok in cases if not ok]
    print("selftest: %d cases, %s" % (len(cases), "OK" if not bad else "FAILED: " + ", ".join(bad))); return 1 if bad else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv: sys.exit(selftest())
    a = sys.argv[1:]; opt = lambda n, dflt: a[a.index(n) + 1] if n in a and a.index(n) + 1 < len(a) else dflt
    sys.exit(run(opt("--runs", RUNS), opt("--plan", PLAN), opt("--out", OUT), opt("--council", COUNCIL)))
