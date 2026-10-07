#!/usr/bin/env python3
"""Can the loop stop? Distinguish FINISHED from merely IDLE, so an unattended run ends instead of spinning.

usage (repo root): python3 -B loop_done.py [--scope REGEX]        python3 -B loop_done.py --selftest
Exit 0 FINISHED (stop), 1 WORKING (work remains), 2 NO-DATA (state unreadable: never stop on this),
3 STALLED (work remains but NONE of it is admissible: a human or a rubric change is needed, not a pass).

WHY IT EXISTS. `brother_pass` already has a NOTHING rung, but read its own words: "every remaining sub unit is
running, held by the council, under the spec bar or waiting on a fact". That is IDLE, a temporary state that a
landing or a diagnosed fact will clear, and it is the single most common state of a healthy loop mid flight.
`loop_pass.sh` exits 0 unconditionally. So nothing the loop emits has ever meant "stop", and an unattended run
had no way to end: it would keep waking, re-reading the plan and paying for a pass, forever, long after the
last unit closed.

FINISHED is a much stronger claim than IDLE and it is deliberately hard to earn. Every one of these must hold:
  1. every in scope unit reads DONE **and carries evidence** (a DONE with an empty evidence field is a CLAIM,
     which the estate's counting rule excludes from every percentage, so it cannot end a run either);
  2. no build is sitting READY and unlanded, since a finished board with unlanded work is not finished;
  3. no unit runner process is alive, because a live runner may be seconds from producing one;
  4. the plan itself was readable, and read in THIS pass.
Anything unreadable is NO-DATA and the loop keeps running. The asymmetry is deliberate: stopping early abandons
real work, while one extra pass costs a few cents, so every unknown fails toward WORKING."""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # THIS directory's copy
import plan_store  # noqa: E402  (the one landed test every loop reader shares: plan_store.sub_landed)
import loop_procs  # noqa: E402  (the one process ownership rule every loop reader and stop shares)

PLAN = "docs/plan/BROTHER-1.1.0-LAUNCH-WBS.json"
RUNS = os.path.expanduser("~/.claude/evidence/unit-runs")


def live_runners(snap=None):
    """How many unit runners are alive. Read from the process table, never assumed from a status file: a status
    file says what a run last wrote, not whether it is still going. ONE OWNERSHIP RULE (X2 finding 7, 2026-09-27):
    the count is loop_procs', the program position a stop signals by, so a viewer whose ARGUMENT names
    bin/unit_runner.py is not a runner here either (it kept a finished board WORKING). snap: loop_procs rows (pid,
    ppid, pgid, command); None reads the table. An unreadable table, refused, empty, or with a malformed row, is None:
    unknown, and unknown must not read as zero (finding 9); an undecodable byte is not an unreadable table."""
    return loop_procs.live_runner_count(snap)


def ready_builds(runs_dir=RUNS, listdir=None, read=None, landed=None, scope=None, mtime=None, unit_of=None):
    """Sub units whose NEWEST run left a READY build that has NOT already landed.
    unit_of maps a sub unit id to its unit id: the scope regex is the pool's, matched against UNIT ids (2026-10-04: an
    anchored unit scope matched no sub unit id, so every READY build vanished and WORKING read STALLED); without the map
    the sub unit id is matched, as before. A sub unit the owner kept from the loop (plan_store.supply_hold) never counts.

    Three corrections, each one a defect this function had on its first run and each one already a recorded
    lesson elsewhere in this estate:
      - A run folder keeps its READY status forever after its build lands, so counting every READY folder
        reported 53 builds waiting while the digest correctly reported 0. Sub units named as landed in the
        plan's evidence are excluded, exactly as pass_digest does it.
      - Only the NEWEST run of a sub unit counts, and newest is decided by MODIFICATION TIME, never by the
        HHMMSS stamp in the folder name, because that stamp WRAPS AT MIDNIGHT and silently reorders a night's
        work.
      - A READY build outside the scope being asked about must not block a scoped stop.
    An unreadable run history returns None, which the verdict treats as NO-DATA rather than as nothing ready."""
    listdir = listdir or (lambda d: sorted(os.listdir(d)))
    read = read or (lambda p: open(p, encoding="utf-8").read())
    mtime = mtime or (lambda p: os.path.getmtime(p))
    landed = landed or set()
    rx = re.compile(scope) if scope else None
    newest = {}
    try:
        entries = listdir(runs_dir)
    except OSError:  # sbe: allow-silent None is NO-DATA by this function's contract, and verdict() refuses to close on it
        return None
    for name in entries:
        sub = re.sub(r"-\d{6}$", "", name)
        if sub in landed:
            continue
        if rx and not rx.match(unit_of.get(sub, sub) if unit_of else sub):
            continue
        if plan_store.supply_hold(sub):
            continue
        st = os.path.join(runs_dir, name, "STATUS")
        try:
            first = read(st).split()[0]
        except (OSError, IndexError):
            continue
        try:
            when = mtime(os.path.join(runs_dir, name))
        except OSError:
            when = 0.0
        if sub not in newest or when >= newest[sub][0]:
            newest[sub] = (when, first)
    # EXACTLY READY, NEVER A PREFIX. `v.startswith("READY")` was also true of READY-UNPROBED, so an
    # unprobed build blocked the FINISHED verdict while runner_pool, reading the same prefix, refused to
    # start a runner that could clear it: the loop could neither finish nor work. land_batch and
    # pass_digest always compared the status WORD; these two readers now do the same, which is the whole
    # agreement. An unprobed build is progress that is NOT waiting to land, so it does not belong here:
    # while its unit is unfinished the `unfinished` test below keeps the verdict WORKING anyway, and
    # probe_round or a fresh runner is what moves it on.
    return {s: v for s, (_, v) in newest.items() if v == "READY"}


def landed_subs(plan):
    """Every sub unit the plan's own evidence names as landed, read by plan_store.sub_landed, the one landed test."""
    out = set()
    for u in plan.get("units") or []:
        ev = u.get("evidence") or ""
        for s in (u.get("sub_units") or []):
            sid = s["id"] if isinstance(s, dict) else s
            if plan_store.sub_landed(sid, ev):
                out.add(sid)
    return out


def eligible_count(plan, scores, floor=8, scope=None):
    """How many sub units a worker could legally be given right now. Zero, with work remaining and nothing in
    flight, is the STALLED condition. An unreadable score mapping returns None, which is NO-DATA: a loop must
    never conclude 'nothing is eligible' from a file it could not read."""
    if scores is None:
        return None
    rx = re.compile(scope) if scope else None
    landed = landed_subs(plan)
    n = 0
    for u in plan.get("units") or []:
        if u.get("state") == "DONE" or plan_store.release_refusal(u.get("state")) or (rx and not rx.match(u.get("id") or "")):
            continue   # DEFERRED or RETIRED: runner_pool.admissible gives it no worker, so it is not eligible work either
        for s_ in (u.get("sub_units") or []):
            sid = s_["id"] if isinstance(s_, dict) else s_
            if sid in landed or plan_store.supply_hold(sid):   # landed, or kept from the loop by the owner (2026-10-04)
                continue
            sc = (scores.get(sid) or {}).get("score")
            if isinstance(sc, (int, float)) and sc >= floor and not routed(sid, u.get("spec")):
                n += 1
    return n


def routed(sid, spec):
    """True when runner_pool's admission screen sends this sub unit to the reviewed route, so no worker can get it
    (review 2026-10-04: without this a run whose every remaining sub unit is routed ended UNPRODUCTIVE, not STALLED
    with its reason). The pool's own functions are asked, never a copy; anything unreadable answers False, which
    keeps the verdict WORKING, the direction this module's unknowns fail toward."""
    if not isinstance(spec, str):
        return False
    try:
        import runner_pool
        return bool(runner_pool.reviewed_route(runner_pool.write_set(sid, spec)))
    except Exception:   # sbe: allow-silent an unreadable screen is not "routed": the verdict stays WORKING
        return False


def unfinished(plan, scope=None):
    """[(unit id, why it is not finished)] for every in scope unit that cannot count as done."""
    rx = re.compile(scope) if scope else None
    out = []
    for u in plan.get("units") or []:
        uid = u.get("id") or "?"
        if rx and not rx.match(uid):
            continue
        state = u.get("state")
        if state != "DONE":
            out.append((uid, "state is %s" % (state or "unset")))
        elif not (u.get("evidence") or "").strip():
            # the estate's counting rule: a DONE with no evidence is a CLAIM, excluded from every percentage,
            # so it cannot be what ends a run either
            out.append((uid, "reads DONE but carries no evidence, which is a claim, not a completion"))
    return out


def verdict(plan, ready, runners, scope=None, eligible=None):
    """('FINISHED'|'WORKING'|'STALLED'|'NO-DATA', reason).

    THE TERNARY, added 2026-09-21 after three independent adversarial reviews (one local reasoning model, two
    outside models, briefed separately on a generalised description) each raised the SAME defect without seeing
    each other's answers: FINISHED and WORKING are not exhaustive, and the gap between them is a livelock.

    The deadlock is concrete. The estate's own admission rule refuses a worker to any sub unit whose
    specification scores under the bar. If every remaining sub unit sits under that bar, the eligible set is
    empty while the unfinished set is not. The loop is then not permitted to work and not permitted to finish.
    A loop testing only 'is anything unfinished' spins forever, waking and paying; a loop testing only 'is the
    pool empty' declares a false completion, which is worse.

    STALLED is that state named: unfinished work exists and NONE of it is admissible. It is not a failure and
    not a completion. It is the one state that needs a human or a change of rubric, and it must be reported
    rather than slept through. `eligible` is the count of sub units a worker could legally be given right now;
    passing None means the caller could not compute it, which is NO-DATA rather than a licence to spin."""
    if not isinstance(plan, dict) or not plan.get("units"):
        return "NO-DATA", "the plan is unreadable or has no units, so nothing may be concluded from it"
    if ready is None:
        return "NO-DATA", "the run history could not be read, so a READY build cannot be ruled out"
    if runners is None:
        return "NO-DATA", "the process table could not be read, so a live runner cannot be ruled out"
    left = unfinished(plan, scope)
    if not left and not ready and not runners:
        return "FINISHED", "every in scope unit reads DONE with evidence, nothing is READY, no runner is alive"
    # work remains. Is any of it admissible? A READY build or a live runner IS progress in flight, so neither
    # can be a stall however empty the eligible set looks.
    if not ready and not runners and eligible == 0 and left:
        return "STALLED", ("%d unit(s) unfinished and NOT ONE sub unit is eligible for a worker: nothing is "
                           "READY, no runner is alive, and every remaining sub unit is under the bar or held. "
                           "This needs a decision or a change of rubric, not another pass." % len(left))
    if left:
        return "WORKING", "%d unit(s) not finished, first: %s (%s)" % (len(left), left[0][0], left[0][1])
    if ready:
        return "WORKING", "%d build(s) READY and unlanded: %s" % (len(ready), ", ".join(sorted(ready)[:4]))
    return "WORKING", "%d unit runner(s) still alive" % runners


def _routed_counts():
    """(eligible with a protected Owns path, eligible with a writable one) on a throwaway spec: the screen's effect on
    eligible_count, measured through the real runner_pool functions."""
    import tempfile, shutil
    d = tempfile.mkdtemp(prefix="loop-done-routed-")
    try:
        out = []
        for owned in ("scripts/cut_preflight.py", "scripts/d3_new.py"):
            spec = os.path.join(d, "D3.md")
            with open(spec, "w", encoding="utf-8") as fh:
                fh.write("# D3\n### D3.1\nOwns: `%s` (existing)\n" % owned)
            out.append(eligible_count({"units": [{"id": "D3", "state": "OPEN", "spec": spec, "sub_units": ["D3.1"], "evidence": ""}]},
                                      {"D3.1": {"score": 9}}))
        return tuple(out)
    finally:
        shutil.rmtree(d, True)


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
    done = {"id": "A", "state": "DONE", "evidence": "landed, proven"}
    claim = {"id": "B", "state": "DONE", "evidence": ""}
    open_u = {"id": "C", "state": "PARTIAL", "evidence": "x"}
    cases = [
        ("a finished board finishes", verdict({"units": [done]}, {}, 0)[0] == "FINISHED"),
        ("an open unit keeps it working", verdict({"units": [done, open_u]}, {}, 0)[0] == "WORKING"),
        ("a DONE with no evidence is a claim, not a completion",
         verdict({"units": [claim]}, {}, 0)[0] == "WORKING"),
        ("a READY unlanded build blocks the stop", verdict({"units": [done]}, {"A.1": "READY x"}, 0)[0] == "WORKING"),
        ("a live runner blocks the stop", verdict({"units": [done]}, {}, 2)[0] == "WORKING"),
        ("an unreadable plan is NO-DATA, never FINISHED", verdict({}, {}, 0)[0] == "NO-DATA"),
        ("a plan with no units is NO-DATA", verdict({"units": []}, {}, 0)[0] == "NO-DATA"),
        ("unreadable run history is NO-DATA", verdict({"units": [done]}, None, 0)[0] == "NO-DATA"),
        ("an unreadable process table is NO-DATA", verdict({"units": [done]}, {}, None)[0] == "NO-DATA"),
        ("scope limits which units must be done",
         verdict({"units": [done, {"id": "Z9", "state": "PARTIAL"}]}, {}, 0, scope=r"^A")[0] == "FINISHED"),
        ("out of scope work is not silently counted as finished in scope",
         verdict({"units": [done, {"id": "Z9", "state": "PARTIAL"}]}, {}, 0)[0] == "WORKING"),
        ("work remains and nothing is admissible is STALLED, not WORKING and not FINISHED",
         verdict({"units": [open_u]}, {}, 0, eligible=0)[0] == "STALLED"),
        ("a READY build means progress, so it is never a stall",
         verdict({"units": [open_u]}, {"C.1": "READY x"}, 0, eligible=0)[0] == "WORKING"),
        ("a live runner means progress, so it is never a stall",
         verdict({"units": [open_u]}, {}, 1, eligible=0)[0] == "WORKING"),
        ("eligible work means WORKING", verdict({"units": [open_u]}, {}, 0, eligible=4)[0] == "WORKING"),
        ("a sub unit the admission screen routes to the reviewed route is not eligible, a writable one is",
         _routed_counts() == (0, 1)),
        ("an uncomputable eligible set never stalls the loop",
         verdict({"units": [open_u]}, {}, 0, eligible=None)[0] == "WORKING"),
        ("a finished board is FINISHED even with eligible 0",
         verdict({"units": [done]}, {}, 0, eligible=0)[0] == "FINISHED"),
        ("eligible_count counts only unlanded sub units at or above the floor",
         eligible_count({"units": [{"id": "A", "sub_units": ["A.1", "A.2", "A.3"], "evidence": "A.1 landed"}]},
                        {"A.1": {"score": 10}, "A.2": {"score": 9}, "A.3": {"score": 4}}) == 1),
        ("eligible_count never counts a unit that is not in this release (DEFERRED, RETIRED)",
         eligible_count({"units": [{"id": s, "state": s, "sub_units": [s + ".1"], "evidence": ""} for s in ("DEFERRED", "RETIRED", "PARTIAL")]},
                        {"DEFERRED.1": {"score": 10}, "RETIRED.1": {"score": 10}, "PARTIAL.1": {"score": 10}}) == 1),
        ("eligible_count returns None when the scores cannot be read",
         eligible_count({"units": []}, None) is None),
        ("the reason names the first blocker", "C" in verdict({"units": [done, open_u]}, {}, 0)[1]),
        ("live_runners counts a real runner (the deployed bin copy: a runner is the loop's only from $HOME/.claude/bin or a run of this loop)",
         live_runners([dict(pid=7, ppid=1, pgid=7, command="python3 %s/.claude/bin/unit_runner.py D1 D1.2" % os.path.expanduser("~")),
                       dict(pid=8, ppid=1, pgid=8, command="something else")]) == 1),
        ("live_runners does not count an unrelated process",
         live_runners([dict(pid=7, ppid=1, pgid=7, command="bash -c echo bin/unit_runner.py")]) == 0),
        ("live_runners does not count a runner a stop would not own (not the deployed bin copy)",
         live_runners([dict(pid=7, ppid=1, pgid=7, command="python3 /x/scripts/loop/unit_runner.py D1 D1.2")]) == 0),
        ("live_runners does not count a process whose argument names a runner",
         live_runners([dict(pid=7, ppid=1, pgid=7, command="python3 /v/viewer.py /x/bin/unit_runner.py D1 D1.2")]) == 0),
        ("ready_builds matches an anchored unit scope against the sub unit's UNIT when the map is given",
         ready_builds("/r", listdir=lambda d: ["A.1-101010"], read=lambda p: "READY /x/b.json\n",
                      scope=r"^(A)$", unit_of={"A.1": "A"}, mtime=lambda p: 1.0) == {"A.1": "READY"}),
        ("a READY build of a sub unit the owner kept from the loop never waits to land",
         ready_builds("/r", listdir=lambda d: ["D2.6-101010"], read=lambda p: "READY /x/b.json\n", mtime=lambda p: 1.0) == {}),
        ("eligible_count never counts a sub unit the owner kept from the loop",
         eligible_count({"units": [{"id": "MG1", "state": "PARTIAL", "sub_units": ["MG1.a", "MG1.e"], "evidence": ""}]},
                        {"MG1.a": {"score": 10}, "MG1.e": {"score": 10}}) == 1),
        ("an emptied supply (D2, 2026-10-05) leaves no sub unit of its unit eligible",
         eligible_count({"units": [{"id": "D2", "state": "PARTIAL", "sub_units": ["D2.6", "D2.7"], "evidence": ""}]},
                        {"D2.6": {"score": 10}, "D2.7": {"score": 10}}) == 0),
        ("ready_builds reads a READY status",
         ready_builds("/r", listdir=lambda d: ["A.1-101010"], read=lambda p: "READY /x/b.json\n") == {"A.1": "READY"}),
        ("ready_builds ignores an exhausted run",
         ready_builds("/r", listdir=lambda d: ["A.1-101010"], read=lambda p: "EXHAUSTED after 5\n") == {}),
        ("an UNPROBED build is not a READY build waiting to land, so it cannot deadlock the verdict",
         ready_builds("/r", listdir=lambda d: ["A.1-101010"],
                      read=lambda p: "READY-UNPROBED /x/b.json\n") == {}),
        ("a landed sub unit no longer counts as a READY build waiting",
         ready_builds("/r", listdir=lambda d: ["A.1-101010"], read=lambda p: "READY /x/b.json\n",
                      landed={"A.1"}, mtime=lambda p: 1.0) == {}),
        ("a READY build outside the scope does not block a scoped stop",
         ready_builds("/r", listdir=lambda d: ["Z9.1-101010"], read=lambda p: "READY /x/b.json\n",
                      scope=r"^A", mtime=lambda p: 1.0) == {}),
        ("the NEWEST run wins by mtime, not by the folder name that wraps at midnight",
         ready_builds("/r", listdir=lambda d: ["A.1-235959", "A.1-000001"],
                      read=lambda p: ("EXHAUSTED x" if "235959" in p else "READY /x/b.json"),
                      mtime=lambda p: (1.0 if "235959" in p else 2.0)) == {"A.1": "READY"}),
        ("and the older run does not win just because it sorts later",
         ready_builds("/r", listdir=lambda d: ["A.1-235959", "A.1-000001"],
                      read=lambda p: ("READY /x/b.json" if "235959" in p else "EXHAUSTED x"),
                      mtime=lambda p: (2.0 if "235959" in p else 1.0)) == {"A.1": "READY"}),
        ("landed_subs reads the plan's own evidence prose",
         landed_subs({"units": [{"id": "A", "sub_units": ["A.1", "A.2"], "evidence": "A.1 landed ok"}]}) == {"A.1"}),
        ("an unreadable runs directory is None, not empty",
         ready_builds("/r", listdir=lambda d: (_ for _ in ()).throw(OSError("no"))) is None),
    ]
    bad = [n for n, ok in cases if not ok]
    print("selftest: %d cases, %s" % (len(cases), "OK" if not bad else "FAILED: " + ", ".join(bad)))
    return 1 if bad else 0


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if "--selftest" in args:
        return selftest()
    scope = args[args.index("--scope") + 1] if "--scope" in args else None
    try:
        plan = json.load(open(PLAN, encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print("NO-DATA  the plan could not be read (%s); the loop keeps running" % exc)
        return 2
    try:
        scores = json.load(open(os.path.expanduser("~/.claude/evidence/spec-scores.json"), encoding="utf-8"))
    except (OSError, ValueError):
        scores = None            # unreadable scores are NO-DATA, never "nothing is eligible"
    unit_of = {(s["id"] if isinstance(s, dict) else s): u.get("id") for u in plan.get("units") or [] for s in (u.get("sub_units") or [])}
    v, why = verdict(plan, ready_builds(landed=landed_subs(plan), scope=scope, unit_of=unit_of), live_runners(), scope,
                     eligible=eligible_count(plan, scores, scope=scope))
    print("%-9s %s" % (v, why))
    return {"FINISHED": 0, "WORKING": 1, "STALLED": 3}.get(v, 2)


if __name__ == "__main__":
    sys.exit(main())
