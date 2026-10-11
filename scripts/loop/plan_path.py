#!/usr/bin/env python3
"""The one plan selector: which plan file the maintainer loop reads and writes.

Until 2026-10-10 the path docs/plan/BROTHER-1.1.0-LAUNCH-WBS.json was spelled in about 45 loop tools, so the loop
could only ever run one plan. Every reader, writer, scorer, closer and finisher now asks this module instead, and
the answer comes from ONE environment variable, BROTHER_WBS (the name build_brief.py already read), with the
historical plan as the default when it is unset or blank. The default file is never renamed or overwritten by this.

FAIL DIRECTION: an invalid value is REFUSED with its reason (ValueError), never quietly replaced by the default. A
run pointed at a wrong plan would score, land and close units against the wrong records, so a path that is
absolute, climbs with `..`, lives outside docs/plan/, or is not a .json file blocks the tool that asked.

    python3 -B scripts/loop/plan_path.py        prints the selected path (exit 0), or the reason on stderr (exit 2)

Python 3.9, standard library only. Tests: scripts/loop/test_loop_plan_selection.py.
"""
import os
import sys

ENV = "BROTHER_WBS"
DEFAULT = "docs/plan/BROTHER-1.1.0-LAUNCH-WBS.json"
PLAN_DIR = "docs/plan/"


def validate(path):
    """`path` back, stripped, when it names a plan this loop may read; ValueError naming the reason otherwise."""
    if not isinstance(path, str):
        raise ValueError("%s must be text, not %s" % (ENV, type(path).__name__))
    p = path.strip()
    if not p:
        raise ValueError("%s is blank" % ENV)
    if os.path.isabs(p) or p.startswith(("/", "\\")):
        raise ValueError("%s=%r is an absolute path: a plan path is relative to the launch tree" % (ENV, p))
    parts = p.replace("\\", "/").split("/")
    if ".." in parts:
        raise ValueError("%s=%r has a .. segment: a plan path never climbs out of the tree" % (ENV, p))
    if not p.startswith(PLAN_DIR) or len(p) <= len(PLAN_DIR):
        raise ValueError("%s=%r is not under %s" % (ENV, p, PLAN_DIR))
    if not p.endswith(".json") or os.path.basename(p) == ".json":
        raise ValueError("%s=%r does not name a .json file" % (ENV, p))
    if os.path.normpath(p) != p:
        raise ValueError("%s=%r is not a plain relative path (empty or . segments)" % (ENV, p))
    return p


def selected(env=None):
    """The plan path for this process: BROTHER_WBS when set and not blank (validated), else DEFAULT."""
    env = os.environ if env is None else env
    raw = env.get(ENV)
    if raw is None or not str(raw).strip():
        return DEFAULT
    return validate(raw)


def main(argv=None):
    try:
        print(selected())
    except ValueError as exc:
        print("PLAN REFUSED: %s" % exc, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
