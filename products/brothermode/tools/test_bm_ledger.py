"""The estimate ledger's laws, each calibrated by attempting the violation.

Declare before work, append-only history, no guessed actuals. Every refusal
test asserts the refusal's own words AND that the ledger file did not grow,
because a refusal that quietly appended would be worse than no refusal.
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
LEDGER = os.path.join(HERE, "bm_ledger.py")


class LedgerCase(unittest.TestCase):
    def setUp(self):
        self.vault = tempfile.mkdtemp(prefix="bm-ledger-test-")
        self.path = os.path.join(self.vault, "99-System", "telemetry",
                                 "estimates.jsonl")

    def tearDown(self):
        shutil.rmtree(self.vault, ignore_errors=True)

    def run_ledger(self, *argv):
        env = dict(os.environ, BROTHERMODE_VAULT=self.vault)
        out = subprocess.run([sys.executable, LEDGER] + list(argv),
                             capture_output=True, text=True, env=env, timeout=30)
        combined = out.stdout + out.stderr
        # Three values, matching the house rule that a two-value return can
        # read as a (verdict, evidence) pair to an honesty scanner.
        return out.returncode, combined, out.stderr

    def lines(self):
        if not os.path.exists(self.path):
            return []
        return [json.loads(l) for l in io.open(self.path) if l.strip()]

    def declare(self, run="r1", loop="L1", minutes=50, tokens=380000):
        code, text, _ = self.run_ledger("declare", run, loop, "--minutes",
                                        str(minutes), "--tokens", str(tokens))
        self.assertEqual(code, 0, text)


class TestDeclareBeforeWork(LedgerCase):
    def test_a_close_without_a_declaration_is_refused_and_appends_nothing(self):
        code, text, _ = self.run_ledger("close", "r1", "L1", "--minutes", "10",
                                        "--why", "it went fine")
        self.assertEqual(code, 1, text)
        self.assertIn("never declared", text)
        self.assertIn("prediction of the past", text)
        self.assertEqual(self.lines(), [], "a refusal must not append")

    def test_declare_writes_one_timestamped_row(self):
        self.declare()
        rows = self.lines()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["kind"], "declare")
        self.assertEqual(rows[0]["estMinutes"], 50)
        self.assertIn("ts", rows[0])

    def test_a_zero_estimate_is_refused(self):
        code, text, _ = self.run_ledger("declare", "r1", "L1", "--minutes", "0",
                                        "--tokens", "1000")
        self.assertEqual(code, 1, text)
        self.assertIn("not an estimate", text)
        self.assertEqual(self.lines(), [])


class TestAppendOnlyHistory(LedgerCase):
    def test_double_declare_is_refused_but_amend_appends_and_wins(self):
        self.declare(minutes=50, tokens=100000)
        code, text, _ = self.run_ledger("declare", "r1", "L1", "--minutes", "60",
                                        "--tokens", "1")
        self.assertEqual(code, 1, text)
        self.assertIn("amend", text)
        code, text, _ = self.run_ledger("amend", "r1", "L1", "--minutes", "100",
                                        "--tokens", "100000")
        self.assertEqual(code, 0, text)
        code, text, _ = self.run_ledger("close", "r1", "L1", "--minutes", "100",
                                        "--why", "graded against the amendment")
        self.assertEqual(code, 0, text)
        rows = self.lines()
        self.assertEqual([r["kind"] for r in rows], ["declare", "amend", "close"],
                         "history keeps every row; nothing was edited")
        self.assertEqual(rows[-1]["verdict"]["time"], "on",
                         "the close grades against the LATEST declaration")

    def test_amend_without_a_declaration_is_refused(self):
        code, text, _ = self.run_ledger("amend", "r1", "L1", "--minutes", "10",
                                        "--tokens", "10")
        self.assertEqual(code, 1, text)
        self.assertIn("never declared", text)

    def test_double_close_is_refused(self):
        self.declare()
        code, text, _ = self.run_ledger("close", "r1", "L1", "--minutes", "50",
                                        "--why", "first close stands")
        self.assertEqual(code, 0, text)
        code, text, _ = self.run_ledger("close", "r1", "L1", "--minutes", "10",
                                        "--why", "trying to look better")
        self.assertEqual(code, 1, text)
        self.assertIn("already closed", text)
        self.assertEqual(len([r for r in self.lines() if r["kind"] == "close"]), 1)

    def test_a_corrupt_line_stops_the_tool_instead_of_being_skipped(self):
        self.declare()
        with io.open(self.path, "a") as fh:
            fh.write("{this is not json\n")
        code, text, _ = self.run_ledger("report")
        self.assertNotEqual(code, 0, text)
        self.assertIn("does not parse", text)


class TestGrading(LedgerCase):
    def test_on_better_and_worse_land_on_the_named_band(self):
        cases = (("L-on", 100, 110, "on"),        # +10% inside the 25% band
                 ("L-better", 100, 60, "better"),  # -40%
                 ("L-worse", 100, 200, "worse"))   # +100%
        for loop, est, actual, want in cases:
            self.declare(loop=loop, minutes=est, tokens=1000)
            code, text, _ = self.run_ledger("close", "r1", loop, "--minutes",
                                            str(actual), "--why",
                                            "constructed grading case")
            self.assertEqual(code, 0, text)
            row = [r for r in self.lines()
                   if r["kind"] == "close" and r["loop"] == loop][0]
            self.assertEqual(row["verdict"]["time"], want,
                             "est %d actual %d must grade %s" % (est, actual, want))

    def test_absent_token_actuals_read_no_data_never_a_number(self):
        self.declare()
        code, text, _ = self.run_ledger("close", "r1", "L1", "--minutes", "50",
                                        "--why", "tokens were not measurable here")
        self.assertEqual(code, 0, text)
        row = self.lines()[-1]
        self.assertEqual(row["verdict"]["tokens"], "NO-DATA")
        self.assertIsNone(row["actualTokens"])
        self.assertEqual(row["verdict"]["overall"], "on",
                         "overall grades from the dimensions that HAVE data")

    def test_supplied_tokens_without_a_basis_are_refused(self):
        self.declare()
        code, text, _ = self.run_ledger("close", "r1", "L1", "--minutes", "50",
                                        "--tokens", "400000", "--why",
                                        "forgot to label the count")
        self.assertEqual(code, 1, text)
        self.assertIn("--basis", text)
        self.assertEqual([r["kind"] for r in self.lines()], ["declare"])

    def test_a_close_needs_a_real_why_sentence(self):
        self.declare()
        code, text, _ = self.run_ledger("close", "r1", "L1", "--minutes", "50",
                                        "--why", "done")
        self.assertEqual(code, 1, text)
        self.assertIn("why", text.lower())


class TestReport(LedgerCase):
    def test_report_states_bias_from_the_rows_and_no_data_when_empty(self):
        code, text, _ = self.run_ledger("report")
        self.assertEqual(code, 0)
        self.assertIn("NO-DATA", text)
        self.assertIn("not a clean bill", text)
        self.declare(loop="A", minutes=100, tokens=100000)
        self.declare(loop="B", minutes=100, tokens=100000)
        self.run_ledger("close", "r1", "A", "--minutes", "150", "--why",
                        "ran half again as long")
        self.run_ledger("close", "r1", "B", "--minutes", "90", "--tokens",
                        "50000", "--basis", "measured", "--why",
                        "engine was faster than the baseline")
        code, text, _ = self.run_ledger("report")
        self.assertEqual(code, 0, text)
        self.assertIn("2 closed loop(s)", text)
        self.assertIn("time bias +20%", text,
                      "(+50% and -10%) must average to +20, computed not vibed")
        self.assertIn("1 on", text,
                      "loop B is on for time and better for tokens; overall takes "
                      "the WORST graded dimension, so it reads on")
        self.assertIn("1 worse", text)
        self.assertIn("token bias -50%", text)


class RedactionOnTheWayToDisk(LedgerCase):
    """ADDED 2026-07-31 when this tool was landed into the public repository.

    The repository's pre-write gate refused the file: "bm_ledger.py writes
    files but is not in the reviewed inventory. Review whether every text it
    writes passes through redaction." The review found that it did NOT, while
    all ten sibling tools already in that inventory did. This tool stores a
    founder-written `why` sentence and a loop name, which is precisely the
    shape a secret gets pasted into.

    Asserted at the BYTES on disk rather than at any display path, because a
    masking display path hiding a leaking storage column is a defect this
    project has already shipped once."""

    def _bytes(self):
        if not os.path.exists(self.path):
            return ""
        with io.open(self.path, encoding="utf-8") as fh:
            return fh.read()

    def test_a_secret_in_the_loop_name_never_reaches_the_file(self):
        secret = "sk-ABCDEF1234567890abcdefGHIJ"
        code, combined, _ = self.run_ledger(
            "declare", "--run", "r1", "--loop",
            "rebuild auth using %s today" % secret,
            "--minutes", "10", "--tokens", "1000")
        self.assertEqual(code, 0, combined)
        self.assertNotIn(secret, self._bytes(),
                         "the ledger stored a secret-shaped value verbatim")

    def test_a_secret_in_the_why_sentence_never_reaches_the_file(self):
        secret = "sk-ZYXWVU9876543210zyxwvuTSRQ"
        code, combined, _ = self.run_ledger(
            "declare", "--run", "r2", "--loop", "loop-b",
            "--minutes", "10", "--tokens", "1000")
        self.assertEqual(code, 0, combined)
        code, combined, _ = self.run_ledger(
            "close", "--run", "r2", "--loop", "loop-b", "--minutes", "12",
            "--why", "slow because %s was wrong" % secret)
        self.assertEqual(code, 0, combined)
        self.assertNotIn(secret, self._bytes(),
                         "the why sentence is founder prose and reached disk raw")

    def test_calibrated_removing_the_scrub_step_lets_the_secret_through(self):
        """The green above means nothing unless it can go red. Bypass the scrub
        exactly as this file did before it was landed, and confirm the secret
        does reach disk."""
        if HERE not in sys.path:
            sys.path.insert(0, HERE)
        import bm_ledger
        secret = "sk-CALIBRATION0000000000000000"
        original_scrub, original_vault = bm_ledger._scrub_row, bm_ledger.VAULT
        bm_ledger._scrub_row = lambda row: row
        bm_ledger.VAULT = self.vault
        try:
            bm_ledger._append({"kind": "declare", "run": "cal",
                               "loop": "loop with %s inside" % secret,
                               "at": "2026-07-31T00:00:00Z"})
        finally:
            bm_ledger._scrub_row = original_scrub
            bm_ledger.VAULT = original_vault
        self.assertIn(secret, self._bytes(),
                      "the pre-fix behaviour no longer reproduces, so the two "
                      "tests above are not proving what they claim")


if __name__ == "__main__":
    unittest.main(verbosity=2)
