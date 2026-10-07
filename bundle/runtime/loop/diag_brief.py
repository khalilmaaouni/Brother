#!/usr/bin/env python3
"""Build one DeepSeek diagnostician brief per stuck sub unit, plus the jobs file for or_fanout.
usage (repo root): diag_brief.py <out dir> [sub ...]      diag_brief.py --selftest
With no sub named, every sub unit whose NEWEST run ended EXHAUSTED or WITHHELD and that the plan does not record as landed.
A brief carrying a private term is WITHHELD, never sent; a brief over the dispatcher's size is trimmed by dropping whole
source files, never by cutting one in half. The answer must be ONE fact plus a command that proves it: diag_apply.py runs it."""
import glob, json, os, re, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # THIS directory's copy
import plan_store  # noqa: E402  (the one landed test every loop reader shares: plan_store.sub_landed)
BIN = os.path.expanduser("~/.claude/bin"); RUNS = os.path.expanduser("~/.claude/evidence/unit-runs")
PLAN = "docs/plan/BROTHER-1.1.0-LAUNCH-WBS.json"; BUDGET = 190000
ASK = ('You are a DIAGNOSTICIAN. Several worker builds of sub unit %s failed, or the brief could not be sent. Find the ONE fact '
       'the workers were never shown that explains it. Answer as JSON only: {"fact": one sentence, "prove": ONE plain grep or '
       'git grep command, run from the repo root, repo relative paths only, no pipes and no shell characters, "expect": the '
       'substring its output must contain, "hint": at most 80 words to add to the next worker brief}. Say UNKNOWN in fact if the '
       'evidence does not support one. Never name a path that is not shown below.\n')

ASKED = "DIAGNOSED"   # marker file in a run folder: this run was already sent to the diagnostician once


def already_asked(run_dir):
    """True when this exact run was diagnosed before. A pass runs every few minutes and an unproven fact leaves the run
    EXHAUSTED, so without this every pass would pay to ask the same question about the same run again. A NEW run
    (a restart on a proven fact, or any other) has no marker and is asked once more."""
    return os.path.isfile(os.path.join(run_dir, ASKED))


def mark_asked(run_dir, now=None):
    """Write the marker, with the time, so a reader can tell when the question was sent. The run folder keeps its own
    modified time: every reader orders runs and dates facts by it (runner_pool._dir_time and its copies), and a marker is
    bookkeeping, not run activity. 2026-10-03: the marker written at 16:58:56 made L5a-7's run read newer than the grader
    fix deployed for it (16:41:39), so still_parked kept it out of the pool. A time that cannot be restored is named on
    stderr and leaves the folder newer, which parks (spends nothing), never readmits."""
    st = os.stat(run_dir)
    with open(os.path.join(run_dir, ASKED), "w", encoding="utf-8") as f:
        f.write("%s\n" % time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)))
    try:
        os.utime(run_dir, ns=(st.st_atime_ns, st.st_mtime_ns))
    except OSError as exc:
        sys.stderr.write("diag_brief: the run folder's time could not be restored after marking (%s): %s\n" % (run_dir, exc))


def stuck_subs(plan, status_of):
    """Sub units whose newest run ended EXHAUSTED or WITHHELD and that no unit's evidence records as landed."""
    landed = {s for u in plan.get("units", []) for s in (u.get("sub_units") or [])
              if plan_store.sub_landed(s, u.get("evidence") or "")}   # the one landed test (X3 finding 5)
    out = []
    for sub, state in sorted(status_of.items()):
        if state in ("EXHAUSTED", "WITHHELD") and sub not in landed: out.append(sub)
    return out

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

def newest_status(dirs, read, mtime=None):
    """{sub: first STATUS word} of each sub unit's NEWEST run, newest by the folder's clock (_dir_time), the name only
    breaking a tie. RR lane C, 2026-09-27: this sorted by the HHMMSS name, so across midnight last night's EXHAUSTED
    M.1-235000 beat this morning's READY M.1-001000 and a READY sub unit was sent to the diagnostician and marked."""
    last = {}
    for d in sorted(dirs, key=lambda d: (_dir_time(d, mtime), os.path.basename(d.rstrip("/")))):
        m = re.match(r"(.+)-(\d{6})$", os.path.basename(d.rstrip("/")))
        if m: last[m.group(1)] = (read(d).split() or ["RUNNING"])[0]
    return last

def fit(head, blocks, budget=BUDGET):
    """head plus as many whole blocks as fit; each dropped block is named, never truncated."""
    out, dropped = head, []
    for name, text in blocks:
        piece = "\n### %s\n```python\n%s\n```\n" % (name, text)
        if len((out + piece).encode()) > budget: dropped.append(name); continue
        out += piece
    if dropped: out += "\nNOT SHOWN, whole files dropped to fit: %s\n" % ", ".join(dropped)
    return out, dropped

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
    plan = {"units": [{"id": "D1", "sub_units": ["D1.2", "D1.3"], "evidence": "D1.2 landed 2026-09-20."}]}
    st = {"D1.2": "EXHAUSTED", "D1.3": "EXHAUSTED", "D4.c": "READY", "L0.1": "WITHHELD", "x": "RUNNING"}
    big = "x" * 300
    body, dropped = fit("H", [("a.py", big), ("b.py", big)], budget=len(b"H") + 340)
    cases = [("landed sub unit is not stuck", stuck_subs(plan, st) == ["D1.3", "L0.1"]),
             ("ready is not stuck", "D4.c" not in stuck_subs(plan, st)),
             ("running is not stuck", "x" not in stuck_subs(plan, st)),
             ("newest run wins", newest_status(["/r/A-100000/", "/r/A-200000/"], lambda d: "EXHAUSTED" if "200000" in d else "READY /b")["A"] == "EXHAUSTED"),
             ("status missing reads RUNNING", newest_status(["/r/A-100000/"], lambda d: "")["A"] == "RUNNING"),
             ("oversize block is dropped whole", dropped == ["b.py"] and big in body and "NOT SHOWN" in body),
             ("nothing dropped when it fits", fit("H", [("a.py", "s")])[1] == [])]
    import tempfile
    rd = tempfile.mkdtemp()
    cases.append(("a fresh run has not been asked", not already_asked(rd)))
    os.utime(rd, (1000000, 1000000))
    mark_asked(rd, now=0)
    cases.append(("marking keeps the run folder's own time (readers date runs by it)", os.path.getmtime(rd) == 1000000))
    cases.append(("a marked run reads as asked, and the marker carries a time", already_asked(rd)
                  and open(os.path.join(rd, ASKED), encoding="utf-8").read().strip() != ""))
    bad = [n for n, ok in cases if not ok]
    print("selftest: %d cases, %s" % (len(cases), "OK" if not bad else "FAILED: " + ", ".join(bad))); return 1 if bad else 0

def main():
    if "--selftest" in sys.argv: return selftest()
    sys.path.insert(0, BIN); import grade_build as G
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry = "--dry" in sys.argv   # a dry round writes briefs to look at and marks NOTHING as asked
    if not args: print(__doc__); return 2
    out = os.path.expanduser(args[0]); os.makedirs(out + "/out", exist_ok=True)
    plan = json.load(open(PLAN, encoding="utf-8"))
    def read(d):
        try:
            with open(os.path.join(d, "STATUS"), encoding="utf-8") as f: return f.read()
        except OSError: return ""
    subs = args[1:] or stuck_subs(plan, newest_status(glob.glob(RUNS + "/*-*/"), read))
    jobs = []
    # SELECT BEFORE MARKING (review 2026-09-27): the round used to truncate the jobs to its limit AFTER every brief had
    # marked its run as asked, so the lanes past the limit were marked and never sent. The limit is applied here.
    limit = next((int(a.split("=", 1)[1]) for a in sys.argv[1:] if a.startswith("--limit=") and a.split("=", 1)[1].isdigit()), None)
    for sub in subs:
        if limit is not None and len(jobs) >= limit:
            print("LIMIT    %-8s left for the next round, not marked" % sub); continue
        runs = sorted(glob.glob("%s/%s-*/" % (RUNS, sub)),
                      key=lambda p: (_dir_time(p), os.path.basename(p.rstrip("/"))))
        if not runs: print("SKIP     %-8s no run folder" % sub); continue
        r = runs[-1]
        if already_asked(r) and sub not in args[1:]:
            print("ASKED    %-8s this run was already diagnosed (%s marker); a new run is asked again" % (sub, ASKED)); continue
        with open(r + "STATUS", encoding="utf-8") as f: status = f.read()[:400] if os.path.isfile(r + "STATUS") else "RUNNING"
        tails = []
        for g in sorted(glob.glob(r + "round*/grades/*.txt"))[-3:]:
            with open(g, errors="replace") as f: tails.append("--- %s\n%s" % (os.path.basename(g), f.read()[-2500:]))
        u = next((x for x in plan["units"] if sub in (x.get("sub_units") or [])), None)
        if u is None: print("SKIP     %-8s in no plan unit" % sub); continue
        with open(u["spec"], encoding="utf-8") as f: spec = f.read()
        i = spec.find(sub); sec = spec[max(0, i - 200):i + 5000] if i >= 0 else spec[:5000]
        head = ASK % sub + "\nRUN STATUS: %s\n\nGRADER OUTPUT (tails):\n%s\n\nSPEC SECTION:\n%s\n" % (status, "\n".join(tails)[-9000:], sec)
        blocks = []
        for f in (u.get("owns") or []):
            if f.endswith(".py") and os.path.isfile(f):
                with open(f, encoding="utf-8", errors="replace") as fh: blocks.append((f, fh.read()))
        brief, dropped = fit(head, blocks)
        if G.private_hits(brief): print("WITHHELD %-8s private term in the brief, not sent" % sub); continue
        pf = "%s/%s.md" % (out, sub)
        with open(pf, "w", encoding="utf-8") as f: f.write(brief)
        G.record_claim("diagnostic", sub, "this one fact will unblock the unit")
        if not dry: mark_asked(r)
        # "sensitivity" is EARNED by the private-term screen above (a brief carrying one is
        # WITHHELD and never reaches this line), not assumed: or_fanout refuses an unlabelled job.
        jobs.append({"id": "diag-" + sub, "model": os.environ.get("BROTHER_DIAG_MODEL", "deepseek"), "prompt_file": pf,
                     "out": "%s/out/%s.json" % (out, sub), "expect": "json",
                     "sensitivity": "public"})
        print("LANE     %-8s %d bytes%s" % (sub, len(brief.encode()), ", dropped %d file(s)" % len(dropped) if dropped else ""))
    with open(out + "/jobs.json", "w") as f: json.dump(jobs, f, indent=1)
    print("%d job(s) -> %s/jobs.json" % (len(jobs), out))
    return 0 if jobs else 1
if __name__ == "__main__": sys.exit(main())
