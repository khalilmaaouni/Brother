"""The ONE failing test named in TASK.md's prompt. Make this pass."""
import unittest

from order_total import compute_total


class ComputeTotalTest(unittest.TestCase):
    def test_applies_percentage_discount(self):
        # 50 with 20 percent off is 40.0, not 50 - 20 = 30.
        self.assertEqual(compute_total(50, 20), 40.0)


if __name__ == "__main__":
    unittest.main()
