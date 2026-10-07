#!/usr/bin/env python3
"""FX-13.2: the NEW-EXISTS and SCREEN rules of plan_lint, exercised on a section body built in a temp tree."""
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import plan_lint  # noqa: E402  (the module under test, beside this file)


def _unit(runners=None):
    unit = {"id": "U", "state": "OPEN", "done_check": "python3 -B scripts/test_a.py",
            "spec": "spec.md", "sub_units": ["U.1"], "evidence": ""}
    if runners is not None:
        unit["command_runners"] = runners
    return unit


def _touch(root, rel):
    path = os.path.join(root, rel)
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("# placeholder\n")


def _rules(section, root=".", unit=None):
    findings = plan_lint._section_new_and_screen("U", "U.1", unit if unit is not None else _unit(), section, root)
    return [f["rule"] for f in findings]


class NewExistsTest(unittest.TestCase):
    """A path labelled NEW that already exists is a blocking finding, in every spelling."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name

    def test_new_on_existing_path_is_flagged(self):
        _touch(self.root, "scripts/exists.py")
        self.assertIn("NEW-EXISTS", _rules("#### U.1\n\nOwns: `scripts/exists.py` NEW\n", self.root))

    def test_new_on_absent_path_is_not_flagged(self):
        self.assertNotIn("NEW-EXISTS", _rules("#### U.1\n\nOwns: `scripts/absent.py` NEW\n", self.root))

    def test_new_inside_backticks_on_existing_path_is_flagged(self):
        _touch(self.root, "scripts/here.py")
        self.assertIn("NEW-EXISTS", _rules("#### U.1\n\nOwns: `NEW: scripts/here.py`\n", self.root))

    def test_new_parenthesised_on_existing_path_is_flagged(self):
        _touch(self.root, "scripts/par.py")
        self.assertIn("NEW-EXISTS", _rules("#### U.1\n\nOwns: `scripts/par.py` (NEW)\n", self.root))

    def test_new_before_path_on_existing_path_is_flagged(self):
        _touch(self.root, "scripts/pre.py")
        self.assertIn("NEW-EXISTS", _rules("#### U.1\n\nOwns: NEW `scripts/pre.py`\n", self.root))

    def test_existing_label_on_an_existing_path_is_not_flagged(self):
        _touch(self.root, "scripts/old.py")
        self.assertNotIn("NEW-EXISTS", _rules("#### U.1\n\nUses `scripts/old.py` (existing)\n", self.root))


class ScreenTest(unittest.TestCase):
    """A fenced python block the grader refuses is a blocking finding; the command runner list is honoured."""

    def test_http_client_fence_is_flagged(self):
        section = "#### U.1\n\nFiles: `scripts/foo.py`\n\n```python\nimport http.client\n```\n"
        self.assertIn("SCREEN", _rules(section))

    def test_clean_fence_is_not_flagged(self):
        section = "#### U.1\n\nFiles: `scripts/foo.py`\n\n```python\nimport os\nprint(os.getpid())\n```\n"
        self.assertNotIn("SCREEN", _rules(section))

    def test_command_runner_path_is_screened_like_the_grader(self):
        section = "#### U.1\n\nFiles: `scripts/runner.py`\n\n```python\nimport subprocess\n```\n"
        self.assertIn("SCREEN", _rules(section))
        self.assertNotIn("SCREEN", _rules(section, unit=_unit(["scripts/runner.py"])))

    def test_backticked_forbidden_import_is_advisory(self):
        section = "#### U.1\n\nThe grader forbids `import subprocess` in a build.\n"
        findings = plan_lint._section_new_and_screen("U", "U.1", _unit(), section, ".")
        screen = [f for f in findings if f["rule"] == "SCREEN"]
        self.assertTrue(screen)
        self.assertTrue(all(f["severity"] == "ADVISORY" for f in screen))


class SectionLookupTest(unittest.TestCase):
    """The body of the named section is isolated; the sub id stands as a whole token."""

    def test_heading_match_isolates_the_body(self):
        text = "### 1. Top\n\nintro\n\n#### U.1\n\nOwns: `scripts/absent.py` NEW\n\n#### U.2\n\nother\n"
        body = plan_lint._section_by_sub(text, "U.1")
        self.assertIsInstance(body, str)
        self.assertIn("absent.py", body)
        self.assertNotIn("other", body)

    def test_sub_id_is_a_whole_token(self):
        self.assertIsNone(plan_lint._section_by_sub("#### U.10\n\nbody one\n", "U.1"))

    def test_no_section_is_none(self):
        self.assertIsNone(plan_lint._section_by_sub("#### X.9\n\nbody\n", "U.1"))


class RefusalTest(unittest.TestCase):
    """Hostile input is refused with the module's own refusal value, never a raw interpreter exception."""

    def test_hostile_section_and_unit_are_refused(self):
        unit = _unit()
        for bad in (None, 5, b"x", "", []):
            self.assertEqual(plan_lint._section_new_and_screen("U", "U.1", unit, bad, "."), [])
        self.assertEqual(plan_lint._section_new_and_screen("U", "U.1", None, "x", "."), [])
        self.assertEqual(plan_lint._section_new_and_screen("U", "U.1", [], "x", "."), [])
        self.assertEqual(plan_lint._section_new_and_screen("U", "U.1", "x", "x", "."), [])
        self.assertEqual(plan_lint._section_new_and_screen(None, None, unit, "x", "."), [])

    def test_hostile_text_is_refused(self):
        self.assertIsNone(plan_lint._section_by_sub(None, "U.1"))
        self.assertIsNone(plan_lint._section_by_sub("x", None))
        self.assertIsNone(plan_lint._section_by_sub("x", ""))
        self.assertIsNone(plan_lint._section_by_sub(5, "U.1"))
        self.assertIsNone(plan_lint._section_by_sub(b"x", "U.1"))
        self.assertIsNone(plan_lint._section_by_sub("x", 5))


if __name__ == "__main__":
    unittest.main()
