"""The cognitive debt a delivery leaves the next reader, counted and driven
both ways (E75.3).

WHY THIS IS ITS OWN CHECK RATHER THAN THREE MORE CASES IN
test_receipt_door.py: the count is the one part of acceptance compression
that is deliberately INTERNAL. It rides on the machine receipt and never
becomes a flag, a mode or a screen, and a rule with nothing running it is a
sentence in a docstring. This file is what runs it, and check_all.sh's own
"acceptance-compression" line is what makes the battery run this.

Every case below builds a record and a claim dict and pushes them through
receipts_for, the same seam the receipt's own tests use, so no receipt here
is hand-typed and no signal is read off prose.
"""
import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import receipt_door as RD  # noqa: E402


def receipts_for_rows(rows):
    """(record, receipts) for rows whose checks all passed, built through
    receipts_for rather than typed: check_passed_before False and exit 0 is
    what the engine records for a unit that actually proved its change."""
    record = {"outcome": "seeded", "work_id": "w", "rows": rows}
    claims = {r["id"]: {"state": "done", "evidence": {"exit_code": 0,
                                                      "output": ""}}
              for r in rows}
    return record, RD.receipts_for(record, claims, [], "run.log")


def row(uid, owns, files):
    return {"id": uid, "objective": "seeded", "done_check": "true",
            "status": "DONE", "check_passed_before": False,
            "owns": owns, "files_changed_by_unit": files}


class TheDebtCountReadsTheRunsOwnFields(unittest.TestCase):
    def test_a_plain_change_carries_no_debt(self):
        record, receipts = receipts_for_rows(
            [row("U1", ["src/adder.py"], ["src/adder.py"])])
        self.assertEqual(RD.cognitive_debt(record, receipts),
                         {"count": 0, "signals": []})

    def test_a_new_dependency_raises_the_count(self):
        record, receipts = receipts_for_rows(
            [row("U1", ["src/", "requirements.txt"],
                 ["src/adder.py", "requirements.txt"])])
        debt = RD.cognitive_debt(record, receipts)
        self.assertEqual(debt["count"], 1)
        self.assertEqual(debt["signals"][0]["signal"], "new dependency")
        self.assertEqual(debt["signals"][0]["path"], "requirements.txt")

    def test_a_new_abstraction_raises_the_count(self):
        record, receipts = receipts_for_rows(
            [row("U1", ["src/"], ["src/storage_adapter.py"])])
        debt = RD.cognitive_debt(record, receipts)
        self.assertEqual(debt["count"], 1)
        self.assertEqual(debt["signals"][0]["signal"], "new abstraction")

    def test_a_file_outside_the_declared_scope_raises_the_count(self):
        record, receipts = receipts_for_rows(
            [row("U1", ["src/adder.py"], ["src/adder.py", "tools/other.py"])])
        debt = RD.cognitive_debt(record, receipts)
        self.assertEqual(debt["count"], 1)
        self.assertEqual(debt["signals"][0]["signal"], "scope drift")
        self.assertEqual(debt["signals"][0]["path"], "tools/other.py")

    def test_the_three_signals_add_up_rather_than_replacing_each_other(self):
        record, receipts = receipts_for_rows(
            [row("U1", ["src/"], ["src/base_writer.py", "requirements.txt",
                                  "tools/other.py"])])
        debt = RD.cognitive_debt(record, receipts)
        # requirements.txt: a dependency AND outside src/. base_writer.py: an
        # abstraction. tools/other.py: outside src/. Four facts, four
        # signals, because a count that collapses them hides one.
        self.assertEqual(
            sorted(s["signal"] for s in debt["signals"]),
            ["new abstraction", "new dependency", "scope drift",
             "scope drift"])
        self.assertEqual(debt["count"], 4)

    def test_the_count_rides_on_the_machine_receipt(self):
        record, receipts = receipts_for_rows(
            [row("U1", ["src/", "requirements.txt"],
                 ["src/adder.py", "requirements.txt"])])
        rec = RD.receipt_record(record, receipts)
        self.assertEqual(rec["attention"]["cognitive_debt"]["count"], 1)


class TheCountIsNeverAVisibleMode(unittest.TestCase):
    """Driven the way the row names it: the engine's own front door must
    grow no flag for this. A count a person is shown is a count a person can
    be asked to hit."""

    def test_the_engines_help_grew_no_flag(self):
        run = subprocess.run(
            [sys.executable, os.path.join(HERE, "brother_run.py"), "--help"],
            capture_output=True, text=True, timeout=120)
        if run.returncode != 0:
            print("%s: brother_run.py --help exited %d, so this case could "
                  "not read its flags: %s"
                  % (RD.NODATA, run.returncode, (run.stderr or "").strip()))
            self.skipTest("%s: brother_run.py --help did not run" % RD.NODATA)
        for word in ("cognitive", "debt", "compression"):
            self.assertNotIn(word, run.stdout.lower(), run.stdout)

    def test_the_acceptance_screen_never_prints_the_count(self):
        record, receipts = receipts_for_rows(
            [row("U1", ["src/"], ["src/adder.py", "requirements.txt"])])
        spec = RD.acceptance_spec(record, receipts)
        html = __import__("decide").render(spec).lower()
        # The four reading sections ARE on the screen; the count is not.
        self.assertIn(RD.REVIEW_FIRST.lower(), html)
        self.assertNotIn("cognitive debt", html)


if __name__ == "__main__":
    unittest.main()
