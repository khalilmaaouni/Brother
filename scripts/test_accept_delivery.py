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

# E100: one sandbox for every temp tree this process makes, removed at exit.
import os as _e100_os, sys as _e100_sys  # noqa: E402
_e100_sys.path.append(_e100_os.path.join(
    _e100_os.path.dirname(_e100_os.path.abspath(__file__)), '.'))
try:  # noqa: E402
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    # A packager (scripts/export_public.py, make_benchmark_bundle.py)
    # can copy this test without scripts/tmp_sandbox.py beside it. Say
    # so rather than dying: the sandbox is hygiene, not the subject.
    _e100_sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % _e100_os.path.basename(__file__))


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

    def test_a_record_with_no_receipt_reads_NO_DATA_in_its_checks_field(self):
        """E94: omitting the field made a record that proves nothing the
        same shape on disk as one that never claimed to, which is exactly
        how the shipped record read. NO-DATA with its reason, never an
        absent key and never an empty list."""
        ok, path = ad.record("no receipt was given", "sha-no-receipt",
                             "Khalil Maaouni", "2026-09-04", "person",
                             directory=self.tmp)
        self.assertTrue(ok, path)
        with open(path, encoding="utf-8") as fh:
            entry = json.load(fh)
        self.assertEqual(entry["checks"], "NO-DATA")
        self.assertIn("--run-dir", entry["checks_reason"])
        self.assertNotIn("run", entry)

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


class ChecksDerivedFromARunDirectory(unittest.TestCase):
    """BO2, closing the delivery-proof skeptic's first defect: the shipped
    record named no changed file and no check because building that list
    meant hand-writing JSON. --run-dir reads the two files a completed run
    already wrote and derives the list from them."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="accept-delivery-rundir-")
        self.run = os.path.join(self.tmp, "run")
        os.makedirs(self.run)
        self.write_run()

    def write_run(self, work=True, claims=True):
        if work:
            with open(os.path.join(self.run, "W-toy.json"), "w",
                      encoding="utf-8") as fh:
                json.dump({
                    "work_id": "W-toy",
                    "outcome": "guard non-numeric input",
                    "rows": [{
                        "id": "guard",
                        "title": "raise TypeError on non-numeric input",
                        "done_check": "python3 -m pytest test_mathlib.py -q",
                        "owns": ["mathlib.py"],
                        "depends_on": [],
                        "status": "DONE",
                        "check_passed_before": False,
                        "files_changed_by_unit": ["mathlib.py"],
                    }],
                }, fh)
        if claims:
            with open(os.path.join(self.run, "claims.json"), "w",
                      encoding="utf-8") as fh:
                json.dump({"guard": {
                    "state": "done", "unit_id": "guard",
                    "evidence": {
                        "canonical_rev": "da88480d731ddfa3cb2862066d56a43e",
                        "check_command": "python3 -m pytest test_mathlib.py -q",
                        "exit_code": 0, "output": "1 passed",
                        "output_truncated": False,
                    }}}, fh)

    def test_the_derived_list_names_the_changed_file_and_its_check(self):
        checks, reason = ad.checks_from_run_dir(self.run)
        self.assertEqual(reason, "")
        self.assertEqual([c["file"] for c in checks], ["mathlib.py"])
        self.assertEqual(checks[0]["check_command"],
                         "python3 -m pytest test_mathlib.py -q")
        self.assertEqual(checks[0]["exit_code"], 0)

    def test_a_run_directory_with_no_work_document_is_refused(self):
        os.remove(os.path.join(self.run, "W-toy.json"))
        checks, reason = ad.checks_from_run_dir(self.run)
        self.assertIsNone(checks)
        self.assertIn("W-*.json", reason)

    def test_a_run_directory_with_no_claims_is_refused(self):
        os.remove(os.path.join(self.run, "claims.json"))
        checks, reason = ad.checks_from_run_dir(self.run)
        self.assertIsNone(checks)
        self.assertIn("claims.json", reason)

    def test_a_directory_that_is_not_there_is_refused(self):
        checks, reason = ad.checks_from_run_dir(
            os.path.join(self.tmp, "nowhere"))
        self.assertIsNone(checks)
        self.assertIn("not a directory", reason)

    def test_the_cli_writes_the_derived_checks_into_the_record(self):
        out = os.path.join(self.tmp, "deliveries")
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = ad.main([
                "--name", "toy delivery", "--ref", "toy-1",
                "--accepted-by", "Khalil Maaouni",
                "--accepted-at", "2026-09-04", "--recorded-by", "person",
                "--run-dir", self.run, "--dir", out,
                "--pattern-root", os.path.join(self.tmp, "vault")])
        self.assertEqual(code, 0, buf.getvalue())
        with open(ad.record_path("toy-1", out), encoding="utf-8") as fh:
            entry = json.load(fh)
        self.assertEqual([c["file"] for c in entry["checks"]], ["mathlib.py"])

    def test_the_record_names_the_run_by_identity_not_only_a_local_path(self):
        """E94: a path is not an identity. A record written from a run
        carries that run's id and a digest over the receipt bytes its
        checks were read from, and says whether the path beside them is one
        a reader of this repository can open."""
        out = os.path.join(self.tmp, "deliveries-identity")
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = ad.main([
                "--name", "toy delivery", "--ref", "toy-identity",
                "--accepted-by", "Khalil Maaouni",
                "--accepted-at", "2026-09-04", "--recorded-by", "person",
                "--run-dir", self.run, "--dir", out,
                "--pattern-root", os.path.join(self.tmp, "vault")])
        self.assertEqual(code, 0, buf.getvalue())
        with open(ad.record_path("toy-identity", out), encoding="utf-8") as fh:
            entry = json.load(fh)
        run = entry["run"]
        self.assertEqual(run["run_id"], os.path.basename(self.run))
        self.assertTrue(run["receipt_digest"].startswith("sha256:"), run)
        self.assertEqual(len(run["receipt_digest"]), len("sha256:") + 64)
        self.assertEqual(run["receipt_files"],
                         ["W-toy.json", "claims.json"])
        self.assertFalse(run["run_dir_in_repository"])
        for check in entry["checks"]:
            self.assertEqual(check["output_location_scope"], "machine-local")

    def test_the_digest_changes_when_the_receipt_bytes_change(self):
        """A digest that does not move when the receipt moves identifies
        nothing. Drive it backwards: same directory, different claims."""
        first, reason = ad.run_identity(self.run)
        self.assertEqual(reason, "")
        with open(os.path.join(self.run, "claims.json"), encoding="utf-8") as fh:
            claims = json.load(fh)
        claims["guard"]["evidence"]["exit_code"] = 1
        with open(os.path.join(self.run, "claims.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(claims, fh)
        second, reason = ad.run_identity(self.run)
        self.assertEqual(reason, "")
        self.assertNotEqual(first["receipt_digest"], second["receipt_digest"])

    def test_every_changed_file_carries_a_command_and_an_exit_code(self):
        checks, reason = ad.checks_from_run_dir(self.run)
        self.assertEqual(reason, "")
        self.assertTrue(checks)
        for check in checks:
            self.assertTrue(str(check.get("file") or "").strip(), check)
            self.assertTrue(str(check.get("check_command") or "").strip(), check)
            self.assertIn("exit_code", check)

    def test_both_sources_of_checks_at_once_is_refused(self):
        buf = io.StringIO()
        with redirect_stderr(buf), self.assertRaises(SystemExit):
            ad.main([
                "--name", "toy", "--ref", "toy-2",
                "--accepted-by", "Khalil Maaouni",
                "--accepted-at", "2026-09-04", "--recorded-by", "person",
                "--run-dir", self.run, "--checks-file", "whatever.json",
                "--dir", self.tmp])
        self.assertIn("pass one, never both", buf.getvalue())


class EveryShippedDeliveryRecordCarriesItsPerFileChecks(unittest.TestCase):
    """The record the 2026-09-04 delivery-proof skeptic opened in a fresh
    clone of v1.0.1 named no file and no command, so its chain of proof
    could not be followed one step. This reads the records this repository
    really ships, not a fixture, so it goes red the moment one is written
    without them again."""

    def test_each_record_under_docs_deliveries_passes_the_per_file_gate(self):
        entries = ad.load_all()
        self.assertTrue(entries, "docs/deliveries carries no record at all")
        for entry in entries:
            checks = entry.get("checks")
            self.assertIsNotNone(
                checks,
                "the shipped delivery record %r carries no 'checks': a "
                "reader cannot re-run anything it claims. Record it with "
                "--run-dir or --checks-file." % entry.get("ref"))
            ok, reason = ad.receipt_door.require_per_file_checks(checks)
            self.assertTrue(ok, "%s: %s" % (entry.get("ref"), reason))

    def test_no_shipped_record_cites_a_path_without_naming_its_run(self):
        """E94: the shipped record's only pointer at its evidence was a run
        directory under one machine's home, which no reader of this
        repository can open. A record whose checks cite a machine-local
        path must also carry the run's own identity."""
        for entry in ad.load_all():
            checks = entry.get("checks")
            if not isinstance(checks, list):
                continue
            local = [c for c in checks
                     if c.get("output_location_scope") != "in-repository"]
            if not local:
                continue
            run = entry.get("run")
            self.assertIsNotNone(
                run,
                "the shipped delivery record %r cites a run directory a "
                "reader cannot open and names no run id or receipt digest "
                "to identify the run by" % entry.get("ref"))
            self.assertTrue(str(run.get("run_id") or "").strip(), run)
            self.assertTrue(
                str(run.get("receipt_digest") or "").startswith("sha256:"),
                run)

    def test_each_check_names_a_file_and_a_command_a_stranger_could_run(self):
        for entry in ad.load_all():
            for check in entry.get("checks") or []:
                self.assertTrue(str(check.get("file") or "").strip(), check)
                self.assertTrue(
                    str(check.get("check_command") or "").strip(), check)


if __name__ == "__main__":
    unittest.main()
