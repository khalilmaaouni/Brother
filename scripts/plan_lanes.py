#!/usr/bin/env python3
"""plan_lanes: is a set of owned files LOOP work or HAND work? The 1.1.8 council's split rule (DECISIONS-118 C1), as
the 1.1.9 access decision applies it (AC1): LOOP only when no owned file is a judge kernel closure member
(scripts/kernel_closure.txt), a loop tool (scripts/loop/), a path the grader refuses or a generated one
(plan_lint.protected_reason, read live), or a cut file; the set is not test or doc only; it is not empty; and the plan
of record does not put the unit in a kernel, session, field or owner lane. Anything else is HAND, every reason named.
File size is a note, never a reason: plan_lint's BRIEF-BUDGET judges each sub unit's real brief at admission.

    python3 scripts/plan_lanes.py --selftest     one fixture per guard; deleting any guard turns its own fixture red
    python3 scripts/plan_lanes.py PATH...        print LOOP or HAND and the reasons for one file set

Fail direction: an unreadable kernel closure list makes every file HAND. Standard library only, ASCII only."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, "loop"))
import plan_lint  # noqa: E402

BIG = 100_000
CUT = ("scripts/cut.py", "scripts/cut_preflight.py", "scripts/cut_rehearsal.py", "scripts/required_fast.sh",
       "scripts/export_public.py", "scripts/cut_ready.py", "scripts/cut_driver.py")
PLAN_HAND_LANES = ("kernel", "session", "field", "owner")


def kernel_set(root=ROOT):
    """The kernel closure members, or None when the list cannot be read (then every file reads HAND)."""
    try:
        with open(os.path.join(root, "scripts", "kernel_closure.txt"), encoding="utf-8") as fh:
            return {l.strip() for l in fh if l.strip() and not l.startswith("#")}
    except OSError:
        return None


def lane(owns, flags=(), root=ROOT):
    """(LOOP or HAND, [reasons], [notes]) for one file set."""
    why, notes = [], []
    if not owns:
        why.append("no owns: nothing to judge, and plan_lint blocks a unit with no owns")
    kernel = kernel_set(root)
    if kernel is None:
        why.append("NO-DATA: scripts/kernel_closure.txt cannot be read, so nothing can be shown loop safe")
        kernel = set()
    for p in owns:
        if p in kernel:
            why.append("kernel file %s" % p)
        if p.startswith("scripts/loop/"):
            why.append("loop tool %s" % p)
        if p in CUT:
            why.append("cut file %s" % p)
        r = plan_lint.protected_reason(p)
        if r:
            why.append("%s: %s" % (p, r.replace("a generated file", "generated file")))
        full = os.path.join(root, p)
        if os.path.isfile(full) and os.path.getsize(full) > BIG:
            notes.append("brief risk: %s is %d bytes" % (p, os.path.getsize(full)))
    if owns and all(os.path.basename(p).startswith("test_") or p.startswith(("docs/", "tests/")) or p.endswith(".md")
                    for p in owns):
        why.append("test or doc only shape (the grader needs tests that fail without the code)")
    why += list(flags)
    return ("HAND" if why else "LOOP"), why, notes


def plan_flags(u, src):
    """The plan of record's own lane wins over the files."""
    return ["the %s plan puts it in its %s lane" % (src, u["lane"])] if u.get("lane") in PLAN_HAND_LANES else []


FIXTURES = [  # one guard each
    (["products/brothermode/tools/bm_clock_guard.py"], (), "LOOP", None),
    (["scripts/brother_run.py"], (), "HAND", "kernel file"),
    (["scripts/loop/peak_bench.py"], (), "HAND", "loop tool"),
    (["scripts/cut_ready.py"], (), "HAND", "cut file"),
    (["bundle/runtime/brother_run.py"], (), "HAND", "generated file"),
    (["products/brothermode/tools/test_bm_clock_guard.py"], (), "HAND", "test or doc only"),
    ([], (), "HAND", "no owns"),
    (["products/brothermode/tools/bm_clock_guard.py"], plan_flags({"lane": "kernel"}, "x"), "HAND", "kernel lane"),
    (["products/brothermode/tools/bm_clock_guard.py"], plan_flags({"lane": "loop"}, "x"), "LOOP", None),
]


def selftest():
    bad = []
    for owns, flags, want, cls in FIXTURES:
        got, why, _n = lane(owns, flags)
        if got != want or (cls and (len(why) != 1 or cls not in why[0])):
            bad.append((owns, flags, want, got, why))
    import tempfile
    with tempfile.TemporaryDirectory() as empty:   # no kernel_closure.txt: every file reads HAND, the fail direction
        got, why, _n = lane(["products/brothermode/tools/bm_clock_guard.py"], (), root=empty)
        if got != "HAND" or len(why) != 1 or "NO-DATA" not in why[0]:
            bad.append(("unreadable closure list", got, why))
    got, why, notes = lane(["scripts/brother_run.py"])
    if not any("brief risk" in n for n in notes) or any("brief" in w for w in why):
        bad.append(("size is a note, never a reason", why, notes))
    print("selftest: %d fixtures, %s" % (len(FIXTURES) + 2, "OK" if not bad else "FAIL %s" % bad))
    return 0 if not bad else 1


def main(argv):
    if argv == ["--selftest"]:
        return selftest()
    if not argv or argv[0].startswith("-"):
        print(__doc__)
        return 2
    got, why, notes = lane(argv)
    print(got)
    for w in why + notes:
        print("  " + w)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
