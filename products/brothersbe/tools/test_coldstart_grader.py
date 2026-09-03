import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import sbe_score


class ColdstartGrader(unittest.TestCase):
    """The receipt's three states stay distinguishable, and an absent
    measurement never reads as a passing one."""

    def _ctx_over(self, lines):
        """A Ctx whose coldstart receipt is the given list of row dicts.

        None means the file does not exist at all.
        """
        d = tempfile.mkdtemp()
        path = os.path.join(d, "coldstart.jsonl")
        if lines is not None:
            with open(path, "w") as fh:
                for row in lines:
                    fh.write(json.dumps(row) + "\n")
        saved = sbe_score.COLDSTART
        sbe_score.COLDSTART = path
        self.addCleanup(setattr, sbe_score, "COLDSTART", saved)
        return sbe_score.Ctx()

    def test_absent_receipt_is_no_data(self):
        verdict, evidence = sbe_score.check_coldstart_receipt(self._ctx_over(None))
        self.assertEqual(verdict, "NO-DATA")
        self.assertIn("coldstart", evidence)

    def test_receipt_with_no_rows_is_no_data_and_says_so(self):
        verdict, evidence = sbe_score.check_coldstart_receipt(self._ctx_over([]))
        self.assertEqual(verdict, "NO-DATA")
        self.assertIn("zero", evidence)

    def test_batch_whose_runs_all_failed_is_no_data(self):
        row = {"head_sha": "a" * 40, "proxy": "model-as-beginner",
               "measured_at": "2026-08-10T00:00:00Z", "complete": True, "runs": []}
        verdict, _ = sbe_score.check_coldstart_receipt(self._ctx_over([row]))
        self.assertEqual(verdict, "NO-DATA")

    def test_missing_proxy_label_is_a_fail(self):
        row = {"head_sha": "a" * 40, "measured_at": "2026-08-10T00:00:00Z",
               "complete": True, "runs": [{"completed": True}]}
        verdict, evidence = sbe_score.check_coldstart_receipt(self._ctx_over([row]))
        self.assertEqual(verdict, "FAIL")
        self.assertIn("proxy", evidence)

    def test_incomplete_batch_is_no_data(self):
        row = {"head_sha": "a" * 40, "proxy": "model-as-beginner",
               "measured_at": "2026-08-10T00:00:00Z", "complete": False,
               "runs": [{"completed": True}]}
        verdict, _ = sbe_score.check_coldstart_receipt(self._ctx_over([row]))
        self.assertEqual(verdict, "NO-DATA")

    def test_thresholds_undeclared_is_no_data_not_pass(self):
        row = {"head_sha": "a" * 40, "proxy": "model-as-beginner",
               "measured_at": "2026-08-10T00:00:00Z", "complete": True,
               "runs": [{"completed": True, "commands_typed": 3}]}
        ctx = self._ctx_over([row])
        saved = sbe_score.COLDSTART_THRESHOLDS
        sbe_score.COLDSTART_THRESHOLDS = {}
        self.addCleanup(setattr, sbe_score, "COLDSTART_THRESHOLDS", saved)
        verdict, evidence = sbe_score.check_coldstart_thresholds(ctx)
        self.assertEqual(verdict, "NO-DATA")
        self.assertIn("threshold", evidence)


if __name__ == "__main__":
    unittest.main()
