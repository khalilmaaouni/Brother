#!/usr/bin/env python3
"""S3 harness: admission refuses a test or doc only sub unit, and one whose council record carries a proven blocker.

runner_pool.admissible admitted LC3.b with five blockers its council had proven (D13), and admits a sub unit whose
every changed file is a test or a doc, which the grader can never pass: it requires tests that fail without the code
(D15). admissible is the one admission path (the pass, salvage and diag_apply route through it), so the refusal lives
there and names its reason.

The cases reach admissible itself with a one unit plan and a real spec file; the checks admissible runs BEFORE the
new rule (the owner's supply hold, the landing gate's done check, the plan lint) are stubbed to admit, so each case
judges the new rule alone. The council record is a fixture file named through runner_pool.COUNCIL_PATH.

Written first, red on the base by assertion (1.1.8 stability release, owner ruling C2, 2026-10-24; spec
docs/plan/specs/1.1.9/S3.md). Done check: python3 -B scripts/test_admission_refusals.py Refusals
"""
import contextlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "loop"))
import runner_pool as RP  # noqa: E402

SPEC = """# X1

### X1.a one thing

Files: %s.

Harness: `scripts/test_x1.py` (NEW).

Done check: `python3 -B scripts/test_x1.py X1a`
"""


class Refusals(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="admission-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.council = os.path.join(self.dir, "spec-council.json")
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(mock.patch.object(RP, "COUNCIL_PATH", self.council, create=True))
        stack.enter_context(mock.patch.object(RP.plan_store, "supply_hold", lambda sub: ""))
        stack.enter_context(mock.patch.object(RP.spec_check, "gate_refusal", lambda text, sub: ""))
        stack.enter_context(mock.patch.object(RP, "lint_refusal", lambda unit, sub, text: ""))

    def plan(self, files):
        spec = os.path.join(self.dir, "X1.md")
        with open(spec, "w", encoding="utf-8") as fh:
            fh.write(SPEC % ", ".join("`%s` (existing)" % f for f in files))
        return {"units": [{"id": "X1", "state": "OPEN", "sub_units": ["X1.a"], "spec": spec, "evidence": ""}]}

    def council_says(self, record):
        with open(self.council, "w", encoding="utf-8") as fh:
            json.dump(record, fh)

    def test_a_code_sub_unit_is_admitted(self):
        self.council_says({"X1": {"state": "FIX-FIRST", "blockers": [{"claim": "c", "proof": "p", "expect": "e"}]}})
        self.assertEqual(RP.admissible(self.plan(["scripts/x1.py", "scripts/test_x1.py"]), "X1.a"), "")

    def test_a_test_only_sub_unit_is_refused(self):
        why = RP.admissible(self.plan(["scripts/test_x1.py"]), "X1.a")
        self.assertIn("TEST-OR-DOC-ONLY", why)

    def test_a_doc_only_sub_unit_is_refused(self):
        why = RP.admissible(self.plan(["docs/guide.md", "products/brothermode/docs/PAGE.md"]), "X1.a")
        self.assertIn("TEST-OR-DOC-ONLY", why)

    def test_a_section_naming_no_file_is_left_to_the_spec_gates(self):
        self.assertEqual(RP.admissible(self.plan([]), "X1.a"), "")

    def test_a_proven_blocker_refuses(self):
        self.council_says({"X1": {"state": "FIX-FIRST", "blockers": [
            {"claim": "the done check names the wrong test", "proof": "p", "expect": "e", "state": "PROVEN"}]}})
        why = RP.admissible(self.plan(["scripts/x1.py"]), "X1.a")
        self.assertIn("PROVEN-BLOCKER", why)
        self.assertIn("the done check names the wrong test", why)

    def test_a_blocker_state_other_than_proven_does_not_refuse(self):
        self.council_says({"X1": {"blockers": [{"claim": "c", "state": "UNPROVEN"}, {"claim": "d"}]}})
        self.assertEqual(RP.admissible(self.plan(["scripts/x1.py"]), "X1.a"), "")

    def test_an_unreadable_record_refuses(self):
        with open(self.council, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        self.assertIn("NO-DATA", RP.admissible(self.plan(["scripts/x1.py"]), "X1.a"))

    def test_no_council_file_admits(self):
        self.assertFalse(os.path.exists(self.council))
        self.assertEqual(RP.admissible(self.plan(["scripts/x1.py"]), "X1.a"), "")


if __name__ == "__main__":
    unittest.main()
