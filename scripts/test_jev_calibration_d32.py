#!/usr/bin/env python3
"""D3.2 terminal phase row tests. Live beside scripts/jev_calibration.py."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import jev_calibration as cal


class TestTerminalPhaseRow(unittest.TestCase):
    def test_terminal_phases_pinned(self):
        self.assertIsInstance(cal.TERMINAL_PHASES, frozenset)
        self.assertIn("answered", cal.TERMINAL_PHASES)
        self.assertIn("no_data", cal.TERMINAL_PHASES)
        self.assertIn("quarantined", cal.TERMINAL_PHASES)

    def _valid(self, **overrides):
        rec = {
            "schema": "attempt/v1",
            "phase": "answered",
            "attempt_id": "0123456789abcdef0123456789abcdef",
            "parent_id": None,
            "entry_id": "entry-1",
            "mode": "shadow",
            "at": "2026-09-20T00:00:00Z",
            "family": "entry-1",
            "qtype": "noul",
            "framing": "0123456789abcdef",
            "model": "model-a",
            "answer": 0.8,
            "prob": 0.8,
            "confidence": 0.8,
            "cost": 0.0,
            "reason": None,
            "audit": False,
        }
        rec.update(overrides)
        return rec

    def test_append_terminal_writes_valid_row(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "attempts.jsonl")
            cal.append_terminal(path, self._valid())
            with open(path, "rb") as fh:
                rows = [json.loads(line.decode("utf-8")) for line in fh if line.strip()]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["phase"], "answered")

    def test_append_terminal_rejects_missing_phase(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "attempts.jsonl")
            with self.assertRaises(ValueError):
                cal.append_terminal(path, self._valid(phase="not-a-phase"))

    def test_append_terminal_rejects_bad_attempt_id(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "attempts.jsonl")
            with self.assertRaises(ValueError):
                cal.append_terminal(path, self._valid(attempt_id="short"))

    def test_append_terminal_rejects_nan_prob(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "attempts.jsonl")
            with self.assertRaises(ValueError):
                cal.append_terminal(path, self._valid(prob=float("nan")))

    def test_append_terminal_rejects_bool_prob(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "attempts.jsonl")
            with self.assertRaises(ValueError):
                cal.append_terminal(path, self._valid(prob=True))

    def test_append_terminal_rejects_bool_cost(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "attempts.jsonl")
            with self.assertRaises(ValueError):
                cal.append_terminal(path, self._valid(cost=True))

    def test_append_terminal_rejects_none_record(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "attempts.jsonl")
            with self.assertRaises(ValueError):
                cal.append_terminal(path, None)

    def test_append_terminal_rejects_list_record(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "attempts.jsonl")
            with self.assertRaises(ValueError):
                cal.append_terminal(path, [])

    def test_append_terminal_rejects_bytes_answer(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "attempts.jsonl")
            with self.assertRaises(ValueError):
                cal.append_terminal(path, self._valid(answer=b"not-utf8"))

    def test_append_terminal_accepts_none_qtype_and_framing(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "attempts.jsonl")
            cal.append_terminal(path, self._valid(qtype=None, framing=None))

    def test_append_terminal_rejects_bad_framing(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "attempts.jsonl")
            with self.assertRaises(ValueError):
                cal.append_terminal(path, self._valid(framing="xyz"))

    def test_append_terminal_rejects_qtype_list(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "attempts.jsonl")
            with self.assertRaises(ValueError):
                cal.append_terminal(path, self._valid(qtype=[]))

    def test_append_terminal_rejects_phase_list(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "attempts.jsonl")
            with self.assertRaises(ValueError):
                cal.append_terminal(path, self._valid(phase=[]))

    def test_append_terminal_rejects_path_none(self):
        rec = self._valid()
        with self.assertRaises(ValueError):
            cal.append_terminal(None, rec)

    def test_append_terminal_rejects_path_directory(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ValueError):
                cal.append_terminal(td, self._valid())


    def test_append_terminal_refuses_duplicate_terminal(self):
        # D3 section 5: exactly one terminal row per attempt_id. The second
        # write for the same id is refused and the ledger keeps one line.
        # The guard is process local and read free (off mode calls are
        # pinned to cost only their appends, test_jev_checks).
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "attempts.jsonl")
            cal.append_terminal(path, self._valid())
            with self.assertRaises(ValueError):
                cal.append_terminal(path, self._valid(phase="no_data"))
            with open(path, encoding="utf-8") as fh:
                self.assertEqual(len([l for l in fh if l.strip()]), 1)

    def test_append_terminal_allows_a_second_attempt_id(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "attempts.jsonl")
            cal.append_terminal(path, self._valid())
            cal.append_terminal(path, self._valid(attempt_id="f" * 32))
            with open(path, encoding="utf-8") as fh:
                self.assertEqual(len([l for l in fh if l.strip()]), 2)

    def test_append_terminal_allows_terminal_after_its_own_submission_row(self):
        # A submission row for the same attempt_id is the normal predecessor,
        # never a duplicate terminal.
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "attempts.jsonl")
            cal.append_submission(path, {
                "schema": "attempt/v1", "phase": "submitted",
                "attempt_id": "0123456789abcdef0123456789abcdef", "parent_id": None,
                "entry_id": "entry-1", "qtype": "noul", "framing": "0123456789abcdef",
                "model": "model-a", "mode": "shadow", "at": "2026-09-20T00:00:00Z",
            })
            cal.append_terminal(path, self._valid())
            with open(path, encoding="utf-8") as fh:
                self.assertEqual(len([l for l in fh if l.strip()]), 2)

    def test_terminal_decision_id_accepts_the_minted_shape_and_refuses_junk(self):
        # jev_seam._decision_id mints "<entry_id>:<16 hex>:<16 hex>"; the
        # legacy 32 hex fixture shape stays readable; free text is refused.
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "attempts.jsonl")
            minted = "entry-1:" + "a" * 16 + ":" + "b" * 16
            cal.append_terminal(path, self._valid(decision_id=minted))
            cal.append_terminal(path, self._valid(attempt_id="1" * 32, decision_id="d" * 32))
            for junk in ("not-a-decision-id", "", "entry-1:short:" + "b" * 16, 7):
                with self.assertRaises(ValueError, msg=repr(junk)):
                    cal.append_terminal(path, self._valid(attempt_id="2" * 32, decision_id=junk))

if __name__ == "__main__":
    unittest.main()
