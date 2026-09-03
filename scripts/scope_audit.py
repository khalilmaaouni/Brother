"""scope_audit: what a run actually changed, against what it said it would.

The founder's answer, chosen 2026-08-29 over declaring every hidden surface in
advance: let work run, then compare what it ACTUALLY changed against what it
DECLARED, and quarantine a result that wrote outside its declaration rather than
merging it.

WHY THAT WAY ROUND. A scheduler decides what may run beside what by comparing
declared write sets. Two units can declare disjoint paths and still collide
through a generated file, a shared contract, or an exclusive resource neither of
them names. Declaring those in advance only protects against collisions somebody
already thought of, and the ones that hurt are the ones nobody did. Comparing
afterwards catches every case, including the ones that were impossible to
predict, at the cost of catching them late.

Late is affordable HERE and would not be everywhere: this estate commits at every
checkpoint, so an undeclared write is one revert away. A system that could not
revert cheaply should declare in advance instead. The trade is stated because it
is the whole reason this design is allowed to be the cheap one.

QUARANTINE IS NOT REJECTION. A quarantined result is held, named, and left for a
person or a later pass to accept or discard. Deleting it would destroy work that
is probably fine and merely undeclared, and merging it would defeat the audit.
Holding it is the only honest third option, and it matches this estate's rule
that work is never lost because a rule was broken.

WHAT IT DELIBERATELY DOES NOT DO: judge whether a declaration was WISE. A unit
that declares the whole repository passes this audit and is a different problem,
caught by the decomposition standard rather than here. One check, one question.

Python 3, standard library only. No network.
"""
import argparse
import json
import os
import subprocess
import sys

CLEAN = "CLEAN"
QUARANTINE = "QUARANTINE"
NO_DATA = "NO-DATA"

EXIT_CLEAN = 0
EXIT_QUARANTINE = 1
EXIT_NO_DATA = 2


def changed_paths(before, after=None, cwd=None, runner=None):
    """Every path that really changed between two refs, or (None, problem).

    Reads git rather than trusting a worker's own account of itself: a worker
    reporting its own artifacts is the thing being audited, so believing it
    would make the audit circular."""
    runner = runner or (lambda cmd, **kw: subprocess.run(
        cmd, capture_output=True, text=True, cwd=cwd, timeout=60, **kw))
    rng = "%s..%s" % (before, after) if after else before
    try:
        proc = runner(["git", "diff", "--name-only", rng])
    except Exception as exc:  # noqa: BLE001
        # sbe: allow-silent the failure becomes the NO-DATA reason below
        return None, "could not read the diff for %s: %s" % (rng, exc)
    if proc.returncode != 0:
        return None, ("git could not resolve %s: %s"
                      % (rng, (proc.stderr or "").strip()[:200]))
    return [p for p in (proc.stdout or "").splitlines()
            if p.strip() and not _generated_noise(p)], ""


def _generated_noise(path):
    """Interpreter bytecode is never a deliverable and never a leak: a worker
    that RUNS the tests it wrote produces __pycache__ as a side effect of the
    run, not as a write. Counting it quarantined a correct unit on the first
    live product-path run (2026-08-30, unit tests, tests/__pycache__), which
    is the false-refusal class the laws name: a refusal on machine noise
    teaches humans to bypass the gate. Only bytecode is excluded; every other
    generated file stays auditable because it CAN carry content."""
    parts = path.split("/")
    return ("__pycache__" in parts
            or path.endswith(".pyc")
            or path.endswith(".pyo"))


def covered(path, declared):
    """Does the declaration cover this path? Prefix for directories, exact for
    files, matching the reading every other control here already uses.

    An EMPTY declaration covers nothing, which is the point: a unit that
    declared itself read-only and then wrote is exactly the case worth
    catching, and it is NOT the same as a unit that declared nothing at all."""
    for owned in declared or []:
        owned = str(owned).strip().rstrip("/")
        if not owned:
            continue
        # THE REPOSITORY ROOT. "." and "./" mean the whole tree, and the prefix
        # test below would reject them, because "sneaky.py" does not start with
        # "./". Found by driving the case rather than by reading the function:
        # a unit declaring the root was quarantined for writing inside it.
        if owned in (".", ""):
            return True
        if owned.startswith("./"):
            owned = owned[2:]
        if path == owned or path.startswith(owned + "/"):
            return True
    return False


def audit(unit, before, after=None, cwd=None, runner=None):
    """(verdict, detail). The unit needs a write_scope and an id."""
    declared = unit.get("write_scope")
    if declared is None:
        return NO_DATA, {"reason": (
            "the unit declares no write_scope at all, so there is nothing to "
            "compare against. Absent is not read-only, and this is not a pass: "
            "an undeclared unit should never have been dispatched"),
            "undeclared": [], "changed": []}

    paths, problem = changed_paths(before, after, cwd, runner)
    if paths is None:
        return NO_DATA, {"reason": problem, "undeclared": [], "changed": []}

    undeclared = sorted(p for p in paths if not covered(p, declared))
    if undeclared:
        return QUARANTINE, {
            "reason": ("%d path(s) changed that %s never declared: %s. Held, not "
                       "discarded: the work is probably fine and merely "
                       "undeclared, but merging it would defeat the audit that "
                       "the scheduler's own safety rests on"
                       % (len(undeclared), unit.get("unit_id", "this unit"),
                          ", ".join(undeclared[:8]))),
            "undeclared": undeclared, "changed": sorted(paths)}
    return CLEAN, {"reason": ("%d path(s) changed, every one inside the "
                              "declaration" % len(paths)),
                   "undeclared": [], "changed": sorted(paths)}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--unit", help="JSON with unit_id and write_scope, or a path to it")
    ap.add_argument("--before", required=True, help="the ref the unit started from")
    ap.add_argument("--after", help="the ref it ended at (default: working tree)")
    ap.add_argument("--cwd")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))

    raw = args.unit or ""
    if raw and os.path.isfile(raw):
        with open(raw, encoding="utf-8") as fh:
            raw = fh.read()
    try:
        unit = json.loads(raw) if raw else {}
    except ValueError as exc:
        print("NO-DATA: could not read the unit: %s" % exc, file=sys.stderr)
        return EXIT_NO_DATA

    verdict, detail = audit(unit, args.before, args.after, args.cwd)
    if args.json:
        print(json.dumps({"verdict": verdict, **detail}, indent=2, sort_keys=True))
    else:
        print("%s: %s" % (verdict, detail["reason"]))
    if verdict == QUARANTINE:
        return EXIT_QUARANTINE
    if verdict == NO_DATA:
        return EXIT_NO_DATA
    return EXIT_CLEAN


if __name__ == "__main__":
    sys.exit(main())
