#!/usr/bin/env python3
"""FIRST1H: contract coverage and the unrun check reason, exercised rather than named.

WHY THIS FILE EXISTS, for the same reason scripts/test_consent_gate.py exists beside
FIRST1's frozen harness. scripts/test_first_task.py is FIRST1H's done check and it is
frozen by sha in the plan (HARN1 refuses a lane that edits it), which is right. But its
`Unrun` class is three cases and all three assert only that one symbol exists, so a
single `def unrun_check_reason(): pass` turns the class green; and `Twice` carries one
real assertion (that the word startswith is gone) plus three presence lookups, so a
`covers` that always returned True would satisfy it.

So the frozen harness stays untouched and this is the adversary beside it: every case
calls the thing and judges what it did.

TWO MEASUREMENTS THAT SHAPED THIS FILE, both recorded because they contradict the
harness's own docstrings and the next reader deserves to know:

  The base was ALREADY right on all three Unrun states. `Unrun`'s docstring says "On
  the base such a row yields PASS receipts". Measured on 2026-10-29: an absent exit
  code, a string, a float and a bool all read no-data, and a recorded 0 read verified.
  What was wrong was the REASON, which told a reader "no captured exit code was
  recorded" for three cases where something was recorded and merely was not an exit
  code. That hides a producer writing rubbish, so that is what FIRST1H.b fixes, and the
  states are pinned here so the fix cannot quietly change them.

  THE HONEST PASS HAS TO SURVIVE. A `covers` that always said False, and an
  `unrun_check_reason` that always returned a reason, would both satisfy every refusal
  case in this file. So each guard is tested as a PAIR, and the paired case is marked.

    python3 -B scripts/test_contract_coverage.py
    python3 -B scripts/test_contract_coverage.py --selftest     (same, for check_all.sh)
"""
import importlib.util
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def _load(name, rel):
    """Load by path, with scripts/ on sys.path: receipt_door.py imports `decide`, a
    sibling, so a load that does not put its directory on the path raises
    ModuleNotFoundError rather than answering."""
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, rel))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


RUN = _load("run_under_test", "scripts/brother_run.py")
DOOR = _load("door_under_test", "scripts/receipt_door.py")

FILE_CMD = "python3 -m pytest -q test_mathlib.py"
FILTERED = "python3 -m pytest -q test_mathlib.py -k reject"
NODE = "python3 -m pytest -q test_mathlib.py::test_one"


class Coverage(unittest.TestCase):
    """FIRST1H.a: the exact command or a declared superset, never a prefix."""

    def test_the_exact_command_covers(self):
        """THE PAIRED CASE. Without it, a covers() that always returned False would
        satisfy every refusal below and the contract gate would refuse everything."""
        self.assertTrue(RUN.covers(FILE_CMD, FILE_CMD))

    def test_a_k_filter_does_not_cover(self):
        """The measured defect: on the base `startswith` made a filter that selects one
        vacuous test satisfy a contract that asked for the whole file (V-main)."""
        self.assertFalse(RUN.covers(FILTERED, FILE_CMD))

    def test_a_node_id_does_not_cover(self):
        self.assertFalse(RUN.covers(NODE, FILE_CMD))

    def test_a_declared_superset_covers(self):
        self.assertTrue(RUN.covers(FILTERED, FILE_CMD, declared=[FILE_CMD]))

    def test_an_undeclared_superset_does_not_cover(self):
        """The pair for the case above: declaring SOMETHING must not declare everything."""
        self.assertFalse(RUN.covers(FILTERED, FILE_CMD, declared=["python3 -m pytest -q other.py"]))

    def test_two_spellings_of_one_path_are_one_command(self):
        self.assertTrue(RUN.covers("python3 ./t.py", "python3 sub/../t.py"))

    def test_two_different_paths_are_not_one_command(self):
        """The pair: realpath must collapse spellings of ONE path, not two paths."""
        self.assertFalse(RUN.covers("python3 ./t.py", "python3 ./u.py"))

    def test_an_empty_command_is_never_covered(self):
        self.assertFalse(RUN.covers("python3 t.py", ""))
        self.assertFalse(RUN.covers("", ""))

    def test_an_unparseable_command_does_not_raise(self):
        """A quoting mistake is a command somebody wrote, not a crash."""
        self.assertFalse(RUN.covers('python3 "unclosed', FILE_CMD))
        self.assertTrue(RUN.covers('python3 "unclosed', 'python3 "unclosed'))

    def test_the_key_is_stable_across_calls(self):
        """REQ-FIRST1HA-04: no state survives between calls."""
        self.assertEqual(RUN._command_key(FILE_CMD), RUN._command_key(FILE_CMD))


class UnmetChecks(unittest.TestCase):
    """The entry point, not the helper: coverage only matters where the gate uses it."""

    def gate(self, command, done_check, declared=None):
        unit = {"id": "U1", "done_check": done_check}
        if declared is not None:
            unit["covers"] = declared
        return RUN.unmet_contract_checks(
            {"success_checks": [{"id": "c1", "command": command}]}, [unit])

    def test_a_filtered_done_check_leaves_the_contract_unmet(self):
        self.assertEqual(self.gate(FILE_CMD, FILTERED), [("c1", FILE_CMD)])

    def test_the_exact_done_check_meets_it(self):
        """THE PAIRED CASE: the gate must still accept a plan that does run the check."""
        self.assertEqual(self.gate(FILE_CMD, FILE_CMD), [])

    def test_a_declared_superset_meets_it(self):
        self.assertEqual(self.gate(FILE_CMD, FILTERED, declared=[FILE_CMD]), [])

    def test_a_contract_with_no_command_is_not_counted(self):
        self.assertEqual(RUN.unmet_contract_checks(
            {"success_checks": [{"id": "c1", "command": ""}]}, [{"done_check": "x"}]), [])

    def test_a_non_dict_unit_is_skipped_not_crashed(self):
        self.assertEqual(RUN.unmet_contract_checks(
            {"success_checks": [{"id": "c1", "command": FILE_CMD}]},
            ["not a unit", {"done_check": FILE_CMD}]), [])

    def test_twice_gives_the_same_answer(self):
        contract = {"success_checks": [{"id": "c1", "command": FILE_CMD}]}
        units = [{"id": "U1", "done_check": FILTERED}]
        self.assertEqual(RUN.unmet_contract_checks(contract, units),
                         RUN.unmet_contract_checks(contract, units))


class UnrunReason(unittest.TestCase):
    """FIRST1H.b: the reason names which case it is, and the states do not move."""

    def test_an_absent_exit_code_says_nothing_was_recorded(self):
        r = DOOR.unrun_check_reason(None)
        self.assertIn("no captured exit code was recorded", r)

    def test_a_text_exit_code_says_it_is_not_an_integer(self):
        """The measured defect: this used to report that nothing was recorded, when a
        value WAS recorded and merely was not an exit code."""
        r = DOOR.unrun_check_reason("0")
        self.assertIn("not an", r)
        self.assertIn("str", r)
        self.assertNotIn("no captured exit code was recorded", r)

    def test_a_float_exit_code_says_it_is_not_an_integer(self):
        r = DOOR.unrun_check_reason(0.0)
        self.assertIn("float", r)
        self.assertNotIn("no captured exit code was recorded", r)

    def test_a_boolean_exit_code_is_called_out_by_name(self):
        """True is an int in Python, so a bool would otherwise be read as exit code 1."""
        r = DOOR.unrun_check_reason(True)
        self.assertIn("boolean", r)

    def test_a_real_exit_code_has_no_reason(self):
        """THE PAIRED CASE. A function that always returned a reason would satisfy every
        case above and would turn every honest pass into a no-data."""
        self.assertEqual(DOOR.unrun_check_reason(0), "")
        self.assertEqual(DOOR.unrun_check_reason(1), "")


class ThroughTheDoor(unittest.TestCase):
    """The wiring. A reason nothing reaches is a reader nothing calls (PROOF1)."""

    def receipt(self, exit_code, present=True):
        row = {"id": "U1", "done_check": "python3 -B t.py", "status": "DONE",
               "check_passed_before": False, "files_changed_by_unit": ["a.py"]}
        ev = {"check_command": "python3 -B t.py"}
        if present:
            ev["exit_code"] = exit_code
        rec = {"rows": [row]}
        r = DOOR.receipts_for(rec, {"U1": {"evidence": ev}}, [])[0]
        return r, DOOR.per_file_checks(rec, [r])

    def test_a_text_exit_code_reaches_the_file_entry_verbatim(self):
        r, files = self.receipt("0")
        self.assertEqual(r["state"], "no-data")
        self.assertIn("not an", r["reason"])
        self.assertEqual(files[0]["reason"], r["reason"],
                         "the per-file entry does not carry the reason verbatim")

    def test_a_float_exit_code_reaches_the_file_entry(self):
        r, _files = self.receipt(0.0)
        self.assertEqual(r["state"], "no-data")
        self.assertIn("float", r["reason"])

    def test_an_absent_exit_code_is_still_no_data(self):
        r, files = self.receipt(None, present=False)
        self.assertEqual(r["state"], "no-data")
        self.assertEqual(files[0]["state"], "no-data")

    def test_a_recorded_zero_still_reads_verified(self):
        """THE PAIRED CASE, and the one that matters most: REQ-FIRST1HB-02. The new
        reason must not swallow the honest pass."""
        r, files = self.receipt(0)
        self.assertEqual(r["state"], "verified")
        self.assertEqual(files[0]["state"], "verified")
        self.assertEqual(r["reason"], "")

    def test_a_recorded_one_names_the_exit_code(self):
        r, _files = self.receipt(1)
        self.assertEqual(r["state"], "no-data")
        self.assertIn("exited 1", r["reason"])

    def test_a_boolean_exit_code_is_not_read_as_one(self):
        r, _files = self.receipt(True)
        self.assertEqual(r["state"], "no-data")
        self.assertIn("boolean", r["reason"])
        self.assertNotIn("exited 1", r["reason"])


def main(argv):
    suite = unittest.TestLoader().loadTestsFromModule(sys.modules[__name__])
    return 0 if unittest.TextTestRunner(verbosity=1).run(suite).wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main([a for a in sys.argv[1:] if a != "--selftest"]))
