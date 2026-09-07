"""guarded_strip must never delete without counting, and never on a mismatch.

Row M1 of the 2026-09-07 reflection: a cleanup filter meant for 7 rows
dropped 1460. The property under test is not "it deletes rows"; it is that
a wrong --expect deletes nothing, and a right --expect deletes exactly the
matched rows and leaves a byte-identical backup behind.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import guarded_strip as G  # noqa: E402

try:
    import tmp_sandbox
    tmp_sandbox.install()
except ImportError:
    sys.stderr.write("tmp_sandbox absent: %s leaves its temp trees behind\n"
                      % os.path.basename(__file__))

import tempfile  # noqa: E402


FIXTURE = (
    '{"kind": "residue", "n": 1}\n'
    '{"kind": "keep", "n": 2}\n'
    'not json at all\n'
    '{"kind": "residue", "n": 3}\n'
    '{"kind": "keep", "n": 4}\n'
)


def write_fixture(dirpath):
    path = os.path.join(dirpath, "log.jsonl")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(FIXTURE)
    return path


class SelectRows(unittest.TestCase):
    def test_match_selects_only_hits_and_reports_malformed_kept(self):
        lines = FIXTURE.splitlines(keepends=True)
        selected, malformed = G.select_rows(lines, match_expr="row.get('kind') == 'residue'")
        self.assertEqual([i for i, _ in selected], [1, 4])
        self.assertEqual(malformed, [3])

    def test_line_range_is_inclusive_and_ignores_json(self):
        lines = FIXTURE.splitlines(keepends=True)
        selected, malformed = G.select_rows(lines, line_range=(2, 3))
        self.assertEqual([i for i, _ in selected], [2, 3])
        self.assertEqual(malformed, [])

    def test_match_expression_error_on_wellformed_row_raises_usage(self):
        lines = FIXTURE.splitlines(keepends=True)
        with self.assertRaises(G.Usage):
            G.select_rows(lines, match_expr="row['does_not_exist']")


class CountMismatchDeletesNothing(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = write_fixture(self.tmp)

    def test_wrong_expect_refuses_and_writes_nothing(self):
        with open(self.path, encoding="utf-8") as fh:
            before = fh.read()
        code = G.main([
            "--file", self.path, "--match", "row.get('kind') == 'residue'",
            "--expect", "7", "--apply",
        ])
        self.assertEqual(code, 3)
        with open(self.path, encoding="utf-8") as fh:
            after = fh.read()
        self.assertEqual(before, after)
        # no backup either: a refusal never gets far enough to make one
        self.assertEqual(
            [n for n in os.listdir(self.tmp) if n != "log.jsonl"], [])


class ExactMatchApplies(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = write_fixture(self.tmp)

    def test_apply_removes_exactly_the_matched_rows_and_backs_up(self):
        with open(self.path, encoding="utf-8") as fh:
            original = fh.read()
        code = G.main([
            "--file", self.path, "--match", "row.get('kind') == 'residue'",
            "--expect", "2", "--apply",
        ])
        self.assertEqual(code, 0)

        with open(self.path, encoding="utf-8") as fh:
            remaining = fh.read()
        self.assertNotIn('"n": 1', remaining)
        self.assertNotIn('"n": 3', remaining)
        self.assertIn('"n": 2', remaining)
        self.assertIn('"n": 4', remaining)
        self.assertIn("not json at all", remaining)  # malformed line kept

        backups = [n for n in os.listdir(self.tmp) if n != "log.jsonl"]
        self.assertEqual(len(backups), 1)
        with open(os.path.join(self.tmp, backups[0]), encoding="utf-8") as fh:
            backup_content = fh.read()
        self.assertEqual(backup_content, original)

    def test_dry_run_without_apply_never_writes(self):
        with open(self.path, encoding="utf-8") as fh:
            before = fh.read()
        code = G.main([
            "--file", self.path, "--match", "row.get('kind') == 'residue'",
            "--expect", "2",
        ])
        self.assertEqual(code, 0)
        with open(self.path, encoding="utf-8") as fh:
            after = fh.read()
        self.assertEqual(before, after)
        self.assertEqual(
            [n for n in os.listdir(self.tmp) if n != "log.jsonl"], [])


if __name__ == "__main__":
    unittest.main()
