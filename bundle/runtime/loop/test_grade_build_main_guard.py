#!/usr/bin/env python3
"""grade_build.apply refuses a Python file whose `if __name__ == "__main__":` block has a definition below it.

2026-09-28: scripts/supply_chain_gate.py carried its guard above five functions the gate calls, so every script run
died with NameError and printed NO-DATA, and its own test accepted exit 2. Each fixture below trips one condition only.
Run: python3 scripts/loop/test_grade_build_main_guard.py
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import grade_build as G  # noqa: E402

GUARD = 'if __name__ == "__main__":\n    main()\n'


class ApplyRefusesAGuardAboveADefinition(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name

    def existing(self, rel, text):
        with open(os.path.join(self.root, rel), "w", encoding="utf-8") as fh:
            fh.write(text)

    def applied(self, items):
        problems = []
        G.apply(self.root, items, problems, "edit")
        return problems

    def test_an_append_below_an_existing_guard_is_refused(self):
        self.existing("m.py", "def main():\n    pass\n\n\n" + GUARD)
        problems = self.applied([{"path": "m.py", "find": "    main()\n", "replace": "    main()\n\n\ndef late():\n    pass\n"}])
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("m.py defines something at line 9", problems[0])

    def test_a_new_file_with_its_guard_mid_file_is_refused(self):
        problems = self.applied([{"path": "n.py", "new_file_content": "def main():\n    pass\n\n\n" + GUARD + "\n\nX = 1\n"}])
        self.assertEqual(len(problems), 1, problems)

    def test_a_guard_at_the_end_passes(self):
        self.existing("m.py", "def main():\n    pass\n\n\n" + GUARD)
        self.assertEqual(self.applied([{"path": "m.py", "find": "def main():\n    pass\n",
                                        "replace": "def early():\n    pass\n\n\ndef main():\n    early()\n"}]), [])

    def test_a_file_with_no_guard_passes(self):
        self.assertEqual(self.applied([{"path": "lib.py", "new_file_content": "def a():\n    pass\n\n\nX = 1\n"}]), [])

    def test_two_items_on_one_file_give_one_refusal(self):
        self.existing("m.py", "def main():\n    pass\n\n\n" + GUARD + "\n\nY = 2\n")
        problems = self.applied([{"path": "m.py", "find": "Y = 2", "replace": "Y = 3"},
                                 {"path": "m.py", "find": "Y = 3", "replace": "Y = 4"}])
        self.assertEqual(len(problems), 1, problems)

    def test_a_non_python_file_is_not_parsed(self):
        self.assertEqual(self.applied([{"path": "notes.md", "new_file_content": GUARD + "\ndef x():\n    pass\n"}]), [])


if __name__ == "__main__":
    unittest.main()
