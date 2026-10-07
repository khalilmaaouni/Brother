#!/usr/bin/env python3
"""ORCH-37: before a row is called new work, ask whether it already exists.

WHY, from this run's own record: the orchestrator specified fifteen rows in
one pass, and one of them (ORCH-19) named scripts/mutation_gate.py as a file
to write. That module already existed, committed under an earlier release
line, and was already registered in the battery as mutation-gate-self and
mutation-gate. A dispatched builder caught it and refused to overwrite
load-bearing code; nothing mechanical did. The same class has cost this
estate before in a different shape: fixes landing on a branch where they were
already fixed upstream, correct and tested and worth nothing.

WHAT IT DECIDES, per row:
  NEW       none of the row's owns paths exist, and no check for them is
            registered: the row may be dispatched as new work.
  MODIFY    at least one owns path exists, or a check naming it is already
            registered. The row is not new work, and the refusal NAMES the
            existing file and the registered check line so the row can be
            rewritten as a modification against the check that already
            decides it.
  NO-DATA   the plan, the tree or the battery file could not be read.
            Refuses, because "I could not look" is not "it does not exist".

WHAT IT DOES NOT DO: judge whether the existing module is any good, or
rewrite the row. It answers one question, which is the one nobody asked.

Usage:
  python3 scripts/spec_precheck.py --plan docs/plan/ORCH-1020-WBS.json \
      [--tree .] [--battery scripts/check_all.sh] [--row ORCH-19]
Exit 0 every row checked is NEW, 1 at least one is MODIFY, 2 NO-DATA.
"""
import argparse
import glob
import json
import os
import re
import sys


class Unreadable(Exception):
    """A source could not be read, so no conclusion may be drawn."""


def _read(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError as exc:
        raise Unreadable("%s could not be read: %s" % (path, exc))


def registered_checks(battery_text):
    """{basename: the whole run_check line} for every check the battery
    registers. Read from the battery's own text rather than a second list,
    because two lists of one fact drift silently."""
    found = {}
    for line in battery_text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("run_check"):
            continue
        for token in stripped.split():
            if token.endswith(".py") or token.endswith(".sh"):
                found.setdefault(os.path.basename(token), stripped)
    return found


def check_row(row, tree, checks):
    """(verdict, [reasons]) for one plan row."""
    reasons = []
    for owned in row.get("owns", []):
        if owned.startswith("~") or os.path.isabs(owned):
            path = os.path.expanduser(owned)
        else:
            path = os.path.join(tree, owned)
        if os.path.exists(path):
            reasons.append("owns path already exists: %s" % owned)
        base = os.path.basename(owned.rstrip("/"))
        if base in checks:
            reasons.append("the battery already registers a check for %s: %s"
                           % (base, checks[base]))
    return ("MODIFY" if reasons else "NEW"), reasons


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True)
    ap.add_argument("--tree", default=".")
    ap.add_argument("--battery", default="scripts/check_all.sh")
    ap.add_argument("--row", action="append", default=None,
                    help="only these row ids (repeatable); default is every row with owns")
    a = ap.parse_args(argv)
    try:
        plan = json.loads(_read(a.plan))
        checks = registered_checks(_read(os.path.join(a.tree, a.battery))
                                   if not os.path.isabs(a.battery) else _read(a.battery))
        units = plan["units"]
    except (Unreadable, ValueError, KeyError, TypeError) as exc:
        print("spec-precheck: NO-DATA, %s. Refusing to call anything new work." % exc)
        return 2
    wanted = set(a.row) if a.row else None
    modify, checked = [], 0
    for row in units:
        if wanted is not None and row.get("id") not in wanted:
            continue
        if not row.get("owns"):
            continue
        checked += 1
        verdict, reasons = check_row(row, a.tree, checks)
        if verdict == "MODIFY":
            modify.append((row.get("id"), reasons))
    if not checked:
        print("spec-precheck: NO-DATA, no row with owns paths was checked")
        return 2
    for row_id, reasons in modify:
        print("MODIFY  %s" % row_id)
        for reason in reasons:
            print("        %s" % reason)
    print("spec-precheck: %d row(s) checked, %d already exist and are NOT new work"
          % (checked, len(modify)))
    return 1 if modify else 0


# ---------------------------------------------------------------------------
# A SPECIFICATION THAT ORDERS WHAT A GATE REFUSES IS UNWINNABLE, added 2026-09-21.
#
# Measured that day on L5f-c: its spec said "Stdlib `urllib` plus `json` plus `hashlib` only", while the build
# safety screen in scripts/loop/grade_build.py refuses `urllib` outright. Every worker that obeyed the sentence
# was failed on the screen BEFORE A SINGLE TEST RAN. All six grades across three exhausted rounds are safety
# screen refusals and not one is a test failure. Three rounds of model spend bought nothing, and the loop
# reported it as "needs one fact about the real tree" when the real fact was that the lane could not be won.
#
# This is a different class from the NEW-versus-exists check above. There the spec is wrong about the TREE; here
# the spec is wrong about our own GATES, and no amount of worker skill can satisfy both.
#
# The refused list is IMPORTED from the screen rather than copied, so the two can never drift apart. A copy
# would be a second source of truth and would go stale the first time the screen changed.
def refused_modules():
    """The modules the build safety screen refuses, read from the screen itself. Returns None when it cannot be
    read, which the caller reports as NO-DATA: a check that silently finds nothing is worse than one that says
    it could not look."""
    import importlib.util
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, "loop", "grade_build.py")
    try:
        spec = importlib.util.spec_from_file_location("_gb_precheck", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return tuple(getattr(mod, "NET_MODULES")) + ("urllib",)
    except (OSError, AttributeError, ImportError, SyntaxError, ValueError):
        return None


def gate_conflicts(spec_text, refused):
    """[(module, quoted line)] for every refused module this spec text tells a worker to USE.

    THE PREDICATE IS THE WHOLE DIFFICULTY, and a first version of it cried wolf on 17 lines of which most were
    English rather than instructions: "concurrent requests", "without a socket ever opening", "import of
    `urllib.parse` does not count as a network import". A check whose findings are mostly false is worse than no
    check at all, because it teaches its reader to skip it.

    So a line counts only when it is an IMPORT or a direct USE: an import statement, a dotted call into the
    module, or a phrase that hands the module to the worker as the thing to build with. A line that merely names
    the module while DENYING it, describing a mutation, or explaining that something does not count, is not an
    instruction and is skipped."""
    if not refused or not isinstance(spec_text, str):
        return []
    deny = re.compile(r"refus|screen|must not|never|no network|denied|deny|instead of|imports no|does not count|"
                      r"trap|amended|without a|fails inside|flagged|remove |delete|forbidden|mutation", re.I)   # delete/forbidden: 2026-09-22, two L5f and L3b lines describing a mutation and a refusal test read as orders
    out = []
    for line in spec_text.splitlines():
        if deny.search(line):
            continue
        for m in refused:
            q = re.escape(m)
            use = (r"^\s*(from|import)\s+%s\b" % q,          # import statement
                   r"\bimport\s+%s\b" % q,                    # inline import
                   r"\b%s\.[a-z_]+\s*\(" % q,                # a dotted call into it
                   r"[Ss]tdlib\s+`?%s`?" % q,                   # "Stdlib urllib"
                   r"\buse\s+`?%s`?" % q)                      # "use requests"
            if any(re.search(u, line) for u in use):
                out.append((m, line.strip()[:110]))
                break
    return out


def units_with_command_runners(plan_path="docs/plan/BROTHER-1.1.0-LAUNCH-WBS.json"):
    """Unit ids whose plan record names command_runners (grade_build.allowed_runners reads the same field), plus DONE units.
    Unreadable plan: the empty set, so every subprocess conflict is still reported."""
    try:
        with open(plan_path, encoding="utf-8") as fh:
            units = json.load(fh).get("units") or []
        return {u["id"] for u in units if isinstance(u, dict) and u.get("id")
                and (u.get("command_runners") or u.get("state") == "DONE")}   # a DONE unit's spec orders nothing to anyone any more
    except (OSError, ValueError, AttributeError, TypeError):
        return set()


def malformed_runners(plan_path="docs/plan/BROTHER-1.1.0-LAUNCH-WBS.json"):
    """[(unit id, entry)] for every command_runners entry that is not a .py or .sh path. grade_build.allowed_runners
    admits subprocess for listed PATHS only, so a module name ("subprocess", "os": all six H units on 2026-09-24)
    allows nothing while the brief tells the worker it may run commands. Unreadable plan: [] (the scan below still
    reports every conflict, since no unit is then exempt)."""
    try:
        with open(plan_path, encoding="utf-8") as fh:
            units = json.load(fh).get("units") or []
    except (OSError, ValueError, AttributeError, TypeError):
        return []
    return [(u.get("id"), e) for u in units if isinstance(u, dict) for e in (u.get("command_runners") or [])
            if not (isinstance(e, str) and e.strip() == e and e.endswith((".py", ".sh")) and " " not in e)]


def main_gate_scan(specs_dir="docs/plan/specs", plan_path="docs/plan/BROTHER-1.1.0-LAUNCH-WBS.json"):
    refused = refused_modules()
    if refused is None:
        print("NO-DATA: the build safety screen could not be read, so no spec was checked against it")
        return 2
    bad = 0
    for unit, entry in malformed_runners(plan_path):
        print("MALFORMED %-8s command_runners names %r, which is not a .py or .sh path: the build screen allows nothing for it" % (unit, entry))
        bad += 1
    runners_units = units_with_command_runners(plan_path)
    for path in sorted(glob.glob(os.path.join(specs_dir, "*.md"))):
        try:
            text = open(path, encoding="utf-8").read()
        except OSError as exc:
            print("UNREADABLE %-26s could not be read (%s), so it was not checked" % (os.path.basename(path), type(exc).__name__))
            bad += 1
            continue
        hits = gate_conflicts(text, refused)
        if os.path.basename(path)[:-3] in runners_units:
            # owner ruling 2026-09-22: a unit that names command runners in the plan may order subprocess for them
            hits = [(m, l) for m, l in hits if m != "subprocess"]
        for mod, line in hits:
            print("CONFLICT %-28s orders `%s`, which the build screen refuses: %s"
                  % (os.path.basename(path), mod, line))
            bad += 1
    print("%d spec(s) scanned against %d refused module(s); %d conflict(s)"
          % (len(glob.glob(os.path.join(specs_dir, "*.md"))), len(refused), bad))
    return 1 if bad else 0


if __name__ == "__main__" and "--gate-scan" in sys.argv:
    # intercepts BEFORE the argparse main below, which requires --plan and would refuse this call
    sys.exit(main_gate_scan())
if __name__ == "__main__":
    sys.exit(main())
