#!/usr/bin/env python3
"""Blind common endpoint for the A/B/C test. For every arm branch and every sample sub unit, in a scratch worktree of
the arm's tip with an EMPTY HOME: the sub unit's own done check (from its spec section at the base commit) must be RED on
the base and GREEN on the tip under python3 AND /usr/bin/python3; its non-test Python files changed on the arm branch
get a mutation sweep with that done check as the check. Delivery time = the first arm commit naming the sub unit, minus
the arm's start. Output rows carry blind labels; the label map is written to a separate file.
usage: score_arms.py            (reads ab/BASE.txt, ab/sample.json, ab/start-<a>.txt; writes ab/endpoint.json)
Verdicts per cell: DELIVERED, NOT DELIVERED, NO-DATA (done check not found, or already green on base)."""
import datetime, json, os, random, re, shutil, subprocess, sys, tempfile
AB = os.environ.get("ABC_DIR") or os.path.expanduser("~/.claude/evidence/loop-run-2026-09-23/ab"); REPO = os.path.expanduser("~/Brother")
BRANCH = os.environ.get("ABC_BRANCH_FMT") or "exp/abc-2026-09-23-%s"   # overrides only for the dry run on Phase 2
BASE = open(os.path.join(AB, "BASE.txt")).read().strip()
SAMPLE = json.load(open(os.path.join(AB, "sample.json")))["sample"]
ARMS = [a for a in ("a", "b", "c", "a2", "o") if os.path.isfile(os.path.join(AB, "start-%s.txt" % a))]

def sh(cmd, cwd, timeout=900, env=None):
    try:
        r = subprocess.run(cmd, cwd=cwd, shell=isinstance(cmd, str), capture_output=True, text=True, timeout=timeout, env=env)
        return r.returncode, (r.stdout + r.stderr)
    except subprocess.TimeoutExpired:
        return 124, "TIMEOUT"

def done_check(tree, unit, sub):
    plan = json.load(open(os.path.join(tree, "docs/plan/BROTHER-1.1.0-LAUNCH-WBS.json")))
    spec = open(os.path.join(tree, next(u["spec"] for u in plan["units"] if u["id"] == unit)), encoding="utf-8").read()
    h = re.search(r"^(#{2,4}) %s\b" % re.escape(sub), spec, flags=re.M)
    sec = re.search(r"^#{2,4} %s\b.*?(?=^#{2,%d} |\Z)" % (re.escape(sub), len(h.group(1))), spec, flags=re.M | re.S).group(0) if h else ""
    m = re.search(r"(?i)done[- ]check[^\n`]*`([^`\n]+)`", sec)
    if m: return m.group(1).strip()
    m = re.search(r"(?i)done[- ]check[^\n]*\n+```[a-z]*\n(.*?)\n```", sec, flags=re.S)   # EVERY line of the block runs
    lines = [l.strip() for l in m.group(1).splitlines() if l.strip() and not l.strip().startswith("#")] if m else []
    if lines: return " && ".join(lines)
    m = re.search(r"(?im)done[- ]check[^\n]*\n+\s*((?:/usr/bin/)?python3 [^\n]+)", sec)   # third wording: label line, then the bare command
    return m.group(1).strip() if m else None

def worktree(rev):
    d = tempfile.mkdtemp(prefix="abc-score-"); shutil.rmtree(d)
    rc, out = sh(["git", "clone", "-q", "--shared", "--no-checkout", REPO, d], REPO)
    rc = rc or sh(["git", "-C", d, "checkout", "-q", "--detach", rev], d)[0]   # a real clone: tests that read git work
    if rc: raise RuntimeError("clone %s: %s" % (rev, out[-200:]))
    return d

def drop(d):
    shutil.rmtree(d, ignore_errors=True)   # a scratch clone this script made, never a worktree of the repository

def green_both(tree, cmd):
    home = tempfile.mkdtemp(prefix="abc-home-"); env = dict(os.environ, HOME=home)
    res = {}
    for py in ("python3", "/usr/bin/python3"):
        c = re.sub(r"(^|&&\s*)python3\b", lambda m_: m_.group(1) + py, cmd); rc, out = sh(c, tree, env=env); res[py] = (rc, (out.strip().splitlines() or [""])[-1][:160])
    shutil.rmtree(home, ignore_errors=True)
    return all(v[0] == 0 for v in res.values()), res

def main():
    base = worktree(BASE); rows = []
    try:
        checks = {r["sub"]: done_check(base, r["unit"], r["sub"]) for r in SAMPLE}
        base_green = {s: (green_both(base, c)[0] if c else None) for s, c in checks.items()}
    finally:
        drop(base)
    for a in ARMS:
        br = BRANCH % a if "%s" in BRANCH else BRANCH
        start = datetime.datetime.strptime(open(os.path.join(AB, "start-%s.txt" % a)).read().strip(), "%Y-%m-%d %H:%M:%S")
        rc, log = sh(["git", "-C", REPO, "log", "--reverse", "--format=%H %ct %s", "%s..%s" % (BASE, br)], REPO)
        commits = [l.split(" ", 2) for l in log.splitlines() if l.strip()] if rc == 0 else []
        # EQUAL SLOTS: judge the arm at its last commit at or before its slot end, never a later landing
        end = datetime.datetime.strptime(open(os.path.join(AB, "end-%s.txt" % a)).read().strip(), "%Y-%m-%d %H:%M:%S")
        late = [x for x in commits if datetime.datetime.fromtimestamp(int(x[1])) > end]
        commits = [x for x in commits if datetime.datetime.fromtimestamp(int(x[1])) <= end]
        if late: print("arm %s: %d commit(s) after the slot end NOT judged" % (a, len(late)))
        tip = worktree(commits[-1][0] if commits else BASE)
        # EXTRAS: sub units named by in-slot commits beyond the sample (loop "<a> and <b> land:", arm C "ABC-C <id>:")
        named_ids = set()
        for x in commits:
            m1 = re.match(r"^(.+?) land:", x[2]); m2 = re.match(r"^ABC-C (\S+?):", x[2])
            if m1: named_ids |= {s.strip() for s in m1.group(1).split(" and ")}
            if m2: named_ids.add(m2.group(1))
        plan = json.load(open(os.path.join(tip, "docs/plan/BROTHER-1.1.0-LAUNCH-WBS.json")))
        unit_of = {s: u["id"] for u in plan["units"] for s in (u.get("sub_units") or [])}
        extras = [{"unit": unit_of[s], "sub": s, "size": None, "files": [], "extra": True}
                  for s in sorted(named_ids - {r["sub"] for r in SAMPLE}) if s in unit_of]
        for e in extras:
            if e["sub"] not in checks:
                bb = worktree(BASE)
                try:
                    checks[e["sub"]] = done_check(bb, e["unit"], e["sub"])
                    base_green[e["sub"]] = green_both(bb, checks[e["sub"]])[0] if checks[e["sub"]] else None
                finally:
                    drop(bb)
        try:
            for r in SAMPLE + extras:
                s, c = r["sub"], checks[r["sub"]]
                row = {"arm": a, "sub": s, "unit": r["unit"], "size": r["size"], "check": c, "extra": bool(r.get("extra"))}
                if not c or base_green[s]:
                    row["verdict"] = "NO-DATA"; row["why"] = "no done check in the section" if not c else "done check already green on the base"
                    rows.append(row); continue
                ok, res = green_both(tip, c); row["python3"], row["python39"] = res["python3"], res["/usr/bin/python3"]
                named = [x for x in commits if re.search(r"(?<![\w.])%s(?![\w])" % re.escape(s), x[2])]
                row["minutes_to_deliver"] = round((datetime.datetime.fromtimestamp(int(named[0][1])) - start).total_seconds() / 60, 1) if named else None
                row["verdict"] = "DELIVERED" if ok else "NOT DELIVERED"
                if ok and named:
                    # the sub unit's OWN commits, each against its parent: never files another sub unit changed
                    mods = set()
                    for x in named:
                        rc2, files = sh(["git", "-C", REPO, "diff", "--name-only", x[0] + "^", x[0]], REPO)
                        mods |= {f for f in files.split() if f.endswith(".py") and not os.path.basename(f).startswith("test_")
                                 and not f.startswith("bundle/")}   # test files by NAME; a module under tests/ is still code
                    mods = sorted(mods); row["swept"] = mods
                    surv = tot = 0
                    for m in mods:
                        rc3, out = sh([sys.executable, "-B", os.path.join(tip, "scripts/mutation_sweep.py"), "--module", m, "--check", c], tip, timeout=1800)
                        mm = re.search(r"(\d+) of (\d+) mutation\(s\) SURVIVED", out)
                        if mm: surv += int(mm.group(1)); tot += int(mm.group(2))
                    row["mutations"] = {"survived": surv, "total": tot} if tot else "NO-DATA"
                rows.append(row)
        finally:
            drop(tip)
    labels = dict(zip(ARMS, random.Random("abc-blind-2026-09-23").sample(["X", "Y", "Z", "V", "W"][:len(ARMS)], len(ARMS))))
    json.dump(labels, open(os.path.join(AB, "blind-map.json"), "w"))
    blind = [dict(r, arm=labels[r["arm"]]) for r in rows]
    json.dump(blind, open(os.path.join(AB, "endpoint.json"), "w"), indent=1)
    for r in blind:
        print("%s %-8s %-13s min=%s mut=%s" % (r["arm"], r["sub"], r["verdict"], r.get("minutes_to_deliver"), r.get("mutations")))

if __name__ == "__main__":
    main()
