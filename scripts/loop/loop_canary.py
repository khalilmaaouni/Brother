#!/usr/bin/env python3
"""The canary: a build that really landed once is replayed through the judging path before any run may start.

WHY (measured 2026-09-22). Every judge of the loop had a green selftest and the pipeline as a whole was never tried.
A runner that crashed at its grade stage on every run, and a checker that vetoed 17 of 17, each cost hours before a
human saw them. The canary is the whole path on a known good input: the build named in docs/plan/loop-canary.json is
applied and graded in a sandbox worktree at the commit its landing was made against, then the landing side rules on it
(STATUS word). It must reach WOULD LAND. No model is called, nothing is pushed, the
real tree is never touched.

usage (repo root): loop_canary.py [--landing [--keep]]   exit 0 WOULD LAND, 1 a stage refused (named), 3 NO-DATA (the spec, the
                                            build json, git, or the deployed stamp's revision cannot be read: on a
                                            machine without the evidence the canary cannot run, and that is said,
                                            never passed)
                   loop_canary.py --selftest
THE LANDING STAGE (1.1.7 LR1.d), asked for with --landing, which start_run.sh passes and nothing else does: the intake
(loop_intake.canary, cached for a day) and the deploy's staged proof (tool_stamp.prove) run this file bare with a 1800 second
bound, and a whole gate on every deploy and every intake would run past it and refuse both. The replay is what they get.
After the replay reaches WOULD LAND, landing_stage sends one tiny real change through the
LIVE judge: a disposable worktree at the revision the DEPLOYED stamp names, one scratch file changed and committed there
(the judge refuses a checkout with uncommitted changes, so the change must be a commit; it is on a detached HEAD in that
worktree only, on no branch, never pushed, unreachable once the worktree is removed), then land_batch.deploy_steps' judge:
the boxed whole gate on that merged tree with the stamp's kernel overlay, exactly as a landing runs it. It calls no model
and writes nothing outside its temporary worktree. --keep leaves that worktree for inspection and says where. A replay
that already refused, or has no data, skips it: the whole gate is not spent on a launch that is already refused.
Covers: git worktree at the base commit, grade_build (preflight, red without code, green with code, mutations, both
Pythons as the grader does it), the STATUS word rule (the checker hold is gone with the checker, owner decision 2026-10-02). Does NOT cover: the executed probe stage
(it needs a model), the battery on the whole tree (minutes; the landing runs it), the push.
"""
import json, os, re, shutil, signal, stat, subprocess, sys, tempfile, time
from typing import List, Optional

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


FIXTURE_PLAN = "docs/plan/BROTHER-1.1.0-LAUNCH-WBS.json"   # N2 (2026-10-11): the plan every current fixture was specified in


def fixture_plan(fx):
    """The plan a fixture's sub unit was specified in: fx["plan"] through plan_path.validate (ValueError on a bad value),
    else FIXTURE_PLAN. Never the run's plan (BROTHER_WBS): the fixture's base commit need not hold it, which refused
    every intake under a newer plan (2026-10-11 00:54, 'CANARY REFUSED at spec gate')."""
    if "plan" not in fx:
        return FIXTURE_PLAN
    import plan_path
    return plan_path.validate(fx["plan"])


def read_fixture_plan(spec, root):
    """The plan document the spec gate reads: the fixture's own plan, inside `root` (the sandbox at its base commit)."""
    with open(os.path.join(root, fixture_plan(spec)), encoding="utf-8") as fh:
        return json.load(fh)


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
            return land_batch.spec_gate(sub, read_fixture_plan(spec, root), root=root)
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


RUN_BOUND_S = 1700   # one child's bound, seconds; a child past it is killed with its whole group


def real_run(argv, cwd, bound=None):
    """(returncode, text) of one child, which leads its own process group: when this process is interrupted (a TERM or an
    INT raised as SystemExit by install_signal_raisers) or the child outlives its bound, the WHOLE group is killed, so a
    grader's own children never outlive the canary."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    try:
        p = subprocess.Popen(argv, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, env=env,
                             start_new_session=True)
    except OSError as exc:
        return 99, str(exc)
    try:
        out, err = p.communicate(timeout=bound or RUN_BOUND_S)
    except subprocess.TimeoutExpired as exc:
        _kill_group(p)
        return 99, str(exc)
    except BaseException:
        _kill_group(p)
        raise
    # stderr FIRST: every stage reads a tool's verdict from the last line, and a tool's stderr notes (a lost
    # ledger claim, 2026-09-30) are diagnostics that must never stand in for the verdict it printed on stdout.
    return p.returncode, "\n".join(x for x in (err.rstrip("\n"), out) if x)


CHILD_GRACE_S = 40   # a child told to TERM is given this long to unwind (finish() drops the judge's copy, run_group kills the gate)


def _kill_group(p, grace=None):
    """TERM the process group the child leads, give it `grace` seconds (CHILD_GRACE_S) to unwind its own finally blocks (a
    deployed judge's finish() and run_group's kill of its gate run only then), then SIGKILL what still stands and reap the
    child. Never raises: the caller is already unwinding."""
    grace = CHILD_GRACE_S if grace is None else grace
    try:
        os.killpg(p.pid, signal.SIGTERM)
    except OSError:
        pass   # sbe: allow-silent the group is already gone, the state this wants
    try:
        p.communicate(timeout=grace)
    except (OSError, subprocess.SubprocessError):
        pass   # sbe: allow-silent it did not unwind in time: the kill below is the answer
    try:
        os.killpg(p.pid, signal.SIGKILL)   # whatever is left of the group, the leader included
    except OSError:
        pass   # sbe: allow-silent nothing is left, the state this wants
    try:
        p.communicate(timeout=10)
    except (OSError, subprocess.SubprocessError):
        pass   # sbe: allow-silent a child that will not be reaped in ten seconds is left to init; its group is already killed


def _raise_on_signal(signum, frame):
    """A TERM, INT or HUP becomes SystemExit(128 + signal), so every `finally` of the run unwinds (the sandbox worktree, the
    landing worktree, the child groups). Later signals are ignored: a second one must not cut the clean up short."""
    for name in ("SIGTERM", "SIGINT", "SIGHUP"):
        signal.signal(getattr(signal, name), signal.SIG_IGN)
    raise SystemExit(128 + signum)


def install_signal_raisers():
    """Install _raise_on_signal for SIGTERM, SIGINT and SIGHUP (it also overrides an inherited ignore, which a shell gives a
    background job). Only the main thread can; anywhere else this says nothing and changes nothing."""
    try:
        for name in ("SIGTERM", "SIGINT", "SIGHUP"):
            signal.signal(getattr(signal, name), _raise_on_signal)
    except ValueError:
        pass   # sbe: allow-silent not the main thread: signal.signal refuses, and no handler is the only other choice


def missing_fixture_files(spec: dict) -> List[str]:
    """One <sub>: <build path> line for every fixture whose build is not an existing regular file, in fixture
    order. The canary calls this before it replays anything: a fixture whose build file is absent is NO-DATA
    (exit 3), one line each, never a stage that runs for minutes and then fails. A fixture entry that
    fixtures_of skips (it lacks sub, base_commit or build) is not reported again. A spec that is not a dict,
    and a spec whose fixtures value is neither absent nor a list, are corrupt input refused with ValueError."""
    if not isinstance(spec, dict):
        raise ValueError("spec must be a dict, not %s" % type(spec).__name__)
    listed = spec.get("fixtures")
    if listed is not None and not isinstance(listed, list):
        raise ValueError("spec fixtures must be a list, not %s" % type(listed).__name__)
    out = []
    for fixture in fixtures_of(spec):
        sub = fixture.get("sub") if isinstance(fixture, dict) else fixture
        build = fixture.get("build") if isinstance(fixture, dict) else None
        if not isinstance(build, str):
            out.append("%s: %r" % (sub, build))
            continue
        path = os.path.expanduser(build)
        if not os.path.isfile(path):
            out.append("%s: %s" % (sub, path))
    return out


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


def canary_key(spec, repo, python_version=None):
    """LA4.a: the content key of one canary run, None when any input cannot be read (the canary then runs and says
    NO-DATA itself). Keyed: every fixture (canonical JSON), each base commit resolved to its full sha, each build and
    named probe file, the bytes of docs/plan/loop-canary.json, scripts/loop, and both interpreters (the grader runs
    both). Ambient state the grade tools read is bounded by the cache's 24 h age, not keyed here."""
    import loop_intake as LI
    if not isinstance(spec, dict):
        return None
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    rows = []
    try:
        for fx in fixtures_of(spec):
            r = subprocess.run(["git", "rev-parse", "--verify", "-q", fx["base_commit"] + "^{commit}"], cwd=repo,
                               capture_output=True, text=True, timeout=30, env=env)
            sha = r.stdout.strip()
            if r.returncode != 0 or not re.fullmatch(r"[0-9a-f]{40,64}", sha):
                return None
            probe = os.path.expanduser(fx.get("probe") or "")
            rows.append([json.dumps(fx, sort_keys=True), sha, LI._sha_file(os.path.expanduser(fx["build"])),
                         LI._sha_file(probe) if probe else None])
        spec_file = os.path.join(repo, "docs", "plan", "loop-canary.json")
        spec_bytes = LI._sha_file(spec_file) if os.path.exists(spec_file) else "missing"
    except (OSError, subprocess.SubprocessError, TypeError, ValueError):
        return None
    tools = LI.tree_digest(os.path.join(repo, "scripts", "loop"))
    py39 = LI.stat_signature("/usr/bin/python3")
    if tools is None or py39 is None:
        return None
    return LI._digest(["canary", rows, spec_bytes, tools, python_version or sys.version, py39])


def verdict_set(results):
    """[(sub, rows)] -> (line, code): NO-DATA if any fixture is NO-DATA, REFUSED if any refused, else WOULD LAND with the
    unmeasured stages named; the worst fixture decides, never the average."""
    codes = [(sub,) + verdict(rows) for sub, rows in results]
    worst = max(codes, key=lambda c: c[2])
    if worst[2] != 0: return "CANARY %s: fixture %s: %s" % ("NO-DATA" if worst[2] == 3 else "REFUSED", worst[0], worst[1]), worst[2]
    unm = sorted({n for _, rows in results for n, s, _ in rows if s == "UNMEASURED"})
    return "CANARY WOULD LAND: %d fixture(s), %d stage(s) passed%s" % (len(results), sum(1 for _, rows in results for _, s, _ in rows if s == "OK"),
                                                                      ", unmeasured: %s" % ", ".join(unm) if unm else ""), 0


#: The first words of the line a canary prints only when the landing stage ran and reached WOULD LAND. start_run.sh requires
#: them: an older loop_canary.py that ignores --landing exits 0 after the replay alone, and must not read as a pass.
LANDING_OK = "CANARY WOULD LAND: the replay and the landing stage"
LANDING_SCRATCH = "logs/landing-canary-scratch.txt"   # the one file the landing stage changes: a new file, no neighbour reads it


def deployed_bin() -> str:
    """The deployed bin whose stamp names generation N: BROTHER_DEPLOY_TARGET (the deploy tools' own switch) else ~/.claude/bin."""
    return os.path.expanduser(os.environ.get("BROTHER_DEPLOY_TARGET") or "~/.claude/bin")


#: THE DEPLOYED LAND_BATCH RUNS IN A CHILD PYTHON, never imported here. The judge a landing runs is the deployed bin's, not this
#: checkout's scripts/loop copy (the very code a landing is about to change); the child puts that bin first on its own
#: sys.path and imports land_batch there, so this process loads no second copy and the deploy's closure check sees no dynamic
#: import. mode "stamp" prints `STAMP <json>`; mode "judge" runs deploy_steps' judge inside the tree, finishes it, and prints
#: `JUDGE [ok, why]`.
DEPLOYED_CHILD = r"""
import json, os, signal, sys
bin_dir, mode = sys.argv[1], sys.argv[2]
sys.path.insert(0, bin_dir)
import land_batch as lb
def _raise(signum, frame):
    for name in ("SIGTERM", "SIGINT", "SIGHUP"):
        signal.signal(getattr(signal, name), signal.SIG_IGN)
    raise SystemExit(128 + signum)
for name in ("SIGTERM", "SIGINT", "SIGHUP"):
    signal.signal(getattr(signal, name), _raise)
if mode == "stamp":
    print("STAMP " + json.dumps(lb.deploy_stamp(bin_dir)))
    sys.stdout.flush()
else:
    tree, stamp, logpath = sys.argv[3:6]
    os.chdir(tree)
    with open(logpath, "a", encoding="utf-8") as log:
        do = lb.deploy_steps(json.loads(stamp), log, {})
        try:
            ok, why = do["judge"]()
        finally:
            do["finish"]()
    print("JUDGE " + json.dumps([ok, why]))
    sys.stdout.flush()
"""
JUDGE_BOUND_S = 7300   # the judge's own bound is 7200 seconds (land_batch.deploy_steps)


def _deployed_child(bin_dir, mode, *args, bound=None):
    """(returncode, output) of DEPLOYED_CHILD in a child python over `bin_dir`: stderr first, stdout last."""
    # -I: PYTHONPATH, the user site and the current directory cannot shadow a module; the bin is first on its sys.path
    # explicitly (DEPLOYED_CHILD), so the deployed land_batch and its siblings are what is imported
    return real_run([sys.executable, "-I", "-B", "-c", DEPLOYED_CHILD, bin_dir, mode] + list(args), None, bound)


def _last(text, word):
    """The JSON after the last `<word> ` line of a child's output, or None."""
    for line in reversed((text or "").splitlines()):
        if line.startswith(word + " "):
            try:
                return json.loads(line[len(word) + 1:])
            except ValueError:
                break   # the last verdict line is malformed: that is no verdict, and an older line must not stand in for it
    return None


def deployed_stamp(bin_dir: Optional[str] = None) -> Optional[dict]:
    """The deployed bin's stamp as ITS land_batch reads it (deploy_stamp: a stamp carrying a kernel), or None, said on stderr,
    when the bin holds no land_batch.py, it will not run, or the stamp is missing or unreadable."""
    bin_dir = bin_dir or deployed_bin()
    if not os.path.isfile(os.path.join(bin_dir, "land_batch.py")):
        sys.stderr.write("loop_canary: no land_batch.py in the deployed bin %s\n" % bin_dir)
        return None
    rc, text = _deployed_child(bin_dir, "stamp", bound=300)
    stamp = _last(text, "STAMP")
    if not isinstance(stamp, dict):
        sys.stderr.write("loop_canary: the deployed land_batch in %s gave no stamp (exit %d): %s\n"
                         % (bin_dir, rc, (text.strip().splitlines() or ["no output"])[-1][:160]))
        return None
    return stamp


def live_judge(stamp: dict, tree: str, log, bin_dir: Optional[str] = None) -> tuple:
    """(ok, why) from the landing's own judge step, the DEPLOYED land_batch's, run in a child python INSIDE `tree` (the judge
    reads the checkout it runs in), its finish() run whatever the judge did; the gate's output goes to `log` (a file, by
    name). No deployed land_batch, a child that exits non zero, and a child that prints no verdict are RuntimeError, never a
    fall back to this checkout's copy and never a pass. The child leads its own process group, so an interrupt kills it."""
    bin_dir = bin_dir or deployed_bin()
    if not os.path.isfile(os.path.join(bin_dir, "land_batch.py")):
        raise RuntimeError("no land_batch.py in the deployed bin %s" % bin_dir)
    logpath = getattr(log, "name", None)
    if not isinstance(logpath, str):
        raise RuntimeError("the judge's log has no file name")
    rc, text = _deployed_child(bin_dir, "judge", tree, json.dumps(stamp), logpath, bound=JUDGE_BOUND_S)
    verdict = _last(text, "JUDGE")
    if rc != 0 or verdict is None:
        raise RuntimeError("the deployed judge child exited %d without a verdict: %s"
                           % (rc, (text.strip().splitlines() or ["no output"])[-1][:200]))
    return verdict[0], verdict[1]


def _git(argv, cwd=None):
    """(returncode, text) of one git command. A command that outlives its bound or cannot start is a non zero code (124, 127),
    never an exception: the clean up in landing_stage's finally calls this too."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    try:
        r = subprocess.run(["git"] + argv, cwd=cwd, capture_output=True, text=True, timeout=300, env=env)
    except subprocess.TimeoutExpired:
        return 124, "git %s timed out after 300 seconds" % (argv[0] if argv and argv[0] != "-c" else "command")
    except OSError as exc:
        return 127, "git could not start (%s)" % type(exc).__name__
    return r.returncode, (r.stdout + r.stderr).strip()


def _no_data(why: str) -> List[dict]:
    sys.stderr.write("loop_canary: landing stage NO-DATA: %s\n" % why)
    return []


GATE_LOG_TAIL = 2 * 1024 * 1024   # a kept gate log is its last 2 MB: the failing rows are at the end


def keep_gate_log(box: str, log_path: str, stamp: Optional[str] = None) -> Optional[str]:
    """Copy the refused landing judge's whole gate log (and any failing row's own output the judge left under `box`,
    required-fast-fail-*.txt) to <evidence>/loop-starts/canary-gate-<stamp>.log, its last GATE_LOG_TAIL bytes each, and
    return that path. Called BEFORE the disposable worktree and box are removed: 2026-10-21 a real launch was refused with
    "the whole gate exited 1 boxed: deploy-canary FAIL exit 1" and the log went with the box, so nobody could see why. The
    evidence folder is START_RUN_EVIDENCE when the launcher names one, else ~/.claude/evidence. A copy that cannot be made
    returns None and says so on stderr: it never changes or fails the refusal itself."""
    try:
        stamp = stamp or "%s-%d" % (time.strftime("%Y%m%d-%H%M%S"), os.getpid())
        folder = os.path.join(os.environ.get("START_RUN_EVIDENCE") or os.path.expanduser("~/.claude/evidence"), "loop-starts")
        os.makedirs(folder, exist_ok=True)
        dest = os.path.join(folder, "canary-gate-%s.log" % stamp)
        sources = [log_path]
        base = os.path.realpath(box)
        for d, dirs, files in os.walk(box):   # os.walk never descends into a directory link
            dirs[:] = [x for x in dirs if x != ".git"]
            # the landing under judgement writes the box: a link, or a path resolving outside it, is never read
            # (security review 2026-10-21, the same rule as land_batch.read_kept)
            sources += sorted(p for p in (os.path.join(d, f) for f in files if f.startswith("required-fast-fail-") and f.endswith(".txt"))
                              if not os.path.islink(p) and os.path.realpath(p).startswith(base + os.sep))
        with open(dest, "wb") as out:
            for src in sources:
                fd = os.open(src, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))   # a link swapped in after the walk refuses
                if not stat.S_ISREG(os.fstat(fd).st_mode):
                    os.close(fd)
                    continue
                size = os.fstat(fd).st_size
                with os.fdopen(fd, "rb") as fh:
                    if size > GATE_LOG_TAIL:
                        fh.seek(size - GATE_LOG_TAIL)
                    out.write(("==== %s%s\n" % (os.path.basename(src), " (last %d of %d bytes)" % (GATE_LOG_TAIL, size) if size > GATE_LOG_TAIL else "")).encode())
                    out.write(fh.read())
                    out.write(b"\n")
        return dest
    except OSError as exc:
        sys.stderr.write("loop_canary: could not keep the gate log of the refusal (%s: %s)\n" % (type(exc).__name__, exc))
        return None


def landing_stage(spec: dict, run: str, keep: bool = False, bin_dir: Optional[str] = None, judge=None) -> List[dict]:
    """[{"name", "exit_code", "detail"}], one row for the live judge, or [] when it could not run (NO-DATA: the deployed
    stamp or its revision cannot be read, no worktree, no commit). A spec that is not a dict and a run that is not a
    non empty string are ValueError, before any worktree is made. bin_dir (default deployed_bin()) and judge (default
    live_judge) are seams; the judge gets (stamp, tree, log) and answers (ok, why). A refusal row names the judge's own
    refusal text, which names the gate rows that went red. The worktree is removed on every path unless keep."""
    if not isinstance(spec, dict):
        raise ValueError("spec must be a dict, not %s" % type(spec).__name__)
    if not isinstance(run, str) or not run.strip():
        raise ValueError("run must be a non empty string, not %r" % (run,))
    stamp = deployed_stamp(bin_dir)
    if stamp is None:
        return _no_data("the deployed bin %s has no land_batch.py that runs, or its stamp is missing, unreadable or carries no kernel"
                        % (bin_dir or deployed_bin()))
    rev = stamp.get("revision")
    if not isinstance(rev, str) or not re.fullmatch(r"[0-9a-f]{7,64}", rev):
        return _no_data("the deployed stamp names no revision (%r)" % (rev,))
    rc, txt = _git(["rev-parse", "--verify", "-q", rev + "^{commit}"])
    if rc != 0:
        return _no_data("the deployed revision %s is not in this repository" % rev[:12])
    try:
        box = tempfile.mkdtemp(prefix="loop-canary-landing-")
    except OSError as exc:
        return _no_data("no temporary folder for the disposable worktree (%s)" % type(exc).__name__)
    tree = os.path.join(box, "tree")
    made = False
    log = None
    try:
        rc, txt = _git(["worktree", "add", "--detach", "-q", tree, rev])
        if rc != 0:
            return _no_data("git worktree add failed: %s" % txt[-120:])
        made = True
        scratch = os.path.join(tree, LANDING_SCRATCH)
        if os.path.commonpath([os.path.realpath(tree), os.path.realpath(scratch)]) != os.path.realpath(tree):
            return _no_data("the scratch path %s leaves the worktree" % LANDING_SCRATCH)
        os.makedirs(os.path.dirname(scratch), exist_ok=True)
        with open(scratch, "w", encoding="utf-8") as fh:
            fh.write("landing canary scratch change for run %s at %s\n" % (run.strip(), rev))
        for argv in (["add", "-f", "--", LANDING_SCRATCH],
                     ["-c", "core.hooksPath=/dev/null", "-c", "user.name=loop landing canary", "-c", "user.email=canary@example.invalid",
                      "-c", "commit.gpgsign=false", "commit", "-q", "-m", "loop landing canary: one scratch change (disposable, never pushed)"]):
            rc, txt = _git(argv, cwd=tree)
            if rc != 0:
                return _no_data("git %s failed in the disposable worktree: %s" % (argv[0] if argv[0] != "-c" else "commit", txt[-120:]))
        log_path = os.path.join(box, "judge.log")
        log = open(log_path, "w", encoding="utf-8")
        try:
            ok, why = (judge or (lambda st, tr, lg: live_judge(st, tr, lg, bin_dir)))(stamp, tree, log)
        except Exception as exc:   # sbe: allow-silent the judge's own failure IS the row: it is refused and named, never passed
            ok, why = False, "the judge could not run: %s: %s" % (type(exc).__name__, str(exc)[:160])
        detail = str(why)[:300] if why else "the judge gave no reason"
        if ok is not True:   # a refusal keeps the gate's log past the box (LR1.d); the path sits on the REFUSED line
            log.close()
            kept = keep_gate_log(box, log_path)
            if kept:
                detail = "%s (gate log %s)" % (detail[:200], kept)
        if keep:
            detail += " (kept at %s, judge log %s)" % (tree, log_path)
        return [{"name": "landing judge", "exit_code": 0 if ok is True else 1, "detail": detail}]
    finally:
        if log is not None:
            log.close()
        if made and not keep:
            _git(["worktree", "remove", "--force", tree])
            _git(["worktree", "prune"])
        if not keep:
            shutil.rmtree(box, ignore_errors=True)


def main():
    if "--selftest" in sys.argv:
        return selftest()
    install_signal_raisers()   # a TERM during the run unwinds every finally: no leaked worktree, no orphaned child group
    spec = load_spec()
    if spec is None:
        print("CANARY NO-DATA: docs/plan/loop-canary.json cannot be read or lacks sub, base_commit or build"); return 3
    missing = missing_fixture_files(spec)
    if missing:
        for m in missing:
            print(m)
        # the verdict is the LAST line: tool_stamp.prove keeps only the last line as the deploy's refusal reason, and
        # the deploy canary test reads CANARY NO-DATA there (the file list after it hid the verdict, 2026-10-14)
        print("CANARY NO-DATA: %d fixture build file(s) missing, no fixture was replayed" % len(missing))
        return 3
    results = []
    for fx in fixtures_of(spec):
        rows = stages(fx, real_run)
        print("== fixture %s at %s" % (fx["sub"], fx["base_commit"]))
        for n, s, d in rows:
            print("%-8s %-13s %s" % (s, n, d))
        results.append((fx["sub"], rows))
        if verdict(rows)[1] != 0: break   # the first refused or unreadable fixture ends the canary: a run may not start
    line, code = verdict_set(results); print(line)
    if code != 0:
        return code   # the replay refused or has no data: the whole gate is not spent on a launch that is already refused
    # THE LANDING STAGE (LR1.d): one tiny real change through the live judge. No rows is NO-DATA, never a pass.
    if "--landing" not in sys.argv:
        return code
    keep = "--keep" in sys.argv
    run = "canary-%d" % os.getpid()
    rows = []
    rows.extend(landing_stage(spec, run, keep))
    if not rows:
        print("CANARY NO-DATA: the landing stage could not run (the deployed stamp, its revision or the sandbox; the reason is on stderr)")
        return 3  # NO-DATA
    for row in rows:
        print("%-8s %-13s %s" % ("OK" if row.get("exit_code") == 0 else "REFUSED", row.get("name"), row.get("detail")))
        code = row.get("exit_code")
        if isinstance(code, bool) or not isinstance(code, int):   # a row that does not say 0 or a number is not a pass
            print("CANARY REFUSED at %s: the row carries no exit code (%r)" % (row.get("name"), code))
            return 1
        if row.get("exit_code"):
            print("CANARY REFUSED at %s: %s" % (row.get("name"), row.get("detail")))
            return 1
    print("%s (%d judge row(s))" % (LANDING_OK, len(rows)))
    return 0


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
    r = tempfile.mkdtemp(prefix="canary-plan-"); os.makedirs(os.path.join(r, "docs", "plan"))
    with open(os.path.join(r, FIXTURE_PLAN), "w", encoding="utf-8") as fh: json.dump({"units": [{"id": "x"}]}, fh)
    # a real run sets BROTHER_WBS before land_batch is imported, so land_batch.PLAN IS the run's plan: model both
    import land_batch
    saved = os.environ.get("BROTHER_WBS"); saved_plan = land_batch.PLAN
    os.environ["BROTHER_WBS"] = land_batch.PLAN = "docs/plan/OTHER.json"
    try:
        read = read_fixture_plan(spec, r)
    except OSError:
        read = None
    finally:
        land_batch.PLAN = saved_plan
        if saved is None: os.environ.pop("BROTHER_WBS", None)
        else: os.environ["BROTHER_WBS"] = saved
    try:
        fixture_plan(dict(spec, plan="/etc/x.json")); bad_refused = False
    except ValueError:
        bad_refused = True
    bad_rows = _stages(dict(spec, plan="/etc/x.json"), ok, False, None)
    shutil.rmtree(r, ignore_errors=True)
    cases += [("the spec gate reads the fixture's plan, not BROTHER_WBS (N2, the 00:54 refusal)", read == {"units": [{"id": "x"}]}),
              ("a bad fixture plan is refused by the selector, never read", bad_refused),
              ("a bad fixture plan reads REFUSED at the spec gate stage", (st(bad_rows, "spec gate") or ("",))[0] == "REFUSED")]
    failed = [n for n, good in cases if not good]
    print("selftest: %d cases, %s" % (len(cases), "OK" if not failed else "FAILED: " + ", ".join(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
