"""HIDDEN: not named in TASK.md's prompt to the competitor. Covers the
two boundary cases a fix aimed only at the visible test's 10-by-3 case
can miss: an empty list, and a length that is an exact multiple of the
chunk size. A fix that just bumps the naive loop bound by one (rather
than deriving the chunk count from the actual remainder) passes the
visible test but produces a spurious extra chunk on both cases here.
"""
import unittest

from export import export_batches


class ExportBoundariesTest(unittest.TestCase):
    def test_empty_list_yields_no_chunks(self):
        self.assertEqual(export_batches([], 3), [])

    def test_exact_multiple_yields_no_spurious_trailing_chunk(self):
        self.assertEqual(
            export_batches(list(range(9)), 3),
            [[0, 1, 2], [3, 4, 5], [6, 7, 8]],
        )


if __name__ == "__main__":
    unittest.main()
