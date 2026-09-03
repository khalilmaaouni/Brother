#!/usr/bin/env python3
"""Fixtures for `sbe testkit`. Run: python3 tools/test_sbe_testkit.py

Every test runs the real `bin/sbe testkit` command against a temporary
directory, the same "no mocked filesystem" rule `tools/test_sbe_adopt.py`
already states: the defect this tool exists to avoid lives at the seam
between what a hand-filled sheet actually says and what a parser claims it
says, and a mocked sheet would test the mock.
"""
import io
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
SBE = os.path.join(ROOT, "bin", "sbe")

sys.path.insert(0, HERE)
import sbe_testkit  # noqa: E402  (path setup has to come first)

BEHAVIOUR_TWO_ROWS = """# 08. Behaviour table

## Rules
| ID | Starting point | Trigger | Required outcome | Proof |
|---|---|---|---|---|
| B1 | A user on the settings screen | The user taps save | The settings are persisted | Integration test: tap save, reload, assert settings persisted |
| B2 | A user with an expired session | The user opens the app | The user is sent to the login screen | Integration test: expire session, open app, assert login screen shown |
"""


def write(path, body):
    directory = os.path.dirname(path)
    if directory and not os.path.isdir(directory):
        os.makedirs(directory)
    with io.open(path, "w", encoding="utf-8") as fh:
        fh.write(body)


def run(*args):
    out = subprocess.run([SBE] + list(args), capture_output=True, text=True)
    return out.returncode, out.stdout, out.stderr


def sheet_path(dossier_dir):
    hits = [f for f in os.listdir(os.path.join(dossier_dir, ".sbe"))
            if f.startswith("test-run-")]
    assert len(hits) == 1, hits
    return os.path.join(dossier_dir, ".sbe", hits[0])


class TestSheet(unittest.TestCase):
    def test_no_data_when_no_behaviour_file(self):
        with tempfile.TemporaryDirectory() as d:
            code, out, _err = run("testkit", "sheet", d)
            self.assertEqual(code, 0)
            lines = [l for l in out.splitlines() if l.strip()]
            self.assertEqual(len(lines), 1, out)
            self.assertTrue(lines[0].startswith("NO-DATA:"), out)
            self.assertFalse(os.path.isdir(os.path.join(d, ".sbe")))

    def test_one_case_per_row_plus_five_charters(self):
        with tempfile.TemporaryDirectory() as d:
            write(os.path.join(d, "08-behaviour.md"), BEHAVIOUR_TWO_ROWS)
            code, out, err = run("testkit", "sheet", d)
            self.assertEqual(code, 0, err)
            body = io.open(sheet_path(d), encoding="utf-8").read()
            self.assertEqual(body.count("\nCASE "), 2)
            self.assertEqual(body.count("\nCHARTER "), 5)
            for cid in ("unnatural-behaviour", "user-experience",
                       "analyst-developer-gap", "translated-text", "limits"):
                self.assertIn("CHARTER %s" % cid, body)
            # Verbatim fields, not paraphrased.
            self.assertIn("GIVEN   A user on the settings screen", body)
            self.assertIn("EXPECT  The settings are persisted", body)
            self.assertIn("PROOF PLANNED  Integration test: tap save, reload, "
                          "assert settings persisted", body)
            self.assertIn("SCREEN  ____", body)
            self.assertIn("STRING AS SHOWN  ____", body)

    def test_limits_charter_prompts_for_all_six_classes(self):
        with tempfile.TemporaryDirectory() as d:
            write(os.path.join(d, "08-behaviour.md"), BEHAVIOUR_TWO_ROWS)
            code, out, err = run("testkit", "sheet", d)
            self.assertEqual(code, 0, err)
            body = io.open(sheet_path(d), encoding="utf-8").read()
            for label in ("RATE LIMIT", "SIZE LIMIT", "TIMEOUT", "PAGINATION BOUNDARY",
                         "NUMERIC OVERFLOW", "UNICODE/LOCALE"):
                self.assertIn("%s  ____" % label, body)

    def test_every_case_and_charter_carries_not_reached_tested_by_and_date(self):
        with tempfile.TemporaryDirectory() as d:
            write(os.path.join(d, "08-behaviour.md"), BEHAVIOUR_TWO_ROWS)
            code, out, err = run("testkit", "sheet", d)
            self.assertEqual(code, 0, err)
            body = io.open(sheet_path(d), encoding="utf-8").read()
            cases, charters = body.count("\nCASE "), body.count("\nCHARTER ")
            # CASE's own RESULT line and CHARTER's new STATUS line both end in
            # this exact phrase, so counting it names who ran a block and when
            # for every block in the sheet, cases and charters alike.
            self.assertEqual(body.count("tested by ____  date ____"), cases + charters)
            self.assertIn("STATUS  [ ] not reached    tested by ____  date ____", body)

    def test_no_case_marked_reconfirm_today(self):
        with tempfile.TemporaryDirectory() as d:
            write(os.path.join(d, "08-behaviour.md"), BEHAVIOUR_TWO_ROWS)
            code, out, err = run("testkit", "sheet", d)
            self.assertEqual(code, 0, err)
            body = io.open(sheet_path(d), encoding="utf-8").read()
            # _row_changed has nothing to compare against yet (no fingerprint
            # has landed in sbe_design), so today's sheet never marks or
            # reorders anything. This locks that default in.
            self.assertNotIn("RE-CONFIRM", body)
            self.assertLess(body.index("CASE B1"), body.index("CASE B2"))

    def test_refuses_when_target_is_not_a_directory(self):
        with tempfile.TemporaryDirectory() as d:
            not_a_dir = os.path.join(d, "nope")
            code, _out, err = run("testkit", "sheet", not_a_dir)
            self.assertNotEqual(code, 0)
            self.assertIn(not_a_dir, err)

    def test_error_flattens_a_dossier_dir_carrying_a_newline(self):
        with tempfile.TemporaryDirectory() as d:
            # A crafted path cannot forge a second stderr line: this is the
            # exact scenario one_line() exists for (a path carrying a line
            # break), applied here to cmd_sheet's own stderr write.
            not_a_dir = os.path.join(d, "nope\nFORGED VERDICT LINE")
            code, _out, err = run("testkit", "sheet", not_a_dir)
            self.assertNotEqual(code, 0)
            self.assertEqual(err.count("\n"), 1, err)
            self.assertIn("nope \\n FORGED VERDICT LINE", err)


class TestReconfirmOrdering(unittest.TestCase):
    """Unit-level, not subprocess: `_row_changed` has nothing real to read
    yet (no per-rule fingerprint exists in sbe_design), so the only way to
    prove the ordering and RE-CONFIRM marking mechanism itself works is to
    fake the one input it is waiting on.
    """

    ROWS = [
        {"id": "B1", "starting point": "s1", "trigger": "t1",
         "required outcome": "r1", "proof": "p1"},
        {"id": "B2", "starting point": "s2", "trigger": "t2",
         "required outcome": "r2", "proof": "p2"},
        {"id": "B3", "starting point": "s3", "trigger": "t3",
         "required outcome": "r3", "proof": "p3"},
    ]

    def test_changed_row_moves_first_and_is_marked(self):
        with mock.patch.object(sbe_testkit, "_row_changed",
                               side_effect=lambda r: r["id"] == "B2"):
            body = sbe_testkit.render_sheet(".", self.ROWS, [])
        self.assertLess(body.index("CASE B2"), body.index("CASE B1"))
        self.assertLess(body.index("CASE B1"), body.index("CASE B3"))
        self.assertIn("CASE B2  RE-CONFIRM", body)
        self.assertNotIn("CASE B1  RE-CONFIRM", body)
        self.assertNotIn("CASE B3  RE-CONFIRM", body)

    def test_nothing_flagged_changed_today(self):
        self.assertFalse(sbe_testkit._row_changed({"id": "B1"}))


class TestAdopt(unittest.TestCase):
    def test_no_data_when_sheet_missing(self):
        with tempfile.TemporaryDirectory() as d:
            code, out, _err = run("testkit", "adopt", os.path.join(d, "nope.md"))
            self.assertEqual(code, 0)
            self.assertTrue(out.strip().startswith("NO-DATA:"), out)

    def test_no_data_when_nothing_filled_in(self):
        with tempfile.TemporaryDirectory() as d:
            write(os.path.join(d, "08-behaviour.md"), BEHAVIOUR_TWO_ROWS)
            run("testkit", "sheet", d)
            code, out, _err = run("testkit", "adopt", sheet_path(d))
            self.assertEqual(code, 0)
            self.assertTrue(out.strip().startswith("NO-DATA:"), out)
            self.assertFalse(os.path.exists(os.path.join(d, ".sbe", "proposed-rows.md")))

    def test_round_trip_ids_continue_and_never_touch_08(self):
        with tempfile.TemporaryDirectory() as d:
            behaviour_path = os.path.join(d, "08-behaviour.md")
            write(behaviour_path, BEHAVIOUR_TWO_ROWS)
            run("testkit", "sheet", d)
            path = sheet_path(d)
            text = io.open(path, encoding="utf-8").read()
            before_08 = io.open(behaviour_path, encoding="utf-8").read()

            # Fill one FINDING (case B1) and one charter SAW line (translated-text),
            # exactly as a tester would edit the sheet by hand.
            text = text.replace(
                "FINDING (only if fail or blocked): what you saw:\n\nCASE B2",
                "FINDING (only if fail or blocked): what you saw: settings "
                "reverted to defaults after reopening the app\n\nCASE B2",
                1)
            text = text.replace(
                "CHARTER translated-text  (awkward translated text: a string "
                "that reads wrong on screen)\n"
                "GIVEN    ____\n"
                "DO       ____\n"
                "SCREEN  ____\n"
                "STRING AS SHOWN  ____\n"
                "SAW      ____\n"
                "EXPECTED ____\n",
                "CHARTER translated-text  (awkward translated text: a string "
                "that reads wrong on screen)\n"
                "GIVEN    the settings screen in French\n"
                "DO       open the language menu\n"
                "SCREEN   settings/language\n"
                "STRING AS SHOWN  Enregistrer les paramètre\n"
                "SAW      the label is missing its final s\n"
                "EXPECTED Enregistrer les paramètres\n")
            write(path, text)

            code, out, err = run("testkit", "adopt", path)
            self.assertEqual(code, 0, err)

            proposed_path = os.path.join(d, ".sbe", "proposed-rows.md")
            self.assertTrue(os.path.isfile(proposed_path))
            proposed = io.open(proposed_path, encoding="utf-8").read()

            # Ids continue after B1/B2: B3 and B4, never reusing an existing id.
            self.assertIn("| B3 |", proposed)
            self.assertIn("| B4 |", proposed)
            self.assertNotIn("| B1 |", proposed)
            self.assertNotIn("| B2 |", proposed)
            self.assertIn("Manual: repeat the steps in finding F-1", proposed)
            self.assertIn("Manual: repeat the steps in finding F-2", proposed)
            self.assertIn("settings reverted to defaults after reopening the app", proposed)
            self.assertIn("Enregistrer les paramètres", proposed)
            # Only 2 findings were filled in (one case, one charter); the other
            # case and the other three charters must not have manufactured rows.
            self.assertEqual(proposed.count("| B"), 2)

            # HARD CONSTRAINT: 08-behaviour.md is never edited by adopt.
            after_08 = io.open(behaviour_path, encoding="utf-8").read()
            self.assertEqual(before_08, after_08)

    def test_malformed_case_header_is_named_not_dropped(self):
        with tempfile.TemporaryDirectory() as d:
            write(os.path.join(d, "08-behaviour.md"), BEHAVIOUR_TWO_ROWS)
            broken = (
                "CASE\n"
                "GIVEN   something\n"
                "FINDING (only if fail or blocked): what you saw: a real finding "
                "no id could be read for\n")
            path = os.path.join(d, "broken-sheet.md")
            write(path, broken)
            code, out, err = run("testkit", "adopt", path)
            self.assertEqual(code, 0)
            combined = out + err
            self.assertIn("CASE", combined)
            self.assertTrue(out.strip().startswith("NO-DATA:"), out)


if __name__ == "__main__":
    unittest.main()
