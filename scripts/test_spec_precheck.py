"""ORCH-37 calibration.

THE BAD STATE A GREEN CHECK WOULD ALSO PASS: a precheck that reports NEW
because it could not read the tree or the battery, which is exactly how the
error it exists to prevent happened (nobody looked).
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import spec_precheck as P  # noqa: E402

BATTERY = '''#!/bin/bash
run_check "mutation-gate-self" python3 scripts/test_mutation_gate.py -v
run_check "mutation-gate"      python3 scripts/mutation_gate.py
'''


class SpecPrecheck(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tree = self.tmp.name
        os.makedirs(os.path.join(self.tree, "scripts"))
        with open(os.path.join(self.tree, "scripts", "check_all.sh"), "w") as fh:
            fh.write(BATTERY)
        self.plan = os.path.join(self.tree, "wbs.json")

    def tearDown(self):
        self.tmp.cleanup()

    def write_plan(self, units):
        with open(self.plan, "w") as fh:
            json.dump({"units": units}, fh)
        return ["--plan", self.plan, "--tree", self.tree]

    def touch(self, rel):
        path = os.path.join(self.tree, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write("# already here\n")

    def test_a_row_naming_a_file_that_exists_is_modify(self):
        self.touch("scripts/already.py")
        args = self.write_plan([{"id": "R1", "owns": ["scripts/already.py"]}])
        self.assertEqual(P.main(args), 1)

    def test_a_row_whose_check_is_registered_is_modify_even_if_the_file_is_gone(self):
        """The real ORCH-19 shape: the battery already decides this row."""
        args = self.write_plan([{"id": "R2", "owns": ["scripts/mutation_gate.py"]}])
        self.assertEqual(P.main(args), 1)

    def test_a_genuinely_new_row_is_new(self):
        args = self.write_plan([{"id": "R3", "owns": ["scripts/brand_new.py"]}])
        self.assertEqual(P.main(args), 0)

    def test_rows_without_owns_are_not_counted(self):
        args = self.write_plan([{"id": "R4"}])
        self.assertEqual(P.main(args), 2, "nothing checked must be NO-DATA, never NEW")

    def test_an_unreadable_plan_is_no_data(self):
        self.assertEqual(P.main(["--plan", os.path.join(self.tree, "gone.json"),
                                 "--tree", self.tree]), 2)

    def test_an_unreadable_battery_is_no_data_not_new(self):
        args = self.write_plan([{"id": "R5", "owns": ["scripts/brand_new.py"]}])
        self.assertEqual(P.main(args + ["--battery", "scripts/missing.sh"]), 2)

    def test_only_the_named_row_is_checked(self):
        self.touch("scripts/already.py")
        args = self.write_plan([{"id": "R1", "owns": ["scripts/already.py"]},
                                {"id": "R3", "owns": ["scripts/brand_new.py"]}])
        self.assertEqual(P.main(args + ["--row", "R3"]), 0)
        self.assertEqual(P.main(args + ["--row", "R1"]), 1)

    def test_the_reason_names_the_existing_file_and_the_registered_check(self):
        checks = P.registered_checks(BATTERY)
        verdict, reasons = P.check_row({"id": "R2", "owns": ["scripts/mutation_gate.py"]},
                                       self.tree, checks)
        self.assertEqual(verdict, "MODIFY")
        self.assertTrue(any("mutation-gate" in r for r in reasons), reasons)

    def test_a_home_relative_owns_path_is_resolved(self):
        verdict, _ = P.check_row({"id": "R6", "owns": ["~/.claude/hooks/command_text.py"]},
                                 self.tree, {})
        self.assertEqual(verdict, "MODIFY", "a hook that exists is not new work")


if __name__ == "__main__":
    unittest.main()
