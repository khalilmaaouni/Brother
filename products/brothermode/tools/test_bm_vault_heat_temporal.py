#!/usr/bin/env python3
"""Calibration for tools/bm_vault_heat_temporal.py, row V8 (heat-scored
promotion and bi-temporal fields).

Driven both ways per the row's own done-check: a note valid yesterday and
superseded today is returned by as_of() as of yesterday and not as of today;
a heat counter at threshold minus one does not promote, at threshold it does.
Tempfile only. Never touches the real vault or anything under ~/.claude.

No em or en dashes anywhere in this file.
"""
import datetime
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bm_vault_heat_temporal as heat  # noqa: E402

# E100: one sandbox for every temp tree this process makes, removed at exit.
sys.path.append(os.path.join(HERE, "../../../scripts"))
try:  # noqa: E402
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n" % os.path.basename(__file__))

TODAY = datetime.date.today()
YESTERDAY = TODAY - datetime.timedelta(days=1)
LONG_AGO = TODAY - datetime.timedelta(days=60)


def _write(path, frontmatter):
    lines = ["---"]
    for k, v in frontmatter.items():
        lines.append("%s: %s" % (k, v))
    lines.append("---")
    lines.append("")
    lines.append("body")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


class TheBiTemporalParseContract(unittest.TestCase):

    def test_absent_fields_default_to_created_and_open(self):
        text = "---\ncreated: 2026-01-01\n---\nbody\n"
        record, problems = heat.parse_note(text, created=datetime.date(2026, 1, 1))
        self.assertEqual(problems, [])
        self.assertEqual(record["valid_from"], datetime.date(2026, 1, 1))
        self.assertEqual(record["recorded_at"], datetime.date(2026, 1, 1))
        self.assertIsNone(record["valid_to"])
        self.assertIsNone(record["superseded_by"])

    def test_declared_fields_override_the_default(self):
        text = ("---\ncreated: 2026-01-01\nvalid_from: 2026-02-01\n"
                "valid_to: 2026-03-01\nrecorded_at: 2026-01-15\n"
                "superseded_by: other-note\n---\nbody\n")
        record, problems = heat.parse_note(text, created=datetime.date(2026, 1, 1))
        self.assertEqual(problems, [])
        self.assertEqual(record["valid_from"], datetime.date(2026, 2, 1))
        self.assertEqual(record["valid_to"], datetime.date(2026, 3, 1))
        self.assertEqual(record["recorded_at"], datetime.date(2026, 1, 15))
        self.assertEqual(record["superseded_by"], "other-note")

    def test_malformed_date_is_reported_never_silently_current(self):
        text = "---\ncreated: 2026-01-01\nvalid_from: not-a-date\n---\nbody\n"
        record, problems = heat.parse_note(text, created=datetime.date(2026, 1, 1))
        self.assertIsNone(record["valid_from"])
        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0][0], "valid_from")


class TheAsOfQueryContract(unittest.TestCase):
    """The done-check's own scenario: a note valid yesterday, superseded
    today, driven both ways."""

    def test_valid_yesterday_superseded_today_returns_as_of_yesterday_not_today(self):
        notes = {
            "original": {
                "valid_from": LONG_AGO, "valid_to": None,
                "recorded_at": LONG_AGO, "superseded_by": "correction",
            },
            "correction": {
                "valid_from": TODAY, "valid_to": None,
                "recorded_at": TODAY, "superseded_by": None,
            },
        }
        as_of_yesterday = heat.as_of(notes, YESTERDAY)
        as_of_today = heat.as_of(notes, TODAY)
        self.assertIn("original", as_of_yesterday)
        self.assertNotIn("original", as_of_today)
        self.assertIn("correction", as_of_today)

    def test_valid_to_excludes_a_note_that_has_closed(self):
        notes = {"n": {"valid_from": LONG_AGO, "valid_to": YESTERDAY,
                       "recorded_at": LONG_AGO, "superseded_by": None}}
        self.assertEqual(heat.as_of(notes, YESTERDAY), [])
        self.assertEqual(heat.as_of(notes, LONG_AGO), ["n"])

    def test_a_note_not_yet_valid_is_excluded(self):
        notes = {"n": {"valid_from": TODAY, "valid_to": None,
                       "recorded_at": TODAY, "superseded_by": None}}
        self.assertEqual(heat.as_of(notes, YESTERDAY), [])
        self.assertEqual(heat.as_of(notes, TODAY), ["n"])

    def test_superseded_by_target_absent_from_input_is_never_a_crash(self):
        notes = {"n": {"valid_from": LONG_AGO, "valid_to": None,
                       "recorded_at": LONG_AGO, "superseded_by": "nowhere"}}
        self.assertEqual(heat.as_of(notes, TODAY), ["n"])


class TheHeatCounterContract(unittest.TestCase):
    """The done-check's other half: promotion by a mechanical counter,
    driven both ways at the exact threshold boundary."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-vault-heat-unit-")
        self.path = os.path.join(self.tmp, "bm_vault_heat.json")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_threshold_minus_one_does_not_promote_threshold_does(self):
        threshold = 3
        for _ in range(threshold - 1):
            heat.record_recall("n1", self.path)
        before = heat._load_heat(self.path)["n1"]
        self.assertEqual(before, threshold - 1)
        self.assertNotIn("n1", heat.promote(threshold, self.path))
        heat.record_recall("n1", self.path)
        after = heat._load_heat(self.path)["n1"]
        self.assertEqual(after, threshold)
        self.assertIn("n1", heat.promote(threshold, self.path))

    def test_counter_is_mechanical_never_scoped_to_a_single_call(self):
        heat.record_recall("n2", self.path)
        heat.record_recall("n2", self.path)
        heat.record_recall("n3", self.path)
        counts = heat._load_heat(self.path)
        self.assertEqual(counts["n2"], 2)
        self.assertEqual(counts["n3"], 1)

    def test_missing_heat_file_reads_as_empty_never_a_crash(self):
        self.assertEqual(heat._load_heat(self.path), {})
        self.assertEqual(heat.promote(1, self.path), [])


class TheVaultScanContract(unittest.TestCase):
    """End to end through actual note files, the shape a real vault carries."""

    def setUp(self):
        self.vault = tempfile.mkdtemp(prefix="bm-vault-heat-scan-")

    def tearDown(self):
        shutil.rmtree(self.vault, ignore_errors=True)

    def test_scan_and_as_of_over_real_note_files(self):
        _write(os.path.join(self.vault, "original.md"),
               {"created": LONG_AGO, "superseded_by": "correction"})
        _write(os.path.join(self.vault, "correction.md"),
               {"created": TODAY, "recorded_at": TODAY})
        notes = heat.scan_vault(self.vault)
        self.assertIn("original", notes)
        self.assertIn("correction", notes)
        self.assertIn("original", heat.as_of(notes, YESTERDAY))
        self.assertNotIn("original", heat.as_of(notes, TODAY))

    def test_unreadable_note_is_skipped_and_named_not_silently_dropped(self):
        path = os.path.join(self.vault, "ghost.md")
        _write(path, {"created": TODAY})
        os.chmod(path, 0)
        try:
            notes = heat.scan_vault(self.vault)
            if os.geteuid() == 0:
                self.skipTest("root ignores chmod 0")
            self.assertNotIn("ghost", notes)
        finally:
            os.chmod(path, 0o644)


if __name__ == "__main__":
    unittest.main(verbosity=2)
