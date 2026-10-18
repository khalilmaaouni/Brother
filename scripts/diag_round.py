#!/usr/bin/env python3
"""Diagnose every stuck sub unit in one round: ask for ONE fact plus the command that proves it, run that command,
and restart only the runners whose fact survived execution.

usage (launch worktree root): python3 scripts/diag_round.py [--dry] [--limit N] [--out DIR] [--code-root DIR]
--code-root names the frozen candidate the fan out runs from when no BROTHER_CODE_ROOT is set (the loop outside a proof
passes ~/.claude/bin/candidate). It is refused when it is not an absolute directory holding the fan out module, or when it
sits inside the working directory (the landing tree): landing tree code never runs.
Exit 0 when at least one runner was restarted on a proven fact, 1 when none was, 2 when the state is unreadable.

WHY. A runner that exhausts its rounds is almost never beaten by bad workers: it is missing ONE fact about the real
tree that its brief never showed, for instance that the module it was told to create already exists. Measured
2026-09-20: of 17 such lanes, 6 returned a fact whose own grep proved it, and each of those restarts then produced
a build. This was also the last rung of the loop with no command behind it, so a session with 17 stuck lanes named
it every pass and nothing moved.

THE MODEL PROPOSES, EXECUTION DECIDES. A diagnosis is only acted on when its own proving command RUNS and prints
what it predicted. An unproven fact restarts nothing, because restarting on a guess burns a lane on the same wall."""
import argparse
import json
import os
import subprocess
import sys

BIN = os.path.expanduser("~/.claude/bin")
RUNS = os.path.expanduser("~/.claude/evidence/unit-runs")


def brief_cmd(out_dir, subs, dry=False, limit=None):
    """--dry reaches the brief tool too: a dry round must not mark a real run as already asked. --limit reaches it as well,
    so no run is marked asked beyond the lanes this round will actually send (2026-09-27)."""
    return [sys.executable, os.path.join(BIN, "diag_brief.py"), out_dir] + list(subs) + (["--dry"] if dry else []) + (["--limit=%d" % limit] if limit else [])


def fanout_cmd(jobs, results, timeout, workers=40):
    """Never under 300 seconds: a shorter one cuts stragglers that were about to answer (a standing estate rule)."""
    return [sys.executable, "-m", "plugin.runtime.brother.core.or_fanout", jobs,
            "--workers", str(workers), "--retries", "1", "--timeout", str(max(300, timeout)),
            "--results", results]


def apply_cmd(out_dir):
    return [sys.executable, os.path.join(BIN, "diag_apply.py"), out_dir]


def summarise(apply_output):
    """(proven, unproven, refused, nodata) from diag_apply's own tally line. Absent counts read as zero proven,
    never as success: an unreadable tally must not look like a good round."""
    counts = {"PROVEN": 0, "UNPROVEN": 0, "REFUSED": 0, "NO-DATA": 0}
    for line in (apply_output or "").splitlines():
        if "|" in line and "PROVEN" in line:   # the len(bits) == 2 test below is what separates a tally from a per lane line
            for part in line.split("|"):
                bits = part.strip().split()
                if len(bits) == 2 and bits[0] in counts and bits[1].isdigit():
                    counts[bits[0]] = int(bits[1])
    return counts


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("subs", nargs="*", help="sub units to diagnose; default is every stuck one")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--limit", type=int, default=12, help="lanes per round, so one round cannot spend the night")
    ap.add_argument("--out", default=None)
    ap.add_argument("--code-root", default=None, help="frozen candidate to run the fan out from (see the docstring)")
    ap.add_argument("--timeout", type=int, default=600)
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))
    # the fan out runs in the code root, so every path it is handed is absolute
    out = os.path.abspath(args.out or os.path.expanduser("~/.claude/evidence/diag-round"))
    code_root = None
    if not args.dry and args.code_root:
        # an explicit candidate: refused (NO-DATA, nothing asked, nothing bought) unless it is a real directory with the
        # fan out module and is NOT the landing tree or inside it; the working directory is the landing tree here
        root, here = os.path.realpath(args.code_root), os.path.realpath(os.getcwd())
        if not (os.path.isabs(args.code_root) and os.path.isfile(os.path.join(root, "plugin", "runtime", "brother", "core", "or_fanout.py"))):
            print("NO-DATA: --code-root %s holds no fan out module; no lane is diagnosed" % args.code_root[:160])
            return 2
        if root == here or root.startswith(here + os.sep) or here.startswith(root + os.sep):
            print("NO-DATA: --code-root %s overlaps the working tree; landing tree code never runs, no lane is diagnosed" % args.code_root[:160])
            return 2
        code_root = root
    elif not args.dry:
        # THE FAN OUT RUNS THE FROZEN CANDIDATE (U3, B5-08): `-m` resolves the module from the cwd, which is the landing
        # tree. Asked before any brief is built; a refused code root (a proof phase with none set) asks nothing.
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "loop"))
        import model_router as MR
        try:
            code_root = MR.code_root()
        except MR.Refused as exc:
            print("NO-DATA: the code root is refused (%s); no lane is diagnosed" % str(exc)[:160])
            return 2
    # A HELD RUN'S DIAGNOSIS ASKS NO CLAUDE MODEL (S2 round 3, finding 1; decided: refuse, not served). The pass starts
    # this round detached, from the candidate tree, with neither the run's login holder nor its name; handing it one
    # would add a third holder bearing child of the pass, running candidate code past the pass's own life. So on a held
    # run (brother_login.HELD_RUN_VAR) a BROTHER_DIAG_MODEL on the claude transport, or one the registry cannot place,
    # is refused here, by name, before the brief marks any run asked and before any fan out starts. Unset, the brief's
    # default model (a bridge model, no login) runs as before; the fan out's own login_child refuses any Claude arm too.
    diag_model = os.environ.get("BROTHER_DIAG_MODEL", "")
    if not args.dry and diag_model and os.environ.get("BROTHER_LOGIN_HELD_RUN"):
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "loop"))
        try:
            import model_router as MR
            transport = (MR.registry().get(diag_model) or {}).get("transport")
        except Exception:   # sbe: allow-silent an unreadable registry places no model, which refuses below
            transport = None
        if transport not in ("bridge", "codex"):
            print("NO-DATA: BROTHER_DIAG_MODEL=%s needs a Claude login (transport %s), and this run's login holder is not "
                  "handed to the diagnosis; no lane is diagnosed (name a bridge model, or run it off the token lane)"
                  % (diag_model[:60], transport or "unknown"))
            return 2
    os.makedirs(out, exist_ok=True)

    r = subprocess.run(brief_cmd(out, args.subs, args.dry, args.limit), capture_output=True, text=True, timeout=1200)
    print((r.stdout or "").strip().splitlines()[-1] if r.stdout.strip() else "no brief output")
    jobs = os.path.join(out, "jobs.json")
    try:
        with open(jobs, encoding="utf-8") as fh:
            planned = json.load(fh)
    except (OSError, ValueError) as exc:
        print("NO-DATA: no jobs file was written (%s)" % exc)
        return 2
    if not planned:
        print("nothing to diagnose: no stuck sub unit has a brief")
        return 1
    if len(planned) > args.limit:
        planned = planned[:args.limit]
        with open(jobs, "w", encoding="utf-8") as fh:
            json.dump(planned, fh, indent=1)
        print("LIMIT   %d lane(s) this round; the rest wait for the next" % args.limit)
    print("LANES   %d: %s" % (len(planned), ", ".join(j["id"].replace("diag-", "") for j in planned[:6])))
    if args.dry:
        return 0
    subprocess.run(fanout_cmd(jobs, os.path.join(out, "results.json"), args.timeout),
                   capture_output=True, timeout=args.timeout * 4, cwd=code_root)
    a = subprocess.run(apply_cmd(out), capture_output=True, text=True, timeout=1800)
    text = a.stdout or ""
    for line in text.splitlines():
        if line.startswith(("PROVEN", "UNPROVEN", "REFUSED", "NO-DATA")):
            print(line[:150])
    counts = summarise(text)
    print("ROUND   %d proven and noted for the next brief, %d unproven, %d refused, %d no data"
          % (counts["PROVEN"], counts["UNPROVEN"], counts["REFUSED"], counts["NO-DATA"]))
    return 0 if counts["PROVEN"] else 1


if __name__ == "__main__":
    sys.exit(main())
