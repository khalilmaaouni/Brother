#!/usr/bin/env python3
"""The canary: a build that really landed once is replayed through the judging path before any run may start.

WHY (measured 2026-09-22). Every judge of the loop had a green selftest and the pipeline as a whole was never tried.
A runner that crashed at its grade stage on every run, and a checker that vetoed 17 of 17, each cost hours before a
human saw them. The canary is the whole path on a known good input: the build named in docs/plan/loop-canary.json is
applied and graded in a sandbox worktree at the commit its landing was made against, then the landing side rules on it
(STATUS word). It must reach WOULD LAND. No model is called, nothing is pushed, the
real tree is never touched.

usage (repo root): loop_canary.py            exit 0 WOULD LAND, 1 a stage refused (named), 3 NO-DATA (the spec, the
                                            build json or git cannot be read: on a machine without the evidence the
                                            canary cannot run, and that is said, never passed)
                   loop_canary.py --selftest
Covers: git worktree at the base commit, grade_build (preflight, red without code, green with code, mutations, both
Pythons as the grader does it), the STATUS word rule (the checker hold is gone with the checker, owner decision 2026-10-02). Does NOT cover: the executed probe stage
(it needs a model), the battery on the whole tree (minutes; the landing runs it), the push.
"""
import json, os, re, shutil, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)   # THIS file's own directory: the copies installed beside it
SPEC = os.path.join(os.path.dirname(os.path.dirname(HERE)), "docs", "plan", "loop-canary.json")
SPEC_FALLBACK = os.path.expanduser("~/.claude/loop-canary.json")


def load_spec(path=None):
    for p in ([path] if path else [SPEC, SPEC_FALLBACK]):
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict) and all(isinstance(d.get(k), str) and d[k] for k in ("sub", "base_commit", "build")):
                return dict(d, build=os.path.expanduser(d["build"]))
        except (OSError, ValueError):
            continue
    return None


def stages(spec, run, keep=False, spec_gate=None):
    """[(stage, state, detail)] in order; stops at the first refusal. `run(argv, cwd)` -> (rc, text). Pure but for the
    sandbox, which is created and removed here."""
    out = []
    if not os.path.isfile(spec["build"]):
        return [("build json", "NO-DATA", "%s is not on this machine" % spec["build"])]
    rc, txt = run(["git", "rev-parse", "--verify", spec["base_commit"] + "^{commit}"], None)
    if rc != 0:
        return [("base commit", "NO-DATA", "%s is not in this repository" % spec["base_commit"])]
    box = tempfile.mkdtemp(prefix="loop-canary-")
    try:
        rc, txt = run(["git", "worktree", "add", "--detach", "-q", os.path.join(box, "tree"), spec["base_commit"]], None)
        if rc != 0:
            return out + [("sandbox", "NO-DATA", "git worktree add failed: %s" % txt.strip()[-120:])]
        out.append(("sandbox", "OK", "worktree at %s" % spec["base_commit"]))
        rc, txt = run([sys.executable, "-B", os.path.join(HERE, "grade_build.py"), spec["build"]], os.path.join(box, "tree"))
        last = (txt.strip().splitlines() or ["no output"])[-1]
        if rc != 0 or not re.match(r"^PASS\b", last):
            return out + [("grade", "REFUSED", "the grader said: %s" % last[:140])]
        out.append(("grade", "OK", last[:100]))
        # THE PROBE RUNNER AND THE SPEC GATE, IN THE SANDBOX (2026-09-24: the probe gate approved 0 of 6 lanes for an hour
        # because a CLI exit code read as a wrong accept, and the spec gate executed a backticked path; neither tool was
        # on the canary's path, so five driver starts found them one at a time). A fixture with no recorded probe leaves
        # the stage UNMEASURED, said by name, never counted as a pass.
        probe = os.path.expanduser(spec.get("probe") or "")
        if probe and os.path.isfile(probe):
            rc, txt = run([sys.executable, "-B", os.path.join(HERE, "probe_build.py"), spec["build"], probe], os.path.join(box, "tree"))
            m = re.search(r"^PROBES\s+(\d+) run: (\d+) CRASH, (\d+) WRONG-ACCEPT\?", txt, re.M)
            if rc != 0 or not m or int(m.group(1)) == 0 or int(m.group(2)) or int(m.group(3)):
                return out + [("probe", "REFUSED", "the probe runner said: %s" % ((m.group(0) if m else (txt.strip().splitlines() or ["no output"])[-1])[:120]))]
            out.append(("probe", "OK", m.group(0)[:100]))
        else:
            out.append(("probe", "UNMEASURED", "no probe recorded in loop-canary.json: add one from a landed run's probes/out"))
        # THE LANDING TOOL ITSELF, IN THE SANDBOX (2026-09-22): the executed land_build.py had been a 116 line library with
        # no main since 2026-09-21 20:09, exit 0 and no output, and every landing dropped as "fuzz crashes 99: no output"
        # for a day and a half. The canary's first version graded and then trusted the landing side by reading its
        # rules; now it RUNS the tool on the same build and demands the verdict line land_batch demands.
        rc, txt = run([sys.executable, "-B", os.path.join(HERE, "land_apply.py"), spec["build"]], os.path.join(box, "tree"))
        if rc != 0 or "VERDICT SUITES GREEN" not in txt:
            return out + [("land apply", "REFUSED", "the landing tool said: %s" % ((txt.strip().splitlines() or ["NO OUTPUT: exit %d and not one line, which is the 2026-09-21 defect" % rc])[-1][:120]))]
        out.append(("land apply", "OK", "suites green on both Pythons, fuzz clean"))
        # AFTER land apply, so the build's own files are in the tree the spec's done check reads (2026-09-24: run before
        # it, every older fixture read red on a test module the build itself adds)
        import land_batch
        def real_gate(sub, root):
            with open(os.path.join(root, land_batch.PLAN), encoding="utf-8") as fh: plan = json.load(fh)
            return land_batch.spec_gate(sub, plan, root=root)
        gate = spec_gate or real_gate
        try:
            why = gate(spec["sub"], os.path.join(box, "tree"))
        except Exception as exc:   # sbe: allow-silent the gate's own failure is the finding, named below
            why = "the spec gate raised: %s" % exc
        if why:
            return out + [("spec gate", "REFUSED", "the spec gate would drop it: %s" % str(why)[:140])]
        out.append(("spec gate", "OK", "the section's done check is green on both Pythons in the sandbox"))
        status = "READY %s (canary)" % spec["build"]
        w = status.split()
        if w[0] != "READY" or os.path.realpath(w[1]) != os.path.realpath(spec["build"]):
            return out + [("status word", "REFUSED", "the landing would not read this STATUS as READY for this file")]
        out.append(("status word", "OK", "READY for exactly this file"))
        return out
    finally:
        run(["git", "worktree", "remove", "--force", os.path.join(box, "tree")], None)
        if not keep:
            shutil.rmtree(box, ignore_errors=True)


def verdict(rows):
    states = [s for _, s, _ in rows]
    if "NO-DATA" in states: return "CANARY NO-DATA", 3
    if "REFUSED" in states: return "CANARY REFUSED at %s" % [n for n, s, _ in rows if s == "REFUSED"][0], 1
    unm = [n for n, s, _ in rows if s == "UNMEASURED"]
    return "CANARY WOULD LAND: %d stage(s) passed%s" % (len(rows) - len(unm), ", %d unmeasured (%s)" % (len(unm), ", ".join(unm)) if unm else ""), 0


def real_run(argv, cwd):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    try:
        r = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=1700, env=env)
        # stderr FIRST: every stage reads a tool's verdict from the last line, and a tool's stderr notes (a lost
        # ledger claim, 2026-09-30) are diagnostics that must never stand in for the verdict it printed on stdout.
        return r.returncode, "\n".join(p for p in (r.stderr.rstrip("\n"), r.stdout) if p)
    except (OSError, subprocess.SubprocessError) as exc:
        return 99, str(exc)


def fixtures_of(spec):
    """The fixture set: the spec's own build first, then every entry of its "fixtures" list that carries sub, base_commit
    and build (2026-09-24, I7: one fixture proves the path runs; a set of build shapes is what calibrates the rules).
    An entry missing a field is skipped by name, never silently."""
    out = [spec]
    for i, f in enumerate(spec.get("fixtures") or []):
        if isinstance(f, dict) and all(isinstance(f.get(k), str) and f[k] for k in ("sub", "base_commit", "build")):
            out.append(dict(f, build=os.path.expanduser(f["build"])))
        else:
            sys.stderr.write("loop_canary: fixture %d skipped: it lacks sub, base_commit or build\n" % i)
    return out


def verdict_set(results):
    """[(sub, rows)] -> (line, code): NO-DATA if any fixture is NO-DATA, REFUSED if any refused, else WOULD LAND with the
    unmeasured stages named; the worst fixture decides, never the average."""
    codes = [(sub,) + verdict(rows) for sub, rows in results]
    worst = max(codes, key=lambda c: c[2])
    if worst[2] != 0: return "CANARY %s: fixture %s: %s" % ("NO-DATA" if worst[2] == 3 else "REFUSED", worst[0], worst[1]), worst[2]
    unm = sorted({n for _, rows in results for n, s, _ in rows if s == "UNMEASURED"})
    return "CANARY WOULD LAND: %d fixture(s), %d stage(s) passed%s" % (len(results), sum(1 for _, rows in results for _, s, _ in rows if s == "OK"),
                                                                      ", unmeasured: %s" % ", ".join(unm) if unm else ""), 0


def main():
    if "--selftest" in sys.argv:
        return selftest()
    spec = load_spec()
    if spec is None:
        print("CANARY NO-DATA: docs/plan/loop-canary.json cannot be read or lacks sub, base_commit or build"); return 3
    results = []
    for fx in fixtures_of(spec):
        rows = stages(fx, real_run)
        print("== fixture %s at %s" % (fx["sub"], fx["base_commit"]))
        for n, s, d in rows:
            print("%-8s %-13s %s" % (s, n, d))
        results.append((fx["sub"], rows))
        if verdict(rows)[1] != 0: break   # the first refused or unreadable fixture ends the canary: a run may not start
    line, code = verdict_set(results); print(line); return code


def selftest():
    try: return _selftest_body()
    except Exception as exc:
        print("selftest: FAILED before it could finish, %s: %s" % (type(exc).__name__, str(exc)[:120])); return 1


def _selftest_body():
    d = tempfile.mkdtemp(prefix="canary-self-"); b = os.path.join(d, "x-r0-build.json"); open(b, "w").write("{}")
    spec = {"sub": "x", "base_commit": "abc1234", "build": b}
    def fake(answers):
        calls = []; argvs = []
        def run(argv, cwd):
            calls.append((os.path.basename(argv[2]) if argv[0] == sys.executable else argv[0], cwd))
            argvs.append(list(argv))
            for key, (rc, txt) in answers.items():
                if key in " ".join(argv): return rc, txt
            return (0, "VERDICT SUITES GREEN | touched: x") if "land_apply" in " ".join(argv) else (0, "")
        return run, calls, argvs
    green = lambda *a, **k: ""        # the spec gate says nothing: the section's done check is green
    _stages = globals()["stages"]
    def stages(spec, run, keep=False, spec_gate=green): return _stages(spec, run, keep, spec_gate)
    ok, calls, argvs = fake({"grade_build": (0, "APPLY 1\nPASS x-r0")}); rows = stages(spec, ok)
    st = lambda rows, n: dict((a, (s, t)) for a, s, t in rows).get(n)
    cases = [("a passing grade and a green landing tool reach WOULD LAND, exit 0", verdict(rows)[1] == 0 and st(rows, "grade")[0] == "OK" and st(rows, "land apply")[0] == "OK"),
             ("a landing tool that exits 0 with NO OUTPUT is REFUSED at land apply, the 2026-09-21 defect", verdict(stages(spec, fake({"grade_build": (0, "PASS x"), "land_apply": (0, "")})[0]))[0] == "CANARY REFUSED at land apply"),
             ("a landing tool that says RED is REFUSED", verdict(stages(spec, fake({"grade_build": (0, "PASS x"), "land_apply": (0, "VERDICT RED: 2 | touched: x")})[0]))[0] == "CANARY REFUSED at land apply"),
             ("the landing tool runs INSIDE the sandbox too", any(n == "land_apply.py" and cwd and cwd.endswith("/tree") for n, cwd in calls)),
             ("the grader runs INSIDE the sandbox worktree, never here", any(n == "grade_build.py" and cwd and cwd.endswith("/tree") for n, cwd in calls)),
             ("the sandbox worktree is removed afterwards, even on success", any(n == "git" and "remove" in " ".join(a) for (n, _), a in zip(calls, argvs))),
             ("a failing grade is REFUSED at grade, exit 1", verdict(stages(spec, fake({"grade_build": (1, "APPLY 1\nFAIL tests pass without the code")})[0]))[0] == "CANARY REFUSED at grade"),
             ("a grader that exits 0 without a PASS line is REFUSED, never trusted", verdict(stages(spec, fake({"grade_build": (0, "something else")})[0]))[1] == 1),
             ("a missing build json is NO-DATA, exit 3", verdict(stages(dict(spec, build=os.path.join(d, "absent.json")), ok))[1] == 3),
             ("an unknown base commit is NO-DATA", verdict(stages(spec, fake({"rev-parse": (128, "fatal")})[0]))[1] == 3),
             ("a worktree that cannot be made is NO-DATA", verdict(stages(spec, fake({"worktree add": (1, "fatal: x")})[0]))[1] == 3),
             ("a spec missing a field is unreadable", load_spec(os.path.join(d, "absent.json")) is None),
             # the probe runner and the spec gate on the canary's path (2026-09-24)
             ("no recorded probe leaves the probe stage UNMEASURED, named in the verdict, never a pass", st(rows, "probe")[0] == "UNMEASURED" and "1 unmeasured (probe)" in verdict(rows)[0]),
             ("a recorded probe with clean counts is OK", st(stages(dict(spec, probe=b), fake({"grade_build": (0, "PASS x"), "probe_build": (0, "PROBES   25 run: 0 CRASH, 0 WRONG-ACCEPT?, 22 REFUSED")})[0]), "probe")[0] == "OK"),
             ("a recorded probe with a crash is REFUSED at probe", verdict(stages(dict(spec, probe=b), fake({"grade_build": (0, "PASS x"), "probe_build": (0, "PROBES   25 run: 1 CRASH, 0 WRONG-ACCEPT?, 22 REFUSED")})[0]))[0] == "CANARY REFUSED at probe"),
             ("a recorded probe with a wrong accept is REFUSED at probe", verdict(stages(dict(spec, probe=b), fake({"grade_build": (0, "PASS x"), "probe_build": (0, "PROBES   25 run: 0 CRASH, 2 WRONG-ACCEPT?, 22 REFUSED")})[0]))[0] == "CANARY REFUSED at probe"),
             ("a probe runner that ran zero probes is REFUSED, never clean on silence", verdict(stages(dict(spec, probe=b), fake({"grade_build": (0, "PASS x"), "probe_build": (0, "PROBES   0 run: 0 CRASH, 0 WRONG-ACCEPT?, 0 REFUSED")})[0]))[0] == "CANARY REFUSED at probe"),
             ("a red spec done check is REFUSED at the spec gate", verdict(stages(spec, ok, spec_gate=lambda *a, **k: "spec done check red: exit 1"))[0] == "CANARY REFUSED at spec gate"),
             ("a spec gate that raises is REFUSED, never skipped", verdict(stages(spec, ok, spec_gate=lambda *a, **k: 1 / 0))[0] == "CANARY REFUSED at spec gate"),
             # the fixture set (I7)
             ("the fixture set is the spec's build plus every complete fixture entry, incomplete ones skipped by name",
              [f["sub"] for f in fixtures_of(dict(spec, fixtures=[{"sub": "y", "base_commit": "b", "build": "~/y.json"}, {"sub": "z"}]))] == ["x", "y"]),
             ("the worst fixture decides the set: one REFUSED among greens is REFUSED", verdict_set([("x", rows), ("y", [("grade", "REFUSED", "x")])])[1] == 1),
             ("a NO-DATA fixture outranks a REFUSED one", verdict_set([("x", [("grade", "REFUSED", "x")]), ("y", [("build json", "NO-DATA", "x")])])[1] == 3),
             ("a set of greens names its unmeasured stages once", verdict_set([("x", rows), ("y", rows)])[0].endswith("unmeasured: probe"))]
    sp = os.path.join(d, "spec.json"); json.dump({"sub": "x", "base_commit": "abc", "build": "~/nowhere/b.json"}, open(sp, "w"))
    cases += [("the build path is expanded from the spec", load_spec(sp)["build"].startswith("/") and "~" not in load_spec(sp)["build"])]
    # a real child that prints its verdict on stdout and a note on stderr AFTER it: the verdict stays the last line
    rc, txt = real_run([sys.executable, "-c", "import sys; print('PASS x'); sys.stdout.flush(); "
                        "sys.stderr.write('NO-DATA: a claim was not recorded\\n')"], d)
    cases += [("a stderr note never replaces the verdict as the last line", rc == 0 and txt.strip().splitlines()[-1] == "PASS x"
               and "NO-DATA: a claim was not recorded" in txt)]
    failed = [n for n, good in cases if not good]
    print("selftest: %d cases, %s" % (len(cases), "OK" if not failed else "FAILED: " + ", ".join(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
