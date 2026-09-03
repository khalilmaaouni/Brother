"""Calibration for scripts/accept_delivery.py, driven backwards.

The line this tool must never cross is that it records a human's acceptance
and never generates one, so every test here either proves a refusal (no
accepted_by, no accepted_at, a duplicate ref) or proves a real round trip
through the files on disk, never through an in-memory shortcut.
"""
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS)
import accept_delivery as ad  # noqa: E402
import pattern_note as P  # noqa: E402


class RecordFunction(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="accept-delivery-test-")

    def test_a_valid_acceptance_round_trips(self):
        ok, path = ad.record("H2 acceptance seam", "khalilmaaouni/Brother#61",
                             "Khalil Maaouni", "2026-08-30", "person",
                             words="looks right", directory=self.tmp)
        self.assertTrue(ok, path)
        with open(path, encoding="utf-8") as fh:
            entry = json.load(fh)
        self.assertEqual(entry["name"], "H2 acceptance seam")
        self.assertEqual(entry["ref"], "khalilmaaouni/Brother#61")
        self.assertEqual(entry["accepted_by"], "Khalil Maaouni")
        self.assertEqual(entry["accepted_at"], "2026-08-30")
        self.assertEqual(entry["recorded_by"], "person")
        self.assertNotIn("delegation", entry)
        self.assertEqual(entry["words"], "looks right")

    def test_words_is_optional_and_omitted_when_blank(self):
        ok, path = ad.record("no words given", "sha-abc123", "Khalil Maaouni",
                             "2026-08-30", "person", directory=self.tmp)
        self.assertTrue(ok)
        with open(path, encoding="utf-8") as fh:
            entry = json.load(fh)
        self.assertNotIn("words", entry)

    def test_a_duplicate_ref_is_refused_not_overwritten(self):
        ok1, path1 = ad.record("first pass", "khalilmaaouni/Brother#99",
                               "Khalil Maaouni", "2026-08-30", "person",
                               directory=self.tmp)
        self.assertTrue(ok1)
        ok2, reason = ad.record("a different name entirely, same delivery",
                                "khalilmaaouni/Brother#99", "Someone Else",
                                "2026-08-31", "person", directory=self.tmp)
        self.assertFalse(ok2)
        self.assertIn("already accepted", reason)
        with open(path1, encoding="utf-8") as fh:
            entry = json.load(fh)
        self.assertEqual(entry["accepted_by"], "Khalil Maaouni",
                         "the first record must survive a refused duplicate")

    def test_an_unparsable_accepted_at_is_refused(self):
        ok, reason = ad.record("bad date", "sha-def456", "Khalil Maaouni",
                               "not-a-date", "person", directory=self.tmp)
        self.assertFalse(ok)
        self.assertIn("not a valid ISO date", reason)
        self.assertEqual(os.listdir(self.tmp), [],
                         "a refused record must leave no file behind")

    def test_recorded_by_must_be_person_or_agent(self):
        ok, reason = ad.record("bad shape", "sha-ghi789", "Khalil Maaouni",
                               "2026-08-30", "robot", directory=self.tmp)
        self.assertFalse(ok)
        self.assertIn("must be 'person' or 'agent'", reason)
        self.assertEqual(os.listdir(self.tmp), [])

    def test_agent_shape_requires_a_delegation_sentence(self):
        ok, reason = ad.record("agent, no delegation given", "sha-jkl012",
                               "Khalil Maaouni", "2026-08-30", "agent",
                               directory=self.tmp)
        self.assertFalse(ok)
        self.assertIn("requires --delegation", reason)
        self.assertEqual(os.listdir(self.tmp), [])

    def test_agent_shape_with_a_delegation_sentence_round_trips(self):
        ok, path = ad.record("agent, delegated", "sha-mno345",
                             "Khalil Maaouni", "2026-08-30", "agent",
                             delegation="fable, go ahead and take all actions "
                                       "on my behalf", directory=self.tmp)
        self.assertTrue(ok, path)
        with open(path, encoding="utf-8") as fh:
            entry = json.load(fh)
        self.assertEqual(entry["recorded_by"], "agent")
        self.assertEqual(entry["delegation"],
                         "fable, go ahead and take all actions on my behalf")


class PerWeek(unittest.TestCase):
    def test_two_weeks_of_records_count_separately(self):
        entries = [
            {"accepted_at": "2026-08-24"},  # ISO week 35, Monday
            {"accepted_at": "2026-08-25"},  # same week
            {"accepted_at": "2026-08-26"},  # same week
            {"accepted_at": "2026-09-01"},  # ISO week 36
        ]
        counts = ad.per_week(entries)
        self.assertEqual(counts[(2026, 35)], 3)
        self.assertEqual(counts[(2026, 36)], 1)
        self.assertEqual(sum(counts.values()), 4)

    def test_a_record_with_no_accepted_at_is_skipped_not_crashed_on(self):
        counts = ad.per_week([{"ref": "x"}])
        self.assertEqual(counts, {})


class CLI(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="accept-delivery-cli-")
        self.proot = pattern_root()

    def run_main(self, argv):
        # A person-recorded acceptance now has a pattern side effect (roadmap
        # learning_loop n=3): every argv here gets a temp --pattern-root so a
        # test of the acceptance record never also writes into the real
        # Kay Vault, unless a test supplies its own.
        if "--pattern-root" not in argv:
            argv = list(argv) + ["--pattern-root", self.proot]
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = ad.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_missing_accepted_by_is_refused_at_exit_2(self):
        with self.assertRaises(SystemExit) as ctx:
            with redirect_stderr(io.StringIO()):
                ad.main(["--name", "x", "--ref", "sha-1",
                        "--accepted-at", "2026-08-30", "--recorded-by", "person",
                        "--dir", self.tmp])
        self.assertEqual(ctx.exception.code, 2)
        self.assertEqual(os.listdir(self.tmp), [],
                         "a refused call must never write a file")

    def test_missing_accepted_at_is_refused_at_exit_2(self):
        with self.assertRaises(SystemExit) as ctx:
            with redirect_stderr(io.StringIO()):
                ad.main(["--name", "x", "--ref", "sha-1",
                        "--accepted-by", "Khalil Maaouni", "--recorded-by", "person",
                        "--dir", self.tmp])
        self.assertEqual(ctx.exception.code, 2)

    def test_missing_recorded_by_is_refused_at_exit_2(self):
        with self.assertRaises(SystemExit) as ctx:
            with redirect_stderr(io.StringIO()):
                ad.main(["--name", "x", "--ref", "sha-1",
                        "--accepted-by", "Khalil Maaouni",
                        "--accepted-at", "2026-08-30", "--dir", self.tmp])
        self.assertEqual(ctx.exception.code, 2)
        self.assertEqual(os.listdir(self.tmp), [],
                         "a refused call must never write a file")

    def test_agent_recorded_by_without_delegation_is_refused_at_exit_2(self):
        with self.assertRaises(SystemExit) as ctx:
            with redirect_stderr(io.StringIO()):
                ad.main(["--name", "x", "--ref", "sha-1",
                        "--accepted-by", "Khalil Maaouni",
                        "--accepted-at", "2026-08-30",
                        "--recorded-by", "agent", "--dir", self.tmp])
        self.assertEqual(ctx.exception.code, 2)
        self.assertEqual(os.listdir(self.tmp), [])

    def test_list_on_an_empty_directory_is_NO_DATA_not_zero(self):
        code, out, _ = self.run_main(["--list", "--dir", self.tmp])
        self.assertEqual(code, 2)
        self.assertIn("NO-DATA", out)
        self.assertNotRegex(out, r"^\s*0\s*$")

    def test_a_recorded_acceptance_appears_in_list_with_its_week(self):
        code, _, _ = self.run_main(
            ["--name", "H2 acceptance seam", "--ref", "khalilmaaouni/Brother#61",
            "--accepted-by", "Khalil Maaouni", "--accepted-at", "2026-08-30",
            "--recorded-by", "person",
            "--words", "ships the human decision node", "--dir", self.tmp])
        self.assertEqual(code, 0)

        code, out, _ = self.run_main(["--list", "--dir", self.tmp])
        self.assertEqual(code, 0)
        self.assertIn("khalilmaaouni/Brother#61", out)
        self.assertIn("Khalil Maaouni", out)
        self.assertIn("H2 acceptance seam", out)
        self.assertIn("2026-W35", out)

    def test_a_duplicate_via_the_cli_exits_2_and_names_it(self):
        argv = ["--name", "x", "--ref", "sha-dup", "--accepted-by",
               "Khalil Maaouni", "--accepted-at", "2026-08-30",
               "--recorded-by", "person", "--dir", self.tmp]
        code1, _, _ = self.run_main(argv)
        self.assertEqual(code1, 0)
        code2, _, err2 = self.run_main(argv)
        self.assertEqual(code2, 2)
        self.assertIn("already accepted", err2)

    def test_an_agent_recorded_acceptance_is_shown_but_not_counted(self):
        """The count rule (row E49): a week's acceptance count is over
        person-recorded entries only. One agent record and one person record
        land in the same week; the count must read 1, not 2, and the agent
        record must name itself on its own line."""
        code, _, _ = self.run_main(
            ["--name", "agent one", "--ref", "sha-agent-1",
            "--accepted-by", "Khalil Maaouni", "--accepted-at", "2026-08-30",
            "--recorded-by", "agent",
            "--delegation", "fable, go ahead and take all actions on my behalf",
            "--dir", self.tmp])
        self.assertEqual(code, 0)
        code, _, _ = self.run_main(
            ["--name", "person one", "--ref", "sha-person-1",
            "--accepted-by", "Khalil Maaouni", "--accepted-at", "2026-08-31",
            "--recorded-by", "person", "--dir", self.tmp])
        self.assertEqual(code, 0)

        code, out, _ = self.run_main(["--list", "--dir", self.tmp])
        self.assertEqual(code, 0)
        self.assertIn("recorded by an agent under delegation, not counted", out)
        self.assertIn("week 2026-W36: 1 accepted", out)

    def test_a_legacy_record_with_no_recorded_by_field_reads_as_no_data(self):
        """A record written before row E49 has no recorded_by key at all;
        --list must say so plainly and never count it, never guess it."""
        legacy = {"name": "pre-E49 record", "ref": "sha-legacy",
                  "accepted_by": "Khalil Maaouni", "accepted_at": "2026-08-20"}
        with open(os.path.join(self.tmp, "sha-legacy.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(legacy, fh)

        code, out, _ = self.run_main(["--list", "--dir", self.tmp])
        self.assertEqual(code, 0)
        self.assertIn("recorded_by: NO-DATA (record predates the field)", out)
        self.assertIn("week count 0", out)


def pattern_root():
    """A temp vault root with its 50-Reference folder already present, so a
    pattern write against it succeeds rather than reading NO-DATA."""
    d = tempfile.mkdtemp(prefix="accept-delivery-pattern-root-")
    os.makedirs(os.path.join(d, P.FOLDER))
    return d


class PatternSideEffectOfAPersonRecordedAcceptance(unittest.TestCase):
    """The other half of learning_loop item n=3: a mechanical good outcome
    (a person accepting a delivery) now feeds the pattern store the same
    way a failure ceremony feeds the failures folder, without anybody
    typing a pattern command."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="accept-delivery-cli-")
        self.proot = pattern_root()

    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = ad.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_a_person_recorded_acceptance_writes_exactly_one_pattern(self):
        code, out, _ = self.run_main(
            ["--name", "H2 acceptance seam", "--ref", "khalilmaaouni/Brother#61",
            "--accepted-by", "Khalil Maaouni", "--accepted-at", "2026-08-30",
            "--recorded-by", "person",
            "--words", "Ships the human decision node cleanly.",
            "--dir", self.tmp, "--pattern-root", self.proot])
        self.assertEqual(code, 0)
        self.assertIn("pattern written:", out)
        written = [f for f in os.listdir(os.path.join(self.proot, P.FOLDER))
                  if f.endswith(".md") and f != P.INDEX]
        self.assertEqual(len(written), 1)

    def test_an_agent_recorded_acceptance_writes_no_pattern(self):
        code, out, _ = self.run_main(
            ["--name", "agent one", "--ref", "sha-agent-1",
            "--accepted-by", "Khalil Maaouni", "--accepted-at", "2026-08-30",
            "--recorded-by", "agent",
            "--delegation", "fable, go ahead and take all actions on my behalf",
            "--dir", self.tmp, "--pattern-root", self.proot])
        self.assertEqual(code, 0)
        self.assertNotIn("pattern written:", out)
        written = [f for f in os.listdir(os.path.join(self.proot, P.FOLDER))
                  if f.endswith(".md") and f != P.INDEX]
        self.assertEqual(written, [])

    def test_an_unwritable_pattern_root_is_NO_DATA_and_the_acceptance_still_records(self):
        unwritable_root = os.path.join(self.tmp, "no-such-vault")
        code, out, _ = self.run_main(
            ["--name", "no vault here", "--ref", "sha-no-vault",
            "--accepted-by", "Khalil Maaouni", "--accepted-at", "2026-08-30",
            "--recorded-by", "person", "--dir", self.tmp,
            "--pattern-root", unwritable_root])
        self.assertEqual(code, 0, "the acceptance must record even when the "
                                  "pattern store cannot")
        self.assertIn(ad.NODATA + ": no pattern written", out)
        self.assertTrue(os.path.isfile(
            os.path.join(self.tmp, "sha-no-vault.json")))

    def test_find_over_the_pattern_root_returns_the_written_pattern(self):
        code, _, _ = self.run_main(
            ["--name", "H2 acceptance seam", "--ref", "khalilmaaouni/Brother#77",
            "--accepted-by", "Khalil Maaouni", "--accepted-at", "2026-08-30",
            "--recorded-by", "person",
            "--words", "Ships the human decision node cleanly.",
            "--dir", self.tmp, "--pattern-root", self.proot])
        self.assertEqual(code, 0)
        hits = P.find("ships the human decision node cleanly", self.proot)
        self.assertTrue(hits)
        self.assertIn("brother-77", hits[0][1])


if __name__ == "__main__":
    unittest.main()
