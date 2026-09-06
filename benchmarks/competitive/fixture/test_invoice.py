"""HIDDEN test: not named in TASK.md's prompt to the competitor. Scored
by scripts/competitive_score.py's "tests" dimension regardless."""
import unittest

from invoice import compute_invoice_total


class ComputeInvoiceTotalTest(unittest.TestCase):
    def test_applies_percentage_discount(self):
        # Same bug, second call site: catches a fix that patches the
        # caller instead of pricing.apply_discount.
        self.assertEqual(compute_invoice_total(50, 20), 40.0)


if __name__ == "__main__":
    unittest.main()
