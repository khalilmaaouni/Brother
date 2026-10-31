#!/usr/bin/env python3
"""Refuse a plan that contains a unit whose gate CANNOT POSSIBLY PASS.

WHY THIS EXISTS, and it is the whole of a 0/5 taken on 2026-10-29.

A unit in this estate closes when its `done_check` exits 0. Nothing checked that the
done_check was CAPABLE of exiting 0. So units sat OPEN with every piece of their work
landed and correct, and no amount of further work could close them:

  PROOF1 and DOOR1 each sat OPEN FOR TWO DAYS with all four sub units closed. Their
  done_check was a journey walk expecting a row that declared no `check`, and a
  checkless row reads "nothing measured it", which is NO-DATA, which is never a pass.
  Writing the row's check closed both units in minutes.

  ACC1.a's sub done_check named the class `FromTheDoor` in a file frozen by sha whose
  sha matched and which contained seven classes, none of them that one. The command
  died with AttributeError: red for the wrong reason, green never.

  ACC1.b's harness did not exist at all, its frozen sha recorded as the literal string
  "NO-DATA: absent until HX118 writes it".

Measured when this was written: TEN of thirty-nine units carried such a gate, four of
them in the release's own cut line. That is not slow work. It is work that cannot be
finished, and it was invisible because every symptom looked like a unit still in
progress.

THE SHAPE OF THE CONTROL: A RATCHET, NOT A CLIFF.

Refusing every violation outright would have turned the whole gate red on the day it
landed and blocked every push, which is how a control gets switched off. So the known
violations are recorded ONCE, with a reason each, in a baseline file, and from then on:

  a unit NOT in the baseline whose gate cannot pass    REFUSED
  a unit in the baseline that still cannot pass        allowed, and counted
  a baseline entry whose unit now passes               REFUSED, remove it
  a DONE unit whose gate cannot pass                   REFUSED, baseline or not

The last two are what stop the baseline becoming a dumping ground. An entry that is no
longer true must be deleted, so the file shrinks as units are gated and can never
quietly rot; and a baseline entry can never excuse a unit that claims to be DONE,
because a DONE unit's gate is supposed to have actually run.

WHAT "CANNOT PASS" MEANS, precisely. Four decidable conditions, each read off a real
failure rather than imagined:

  1. a `--expect <row,row>` journey walk naming a row that carries no `check` in
     scripts/journey/journeys.json
  2. a command naming a test file that is not on disk
  3. a command naming a class (an argument beginning with a capital) that the named file
     does not define, parsed with ast and never imported
  4. a `harness` entry that is not on disk

WHAT IT DELIBERATELY DOES NOT JUDGE. Whether the check PASSES. A harness written before
its code and red by assertion is this estate's own discipline and is entirely legal
here: the file exists, the class exists, the row has a check, and the check answers
FAIL. That is a measurement. This refuses only the gates that can never answer at all.

GENUINELY UNMEASURABLE ROWS STAY LEGAL, through the baseline and nothing else. One row
in this repository cannot be walked by a machine at all: a walker would have to spend a
consent answer that the unit under test exists to refuse. It sits in the baseline with
that reason written down, where a reader can argue with it, rather than in a silent
exemption inside the code.

    python3 -B scripts/plan_gates_can_pass.py
    python3 -B scripts/plan_gates_can_pass.py --selftest

EXIT 0 when nothing new is broken, 1 on a refusal, 2 on NO-DATA (a plan or journeys
file this cannot read, which is never a pass).
"""
import argparse
import ast
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PLAN = os.path.join(ROOT, "docs/plan/BROTHER-1.1.9-WBS.json")
JOURNEYS = os.path.join(ROOT, "scripts/journey/journeys.json")
BASELINE = os.path.join(ROOT, "docs/plan/UNGATED-UNITS.json")
_EXPECT = re.compile(r"--expect\s+([A-Za-z0-9_.,-]+)")
_CMD = re.compile(r"python3\s+(?:-B\s+)?(\S+\.py)\s*(.*)$")


def row_checks(path=JOURNEYS):
    """{row id: True when it carries a check}. None when the file cannot be read, which
    the caller turns into NO-DATA rather than guessing every row is fine."""
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return None
    out = {}
    for walk in doc.values():
        if not isinstance(walk, dict):
            continue
        for row in walk.get("rows") or []:
            if isinstance(row, dict) and row.get("id"):
                out[row["id"]] = bool(row.get("check"))
    return out


def classes_in(path):
    """The class names a file defines, or None when it is absent or unparseable. Parsed,
    never imported: a module with import side effects must not run to answer this."""
    try:
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=path)
    except (OSError, SyntaxError, ValueError):
        return None
    return {n.name for n in tree.body if isinstance(n, ast.ClassDef)}


def why_impossible(command, rows, root=ROOT):
    """Why `command` can never exit 0, or "" when nothing here says it cannot.

    Conservative on purpose: a command this cannot parse returns "", because refusing a
    shape nobody anticipated would make the control the obstacle instead of the plan."""
    command = (command or "").strip()
    if not command:
        return "no check at all"
    found = _EXPECT.search(command)
    if found:
        missing = [r for r in found.group(1).split(",") if rows.get(r) is not True]
        if missing:
            return ("expects journey row(s) that declare no check, so the row reads "
                    "NO-DATA forever and the unit can never close: %s"
                    % " ".join(missing))
        return ""
    # A done check is a chain of commands joined by &&, and each segment names its own file and classes. Judging the
    # whole string as one command read the second segment's classes as the first file's (measured 2026-10-30: AUTH1's
    # check, two files, was refused for classes the first file "does not define"). One segment at a time.
    for segment in command.split("&&"):
        why = _segment_impossible(segment.strip(), root)
        if why:
            return why
    return ""


def _segment_impossible(command, root=ROOT):
    """Why one command of a chain can never exit 0, or "" (the same conservative rule as why_impossible)."""
    parsed = _CMD.match(command)
    if not parsed:
        return ""
    rel, rest = parsed.group(1), parsed.group(2).split()
    path = os.path.join(root, rel)
    if not os.path.isfile(path):
        return "names a test file that does not exist: %s" % rel
    defined = classes_in(path)
    if defined is None:
        return "names a test file that does not parse: %s" % rel
    absent = [a for a in rest if a[:1].isupper() and a not in defined]
    if absent:
        return ("names class(es) %s that %s does not define, so the command dies with "
                "AttributeError and can never go green" % (" ".join(absent), rel))
    return ""


def unit_problems(unit, rows, root=ROOT):
    """Every reason this unit's gate cannot pass, as (where, why) pairs."""
    out = []
    why = why_impossible(unit.get("done_check"), rows, root)
    if why:
        out.append(("done_check", why))
    for sub, command in sorted((unit.get("sub_done_checks") or {}).items()):
        why = why_impossible(command, rows, root)
        if why:
            out.append((sub, why))
    for harness in (unit.get("harness") or []):
        if not os.path.isfile(os.path.join(root, harness)):
            out.append(("harness", "names a harness that does not exist: %s" % harness))
    return out


def judge(units, rows, baseline, root=ROOT):
    """(exit code, [lines]). Pure, so the selftest judges THIS and not a copy of it."""
    if rows is None:
        return 2, ["NO-DATA: the journeys file could not be read, so no gate could be "
                   "judged; that is never a pass"]
    broken, fixed = {}, []
    for unit in units:
        uid = unit.get("id")
        problems = unit_problems(unit, rows, root)
        if problems:
            broken[uid] = (unit.get("state"), problems)
        elif uid in baseline:
            fixed.append(uid)
    lines, bad = [], False
    new = sorted(u for u in broken if u not in baseline)
    done_broken = sorted(u for u, (state, _p) in broken.items() if state == "DONE")
    if new:
        bad = True
        lines.append("REFUSED: %d unit(s) carry a gate that can never pass and are not "
                     "in the baseline. A unit like this cannot be closed by any amount "
                     "of correct work:" % len(new))
        for uid in new:
            for where, why in broken[uid][1]:
                lines.append("  %-10s %-12s %s" % (uid, where, why))
    if done_broken:
        bad = True
        lines.append("REFUSED: %d unit(s) are DONE while their gate cannot pass, so "
                     "whatever closed them was not this check:" % len(done_broken))
        for uid in done_broken:
            for where, why in broken[uid][1]:
                lines.append("  %-10s %-12s %s" % (uid, where, why))
    if fixed:
        bad = True
        lines.append("REFUSED: %d baseline entr(y/ies) now pass and must be removed, so "
                     "the baseline shrinks instead of rotting: %s"
                     % (len(fixed), " ".join(sorted(fixed))))
    if bad:
        return 1, lines
    allowed = sorted(set(broken) & set(baseline))
    return 0, ["PASS: no new ungated unit, no DONE unit with an impossible gate, and no "
               "stale baseline entry. %d unit(s) remain in the baseline and every one "
               "of them is a unit that cannot be closed until its gate exists: %s"
               % (len(allowed), " ".join(allowed) or "none")]


def load_plan(path=PLAN):
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return None
    units = doc.get("units")
    return list(units.values()) if isinstance(units, dict) else (units or [])


def load_baseline(path=BASELINE):
    """{unit id: reason}. A missing file is an EMPTY baseline, which refuses everything
    known to be broken: absent evidence never widens what is allowed."""
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return {}
    entries = doc.get("ungated") if isinstance(doc, dict) else doc
    if not isinstance(entries, dict):
        return {}
    return {k: str(v) for k, v in entries.items()}


def selftest():
    cases = []

    def case(label, got, want):
        cases.append((label, want, got, got == want))

    rows = {"R.has": True, "R.bare": False}
    here = os.path.join(ROOT, "scripts")

    def unit(uid, state="OPEN", **kw):
        u = {"id": uid, "state": state}
        u.update(kw)
        return u

    ok = unit("U-ok", done_check="python3 -B scripts/journey/run_journey.py "
                                 "--fresh-install --journey J --expect R.has")
    bare = unit("U-bare", done_check="python3 -B scripts/journey/run_journey.py "
                                     "--fresh-install --journey J --expect R.bare")
    absent = unit("U-absent", done_check="python3 -B scripts/test_nope_absent.py")
    badclass = unit("U-class",
                    done_check="python3 -B scripts/plan_gates_can_pass.py NoSuchClass")
    noharness = unit("U-harness", done_check="python3 -B scripts/plan_gates_can_pass.py",
                     harness=["scripts/test_nope_absent.py"])
    # a chain is judged one segment at a time: the second file's classes belong to the second file
    chain_ok = unit("U-chain", done_check="python3 -B scripts/plan_gates_can_pass.py && "
                                          "python3 -B scripts/test_battery_registration.py BatteryRegistration")
    chain_bad = unit("U-chain-bad", done_check="python3 -B scripts/plan_gates_can_pass.py && "
                                               "python3 -B scripts/test_battery_registration.py NoSuchClass")

    case("a row with a check passes", judge([ok], rows, {})[0], 0)
    case("a checkless row is REFUSED", judge([bare], rows, {})[0], 1)
    case("an absent test file is REFUSED", judge([absent], rows, {})[0], 1)
    case("an absent class is REFUSED", judge([badclass], rows, {})[0], 1)
    case("an absent harness is REFUSED", judge([noharness], rows, {})[0], 1)
    case("a chain naming each file's own classes passes", judge([chain_ok], rows, {})[0], 0)
    case("a chain whose second file lacks the class is REFUSED", judge([chain_bad], rows, {})[0], 1)
    case("a baselined violation is allowed",
         judge([bare], rows, {"U-bare": "a reason"})[0], 0)
    case("a DONE unit is REFUSED even when baselined",
         judge([unit("U-bare", state="DONE",
                     done_check=bare["done_check"])], rows,
               {"U-bare": "a reason"})[0], 1)
    case("a baseline entry that now passes is REFUSED",
         judge([ok], rows, {"U-ok": "stale"})[0], 1)
    case("an unreadable journeys file is NO-DATA", judge([ok], None, {})[0], 2)
    case("a unit with no check at all is REFUSED",
         judge([unit("U-none", done_check="")], rows, {})[0], 1)
    # A command shape this does not understand must not be refused, or the control
    # becomes the obstacle rather than the plan.
    case("an unparsed command shape is not refused",
         judge([unit("U-shell", done_check="sh scripts/check_all.sh")], rows, {})[0], 0)
    # ONE CONDITION PER FIXTURE. An absent file trips the isfile guard AND the parse
    # guard, because classes_in returns None for a file that is not there, so the exit
    # code alone cannot tell them apart and deleting the isfile guard changed nothing
    # (M-GATES-FILE-BLIND survived the first sweep). The isfile guard's own
    # contribution is the ACCURATE REASON, so that is what this asserts: "does not
    # exist" and "does not parse" are different findings and a reader acts on them
    # differently.
    why = why_impossible("python3 -B scripts/test_nope_absent.py", rows)
    cases.append(("an absent file says it does not EXIST, not that it does not parse",
                  True, "does not exist" in why, "does not exist" in why))
    # A real unparseable file, not a literal True: a case that cannot fail counts
    # for nothing, by this estate's own law (6), and a reviewer caught this one.
    import tempfile
    broken_dir = tempfile.mkdtemp(prefix="gates-broken-")
    with open(os.path.join(broken_dir, "test_broken.py"), "w", encoding="utf-8") as fh:
        fh.write("def f(:\n")
    why2 = why_impossible("python3 -B test_broken.py", rows, root=broken_dir)
    cases.append(("a present but unparseable file says it does not PARSE", True,
                  "does not parse" in why2, "does not parse" in why2))
    # The refusal has to NAME the unit, or a reader cannot act on it.
    _code, lines = judge([bare], rows, {})
    cases.append(("the refusal names the unit", True,
                  any("U-bare" in ln for ln in lines),
                  any("U-bare" in ln for ln in lines)))
    del here

    bad = 0
    for label, want, got, good in cases:
        bad += 0 if good else 1
        print("%-52s want %s got %s %s" % (label, want, got, "ok" if good else "MISMATCH"))
    print("%d case(s), %s" % (len(cases), "OK" if not bad else "%d FAILURE(S)" % bad))
    return 0 if not bad else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--plan", default=PLAN)
    ap.add_argument("--journeys", default=JOURNEYS)
    ap.add_argument("--baseline", default=BASELINE)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    units = load_plan(args.plan)
    if units is None:
        print("NO-DATA: %s could not be read, so no gate could be judged" % args.plan)
        return 2
    code, lines = judge(units, row_checks(args.journeys), load_baseline(args.baseline))
    for line in lines:
        print(line)
    return code


if __name__ == "__main__":
    sys.exit(main())
