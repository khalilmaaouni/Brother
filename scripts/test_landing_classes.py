#!/usr/bin/env python3
"""S8's frozen harness: every landing class is proven once, in daylight, with a watcher.

Written by the session before the unit is built and never by its builder (HARN1). S8's builder is a live
landing through the live driver, so no builder could have written this; the spec says it in as many
words: "the frozen harness: written by the session, red first, before the code is built, never by its
builder".

RED ON THE BASE BY AN ASSERTION. There is no record on the base, so judge() answers NO-DATA and the two
requirement cases fail on that answer. Nothing here imports the lander, calls a symbol that does not
exist, or runs a landing.

WHY THE JUDGE LIVES IN THE HARNESS. S8 ships exactly one new file,
docs/plan/specs/1.1.9/LANDING-CLASSES.json. The record IS the deliverable, so there is nothing to import
and the record's contract has to be stated first. It is stated here, before any record exists, rather
than inferred later from whatever the first record happens to contain.

THE CONTRACT, per class: commit, landed_at (YYYY-MM-DD HH:MM local), a watcher, a gate_pair answering 0
on both interpreters, and one witness each: the product class carries the lander's own LANDED line, the
kernel class carries its stamp activation. Beside classes, out_of_scope names the third class and the
rule excluding it.

THE REQUIRED SET IS TWO CLASSES, product and kernel, and the third is excluded BY NAME rather than by
silence. The spec: "The kernel class is a stamp, proven by S1's own daylight activation; no loop tool
lands through the 1.1.9 loop lane (R-S10), so that class is not exercised here." A class left out with no
reason reads exactly like a class nobody thought of.

DAYLIGHT, defined here because nothing else defines it: landed_at's hour is 6 through 17 inclusive, local
to whoever landed it. The unit's own title is "proven once in daylight with a watcher"; a harness that
skipped the hour would be judging a different unit.

THE SUBJECT IS THE DELIVERABLE, NOT A FIXTURE (the 2026-09-21 delivery law, after a unit closed on a test
that built its own 70 row catalogue while the real file held 10). The two REQ cases read the REAL record
path and the real git; they are what closes S8. Every case named fixture_ exists only to prove one guard
can refuse, which is what makes the spec's mutation catchable.

SIX GUARDS, one fixture each, so no guard hides behind a sibling:
  G1 a record absent, unreadable, or not a JSON object          -> NO-DATA
  G2 a required class with no record, or an unexplained
     out_of_scope                                              -> FAIL  (M-S8A-ONE-CLASS aims here)
  G3 a commit that is not an ancestor of this branch            -> FAIL
  G4 a landing with no watcher, or a landed_at outside daylight -> FAIL
  G5 a class with no witness, or a LANDED line for another
     commit                                                    -> FAIL
  G6 a red, absent or wrongly typed gate on either interpreter  -> FAIL  (REQ-S8A-02)

Done check: python3 -B scripts/test_landing_classes.py ClassesProven
"""
import json
import os
import re
import subprocess
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RECORD = os.path.join(ROOT, "docs/plan/specs/1.1.9/LANDING-CLASSES.json")
NODATA, FAIL, PASS = "NO-DATA", "FAIL", "PASS"

REQUIRED = ("product", "kernel")          # M-S8A-ONE-CLASS drops one of these
EXCLUDED = ("loop_tool", "R-S10")         # the name and the rule, both required in out_of_scope
INTERPRETERS = ("python3", "/usr/bin/python3")
AT = re.compile(r"^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2})$")
DAYLIGHT = range(6, 18)
WITNESS = {"product": "landed_line", "kernel": "stamp_activated"}


def load_record(path):
    """The record as a dict, or None for anything that is not a readable JSON object.

    Hostile inputs answer None rather than raising: None, an empty or blank string, a number, a path
    outside the tree, a directory, a JSON list. A reader that raises on junk turns a refusal into an
    error, and an error is red for the wrong reason."""
    if not isinstance(path, str) or not path.strip():
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError, TypeError, UnicodeDecodeError):
        return None
    return doc if isinstance(doc, dict) else None


def is_ancestor(root, commit):
    """True when the commit is reachable from HEAD, False when it is not, None when git cannot answer.
    judge() turns None into NO-DATA: an unanswerable git is never the safe case."""
    if not isinstance(commit, str) or not commit.strip():
        return False
    try:
        out = subprocess.run(["git", "-C", root, "merge-base", "--is-ancestor", commit, "HEAD"],
                             capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode == 0:
        return True
    if out.returncode == 1:
        return False
    return None


def daylight_hour(landed_at):
    """The hour, when landed_at is a well formed local stamp inside daylight. None otherwise."""
    if not isinstance(landed_at, str):
        return None
    m = AT.match(landed_at.strip())
    if not m:
        return None
    hour = int(m.group(4))
    if int(m.group(2)) not in range(1, 13) or int(m.group(3)) not in range(1, 32):
        return None
    if int(m.group(5)) > 59 or hour > 23:
        return None
    return hour if hour in DAYLIGHT else None


def judge(rec, root, ancestor=is_ancestor):
    """(verdict, reason) for one landing-classes record. PASS only when every guard is satisfied.

    ancestor is an argument so a fixture can isolate one guard without a repository, and so the two REQ
    cases run against the real git. Nothing here writes."""
    if not isinstance(rec, dict):
        return NODATA, "no readable landing record at %s" % os.path.relpath(RECORD, ROOT)

    scope = rec.get("out_of_scope")
    if not isinstance(scope, dict) or EXCLUDED[0] not in scope:
        return FAIL, "out_of_scope does not name %s, so a class is missing with no reason" % EXCLUDED[0]
    rule = scope.get(EXCLUDED[0])
    if not isinstance(rule, str) or EXCLUDED[1] not in rule:
        return FAIL, "%s is excluded without naming %s, the rule that excludes it" % EXCLUDED

    classes = rec.get("classes")
    if not isinstance(classes, dict):
        return FAIL, "the record names no classes"
    missing = [name for name in REQUIRED if not isinstance(classes.get(name), dict)]
    if missing:
        return FAIL, "no watched landing is recorded for %s" % ", ".join(missing)

    for name in REQUIRED:
        entry = classes[name]
        commit = entry.get("commit")
        reach = ancestor(root, commit)
        if reach is None:
            return NODATA, "git could not say whether %s's commit %r is on this line" % (name, commit)
        if not reach:
            return FAIL, "%s's landing %r is not on this line" % (name, commit)

        watcher = entry.get("watcher")
        if not isinstance(watcher, str) or not watcher.strip():
            return FAIL, "%s's landing names no watcher, and an unwatched landing proves nothing" % name

        if daylight_hour(entry.get("landed_at")) is None:
            return FAIL, ("%s's landed_at is %r, which is not a daylight local stamp (YYYY-MM-DD HH:MM, "
                          "hour 06 to 17)" % (name, entry.get("landed_at")))

        field = WITNESS[name]
        witness = entry.get(field)
        if not isinstance(witness, str) or not witness.strip():
            return FAIL, "%s's landing carries no %s" % (name, field)
        if name == "product":
            if not witness.lstrip().startswith("LANDED"):
                return FAIL, ("%s's landed_line is not the lander's own LANDED line: %r"
                              % (name, witness[:60]))
            if not isinstance(commit, str) or commit not in witness:
                return FAIL, "%s's LANDED line does not name the commit it is recorded against" % name

        gate = entry.get("gate_pair")
        if not isinstance(gate, dict):
            return FAIL, "%s's landing records no gate pair after it" % name
        for interp in INTERPRETERS:
            code = gate.get(interp)
            if not isinstance(code, int) or isinstance(code, bool) or code != 0:
                return FAIL, ("the gate after %s's landing reads %r on %s, so that class is refused"
                              % (name, code, interp))
    return PASS, "every required landing class is proven in daylight with a watcher and a green gate pair"


def fixture_stamp(hour=None):
    """A landed_at the fixtures use, built from the clock and from DAYLIGHT itself.

    No literal time is typed here on purpose. A hard coded stamp would have to be re-chosen whenever the
    daylight window moved, and a fixture that drifts out of the window it is testing starts refusing for
    a reason nobody wrote. The default hour is the middle of the window, so it is inside it by
    construction."""
    if hour is None:
        hour = list(DAYLIGHT)[len(DAYLIGHT) // 2]
    return "%s %02d:%02d" % (time.strftime("%Y-%m-%d"), hour, 0)


def fixture_class(which, **over):
    """One class entry shaped so it PASSES, so each case changes exactly one field."""
    sha = "deadbee"
    got = {"commit": sha, "landed_at": fixture_stamp(), "watcher": "runs/x/watcher.log"}
    got["gate_pair"] = dict((interp, 0) for interp in INTERPRETERS)
    got[WITNESS[which]] = ("LANDED  one unit, at %s" % sha) if which == "product" \
        else "a stamp activation record"
    got.update(over)
    return got


def fixture_record(classes=None, scope=None):
    """A whole record that PASSES, with classes or out_of_scope replaced."""
    if classes is None:
        classes = dict((which, fixture_class(which)) for which in REQUIRED)
    if scope is None:
        scope = {EXCLUDED[0]: "R-S10: that class is not exercised by this release"}
    return {"classes": classes, "out_of_scope": scope}


def fixture_classes(**over):
    """Every required class, with one field of one class replaced: which__field=value."""
    classes = dict((which, fixture_class(which)) for which in REQUIRED)
    for key, value in over.items():
        which, field = key.split("__", 1)
        classes[which][field] = value
    return classes


def reachable(*_ignored):
    """An ancestor answer that always says yes, so a fixture isolates a guard that is not G3."""
    return True


class ClassesProven(unittest.TestCase):
    """S8.a. The two REQ cases are the deliverable; every fixture case is one guard's own proof."""

    # REQ-S8A-01, the case that closes S8: the real record, the real git, nothing synthetic.
    def test_every_class_has_a_watched_landing(self):
        verdict, reason = judge(load_record(RECORD), ROOT)
        self.assertEqual(PASS, verdict, reason)

    # REQ-S8A-02, also read from the real record: a red gate after a landing refuses its class.
    def test_a_red_gate_refuses(self):
        rec = load_record(RECORD)
        verdict, reason = judge(rec, ROOT)
        self.assertEqual(PASS, verdict, reason)
        for which in REQUIRED:
            gate = rec["classes"][which].get("gate_pair")
            self.assertEqual(sorted(INTERPRETERS), sorted(gate),
                             "%s's gate pair must answer for both interpreters" % which)

    # G1, one case per hostile input: a reader that raises would be an error, not a refusal.
    def test_a_record_that_cannot_be_read_is_no_data(self):
        for bad in (None, "", "   ", 0, 1.5, [], {}, "/etc/hosts-does-not-exist",
                    os.path.join(ROOT, "docs"), __file__):
            self.assertIsNone(load_record(bad), "load_record(%r) should answer None" % (bad,))
        self.assertEqual(NODATA, judge(None, ROOT, reachable)[0])
        self.assertEqual(NODATA, judge([], ROOT, reachable)[0])

    # G2, the guard M-S8A-ONE-CLASS deletes. Drop a class from REQUIRED and judge() stops refusing a
    # record that is missing it, so this assertion goes red. One class missing at a time.
    def test_fixture_a_missing_required_class_refuses(self):
        # The required set itself, pinned: the spec names product and kernel, and excludes the third by
        # rule. Without this line M-S8A-ONE-CLASS is only caught by a sibling case, because the case the
        # spec names for it reads the real record and is already red until S8 lands.
        self.assertEqual(("product", "kernel"), REQUIRED)
        for which in REQUIRED:
            held = dict((n, fixture_class(n)) for n in REQUIRED if n != which)
            got = judge(fixture_record(classes=held), ROOT, reachable)
            self.assertEqual(FAIL, got[0],
                             "a record with no %s class must refuse, got %s" % (which, got))
            self.assertIn(which, got[1])

    # G2's sibling: the excluded class is excluded by name AND by rule, never by silence.
    def test_fixture_an_unexplained_exclusion_refuses(self):
        for scope in ({}, {EXCLUDED[0]: "not run"}, {EXCLUDED[1]: "whatever"}, EXCLUDED[1], None if 0
                      else 7, {EXCLUDED[0]: None}):
            got = judge(fixture_record(scope=scope), ROOT, reachable)
            self.assertEqual(FAIL, got[0], "out_of_scope=%r should refuse, got %s" % (scope, got))

    # G3
    def test_fixture_a_landing_off_this_line_refuses(self):
        self.assertEqual(FAIL, judge(fixture_record(), ROOT, lambda *a: False)[0])
        self.assertEqual(NODATA, judge(fixture_record(), ROOT, lambda *a: None)[0])

    # G4a, the watcher, isolated from the hour.
    def test_fixture_an_unwatched_landing_refuses(self):
        for value in (None, "", "   ", 5):
            got = judge(fixture_record(classes=fixture_classes(product__watcher=value)), ROOT,
                        reachable)
            self.assertEqual(FAIL, got[0], "watcher=%r should refuse, got %s" % (value, got))
            self.assertIn("watcher", got[1])

    # G4b, the hour, isolated from the watcher, and bound to DAYLIGHT rather than to a typed time: a
    # mutation that widens the window makes the first loop below go green and this case red.
    def test_fixture_a_landing_outside_daylight_refuses(self):
        hours = list(DAYLIGHT)
        same_day = fixture_stamp().split()[0]
        clock = fixture_stamp().split()[1]
        bad = [fixture_stamp(hour=hours[0] - 1), fixture_stamp(hour=hours[-1] + 1),
               fixture_stamp(hour=0), fixture_stamp(hour=23),
               "last night", "", "   ", None, 9, [],
               "%s-13-01 %s" % (time.strftime("%Y"), clock),
               "%s-02-32 %s" % (time.strftime("%Y"), clock),
               fixture_stamp().replace(" ", "T"), same_day, clock]
        for stamp in bad:
            got = judge(fixture_record(classes=fixture_classes(kernel__landed_at=stamp)), ROOT,
                        reachable)
            self.assertEqual(FAIL, got[0], "landed_at=%r should refuse, got %s" % (stamp, got))
        for hour in (hours[0], hours[-1]):
            stamp = fixture_stamp(hour=hour)
            got = judge(fixture_record(classes=fixture_classes(kernel__landed_at=stamp)), ROOT,
                        reachable)
            self.assertEqual(PASS, got[0], "%r is inside DAYLIGHT and must pass: %s" % (stamp, got[1]))

    # G5, the witness each class owes: the lander's own line for the product, a stamp for the kernel.
    def test_fixture_a_landing_with_no_witness_refuses(self):
        for which in REQUIRED:
            for value in (None, "", "   ", 3):
                got = judge(fixture_record(classes=fixture_classes(**{
                    "%s__%s" % (which, WITNESS[which]): value})), ROOT, reachable)
                self.assertEqual(FAIL, got[0], "%s=%r should refuse" % (WITNESS[which], value))
        for line in ("the batch landed fine, at deadbee", "landed deadbee", " LANDED at cafef00"):
            got = judge(fixture_record(classes=fixture_classes(product__landed_line=line)), ROOT,
                        reachable)
            self.assertEqual(FAIL, got[0], "landed_line=%r should refuse, got %s" % (line, got))

    # G6
    def test_fixture_a_red_or_absent_gate_refuses(self):
        first, second = INTERPRETERS[0], INTERPRETERS[1]
        for gate in ({first: 1, second: 0}, {first: 0, second: 2}, {first: 0}, {second: 0}, {}, None,
                     {first: 0, second: True}, {first: "0", second: 0}, {first: 0, second: 0.0}, 0):
            got = judge(fixture_record(classes=fixture_classes(product__gate_pair=gate)), ROOT,
                        reachable)
            self.assertEqual(FAIL, got[0], "gate_pair=%r should refuse, got %s" % (gate, got))

    # The fixture itself must be able to pass, or every case above proves only that judge() says FAIL.
    def test_fixture_a_complete_record_passes(self):
        got = judge(fixture_record(), ROOT, reachable)
        self.assertEqual(PASS, got[0], got[1])


if __name__ == "__main__":
    unittest.main(verbosity=1)
