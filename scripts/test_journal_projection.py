"""journal_projection.py, driven backwards where it matters: the report it
rebuilds from the journal must be byte-identical to the one build_report
produces from a real claims.json, must SURVIVE claims.json being deleted
(the whole point of row E60), and must show a real diff, not a false clean,
when the journal and claims.json actually disagree.

No engine, no network: this drives claim_store.acquire/release directly
against a temp run directory, the same seam test_claim_store.py already
uses, so claims.json and journal.jsonl are both the real files those
functions write rather than hand-typed fixtures.
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import board_status as B  # noqa: E402
import brother_run as _br  # noqa: E402
import claim_store  # noqa: E402
import journal  # noqa: E402
import journal_projection as JP  # noqa: E402


def _seed_two_unit_run(run_dir, u2_state="failed"):
    """U1 claimed and released done, U2 claimed and released `u2_state`,
    through the real claim_store (so claims.json and journal.jsonl are both
    the real writers' own output). U1's release carries the evidence
    receipt_door.receipts_for actually needs to mark it "verified" (a real
    exit code plus the row's own check_passed_before/files_changed_by_unit
    stamps, the same fields scripts/brother_run.py's own _mark_integrated
    and _stamp_prechecks write), so a test cross-checking receipts_bound's
    two branches is comparing a genuine verified count, not two branches
    agreeing on zero. Returns the Work document dict used."""
    os.makedirs(run_dir, exist_ok=True)
    claims_path = os.path.join(run_dir, "claims.json")
    claim, problem = claim_store.acquire(claims_path, "U1", "workerA")
    assert claim, problem
    claim_store.release(claims_path, "U1", "workerA", state="done",
                        evidence={"check_command":
                                 "python3 -m pytest tests/test_x.py",
                                 "exit_code": 0})
    claim, problem = claim_store.acquire(claims_path, "U2", "workerB")
    assert claim, problem
    claim_store.release(claims_path, "U2", "workerB", state=u2_state)
    record = {"outcome": "add retry", "work_id": "w1", "rows": [
        {"id": "U1", "done_check": "python3 -m pytest tests/test_x.py",
         "status": "DONE", "check_passed_before": False,
         "files_changed_by_unit": ["src/api.py"]},
        {"id": "U2", "done_check": "python3 a.py"}]}
    with open(os.path.join(run_dir, "W-w1.json"), "w", encoding="utf-8") as fh:
        json.dump(record, fh)
    return record


class ClaimsFromJournal(unittest.TestCase):
    """The one field build_report actually reads off `claims`: state."""

    def test_a_released_claim_carries_its_ending_state(self):
        events = [{"type": "claim.acquired", "unit_id": "U1",
                  "payload": {"attempt": 1}},
                 {"type": "claim.released", "unit_id": "U1",
                  "payload": {"state": "done", "attempt": 1}}]
        claims = JP.claims_from_journal(events)
        self.assertEqual(claims, {"U1": {"state": "done", "attempt": 1}})

    def test_an_acquired_but_never_released_claim_stays_claimed(self):
        """An abandoned mid-run claim: claim_store.acquire()'s own initial
        stamp is state "claimed", and nothing released it."""
        events = [{"type": "claim.acquired", "unit_id": "U1",
                  "payload": {"attempt": 1}}]
        claims = JP.claims_from_journal(events)
        self.assertEqual(claims["U1"]["state"], "claimed")

    def test_a_resumed_reclaim_keeps_the_latest_release(self):
        events = [
            {"type": "claim.acquired", "unit_id": "U1", "payload": {"attempt": 1}},
            {"type": "claim.released", "unit_id": "U1",
             "payload": {"state": "failed", "attempt": 1}},
            {"type": "claim.acquired", "unit_id": "U1", "payload": {"attempt": 2}},
            {"type": "claim.released", "unit_id": "U1",
             "payload": {"state": "done", "attempt": 2}},
        ]
        claims = JP.claims_from_journal(events)
        self.assertEqual(claims["U1"], {"state": "done", "attempt": 2})

    def test_a_unit_with_no_claim_event_is_absent(self):
        self.assertEqual(JP.claims_from_journal(
            [{"type": "run.opened", "unit_id": None, "payload": {}}]), {})

    def test_no_events_at_all_is_an_empty_dict_not_an_error(self):
        self.assertEqual(JP.claims_from_journal([]), {})
        self.assertEqual(JP.claims_from_journal(None), {})


class BuildReportFromJournal(unittest.TestCase):
    """The row's own contract: rebuilt from the journal, byte for byte the
    same as the report claims.json would have produced."""

    def test_identical_to_the_real_report_with_claims_json_present(self):
        with tempfile.TemporaryDirectory() as d:
            run_dir = os.path.join(d, "20260903T000000-w1")
            record = _seed_two_unit_run(run_dir)
            with open(os.path.join(run_dir, "claims.json"),
                     encoding="utf-8") as fh:
                real_claims = json.load(fh)
            real_report, real_integ, real_ref = _br.build_report(
                record, real_claims, "abc123", "def456",
                changed=["src/api.py", "tests/test_x.py"])
            proj_report, proj_integ, proj_ref = JP.build_report_from_journal(
                record, run_dir, "abc123", "def456",
                changed=["src/api.py", "tests/test_x.py"])
            self.assertEqual(real_report, proj_report)
            self.assertEqual(real_integ, proj_integ)
            self.assertEqual(real_ref, proj_ref)
            self.assertIn("files changed (2): src/api.py, tests/test_x.py",
                          proj_report)

    def test_the_same_report_rebuilds_after_claims_json_is_deleted(self):
        """THE POINT OF THIS ROW: the journal is not a copy of claims.json
        that happens to agree with it once; it is a second, independent
        source for the one fact build_report needs, and deleting the first
        source must not change the second's answer."""
        with tempfile.TemporaryDirectory() as d:
            run_dir = os.path.join(d, "20260903T000000-w1")
            record = _seed_two_unit_run(run_dir)
            before_report, before_integ, before_ref = JP.build_report_from_journal(
                record, run_dir, "abc123", "def456",
                changed=["src/api.py", "tests/test_x.py"])
            os.remove(os.path.join(run_dir, "claims.json"))
            self.assertFalse(os.path.exists(os.path.join(run_dir, "claims.json")))
            after_report, after_integ, after_ref = JP.build_report_from_journal(
                record, run_dir, "abc123", "def456",
                changed=["src/api.py", "tests/test_x.py"])
            self.assertEqual(before_report, after_report)
            self.assertEqual(before_integ, after_integ)
            self.assertEqual(before_ref, after_ref)
            self.assertIn("refused (1):", after_report)

    def test_a_run_with_no_journal_at_all_rebuilds_as_never_claimed(self):
        """No journal.jsonl written (a run from before row E59): claims
        rebuild as {}, the same starting point an empty claims.json gives."""
        with tempfile.TemporaryDirectory() as run_dir:
            record = {"outcome": "x", "work_id": "w1",
                     "rows": [{"id": "U1", "done_check": "true"}]}
            report, integ, refused = JP.build_report_from_journal(
                record, run_dir, "abc", "def", changed=[])
            self.assertEqual(integ, [])
            self.assertIn("never started this run", dict(refused)["U1"])


class LastEventPerUnit(unittest.TestCase):
    def test_no_journal_at_all_is_none_not_data(self):
        with tempfile.TemporaryDirectory() as run_dir:
            self.assertIsNone(JP.last_event_per_unit(run_dir))

    def test_an_empty_journal_is_an_empty_dict(self):
        with tempfile.TemporaryDirectory() as run_dir:
            open(os.path.join(run_dir, journal.JOURNAL_FILENAME), "w").close()
            self.assertEqual(JP.last_event_per_unit(run_dir), {})

    def test_only_the_latest_event_per_unit_survives_the_fold(self):
        with tempfile.TemporaryDirectory() as run_dir:
            journal.append(run_dir, "claim.acquired", unit_id="U1",
                           payload={"attempt": 1})
            journal.append(run_dir, "attempt.traced", unit_id="U1",
                           payload={"i": 1})
            second = journal.append(run_dir, "unit.done", unit_id="U1",
                                    payload={"files_changed": 2})
            journal.append(run_dir, "claim.acquired", unit_id="U2",
                           payload={"attempt": 1})
            last = JP.last_event_per_unit(run_dir)
            self.assertEqual(set(last), {"U1", "U2"})
            self.assertEqual(last["U1"]["type"], "unit.done")
            self.assertEqual(last["U1"]["event_id"], second)
            self.assertEqual(last["U2"]["type"], "claim.acquired")


class DiffReportForRun(unittest.TestCase):
    def test_no_journal_reads_no_data_naming_the_reason(self):
        with tempfile.TemporaryDirectory() as run_dir:
            with open(os.path.join(run_dir, "W-w1.json"), "w",
                     encoding="utf-8") as fh:
                json.dump({"rows": []}, fh)
            with open(os.path.join(run_dir, "claims.json"), "w",
                     encoding="utf-8") as fh:
                json.dump({}, fh)
            status, detail = JP.diff_report_for_run(run_dir)
            self.assertEqual(status, "no-data")
            self.assertIn("journal.jsonl", detail)

    def test_no_claims_json_reads_no_data(self):
        with tempfile.TemporaryDirectory() as d:
            run_dir = os.path.join(d, "run1")
            _seed_two_unit_run(run_dir)
            os.remove(os.path.join(run_dir, "claims.json"))
            status, detail = JP.diff_report_for_run(run_dir)
            self.assertEqual(status, "no-data")
            self.assertIn("claims.json", detail)

    def test_no_work_document_reads_no_data(self):
        with tempfile.TemporaryDirectory() as d:
            run_dir = os.path.join(d, "run1")
            _seed_two_unit_run(run_dir)
            os.remove(os.path.join(run_dir, "W-w1.json"))
            status, detail = JP.diff_report_for_run(run_dir)
            self.assertEqual(status, "no-data")
            self.assertIn("W-*.json", detail)

    def test_an_agreeing_run_is_clean(self):
        with tempfile.TemporaryDirectory() as d:
            run_dir = os.path.join(d, "run1")
            _seed_two_unit_run(run_dir)
            status, detail = JP.diff_report_for_run(run_dir)
            self.assertEqual((status, detail), ("clean", ""))

    def test_a_tampered_journal_shows_a_real_diff(self):
        """Append a SECOND release for U2 directly (bypassing claim_store,
        the way a corrupted or hand-edited journal would), so the
        journal's own claim state for U2 disagrees with claims.json's:
        the diff must say so, never read clean."""
        with tempfile.TemporaryDirectory() as d:
            run_dir = os.path.join(d, "run1")
            _seed_two_unit_run(run_dir)
            journal.append(run_dir, "claim.released", unit_id="U2",
                           payload={"owner": "someone-else", "state": "done",
                                    "attempt": 9})
            status, detail = JP.diff_report_for_run(run_dir)
            self.assertEqual(status, "diff")
            self.assertTrue(detail)


class ReceiptsBoundFromJournal(unittest.TestCase):
    def _seed_receipt_event(self, run_dir, verified, receipts=None):
        os.makedirs(run_dir, exist_ok=True)
        journal.append(run_dir, "receipt.issued",
                       payload={"receipts": receipts if receipts is not None
                               else verified, "unproven": 0,
                               "verified": verified})

    def test_sums_the_last_receipt_issued_event_per_run(self):
        with tempfile.TemporaryDirectory() as d:
            self._seed_receipt_event(os.path.join(d, "run1"), 2)
            self._seed_receipt_event(os.path.join(d, "run2"), 1)
            count, _cmd, err = B.receipts_bound(runs_root=d, from_journal=True)
            self.assertIsNone(err)
            self.assertEqual(count, 3)

    def test_only_the_last_receipt_issued_event_counts_not_every_call(self):
        with tempfile.TemporaryDirectory() as d:
            run_dir = os.path.join(d, "run1")
            os.makedirs(run_dir)
            journal.append(run_dir, "receipt.issued",
                           payload={"receipts": 2, "unproven": 2,
                                    "verified": 0})
            journal.append(run_dir, "receipt.issued",
                           payload={"receipts": 2, "unproven": 0,
                                    "verified": 2})
            count, _cmd, err = B.receipts_bound(runs_root=d, from_journal=True)
            self.assertIsNone(err)
            self.assertEqual(count, 2)

    def test_a_run_with_no_journal_contributes_zero_not_an_error(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "run-no-journal"))
            self._seed_receipt_event(os.path.join(d, "run-with-journal"), 5)
            count, _cmd, err = B.receipts_bound(runs_root=d, from_journal=True)
            self.assertIsNone(err)
            self.assertEqual(count, 5)

    def test_a_journal_predating_the_verified_field_contributes_zero(self):
        """E59's own journals never wrote "verified"; a run whose last
        receipt.issued has no such key must not crash or fabricate a count."""
        with tempfile.TemporaryDirectory() as d:
            run_dir = os.path.join(d, "run-old-format")
            os.makedirs(run_dir)
            journal.append(run_dir, "receipt.issued",
                           payload={"receipts": 3, "unproven": 1})
            count, _cmd, err = B.receipts_bound(runs_root=d, from_journal=True)
            self.assertIsNone(err)
            self.assertEqual(count, 0)

    def test_matches_the_file_reading_branch_on_the_same_real_run(self):
        """The two branches (open the files, fold the journal) must agree
        on one real run built the ordinary way, or the journal branch is
        answering a different question than the one board_status asks."""
        with tempfile.TemporaryDirectory() as d:
            run_dir = os.path.join(d, "run1")
            record = _seed_two_unit_run(run_dir, u2_state="failed")
            with open(os.path.join(run_dir, "claims.json"),
                     encoding="utf-8") as fh:
                claims = json.load(fh)
            import receipt_door
            os.environ[journal.RUN_DIR_ENV_VAR] = run_dir
            try:
                receipt_door.receipts_for(record, claims, [], None)
            finally:
                os.environ.pop(journal.RUN_DIR_ENV_VAR, None)
            files_count, _c1, err1 = B.receipts_bound(runs_root=d)
            journal_count, _c2, err2 = B.receipts_bound(runs_root=d,
                                                        from_journal=True)
            self.assertIsNone(err1)
            self.assertIsNone(err2)
            self.assertEqual(files_count, journal_count)


class TheDiffCommandLine(unittest.TestCase):
    def _run(self, argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = JP.main(argv)
        return code, out.getvalue()

    def test_a_missing_runs_root_is_no_data_exit_2(self):
        code, out = self._run(["--diff", "/no/such/runs-root"])
        self.assertEqual(code, 2)
        self.assertIn("NO-DATA", out)

    def test_an_empty_runs_root_is_no_data_exit_2(self):
        with tempfile.TemporaryDirectory() as d:
            code, out = self._run(["--diff", d])
            self.assertEqual(code, 2)
            self.assertIn("NO-DATA", out)

    def test_a_root_of_journal_less_runs_is_no_data_exit_2_per_run(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "20260830T000000-old"))
            code, out = self._run(["--diff", d])
            self.assertEqual(code, 2)
            self.assertIn("NO-DATA: 20260830T000000-old", out)

    def test_a_clean_run_prints_nothing_for_it_and_exits_0(self):
        with tempfile.TemporaryDirectory() as d:
            _seed_two_unit_run(os.path.join(d, "20260903T000000-w1"))
            code, out = self._run(["--diff", d])
            self.assertEqual(code, 0)
            self.assertEqual(out, "")

    def test_a_disagreeing_run_prints_the_diff_and_exits_1(self):
        with tempfile.TemporaryDirectory() as d:
            run_dir = os.path.join(d, "20260903T000000-w1")
            _seed_two_unit_run(run_dir)
            journal.append(run_dir, "claim.released", unit_id="U2",
                           payload={"owner": "x", "state": "done",
                                    "attempt": 9})
            code, out = self._run(["--diff", d])
            self.assertEqual(code, 1)
            self.assertIn("DIFF: 20260903T000000-w1", out)


if __name__ == "__main__":
    unittest.main()
