#!/usr/bin/env python3
"""One command a session with NO history can run: read the whole loop's state from disk and name the ONE next action.

usage (launch worktree root): python3 scripts/brother_pass.py [--json out.json] [--stop-hour 7]
Exit 0 when the state was read and an action was named, 2 when a source could not be read. Never a silent pass.

WHY THIS EXISTS. Measured 2026-09-20/21: an orchestration session reached 62 percent of its context window mostly by
reading detail it did not need, while every fact it actually needed was already on disk. The loop never needed the
conversation; the orchestrator was carrying it. This prints at most 25 lines, counts and the first three items only,
and ends with exactly one line beginning NEXT. Two processes sharing no memory read the same disk the same way.

IT DECIDES NOTHING IRREVERSIBLE. It reads, it ranks, it names the next action. Landing, pushing and dispatching stay
with the tools that already own them, so a wrong reading here costs a wasted look, never a bad write."""
import argparse
import datetime
import glob
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import writer_lock  # noqa: E402
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "loop"))
import plan_store  # noqa: E402  (the one landed test: plan_store.sub_landed, finding 2 of 2026-09-27)

PLAN = "docs/plan/BROTHER-1.1.0-LAUNCH-WBS.json"
RUNS = os.path.expanduser("~/.claude/evidence/unit-runs")
COUNCIL = os.path.expanduser("~/.claude/evidence/spec-council.json")
SCORES = os.path.expanduser("~/.claude/evidence/spec-scores.json")
MIN_SPEC = 9


JOURNAL = os.path.expanduser("~/.claude/evidence/brother-loop.jsonl")


def pulse_line(row):
    """One short line for a human checking in: what the loop is doing and whether the board moved."""
    done, units = row.get("done", 0), row.get("units", 0)
    subs, tot = row.get("subs_landed", 0), row.get("subs_total", 0)
    return ("%s  %s  units %s/%s  sub units %s/%s (%d%%)  %-9s %s"
            % (row.get("at", "?")[-8:], bar(subs, tot), done, units, subs, tot,
               int(100 * subs / tot) if tot else 0, row.get("next", "?"),
               short_outcome(row.get("outcome") or "looked only")))


def bar(done, total, width=12):
    """A progress bar, because a fraction is read slower than a shape. Never rounds up to full while work remains."""
    if not total or total < 0 or done < 0:
        return "[" + "?" * width + "]"
    # int() truncates, so a partial board can never render full: no guard is needed and one would be dead code.
    filled = int(width * min(done, total) / total)
    return "[" + "#" * filled + "." * (width - filled) + "]"


def short_outcome(text, limit=58):
    """The fact, without the log path or the trailing advice. A reader wants LANDED D6.5, not the line it came on."""
    head = (text or "").split(" | ")[0].strip()
    head = re.sub(r"\s{2,}", " ", head)
    return head[:limit]


# What each rung typically takes, so a reader can tell a slow cycle from a hung one. These are the durations this
# estate measured on 2026-09-20 and 21; a rung with no measurement says so rather than inventing a figure.
TYPICAL = {"LAND": "about 1 to 7 minutes", "PUSH": "about 1 to 3 minutes",
           "PROBE": "about 5 to 15 minutes", "DIAGNOSE": "about 10 to 15 minutes",
           "CLOSE": "under a minute", "START": "seconds to launch, then 10 to 20 minutes of building"}


def human_secs(secs):
    if secs < 90:
        return "%d second(s)" % int(secs)
    return "%.1f minute(s)" % (secs / 60.0)


def outcome_line(code, returncode, output):
    """What the action actually produced, in a few words, read from the tool's own output. This is the half the
    journal was missing: it recorded the state BEFORE each action and never the result, so a reader could see
    counters but not whether the cycle worked."""
    text = output or ""
    for marker, take in (("LANDED", "LANDED"), ("QUARANTINED", "QUARANTINED"), ("CLOSED", "CLOSED"),
                         ("PROMOTED", "PROMOTED"), ("ROUND", "ROUND"), ("started", "started")):
        for line in text.splitlines():
            if line.strip().startswith(take):
                return line.strip()[:120]
    if returncode == 0:
        return "%s ok" % code
    return "%s exit %d" % (code, returncode)


def journal(state, action, stamp, path=None, outcome=None):
    """Append one row per pass. A loop with no record cannot be checked on without re-deriving everything,
    and appending costs nothing. Never raises: a journal failure must not stop the work."""
    row = {"at": stamp, "done": state["done"], "units": state["units"],
           "subs_landed": state["subs_landed"], "subs_total": state["subs_total"],
           "ready": len(state["ready"]), "unprobed": len(state["unprobed"]),
           "needfact": len(state["needfact"]), "closable": len(state["closable"]),
           "next": action[0], "outcome": outcome}
    try:
        with open(path or JOURNAL, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
    except OSError:
        pass
    return row


def gap_minutes(rows):
    """Median minutes between passes, and the sample size. A pace claimed from fewer than three points is not a
    pace, so the caller is told n and can say so."""
    stamps = []
    for r in rows:
        try:
            stamps.append(datetime.datetime.strptime(r.get("at", ""), "%Y-%m-%d %H:%M:%S"))
        except (ValueError, TypeError):
            continue
    gaps = sorted((b - a).total_seconds() / 60.0 for a, b in zip(stamps, stamps[1:]) if b > a)
    return (gaps[len(gaps) // 2], len(gaps)) if gaps else (None, 0)


def stall_note(rows, live, window=3):
    """The line that matters when checking in: is the board moving, and if not, is anything even working?"""
    recent = rows[-window:]
    if len(recent) < 2:
        return "too few passes yet to say whether the board is moving"
    moved = recent[-1].get("subs_landed", 0) - recent[0].get("subs_landed", 0)
    if moved > 0:
        return "moving: %+d sub unit(s) over the last %d pass(es)" % (moved, len(recent))
    if live:
        return ("NOT moving over the last %d pass(es), but %s still working, so give it a round"
                % (len(recent), " and ".join(sorted(live))))
    return ("STALLED: %d pass(es) with no sub unit landed and nothing running; the loop needs a look"
            % len(recent))


def read_pulse(path, lines=3, live=(), now=None):
    """The last few rows, whether the board moved, the measured pace, and whether anything is alive."""
    try:
        with open(path, encoding="utf-8") as fh:
            rows = [json.loads(l) for l in fh if l.strip()]
    except (OSError, ValueError):
        return ["NO-DATA: the loop journal is unreadable, so no pulse can be given"]
    if not rows:
        return ["NO-DATA: the loop has not journalled a pass yet"]
    out = [pulse_line(r) for r in rows[-lines:]]
    first, last = rows[0], rows[-1]
    out.append("OVER %d pass(es): sub units %+d, whole units %+d"
               % (len(rows), last.get("subs_landed", 0) - first.get("subs_landed", 0),
                  last.get("done", 0) - first.get("done", 0)))
    med, n = gap_minutes(rows)
    if med is not None:
        out.append("PACE    a pass every %.0f minute(s), median of %d gap(s)%s"
                   % (med, n, "" if n >= 3 else "; too few to call a pace"))
    out.append("SIGN    " + stall_note(rows, set(live)))
    return out


class Unreadable(Exception):
    """A source that cannot be read. Never downgraded to an empty one: an empty plan and an unreadable plan mean
    opposite things and would produce opposite next actions."""


def read_json(path, what):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as exc:
        raise Unreadable("%s unreadable (%s): %s" % (what, path, exc))


def dir_time(path, mtime=None):
    """When a run folder was last active. The folder name carries only HHMMSS, which WRAPS AT MIDNIGHT: sorting
    those strings puts yesterday's 220142 after today's 023623, so every run created after midnight became
    invisible and the loop read a stale status from the previous evening (measured 2026-09-21, and it is why the
    board stopped moving). The filesystem knows the real time, so ask it, and fall back to the stamp only when
    it cannot be read."""
    try:
        return (mtime or os.path.getmtime)(path)
    except OSError:
        return 0.0


def newest_status(run_dirs, read, mtime=None):
    """{sub: (state, rest)} from the NEWEST run folder per sub unit. A folder with no STATUS is RUNNING, because a
    runner writes its STATUS only when it finishes; a missing file is work in flight, not a failure."""
    last = {}
    for d in sorted(run_dirs, key=lambda p: (dir_time(p, mtime), os.path.basename(p.rstrip("/")))):
        m = re.match(r"(.+)-(\d{6})$", os.path.basename(d.rstrip("/")))
        if not m:
            continue
        words = (read(d) or "").split(None, 1)
        # a STATUS can be one bare word ("RUNNING"), so the rest is optional, never indexed blind
        last[m.group(1)] = (words[0], words[1].strip() if len(words) > 1 else "") if words else ("RUNNING", "")
    return last


def landed_subs(plan):
    out = set()
    for unit in plan.get("units") or []:
        evidence = unit.get("evidence") or ""
        for sub in unit.get("sub_units") or []:
            if plan_store.sub_landed(sub, evidence):
                out.add(sub)
    return out



def unit_of(sub, plan):
    for unit in plan.get("units") or []:
        if sub in (unit.get("sub_units") or []):
            return unit["id"]
    return None


def closable(plan, landed):
    """Units whose every sub unit is landed but whose state is neither DONE nor RETIRED: their done_check is owed.
    The same test as close_unit.closable, which this decides whether to run (RETIRED: withdrawn by the owner)."""
    out = []
    for unit in plan.get("units") or []:
        subs = unit.get("sub_units") or []
        if subs and unit.get("state") not in ("DONE", "RETIRED") and all(s in landed for s in subs):
            out.append(unit["id"])
    return out


def buildable(plan, last, landed, council, scores):
    """(sub, unit_id) pairs a runner could legitimately start, with the reason each candidate was refused dropped.
    Held for a real reason: the council said DO NOT BUILD, the spec is under the bar, or the last run needs a fact."""
    out = []
    for unit in plan.get("units") or []:
        if unit.get("state") == "DONE" or not unit.get("spec"):
            continue
        if (council.get(unit["id"]) or {}).get("state") == "DO-NOT-BUILD":
            continue
        for sub in unit.get("sub_units") or []:
            if sub in landed:
                continue
            state = (last.get(sub) or ("", ""))[0]
            if state in ("RUNNING", "READY", "READY-UNPROBED", "EXHAUSTED", "WITHHELD", "QUARANTINE"):
                continue
            score = (scores.get(sub) or {}).get("score")
            if not isinstance(score, int) or isinstance(score, bool) or score < MIN_SPEC:
                continue
            out.append((sub, unit["id"]))
            break
    return out



def remaining_by_unit(plan, landed):
    """{unit id: how many of its sub units are still unlanded}. The distance each unit is from being DONE."""
    out = {}
    for unit in plan.get("units") or []:
        subs = unit.get("sub_units") or []
        if not subs or unit.get("state") == "DONE":
            continue
        out[unit["id"]] = sum(1 for s in subs if s not in landed)
    return out


def nearest_first(items, plan, landed):
    """Order (sub, unit) pairs by how close their unit is to DONE, closest first, then by name so two runs of the
    loop agree. WHY: the ladder used to take whatever was READY in plan order, which spread work across many
    units and closed none. Measured 2026-09-21: 36 sub units landed in a night and the whole unit count did not
    move, while SEVEN units sat one sub unit from done. Landing the last sub unit of a unit is worth more than
    landing the first of another, because only the former changes the number anyone is watching."""
    dist = remaining_by_unit(plan, landed)
    return sorted(items, key=lambda pair: (dist.get(pair[1], 99), pair[1], pair[0]))


def next_action(state):
    """The ONE next action, by a fixed ladder. Order is the whole point: a dirty tree makes every later reading
    untrustworthy, an unpushed commit means the work is not really landed, and a READY build is finished work
    waiting on the one serial lane. Returns (code, sentence)."""
    if state["dirty"]:
        return ("CLEAN-TREE", "revert or commit the %d changed path(s); a landing needs a clean tree so every "
                              "change is attributable" % state["dirty"])
    if state["unpushed"]:
        return ("PUSH", "push %d local commit(s) to the hub; landed work that is not pushed is not landed"
                % state["unpushed"])
    if state["ready"]:
        return ("LAND", "land %d ready build(s), one per unit: %s" % (len(state["ready"]), ", ".join(state["ready"][:3])))
    if state["closable"]:
        return ("CLOSE", "run the done_check of %s and mark it DONE with the quoted output" % state["closable"][0])
    if state["unprobed"]:
        return ("PROBE", "probe %d graded build(s) so they can land: %s"
                % (len(state["unprobed"]), ", ".join(state["unprobed"][:3])))
    if state["needfact"]:
        return ("DIAGNOSE", "diagnose %d stuck sub unit(s), one fact plus the command proving it: %s"
                % (len(state["needfact"]), ", ".join(state["needfact"][:3])))
    if state["buildable"] and state["free_lanes"] > 0:
        return ("START", "start %d lane(s) the budget funds, nearest to closing first: %s"
                % (min(state["free_lanes"], len(state["buildable"])),
                   ", ".join(s for s, _ in state["buildable"][:3])))
    if state["buildable"]:
        return ("WAIT", "%d sub unit(s) are buildable but the budget funds no free lane; wait for a lane to finish"
                % len(state["buildable"]))
    return ("NOTHING", "nothing is ready: every remaining sub unit is running, held by the council, under the spec "
                       "bar, or waiting on a fact")



# What each rung of the ladder actually runs. CLEAN-TREE is deliberately absent: reverting a dirty tree DELETES
# work, and this tool is not allowed to do that on its own judgement, so it stays with a human however obvious it
# looks. Every other rung delegates to the tool that already owns that step, with its own gates intact.
BIN = os.path.expanduser("~/.claude/bin")
ACTIONS = {
    "PUSH":     lambda st: ["git", "push", "hub", st["branch"]],
    # ONE build per landing, not the whole batch: a gate refusal can then blame exactly one build, which is what
    # lets a bad one quarantine itself instead of being retried every pass. The landing lane is serial anyway.
    "LAND":     lambda st: [sys.executable, os.path.join(BIN, "land_batch.py")] + st["ready_builds"][:1],
    "PROBE":    lambda st: [sys.executable, "scripts/probe_round.py"],
    "START":    lambda st: [sys.executable, os.path.join(BIN, "runner_pool.py")],
    "CLOSE":    lambda st: [sys.executable, "scripts/close_unit.py"],
    "DIAGNOSE": lambda st: [sys.executable, "scripts/diag_round.py"],
}
HUMAN_ONLY = {
    "CLEAN-TREE": "reverting a dirty tree deletes work, so a person decides which changes survive",
    "WAIT": "nothing to run: the budget funds no free lane",
    "NOTHING": "nothing to run: every remaining sub unit is running, held, under the bar or waiting on a fact",
}


def runnable(code, state):
    """The argv to advance this rung, or None when the rung is human only or has nothing to run.
    PROBE gained its own command (scripts/probe_round.py) on 2026-09-21; before that it was reported rather
    than pretended, which left the loop naming it forever."""
    if code in HUMAN_ONLY:
        return None
    build = ACTIONS.get(code)
    if build is None:
        return None
    argv = build(state)
    return argv if all(isinstance(x, str) and x for x in argv) else None


# COHORTS. The writer is serial by law: one branch, one clean tree, one landing at a time. Everything else is a
# WORKER cohort that writes only evidence and STATUS markers, so cohorts run concurrently with each other and with
# the writer. Their costs differ in kind, which is why one cap cannot govern them all: a build runner burns money
# continuously for as long as it lives, while a probe or a diagnose round is one shot costing cents. Capping the
# one shot rounds with the continuous runner cap is what kept the loop doing one thing per pass.
WRITER_RUNGS = ("CLEAN-TREE", "PUSH", "LAND", "CLOSE")
WORKER_COHORTS = (
    ("probe",    "unprobed", lambda st: [sys.executable, "scripts/probe_round.py"]),
    ("diagnose", "needfact", lambda st: [sys.executable, "scripts/diag_round.py"]),
    ("build",    "buildable", lambda st: [sys.executable, os.path.join(BIN, "runner_pool.py")]),
)


# The build cohort is a POOL, not a job: runner_pool is idempotent, starts at most one runner per unit and caps
# itself at what the budget funds, so running it again TOPS UP the lanes. Treating it as busy because one runner
# was alive is why the loop sat at 2 lanes while 6 were funded and the board did not move for half an hour
# (measured 2026-09-21). The one shot rounds are the opposite: a second copy would duplicate their dispatch.
TOPS_UP = {"build"}


def cohorts_to_start(state, running):
    """[(name, argv)] for every worker cohort with work to do that it is safe to (re)start.
    A one shot round is skipped while a copy is alive; a pool is run again so it can top up to the funded width."""
    out = []
    for name, key, build in WORKER_COHORTS:
        if not state.get(key):
            continue
        if name in running and name not in TOPS_UP:
            continue
        if name == "build" and state.get("free_lanes", 0) <= 0:
            continue
        out.append((name, build(state)))
    return out


def running_cohorts(ps_text):
    """Which cohorts already have a live process, so a pass never starts a second copy of one."""
    live = set()
    for name, marker in (("probe", "probe_round.py"), ("diagnose", "diag_round.py"), ("build", "unit_runner.py")):
        if marker in (ps_text or ""):
            live.add(name)
    return live


def collect(cwd, git, now, stop_hour, lanes_free):
    """Everything the ladder needs, read from disk. Raises Unreadable rather than guessing."""
    plan = read_json(os.path.join(cwd, PLAN), "the plan")
    council = read_json(COUNCIL, "the council verdicts") if os.path.exists(COUNCIL) else {}
    scores = read_json(SCORES, "the spec scores") if os.path.exists(SCORES) else {}
    if not isinstance(plan, dict) or not isinstance(plan.get("units"), list):
        raise Unreadable("the plan has no units list: " + os.path.join(cwd, PLAN))

    def read(d):
        try:
            with open(os.path.join(d, "STATUS"), encoding="utf-8") as fh:
                return fh.read()
        except OSError:
            return ""

    last = newest_status(glob.glob(os.path.join(RUNS, "*-*/")), read)
    landed = landed_subs(plan)
    ready, unprobed, needfact = [], [], []
    ready_pairs, ready_units = [], set()
    for sub, (st, rest) in sorted(last.items()):
        if sub in landed:
            continue
        if st == "READY" and rest.split()[:1]:
            path = rest.split()[0]
            if os.path.isfile(path) and unit_of(sub, plan) not in ready_units:
                ready_pairs.append((sub, unit_of(sub, plan), path)); ready_units.add(unit_of(sub, plan))
        elif st == "READY-UNPROBED":
            unprobed.append(sub)
        elif st in ("EXHAUSTED", "WITHHELD"):
            needfact.append(sub)
    # land what CLOSES a unit first: the last sub unit of a unit changes the number, the first of another does not
    ordered = nearest_first([(sub, uid) for sub, uid, _ in ready_pairs], plan, landed)
    by_sub = {sub: path for sub, _, path in ready_pairs}
    ready = [sub for sub, _ in ordered]
    ready_builds = [by_sub[sub] for sub, _ in ordered]
    units = plan["units"]
    return {
        "dirty": git["dirty"], "unpushed": git["unpushed"], "head": git["head"], "branch": git["branch"],
        "ready": ready, "ready_builds": ready_builds, "unprobed": unprobed, "needfact": needfact,
        "closable": closable(plan, landed),
        "buildable": nearest_first(buildable(plan, last, landed, council, scores), plan, landed),
        "free_lanes": lanes_free,
        "done": sum(1 for u in units if u.get("state") == "DONE"),
        "units": len(units),
        "subs_landed": len(landed),
        "subs_total": sum(len(u.get("sub_units") or []) for u in units),
        "held": sum(1 for v in council.values() if isinstance(v, dict) and v.get("state") == "DO-NOT-BUILD"),
    }


def git_state(cwd, run):
    """The tree as git reports it. An UNREADABLE git state raises rather than reading as clean: run() returns an
    empty string when the command fails, which made dirty and unpushed both 0, which the ladder reads as a clean
    pushed tree and would land on top of. Proved by a council seat 2026-09-21, and it is the most dangerous thing
    an unattended loop could be told."""
    head = run(["git", "rev-parse", "--short", "HEAD"]).strip()
    branch = run(["git", "branch", "--show-current"]).strip()
    if not head:
        raise Unreadable("git could not report HEAD in %s, so the tree state is unknown and nothing may proceed" % cwd)
    status = run(["git", "status", "--porcelain", "-uall"])
    if status is None:
        raise Unreadable("git status could not be read in %s" % cwd)
    dirty = len([l for l in status.splitlines() if l.strip()])
    unpushed = len([l for l in run(["git", "log", "--format=%h", "@{u}..HEAD"]).splitlines() if l.strip()])
    return {"branch": branch, "dirty": dirty, "unpushed": unpushed, "head": head}


def render(state, action):
    """At most 25 lines: counts, the first three of anything, and one NEXT line."""
    code, sentence = action
    return [
        "TREE    branch %s at %s | %s | %s" % (
            state["branch"] or "NO-DATA", state["head"] or "NO-DATA",
            "clean" if not state["dirty"] else "%d changed path(s)" % state["dirty"],
            "pushed" if not state["unpushed"] else "%d commit(s) unpushed" % state["unpushed"]),
        "PLAN    %d unit(s), %d DONE | sub units %d of %d landed | %d unit(s) held by the council"
        % (state["units"], state["done"], state["subs_landed"], state["subs_total"], state["held"]),
        "READY   %s" % first3(state["ready"]),
        "UNPROBED %s" % first3(state["unprobed"]),
        "NEEDFACT %s" % first3(state["needfact"]),
        "CLOSABLE %s" % first3(state["closable"]),
        # the guard keeps a floor of 2 lanes while ANY money remains, so this figure can exceed the strictly
        # funded one; verified by a council seat 2026-09-21 against burn_guard.cap, and said out loud rather
        # than silently over proposing (REQ-BUDGET is amended in the spec to match)
        "STARTABLE %s (%d free lane(s); the guard keeps a floor of %d while money remains)"
        % (first3([s for s, _ in state["buildable"]]), state["free_lanes"], 2),
        "NEXT    %s: %s" % (code, sentence),
    ]


def first3(xs):
    return "%d%s" % (len(xs), (": " + ", ".join(xs[:3]) + (" ..." if len(xs) > 3 else "")) if xs else "")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", default=None, help="also write the whole reading here")
    ap.add_argument("--stop-hour", type=int, default=7)
    ap.add_argument("--lanes-free", type=int, default=None, help="skip the budget guard and use this figure")
    ap.add_argument("--pulse", action="store_true",
                    help="print the last few passes and whether the board moved, then stop")
    ap.add_argument("--no-cohorts", action="store_true",
                    help="do not start worker cohorts; take only the writer action (cohorts are the default)")
    ap.add_argument("--do", action="store_true",
                    help="also RUN the named action, when it is one this tool is allowed to run")
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))

    def run(cmd):
        try:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=60).stdout
        except (OSError, subprocess.SubprocessError):
            return ""

    if args.pulse:
        for line in read_pulse(JOURNAL, live=running_cohorts(run(["ps", "-eo", "command"]))):
            print(line)
        return 0

    lanes = args.lanes_free
    if lanes is None:
        out = run([sys.executable, "scripts/burn_guard.py", "--stop-hour", str(args.stop_hour)])
        tail = [l for l in out.splitlines() if l.strip().isdigit()]
        lanes = int(tail[-1]) if tail else 0      # an unreadable guard funds nothing new, never everything
    try:
        git = git_state(os.getcwd(), run)
        state = collect(os.getcwd(), git, datetime.datetime.now(), args.stop_hour, lanes)
    except Unreadable as exc:
        print("NO-DATA %s" % exc)
        print("NEXT    FIX-STATE: that source has to be readable before any action can be named")
        return 2
    action = next_action(state)
    started = datetime.datetime.now()
    stamp = started.strftime("%Y-%m-%d %H:%M:%S")
    print("CYCLE   %s starting: %s" % (started.strftime("%H:%M:%S"), action[0]))
    for line in render(state, action):
        print(line)
    cohorts = args.do and not args.no_cohorts   # parallel by default: a flag nobody remembers runs serial
    if cohorts:
        ps = run(["ps", "-eo", "command"])
        started = []
        for name, argv in cohorts_to_start(state, running_cohorts(ps)):
            try:
                subprocess.Popen(argv, stdout=open(os.path.expanduser("~/.claude/evidence/cohort-%s.log" % name), "a"),
                                 stderr=subprocess.STDOUT, start_new_session=True)
                started.append(name)
            except (OSError, subprocess.SubprocessError) as exc:
                print("COHORT  %s could not start: %s" % (name, exc))
        print("COHORTS started %s | already running %s"
              % (", ".join(started) or "none", ", ".join(sorted(running_cohorts(ps))) or "none"))
        if action[0] not in WRITER_RUNGS:
            print("NEXT    the writer has nothing to do this pass; the cohorts above carry the work")
            return 0
    if args.do and action[0] in WRITER_RUNGS:
        # One writer at a time. Two ticks of a timer that overlap would apply two builds to one tree, revert each
        # other's paths and commit a mixture neither verified. A pass that loses the lock does something else
        # rather than waiting, which is what a work conserving loop should do anyway.
        writer_lock.break_if_dead()
        if not writer_lock.acquire(what=action[0]):
            holder = writer_lock.read() or {}
            print("HOLD    another writer holds the tree (pid %s, doing %s); the cohorts above carry this pass"
                  % (holder.get("pid"), holder.get("what") or "unstated"))
            journal(state, action, stamp, outcome="skipped: writer held by pid %s" % holder.get("pid"))
            return 0
        held_lock = True
    else:
        held_lock = False
    if args.do:
        code = action[0]
        argv = runnable(code, state)
        if argv is None:
            if held_lock: writer_lock.release()
            print("HOLD    %s is not run automatically: %s"
                  % (code, HUMAN_ONLY.get(code, "no single command owns this step yet")))
            return 1
        print("RUN     " + " ".join(argv))
        try:
            r = subprocess.run(argv, capture_output=True, text=True, timeout=3600)
            sys.stdout.write(r.stdout or "")
            got = outcome_line(code, r.returncode, (r.stdout or "") + (r.stderr or ""))
            journal(state, action, stamp, outcome=got)
            secs = (datetime.datetime.now() - started).total_seconds()
            print("CYCLE   done in %s: %s" % (human_secs(secs), got))
            print("NEXT UP run this again; typical %s takes %s" % (code, TYPICAL.get(code, "an unmeasured time")))
            if held_lock: writer_lock.release()
            if r.returncode:
                return 1
            return 0
        except (OSError, subprocess.SubprocessError) as exc:
            journal(state, action, stamp, outcome="%s could not run: %s" % (code, exc))
            if held_lock: writer_lock.release()
            print("DONE    %s could not run: %s" % (code, exc)); return 1
    if not args.do:
        journal(state, action, stamp, outcome=None)
    if args.json:
        try:
            with open(args.json, "w", encoding="utf-8") as fh:
                json.dump({"state": {k: v for k, v in state.items()}, "next": action}, fh, indent=1, default=list)
        except OSError as exc:
            print("note: could not write %s (%s)" % (args.json, exc))
    return 0


if __name__ == "__main__":
    sys.exit(main())
