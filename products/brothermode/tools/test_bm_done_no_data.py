#!/usr/bin/env python3
"""BR-16 calibration. Run: python3 tools/test_bm_done_no_data.py

WHY THIS SUITE EXISTS
  The definition-of-done review reported every checklist point as pass or
  not-yet, a two-state vocabulary, inside a product whose first law is that
  absent evidence is never a pass. A point whose evidence could not be read
  (the check did not run, the file was unreadable, the command errored) has
  no honest answer in that vocabulary, and not-yet misreports it as work
  remaining when the truth is that nobody can tell.

  This suite checks the three prose surfaces that state the review verdict
  vocabulary and requires each to name a third state, NO-DATA, define when
  it applies, and state that it is never a pass and never a block on its
  own.

Standard library only. Python 3.9. Reads files, writes none.
No em or en dashes anywhere in this file or its output.
"""
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# The three prose surfaces that state the pass / not-yet / NO-DATA
# vocabulary for a definition-of-done review. BR-16 names exactly these.
TARGETS = (
    os.path.join("references", "definition-of-done.md"),
    os.path.join("skills", "review", "SKILL.md"),
    os.path.join("commands", "brotherme-review.md"),
)


def read(rel):
    with open(os.path.join(ROOT, rel), "r", encoding="utf-8") as fh:
        return fh.read()


class TestThirdVerdictStateIsNamed(unittest.TestCase):
    """A point whose evidence cannot be read has no honest answer in a
    two-state vocabulary. Each surface must name all three states, define
    when NO-DATA applies, and say it is never a pass or a block on its
    own."""

    def test_each_surface_names_all_three_verdict_states(self):
        offenders = []
        for rel in TARGETS:
            text = read(rel)
            missing = [word for word in ("pass", "not-yet", "NO-DATA")
                       if word not in text]
            if missing:
                offenders.append("%s is missing %s"
                                  % (rel, ", ".join(missing)))
        self.assertEqual(
            offenders, [],
            "a review-vocabulary surface does not name all three verdict "
            "states (pass, not-yet, NO-DATA). Found: %s"
            % "; ".join(offenders))

    def test_each_surface_defines_when_no_data_applies(self):
        offenders = []
        for rel in TARGETS:
            text = read(rel)
            if "NO-DATA" not in text:
                offenders.append(rel)
                continue
            if "could not be read" not in text or "did not run" not in text:
                offenders.append(rel)
        self.assertEqual(
            offenders, [],
            "a surface names NO-DATA without saying when it applies (the "
            "evidence could not be read or the check did not run). "
            "Found: %s" % ", ".join(offenders))

    def test_each_surface_states_no_data_is_not_a_verdict_on_its_own(self):
        offenders = []
        for rel in TARGETS:
            text = read(rel)
            if "never a pass" not in text or "next action" not in text:
                offenders.append(rel)
        self.assertEqual(
            offenders, [],
            "a surface does not state that NO-DATA is never a pass and "
            "never a block on its own, naming what could not be read and "
            "the next action. Found: %s" % ", ".join(offenders))

    def test_calibrated_removing_no_data_is_caught(self):
        """The vacuous-pass guard. The first check above would pass just
        as happily if it never actually looked for NO-DATA. This strips
        every occurrence of NO-DATA out of each file's text and requires
        the same check to fail on the stripped copy."""
        for rel in TARGETS:
            text = read(rel)
            self.assertIn("NO-DATA", text,
                          "%s does not carry NO-DATA to strip; the "
                          "calibration proves nothing" % rel)
            stripped = text.replace("NO-DATA", "")
            self.assertNotEqual(stripped, text,
                               "stripping NO-DATA changed nothing, so it "
                               "proves nothing about the check above")
            missing = [word for word in ("pass", "not-yet", "NO-DATA")
                       if word not in stripped]
            self.assertIn("NO-DATA", missing,
                         "%s still names NO-DATA after every occurrence "
                         "was stripped" % rel)


if __name__ == "__main__":
    unittest.main(verbosity=1)
