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
import json
import os
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


if __name__ == "__main__":
    sys.exit(main())
