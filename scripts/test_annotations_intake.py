"""A correction stored once is shown at the next intake.

annotations_store.py can hold a correction, list it, and remove it, but
until this test nothing in a live intake ever read one back. This drives
decide.py's own render() against a temp store: one mark with a matching
stored correction must carry "corrected before" on the rendered page, and
an unmatched mark must not.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import annotations_store as AS  # noqa: E402
import decide as D  # noqa: E402

SPEC = {
    "title": "T",
    "criteria": [{"key": "tokens", "label": "Tokens", "weight": 0.5},
                 {"key": "turns", "label": "Turns", "weight": 0.5}],
    "options": [
        {"id": "A", "name": "Option A", "scores": {"tokens": 8, "turns": 6}},
    ],
}


class CorrectedBeforeLine(unittest.TestCase):
    def test_a_matched_mark_shows_corrected_before_and_carries_the_note(self):
        with tempfile.TemporaryDirectory() as d:
            AS.save_annotations(d, [
                {"id": "a1", "option": "A", "criterion": "tokens",
                 "note": "undercounts the drafter's own overhead",
                 "persona": "lead", "at": "2026-09-06T00:00:00Z",
                 "record": "r.json"},
            ])
            real_root = D.ROOT
            D.ROOT = d
            try:
                html = D.render(SPEC)
            finally:
                D.ROOT = real_root

            self.assertIn("corrected before", html)
            self.assertIn("undercounts the drafter&#x27;s own overhead", html)

    def test_an_unmatched_mark_carries_no_corrected_before_line(self):
        with tempfile.TemporaryDirectory() as d:
            AS.save_annotations(d, [
                {"id": "A", "option": "A", "criterion": "some-other-criterion",
                 "note": "irrelevant", "persona": "lead",
                 "at": "t", "record": "r.json"},
            ])
            real_root = D.ROOT
            D.ROOT = d
            try:
                html = D.render(SPEC)
            finally:
                D.ROOT = real_root
            self.assertNotIn("corrected before", html)

    def test_an_empty_store_renders_nothing_extra_and_never_errors(self):
        with tempfile.TemporaryDirectory() as d:
            real_root = D.ROOT
            D.ROOT = d
            try:
                html = D.render(SPEC)
            finally:
                D.ROOT = real_root
            self.assertNotIn("corrected before", html)


if __name__ == "__main__":
    unittest.main()
