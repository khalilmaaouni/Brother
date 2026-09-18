"""Calibration for scripts/useful_unwatched_work.py, driven in both directions.

The property this file exists to assert is not that a number comes out. It is
that USEFUL minutes stay at zero for a long, clean span with no independently
verified accepted unit in it, and that a run whose claims store cannot be
read reads NO-DATA rather than a flattering zero. Mirrors the fixture shape
of scripts/test_safe_unwatched_time.py on purpose (same journal vocabulary,
same write_run helper), since this module composes directly with that one.

Every fixture is built in a temp directory. Nothing here reads or writes the
real run store.
"""
import datetime
import json
import os
import shutil
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, 'scripts'))
import useful_unwatched_work as uuw  # noqa: E402
import safe_unwatched_time as sut  # noqa: E402

try:
    import tmp_sandbox
    tmp_sandbox.install()
except ImportError:
    sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % os.path.basename(__file__))

OPENED = sut.EV_RUN_OPENED
FINISHED = sut.EV_UNIT_DONE
VERIFIED = sut.EV_EVIDENCE_VERIFIED
ISSUED = sut.EV_RECEIPT_ISSUED

BASE = datetime.datetime(2026, 9, 4, 22, 0, 0, tzinfo=datetime.timezone.utc)


def at(minute):
    """The fixture clock. Minute 0 is the run's first event."""
    return (BASE + datetime.timedelta(minutes=minute)).isoformat()


def event(minute, kind, payload=None, unit_id=None):
    return {"at": at(minute), "type": kind, "unit_id": unit_id,
            "payload": payload or {}}


def claim(unit_id, minute, exit_code=0, state="done", released=True):
    """A claims.json entry, epoch stamped the way the real store writes them.

    released=False leaves released_at out entirely, so a test can exercise
    the claimed_at-only path without also exercising the "present but
    invalid" fallback path (see claim_with_bad_released_at below)."""
    when = (BASE + datetime.timedelta(minutes=minute)).timestamp()
    evidence = {"check_command": "python3 -c pass"}
    if exit_code is not None:
        evidence["exit_code"] = exit_code
    entry = {"unit_id": unit_id, "state": state, "claimed_at": when,
             "evidence": evidence}
    if released:
        entry["released_at"] = when
    return entry


def claim_with_bad_released_at(unit_id, minute):
    """released_at present but unparseable; claimed_at is the good stamp.
    Exercises the sibling's own _epoch(x) or _epoch(y) fallback idiom."""
    when = (BASE + datetime.timedelta(minutes=minute)).timestamp()
    return {"unit_id": unit_id, "state": "done", "claimed_at": when,
            "released_at": "not-a-timestamp",
            "evidence": {"exit_code": 0}}


#: A ninety minute run that never breaks on its own: two clean units in the
#: journal (their claims are supplied separately per test).
CLEAN_90 = [
    event(0, OPENED, {"units": 2}),
    event(30, FINISHED, {"files_changed": 1}, "u1"),
    event(31, VERIFIED, {"check_exit": 0}, "u1"),
    event(60, FINISHED, {"files_changed": 1}, "u2"),
    event(61, VERIFIED, {"check_exit": 0}, "u2"),
    event(85, ISSUED, {"receipts": 2, "unproven": 0}),
    event(90, "acceptance.screened", {"screens": 1}),
]


def write_run(root, name, events, claims=None):
    """One run directory on disk. Returns its path. claims=None writes no
    claims.json at all (distinct from claims={}, which writes an empty
    object); this distinction is the whole point of several tests below."""
    run_dir = os.path.join(root, name)
    os.makedirs(run_dir)
    with open(os.path.join(run_dir, "journal.jsonl"), "w",
              encoding="utf-8") as fh:
        for e in events:
            fh.write(json.dumps(e) + "\n")
    if claims is not None:
        with open(os.path.join(run_dir, "claims.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(claims, fh, indent=1)
    return run_dir


class Sandbox(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="uuw-")
        self.addCleanup(shutil.rmtree, self.root, True)


class NoDataOutcomes(Sandbox):
    """Outcome (a): the underlying span itself could not be computed."""

    def test_a_run_with_no_journal_propagates_the_underlying_no_data(self):
        run = os.path.join(self.root, "empty")
        os.makedirs(run)
        result, code = uuw.measure(run)
        self.assertEqual(code, 3)
        self.assertIn("no journal event", result["nodata"])
        self.assertNotIn("minutes", result)

    def test_a_missing_run_directory_exits_2_like_the_sibling_does(self):
        result, code = uuw.measure(os.path.join(self.root, "nope"))
        self.assertEqual(code, 2)
        self.assertIn("not a directory", result["nodata"])

    def test_a_non_path_run_dir_is_no_data_not_a_crash(self):
        result, code = uuw.measure(12345)
        self.assertEqual(code, 2)
        self.assertIn("nodata", result)


class NoDataAcceptance(Sandbox):
    """Outcome (b): the span computed fine, but acceptance is unverifiable."""

    def test_missing_claims_file_is_no_data_never_a_zero(self):
        run = write_run(self.root, "no-claims", CLEAN_90, claims=None)
        result, code = uuw.measure(run)
        self.assertEqual(code, 3)
        self.assertIn("acceptance cannot be verified", result["nodata"])
        self.assertNotIn("useful_minutes", result)

    def test_no_data_never_prints_as_zero_minutes(self):
        run = write_run(self.root, "no-claims2", CLEAN_90, claims=None)
        result, _ = uuw.measure(run)
        line = uuw.report_line(result)
        self.assertIn("NO-DATA", line)
        self.assertNotIn("0.0 min over", line)

    def test_corrupt_claims_json_is_no_data(self):
        run = write_run(self.root, "corrupt", CLEAN_90, claims={})
        with open(os.path.join(run, "claims.json"), "w",
                  encoding="utf-8") as fh:
            fh.write("{not valid json")
        result, code = uuw.measure(run)
        self.assertEqual(code, 3)
        self.assertIn("acceptance cannot be verified", result["nodata"])

    def test_claims_json_holding_a_list_is_no_data_not_zero(self):
        run = write_run(self.root, "list-claims", CLEAN_90, claims=[1, 2])
        result, code = uuw.measure(run)
        self.assertEqual(code, 3)
        self.assertIn("not a JSON object", result["nodata"])


class RealZeroVsPositive(Sandbox):
    """Outcome (c) versus the positive case: both are exit 0, computed, and
    distinguishable from NO-DATA by an empty 'nodata' string."""

    def test_an_empty_claims_object_is_a_real_computed_zero(self):
        run = write_run(self.root, "empty-claims", CLEAN_90, claims={})
        result, code = uuw.measure(run)
        self.assertEqual(code, 0)
        self.assertEqual(result["nodata"], "")
        self.assertEqual(result["accepted_units"], 0)
        self.assertEqual(result["useful_minutes"], 0.0)

    def test_one_accepted_unit_makes_the_whole_span_useful(self):
        run = write_run(self.root, "one-good", CLEAN_90,
                        claims={"u1": claim("u1", 30)})
        result, code = uuw.measure(run)
        self.assertEqual(code, 0)
        self.assertEqual(result["accepted_units"], 1)
        self.assertAlmostEqual(result["useful_minutes"], result["minutes"],
                               places=3)
        self.assertAlmostEqual(result["useful_minutes"], 90.0, places=3)

    def test_multiple_accepted_units_are_all_counted(self):
        run = write_run(self.root, "two-good", CLEAN_90,
                        claims={"u1": claim("u1", 30),
                                "u2": claim("u2", 60)})
        result, _ = uuw.measure(run)
        self.assertEqual(result["accepted_units"], 2)

    def test_zero_accepted_units_yields_zero_useful_minutes_on_a_long_clean_span(self):
        """THE deciding property. The span is 90 clean minutes; the one claim
        in it never verified (no exit code at all). Useful minutes must stay
        at zero, not borrow the raw span's length."""
        run = write_run(self.root, "unverified", CLEAN_90,
                        claims={"u1": claim("u1", 30, exit_code=None)})
        result, code = uuw.measure(run)
        self.assertEqual(code, 0)
        self.assertEqual(result["accepted_units"], 0)
        self.assertEqual(result["useful_minutes"], 0.0)
        self.assertGreater(result["minutes"], 0.0,
                           "the raw span itself must still be positive, or "
                           "this test would not distinguish the two figures")


class ExclusionReasons(Sandbox):
    """Every reason a claims.json entry fails the accepted test, one at a
    time, none of them raising."""

    def run_with(self, claims_entry):
        run = write_run(self.root, "x", CLEAN_90, claims={"u1": claims_entry})
        return uuw.measure(run)[0]

    def test_bool_exit_code_true_is_not_a_verified_zero(self):
        result = self.run_with({"state": "done", "claimed_at":
                                (BASE + datetime.timedelta(minutes=30)).timestamp(),
                                "released_at":
                                (BASE + datetime.timedelta(minutes=30)).timestamp(),
                                "evidence": {"exit_code": True}})
        self.assertEqual(result["accepted_units"], 0)

    def test_nonzero_exit_code_is_not_counted(self):
        result = self.run_with(claim("u1", 30, exit_code=1))
        self.assertEqual(result["accepted_units"], 0)

    def test_missing_evidence_key_is_not_counted(self):
        result = self.run_with({"state": "done", "claimed_at":
                                (BASE + datetime.timedelta(minutes=30)).timestamp()})
        self.assertEqual(result["accepted_units"], 0)

    def test_non_dict_evidence_is_not_counted(self):
        result = self.run_with({"state": "done", "claimed_at":
                                (BASE + datetime.timedelta(minutes=30)).timestamp(),
                                "evidence": "fine, trust me"})
        self.assertEqual(result["accepted_units"], 0)

    def test_unrecognised_state_is_not_counted(self):
        result = self.run_with(claim("u1", 30, state="cancelled"))
        self.assertEqual(result["accepted_units"], 0)

    def test_non_string_unhashable_state_does_not_raise(self):
        result = self.run_with({"state": ["done"], "claimed_at":
                                (BASE + datetime.timedelta(minutes=30)).timestamp(),
                                "evidence": {"exit_code": 0}})
        self.assertEqual(result["accepted_units"], 0)

    def test_missing_timestamp_is_not_counted(self):
        result = self.run_with({"state": "done",
                                "evidence": {"exit_code": 0}})
        self.assertEqual(result["accepted_units"], 0)

    def test_claim_before_the_span_start_is_not_counted(self):
        result = self.run_with(claim("u1", -30))
        self.assertEqual(result["accepted_units"], 0)

    def test_claim_after_the_span_end_is_not_counted(self):
        result = self.run_with(claim("u1", 200))
        self.assertEqual(result["accepted_units"], 0)

    def test_invalid_released_at_falls_back_to_claimed_at(self):
        """Matches the sibling's own _epoch(released) or _epoch(claimed)
        idiom: a present-but-broken released_at does not sink an otherwise
        good claim when claimed_at is valid and in range."""
        result = self.run_with(claim_with_bad_released_at("u1", 30))
        self.assertEqual(result["accepted_units"], 1)


class Composition(Sandbox):
    def test_only_units_inside_a_break_narrowed_span_are_counted(self):
        """u1 finishes clean at minute 30. u2's own claim carries a nonzero
        exit code timestamped at minute 60, which is exactly one of the
        sibling's four break conditions (REFUTED) and narrows the span to
        60 minutes. u1 sits inside the narrowed span and must count; u2
        must not, both because it never verified and because nothing past
        the break counts at all."""
        run = write_run(self.root, "narrowed", CLEAN_90,
                        claims={"u1": claim("u1", 30),
                                "u2": claim("u2", 60, exit_code=2)})
        result, code = uuw.measure(run)
        self.assertEqual(code, 0)
        self.assertEqual(result["broken_by"], sut.REFUTED)
        self.assertAlmostEqual(result["minutes"], 60.0, places=3)
        self.assertEqual(result["accepted_units"], 1)
        self.assertAlmostEqual(result["useful_minutes"], 60.0, places=3)


class ReportLineShape(Sandbox):
    def test_no_data_shape(self):
        line = uuw.report_line({"nodata": "no receipts"})
        self.assertEqual(line, "useful unwatched work: NO-DATA, no receipts")

    def test_normal_shape_is_exact(self):
        line = uuw.report_line({"nodata": "", "minutes": 90.0,
                                "useful_minutes": 90.0, "accepted_units": 2,
                                "broken_by": "none"})
        self.assertEqual(
            line,
            "useful unwatched work: 90.0 min over 2 accepted unit(s) "
            "(raw span 90.0 min, broken by none)")

    def test_zero_shape_is_a_computed_zero_not_no_data(self):
        line = uuw.report_line({"nodata": "", "minutes": 90.0,
                                "useful_minutes": 0.0, "accepted_units": 0,
                                "broken_by": "none"})
        self.assertNotIn("NO-DATA", line)
        self.assertIn("0.0 min over 0 accepted unit(s)", line)

    def test_a_result_missing_a_promised_key_is_not_read_as_a_computed_zero(self):
        """A malformed dict with nodata == "" but no accepted_units must not
        silently print as though zero were computed; that would manufacture
        an answer nothing here actually measured."""
        line = uuw.report_line({"nodata": "", "minutes": 90.0})
        self.assertIn("NO-DATA", line)
        self.assertIn("missing", line)

    def test_a_non_dict_result_is_no_data_not_a_crash(self):
        line = uuw.report_line(["not", "a", "dict"])
        self.assertIn("NO-DATA", line)


if __name__ == '__main__':
    unittest.main()
