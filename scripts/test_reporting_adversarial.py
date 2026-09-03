"""Adversarial cases against Brother's own status reporting.

GAP THIS CLOSES: "no adversarial case has been run against the reporting
itself." board_status.py, gen_readiness_board.py and run_evidence.py each
already carry unit tests that call their internal functions with hand-built
fixtures. This file is different on purpose: it drives the actual command
lines, against COPIES of the real roadmap and real subprocess commands, the
way a person trying to make the board lie would. It never mutates
docs/plan/READINESS-ROADMAP-2026-08-29.json; every case works on a temp copy.

Six cases, matching the six the board is supposed to survive:
  1. A row marked DONE with empty evidence: a CLAIM, excluded from the count.
  2. Same, with whitespace-only evidence.
  3. Truncated / corrupt roadmap JSON: refuse readably, never a traceback,
     never a stale board.
  4. A section with nothing in it: NO-DATA, never a lying 0%.
  5. run_evidence.py's three alert shapes, forced with real commands.
  6. gen_readiness_board.py fed a roadmap that fails its own validate():
     refuses outright rather than writing a board that silently drops rows.

Python 3, standard library only. No network, no real model.
"""
import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS)
import board_status as BS  # noqa: E402
import gen_readiness_board as GRB  # noqa: E402

BOARD_STATUS = os.path.join(SCRIPTS, "board_status.py")
RUN_EVIDENCE = os.path.join(SCRIPTS, "run_evidence.py")


def run(*args):
    """Drive a script exactly as a person would from the shell."""
    p = subprocess.run([sys.executable] + list(args), stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, universal_newlines=True)
    return p.returncode, p.stdout, p.stderr


def real_roadmap():
    """A COPY, in memory, of the live board. Callers mutate the copy and write
    it to a temp file; the file on disk under docs/plan is never touched."""
    with open(BS.SOURCE, encoding="utf-8") as fh:
        return json.load(fh)


def write_json(doc, tmpdir, name):
    path = os.path.join(tmpdir, name)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh)
    return path


class ADoneClaimNeverInflatesThePercentage(unittest.TestCase):
    """Cases 1 and 2. Driven against a copy of the REAL roadmap, through the
    actual board_status.py command line, backwards: start from the honest
    board, add exactly one dishonest row, and prove the number does not move
    the way the dishonest row wants it to."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _rows_section(self, doc, filename):
        path = write_json(doc, self.tmp, filename)
        code, out, err = run(BOARD_STATUS, "--source", path, "--json")
        # NOTE ON THIS ASSERTION: --json mode returns 0 even when the board
        # carries claims (main() only returns 1 on the human-readable path).
        # That asymmetry is real, observed behavior, not something this test
        # papers over; the human-readable path is exercised separately below.
        self.assertEqual(code, 0, out + err)
        secs = json.loads(out)
        return next(s for s in secs if s["key"] == "rows")

    def _claim_case(self, evidence):
        base = real_roadmap()
        base_rows = self._rows_section(base, "base.json")
        self.assertEqual(base_rows["counts"]["claimed"], 0,
                         "the real roadmap already carries an unevidenced claim")

        mutated = copy.deepcopy(base)
        mutated["rows"].append({"id": "ADV-CLAIM", "status": "DONE",
                                "evidence": evidence})
        after_rows = self._rows_section(mutated, "mutated.json")

        # THE PROPERTY: a claim widens the denominator (it is a real row) but
        # never the numerator (it is not evidenced work).
        self.assertEqual(after_rows["counts"]["done"], base_rows["counts"]["done"],
                         "a DONE claim with no evidence raised the done count")
        self.assertEqual(after_rows["counts"]["claimed"], 1)
        self.assertEqual(after_rows["total"], base_rows["total"] + 1)

        # THE HUMAN-READABLE PATH: this is where board_status is documented to
        # exit nonzero and name the claim by count.
        path = write_json(mutated, self.tmp, "mutated-human.json")
        code, out, err = run(BOARD_STATUS, "--source", path)
        combined = out + err
        self.assertEqual(code, 1, combined)
        self.assertIn("claim", combined.lower())
        self.assertIn("1 item", combined)
        self.assertIn("not", combined.lower())

    def test_empty_evidence_is_a_claim_excluded_from_the_percentage(self):
        self._claim_case("")

    def test_whitespace_only_evidence_is_a_claim_too(self):
        self._claim_case("   \n\t  ")


class ACorruptRoadmapNeverBecomesAStaleOrPartialBoard(unittest.TestCase):
    """Case 3. board_status.py must refuse readably, name the file, and never
    print a traceback or a board built from nothing."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_truncated_json_refuses_by_name_with_no_traceback(self):
        path = os.path.join(self.tmp, "truncated.json")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write('{"rows": [{"id": "R1", "status": "DONE", "evidence": "x"')
        code, out, err = run(BOARD_STATUS, "--source", path)
        combined = out + err
        self.assertEqual(code, 2, combined)
        self.assertIn(path, combined)
        self.assertNotIn("Traceback", combined)
        # No stale/partial board printed: the failure happens before any
        # section is ever rendered, so stdout carries nothing at all.
        self.assertEqual(out.strip(), "")

    def test_syntactically_invalid_json_refuses_the_same_way(self):
        path = os.path.join(self.tmp, "garbage.json")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("this is not json at all { [ :")
        code, out, err = run(BOARD_STATUS, "--source", path)
        combined = out + err
        self.assertEqual(code, 2, combined)
        self.assertIn(path, combined)
        self.assertNotIn("Traceback", combined)
        self.assertEqual(out.strip(), "")


class ASectionWithNothingCountableSaysNoDataNeverZero(unittest.TestCase):
    """Case 4. Documented behavior: an empty section is NO-DATA, never a 0%
    that reads as "measured and nothing has started"."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_board_with_nothing_countable_prints_NO_DATA_not_zero_percent(self):
        path = write_json({"rows": [], "features": [], "gates": []}, self.tmp,
                          "empty.json")
        code, out, err = run(BOARD_STATUS, "--source", path)
        self.assertEqual(code, 0, out + err)
        seen = 0
        for line in out.splitlines():
            if line.startswith(("Features", "Readiness rows", "Gates")):
                seen += 1
                self.assertIn(BS.NODATA, line, line)
                self.assertNotIn("0%", line, line)
        self.assertEqual(seen, 3, "expected all three sections to print a line")


class RunEvidenceAlertsFireOnRealCommands(unittest.TestCase):
    """Case 5. Not mocked runners: real subprocesses, so the three shapes that
    have actually fooled someone here are forced for real."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_empty_output_triggers_the_empty_output_alert(self):
        code, out, err = run(RUN_EVIDENCE, "--store", self.tmp, "--", "true")
        self.assertEqual(code, 0, out + err)
        self.assertIn("ALERT", err)
        self.assertIn("NOTHING", err)

    def test_a_tracked_verdict_word_with_exit_zero_triggers_the_disagreement_alert(self):
        code, out, err = run(RUN_EVIDENCE, "--store", self.tmp, "--", "sh", "-c",
                             "echo FAILED: 1 case; exit 0")
        self.assertEqual(code, 0, out + err)
        self.assertIn("ALERT", err)
        self.assertIn("still exited 0", err)

    def test_exit_zero_with_ordinary_output_and_no_verdict_word_is_clean(self):
        code, out, err = run(RUN_EVIDENCE, "--store", self.tmp, "--", "sh", "-c",
                             "echo all fine; exit 0")
        self.assertEqual(code, 0, out + err)
        self.assertNotIn("ALERT", err)
        self.assertNotIn("WARN", err)

    def test_printing_PASS_while_exiting_nonzero_is_documented_as_a_WARN(self):
        """Driven for real rather than assumed. VERDICT_WORDS is exactly
        (FAILED, FAIL:, ERROR, REFUSED, NO-DATA); PASS is not one of them, so a
        command that prints PASS and exits 1 does not hit the ALERT branch
        (verdict word plus exit 0). It hits the other documented branch:
        nonzero exit with no tracked verdict word, which is a WARN. Recording
        this so nobody assumes the tool recognises arbitrary success-looking
        text as a false verdict; it only tracks the five words above."""
        code, out, err = run(RUN_EVIDENCE, "--store", self.tmp, "--", "sh", "-c",
                             "echo PASS; exit 1")
        self.assertEqual(code, 1, out + err)
        self.assertIn("WARN", err)
        self.assertNotIn("ALERT", err)


class GenReadinessBoardRefusesRatherThanDroppingInvalidRows(unittest.TestCase):
    """Case 6. A roadmap that fails validate() must not produce a rendered
    board at all: refusing the whole render is the documented behavior, never
    quietly excluding the offending row from an otherwise-normal-looking page."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.saved_source = GRB.SOURCE
        self.saved_output = GRB.OUTPUT
        self.out_path = os.path.join(self.tmp, "board.html")
        GRB.OUTPUT = self.out_path

    def tearDown(self):
        GRB.SOURCE = self.saved_source
        GRB.OUTPUT = self.saved_output
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _doc_with_a_dangling_dependency(self):
        return {
            "gates": [{"id": "G1", "title": "g", "size": "s", "status": "OPEN",
                      "blocker": "b"}],
            "rows": [{"id": "R1", "gate": "G1", "wave": 1, "title": "t",
                     "detail": "d", "depends_on": ["NOPE"], "owner": "o",
                     "status": "DONE", "done_check": "c", "watchdog_verify": "v",
                     "owns": [], "ships": "s", "role": "r", "why_now": "w",
                     "effect": "e", "visible_when": "when", "persona": "P1",
                     "their_moment": "m", "what_they_see": "sees"}],
            "features": [],
        }

    def test_an_invalid_roadmap_refuses_and_writes_no_board_at_all(self):
        path = write_json(self._doc_with_a_dangling_dependency(), self.tmp,
                          "invalid.json")
        GRB.SOURCE = path
        code = GRB.main([])
        self.assertEqual(code, 1)
        self.assertFalse(os.path.exists(self.out_path),
                         "a board was written despite a failed validate()")

    def test_the_broken_row_is_named_by_id_never_silently_dropped(self):
        path = write_json(self._doc_with_a_dangling_dependency(), self.tmp,
                          "invalid2.json")
        problems = GRB.validate(GRB.load(path))
        self.assertTrue(any("R1" in p and "NOPE" in p for p in problems), problems)


if __name__ == "__main__":
    unittest.main()
