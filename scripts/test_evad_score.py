"""What evad_score.py must keep true.

Two unrelated things live in this one file, so this test module carries two
unrelated classes: the seven-trial EVAD gauntlet score (the module's
original subject) and P16's three DS flywheel counts (--personas), bolted
onto the same CLI because the roadmap's own done-check names this file and
this flag directly. The persona tests below are the ones that matter for
P16; the gauntlet score already had --selftest and this only adds coverage
around the parts --selftest does not drive.
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import evad_score as E  # noqa: E402


def _write_work(run_dir, rows):
    os.makedirs(run_dir, exist_ok=True)
    with open(os.path.join(run_dir, "W-seed.json"), "w", encoding="utf-8") as fh:
        json.dump({"outcome": "o", "rows": rows}, fh)


def _write_claims(run_dir, claims):
    with open(os.path.join(run_dir, "claims.json"), "w", encoding="utf-8") as fh:
        json.dump(claims, fh)


def _full_e18(unit_id):
    return {"id": unit_id, "evidence_family": "E18",
           "e18_evidence": {"metric": "auc", "value": 0.9, "baseline": 0.8,
                             "seed": 7, "holdout_id": "h1"}}


def _gap_e18(unit_id, reason="no metric recorded: no evidence file at all"):
    return {"id": unit_id, "evidence_family": "E18",
           "e18_evidence": {"missing_reason": reason}}


class TheDoneCheckItself(unittest.TestCase):
    """P16's own done_check, driven at this level (not the CLI) so a
    regression here fails fast rather than only through subprocess."""

    def test_two_e18_units_one_full_reads_one_of_two(self):
        with tempfile.TemporaryDirectory() as d:
            run_dir = os.path.join(d, "20260904T000000-a")
            _write_work(run_dir, [_full_e18("u1"), _gap_e18("u2")])
            lines, code = E.personas_report([d])
            self.assertEqual(code, 0)
            self.assertIn("reproducible 1 of 2", lines)

    def test_an_empty_root_reads_no_data(self):
        with tempfile.TemporaryDirectory() as d:
            lines, code = E.personas_report([d])
            self.assertEqual(code, 0)
            self.assertTrue(any("NO-DATA" in l for l in lines), lines)

    def test_a_nonexistent_root_also_reads_no_data_never_a_crash(self):
        lines, code = E.personas_report(["/no/such/directory/at/all"])
        self.assertEqual(code, 0)
        self.assertTrue(any("NO-DATA" in l for l in lines), lines)


class ReproducibleExperimentRate(unittest.TestCase):
    """total counts only E18 units; passed is the ones e18_gap calls full.
    A row outside E18 never enters either count, mirroring
    receipt_door.e18_gap's own untouched rule for a non-E18 row."""

    def test_a_non_e18_row_is_not_counted_at_all(self):
        with tempfile.TemporaryDirectory() as d:
            run_dir = os.path.join(d, "r")
            _write_work(run_dir, [{"id": "u1", "evidence_family": "E1"}])
            passed, total = E.reproducible_experiment_rate(E._run_dirs([d]))
            self.assertEqual((passed, total), (0, 0))

    def test_all_full_is_all_reproducible(self):
        with tempfile.TemporaryDirectory() as d:
            run_dir = os.path.join(d, "r")
            _write_work(run_dir, [_full_e18("u1"), _full_e18("u2")])
            passed, total = E.reproducible_experiment_rate(E._run_dirs([d]))
            self.assertEqual((passed, total), (2, 2))

    def test_a_run_in_a_second_root_is_counted_once_by_name(self):
        with tempfile.TemporaryDirectory() as d1, \
             tempfile.TemporaryDirectory() as d2:
            run_dir = os.path.join(d1, "same-name")
            _write_work(run_dir, [_full_e18("u1")])
            dup_dir = os.path.join(d2, "same-name")
            _write_work(dup_dir, [_full_e18("u9")])
            passed, total = E.reproducible_experiment_rate(
                E._run_dirs([d1, d2]))
            # The d1 copy wins (first root); the d2 copy of the SAME run
            # name is never read, so only u1 is counted, never u9 too.
            self.assertEqual((passed, total), (1, 1))


class LeakageCatches(unittest.TestCase):
    """A caught leak is read off claims.json: the check named
    split_check.py, and its own captured output carrying its FAIL line."""

    def test_a_split_check_fail_is_counted(self):
        with tempfile.TemporaryDirectory() as d:
            run_dir = os.path.join(d, "r")
            _write_work(run_dir, [])
            _write_claims(run_dir, {"u1": {"evidence": {
                "check_command": "python3 scripts/split_check.py --train t "
                                 "--test v",
                "exit_code": 1,
                "output": "split-check: FAIL: key 'c1' appears in both "
                          "train and test"}}})
            self.assertEqual(E.leakage_catches(E._run_dirs([d])), 1)

    def test_a_split_check_pass_is_not_counted(self):
        with tempfile.TemporaryDirectory() as d:
            run_dir = os.path.join(d, "r")
            _write_work(run_dir, [])
            _write_claims(run_dir, {"u1": {"evidence": {
                "check_command": "python3 scripts/split_check.py --train t "
                                 "--test v",
                "exit_code": 0,
                "output": "split-check: PASSED: 3 train row(s), 2 test "
                          "row(s)"}}})
            self.assertEqual(E.leakage_catches(E._run_dirs([d])), 0)

    def test_a_fail_from_an_unrelated_check_is_not_counted(self):
        with tempfile.TemporaryDirectory() as d:
            run_dir = os.path.join(d, "r")
            _write_work(run_dir, [])
            _write_claims(run_dir, {"u1": {"evidence": {
                "check_command": "python3 scripts/other_check.py",
                "exit_code": 1,
                "output": "split-check: FAIL: unrelated coincidence"}}})
            self.assertEqual(E.leakage_catches(E._run_dirs([d])), 0)

    def test_no_claims_file_at_all_is_a_real_zero(self):
        with tempfile.TemporaryDirectory() as d:
            run_dir = os.path.join(d, "r")
            _write_work(run_dir, [])
            self.assertEqual(E.leakage_catches(E._run_dirs([d])), 0)


class PromotionsParked(unittest.TestCase):
    """A parked row (loom.py's own marker) whose trigger name or matched
    words look like a data-science promotion, threshold or retrain."""

    def test_a_promotion_worded_park_is_counted(self):
        with tempfile.TemporaryDirectory() as d:
            run_dir = os.path.join(d, "r")
            _write_work(run_dir, [{
                "id": "u1", "status": "AWAITING FOUNDER",
                "parked": {"triggers": [
                    {"trigger": "promotion", "words": "promote"}],
                          "why": "x", "answered": None}}])
            self.assertEqual(E.promotions_parked(E._run_dirs([d])), 1)

    def test_a_parked_row_with_unrelated_words_is_not_counted(self):
        with tempfile.TemporaryDirectory() as d:
            run_dir = os.path.join(d, "r")
            _write_work(run_dir, [{
                "id": "u1", "status": "AWAITING FOUNDER",
                "parked": {"triggers": [
                    {"trigger": "money", "words": "billing"}],
                          "why": "x", "answered": None}}])
            self.assertEqual(E.promotions_parked(E._run_dirs([d])), 0)

    def test_an_unparked_row_is_not_counted_even_with_the_words(self):
        with tempfile.TemporaryDirectory() as d:
            run_dir = os.path.join(d, "r")
            _write_work(run_dir, [{
                "id": "u1", "title": "promote the model to production"}])
            self.assertEqual(E.promotions_parked(E._run_dirs([d])), 0)

    def test_a_released_but_once_parked_row_still_counts(self):
        """The cumulative rule: promotions_parked reports how many times
        the gate caught one, not how many are still waiting, so a later
        release does not erase the count."""
        with tempfile.TemporaryDirectory() as d:
            run_dir = os.path.join(d, "r")
            _write_work(run_dir, [{
                "id": "u1", "status": "SCHEDULED",
                "parked": {"triggers": [
                    {"trigger": "promotion", "words": "promote"}],
                          "why": "x",
                          "answered": {"choice": "accept", "by": "khalil"}}}])
            self.assertEqual(E.promotions_parked(E._run_dirs([d])), 1)


class RunDirs(unittest.TestCase):
    def test_a_missing_root_contributes_nothing(self):
        self.assertEqual(E._run_dirs(["/no/such/root"]), [])

    def test_an_existing_but_empty_root_is_a_real_empty_list(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(E._run_dirs([d]), [])

    def test_dedup_by_name_keeps_the_first_root(self):
        with tempfile.TemporaryDirectory() as d1, \
             tempfile.TemporaryDirectory() as d2:
            os.makedirs(os.path.join(d1, "same"))
            os.makedirs(os.path.join(d2, "same"))
            dirs = E._run_dirs([d1, d2])
            self.assertEqual(dirs, [os.path.join(d1, "same")])


if __name__ == "__main__":
    unittest.main()
