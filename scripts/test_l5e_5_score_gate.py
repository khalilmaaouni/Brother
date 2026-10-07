"""L5e.5 score computation and gate tests.

Hermetic: every synthetic document is built inside the test itself and
the live audit document is only read behind a skip condition.
"""

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from l5e_5_score_gate import score_audit_doc

_AUDIT_DOC_PATH = os.path.join(
    os.path.dirname(_HERE), "docs", "architecture",
    "L5E-DOCS-ACCURACY-AUDIT.md")

_REQUIRED_SECTIONS = (
    "SYSTEM.md accuracy",
    "PARITY-MATRIX accuracy",
    "README install accuracy",
)


def _doc(rows):
    lines = ["| Area | Weight | Score |", "| --- | --- | --- |"]
    for area, weight, score in rows:
        lines.append("| %s | %s | %s |" % (area, weight, score))
    return "\n".join(lines) + "\n"


class ScoreGateTest(unittest.TestCase):

    def _read_audit_doc(self):
        with open(_AUDIT_DOC_PATH, "rb") as handle:
            return handle.read().decode("utf-8")

    @unittest.skipUnless(
        os.path.isfile(_AUDIT_DOC_PATH),
        "audit document is not present")
    def test_score_gate(self):
        """Assert score_audit_doc returns >= 8.5 and all sections are present."""
        doc = self._read_audit_doc()
        for section in _REQUIRED_SECTIONS:
            self.assertIn(section, doc)
        score = score_audit_doc(doc)
        self.assertGreaterEqual(score, 8.5)
        self.assertLessEqual(score, 10.0)

    def test_score_gate_exact_boundary_passes(self):
        doc = _doc([("A", "0.5", "8.5"), ("B", "0.5", "8.5")])
        self.assertEqual(score_audit_doc(doc), 8.5)

    def test_score_gate_rejects_low_average(self):
        doc = _doc([("A", "0.5", "5.0"), ("B", "0.5", "10.0")])
        with self.assertRaises(ValueError):
            score_audit_doc(doc)

    def test_score_gate_rejects_missing_table(self):
        with self.assertRaises(ValueError):
            score_audit_doc("no table here\n")
        with self.assertRaises(ValueError):
            score_audit_doc("")

    def test_score_gate_rejects_non_numeric(self):
        doc = _doc([("A", "0.5", "nope"), ("B", "0.5", "10.0")])
        with self.assertRaises(ValueError):
            score_audit_doc(doc)

    def test_score_gate_rejects_out_of_range(self):
        negative = _doc([("A", "0.5", "-1.0"), ("B", "0.5", "10.0")])
        with self.assertRaises(ValueError):
            score_audit_doc(negative)
        over = _doc([("A", "0.5", "11.0"), ("B", "0.5", "10.0")])
        with self.assertRaises(ValueError):
            score_audit_doc(over)

    def test_score_gate_rejects_nan_and_infinite(self):
        nan_doc = _doc([("A", "0.5", "nan"), ("B", "0.5", "10.0")])
        with self.assertRaises(ValueError):
            score_audit_doc(nan_doc)
        inf_doc = _doc([("A", "0.5", "inf"), ("B", "0.5", "10.0")])
        with self.assertRaises(ValueError):
            score_audit_doc(inf_doc)

    def test_score_gate_rejects_hostile_input(self):
        for bad in (None, 123, 1.5, True, float("nan"), b"table",
                    bytearray(b"table"), ["| Area |"], {"Area": 1},
                    set(), (1, 2)):
            self.assertNotIsInstance(bad, str)
            with self.assertRaises(ValueError):
                score_audit_doc(bad)

    def test_score_gate_rejects_zero_total_weight(self):
        doc = _doc([("A", "0.0", "10.0"), ("B", "0.0", "10.0")])
        with self.assertRaises(ValueError):
            score_audit_doc(doc)

    def test_score_gate_rejects_negative_weight(self):
        doc = _doc([("A", "-0.5", "10.0"), ("B", "0.5", "10.0")])
        with self.assertRaises(ValueError):
            score_audit_doc(doc)

    def test_score_gate_rejects_oversized_doc(self):
        big = "| Area | Weight | Score |\n| --- | --- | --- |\n"
        big += "| padding | 0.0001 | 10.0 |\n" * 40000
        self.assertGreater(len(big.encode("utf-8")), 512 * 1024)
        with self.assertRaises(ValueError):
            score_audit_doc(big)


if __name__ == "__main__":
    unittest.main()
