#!/usr/bin/env python3
"""Routing by measured yield and cost (P4 of the 2026-09-24 diagnosis, decision A4): the journal's per model validity
and billed cost over the newest runs become a PROPOSED worker mix, printed as advice with its evidence. The intake and
the owner decide; this file never sets anything.

usage (launch worktree root): mix_advice.py [--runs N] [--min-decisions 20] [--floor 0.6]     mix_advice.py --selftest
prints one line per model (decisions, valid, rate, USD per valid answer or NO-DATA) and one ADVICE line with a
BROTHER_WORKER_MIX proposal, or NO-DATA when the newest runs hold fewer decisions than --min-decisions.

RULES, each measured before it was written: a model under the floor over the minimum decisions gets no lane (Muse
answered validly 22 of 111 times on 2026-09-24, 19.8 percent, and held two of eight lanes); lanes go to models above
the floor in proportion to validity divided by cost per valid answer, cost NO-DATA read as the most expensive known
(never as free); fewer than the minimum decisions is NO-DATA, never a proposal; a builder that never appears in the
journal is not proposed (the journal, not the registry, is the evidence)."""
import glob, json, os, subprocess, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model_router  # noqa: E402, code_root() decides which tree the learning report runs from

RUNS = os.path.expanduser("~/.claude/evidence/loop-runs")
WORKERS = 8


def gather(run_dirs, run=None, cwd=None):
    """{model: {"n", "pass", "cost"}} summed over the runs' journal reports (dream_report --json); an unreadable run
    is skipped by name on stderr, never counted. The report runs from the code root unless CWD names another tree:
    from the inherited cwd it was the landing tree's copy in a proof (Codex audit D2, 2026-09-27). Resolved once,
    outside the per run skip, so a proof with no code root refuses (model_router.Refused) and never reads as a set of
    unreadable runs. A run directory is made absolute first: read against the code root it would name another tree."""
    cwd = model_router.code_root() if cwd is None else cwd
    out = {}
    for rd in run_dirs:
        try:
            r = (run or subprocess.run)([sys.executable, "-B", "-m", "plugin.runtime.brother.core.dream_report", os.path.abspath(rd), "--json"], capture_output=True, text=True, timeout=120, cwd=cwd)
            d = json.loads(r.stdout)
        except Exception as exc:   # sbe: allow-silent a run that cannot be read is named and skipped, the advice stays honest on the rest
            sys.stderr.write("mix_advice: %s skipped (%s)\n" % (rd, str(exc)[:80])); continue
        for row in d.get("pass_rate") or []:
            if row.get("kind") != "openrouter.model": continue
            m = out.setdefault(str(row.get("chosen")), {"n": 0, "pass": 0, "cost": 0.0, "costed": 0})
            m["n"] += int(row.get("n") or 0); m["pass"] += int(row.get("pass") or 0)
        c = d.get("cost") or {}
        # cost per model is not in the report yet (D2 landed at 12:2x on 2026-09-24); total measured cost rides along
        if isinstance(c.get("total"), (int, float)): out.setdefault("_total", {"n": 0, "pass": 0, "cost": 0.0, "costed": 0})["cost"] += float(c["total"])
    out.pop("_total", None)
    return out


def advise(stats, workers=WORKERS, min_decisions=20, floor=0.6):
    """(lines, mix or None). Pure."""
    total = sum(v["n"] for v in stats.values())
    lines = []
    if total < min_decisions:
        return ["NO-DATA: %d decision(s) in the newest runs, %d needed before a mix is proposed" % (total, min_decisions)], None
    cpv = {}
    for m, v in sorted(stats.items()):
        rate = v["pass"] / v["n"] if v["n"] else 0.0
        cost = (v["cost"] / v["pass"]) if v.get("costed") and v["pass"] else None
        cpv[m] = (rate, cost)
        lines.append("%-10s decisions %4d | valid %4d | rate %.2f | USD per valid %s" % (m, v["n"], v["pass"], rate, ("%.4f" % cost) if cost is not None else "NO-DATA"))
    known = [c for _, c in cpv.values() if c is not None]; worst = max(known) if known else 1.0
    weight = {}
    for m, (rate, cost) in cpv.items():
        if stats[m]["n"] >= min_decisions and rate >= floor:
            weight[m] = rate / (cost if cost is not None else worst)
    if not weight:
        return lines + ["ADVICE: no model is above the floor %.2f over %d decisions; keep the current mix and read the refusal histogram" % (floor, min_decisions)], None
    tot = sum(weight.values()); lanes = {m: max(1, int(round(workers * w / tot))) for m, w in weight.items()}
    mix = ",".join("%s:%d" % (m, n) for m, n in sorted(lanes.items(), key=lambda x: -x[1]))
    dropped = [m for m in cpv if m not in weight]
    lines.append("ADVICE: BROTHER_WORKER_MIX=%s (validity over cost per valid answer; floor %.2f over %d decisions%s)" % (mix, floor, min_decisions, ("; dropped: " + ", ".join(dropped)) if dropped else ""))
    return lines, mix


def cheapest_valid(stats, min_decisions=20, floor=0.6):
    """The model with the lowest KNOWN cost per valid answer among the models that clear the minimum decisions and
    the validity floor, or None when no such model has a known cost. Pure: no subprocess, no new import, no clock.

    H7.b (REQ-H-CHEAPEST) takes the model the planner and the repair advisor run on from this one name, so the fail
    direction is the whole point of the function. Every way of NOT knowing ends in None, never in a guess: a cost the
    journal never recorded (costed falsy; gather does not fill a per model cost yet) is UNKNOWN and is never read as
    free, which is the one mistake that would rank an unmeasured model first; a rate under the floor gets no lane;
    too few decisions is no evidence. Hostile stats, minimum or floor returns None. A corrupt row is named on stderr
    and skipped: it is never counted, it is never chosen, and it never raises."""
    if not isinstance(stats, dict):
        return None
    if isinstance(min_decisions, bool) or not isinstance(min_decisions, int):
        return None
    if isinstance(floor, bool) or not isinstance(floor, (int, float)) or floor != floor:
        return None
    winner, key = None, None
    for name in stats:
        row = stats[name]
        if not isinstance(name, str) or not name or name == "None":
            sys.stderr.write("mix_advice: model %r skipped: not a model name\n" % (name,))
            continue
        if not isinstance(row, dict):
            sys.stderr.write("mix_advice: model %s skipped: the row is not a dict\n" % name)
            continue
        n, passed = row.get("n"), row.get("pass")
        if isinstance(n, bool) or isinstance(passed, bool) or not isinstance(n, int) or not isinstance(passed, int) or n <= 0 or passed < 0 or passed > n:
            sys.stderr.write("mix_advice: model %s skipped: n=%r and pass=%r are not a possible pair\n" % (name, n, passed))
            continue
        if n < min_decisions or passed / float(n) < floor:
            continue
        c, per_valid = row.get("cost"), None
        if row.get("costed") and passed > 0 and isinstance(c, (int, float)) and not isinstance(c, bool) and 0 <= c < float("inf"):
            per_valid = c / float(passed)
        if per_valid is None:
            continue
        k = (per_valid, -passed / float(n), name)
        if key is None or k < key:
            winner, key = name, k
    return winner


def newest_runs(root, n):
    """The newest n run folders that hold a decision journal (scripts/journal.py JOURNAL_FILENAME). A folder with none
    recorded no decision (a driver refused at its first line, or a test that minted one: 32 empty folders on 2026-09-24
    took all three slots, the advice read 0 decisions and day-run refused to start), so it never displaces a run that did."""
    runs = [d for d in glob.glob(os.path.join(root, "run-*")) if os.path.isfile(os.path.join(d, "journal.jsonl"))]
    return sorted(runs, key=os.path.getmtime)[-n:]


def main(argv=None):
    a = list(sys.argv[1:] if argv is None else argv)
    if a[:1] == ["--selftest"]: return selftest()
    n = int(a[a.index("--runs") + 1]) if "--runs" in a else 3
    md = int(a[a.index("--min-decisions") + 1]) if "--min-decisions" in a else 20
    fl = float(a[a.index("--floor") + 1]) if "--floor" in a else 0.6
    runs = newest_runs(RUNS, n)
    if not runs: print("NO-DATA: no run directory with a journal under %s" % RUNS); return 3
    lines, mix = advise(gather(runs), min_decisions=md, floor=fl)
    for l in lines: print(l)
    return 0 if mix else 3


def selftest():
    s = {"deepseek": {"n": 40, "pass": 32, "cost": 0.08, "costed": 1}, "muse": {"n": 111, "pass": 22, "cost": 0.3, "costed": 1}}
    lines, mix = advise(s)
    s2 = {"deepseek": {"n": 30, "pass": 24, "cost": 0.0, "costed": 0}, "sonnet": {"n": 30, "pass": 27, "cost": 0.0, "costed": 0}}
    l2, m2 = advise(s2)
    cases = [("a model under the floor gets no lane and is named as dropped", mix == "deepseek:8" and "dropped: muse" in lines[-1]),
             ("fewer decisions than the minimum is NO-DATA, never a proposal", advise({"deepseek": {"n": 5, "pass": 5, "cost": 0, "costed": 0}})[1] is None and advise({"deepseek": {"n": 5, "pass": 5, "cost": 0, "costed": 0}})[0][0].startswith("NO-DATA")),
             ("no cost known: lanes follow validity alone, both above the floor", m2 is not None and "sonnet:" in m2 and "deepseek:" in m2 and sum(int(x.split(":")[1]) for x in m2.split(",")) in (8, 9)),
             ("cost NO-DATA is read as the most expensive known, never free", advise({"a": {"n": 40, "pass": 32, "cost": 0.0, "costed": 0}, "b": {"n": 40, "pass": 32, "cost": 0.4, "costed": 1}})[1] == "a:4,b:4"),
             ("every model gets its evidence line", sum(1 for l in lines if "| valid" in l) == 2),
             ("nobody above the floor keeps the current mix, said", advise({"x": {"n": 50, "pass": 10, "cost": 0.0, "costed": 0}})[1] is None)]
    # THE ENTRY POINT: main() over a root of two journaled runs and three NEWER folders with no journal (the shape of
    # 2026-09-24's leak), gather stubbed to record which folders it was handed.
    import contextlib, io, shutil, tempfile
    mod, root, seen = sys.modules[__name__], tempfile.mkdtemp(prefix="mix-advice-"), []
    keep = (mod.RUNS, mod.gather)
    try:
        for i, name in enumerate(("run-a", "run-b", "run-c", "run-d", "run-e")):
            d = os.path.join(root, name); os.makedirs(d)
            if name in ("run-a", "run-b"): open(os.path.join(d, "journal.jsonl"), "w").close()
            os.utime(d, (1000 + i, 1000 + i))
        mod.RUNS, mod.gather = root, (lambda rds, **k: seen.extend(rds) or {"deepseek": {"n": 40, "pass": 32, "cost": 0.0, "costed": 0}})
        with contextlib.redirect_stdout(io.StringIO()): rc_mixed = main(["--runs", "3"])
        read_mixed = sorted(os.path.basename(d) for d in seen); del seen[:]
        for x in ("run-a", "run-b"): os.remove(os.path.join(root, x, "journal.jsonl"))
        out = io.StringIO()
        with contextlib.redirect_stdout(out): rc_empty = main(["--runs", "3"])
    finally:
        mod.RUNS, mod.gather = keep; shutil.rmtree(root, ignore_errors=True)
    cases += [("main reads the newest runs with a journal; newer folders with none never take a slot", rc_mixed == 0 and read_mixed == ["run-a", "run-b"]),
              ("main over folders with no journal is NO-DATA naming the journal, never advice from zero", rc_empty == 3 and "with a journal" in out.getvalue() and seen == [])]
    bad = [n for n, ok in cases if not ok]
    print("selftest: %d cases, %s" % (len(cases), "OK" if not bad else "FAILED: " + ", ".join(bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
