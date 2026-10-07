#!/usr/bin/env python3
"""Probe every graded but unprobed build in one round, and promote only the ones whose probes actually ran clean.

usage (launch worktree root): python3 scripts/probe_round.py [--dry] [--out DIR]
Exit 0 when at least one build was promoted, 1 when none was, 2 when the state could not be read.

WHY. A build that is graded but unprobed is finished work that cannot land: READY-UNPROBED is never landed as clean,
because a grader proves the code does what its own tests say and says nothing about hostile input. Running this by
hand three times on 2026-09-20 found real defects behind five of nine such builds, so the step earns its place; it
was the last rung of the loop with no single command owning it, which left a fresh session stuck naming it forever.

THE ADVERSARY PROPOSES, EXECUTION DECIDES. Two adversaries write probes as code, the runner executes them against
the real build, and only a lane with probes that RAN, zero crashes and zero wrong accepts is promoted. A lane whose
adversaries returned nothing stays READY-UNPROBED: silence is not a pass."""
import argparse
import fcntl
import glob
import json
import os
import re
import subprocess
import sys

BIN = os.path.expanduser("~/.claude/bin")
RUNS = os.path.expanduser("~/.claude/evidence/unit-runs")
PLAN = "docs/plan/BROTHER-1.1.0-LAUNCH-WBS.json"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "loop")); import plan_store  # noqa: E402  (the one landed test)
ADVERSARIES = (("a", "deepseek"), ("b", "muse"))   # DeepSeek first by the owner's order, Muse as the second lane
if os.environ.get("BROTHER_ADVERSARY_MODEL", "").strip() not in ("", "deepseek"):   # one named model for both lanes; "deepseek" is today's pair (probe_wave.adversary_models, 2026-09-30)
    ADVERSARIES = (("a", os.environ["BROTHER_ADVERSARY_MODEL"].strip()), ("b", os.environ["BROTHER_ADVERSARY_MODEL"].strip()))


def landed_subs(plan):
    out = set()
    for unit in plan.get("units") or []:
        ev = unit.get("evidence") or ""
        for sub in unit.get("sub_units") or []:
            if plan_store.sub_landed(sub, ev):
                out.add(sub)
    return out


def _dir_time(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def unprobed_builds(run_dirs, read, landed):
    """{sub: build path} for each sub unit whose NEWEST run is READY-UNPROBED and that is not already landed.
    A status naming a build that does not exist is skipped, never guessed at."""
    newest = {}
    # by real folder time, never by the HHMMSS name: that stamp wraps at midnight and hid every run made after it
    for d in sorted(run_dirs, key=lambda p: (_dir_time(p), os.path.basename(p.rstrip("/")))):
        m = re.match(r"(.+)-(\d{6})$", os.path.basename(d.rstrip("/")))
        if m:
            newest[m.group(1)] = (d, read(d) or "")
    out = {}
    for sub, (d, text) in sorted(newest.items()):
        words = text.split()
        if sub in landed or len(words) < 2 or words[0] != "READY-UNPROBED":
            continue
        out[sub] = (d, words[1])
    return out


def verdict(results, expected=None):
    """CLEAN only when EVERY dispatched adversary answered and every one came back clean.
    results is [(exit_code, summary_line)]; expected is how many lanes were dispatched.
    Two council rounds moved this rule, each time toward refusing more. First it promoted on ANY clean lane, so an
    adversary finding three crashes was overruled by one finding nothing. Then it required every ANSWERED lane to
    be clean, which still let a silent lane be ignored: the lane that would have found the defect can simply time
    out, and silence became consent. A missing answer is now NO-DATA, which is never a pass.
    THE PROBE RUNNER'S EXIT IS READ BY NAME (X2 finding 6, 2026-09-27): probe_build exits 0 clean, 1 dirt, and 2 when no
    probe ran (no fire() call, no sandbox). Every non zero exit read DIRTY, so a probe that never ran sent a build back
    for a rebuild with no evidence against it. Exit 2, and any exit that is not 0 or 1, is no evidence either way: that
    lane counts as unanswered."""
    ran = [r for r in results if r is not None]
    # A found defect decides FIRST, whatever the other lanes did. Checking silence first would return NO-DATA for
    # a build an adversary had already broken, and NO-DATA leaves it READY-UNPROBED to be probed again forever
    # rather than sent back for a rebuild, which is the livelock this file already fixed once.
    if any(code == 1 for code, _ in ran):
        return "DIRTY"
    clean = [r for r in ran if r[0] == 0]
    if not clean or len(clean) < len(ran) or (expected is not None and len(clean) < expected):
        return "NO-DATA"
    return "CLEAN"


def write_status_if(status_path, seen, text):
    """Write text to status_path only while it holds exactly `seen`, the bytes the probe decision read: '' when written,
    else why not. X2 finding 3 (2026-09-27): reprobe read READY-UNPROBED with no lock and wrote with a plain write, so a
    salvage promotion and a landing's QUARANTINE, written under the lock, could land in between and the stale READY
    restored a build the landing had refused. THE SHARED RULE (salvage.promote, land_batch.write_status): under
    <runs>/.status.lock re-read, compare, write a temp file and os.replace it. The replace moves the run folder's mtime,
    and readers take the NEWEST run of a sub unit, so the write is refused too once a newer run of the sub unit exists:
    the older build must never be made to speak for it again. A folder not named <sub>-<HHMMSS> is refused, and so is a
    missing snapshot (it never equals the bytes on disk): unknown never reads as unchanged."""
    run = os.path.dirname(os.path.abspath(status_path)); runs = os.path.dirname(run)
    m = re.match(r"(.+)-\d{6}$", os.path.basename(run))
    if not m:
        return "the run folder %s is not named <sub>-<HHMMSS>; nothing written" % os.path.basename(run)
    try:
        with open(os.path.join(runs, ".status.lock"), "a") as lock:   # outside every run folder, so no run's mtime moves
            fcntl.flock(lock, fcntl.LOCK_EX)   # released when the file closes
            try:
                with open(status_path, "rb") as fh:
                    now = fh.read()
            except OSError as exc:
                return "STATUS unreadable now (%s); nothing written" % str(exc)[:80]
            if now != seen:
                return "STATUS changed since the probe decision, now %r; the newer word stands" % now[:80]
            mine = (_dir_time(run), os.path.basename(run))
            for d in glob.glob(os.path.join(runs, glob.escape(m.group(1)) + "-*")):
                name = os.path.basename(d)
                if name != mine[1] and re.match(re.escape(m.group(1)) + r"-\d{6}$", name) and os.path.isdir(d) \
                        and (_dir_time(d), name) > mine:
                    return "a newer run of %s exists (%s); nothing written" % (m.group(1), name)
            tmp = status_path + ".tmp-%d" % os.getpid()
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(text)
            os.replace(tmp, status_path)
    except OSError as exc:
        return "the STATUS lock or write failed (%s); nothing written" % str(exc)[:80]
    return ""


def promote(status_path, build, note, seen):
    """Rewrite a READY-UNPROBED status to READY, keeping why: '' when written, else why not (write_status_if)."""
    if not (isinstance(seen, bytes) and seen.startswith(b"READY-UNPROBED")):
        return "STATUS was not READY-UNPROBED at the probe decision; nothing written"
    return write_status_if(status_path, seen, "READY %s (grader PASS; %s)\n" % (build, note))


def mark_dirty(status_path, build, summary, seen):
    """A lane whose probes found defects stops being a probe candidate and becomes a rebuild candidate: '' when written,
    else why not. Only a status still holding the decision's bytes is rewritten, so a landing quarantine never is."""
    if not (isinstance(seen, bytes) and seen.startswith(b"READY-UNPROBED")):
        return "STATUS was not READY-UNPROBED at the probe decision; nothing written"
    return write_status_if(status_path, seen, "DIRTY %s executed probes found defects: %s\n" % (build, summary))


def round_answered(jobs, isfile=os.path.isfile):
    """Ids of the jobs whose adversary wrote an answer. Empty means the round measured nothing."""
    return [j["id"] for j in jobs if isfile(j["out"])]


def probe_job(sub, lane, model, brief, out_dir):
    """One adversary job. `sensitivity` is EARNED by the private screen the caller ran on the brief just before
    this, exactly as unit_runner labels its build jobs; or_fanout refuses an unlabelled job as PRIVATE."""
    return {"id": "probe-%s-%s" % (sub, lane), "model": model, "prompt_file": brief,
            "out": os.path.join(out_dir, "out", "%s-%s.json" % (sub, lane)),
            "estimated_cost": 0.02, "expect": "json", "sensitivity": "public"}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry", action="store_true", help="list the lanes and write nothing")
    ap.add_argument("--out", default=os.path.expanduser("~/.claude/evidence/probe-round"))
    ap.add_argument("--timeout", type=int, default=600)
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))
    args.out = os.path.abspath(args.out)   # the fan out runs in the code root, so every path it is handed is absolute
    try:
        with open(PLAN, encoding="utf-8") as fh:
            plan = json.load(fh)
    except (OSError, ValueError) as exc:
        print("NO-DATA: the plan is unreadable (%s)" % exc)
        return 2

    seen = {}   # run folder -> the STATUS bytes the decision read; every write compares against these (X2 finding 3)

    def read(d):
        try:
            with open(os.path.join(d, "STATUS"), "rb") as fh:
                seen[d] = fh.read()
        except OSError:
            return ""
        return seen[d].decode("utf-8", "replace")

    lanes = unprobed_builds(glob.glob(os.path.join(RUNS, "*-*/")), read, landed_subs(plan))
    lanes = {s: v for s, v in lanes.items() if os.path.isfile(v[1])}
    if not lanes:
        print("nothing to probe: no graded build is waiting")
        return 1
    print("LANES   %d: %s" % (len(lanes), ", ".join(sorted(lanes)[:6])))
    if args.dry:
        return 0
    # THE FAN OUT RUNS THE FROZEN CANDIDATE (U3, B5-08): `-m` resolves the module from the cwd, which is the landing tree.
    # Asked before anything is written; a refused code root (a proof phase with none set) sends nothing.
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "loop"))
    import model_router as MR
    try:
        code_root = MR.code_root()
    except MR.Refused as exc:
        print("NO-DATA: the code root is refused (%s); no adversary job is written or sent" % str(exc)[:160])
        return 2
    os.makedirs(os.path.join(args.out, "out"), exist_ok=True)
    sys.path.insert(0, BIN)
    import grade_build as G
    jobs = []
    for sub, (d, build) in sorted(lanes.items()):
        unit = next((u for u in plan["units"] if sub in (u.get("sub_units") or [])), None)
        if not unit or not unit.get("spec"):
            continue
        brief = os.path.join(args.out, sub + ".md")
        subprocess.run([sys.executable, os.path.join(BIN, "probe_brief.py"), unit["spec"], sub, build, brief],
                       capture_output=True, timeout=300)
        if not os.path.isfile(brief):
            print("SKIP    %-8s no brief could be built" % sub)
            continue
        text = open(brief, encoding="utf-8").read()
        if G.private_hits(text) or len(text.encode()) > 195000:
            print("WITHHELD %-8s private term or size; not sent" % sub)
            continue
        for lane, model in ADVERSARIES:
            jobs.append(probe_job(sub, lane, model, brief, args.out))
    if not jobs:
        print("nothing dispatched: every lane was skipped or withheld")
        return 1
    jf = os.path.join(args.out, "jobs.json")
    with open(jf, "w", encoding="utf-8") as fh:
        json.dump(jobs, fh, indent=1)
    for j in jobs:                     # an adversary answer from another day must never count as tonight's (audit 2026-09-22)
        try: os.unlink(j["out"])
        except OSError: pass
    # THE FAN OUT'S EXIT CODE IS READ (2026-09-22). It was discarded, and with it the reason every job was
    # refused: or_fanout treats a job with no sensitivity key as PRIVATE and refuses to send it to a third party
    # model, so this rung "ran" in seven seconds, promoted nothing, and left last week's results.json in place to
    # be read as if it were tonight's. A refused dispatch is NO-DATA for every lane, said out loud.
    r = subprocess.run([sys.executable, "-m", "plugin.runtime.brother.core.or_fanout", jf,
                        "--workers", "40", "--retries", "1", "--timeout", str(args.timeout),
                        "--results", os.path.join(args.out, "results.json")],
                       capture_output=True, text=True, timeout=args.timeout * 3, cwd=code_root)
    answered = round_answered(jobs)
    if not answered:
        # ZERO answers is NO-DATA for the round; a partial fan out is judged lane by lane on whoever answered (measured
        # 2026-09-22 04:01: 6 of 10 adversaries answered and an exit code rule threw all six away).
        last = ((r.stderr or r.stdout).strip().splitlines() or ["no output"])[-1]
        print("NO-DATA: the fan out exited %d and no adversary answered, so no lane was probed this round: %s" % (r.returncode, last[:140]))
        return 2
    print("ANSWERED %d of %d adversary job(s)%s" % (len(answered), len(jobs), "" if r.returncode == 0 else " (fan out exit %d, judging on what answered)" % r.returncode))
    promoted = dirty = 0
    for sub, (d, build) in sorted(lanes.items()):
        results = []
        for lane, _ in ADVERSARIES:
            probe = os.path.join(args.out, "out", "%s-%s.json" % (sub, lane))
            if not os.path.isfile(probe):
                continue
            try:
                r = subprocess.run([sys.executable, os.path.join(BIN, "probe_build.py"), build, probe],
                                   capture_output=True, text=True, timeout=900)
            except subprocess.TimeoutExpired:
                results.append((None, "probe_build timed out after 900 s"))   # no exit read: no evidence either way
                continue
            line = [l for l in (r.stdout + r.stderr).splitlines() if l.startswith(("PROBES", "NO-DATA"))]
            results.append((r.returncode, line[0] if line else ""))
        v = verdict(results, expected=len(ADVERSARIES))
        if v == "NO-DATA" and results:
            print("%-8s NO-DATA  probe exits %s: no evidence either way; STATUS left as it is" % (sub, [c for c, _ in results]))
        else:
            print("%-8s %-8s %s" % (sub, v, (results[0][1] if results else "no adversary answered")[:80]))
        status = os.path.join(d, "STATUS")
        why = None
        if v == "CLEAN":
            why = promote(status, build, "executed probes clean, %d adversary lane(s)" % len(results), seen.get(d))
            promoted += not why
        elif v == "DIRTY":
            # A build whose probes found real defects needs a REBUILD, not another probe. Left as READY-UNPROBED it
            # is re-probed every pass forever and no new build ever starts, which is exactly the livelock the
            # landing step already had (seen 2026-09-21: five lanes re-probed three passes running, 0 promoted).
            # DIRTY is deliberately not QUARANTINE: the sub unit stays startable so a runner can build it again.
            why = mark_dirty(status, build, next((l for c, l in results if c == 1), "")[:120], seen.get(d))
            dirty += not why
        if why:
            print("REFUSED  %-8s %s" % (sub, why))
    print("PROMOTED %d of %d lane(s) to READY | %d marked DIRTY for a rebuild" % (promoted, len(lanes), dirty))
    return 0 if promoted else 1


if __name__ == "__main__":
    sys.exit(main())
