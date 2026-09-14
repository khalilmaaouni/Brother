"""The ONE test named in TASK.md's prompt. Make this pass."""
import unittest

from util import chunk


class ChunkTest(unittest.TestCase):
    def test_chunk_ten_by_three(self):
        result = chunk(list(range(10)), 3)
        self.assertEqual([len(c) for c in result], [3, 3, 3, 1])
        self.assertEqual(result, [[0, 1, 2], [3, 4, 5], [6, 7, 8], [9]])


if __name__ == "__main__":
    unittest.main()
