#!/usr/bin/env python3
"""ONE pass in ONE command, at most 25 lines: parity, load, plan counts, builds ready to land, runners that need a fact, council.
usage (repo root of the launch worktree): pass_digest.py [--no-fetch]      pass_digest.py --selftest
Counts and the first 3 items only; details stay on disk. An unreadable source prints NO-DATA, never a zero and never a pass.
The READY line is the exact argument list for land_batch.py."""
import collections, json, os, re, subprocess, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import plan_store, unit_ledger  # noqa: E402  (the one landed test, and the one runs folder lister)
EV = os.path.expanduser(os.environ.get("BROTHER_EVIDENCE") or "~/.claude/evidence"); RUNS = os.path.join(EV, "unit-runs")
REMOTE, BRANCH = "hub", "refactor/brother-unified-1.1"   # fixture defaults for the pure helpers; main() replaces both from upstream()
def upstream():
    """(remote, branch) from the checkout's configured upstream, or None. A literal remote and branch were true on one
    laptop and false in every other clone; nothing pushes or compares without a real upstream (owner, 2026-09-22)."""
    try:
        r = subprocess.run(["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    up = r.stdout.strip()
    return tuple(up.split("/", 1)) if r.returncode == 0 and "/" in up else None
PLAN = "docs/plan/BROTHER-1.1.0-LAUNCH-WBS.json"

def _dir_time(path, mtime=None):
    """When a run folder was last active. The folder name carries only HHMMSS, which WRAPS AT MIDNIGHT: sorting
    those strings puts yesterday's 220142 after today's 023623, so a run made after midnight reads as older than
    the previous evening's and the tool answers with a stale status. Measured 2026-09-21: D1.5, L3.1 and L1.1
    each had a READY build from this morning while the name sort returned a WITHHELD or EXHAUSTED folder from the
    night before, so finished work was invisible and the units were reported as needing a human fact. The
    filesystem knows the real time, so ask it, and fall back to nothing rather than to the stamp.
    Same defect, same fix, as dir_time() in scripts/brother_pass.py and the mtime path in loop_done.py."""
    try:
        return (mtime or os.path.getmtime)(path)
    except OSError:
        return 0.0

def newest_status(run_dirs, read):
    """{sub: (state, rest)} from the NEWEST run folder of each sub unit; a folder with no STATUS is RUNNING.

    Hostile input is refused by this module, never by the interpreter: a run_dirs that is not a list, a
    reader that is not callable, a run folder that is not a string and a read that does not return text
    all raise ValueError naming the offender. A STATUS that cannot be read (a directory sits where the
    file belongs, or a permissions error) is corrupt input and is refused by name too, because reading it
    away as RUNNING would let a broken run folder pass for a live one."""
    if not isinstance(run_dirs, (list, tuple, set, frozenset)):
        raise ValueError("newest_status needs a list of run folders, got %s" % type(run_dirs).__name__)
    if not callable(read):
        raise ValueError("newest_status needs a reader callable, got %s" % type(read).__name__)
    dirs = list(run_dirs)
    for d in dirs:
        if not isinstance(d, str):
            raise ValueError("newest_status: run folder is not a string: %r" % (d,))
    last = {}
    for d in sorted(dirs, key=lambda p: (_dir_time(p), os.path.basename(p.rstrip("/")))):
        m = re.match(r"(.+)-(\d{6})$", os.path.basename(d.rstrip("/")))
        if not m: continue
        try:
            t = read(d)
        except OSError as exc:
            raise ValueError("newest_status: STATUS of %s is unreadable: %s" % (d, exc))
        if not isinstance(t, str):
            raise ValueError("newest_status: %s read as %s, not text" % (d, type(t).__name__))
        w = t.split(None, 1) if t else []
        last[m.group(1)] = (w[0], w[1].strip() if len(w) > 1 else "") if w else ("RUNNING", "")
    return last

def landed_subs(plan):
    """Sub units the plan evidence records as landed. A plan that is not a mapping, a units field that is
    not a list, a unit that is not a mapping, a sub_units field or evidence that is not text and a sub unit
    id that is not a string all raise ValueError naming the offender, never AttributeError or TypeError."""
    if not isinstance(plan, dict):
        raise ValueError("landed_subs needs the plan dict, got %s" % type(plan).__name__)
    units = plan.get("units")
    if units is None:
        units = []
    if not isinstance(units, (list, tuple)):
        raise ValueError("landed_subs: plan units is %s, not a list" % type(units).__name__)
    out = set()
    for u in units:
        if not isinstance(u, dict):
            raise ValueError("landed_subs: a unit is %s, not a dict" % type(u).__name__)
        subs = u.get("sub_units")
        if subs is None:
            subs = []
        if not isinstance(subs, (list, tuple)):
            raise ValueError("landed_subs: sub_units is %s, not a list" % type(subs).__name__)
        evidence = u.get("evidence")
        if evidence is None:
            evidence = ""
        if not isinstance(evidence, str):
            raise ValueError("landed_subs: evidence is %s, not text" % type(evidence).__name__)
        for s in subs:
            if not isinstance(s, str):
                raise ValueError("landed_subs: a sub unit id is %s, not a string" % type(s).__name__)
            if plan_store.sub_landed(s, evidence): out.add(s)
    return out

def partition(last, landed, alive_subs, closed=frozenset()):
    """ready build paths, then sub units needing a fact, then stalled ones (RUNNING with no live process).

    READY-UNPROBED IS ITS OWN PARTITION AND NEVER `ready`, and this reader always compared the status WORD
    rather than a prefix. On 2026-09-21 runner_pool and loop_done were corrected to agree with it: a
    prefix match there made an unprobed build read as a READY build waiting to land, which held its unit
    out of the pool forever while land_batch refused the same string. The three readers now share one
    contract: exactly READY waits to land, READY-UNPROBED is an unknown that probe_round re-probes and a
    fresh runner otherwise rebuilds.

    A SUB UNIT OF A CLOSED UNIT IS NOT A BLOCKER, whatever its run folder says. Measured 2026-09-21: D0 closed
    at about 12:55 and D0.3 kept reading STALLED for the rest of the day, because one old run folder has no
    STATUS file and a missing STATUS means RUNNING. The board therefore showed a blocker that no action could
    ever clear, on a unit that was finished. A board that reports work on closed units teaches a reader to
    discount it, which is worse than reporting nothing."""
    if not isinstance(last, dict):
        raise ValueError("partition needs the newest_status dict, got %s" % type(last).__name__)
    for _label, _members in (("landed", landed), ("alive_subs", alive_subs), ("closed", closed)):
        if not isinstance(_members, (set, frozenset)):
            raise ValueError("partition needs %s as a set of names, got %s" % (_label, type(_members).__name__))
    for _sub, _row in last.items():
        if not isinstance(_sub, str) or not isinstance(_row, (tuple, list)) or len(_row) != 2 or not (
                isinstance(_row[0], str) and isinstance(_row[1], str)):
            raise ValueError("partition: %s has no (state, rest) text pair: %r" % (_sub, _row))
    ready = sorted(rest.split()[0] for s, (st, rest) in last.items()
                   if st == "READY" and s not in landed and s not in closed and rest)
    unprobed = sorted(s for s, (st, _) in last.items()
                      if st == "READY-UNPROBED" and s not in landed and s not in closed)
    need = sorted(s for s, (st, _) in last.items()
                  if st in ("EXHAUSTED", "WITHHELD") and s not in landed and s not in closed)
    stalled = sorted(s for s, (st, _) in last.items()
                     if st == "RUNNING" and s not in alive_subs and s not in closed)
    return ready, unprobed, need, stalled


def skipped_ready(last, landed, closed):
    """The READY sub units this pass does not offer to land: their unit is closed, or their build already
    landed. REQ-H-SKIPPED: a skipped build is NAMED, never hidden behind a zero. Sorted. A READY status that
    carries no build path is not a build waiting to land, so it is not counted. Unknown, corrupt or missing
    input raises ValueError: it never reads as nothing skipped."""
    if not isinstance(last, dict):
        raise ValueError("skipped_ready needs a dict of sub unit to (state, rest), got %s" % type(last).__name__)
    for label, members in (("landed", landed), ("closed", closed)):
        if not isinstance(members, (set, frozenset)):
            raise ValueError("skipped_ready needs %s as a set of sub unit ids, got %s" % (label, type(members).__name__))
        for m in members:
            if not isinstance(m, str):
                raise ValueError("skipped_ready: %s holds a %s, not a sub unit id" % (label, type(m).__name__))
    out = []
    for sub, row in last.items():
        if not isinstance(sub, str):
            raise ValueError("skipped_ready: sub unit id is not a string: %r" % (sub,))
        if not isinstance(row, (tuple, list)) or len(row) != 2:
            raise ValueError("skipped_ready: %s has no (state, rest) pair: %r" % (sub, row))
        state, rest = row
        if not isinstance(state, str) or not isinstance(rest, str):
            raise ValueError("skipped_ready: %s has a state or rest that is not a string: %r" % (sub, row))
        if state == "READY" and rest.strip() and (sub in landed or sub in closed):
            out.append(sub)
    return sorted(out)


def out_of_scope_subs(plan, scope_src=None):
    """Sub units of every unit the run's scope does not admit: the same rule runner_pool applies (re.match of
    BROTHER_SCOPE, default "." admits all). 2026-10-03: a run scoped ^(CV1|ACC2)$ offered an older HP1.d build to the
    lander, which would have landed out of scope work inside a scoped run (and inside a proof, whose scope must equal the
    intake's). An unreadable scope admits nothing to the lander rather than everything."""
    src = os.environ.get("BROTHER_SCOPE") if scope_src is None else scope_src
    try:
        rx = re.compile(src or ".")
    except re.error:
        return {s for u in plan.get("units", []) for s in (u.get("sub_units") or [])}
    out = {s for u in plan.get("units", []) if not rx.match(str(u.get("id", ""))) for s in (u.get("sub_units") or [])}
    # a sub unit the owner kept from the loop (plan_store.supply_hold, 2026-10-04) is never offered to the lander either
    return out | {s for u in plan.get("units", []) for s in (u.get("sub_units") or []) if isinstance(s, str) and plan_store.supply_hold(s)}


def closed_subs(plan):
    """Every sub unit belonging to a unit the plan records as DONE.

    A plan that is not a mapping, a units field that is not a list, a unit that is not a mapping, a
    sub_units field that is not a list and a sub unit id that is not a string all raise ValueError naming
    the offender. A sub unit id that cannot be put in the set is corrupt plan data and is refused here,
    where every caller routes through, rather than surfacing as an unhashable TypeError."""
    if not isinstance(plan, dict):
        raise ValueError("closed_subs needs the plan dict, got %s" % type(plan).__name__)
    units = plan.get("units")
    if units is None:
        units = []
    if not isinstance(units, (list, tuple)):
        raise ValueError("closed_subs: plan units is %s, not a list" % type(units).__name__)
    out = set()
    for u in units:
        if not isinstance(u, dict):
            raise ValueError("closed_subs: a unit is %s, not a dict" % type(u).__name__)
        if u.get("state") == "DONE":
            subs = u.get("sub_units")
            if subs is None:
                subs = []
            if not isinstance(subs, (list, tuple)):
                raise ValueError("closed_subs: sub_units is %s, not a list" % type(subs).__name__)
            for s in subs:
                if not isinstance(s, str):
                    raise ValueError("closed_subs: a sub unit id is %s, not a string" % type(s).__name__)
                out.add(s)
    return out

def first3(xs):
    """The count, then the first three names. A value that is not a sized sequence of strings (None, a
    number, a mapping, any object without a length) raises ValueError naming the offender: the caller reads
    the refusal, not a TypeError about NoneType."""
    if not isinstance(xs, (list, tuple, str, range)):
        raise ValueError("first3 needs a sized sequence of strings, got %s" % type(xs).__name__)
    return "%d%s" % (len(xs), (": " + ", ".join(xs[:3]) + (" ..." if len(xs) > 3 else "")) if xs else "")

def selftest():
    """Answer the question even when a case RAISES. Measured 2026-09-22: eleven selftests in this
    directory exited 1 with a bare traceback and no verdict, so a pipeline reading the exit code and
    a human reading the text described the same run differently. Cases are built EAGERLY, so one
    raising expression takes the whole run with it; this wrapper is what turns that into a readable
    refusal. It does not make a broken module pass: it still returns non zero."""
    try:
        return _selftest_body()
    except Exception as exc:
        print("selftest: FAILED before it could finish, %s: %s" % (type(exc).__name__, str(exc)[:120]))
        return 1


def _selftest_body():
    texts = {"/r/D4.c-185348": "EXHAUSTED after 3 rounds", "/r/D4.c-195709": "READY /b/D4.c-r1-build.json (round 2)", "/r/D1.2-195926": "READY /b/D1.2-r1-build.json",
             "/r/L5a-1-195709": "READY-UNPROBED /b/L5a-1-r1-build.json", "/r/D7-C-195710": "EXHAUSTED after 3", "/r/D6.2-200000": "", "/r/D9.a-200001": "", "/r/junk": "READY /b/x"}
    last = newest_status(list(texts), lambda d: texts[d])
    plan = {"units": [{"id": "D1", "sub_units": ["D1.2"], "evidence": "D1.2 landed 2026-09-20."}, {"id": "D4", "sub_units": ["D4.c"], "evidence": "D4.c spec. Something landed"}]}
    ready, unprobed, need, stalled = partition(last, landed_subs(plan), {"D9.a"})
    plan_closed = {"units": [{"id": "Z", "state": "DONE", "sub_units": ["Z.1"]},
                             {"id": "Y", "state": "PARTIAL", "sub_units": ["Y.1"]}]}
    closed_only = partition({"Z.1": ("RUNNING", ""), "Y.1": ("RUNNING", "")}, set(), set(),
                            closed_subs(plan_closed))[3]
    cases = [("newest folder wins", last["D4.c"][0] == "READY"), ("folder without stamp ignored", "junk" not in last),
             ("a sub unit the owner kept from the loop is out of the lander's scope even when its unit is admitted",
              out_of_scope_subs({"units": [{"id": "MG1", "sub_units": ["MG1.a", "MG1.e"]}]}, "^(MG1)$") == {"MG1.a"}),
             ("landed build not offered again", ready == ["/b/D4.c-r1-build.json"]), ("a full stop breaks the landed match", "D4.c" not in landed_subs(plan)),
             ("unprobed is never ready", unprobed == ["L5a-1"] and not any("L5a" in r for r in ready)), ("needs a fact", need == ["D7-C"]),
             ("dead runner is a stall, live one is not", stalled == ["D6.2"]),
             ("a sub unit of a CLOSED unit is never a blocker", closed_only == ["Y.1"]),
             ("closed_subs reads only DONE units", closed_subs(plan_closed) == {"Z.1"}), ("first3 truncates", first3(list("abcde")) == "5: a, b, c ...")]
    import tempfile
    _d = tempfile.mkdtemp(prefix="upstream-"); _g = lambda *a: subprocess.run(["git"] + list(a), capture_output=True, text=True, timeout=60)
    _bare = os.path.join(_d, "r.git"); _clone = os.path.join(_d, "c"); _g("init", "-q", "--bare", "-b", "main", _bare); _g("clone", "-q", _bare, _clone)
    for k, v in (("user.email", "t@t"), ("user.name", "t"), ("commit.gpgsign", "false")): _g("-C", _clone, "config", k, v)
    _g("-C", _clone, "commit", "-q", "--allow-empty", "-m", "a"); _g("-C", _clone, "push", "-q", "-u", "origin", "main")
    _cwd = os.getcwd()
    try:
        os.chdir(_clone); _with = upstream(); _g("-C", _clone, "branch", "--unset-upstream"); _without = upstream()
    finally:
        os.chdir(_cwd)
    cases.append(("upstream() reads (remote, branch) from the checkout, and None with no upstream", _with == ("origin", "main") and _without is None))
    # THE ENTRY POINT, run for real: main() in a throwaway clone with a plan file. On 2026-09-22 14:10 a string
    # formatting error in main() crashed every pass while this selftest, which covered only the helpers, read OK.
    _plan = os.path.join(_clone, "docs", "plan"); os.makedirs(_plan)
    with open(os.path.join(_plan, "BROTHER-1.1.0-LAUNCH-WBS.json"), "w") as f:
        json.dump({"units": [{"id": "X1", "state": "DONE", "subunits": []}, {"id": "X2", "state": "PARTIAL", "subunits": []}]}, f)
    _g("-C", _clone, "branch", "--set-upstream-to=origin/main")
    _env = dict(os.environ, BROTHER_EVIDENCE=os.path.join(_d, "ev"))
    _r = subprocess.run([sys.executable, "-B", os.path.abspath(__file__), "--no-fetch"], cwd=_clone, capture_output=True, text=True, timeout=120, env=_env)
    _lines = _r.stdout.splitlines()
    cases.append(("the entry point: main() runs end to end in a clone, exit 0, PARITY names the real remote, PLAN counts the units",
                  _r.returncode == 0 and any(l.startswith("PARITY  branch main | local ") and " origin " in l and "behind origin main" in l for l in _lines)
                  and any(l.startswith("PLAN    units 2 | DONE 1 | PARTIAL 1") for l in _lines) and any(l.startswith("READY   0") for l in _lines) and any(l.startswith("SPECS   NO-DATA") for l in _lines)))
    _g("-C", _clone, "branch", "--unset-upstream")
    _r2 = subprocess.run([sys.executable, "-B", os.path.abspath(__file__), "--no-fetch"], cwd=_clone, capture_output=True, text=True, timeout=120, env=_env)
    cases.append(("the entry point: no upstream prints PARITY NO-DATA with the command that sets one, and still exits 0",
                  _r2.returncode == 0 and any("PARITY  NO-DATA" in l and "--set-upstream-to" in l for l in _r2.stdout.splitlines())))
    bad = [n for n, good in cases if not good]
    print("selftest: %d cases, %s" % (len(cases), "OK" if not bad else "FAILED: " + ", ".join(bad))); return 1 if bad else 0

def out(cmd, timeout=120):
    try: return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired): return None

def main():
    if "--selftest" in sys.argv: return selftest()
    r = out(["date", "+%Y-%m-%d %H:%M %Z"]); print("TIME    " + (r.stdout.strip() if r else "NO-DATA"))
    global REMOTE, BRANCH
    up = upstream()
    if not up:
        print("PARITY  NO-DATA: this branch has no upstream, so there is nothing to compare against: git branch --set-upstream-to <remote>/<branch>")
    REMOTE, BRANCH = up or (REMOTE, BRANCH)
    fetched = "--no-fetch" in sys.argv or (out(["git", "fetch", "-q", REMOTE], 300) or subprocess.CompletedProcess([], 1)).returncode == 0
    g = lambda *a: ((out(["git"] + list(a)) or subprocess.CompletedProcess([], 1, "", "")).stdout or "").strip()
    dirty = len([l for l in g("status", "--porcelain").splitlines() if l.strip()])
    print("PARITY  branch %s%s | local %s %s %s | behind %s main %s | tree %s%s" % (g("branch", "--show-current"), "" if g("branch", "--show-current") == BRANCH else " (NOT the launch branch)",
          g("rev-parse", "--short", "HEAD") or "NO-DATA", REMOTE, g("rev-parse", "--short", REMOTE + "/" + BRANCH) or "NO-DATA", REMOTE, g("rev-list", "--count", "HEAD..%s/main" % REMOTE) or "NO-DATA",
          "clean" if not dirty else "%d changed paths" % dirty, "" if fetched else " | FETCH FAILED: %s figures are stale" % REMOTE))
    ps = (out(["ps", "-eo", "command"]) or subprocess.CompletedProcess([], 1, "", "")).stdout or ""
    alive = re.findall(r"unit_runner\.py (\S+) (\S+)", ps)
    try: load = "%.1f %.1f %.1f" % os.getloadavg()
    except OSError: load = "NO-DATA"
    print("LOAD    %s on %s cores | runners alive %s" % (load, os.cpu_count(), first3(sorted(s for _, s in alive))))
    try: plan = json.load(open(PLAN, encoding="utf-8"))
    except (OSError, ValueError, TypeError): plan = None
    if plan is None: print("PLAN    NO-DATA: %s unreadable from %s" % (PLAN, os.getcwd())); return 1
    c = collections.Counter(u.get("state") for u in plan["units"])
    print("PLAN    units %d | DONE %d | %s" % (len(plan["units"]), c.get("DONE", 0), " ".join("%s %d" % kv for kv in sorted(c.items(), key=str) if kv[0] != "DONE")))
    def read(d):
        # NO STATUS YET is a live run. A STATUS that exists and cannot be read is NOT one: it raises, and
        # newest_status refuses it by name (finding 8, 2026-09-27: reading it away as RUNNING printed a stall).
        try:
            with open(os.path.join(d, "STATUS"), encoding="utf-8") as f: return f.read()
        except FileNotFoundError: return ""
    try:
        last = newest_status(unit_ledger.run_dirs(RUNS), read)
    except (OSError, ValueError) as exc:
        # FINDING 8, 2026-09-27: glob() swallowed a permission error on this folder, so the digest printed READY 0,
        # STALLED 0 and exited 0 over a folder holding a READY build. Unknown is not zero.
        print("RUNS    NO-DATA: %s is unreadable (%s); READY, UNPROBED, NEEDFACT and STALLED are unknown, not zero" % (RUNS, str(exc)[:160]))
        last = None
    if last is not None:
        outside = out_of_scope_subs(plan)
        held = sorted(s for s, (st, rest) in last.items() if st == "READY" and rest.strip() and s in outside and s not in landed_subs(plan))
        if held: print("READY outside this run's scope, not offered to land: %s" % ", ".join(held))
        ready, unprobed, need, stalled = partition(last, landed_subs(plan), {s for _, s in alive}, closed_subs(plan) | outside)
        print("READY   %d to land%s" % (len(ready), ":" if ready else ""))
        for p in ready[:8]: print("        " + p)
        if len(ready) > 8: print("        ... %d more" % (len(ready) - 8))
        skipped = skipped_ready(last, landed_subs(plan), closed_subs(plan))
        if skipped: print("READY on closed units: %d skipped: %s" % (len(skipped), ", ".join(skipped)))
        print("UNPROBED %s (never landed: probe_round re-probes it, then a fresh runner rebuilds it)" % first3(unprobed))
        print("NEEDFACT %s (EXHAUSTED or WITHHELD: diagnostician lane, then RUNNER_HINT)" % first3(need))
        print("STALLED %s (RUNNING with no live process)" % first3(stalled))
    try:
        with open(os.path.join(EV, "spec-council.json"), encoding="utf-8") as f: cs = collections.Counter(v.get("state") for v in json.load(f).values())
        print("COUNCIL " + " | ".join("%s %d" % kv for kv in sorted(cs.items(), key=str)))
    except (OSError, ValueError, AttributeError): print("COUNCIL NO-DATA: spec-council.json unreadable")
    try:
        with open(os.path.join(EV, "spec-scores.json"), encoding="utf-8") as f: sc = json.load(f)
        low = sorted(k for k, v in sc.items() if not isinstance(v.get("score"), int) or v["score"] < 9)
        print("SPECS   scored %d | under 9: %s" % (len(sc), first3(low)))
    except (OSError, ValueError, AttributeError): print("SPECS   NO-DATA: spec-scores.json unreadable")
    return 0 if last is not None else 1
if __name__ == "__main__": sys.exit(main())
