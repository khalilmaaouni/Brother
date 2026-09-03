#!/usr/bin/env python3
"""Drive every branch of wisdom_capture with forced inputs.

Written this way on purpose. The night that produced this tool also produced two
watchdogs that were rewritten to be more careful and shipped a wrong count and
then a wrong label; both were found by DRIVING them and neither by reading them.
So each case below forces a state rather than trusting the happy path: a missing
field, a duplicate name, a second identical run, a hand written note in the way,
and an empty input.
"""
import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wisdom_capture import capture, main, render, validate, MARKER


def lesson(name="a-thing-went-wrong", **kw):
    base = {"name": name,
            "symptom": "the build passed and the screen was blank",
            "description": "A check answered a narrower question than the one it was trusted for.",
            "body": "What happened, and what to do instead."}
    base.update(kw)
    return base


class TestValidate(unittest.TestCase):

    def test_a_missing_symptom_is_refused_by_name(self):
        problems = validate([lesson(symptom="")])
        self.assertEqual(len(problems), 1)
        self.assertIn("symptom", problems[0])

    def test_a_whitespace_only_field_is_refused_like_an_empty_one(self):
        self.assertTrue(validate([lesson(description="   ")]))

    def test_duplicate_names_are_refused_because_one_would_overwrite_the_other(self):
        problems = validate([lesson(), lesson()])
        self.assertTrue(any("duplicate" in p for p in problems))

    def test_a_complete_lesson_passes(self):
        self.assertEqual(validate([lesson()]), [])


class TestRender(unittest.TestCase):

    def test_the_symptom_reaches_the_frontmatter_because_bake_reads_it(self):
        out = render(lesson(), "2026-08-29", "brother")
        self.assertIn("symptom: the build passed and the screen was blank", out)
        self.assertIn("type: failure", out)
        self.assertIn("created: 2026-08-29", out)

    def test_the_generator_marker_is_present_so_reruns_can_tell_its_own_notes(self):
        self.assertIn(MARKER, render(lesson(), "2026-08-29", "brother"))


class TestCapture(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        (self.root / "40-Failures").mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_second_identical_run_writes_nothing(self):
        first = capture([lesson()], self.root, "2026-08-29", "brother")
        second = capture([lesson()], self.root, "2026-08-29", "brother")
        self.assertEqual(first, [("written", "a-thing-went-wrong")])
        self.assertEqual(second, [("unchanged", "a-thing-went-wrong")])

    def test_an_edited_lesson_is_rewritten_rather_than_left_stale(self):
        capture([lesson()], self.root, "2026-08-29", "brother")
        out = capture([lesson(body="different words")], self.root, "2026-08-29", "brother")
        self.assertEqual(out, [("written", "a-thing-went-wrong")])

    def test_a_hand_written_note_is_refused_rather_than_clobbered(self):
        p = self.root / "40-Failures" / "a-thing-went-wrong.md"
        p.write_text("---\ntype: failure\n---\n\nsomebody wrote this by hand\n")
        out = capture([lesson()], self.root, "2026-08-29", "brother")
        self.assertEqual(out, [("REFUSED-hand-written", "a-thing-went-wrong")])
        self.assertIn("by hand", p.read_text())

    def test_force_overwrites_a_hand_written_note_only_when_asked(self):
        p = self.root / "40-Failures" / "a-thing-went-wrong.md"
        p.write_text("hand written\n")
        out = capture([lesson()], self.root, "2026-08-29", "brother", force=True)
        self.assertEqual(out, [("written", "a-thing-went-wrong")])
        self.assertIn(MARKER, p.read_text())


class TestNoDataIsNeverAPass(unittest.TestCase):

    def test_an_empty_lessons_file_exits_nonzero_rather_than_reporting_success(self):
        with tempfile.TemporaryDirectory() as d:
            src = pathlib.Path(d) / "lessons.json"
            src.write_text(json.dumps({"lessons": []}))
            code = main(["--lessons", str(src), "--created", "2026-08-29",
                         "--vault", d])
            self.assertEqual(code, 2)

    def test_a_missing_lessons_file_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as d:
            code = main(["--lessons", str(pathlib.Path(d) / "nope.json"),
                         "--created", "2026-08-29", "--vault", d])
            self.assertEqual(code, 2)

    def test_a_missing_vault_exits_nonzero_rather_than_creating_one(self):
        with tempfile.TemporaryDirectory() as d:
            src = pathlib.Path(d) / "lessons.json"
            src.write_text(json.dumps({"lessons": [lesson()]}))
            code = main(["--lessons", str(src), "--created", "2026-08-29",
                         "--vault", str(pathlib.Path(d) / "no-vault-here")])
            self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main(verbosity=1)
