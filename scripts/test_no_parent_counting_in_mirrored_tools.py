#!/usr/bin/env python3
"""A MIRRORED tool must not compute a path by counting parents of __file__.

This bit the estate twice in one evening, in two different files, and the second time it cost a
full debugging round because the symptom pointed somewhere else.

A loop tool exists twice: a reviewed copy in scripts/loop and an executed copy in ~/.claude/bin.
The expression

    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

resolves to the repository root from the first and to the HOME directory from the second. Both
failures came from that one line:

  model_router.py looked for its registry at ~/docs/plan/model-registry.json and refused to route.
  model_call.py handed Codex a HOME directory as its working root, and Codex refused with
  "Not inside a trusted directory". That message arrives AFTER a line about stdin, so it read as a
  stdin bug until the path was printed.

The correct resolver asks git for the worktree, with an environment variable able to override it,
and lives in ONE place: model_router.repo_root(). This check refuses a second copy of the pattern
in any file that is mirrored, because unmirrored files genuinely know where they are.

Run: python3 scripts/test_no_parent_counting_in_mirrored_tools.py
"""
import os, re, sys

LOOP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "loop")
BIN = os.path.expanduser("~/.claude/bin")
# Two or more nested dirname() calls wrapping __file__, ON ONE LINE and bounded.
# The first version used re.S with a greedy .*, so it matched from a dirname() operating on a DATA
# path all the way to an unrelated __file__ hundreds of lines later, and reported a clean file as
# an offender. A check that cries wolf gets switched off, so the bound is part of the check.
PATTERN = re.compile(r"dirname\s*\(\s*os\.path\.dirname\s*\([^\n]{0,200}?__file__")
# THE RULE, not a list of names. Counting parents is allowed only as the LAST RESORT inside a
# resolver that asks git FIRST, and a file proves it does that by containing the git call. Naming
# exempt files instead would rot: the next file to need a root would be added to the list by
# whoever hit the gate, which is how an exception list becomes the norm.
ASKS_GIT = "rev-parse"
SHOW_TOPLEVEL = "--show-toplevel"


def offenders():
    out = []
    if not os.path.isdir(LOOP):
        return None
    for name in sorted(os.listdir(LOOP)):
        if not name.endswith(".py"):
            continue
        path = os.path.join(LOOP, name)
        if not os.path.isfile(os.path.join(BIN, name)):
            continue                      # not mirrored: it knows where it is
        try:
            body = open(path, encoding="utf-8", errors="replace").read()
        except OSError:
            out.append((name, "unreadable"))
            continue
        if ASKS_GIT in body and SHOW_TOPLEVEL in body:
            continue                      # asks git first; the count is its documented last resort
        for m in PATTERN.finditer(body):
            line = body[:m.start()].count("\n") + 1
            out.append((name, "line %d counts parents of __file__ and never asks git" % line))
    return out


def main():
    bad = offenders()
    if bad is None:
        print("NOT APPLICABLE: %s does not exist in this tree, so there are no mirrored tools "
              "to check" % LOOP)
        return 0
    for name, why in bad:
        print("FAIL %-26s %s" % (name, why))
    if bad:
        print("\n%d mirrored tool(s) compute a path by counting parents of __file__.\n"
              "Use model_router.repo_root(), which asks git and works from either copy." % len(bad))
        return 1
    print("PASS: every mirrored loop tool that resolves a root asks git first; "
          "counting parents survives only as a documented last resort")
    return 0


if __name__ == "__main__":
    sys.exit(main())
