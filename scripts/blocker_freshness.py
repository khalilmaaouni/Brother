#!/usr/bin/env python3
"""Re-measure every release blocker's own done-check, cheaply, on demand.

THE DEFECT THIS EXISTS FOR, measured 2026-08-25. Six release-blocking premises
went stale in two nights (BR-20, M7, M24, BR-21, M30, and BR-24's first half),
every one found by measuring rather than by reading the ledger. Each stale row
cost a session either a rebuild of something that already shipped or a false
close of something still open.

WHY THE LIST DECAYS, and it is not carelessness. Measured over the blocker rows
the day this was written: 8 of 9 carried a done_check that is PROSE ONLY, with
nothing a machine can run. Re-measuring the list therefore cost a hand
derivation per row, so nobody did it, so it rotted. A list that is expensive to
re-measure will always go stale.

AND THE ONE RUNNABLE CHECK WAS THE ONE THAT LIED. BR-24's check named pull
request 48, which is MERGED, while the actual BR-24 work is pull request 58.
Run verbatim it returned MERGED, and the row would have read DONE. It failed in
the direction of closing an open row.

UPDATED 2026-08-27, and the update is itself the lesson: the paragraph above used
to end "which is OPEN", and that had become FALSE. Re-measured, both are MERGED
(pull request 48 is the reconciliation feature, pull request 58 is titled "BR-24:
an idempotence check whose own measurement was not idempotent", the flake fix),
and `python3 -m unittest tools.test_bm_reconcile -v` on main exits 0 with 17 tests
OK. So BR-24 is genuinely DONE. A STALENESS DETECTOR THAT HARDCODES A STATE GOES
STALE ITSELF: this file spent three days telling readers a merged pull request was
open, which is the exact failure class it was written to catch.

WHAT THIS DOES. For every blocker, it extracts commands from the row's own
done_check, runs the ones on a strict READ-ONLY allowlist, and reports each
row's verdict with the command's OWN exit code. A row with no runnable command
is NO-DATA, named as such: not a pass, not a failure, and not silence.

SAFETY. Commands come from a data file, so nothing is executed unless it
matches the allowlist below: gh read verbs, git read verbs, and unittest. Any
other command is reported as present-but-not-auto-runnable rather than run.
Nothing here writes, merges, pushes or deletes.

    python3 scripts/blocker_freshness.py

Exit 0 when every blocker is genuinely still blocking or is NO-DATA. Exit 1 if
any blocker is STALE, meaning its own done_check is already SATISFIED and the
row should have been closed. That inversion is the whole point: for a blocker,
a passing check is the alarm.
"""
import json
import pathlib
import re
import shlex
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
ROADMAP = REPO / "docs" / "plan" / "ROADMAP-2026-08-23.json"

# READ ONLY. Anything not matching is reported, never executed.
ALLOW = (
    re.compile(r"^gh (pr view|pr list|api|repo view) "),
    re.compile(r"^git (ls-remote|log|rev-parse|cat-file|status|branch) "),
    re.compile(r"^(python3|/usr/bin/python3) -m unittest "),
    # Our own scripts, WITH their flags. The first version anchored on $ and so
    # rejected `... --strict`, which made a genuinely runnable check read as
    # NO-DATA. A tool that under-reports what it can check is as misleading as
    # one that over-reports: it hides the very rows it exists to surface.
    re.compile(r"^python3 scripts/[a-z_]+\.py(\s+--[a-z-]+)*$"),
)


# Characters that mean the extracted span is a SENTENCE, not a command. A
# done_check is prose, so a naive span grabs pipes, redirects and clauses. The
# first three versions of this tool ran those and reported the RESULTING errors
# as findings: a pipe handed to git produced "fatal: ambiguous argument", prose
# handed to gh produced "accepts 1 arg(s), received 9", and both were printed as
# REGRESSED. THAT IS AN INSTRUMENT REPORTING ITS OWN PARSING BUG AS A DEFECT,
# which is worse than no tool because it teaches the reader to ignore it.
UNPARSEABLE = ("|", ">", "<", "&&", "||", "$(", "`")

# SHELL EXPANSIONS this runner does NOT perform. shlex.split does not expand
# braces or globs, so a check written as
#     gh api repos/owner/{RepoA,RepoB,RepoC}
# reaches gh as a literal brace and returns 404. The first version reported
# that 404 as "the check's REFERENCE is stale", which was FALSE: the reference
# was fine and THE RUNNER COULD NOT RUN IT. Run properly all three repositories
# answered exactly as the check asserts.
#
# A checker that cannot execute a command must SAY SO, naming what it could not
# do, and must never convert its own limitation into a claim about the thing it
# was checking. That is the seventh time this tool reported its own conditions
# as the world's, and the only time the wrong diagnosis reached a commit.
NEEDS_SHELL = ("{", "*", "~", "$")

# Commands whose EXIT CODE carries no verdict: they succeed whatever the world
# looks like, and the condition lives in their OUTPUT. BR-21's check reads
# "git status --short shows CHECKSUMS.sha256 committed (clean tree)", and git
# status exits 0 whether the tree is clean or filthy. Running it and reporting
# "holds" asserts the code where the claim is about the text.
#
# This is the exact MIRROR of this estate's own recorded failure, where eleven
# tests asserted a gate's printed verdict and none asserted its exit code. Both
# are the same mistake: reading the half that does not carry the answer.
ALWAYS_ZERO = ("git status", "git log", "git branch", "git ls-files", "git ls-remote")


def inconclusive(cmd):
    return cmd.startswith(ALWAYS_ZERO)


def needs_shell(cmd):
    return any(ch in cmd for ch in NEEDS_SHELL)


def parseable(cmd):
    """Could this span plausibly BE a command, rather than a sentence about one."""
    if any(ch in cmd for ch in UNPARSEABLE):
        return False
    if needs_shell(cmd):
        return False
    try:
        parts = shlex.split(cmd)
    except ValueError:
        return False
    return 1 < len(parts) <= 12


def runnable(cmd):
    return parseable(cmd) and any(p.match(cmd) for p in ALLOW)


def extract(done_check):
    """Commands a person could actually run, taken from the row's own text."""
    if not done_check:
        return []
    out = []
    # A command can follow a colon as easily as a semicolon: R3's check reads
    # "... is checkable now: /usr/bin/python3 -m unittest ...", and the first
    # version split only on ; and . and newline, so that command was invisible
    # and the row reported as prose only.
    for m in re.finditer(r"(?:^|[;\n:]|\. )\s*((?:gh|git|python3|/usr/bin/python3|sh|bash)\b[^;\n]*)",
                         done_check):
        cmd = m.group(1).strip().rstrip(".")
        # A done_check is a SENTENCE, not a shell line: it reads "gh pr view 58
        # ... --json state prints MERGED". Taking the whole span hands `gh`
        # three arguments and it exits 2, which this tool then reported as the
        # ROW failing. That is an instrument reporting its own parsing bug as a
        # finding, which is the failure family this estate keeps meeting. Cut
        # at the prose verb that ends the command and begins the assertion.
        for marker in (" prints ", " returns ", " passes ", " shows ",
                       " confirms ", " exits ", " reports "):
            i = cmd.find(marker)
            if i > 0:
                cmd = cmd[:i]
        cmd = cmd.strip()
        if len(cmd) > 8:
            out.append(cmd)
    return out


def main():
    with ROADMAP.open(encoding="utf-8") as fh:
        d = json.load(fh)
    rows = d if isinstance(d, list) else d.get("rows") or []
    # EVERY OPEN ROW, not only the blockers. On 2026-08-25 five of fourteen CARRY
    # rows turned out to be work already finished whose disposition nobody moved,
    # and they were found BY HAND, which is exactly what this tool exists to stop.
    # A CARRY row and a BLOCKER row decay identically: both describe work not yet
    # done, so for both a PASSING check means the row is STALE.
    OPEN = ("BLOCKER", "CARRY")
    blockers = [r for r in rows if str(r.get("disposition", "")).startswith(OPEN)]
    # DONE rows go stale in the OTHER direction and nobody watches for it. A
    # closed row whose own check now FAILS is a REGRESSION: the thing that made
    # it done stopped being true, and the ledger still says done. Same decay,
    # opposite sign, and arguably the more dangerous one because a blocker at
    # least gets looked at.
    completed = [r for r in rows if r.get("disposition") == "DONE"]

    fails, nodata, passes, refused = 0, 0, 0, 0
    print("OPEN rows (BLOCKER and CARRY), each re-measured against its OWN done-check")
    print("%-22s %-9s %-9s %s" % ("ROW", "STATE", "VERDICT", "DETAIL"))
    for r in blockers:
        rid = str(r.get("id"))
        disp = str(r.get("disposition", ""))[:8]
        cmds = [c for c in extract(r.get("done_check")) if runnable(c)]
        blocked = [c for c in extract(r.get("done_check")) if not runnable(c)]
        if not cmds:
            nodata += 1
            shellish = [c for c in blocked if needs_shell(c)]
            if shellish:
                why = ("%d command(s) need SHELL EXPANSION this runner does not do; "
                       "THIS IS THIS TOOL'S LIMIT, NOT A FINDING ABOUT THE ROW" % len(shellish))
            elif blocked:
                why = "%d command(s) present but not on the read-only allowlist" % len(blocked)
            else:
                why = "done_check is prose only, nothing to run"
            print("%-22s %-9s %-9s %s" % (rid, disp, "NO-DATA", why))
            refused += len(blocked)
            continue
        worst, detail, inconclusive_run = 0, "", None
        for c in cmds:
            try:
                p = subprocess.run(shlex.split(c), capture_output=True, text=True,
                                   timeout=60, cwd=str(REPO))
                code = p.returncode
            except subprocess.TimeoutExpired:
                # A TIMEOUT IS NOT A VERDICT ABOUT THE ROW. Measured 2026-08-25
                # under load 200 on 8 cores: a check that normally answers in a
                # second did not finish in 60, and the first version printed
                # that as the row's state. The machine was the finding, not the
                # row. Same family as every other misreport this tool has made:
                # an instrument reporting its own conditions as the world's.
                inconclusive_run = "TIMED OUT after 60s; machine load, not a verdict about this row"
                break
            except Exception as exc:
                inconclusive_run = "could not run: %s" % type(exc).__name__
                break
            # A tool that cannot find what it was asked about has not judged it.
            blob = ((p.stdout or "") + (p.stderr or "")).lower()
            if code != 0 and ("not found" in blob or "could not resolve" in blob):
                inconclusive_run = "the check's target does not exist; the REFERENCE is stale"
                break
            if code > worst:
                worst = code
            if not detail:
                first = (p.stdout or p.stderr or "").strip().splitlines()[:1]
                detail = first[0][:58] if first else ""
        if inconclusive_run:
            nodata += 1
            print("%-22s %-9s %-9s %s" % (rid, disp, "NO-DATA", inconclusive_run))
            continue
        # THE VERDICT SEMANTICS ARE INVERTED FOR A BLOCKER, and getting this
        # backwards was the first version's defect. A blocker's done_check
        # describes the state in which the row would be DONE. So:
        #   check FAILS  -> the row genuinely still blocks. Expected. Not an alarm.
        #   check PASSES -> the row is STALE: it is marked BLOCKER and its own
        #                   condition is already satisfied. THAT is the alarm,
        #                   and it is exactly the shape that cost six premises
        #                   in two nights.
        # A done_check can carry BOTH runnable commands and prose conditions a
        # person must judge. Running the machine half and calling the row STALE
        # is a narrow check answering a broader question, which is how a tool
        # closes a row it only partly examined. R3 is exactly that shape: its
        # context-budget clause is a command and its four-journeys clause needs
        # a human. So a PASS only means STALE when the WHOLE check was run.
        residual = r.get("done_check") or ""
        for c in cmds + blocked:
            residual = residual.replace(c, " ")
        residual = " ".join(residual.split())
        partly_prose = len(residual) > 80

        if worst == 0 and partly_prose:
            nodata += 1
            print("%-22s %-9s %-9s %s" % (rid, disp, "PARTIAL",
                  "machine half PASSES; %d chars of prose need a human" % len(residual)))
            continue
        if worst == 0:
            fails += 1; v = "STALE"
            detail = "own done_check is SATISFIED: this row should be DONE. " + detail
        else:
            passes += 1; v = "open"
        print("%-22s %-9s %-9s exit %s  %s" % (rid, disp, v, worst, detail[:52]))

    regressed = 0
    checked_done = 0
    print()
    print("DONE rows, re-measured: a FAILING check here is a REGRESSION")
    for r in completed:
        rid = str(r.get("id"))
        allcmds = extract(r.get("done_check"))
        cmds = [c for c in allcmds if runnable(c)]
        unparsed = [c for c in allcmds if not parseable(c)]
        if not cmds:
            if unparsed:
                print("%-22s %-9s %s" % (rid, "NO-DATA",
                      "%d span(s) look like prose, not a command; NOT run" % len(unparsed)))
            continue
        checked_done += 1
        worst, detail = 0, ""
        for c in cmds:
            try:
                pr = subprocess.run(shlex.split(c), capture_output=True, text=True,
                                    timeout=60, cwd=str(REPO))
                code = pr.returncode
            except Exception as exc:
                code, pr = 2, None
                detail = "%s: %s" % (type(exc).__name__, exc)
            if code > worst:
                worst = code
            if pr is not None and not detail:
                first = (pr.stdout or pr.stderr or "").strip().splitlines()[:1]
                detail = first[0][:52] if first else ""
        if worst == 0 and all(inconclusive(c) for c in cmds):
            print("%-22s %-9s %s" % (rid, "NO-DATA",
                  "exit code carries no verdict here; the condition is in the OUTPUT"))
        elif worst == 0:
            print("%-22s %-9s %s" % (rid, "holds", detail[:60]))
        else:
            regressed += 1
            print("%-22s %-9s exit %s  %s" % (rid, "REGRESSED", worst, detail[:52]))
    if checked_done == 0:
        print("  NO-DATA: no DONE row carries a runnable check.")
    print("  %d of %d DONE rows carry a runnable check; the rest cannot be re-measured."
          % (checked_done, len(completed)))

    print()
    print("open rows %d   genuinely open %d   STALE %d   no-data %d"
          % (len(blockers), passes, fails, nodata))
    if nodata:
        print("NO-DATA AND PARTIAL ARE NOT PASSES. %d open row(s) cannot be FULLY re-measured by"
              % nodata)
        print("machine. A PARTIAL row had its runnable clause pass and its prose clause unjudged;")
        print("closing it on that alone would be a narrow check answering a broader question.")
        print("The rest are prose only, which is why this list goes stale: re-measuring")
        print("costs a hand derivation per row, so nobody does it.")
    if refused:
        print("%d command(s) in done_checks were NOT run because they are not read-only." % refused)
    if regressed:
        print("%d DONE row(s) REGRESSED: the ledger says done and the check disagrees." % regressed)
    return 1 if (fails or regressed) else 0


if __name__ == "__main__":
    sys.exit(main())
