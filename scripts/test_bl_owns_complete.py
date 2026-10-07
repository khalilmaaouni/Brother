#!/usr/bin/env python3
"""Every component of brother.loop is named by unit BL, and every one is committed.

Owner order 2026-09-21: "make sure all the tools and all the components of the Brother.loop are
committed within its WBS".

Two failure directions, and both are real on an estate where the loop writes its own tooling:

  UNOWNED     a file lives in scripts/loop and unit BL does not name it. The board then understates
              what the unit is, so a reviewer reading the WBS cannot see the whole component, and a
              done check scoped to the unit's owns silently skips it.

  UNCOMMITTED a path BL names is not tracked by git. That is the defect that mattered most this
              night: six loop tools, including the gate that scans every commit for secrets, long
              dashes, attribution trailers and private terms, existed on exactly one laptop and in
              no repository. A component the board claims and git does not hold is not a component,
              it is a local file that dies with the disk.

This check makes the ownership claim mechanical. A claim nothing verifies is a sentence.

Run: python3 scripts/test_bl_owns_complete.py
"""
import json, glob, os, subprocess, sys

PLAN = "docs/plan/BROTHER-1.1.0-LAUNCH-WBS.json"
UNIT = "BL"


def components():
    """What brother.loop is made of, discovered from the tree rather than from a list somebody
    remembered to update. __pycache__ and anything that is not a tool or a test is excluded."""
    loop = [f for f in glob.glob("scripts/loop/*") if os.path.isfile(f) and f.endswith((".py", ".sh"))]
    tests = glob.glob("scripts/test_loop*.py") + glob.glob("scripts/test_unit_runner*.py")
    return sorted(set(loop + tests))


# ONE GIT CALL, NOT ONE PER COMPONENT. Measured 2026-09-22: this check cost 28.9 seconds, which is
# 0.36 s for each of the 81 components the unit names. That is process spawn overhead, not audit
# work: `git ls-files --error-unmatch <path>` was run once per path in a loop. The whole tracked set
# comes back from a SINGLE call, so the audit becomes a set lookup and the 28.9 s becomes one git
# process. The verdict is unchanged, because "is this path tracked" is the same question asked of
# the same git index either way.
#
# FAIL DIRECTION: if the one call fails for any reason (not a repository, git missing, an unreadable
# index) the set is EMPTY and every component reads as untracked, so the check goes red and refuses.
# It does not read an unreadable index as "everything is fine", which is the direction that would
# turn this speedup into a hole.
_TRACKED = None


def tracked(path):
    global _TRACKED
    if _TRACKED is None:
        r = subprocess.run(["git", "ls-files", "-z"], capture_output=True, text=True)
        _TRACKED = set(r.stdout.split("\0")) - {""} if r.returncode == 0 else set()
    return path in _TRACKED


def main():
    if not os.path.isfile(PLAN):
        # THE ESTATE'S OWN CONVENTION, and a first attempt at something cleverer got it wrong: a check that
        # must read a live repository document SKIPS when that document is absent. The export tree keeps the
        # docs/plan FOLDER while excluding the plan itself, so "does the folder exist" cannot tell an export
        # copy from a gap, which is what that first attempt assumed and why the gate refused it twice.
        #
        # Skipping does not weaken anything. In the real checkout the file IS present and this check runs for
        # real, which is where its verdict matters; in a tree without the plan there is simply nothing to
        # judge, and a check that cannot look must not pretend to a verdict in either direction.
        print("SKIPPED: %s is not in this tree, so there is nothing to judge here. This check is meaningful "
              "only in a checkout that carries the plan." % PLAN)
        return 0
    try:
        plan = json.load(open(PLAN, encoding="utf-8"))
    except ValueError as exc:
        print("NO-DATA: %s is not readable JSON (%s). That is not a pass." % (PLAN, exc))
        return 2
    unit = next((u for u in plan.get("units", []) if u.get("id") == UNIT), None)
    if unit is None:
        print("FAIL: the plan has no unit %s, so brother.loop is not on the board at all" % UNIT)
        return 1

    owns = set(unit.get("owns") or [])
    found = components()
    unowned = [f for f in found if f not in owns]
    uncommitted = [f for f in sorted(owns) if not tracked(f)]
    missing = [f for f in sorted(owns) if not os.path.isfile(f)]

    for f in unowned:
        print("FAIL unowned      %-46s exists in the tree, unit %s does not name it" % (f, UNIT))
    for f in missing:
        print("FAIL absent       %-46s unit %s names it and it does not exist" % (f, UNIT))
    for f in uncommitted:
        print("FAIL uncommitted  %-46s named by %s but not tracked by git" % (f, UNIT))

    if unowned or uncommitted or missing:
        print("\n%d unowned, %d absent, %d uncommitted. The board's claim about brother.loop is not true."
              % (len(unowned), len(missing), len(uncommitted)))
        return 1
    print("PASS: unit %s names %d component(s); every one exists and every one is committed"
          % (UNIT, len(owns)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
