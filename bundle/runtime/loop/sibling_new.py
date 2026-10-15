#!/usr/bin/env python3
"""sibling_new.py (CT1.d, L07): name a NEW path a later sibling sub unit labels that an earlier sibling already creates.

Reads a unit spec, walks its sub units in the order given, and answers one (later_sub, path, earlier_sub)
per later sibling whose section labels a path NEW that an earlier sibling's section already labelled NEW.
Sections and labels come from brief_check beside this file (the one reader of a section and of a path and
its NEW label); this module keeps no second copy of either rule.

usage: sibling_new.py <plan.json>    exit 0 clean, 1 a conflict was named, 2 NO-DATA (a plan or a spec that cannot be read)
"""
import json
import os
import sys
from typing import List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import brief_check as BC  # noqa: E402  the one reader of a section and of a path and its NEW label


def sibling_new_conflicts(spec_text: str, subs: List[str]) -> List[Tuple[str, str, str]]:
    """One (later_sub, path, earlier_sub) for every later sub unit in `subs` whose brief checker section
    labels a path NEW that an earlier sub unit in the same list already labelled NEW. A path labelled
    existing is never a conflict; the same path NEW twice inside one section is not one either. A sub
    with no section is skipped. The answer follows `subs` order. A non string spec_text, a non list subs,
    or a non string element raises ValueError; nothing else is accepted."""
    if not isinstance(spec_text, str):
        raise ValueError("spec_text must be a string, not %s" % type(spec_text).__name__)
    if not isinstance(subs, list):
        raise ValueError("subs must be a list, not %s" % type(subs).__name__)
    for i, sub in enumerate(subs):
        if not isinstance(sub, str):
            raise ValueError("subs[%d] must be a string, not %s" % (i, type(sub).__name__))
    first_new = {}
    out = []
    for sub in subs:
        section = BC.section_of(spec_text, sub)
        if section is None:
            continue
        seen_here = set()
        for path, label, _start, _end in BC.labelled_paths(section):
            if label != "NEW":
                continue
            if path in seen_here:
                continue
            seen_here.add(path)
            earlier = first_new.get(path)
            if earlier is None:
                first_new[path] = sub
            else:
                out.append((sub, path, earlier))
    return out


USAGE = "usage: sibling_new.py <plan.json>"


def main(argv: Optional[List[str]] = None) -> int:
    """Read a plan JSON path; for every unit that carries a `spec` and a `sub_units` list, read the spec
    file relative to the current directory and print one line per NEW sibling conflict. Exit 1 when any
    line was printed, 0 when none, 2 when the plan or a named spec cannot be read (NO-DATA is never a
    pass). No argument: usage and exit 2. Any argv that is not a list of str refuses with exit 2."""
    if argv is None:
        argv = sys.argv[1:]
    if not isinstance(argv, list):
        print(USAGE, file=sys.stderr)
        return 2
    for a in argv:
        if not isinstance(a, str):
            print(USAGE, file=sys.stderr)
            return 2
    if len(argv) != 1:
        print(USAGE, file=sys.stderr)
        return 2
    plan_path = argv[0]
    try:
        with open(plan_path, encoding="utf-8") as fh:
            plan = json.load(fh)
    except (OSError, ValueError) as exc:
        print("NEW-SIBLING NO-DATA: cannot read plan %s: %s" % (plan_path, exc))
        return 2
    if not isinstance(plan, dict):
        print("NEW-SIBLING NO-DATA: plan %s is not a JSON object" % plan_path)
        return 2
    units = plan.get("units")
    if not isinstance(units, list):
        units = []
    printed = 0
    for unit in units:
        if not isinstance(unit, dict):
            continue
        spec_ref = unit.get("spec")
        unit_subs = unit.get("sub_units")
        if not isinstance(spec_ref, str) or not spec_ref:
            continue
        if not isinstance(unit_subs, list):
            continue
        try:
            with open(spec_ref, encoding="utf-8") as fh:
                spec_text = fh.read()
        except OSError as exc:
            print("NEW-SIBLING NO-DATA: cannot read spec %s: %s" % (spec_ref, exc))
            return 2
        unit_name = unit.get("id")
        if not isinstance(unit_name, str) or not unit_name:
            unit_name = "?"
        for later, path, earlier in sibling_new_conflicts(spec_text, unit_subs):
            print("NEW-SIBLING %s: %s labels %s NEW, already created by %s"
                  % (unit_name, later, path, earlier))
            printed += 1
    return 1 if printed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
