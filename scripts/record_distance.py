#!/usr/bin/env python3
"""ORCH-33: distance to the repository of record, with a forced disposition.

THE GAP THIS CLOSES: measuring distance to the repository of record is not
enough on its own, because a number sitting on a screen is exactly the kind
of "state" this estate has already shipped on wrongly (see ASSUMED STATE IS
NEVER GROUND TRUTH). Seven confirmed rework instances landed between 686 and
5266 commits behind, including two same-day fixes that were correct, tested
and pushed onto a tree thousands of commits stale, each already fixed
upstream: worth nothing. Measuring the gap did not stop that; only a
recorded, checked DECISION about what to do with the gap can.

WHAT THIS DOES: measures how far a working tree is behind AND ahead of its
repository of record (both directions, from git itself, never guessed), then
refuses to hand back a clean answer unless the caller also states a
disposition:

  UPDATE      rebase or re-cut the work onto the current base
  REPRODUCE   confirm the defect or claim still reproduces on the current
              base before touching it
  HISTORICAL  an intentional target, held here on purpose, with a reason

A disposition that is missing, not one of the three names above, or
HISTORICAL with no stated reason, is REFUSED. This never silently defaults
to "probably fine": a refusal blocks whatever process called this, the same
direction every boundary check in this estate is required to fail in.

WHY EVERY DISTANCE FORCES A DISPOSITION, INCLUDING ZERO: zero behind and
zero ahead does not exempt the caller. The cost of naming a disposition is
one flag; the cost of skipping it, on this record, is thousands of wasted
commits. A disposition recorded at distance zero is also what a LATER
distance check has to compare against, so skipping it there breaks the
chain everywhere else it is measured.

DIVERGENCE: an ahead-only count reads as "safe to push" and hides that the
tree has also drifted behind, which is exactly the shape this estate has
shipped wrong before. So the count is always both directions from one git
call (`rev-list --left-right --count`), never ahead alone.

DEFAULT LOCAL REF: the caller's current branch, resolved with
`git symbolic-ref --short HEAD`. A detached HEAD has no branch name, so that
resolution fails on purpose rather than silently falling back to the literal
ref "HEAD": a literal ref is still available via `--local`, explicitly,
because guessing what ref a detached checkout means to compare is exactly
the kind of assumption this module exists to refuse.

EDGES HANDLED, NAMED HERE SO THEY ARE NOT REDISCOVERED BY INCIDENT:
  zero behind             still forces a disposition (see above)
  diverged                behind and ahead both nonzero: reported, never
                          collapsed into a single number
  remote does not exist   git rev-list fails, NO-DATA
  git exits 0, no output  cannot parse two counts, NO-DATA, never "0 behind"
  ref does not resolve    git rev-list fails, NO-DATA
  detached HEAD           default --local resolution fails, NO-DATA unless
                          an explicit --local is given

  0  ANSWERED   distance measured, disposition accepted
  1  REFUSED    distance measured, disposition missing/unknown/unjustified
  2  NO-DATA    distance could not be measured: refuse, never "0 behind"

Usage:
  python3 scripts/record_distance.py --repo R --remote hub/main \\
      --disposition UPDATE [--local REF] [--reason TEXT]
"""
import argparse
import subprocess
import sys

#: The only three answers a caller may give. Anything else is REFUSED, not
#: coerced or guessed at.
DISPOSITIONS = ("UPDATE", "REPRODUCE", "HISTORICAL")


class DistanceUnknown(Exception):
    """Distance could not be measured. Never becomes a fabricated 0."""


def _run(cmd, run=None):
    run = run or (lambda c: subprocess.run(c, capture_output=True, text=True, timeout=60))
    try:
        return run(cmd)
    except (OSError, subprocess.SubprocessError) as exc:
        raise DistanceUnknown("could not run %s: %s" % (" ".join(cmd), exc))


def current_branch(repo, run=None):
    """The checkout's current branch name, or DistanceUnknown when detached.

    Used only as the default local ref: a caller who wants to compare a
    detached checkout, a tag, or a raw SHA passes --local explicitly instead
    of relying on this guess.
    """
    proc = _run(["git", "-C", repo, "symbolic-ref", "--short", "HEAD"], run=run)
    if proc.returncode != 0:
        raise DistanceUnknown(
            "no current branch (detached HEAD or unresolved ref): %s" % proc.stderr.strip())
    name = proc.stdout.strip()
    if not name:
        raise DistanceUnknown("git symbolic-ref returned no name")
    return name


def measure_distance(repo, remote_ref, local_ref, run=None):
    """(behind, ahead) commit counts between local_ref and remote_ref.

    One `git rev-list --left-right --count` call answers both directions,
    so an ahead-only reading can never hide a divergence.

    Raises DistanceUnknown, never a fabricated (0, 0), when: git is not on
    PATH or fails to run, either ref (including the remote itself) does not
    resolve, or git exits 0 with output that will not parse as two integers.
    """
    proc = _run(["git", "-C", repo, "rev-list", "--left-right", "--count",
                 "%s...%s" % (remote_ref, local_ref)], run=run)
    if proc.returncode != 0:
        raise DistanceUnknown("git rev-list exited %s: %s"
                               % (proc.returncode, proc.stderr.strip()))
    parts = proc.stdout.split()
    if len(parts) != 2:
        raise DistanceUnknown("git rev-list gave unparseable output: %r" % proc.stdout)
    try:
        behind, ahead = int(parts[0]), int(parts[1])
    except ValueError:
        raise DistanceUnknown("git rev-list gave non-numeric output: %r" % proc.stdout)
    return behind, ahead


def evaluate(behind, ahead, disposition, reason=None):
    """(verdict, message). verdict is "ACCEPTED" or "REFUSED".

    Every measured distance, including 0 behind and 0 ahead, must carry one
    of the three named dispositions. HISTORICAL additionally requires a
    non-empty reason: an intentional target with no stated reason is
    indistinguishable from one nobody ever decided about.
    """
    if disposition not in DISPOSITIONS:
        return "REFUSED", ("disposition %r is missing or not one of %s"
                            % (disposition, ", ".join(DISPOSITIONS)))
    if disposition == "HISTORICAL" and not (reason and reason.strip()):
        return "REFUSED", "HISTORICAL disposition requires a stated reason"
    shape = "diverged, " if behind and ahead else ""
    detail = "%s%d behind, %d ahead" % (shape, behind, ahead)
    message = "%s (%s)" % (detail, disposition)
    if disposition == "HISTORICAL":
        message += ": %s" % reason.strip()
    return "ACCEPTED", message


def main(argv=None, run=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo", default=".", help="working tree to measure")
    ap.add_argument("--remote", required=True,
                    help="the repository of record's ref, e.g. hub/main")
    ap.add_argument("--local", help="ref to compare (default: current branch)")
    ap.add_argument("--disposition", choices=DISPOSITIONS)
    ap.add_argument("--reason", help="required when --disposition HISTORICAL")
    a = ap.parse_args(argv)
    try:
        local_ref = a.local or current_branch(a.repo, run=run)
        behind, ahead = measure_distance(a.repo, a.remote, local_ref, run=run)
    except DistanceUnknown as exc:
        print("record_distance: NO-DATA, distance could not be measured (%s). "
              "Refusing: this is never reported as 0 behind." % exc)
        return 2
    verdict, message = evaluate(behind, ahead, a.disposition, a.reason)
    print("record_distance: %s, %s vs %s: %s" % (verdict, local_ref, a.remote, message))
    return 0 if verdict == "ACCEPTED" else 1


if __name__ == "__main__":
    sys.exit(main())
