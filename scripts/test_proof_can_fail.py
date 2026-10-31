#!/usr/bin/env python3
"""PROOF1: the frozen harness for "a check that would have passed anyway never reads PASS".

Written by the session before PROOF1 is built, never by its builder (HARN1), and RED ON THIS TREE BY
AN ASSERTION, never by an import error and never by a skip. The builder never edits this file.

CLASSES AND THE SUB UNIT EACH ONE CLOSES (the plan's own sub_done_checks are the source of this map):
  SelfRevert   PROOF1.a   scripts/brother_run.py, the self revert re-run stamped for every DONE row
  ZeroTests    PROOF1.b   scripts/receipt_door.py, the downgrade of a hollow PASS to NO-DATA
  Label        PROOF1.c   scripts/proof_card.py, the label the card prints
  Labels       PROOF1.d   scripts/brother_run.py and scripts/receipt_door.py, one author, no false repair

WHY THE POLARITY IS THE WHOLE RISK. Every requirement below makes the engine report LESS: a PASS it
printed yesterday becomes NO-DATA. So the cases that must survive the build are the ones where the
PASS is real, and they are asserted here beside the new refusals: a check that genuinely fails with
its own change reverted still reads PASS (REQ-PROOF1B-03), and a re-run that could not be made reads
NO-DATA rather than either verdict (REQ-PROOF1A-04, REQ-PROOF1B-04).

MEASURED ON THIS TREE BEFORE EACH ASSERTION WAS WRITTEN, by parsing the three files with ast and by
loading all three modules under both interpreters (3.13.14 and 3.9.6, all three load clean):
  scripts/brother_run.py   _check_without                 line 4714, present
                           _stamp_dependency_mutations    line 4824, present, and its loop reads
                                                          `if row.get("status") != "DONE" or not deps:
                                                          continue`, so a row with no depends_on is
                                                          skipped and gets no re-run at all
                           _self_revert_files             ABSENT
                           _stamp_self_revert             ABSENT
                           "sandbox-exec", "sandbox_exec" 0 occurrences in the whole file
  scripts/receipt_door.py  dependency_gap                 line 397, present, and returns "" at once
                                                          for a row with no depends_on
                           revert_broke_check             line 335, present, True for ImportError
                           red_check_gap                  line 616, present, scoped to change_kind
                                                          "behaviour" only
                           receipts_for                   line 1046, present, and calls dependency_gap,
                                                          e18_gap and red_check_gap and nothing else
                           unrun_check_reason             ABSENT (it is FIRST1's, not PROOF1's; see
                                                          the dependency note below)
                           self_revert_gap                ABSENT
                           zero_tests_ran                 ABSENT
                           "Ran 0 tests", "no tests ran", "collected 0 items"   0 occurrences
  scripts/proof_card.py    file_proof_label               line 152, present, returns a (label, reason)
                                                          PAIR and not a str, whatever the spec's
                                                          signature says, and its labels are exactly
                                                          "GREEN ONLY", "PROVES CHANGE" and NO-DATA
  "would have passed anyway", "zero tests ran"             0 occurrences in any of the three files
  the author wording "the planning model"                  4 literal occurrences (brother_run.py 7019
                                                           and 7022, receipt_door.py 1112 and 2174),
                                                           0 module level constants holding it
  receipt_door.receipt_record (line 2450) appends a repair_history row on
  `(isinstance(attempt, int) and attempt > 1) or was_rewritten`, which names no recorded failure, so a
  unit handed to the session and graded twice is reported as repaired

MEASURED LIVE, not only by reading the source. A hand written outcome contract (contract_check exit 0)
drove scripts/brother_run.py --plan --contract through a throwaway git repository with ZERO model
calls, and the receipt it wrote carries all three defects above:
  repair_history   [{"unit": "U1", "attempts": 2, "check_rewritten": false}]   a unit that never
                   failed, reported as repaired, which is REQ-PROOF1D-02 reproducing
  check_without_dependencies   ABSENT from the whole receipt, which is REQ-PROOF1A-01 reproducing: a
                   one unit task with no depends_on gets no re-run stamped at all
  scope.changed[0]   state "no-data", reason "the check already passed before the work began, so it
                   cannot prove the work", so THAT shape of vacuous probe is already caught, by
                   check_passed_before and not by anything PROOF1 adds. The builder must not read
                   this harness as proof that the J2 row C.vacuous-single-unit-not-pass is still red:
                   see the finding filed with this harness.

TWO REQUIREMENTS ARE ALREADY SATISFIED HERE AND ARE KEPT AS LABELLED REGRESSION GUARDS, not passed
off as reds. Each is labelled again at its own test:
  REQ-PROOF1A-04, the sandbox half: brother_run.py names sandbox-exec nowhere and _check_without runs
    its re-run through a plain subprocess runner in a throwaway git worktree, so the re-run already
    needs no sandbox. It is kept because PROOF1.a adds a second caller of that helper, and the thing
    most likely to break while adding one is the property the existing caller already has.
  REQ-PROOF1B-03, the dependency half: dependency_gap already answers "" for a non-zero clean failure,
    which is the PASS that must survive this unit. PROOF1.b edits that function's neighbourhood, so
    the permissive change is asserted not to leak into the one case that must still read PASS.

A DEPENDENCY THE PLAN DOES NOT ENCODE, recorded here because this harness measured it. PROOF1.b's
spec says receipts_for calls self_revert_gap and zero_tests_ran "after unrun_check_reason", and
unrun_check_reason does not exist in scripts/receipt_door.py on this tree: it is FIRST1's to write
(scripts/test_first_task.py asserts it at lines 183, 188 and 192). PROOF1 does not name FIRST1 in
depends_on. So the chain position PROOF1.b is specified to take does not exist yet. This harness does
NOT assert that position, because asserting another unit's symbol would make PROOF1 unbuildable until
FIRST1 lands; it asserts the two functions and their verdicts, which PROOF1 owns.

None of the three files is a member of a bundle/runtime twin, so no mirror regenerates with them.
"""
import ast
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BRUN = os.path.join(ROOT, "scripts/brother_run.py")
DOOR = os.path.join(ROOT, "scripts/receipt_door.py")
CARD = os.path.join(ROOT, "scripts/proof_card.py")
ABSENT = "NO-DATA: %s is not in this tree, so nothing here can judge it"
AUTHOR_WORDING = "the planning model"
PASSED_ANYWAY = "would have passed anyway"
ZERO_TESTS = "zero tests ran"


def source(path):
    """The text at path, or None when it cannot be read. An unreadable file is never empty text: the
    caller asserts on None, so a file this harness cannot read is a failure and never a pass."""
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return None


def parsed(path):
    """The module ast at path, or None when it cannot be read or parsed."""
    text = source(path)
    if text is None:
        return None
    try:
        return ast.parse(text, filename=path)
    except SyntaxError:
        return None


def defines(path, name):
    """True when path defines a module level function or class called name, False when it does not,
    None when the file could not be read or parsed. This is how every symbol below is asserted: by
    parsing, never by calling an absent function and reading the AttributeError as a verdict."""
    tree = parsed(path)
    if tree is None:
        return None
    return any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
               and n.name == name for n in tree.body)


def function_source(path, name):
    """The exact source of one module level function, or None. SCOPED BY NAME, because an anchor that
    appears in two functions reads the wrong one: measured on this estate 2026-10-25."""
    tree, text = parsed(path), source(path)
    if tree is None or text is None:
        return None
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(text, node)
    return None


def module(path, name):
    """The module at path loaded by file path, or None when it cannot be loaded. A load failure is the
    finding the caller asserts on, never a skip."""
    import importlib.util
    import sys
    if not os.path.isfile(path):
        return None
    for extra in (os.path.join(ROOT, "scripts"), os.path.join(ROOT, "scripts", "loop")):
        if extra not in sys.path:
            sys.path.insert(0, extra)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception:  # noqa: BLE001  the module is under test; a load failure is the finding
        return None
    return mod


def label_of(answer):
    """The label out of file_proof_label's answer, whichever shape it has. The spec's signature says
    the function returns a str and the tree's own function returns a (label, reason) pair, so this
    harness reads either rather than freezing that mismatch into a requirement."""
    if isinstance(answer, (tuple, list)) and answer:
        return str(answer[0])
    return str(answer)


def author_literals(path):
    """Every line in path carrying a string literal with the author wording, or None when the file
    cannot be parsed."""
    tree = parsed(path)
    if tree is None:
        return None
    return [node.lineno for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and AUTHOR_WORDING in node.value]


def author_constants(path):
    """The names of the module level constants in path whose value literally carries the author
    wording, or None when the file cannot be parsed. One such constant across the two files, read by
    name everywhere else, is what REQ-PROOF1D-01 asks for."""
    tree = parsed(path)
    if tree is None:
        return None
    names = []
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        value = node.value
        if not (isinstance(value, ast.Constant) and isinstance(value.value, str)
                and AUTHOR_WORDING in value.value):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        names.extend(t.id for t in targets if isinstance(t, ast.Name))
    return names


def repair_gate(path):
    """The source of the condition that decides a unit was repaired, or None when it cannot be found.

    Found by walking for the `if` whose body appends to repair_history and returning that `if`'s test.
    Scoped that way on purpose: the word "repair" appears six times in this file and only one of them
    is the gate, so a harness matching the word would judge a docstring. Not found is a FAILURE for
    the caller, never a pass: a gate this harness cannot locate is a gate it cannot hold."""
    tree, text = parsed(path), source(path)
    if tree is None or text is None:
        return None
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        appends = [c for c in ast.walk(node) if isinstance(c, ast.Call)
                   and isinstance(c.func, ast.Attribute) and c.func.attr == "append"
                   and isinstance(c.func.value, ast.Name) and c.func.value.id == "repair_history"]
        if appends:
            return ast.get_source_segment(text, node.test)
    return None


class _Base(unittest.TestCase):
    def setUp(self):
        for path, rel in ((BRUN, "scripts/brother_run.py"), (DOOR, "scripts/receipt_door.py"),
                          (CARD, "scripts/proof_card.py")):
            if not os.path.isfile(path):
                self.fail(ABSENT % rel)

    def has(self, path, name, rel):
        """Assert path defines name. An unreadable file fails by name rather than reading as a file
        that happens not to define it."""
        found = defines(path, name)
        self.assertIsNotNone(found, "%s could not be read or parsed, so nothing here can judge it" % rel)
        self.assertTrue(found, "%s does not define %s" % (rel, name))


class SelfRevert(_Base):
    """PROOF1.a: every DONE row gets its own done check re-run with its own change reverted."""

    def test_a_one_unit_task_gets_a_self_entry(self):
        """REQ-PROOF1A-01: every DONE row gets exactly one self entry, the rows with no depends_on
        included. MEASURED: _stamp_dependency_mutations reads `if row.get("status") != "DONE" or not
        deps: continue`, so a one unit task is skipped and nothing re-runs its check at all, which is
        the hole the V-main vacuous probe walked through."""
        body = function_source(BRUN, "_stamp_dependency_mutations")
        self.assertIsNotNone(body, "_stamp_dependency_mutations is not in scripts/brother_run.py, so "
                                   "nothing stamps a re-run for any row")
        self.assertNotIn("or not deps", body,
                         "the stamping loop still skips a DONE row with no depends_on, so a one unit "
                         "task gets no re-run and its check is never shown to exercise anything")
        self.assertIn("_stamp_self_revert", body,
                      "the stamping loop never calls _stamp_self_revert, so no row carries a self entry")

    def test_tests_are_not_reverted(self):
        """REQ-PROOF1A-02: only the change is reverted, the unit's own test files are kept. Reverting
        the test too would re-run a check that is not there, which reads as broken rather than as a
        check that does not cover its change."""
        self.has(BRUN, "_self_revert_files", "scripts/brother_run.py")
        mod = module(BRUN, "brother_run_under_test_self_files")
        self.assertIsNotNone(mod, "scripts/brother_run.py could not be loaded, so _self_revert_files "
                                  "cannot be exercised")
        kept = mod._self_revert_files({"files_changed_by_unit": [
            "scripts/mathlib.py", "scripts/test_mathlib.py", "scripts/mathlib_test.py",
            "tests/test_deep.py", "scripts/loop/runner.py"]})
        self.assertEqual(sorted(kept), ["scripts/loop/runner.py", "scripts/mathlib.py"],
                         "the reverted set must be the unit's non test files only: a test file put "
                         "back to before the change makes the re-run measure a missing test")

    def test_a_test_only_change_is_noted(self):
        """REQ-PROOF1A-03: a row whose only change is a test file gets exit_code None and the note, so
        it reads NO-DATA. Never 0, which would read as a check that passes with its change reverted,
        and never a silent absence."""
        self.has(BRUN, "_stamp_self_revert", "scripts/brother_run.py")
        mod = module(BRUN, "brother_run_under_test_stamp_self")
        self.assertIsNotNone(mod, "scripts/brother_run.py could not be loaded, so _stamp_self_revert "
                                  "cannot be exercised")

        def never_called(*_a, **_k):
            raise AssertionError("a test only change must be noted without running any command")

        entry = mod._stamp_self_revert(
            {"id": "U1", "status": "DONE", "done_check": "python3 -B scripts/test_mathlib.py",
             "files_changed_by_unit": ["scripts/test_mathlib.py"]},
            "deadbeef", ROOT, runner=never_called)
        self.assertIsInstance(entry, dict, "_stamp_self_revert must return one entry dict")
        self.assertEqual(entry.get("kind"), "self",
                         "the entry must be marked kind 'self', or receipt_door cannot tell it from a "
                         "dependency entry")
        self.assertIsNone(entry.get("exit_code"),
                          "a row with no non test file must give exit_code None, never 0: a 0 there "
                          "reads as a check that passes with its own change reverted")
        self.assertIn("no non test file changed", str(entry.get("note") or ""),
                      "the entry must carry the note saying why there was nothing to revert")

    def test_no_sandbox_needed(self):
        """REQ-PROOF1A-04: the re-run works where sandbox-exec is absent, so an installed plugin on a
        machine without it reads a real exit code and not NO-DATA.

        MEASURED ON THIS TREE: scripts/brother_run.py names sandbox-exec and sandbox_exec ZERO times,
        and _check_without runs its command through a plain subprocess runner inside a throwaway git
        worktree. So the sandbox half of this requirement is ALREADY SATISFIED and that half is a
        REGRESSION GUARD, not a red. It is kept because PROOF1.a adds a second caller to that helper,
        and the property most likely to break while adding one is the property the first caller
        already has. The red half is the stamp itself, which does not exist."""
        self.has(BRUN, "_stamp_self_revert", "scripts/brother_run.py")
        body = function_source(BRUN, "_stamp_self_revert")
        self.assertIsNotNone(body, "_stamp_self_revert is not in scripts/brother_run.py")
        self.assertIn("_check_without", body,
                      "_stamp_self_revert must re-use _check_without rather than inventing a second "
                      "way to re-run a check at a revision")
        text = source(BRUN)
        self.assertIsNotNone(text, "scripts/brother_run.py could not be read")
        for word in ("sandbox-exec", "sandbox_exec"):
            self.assertNotIn(word, text,
                             "the re-run path now names %s, so it cannot be made on a machine without "
                             "it and would report NO-DATA where it used to report an exit code" % word)


class ZeroTests(_Base):
    """PROOF1.b: a check that passes with its own change reverted, or ran zero tests, reads NO-DATA."""

    def door(self):
        mod = module(DOOR, "receipt_door_under_test")
        self.assertIsNotNone(mod, "scripts/receipt_door.py could not be loaded, so its verdicts "
                                  "cannot be exercised")
        return mod

    def test_the_vacuous_probe_is_no_data(self):
        """REQ-PROOF1B-01: the V-main vacuous probe reads NO-DATA "would have passed anyway". MEASURED:
        self_revert_gap does not exist and receipts_for calls dependency_gap, e18_gap and red_check_gap
        only, so a one unit row with no change_kind and no depends_on reaches no gap at all and prints
        PASS. That is receipt 20261018T215741-vacuous-probe, an assert True test over a comment only
        diff, reading DONE PASS."""
        self.has(DOOR, "self_revert_gap", "scripts/receipt_door.py")
        mod = self.door()
        gap = mod.self_revert_gap({"id": "U1", "status": "DONE", "check_without_dependencies": [
            {"unit": "U1", "kind": "self", "files": ["scripts/mathlib.py"], "revision": "deadbeef",
             "exit_code": 0, "stderr": "", "note": ""}]})
        self.assertIn(PASSED_ANYWAY, str(gap),
                      "a check that exits 0 with this unit's OWN change reverted must give the %r "
                      "reason, or a hollow green still prints PASS" % PASSED_ANYWAY)

    def test_zero_tests_is_no_data(self):
        """REQ-PROOF1B-02: a check that collected zero tests is NO-DATA, never PASS. A command that ran
        nothing exits 0, and exit 0 is what the receipt reads as proof."""
        self.has(DOOR, "zero_tests_ran", "scripts/receipt_door.py")
        mod = self.door()
        for text in ("Ran 0 tests in 0.000s\n\nOK\n", "no tests ran in 0.01s",
                     "collected 0 items", "collected 0 items / 1 error"):
            self.assertTrue(mod.zero_tests_ran(text),
                            "%r must read as zero tests collected, or a check that ran nothing is "
                            "reported as a check that proved something" % text[:40])
        for text in ("Ran 7 tests in 0.1s\n\nOK\n", "collected 10 items", "", None, 7):
            self.assertFalse(mod.zero_tests_ran(text),
                             "%r must not read as zero tests: an unreadable or ordinary output is not "
                             "a positive record that nothing ran" % (text,))

    def test_a_real_check_still_passes(self):
        """REQ-PROOF1B-03: a check that FAILS with its own change reverted still reads PASS. This is the
        requirement the unit must not break: every other line here makes the door report less, so the
        case that has to survive it is the one where the proof is real.

        MEASURED ON THIS TREE: dependency_gap already answers "" for a non-zero clean entry, so that
        polarity is ALREADY SATISFIED for dependency entries and that assertion is a REGRESSION GUARD
        rather than a red. It is kept because PROOF1.b edits this exact function's neighbourhood and a
        permissive mistake there would turn a real PASS into NO-DATA for every unit in the estate."""
        mod = self.door()
        covered = {"unit": "DEP", "files": ["scripts/mathlib.py"], "revision": "deadbeef",
                   "exit_code": 1, "stderr": "AssertionError: 2 != 3", "note": ""}
        self.assertEqual(mod.dependency_gap({"id": "U1", "depends_on": ["DEP"],
                                             "check_without_dependencies": [covered]}), "",
                         "a dependency re-run that failed cleanly is a covering check and must read as "
                         "no gap, or every real PASS in the estate turns into NO-DATA")
        self.has(DOOR, "self_revert_gap", "scripts/receipt_door.py")
        self_entry = dict(covered, unit="U1", kind="self")
        self.assertEqual(mod.self_revert_gap({"id": "U1", "status": "DONE",
                                              "check_without_dependencies": [self_entry]}), "",
                         "a self re-run that failed cleanly means the check does exercise the change, "
                         "so it must read as no gap and keep its PASS")

    def test_a_broken_rerun_is_no_data(self):
        """REQ-PROOF1B-04: a re-run that BROKE rather than failed is NO-DATA, not PASS and not a clean
        failure. A re-run whose import blew up reached no verdict about the behaviour, so it shows no
        coverage either.

        MEASURED ON THIS TREE: revert_broke_check already answers True for an ImportError stderr, so
        the teller exists and that assertion is a REGRESSION GUARD. The red is self_revert_gap, which
        has nothing to read it with."""
        mod = self.door()
        broken = "Traceback (most recent call last):\nImportError: no module named mathlib\n"
        self.assertTrue(mod.revert_broke_check(broken),
                        "an ImportError on the re-run must read as broken, or a check that could not "
                        "run is scored as a check that failed for the behaviour")
        self.has(DOOR, "self_revert_gap", "scripts/receipt_door.py")
        gap = mod.self_revert_gap({"id": "U1", "status": "DONE", "check_without_dependencies": [
            {"unit": "U1", "kind": "self", "files": ["scripts/mathlib.py"], "revision": "deadbeef",
             "exit_code": 1, "stderr": broken, "note": ""}]})
        self.assertNotEqual(gap, "",
                            "a self re-run that broke must give a reason and read NO-DATA, never an "
                            "empty gap that leaves the PASS standing")


class Label(_Base):
    """PROOF1.c: the proof card prints the label rather than a verdict the reason contradicts."""

    def card(self):
        mod = module(CARD, "proof_card_under_test")
        self.assertIsNotNone(mod, "scripts/proof_card.py could not be loaded, so its labels cannot be "
                                  "exercised")
        self.assertTrue(hasattr(mod, "file_proof_label"),
                        "scripts/proof_card.py does not define file_proof_label")
        return mod

    def test_the_card_names_the_vacuous_probe(self):
        """REQ-PROOF1C-01: the card names a would have passed anyway entry. MEASURED: file_proof_label
        has three labels, GREEN ONLY, PROVES CHANGE and NO-DATA, and an entry carrying this reason
        reads GREEN ONLY on exit 0, which tells a reader the check was green before the work and not
        that the check cannot tell the work from no work."""
        mod = self.card()
        label = label_of(mod.file_proof_label({
            "file": "scripts/mathlib.py", "unit": "U1", "exit_code": 0, "state": "verified",
            "check_passed_before": True,
            "reason": "%s: the check passes with this unit's own change reverted" % PASSED_ANYWAY}))
        self.assertIn(PASSED_ANYWAY, label,
                      "the card must print %r for this entry: the reason says the check proves nothing "
                      "and the label must say the same thing" % PASSED_ANYWAY)

    def test_the_card_names_zero_tests(self):
        """REQ-PROOF1C-02: the card names a zero tests entry, for the same reason: a command that
        collected nothing exited 0, and a label read off the exit code alone calls that proof."""
        mod = self.card()
        label = label_of(mod.file_proof_label({
            "file": "scripts/mathlib.py", "unit": "U1", "exit_code": 0, "state": "verified",
            "check_passed_before": None,
            "reason": "%s: the check collected no tests at all" % ZERO_TESTS}))
        self.assertIn(ZERO_TESTS, label,
                      "the card must print %r for this entry, or a check that ran nothing is presented "
                      "as one that proved the change" % ZERO_TESTS)


class Labels(_Base):
    """PROOF1.d: the receipt names who wrote the check once, and reports no repair that did not happen."""

    def test_one_author_everywhere(self):
        """REQ-PROOF1D-01: the run line and the receipt name the SAME author, chosen in one place and
        read by both. MEASURED: the author wording sits as a bare literal four times, scripts/
        brother_run.py lines 7019 and 7022 and scripts/receipt_door.py lines 1112 and 2174, with no
        module level constant holding it anywhere, while the run line at scripts/brother_run.py line
        6630 credits the plan to "you". Two authors for one artefact, and nothing keeps them equal."""
        constants = []
        for path, rel in ((BRUN, "scripts/brother_run.py"), (DOOR, "scripts/receipt_door.py")):
            found = author_constants(path)
            self.assertIsNotNone(found, "%s could not be parsed, so its author strings cannot be "
                                        "counted" % rel)
            constants.extend("%s:%s" % (rel, name) for name in found)
        self.assertEqual(len(constants), 1,
                         "exactly one module level constant across brother_run.py and receipt_door.py "
                         "must hold the author wording, found %d (%s): with none, the run line and the "
                         "receipt can drift apart silently"
                         % (len(constants), ", ".join(constants) or "none"))
        literals = []
        for path, rel in ((BRUN, "scripts/brother_run.py"), (DOOR, "scripts/receipt_door.py")):
            lines = author_literals(path)
            self.assertIsNotNone(lines, "%s could not be parsed" % rel)
            literals.extend("%s:%d" % (rel, line) for line in lines)
        self.assertEqual(len(literals), 1,
                         "the author wording must appear as a literal exactly once, inside that one "
                         "constant, found %d (%s): every other site reads it by name, or the two can "
                         "disagree" % (len(literals), ", ".join(literals) or "none"))

    def test_a_session_handed_unit_is_not_repaired(self):
        """REQ-PROOF1D-02: a unit with no recorded failure is never called repaired. MEASURED: the gate
        that appends a repair_history row in receipt_door.receipt_record is `(isinstance(attempt, int)
        and attempt > 1) or was_rewritten`, which names no failure at all, so a unit handed to the
        session and graded a second time is reported as "1 unit(s) were repaired" with nothing having
        failed. A repair the run invented is a claim about work that did not happen."""
        gate = repair_gate(DOOR)
        self.assertIsNotNone(gate, "the condition that appends a repair_history row could not be found "
                                   "in scripts/receipt_door.py, so nothing here can hold it: an "
                                   "unlocatable gate is a failure, never a pass")
        self.assertRegex(gate.lower(), r"fail",
                         "the repair gate (%s) decides on the attempt count alone and never consults a "
                         "recorded failure, so an attempt that followed nothing is reported as a repair"
                         % " ".join(gate.split()))


if __name__ == "__main__":
    unittest.main(verbosity=1)
