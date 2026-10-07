import json
import os
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from tools.jev_catalogue import grow


class GrowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.catalogue = os.path.join(self.tmp.name, "catalogue.md")
        self.ledger = os.path.join(self.tmp.name, "ledger.jsonl")

    def _write_catalogue(self, text):
        with open(self.catalogue, "wb") as f:
            f.write(text.encode("utf-8"))

    def _write_ledger(self, lines):
        with open(self.ledger, "wb") as f:
            for obj in lines:
                f.write((json.dumps(obj) + "\n").encode("utf-8"))

    def test_next_catalogue_id(self):
        self.assertEqual(grow.next_catalogue_id([{"id": 1}, {"id": 2}]), "3")
        self.assertEqual(grow.next_catalogue_id([]), "1")
        with self.assertRaises(ValueError):
            grow.next_catalogue_id(None)
        with self.assertRaises(ValueError):
            grow.next_catalogue_id("x")

    def test_append_blocks_duplicate(self):
        self._write_catalogue("### 1. one\n\nQuestion: `q1`\n\nLedger: `h1`\n")
        ok, reason = grow.append_entry(
            self.catalogue,
            {"type": "noul", "holder_id": "h1", "question_id": "q2", "title": "two"},
            self.ledger,
        )
        self.assertFalse(ok)
        self.assertIn("duplicate holder id", reason)
        ok, reason = grow.append_entry(
            self.catalogue,
            {"type": "noul", "holder_id": "h2", "question_id": "q1", "title": "two"},
            self.ledger,
        )
        self.assertFalse(ok)
        self.assertIn("duplicate question id", reason)

    def test_append_blocks_gate(self):
        self._write_catalogue("")
        ok, reason = grow.append_entry(
            self.catalogue,
            {"type": "score", "holder_id": "h1", "question_id": "q1"},
            self.ledger,
        )
        self.assertFalse(ok)
        self.assertIn("gate", reason)
        ok, reason = grow.append_entry(
            self.catalogue,
            {"type": "choice", "holder_id": "h1", "question_id": "q1"},
            self.ledger,
        )
        self.assertFalse(ok)
        self.assertIn("gate", reason)

    def test_append_requires_proof(self):
        self._write_catalogue("")
        ok, reason = grow.append_entry(
            self.catalogue,
            {"type": "noul", "holder_id": "h1", "question_id": "q1"},
            self.ledger,
        )
        self.assertFalse(ok)
        self.assertIn("ledger file missing", reason)

    def test_append_writes_when_valid(self):
        self._write_catalogue("### 1. one\n\nQuestion: `q0`\n\nLedger: `h0`\n")
        self._write_ledger([
            {"type": "RESERVE", "holder_id": "h0", "reservation_id": "r0"},
            {"type": "RECONCILE", "reservation_id": "r0"},
            {"type": "RESERVE", "holder_id": "h1", "reservation_id": "r1"},
            {"type": "RECONCILE", "reservation_id": "r1"},
        ])
        ok, reason = grow.append_entry(
            self.catalogue,
            {"type": "noul", "holder_id": "h1", "question_id": "q1", "title": "two"},
            self.ledger,
        )
        self.assertTrue(ok, reason)
        with open(self.catalogue, "rb") as f:
            text = f.read().decode("utf-8")
        self.assertIn("### 2. two", text)
        self.assertIn("Question: `q1`", text)
        self.assertIn("Ledger: `h1`", text)

    def test_build_health_counts(self):
        self._write_catalogue(
            "### 1. one\n\nQuestion: `q0`\n\nLedger: `h0`\n\n"
            "### 2. two\n\nQuestion: `q1`\n\nLedger: `h1`\n"
        )
        self._write_ledger([
            {"type": "RESERVE", "holder_id": "h0", "reservation_id": "r0"},
            {"type": "RECONCILE", "reservation_id": "r0"},
        ])
        health = grow.build_health(self.catalogue, self.ledger)
        self.assertEqual(health["total"], 2)
        self.assertEqual(health["proven"], 1)
        self.assertEqual(health["unproven"], 1)

    def test_check_done(self):
        self.assertEqual(grow.check_done({"total": 70, "proven": 70}), (True, "done: 70 or more entries, all proven"))
        self.assertEqual(grow.check_done({"total": 10, "proven": 10}), (True, "seed phase: 10 of 10 proven"))
        self.assertFalse(grow.check_done({"total": 70, "proven": 69})[0])
        self.assertFalse(grow.check_done({"total": 9, "proven": 9})[0])
        with self.assertRaises(ValueError):
            grow.check_done(None)

    def test_hostile_inputs(self):
        with self.assertRaises(ValueError):
            grow.build_health(None, self.ledger)
        with self.assertRaises(ValueError):
            grow.build_health(self.catalogue, None)
        ok, reason = grow.append_entry(None, {}, None)
        self.assertFalse(ok)
        ok, reason = grow.append_entry(self.catalogue, {"type": "noul", "holder_id": None, "question_id": "q"}, self.ledger)
        self.assertFalse(ok)
        with self.assertRaises(ValueError):
            grow.next_catalogue_id([{"id": float("nan")}])


if __name__ == "__main__":
    unittest.main()
