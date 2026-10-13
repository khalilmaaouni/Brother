"""JW3.a1: what scripts/jev_outcome.py must keep true.

Every ledger here is a throwaway folder built with the real writers
(jev_calibration.append_submission, append_terminal, append_decision),
never hand written JSON and never the real ~/.brother/jev ledger: HOME and
BROTHER_JEV_STATE_DIR are pointed at fresh temp folders before any product
import. No test makes a Jev call.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
import uuid
from datetime import datetime, timezone

_HOME = tempfile.mkdtemp(prefix="jev-outcome-home-")
os.environ["HOME"] = _HOME
os.environ["BROTHER_JEV_STATE_DIR"] = os.path.join(_HOME, "jev-state")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import jev_calibration as C  # noqa: E402
import jev_outcome as O  # noqa: E402
import jev_seam  # noqa: E402

FRAMING = "0123456789abcdef"
MODEL = "fixture-model"


def _now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def write_attempt(ledger, prob, *, phase="answered", qtype="noul", decision=True,
                  terminal=True, entry="J117"):
    """One attempt through the real writers; returns its attempt id."""
    os.makedirs(ledger, exist_ok=True)
    path = O.attempts_path(ledger)
    aid = jev_seam.mint_attempt_id()
    C.append_submission(path, {
        "schema": "attempt/v1", "phase": "submitted", "attempt_id": aid,
        "parent_id": None, "entry_id": entry, "qtype": None, "framing": None,
        "model": "", "mode": "shadow", "at": _now()})
    did = None
    if decision:
        h = uuid.uuid4().hex
        did = "%s:%s:%s" % (entry, h[:16], h[16:])
        C.append_decision(os.path.join(ledger, "decisions.jsonl"), {
            "id": did, "family": entry, "qtype": qtype, "framing": FRAMING,
            "answer": prob > 0.5, "prob": prob, "confidence": prob,
            "model": MODEL, "cost": 0.0, "at": _now()})
    if terminal:
        C.append_terminal(path, {
            "schema": "attempt/v1", "phase": phase, "attempt_id": aid,
            "parent_id": None, "entry_id": entry, "mode": "shadow", "at": _now(),
            "family": entry, "qtype": qtype, "framing": FRAMING, "model": MODEL,
            "answer": prob > 0.5, "prob": prob, "confidence": prob, "cost": 0.0,
            "reason": None, "audit": False, "decision_id": did})
    return aid


def outcomes(ledger):
    path = os.path.join(ledger, "outcomes.jsonl")
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="jev-outcome-")
        self.ledger = os.path.join(self.root, "ledger")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)


class AgreeAndDisagree(Base):
    """REQ-JW3-01."""

    def test_four_combinations(self):
        cases = [(0.95, True, True), (0.95, False, False),
                 (0.05, False, True), (0.05, True, False)]
        for prob, rule, correct in cases:
            ledger = os.path.join(self.root, "l%s%s" % (prob, rule))
            aid = write_attempt(ledger, prob)
            state, reason = O.label_attempt(ledger, aid, rule)
            self.assertEqual(state, "LABELLED", reason)
            rows = outcomes(ledger)
            self.assertEqual(len(rows), 1)
            self.assertIs(rows[0]["correct"], correct, (prob, rule))
            C._validate_outcome(rows[0])

    def test_row_passed_in_is_used(self):
        aid = write_attempt(self.ledger, 0.95)
        row = O.terminal_rows(O.attempts_path(self.ledger), {aid})[aid]
        state, _ = O.label_attempt(self.ledger, aid, True, row=row, now="2026-10-12T00:00:00Z")
        self.assertEqual(state, "LABELLED")
        self.assertEqual(outcomes(self.ledger)[0]["at"], "2026-10-12T00:00:00Z")


class BandAndPhase(Base):
    """REQ-JW3-02."""

    def test_closed_band_writes_nothing(self):
        for prob in (0.2, 0.5, 0.8):
            ledger = os.path.join(self.root, "b%s" % prob)
            aid = write_attempt(ledger, prob)
            for rule in (True, False):
                state, _ = O.label_attempt(ledger, aid, rule)
                self.assertEqual(state, "SKIPPED", prob)
            self.assertEqual(outcomes(ledger), [])

    def test_other_phases_write_nothing(self):
        for phase in ("abstain", "no_data"):
            ledger = os.path.join(self.root, phase)
            aid = write_attempt(ledger, 0.95, phase=phase)
            state, _ = O.label_attempt(ledger, aid, True)
            self.assertEqual(state, "SKIPPED", phase)
            self.assertEqual(outcomes(ledger), [])

    def test_other_qtype_writes_nothing(self):
        aid = write_attempt(self.ledger, 0.95, qtype="choice")
        self.assertEqual(O.label_attempt(self.ledger, aid, True)[0], "SKIPPED")
        self.assertEqual(outcomes(self.ledger), [])

    def test_jev_says(self):
        def row(**kw):
            base = {"phase": "answered", "qtype": "noul", "prob": 0.9}
            base.update(kw)
            return base
        self.assertIs(O.jev_says(row(prob=0.81)), True)
        self.assertIs(O.jev_says(row(prob=1)), True)
        self.assertIs(O.jev_says(row(prob=0.19)), False)
        self.assertIs(O.jev_says(row(prob=0)), False)
        for bad in (row(prob=0.8), row(prob=0.2), row(prob=0.5), row(prob=True),
                    row(prob=False), row(prob=float("nan")), row(prob="0.9"),
                    row(prob=None), row(phase="abstain"), row(qtype="choice"),
                    {"phase": "answered", "qtype": "noul"}, None, "row", []):
            self.assertIsNone(O.jev_says(bad), bad)


class Pending(Base):
    """REQ-JW3-03 and the empty edges."""

    def test_submitted_only_is_pending(self):
        aid = write_attempt(self.ledger, 0.95, decision=False, terminal=False)
        self.assertEqual(O.label_attempt(self.ledger, aid, True)[0], "PENDING")
        self.assertEqual(outcomes(self.ledger), [])

    def test_no_ledger_folder_is_pending_and_creates_nothing(self):
        aid = jev_seam.mint_attempt_id()
        self.assertEqual(O.label_attempt(self.ledger, aid, True)[0], "PENDING")
        self.assertFalse(os.path.exists(self.ledger))
        self.assertEqual(O.terminal_rows(O.attempts_path(self.ledger), {aid}), {})

    def test_empty_attempts_file_is_pending(self):
        os.makedirs(self.ledger)
        open(O.attempts_path(self.ledger), "w").close()
        aid = jev_seam.mint_attempt_id()
        self.assertEqual(O.label_attempt(self.ledger, aid, True)[0], "PENDING")
        self.assertEqual(outcomes(self.ledger), [])


class FirstLabelWins(Base):
    """REQ-JW3-04, and the restarted same actor."""

    def test_label_twice_reads_one_row(self):
        aid = write_attempt(self.ledger, 0.95)
        self.assertEqual(O.label_attempt(self.ledger, aid, True)[0], "LABELLED")
        state, reason = O.label_attempt(self.ledger, aid, False)
        self.assertEqual(state, "SKIPPED", reason)
        rows = outcomes(self.ledger)
        self.assertEqual(len(rows), 1)
        self.assertIs(rows[0]["correct"], True)


class CorruptNeverRaises(Base):
    """REQ-JW3-05."""

    def _append(self, data):
        with open(O.attempts_path(self.ledger), "ab") as fh:
            fh.write(data)

    def test_torn_last_line(self):
        aid = write_attempt(self.ledger, 0.95)
        self._append(b'{"schema":"attempt/v1","pha')
        self.assertIn(aid, O.terminal_rows(O.attempts_path(self.ledger), {aid}))
        state, _ = O.label_attempt(self.ledger, aid, True)
        self.assertEqual(state, "REFUSED")
        self.assertEqual(outcomes(self.ledger), [])

    def test_torn_line_mid_file(self):
        write_attempt(self.ledger, 0.95)
        self._append(b'{"truncated\n')
        aid = write_attempt(self.ledger, 0.95)
        self.assertEqual(O.label_attempt(self.ledger, aid, True)[0], "REFUSED")
        self.assertEqual(outcomes(self.ledger), [])

    def test_non_utf8_byte(self):
        aid = write_attempt(self.ledger, 0.95)
        self._append(b"\xff\xfe\n")
        self.assertEqual(O.label_attempt(self.ledger, aid, True)[0], "REFUSED")
        self.assertEqual(outcomes(self.ledger), [])

    def test_directory_in_place_of_file(self):
        os.makedirs(O.attempts_path(self.ledger))
        aid = jev_seam.mint_attempt_id()
        self.assertEqual(O.terminal_rows(O.attempts_path(self.ledger), {aid}), {})
        self.assertIn(O.label_attempt(self.ledger, aid, True)[0], ("PENDING", "REFUSED"))
        self.assertEqual(outcomes(self.ledger), [])

    def test_decision_id_none_is_skipped(self):
        aid = write_attempt(self.ledger, 0.95, decision=False)
        self.assertEqual(O.label_attempt(self.ledger, aid, True)[0], "SKIPPED")
        self.assertEqual(outcomes(self.ledger), [])

    def test_bad_row_argument_never_raises(self):
        aid = write_attempt(self.ledger, 0.95)
        for bad in ({"phase": "answered", "qtype": "noul", "prob": 0.95,
                     "decision_id": "x", "family": None}, ["not", "a", "dict"]):
            state, _ = O.label_attempt(self.ledger, aid, True, row=bad)
            self.assertIn(state, ("SKIPPED", "REFUSED"))
        self.assertEqual(outcomes(self.ledger), [])


class DuplicateTerminal(Base):
    """REQ-JW3-06: built with the real writer into a sibling file, then
    joined, since append_terminal refuses a second terminal in-process."""

    def test_two_terminal_rows_label_nothing(self):
        aid = write_attempt(self.ledger, 0.95)
        other = os.path.join(self.root, "other.jsonl")
        with open(O.attempts_path(self.ledger), "rb") as fh:
            lines = fh.read().splitlines(True)
        terminal = [line for line in lines if b'"phase":"answered"' in line]
        self.assertEqual(len(terminal), 1)
        rec = json.loads(terminal[0])
        C.append_terminal(other, rec)
        with open(other, "rb") as src, open(O.attempts_path(self.ledger), "ab") as dst:
            dst.write(src.read())
        self.assertEqual(O.terminal_rows(O.attempts_path(self.ledger), {aid}), {})
        state, _ = O.label_attempt(self.ledger, aid, True)
        self.assertNotEqual(state, "LABELLED")
        self.assertEqual(outcomes(self.ledger), [])


class LabelShape(Base):
    """REQ-JW3-07."""

    def test_exact_key_set(self):
        aid = write_attempt(self.ledger, 0.05)
        O.label_attempt(self.ledger, aid, False)
        rows = outcomes(self.ledger)
        self.assertEqual(len(rows), 1)
        self.assertEqual(set(rows[0]), {"id", "correct", "at", "source",
                                        "attempt_id", "proposition_hash"})
        row = O.terminal_rows(O.attempts_path(self.ledger), {aid})[aid]
        self.assertEqual(rows[0]["id"], row["decision_id"])
        self.assertEqual(rows[0]["attempt_id"], aid)
        self.assertEqual(rows[0]["source"], O.SOURCE)
        self.assertEqual(rows[0]["proposition_hash"],
                         C.proposition_hash("J117", "noul", FRAMING, MODEL))
        C._parse_ts(rows[0]["at"])


class NonBoolRule(Base):
    def test_non_bool_refused_before_reading(self):
        aid = write_attempt(self.ledger, 0.95)
        for bad in ("yes", None, 1, 0, 0.9):
            state, _ = O.label_attempt(self.ledger, aid, bad)
            self.assertEqual(state, "REFUSED", bad)
        self.assertEqual(outcomes(self.ledger), [])


class Many(Base):
    def test_subset_of_two_hundred(self):
        ids = [write_attempt(self.ledger, 0.95 if i % 2 else 0.05) for i in range(200)]
        wanted = set(ids[::4])
        rows = O.terminal_rows(O.attempts_path(self.ledger), wanted)
        self.assertEqual(set(rows), wanted)
        self.assertTrue(all(r["phase"] == "answered" for r in rows.values()))


if __name__ == "__main__":
    unittest.main()
