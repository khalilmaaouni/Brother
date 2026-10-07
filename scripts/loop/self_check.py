#!/usr/bin/env python3
"""The worker's self check: the grader's safety screen, run on every build BEFORE the sandbox grade, with one free repair.
usage as a library:  self_check.screen(round_dir, runner=None) -> (unsafe, repaired)
usage as a check:    python3 -B self_check.py --selftest
Owner 2026-09-22 23:0x (piece 3 of the redesign): the two refusals that filled the day's grader histogram (a file importing
subprocess or urllib outside the allow list, a dynamic call) cost a whole round each. Here every build that the screen would
refuse is sent back ONCE to its own model with the exact refusal at the head of its prompt, and the answer overwrites the
build, before any sandbox runs. Static only: the "tests fail without the code" check needs a sandbox and stays in the grader.
A build the screen cannot parse is sent back too (the grader would refuse it). Nothing here grades, lands or decides."""
import json, os, subprocess, sys
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import ev_gate          # H5.a: wired where the loop reads its expected value verdict
import fanout_verdict   # H5.a: wired where the loop reads its budget refusal verdict

HEAD = ("SELF CHECK REFUSAL. The grader's safety screen would refuse this build for this reason:\n  %s\n"
        "Fix exactly that, at its source, and return the WHOLE build again in the same JSON shape (edits, tests, done_check, "
        "mutations, unknowns), complete and self contained. Nothing else changes.\n\n")


def unsafe_reason(build, runners=None):
    """The grader's own screen (grade_build.unsafe); an unreadable build is 'not a JSON object' (refused, never passed)."""
    try:
        import grade_build as G
        if not isinstance(build, dict): return "the build is not a JSON object"
        # THE SAME CONTRACT AS THE GRADER (review 2026-09-27): the grader admits the modules a build edits
        # (allowed_imports=_build_imports); self check did not, refused builds the grader accepts, and paid a repair for each
        allowed = G._build_imports(build)
        return G.unsafe(build, runners, allowed_imports=allowed, contained=G.contained()) if runners is not None else G.unsafe(build, allowed_imports=allowed, contained=G.contained())
    except Exception as exc:
        return "the safety screen could not read this build (%s)" % type(exc).__name__


def screen(round_dir, runner=None, timeout=480, runners=None):
    """(unsafe, repaired): builds the screen refused, and how many came back after one repair dispatch.
    `runner(jobs_path, results_path)` runs the fan out; the default is or_fanout. A round that cannot
    be read is (0, 0); a hostile argument (H5.a) is refused by name with ValueError, never left to
    reach os.path.join as a raw TypeError."""
    try:
        round_dir = os.fspath(round_dir)
    except TypeError:
        raise ValueError("screen: round_dir must be a path, got %s" % type(round_dir).__name__)
    if not isinstance(round_dir, str) or not round_dir:
        raise ValueError("screen: round_dir must be a non empty path string")
    round_dir = os.path.abspath(round_dir)   # the fan out runs in the code root, so every path it is handed is absolute
    if runner is not None and not callable(runner):
        raise ValueError("screen: runner must be callable or None, got %s" % type(runner).__name__)
    if runners is not None and not isinstance(runners, (set, frozenset, list, tuple)):
        raise ValueError("screen: runners must be a set of paths or None, got %s" % type(runners).__name__)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout != timeout or timeout <= 0:
        raise ValueError("screen: timeout must be a positive number of seconds, got %r" % (timeout,))
    try:
        with open(os.path.join(round_dir, "jobs.json"), encoding="utf-8") as f: jobs = json.load(f)
    except (OSError, ValueError):
        return 0, 0
    redo = []
    for job in jobs if isinstance(jobs, list) else []:
        if not isinstance(job, dict) or not isinstance(job.get("out"), str): continue
        try:
            with open(job["out"], encoding="utf-8") as f: build = json.load(f)
        except (OSError, ValueError):
            continue                                   # no build came back: nothing to screen
        why = unsafe_reason(build, runners)
        if not why: continue
        try:
            with open(job["prompt_file"], encoding="utf-8") as f: original = f.read()
            pf = job["prompt_file"] + ".selfcheck.md"
            with open(pf, "w", encoding="utf-8") as f: f.write(HEAD % why + original)
            redo.append(dict(job, prompt_file=os.path.abspath(pf), out=os.path.abspath(job["out"])))
        except (OSError, KeyError, TypeError):
            continue
    if not redo: return 0, 0
    code_root = None
    if runner is None:
        # THE FAN OUT RUNS THE FROZEN CANDIDATE (U3, B5-08): `-m` resolves the module from the cwd, which was the landing
        # tree. A refused code root (a proof phase with none set) sends nothing, the same answer as a fan out that failed.
        import model_router as _MR   # HERE is on sys.path
        try:
            code_root = _MR.code_root()
        except _MR.Refused as exc:
            print("SELFCHECK not sent: the code root is refused (%s)" % str(exc)[:160], flush=True)
            return len(redo), 0
    jp = os.path.join(round_dir, "jobs-selfcheck.json"); rp = os.path.join(round_dir, "results-selfcheck.json")
    with open(jp, "w", encoding="utf-8") as f: json.dump(redo, f, indent=1)
    try:
        if runner is None:
            # the fan out exits nonzero when ANY job fails, after writing the others: its exit code is logged, and the outputs
            # below are read either way (Codex review 2026-09-25: an early return here reported real repairs as zero)
            _rc = subprocess.run([sys.executable, "-m", "plugin.runtime.brother.core.or_fanout", jp, "--workers", "99", "--timeout", str(timeout), "--retries", "0", "--results", rp],
                           stdout=open(os.path.join(round_dir, "selfcheck.log"), "w"), stderr=subprocess.STDOUT, timeout=timeout + 60,
                           cwd=code_root).returncode
            if _rc: print("SELFCHECK fan out exit %d: reading the outputs it wrote" % _rc, flush=True)
        else:
            runner(jp, rp)
    except subprocess.TimeoutExpired:
        # a fan out past its deadline may still have written some repairs: log it and read what is there (Codex 2026-09-25)
        print("SELFCHECK fan out timed out after %ds: reading the outputs it wrote" % (timeout + 60), flush=True)
    except (OSError, subprocess.SubprocessError):
        return len(redo), 0
    repaired = 0
    for job in redo:
        try:
            with open(job["out"], encoding="utf-8") as f: b = json.load(f)
            if not unsafe_reason(b, runners): repaired += 1
        except (OSError, ValueError):
            pass
    return len(redo), repaired


def selftest():
    import tempfile
    d = tempfile.mkdtemp(prefix="self-check-"); os.makedirs(os.path.join(d, "out"))
    def w(name, obj):
        p = os.path.join(d, name)
        with open(p, "w", encoding="utf-8") as f: (f.write(obj) if isinstance(obj, str) else json.dump(obj, f))
        return p
    bad = {"edits": [{"path": "scripts/x.py", "new_file_content": "import subprocess\nsubprocess.run(['curl','x'])\n"}], "tests": [], "done_check": "python3 scripts/test_x.py", "mutations": []}
    good = {"edits": [{"path": "scripts/x.py", "new_file_content": "def f(x):\n    return x\n"}], "tests": [], "done_check": "python3 scripts/test_x.py", "mutations": []}
    p1 = w("p1.md", "ORIGINAL PROMPT"); o1 = w("out/A-r0-build.json", bad); o2 = w("out/A-r1-build.json", good)
    w("jobs.json", [{"id": "A-r0", "model": "deepseek", "prompt_file": p1, "out": o1, "expect": "json", "sensitivity": "public"},
                    {"id": "A-r1", "model": "deepseek", "prompt_file": p1, "out": o2, "expect": "json", "sensitivity": "public"}])
    seen = {}
    def fake_runner(jp, rp):
        with open(jp) as f: jobs = json.load(f)
        seen["n"] = len(jobs); seen["head"] = open(jobs[0]["prompt_file"]).read()[:40]
        with open(jobs[0]["out"], "w") as f: json.dump(good, f)      # the model fixed it
    r = screen(d, runner=fake_runner, runners=set())
    r2 = screen(d, runner=fake_runner, runners=set())                  # everything safe now: nothing dispatched
    # X3 FINDING 3 (2026-09-27): the ledger must count the repair this screen writes, under the build's own id, whenever
    # it finishes. A round in a run folder, its results written, then a repair whose results and payment land 300 s later.
    import time, unit_ledger
    rd = os.path.join(d, "runs", "X1.1-000001", "round0"); os.makedirs(os.path.join(rd, "out"))
    ob = os.path.join(rd, "out", "X1.1-r0-build.json")
    with open(ob, "w", encoding="utf-8") as f: json.dump(bad, f)
    with open(os.path.join(rd, "jobs.json"), "w", encoding="utf-8") as f:
        json.dump([{"id": "X1.1-r0", "model": "deepseek", "prompt_file": p1, "out": ob, "expect": "json", "sensitivity": "public"}], f)
    with open(os.path.join(rd, "results.json"), "w", encoding="utf-8") as f: json.dump([{"id": "X1.1-r0", "ok": True}], f)
    t0 = time.time() - 1000; os.utime(os.path.join(rd, "jobs.json"), (t0, t0)); os.utime(os.path.join(rd, "results.json"), (t0 + 50, t0 + 50))
    pay = os.path.join(d, "payments.jsonl")
    def paid(rid, usd, at):
        with open(pay, "a", encoding="utf-8") as f:
            f.write(json.dumps({"type": "RESERVE", "reservation_id": rid, "holder_id": "X1.1-r0", "at": at - 1}) + "\n")
            f.write(json.dumps({"type": "RECONCILE", "reservation_id": rid, "actual_cost": usd, "at": at}) + "\n")
    paid("orig", 2, t0 + 40)
    def slow_repair(jp, rp):
        with open(jp) as f: jobs = json.load(f)
        with open(jobs[0]["out"], "w") as f: json.dump(good, f)
        with open(rp, "w") as f: json.dump([{"id": jobs[0]["id"], "ok": True}], f)
        os.utime(rp, (t0 + 350, t0 + 350)); paid("repair", 3, t0 + 350)
    r3 = screen(rd, runner=slow_repair, runners=set())
    cases = [("the unsafe build is found by the grader's own screen, the safe one is not, and the repaired build comes back safe", r == (1, 1) and seen.get("n") == 1),
             ("the ledger counts the repair this screen wrote 300 s after the round's results, beside the first payment",
              r3 == (1, 1) and unit_ledger.blended_usd("X1.1", os.path.join(d, "runs"), pay) == 5),
             ("the repair prompt heads with the exact refusal", seen.get("head", "").startswith("SELF CHECK REFUSAL")),
             ("a round with nothing unsafe dispatches nothing", r2 == (0, 0)),
             ("an unreadable round is (0, 0), never a crash", screen(os.path.join(d, "none")) == (0, 0)),
             ("a non JSON build is refused, never passed", unsafe_reason("x") is not None)]
    bad_ = [n for n, ok in cases if not ok]
    print("selftest: %d cases, %s" % (len(cases), "OK" if not bad_ else "FAILED: " + ", ".join(bad_))); return 1 if bad_ else 0


if __name__ == "__main__": sys.exit(selftest() if "--selftest" in sys.argv else 2)
