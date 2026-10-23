#!/usr/bin/env python3
"""spec_intake.target: a draft is written where the plan says the unit's spec lives (2026-10-19).

Before this, spec_intake wrote docs/plan/specs/<id>.md whatever the plan said, so a 1.1.7 unit whose plan names
docs/plan/specs/1.1.7/<id>.md got a second spec nobody read. One case per condition; the last proves main() routes
through target() rather than a path of its own."""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import spec_intake  # noqa: E402


class TheSpecIsWrittenWhereThePlanSays(unittest.TestCase):
    def test_a_unit_naming_its_spec_is_written_there(self):
        self.assertEqual(spec_intake.target({"id": "CS12", "spec": "docs/plan/specs/1.1.7/CS12.md"}),
                         "docs/plan/specs/1.1.7/CS12.md")

    def test_a_unit_with_no_spec_gets_the_default(self):
        self.assertEqual(spec_intake.target({"id": "X1"}), "docs/plan/specs/X1.md")
        self.assertEqual(spec_intake.target({"id": "X1", "spec": ""}), "docs/plan/specs/X1.md")

    def test_a_spec_outside_the_spec_folder_is_refused(self):
        for bad in ("/tmp/X1.md", "docs/plan/specs/../X1.md", "docs/plan/X1.md", "docs/plan/specs/X1.txt",
                    "docs/plan/specs//X1.md", "./docs/plan/specs/X1.md", ["docs/plan/specs/X1.md"], 7):
            with self.subTest(spec=bad):
                with self.assertRaises(ValueError):
                    spec_intake.target({"id": "X1", "spec": bad})

    def test_a_unit_that_is_not_a_record_is_refused(self):
        for bad in (None, "X1", {"spec": "docs/plan/specs/X1.md"}, {"id": ""}, {"id": 3}):
            with self.subTest(unit=bad):
                with self.assertRaises(ValueError):
                    spec_intake.target(bad)

    def test_main_asks_target_for_the_path(self):
        tmp = tempfile.mkdtemp(prefix="spec-intake-target-")
        drafts = os.path.join(tmp, "drafts")
        os.makedirs(drafts)
        plan = os.path.join(tmp, "plan.json")
        with open(plan, "w", encoding="utf-8") as fh:
            json.dump({"units": [{"id": "Q1", "spec": "docs/plan/specs/1.1.7/Q1.md", "sub_units": [],
                                  "state": "OPEN"}]}, fh)
        with open(os.path.join(drafts, "Q1-spec-deepseek.md"), "w", encoding="utf-8") as fh:
            fh.write("### Q1.a one\n### Q1.b two\n### Q1.c three\n")
        saved = (spec_intake.PLAN, spec_intake.target)
        spec_intake.PLAN = plan

        def refusing(unit):
            raise ValueError("probe refusal for %s" % unit["id"])
        spec_intake.target = refusing
        out = io.StringIO()
        here = os.getcwd()
        os.chdir(tmp)   # a main() that ignored target() writes relative paths here, never into the checkout
        try:
            with contextlib.redirect_stdout(out):
                spec_intake.main(["spec_intake.py", drafts])
        finally:
            os.chdir(here)
            spec_intake.PLAN, spec_intake.target = saved
        self.assertIn("Q1    skipped (probe refusal for Q1)", out.getvalue())
        self.assertFalse(os.path.exists(os.path.join(tmp, "docs")))


if __name__ == "__main__":
    unittest.main()
