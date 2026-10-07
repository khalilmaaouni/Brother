"""Tests for D3.1: submission identity and the submission row.

Covers jev_seam.mint_attempt_id() and jev_calibration.append_submission().
Run from the repo root:

    python3 -B -m unittest scripts.test_jev_submission_identity
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import jev_calibration  # noqa: E402  (sibling module under test)
import jev_seam  # noqa: E402  (sibling module under test)


def _valid_row(**overrides):
    row = {
        "schema": "attempt/v1",
        "phase": "submitted",
        "attempt_id": jev_seam.mint_attempt_id(),
        "parent_id": None,
        "entry_id": "billing.late_fee",
        "mode": "shadow",
        "at": "2026-09-19T12:00:00Z",
    }
    row.update(overrides)
    return row


def _without(row, key):
    trimmed = dict(row)
    trimmed.pop(key, None)
    return trimmed


def _rows_written(path):
    with open(path, "r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


class TestMintAttemptId(unittest.TestCase):
    def test_fresh_ids_are_32_lowercase_hex_and_distinct(self):
        first = jev_seam.mint_attempt_id()
        second = jev_seam.mint_attempt_id()
        for value in (first, second):
            self.assertIsInstance(value, str)
            self.assertEqual(len(value), 32)
            self.assertEqual(value, value.lower())
            self.assertNotIn("-", value)
            int(value, 16)  # ValueError if any character is not a hex digit
        self.assertNotEqual(first, second)


class TestAppendSubmission(unittest.TestCase):
    def test_valid_row_lands_byte_for_byte(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "attempts.jsonl")
            row = _valid_row()
            jev_calibration.append_submission(path, row)
            self.assertTrue(os.path.isfile(path))
            self.assertEqual(_rows_written(path), [row])

    def test_second_row_appends_without_rewriting_the_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "attempts.jsonl")
            first = _valid_row()
            second = _valid_row(entry_id="other.entry", mode="act")
            jev_calibration.append_submission(path, first)
            jev_calibration.append_submission(path, second)
            self.assertEqual(_rows_written(path), [first, second])

    def test_rotation_is_attempted_after_a_successful_append(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "attempts.jsonl")
            jev_calibration.append_submission(path, _valid_row(), max_segment_bytes=1)
            self.assertFalse(os.path.exists(path))
            rotated = [name for name in os.listdir(tmp)
                       if name.startswith("attempts.") and name.endswith(".jsonl")]
            self.assertEqual(len(rotated), 1)

    def test_invalid_rows_raise_value_error_and_write_nothing(self):
        bad_rows = [
            ("not a dict (None)", None),
            ("not a dict (str)", "attempt"),
            ("not a dict (bool)", True),
            ("not a dict (list)", []),
            ("not a dict (tuple)", ()),
            ("wrong schema", _valid_row(schema="attempt/v2")),
            ("missing schema", _without(_valid_row(), "schema")),
            ("schema a list", _valid_row(schema=["attempt/v1"])),
            ("wrong phase", _valid_row(phase="terminal")),
            ("phase None", _valid_row(phase=None)),
            ("attempt_id an int", _valid_row(attempt_id=12345)),
            ("attempt_id a bool", _valid_row(attempt_id=True)),
            ("attempt_id None", _valid_row(attempt_id=None)),
            ("attempt_id too short", _valid_row(attempt_id="0123456789abcdef")),
            ("attempt_id with dashes", _valid_row(attempt_id="01234567-89ab-cdef-0123-456789abcdef")),
            ("attempt_id uppercase", _valid_row(attempt_id="0123456789ABCDEF0123456789ABCDEF")),
            ("attempt_id not hex", _valid_row(attempt_id="z" * 32)),
            ("attempt_id a list", _valid_row(attempt_id=[])),
            ("parent_id wrong type", _valid_row(parent_id=7)),
            ("parent_id a bool", _valid_row(parent_id=True)),
            ("parent_id unhashable", _valid_row(parent_id={"a": 1})),
            ("entry_id missing", _without(_valid_row(), "entry_id")),
            ("entry_id empty", _valid_row(entry_id="")),
            ("entry_id blank", _valid_row(entry_id="   ")),
            ("entry_id wrong type", _valid_row(entry_id=7)),
            ("entry_id a list", _valid_row(entry_id=[])),
            ("mode unknown", _valid_row(mode="steer")),
            ("mode wrong case", _valid_row(mode="ACT")),
            ("mode a bool", _valid_row(mode=True)),
            ("mode None", _valid_row(mode=None)),
            ("mode unhashable", _valid_row(mode=["off"])),
            ("at missing", _without(_valid_row(), "at")),
            ("at not a timestamp", _valid_row(at="not a timestamp")),
            ("at empty", _valid_row(at="")),
            ("at None", _valid_row(at=None)),
            ("at a bool", _valid_row(at=True)),
            ("at nan", _valid_row(at=float("nan"))),
            ("at a list", _valid_row(at=[])),
        ]
        for label, row in bad_rows:
            with self.subTest(label=label):
                with tempfile.TemporaryDirectory() as tmp:
                    path = os.path.join(tmp, "attempts.jsonl")
                    with self.assertRaises(ValueError):
                        jev_calibration.append_submission(path, row)
                    self.assertFalse(os.path.exists(path))


if __name__ == "__main__":
    unittest.main()
