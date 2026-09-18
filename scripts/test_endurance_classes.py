#!/usr/bin/env python3
"""Tests for scripts/endurance_classes.py. Plain unittest, runnable directly.

Every fixture is a tiny, clearly-synthetic run directory built inside
setUp and removed in tearDown, using the real sibling modules (journal,
intervention_events) to write it rather than hand-rolling a second copy of
their file formats. The one exception is
test_real_corpus_dir_is_no_data, which points at the real (intentionally
empty) benchmarks/endurance_classes/runs/ directory shipped with this
change, to prove the shipped harness reports NO-DATA over it rather than a
pass.
"""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import endurance_classes as EC  # noqa: E402
import journal  # noqa: E402
import intervention_events  # noqa: E402

# Pinned independently of the module's own REPORT_FIELDS constant: this is
# the contract the test enforces, not a mirror of whatever the
# implementation currently says it is.
REQUIRED_FIELDS = frozenset((
    "useful_units", "idle_minutes", "retries", "abandoned_approaches",
    "context_resets", "crashes", "interventions", "scope_incidents",
))
UNINSTRUMENTED_FIELDS = frozenset((
    "retries", "abandoned_approaches", "context_resets", "crashes",
))


def _write_claims(run_dir, claims):
    with open(os.path.join(run_dir, "claims.json"), "w",
              encoding="utf-8") as fh:
        json.dump(claims, fh)


def _bare_run_dir(base):
    """A run directory with no journal.jsonl at all: the total NO-DATA
    case for every field this module can compute."""
    run_dir = os.path.join(base, "bare")
    os.makedirs(run_dir)
    return run_dir


def _journal_only_run_dir(base, name="journal-only"):
    """A run directory with a journal but no receipt: uuw.measure() is
    NO-DATA (no receipt), while interventions and scope_incidents can
    still be read directly off the journal."""
    run_dir = os.path.join(base, name)
    os.makedirs(run_dir)
    journal.append(run_dir, "run.opened", payload={})
    journal.append(run_dir, "integrate.refused",
                   payload={"reason": "a unit wrote a path it never "
                            "declared, quarantined"})
    intervention_events.record_intervention(
        run_dir, "clarification", "asked which branch to target", True)
    return run_dir


def _fully_backed_run_dir(base, name="backed", accepted=True):
    """A run directory with a journal, a receipt, and (when accepted) one
    claims.json entry that meets useful_unwatched_work's own acceptance
    test: state in (done, released, closed), evidence.exit_code == 0
    (a real int, not a bool), timestamped inside the span."""
    run_dir = os.path.join(base, name)
    os.makedirs(run_dir)
    journal.append(run_dir, "run.opened", payload={})
    # Captured between the first and last journal event so it is
    # guaranteed to land inside [started, ended] regardless of how long
    # the remaining appends take.
    now = time.time()
    journal.append(run_dir, "receipt.issued",
                   payload={"receipts": 1, "unproven": 0})
    journal.append(run_dir, "unit.done", payload={"unit_id": "u1"})
    if accepted:
        claims = {"u1": {"state": "done",
                         "evidence": {"exit_code": 0},
                         "claimed_at": now, "released_at": now}}
    else:
        claims = {}
    _write_claims(run_dir, claims)
    return run_dir


class ScopeIncidentsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="endurance_scope_test_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_no_journal_is_no_data(self):
        run_dir = os.path.join(self.tmp, "nope")
        os.makedirs(run_dir)
        count, reason = EC.scope_incidents_in_journal(run_dir)
        self.assertIsNone(count)
        self.assertTrue(reason)

    def test_empty_journal_is_a_real_zero(self):
        run_dir = os.path.join(self.tmp, "empty")
        os.makedirs(run_dir)
        open(os.path.join(run_dir, "journal.jsonl"), "w").close()
        count, reason = EC.scope_incidents_in_journal(run_dir)
        self.assertEqual(count, 0)
        self.assertEqual(reason, "")

    def test_matching_and_non_matching_events(self):
        run_dir = os.path.join(self.tmp, "mixed")
        os.makedirs(run_dir)
        journal.append(run_dir, "integrate.refused",
                       payload={"reason": "never declared this path"})
        journal.append(run_dir, "integrate.refused",
                       payload={"reason": "a test did not pass"})
        journal.append(run_dir, "integrate.refused",
                       payload={"reason": "QUARANTINED for scope"})
        journal.append(run_dir, "unit.done", payload={})
        count, reason = EC.scope_incidents_in_journal(run_dir)
        self.assertEqual(count, 2)
        self.assertEqual(reason, "")

    def test_malformed_and_wrong_shaped_lines_are_skipped(self):
        run_dir = os.path.join(self.tmp, "malformed")
        os.makedirs(run_dir)
        path = os.path.join(run_dir, "journal.jsonl")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("{not json\n")
            fh.write(json.dumps([1, 2, 3]) + "\n")
            fh.write(json.dumps({"type": "integrate.refused"}) + "\n")
            fh.write(json.dumps({"type": "integrate.refused",
                                 "payload": "not a dict"}) + "\n")
            fh.write(json.dumps({"type": "integrate.refused",
                                 "payload": {"reason": 42}}) + "\n")
            fh.write("\n")
        count, reason = EC.scope_incidents_in_journal(run_dir)
        self.assertEqual(count, 0)
        self.assertEqual(reason, "")

    def test_truncated_utf8_tail_does_not_raise(self):
        run_dir = os.path.join(self.tmp, "torn")
        os.makedirs(run_dir)
        path = os.path.join(run_dir, "journal.jsonl")
        good = json.dumps({"type": "integrate.refused",
                           "payload": {"reason": "quarantine"}}) + "\n"
        with open(path, "wb") as fh:
            fh.write(good.encode("utf-8"))
            # An incomplete 3-byte UTF-8 sequence, as a crash mid-write of
            # a multibyte character would leave behind.
            fh.write(b"\xe2\x98")
        count, reason = EC.scope_incidents_in_journal(run_dir)
        self.assertEqual(count, 1)
        self.assertEqual(reason, "")

    def test_wrong_type_run_dir_is_no_data_not_a_crash(self):
        count, reason = EC.scope_incidents_in_journal(None)
        self.assertIsNone(count)
        self.assertTrue(reason)


class ClassifyRunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="endurance_classify_test_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_missing_run_dir_is_no_data_exit_2(self):
        report, code = EC.classify_run(os.path.join(self.tmp, "nope"))
        self.assertEqual(code, 2)
        self.assertEqual(set(report.keys()), {"nodata"})

    def test_wrong_type_run_dir_is_no_data_exit_2(self):
        report, code = EC.classify_run(123)
        self.assertEqual(code, 2)
        self.assertEqual(set(report.keys()), {"nodata"})

    def test_report_always_has_exactly_the_eight_pinned_fields(self):
        run_dir = _bare_run_dir(self.tmp)
        report, code = EC.classify_run(run_dir)
        self.assertEqual(code, 0)
        self.assertEqual(set(report.keys()), REQUIRED_FIELDS)

    def test_bare_run_dir_is_no_data_on_every_field(self):
        run_dir = _bare_run_dir(self.tmp)
        report, code = EC.classify_run(run_dir)
        self.assertEqual(code, 0)
        for field in REQUIRED_FIELDS:
            self.assertEqual(report[field], "NO-DATA",
                             "field %r should be NO-DATA" % field)

    def test_uninstrumented_fields_are_always_no_data(self):
        # THE DECIDING PROPERTY, mutation-tested: whatever the run
        # directory holds, these four fields must never read as anything
        # but the string "NO-DATA", because nothing in this codebase
        # measures them yet. A green check that could also pass with one
        # of these silently defaulted to 0 is not evidence; this is the
        # test that would catch that.
        run_dir = _fully_backed_run_dir(self.tmp)
        report, code = EC.classify_run(run_dir)
        self.assertEqual(code, 0)
        for field in UNINSTRUMENTED_FIELDS:
            self.assertEqual(report[field], "NO-DATA")

    def test_journal_only_mixes_real_and_no_data_fields(self):
        run_dir = _journal_only_run_dir(self.tmp)
        report, code = EC.classify_run(run_dir)
        self.assertEqual(code, 0)
        # No receipt: uuw.measure() is NO-DATA, so both of these stay
        # NO-DATA even though the journal itself is real and readable.
        self.assertEqual(report["useful_units"], "NO-DATA")
        self.assertEqual(report["idle_minutes"], "NO-DATA")
        # But the journal exists, so these two are computed for real.
        self.assertEqual(report["interventions"], 1)
        self.assertEqual(report["scope_incidents"], 1)

    def test_accepted_unit_gives_positive_useful_units_and_zero_idle(self):
        run_dir = _fully_backed_run_dir(self.tmp, accepted=True)
        report, code = EC.classify_run(run_dir)
        self.assertEqual(code, 0)
        self.assertEqual(report["useful_units"], 1)
        self.assertEqual(report["idle_minutes"], 0.0)

    def test_no_accepted_unit_gives_zero_useful_units_and_positive_idle(self):
        run_dir = _fully_backed_run_dir(self.tmp, accepted=False)
        report, code = EC.classify_run(run_dir)
        self.assertEqual(code, 0)
        self.assertEqual(report["useful_units"], 0)
        self.assertIsInstance(report["idle_minutes"], float)
        self.assertGreaterEqual(report["idle_minutes"], 0.0)

    def test_interventions_wrong_shaped_sibling_return_stays_no_data(self):
        # The sibling contract promises None or a list; simulate a future
        # break of that promise and prove this module does not fabricate
        # a count off it (finding: len() of a non-list silently "works").
        run_dir = _bare_run_dir(self.tmp)
        with mock.patch.object(EC.intervention_events, "read_interventions",
                               return_value="not a list"):
            report, code = EC.classify_run(run_dir)
        self.assertEqual(code, 0)
        self.assertEqual(report["interventions"], "NO-DATA")


class PrivateHelpersTest(unittest.TestCase):
    """Direct coverage for the defensive branches classify_run's own call
    sites cannot presently exercise, per the Muse finding that a
    defensive branch with no direct test is not evidence it works."""

    def test_is_nodata_report_non_dict_is_treated_as_no_data(self):
        self.assertTrue(EC._is_nodata_report("not a dict"))
        self.assertTrue(EC._is_nodata_report(None))

    def test_is_nodata_report_reads_the_nodata_key(self):
        self.assertTrue(EC._is_nodata_report({"nodata": "why"}))
        self.assertFalse(EC._is_nodata_report({"nodata": ""}))
        self.assertFalse(EC._is_nodata_report({"minutes": 1.0}))

    def test_is_real_number_excludes_bool(self):
        self.assertFalse(EC._is_real_number(True))
        self.assertFalse(EC._is_real_number(False))
        self.assertTrue(EC._is_real_number(0))
        self.assertTrue(EC._is_real_number(3.5))
        self.assertFalse(EC._is_real_number("3"))


class ScoreCorpusTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="endurance_corpus_test_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_missing_corpus_dir_is_no_data(self):
        result, code = EC.score_corpus(os.path.join(self.tmp, "nope"))
        self.assertEqual(code, 3)
        self.assertTrue(result["nodata"])

    def test_empty_corpus_dir_is_no_data(self):
        result, code = EC.score_corpus(self.tmp)
        self.assertEqual(code, 3)
        self.assertTrue(result["nodata"])

    def test_corpus_with_only_non_run_subdirs_is_no_data(self):
        os.makedirs(os.path.join(self.tmp, "not-a-run"))
        result, code = EC.score_corpus(self.tmp)
        self.assertEqual(code, 3)
        self.assertTrue(result["nodata"])

    def test_wrong_type_corpus_dir_is_no_data_not_a_crash(self):
        result, code = EC.score_corpus(None)
        self.assertEqual(code, 2)
        self.assertTrue(result["nodata"])

    def test_mixed_corpus_only_counts_qualifying_runs(self):
        os.makedirs(os.path.join(self.tmp, "not-a-run"))
        _bare_run_dir(self.tmp)  # "bare": no journal.jsonl, does not qualify
        _journal_only_run_dir(self.tmp, name="real-run")
        result, code = EC.score_corpus(self.tmp)
        self.assertEqual(code, 0)
        self.assertEqual(set(result["runs"].keys()), {"real-run"})
        self.assertEqual(set(result["runs"]["real-run"].keys()),
                         REQUIRED_FIELDS)

    def test_vanished_subdir_is_never_smuggled_into_runs(self):
        _journal_only_run_dir(self.tmp, name="survivor")
        _journal_only_run_dir(self.tmp, name="vanishes")

        real_classify_run = EC.classify_run

        def flaky_classify_run(run_dir):
            if os.path.basename(run_dir) == "vanishes":
                return ({"nodata": "vanished mid scan"}, 2)
            return real_classify_run(run_dir)

        with mock.patch.object(EC, "classify_run",
                               side_effect=flaky_classify_run):
            result, code = EC.score_corpus(self.tmp)
        self.assertEqual(code, 0)
        self.assertEqual(set(result["runs"].keys()), {"survivor"})
        for report in result["runs"].values():
            self.assertEqual(set(report.keys()), REQUIRED_FIELDS)

    def test_every_qualifying_run_vanishing_is_no_data(self):
        _journal_only_run_dir(self.tmp, name="vanishes")
        with mock.patch.object(EC, "classify_run",
                               return_value=({"nodata": "gone"}, 2)):
            result, code = EC.score_corpus(self.tmp)
        self.assertEqual(code, 3)
        self.assertTrue(result["nodata"])


class ReportLineTest(unittest.TestCase):
    def test_no_data(self):
        line = EC.report_line({"nodata": "why"}, 3)
        self.assertEqual(line, "endurance classes: NO-DATA, why")

    def test_pass(self):
        line = EC.report_line({"nodata": "", "runs": {"a": {}, "b": {}}}, 0)
        self.assertEqual(line, "endurance classes: 2 run(s) scored")

    def test_unknown_exit_code_raises(self):
        with self.assertRaises(ValueError):
            EC.report_line({}, 99)

    def test_exit_zero_without_runs_dict_raises(self):
        with self.assertRaises(ValueError):
            EC.report_line({"nodata": ""}, 0)
        with self.assertRaises(ValueError):
            EC.report_line({"nodata": "", "runs": "not a dict"}, 0)


class MainTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="endurance_main_test_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_main_returns_exit_code(self):
        _journal_only_run_dir(self.tmp, name="a-run")
        code = EC.main([self.tmp])
        self.assertEqual(code, 0)

    def test_real_corpus_dir_is_no_data(self):
        real_dir = os.path.join(REPO_ROOT, "benchmarks", "endurance_classes",
                                "runs")
        result, code = EC.score_corpus(real_dir)
        self.assertEqual(code, 3)
        self.assertTrue(result["nodata"])


if __name__ == "__main__":
    unittest.main()
