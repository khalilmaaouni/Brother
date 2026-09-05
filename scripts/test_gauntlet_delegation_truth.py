"""The delegation-truth gauntlet runner, driven both ways.

A runner that measures a false-green rate is worth exactly as much as its
ability to REPORT one. These drive it against a door that passes everything
(the rate must read 100 percent, not 0), a door that refuses everything (0
percent), and a case class that cannot be built at all (NO-DATA, excluded
from n, never counted as RIGHT), plus one real case through the real door so
the harness is known to be wired to the product and not only to its own
stubs.
"""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gauntlet_delegation_truth as G  # noqa: E402
import work_record  # noqa: E402

# E100: one sandbox for every temp tree this process makes, removed at exit.
try:  # noqa: E402
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % os.path.basename(__file__))


def stub_build(work_dir):
    """A context the stub doors below never read. The builders that make a
    real repository are exercised by the real-door test at the bottom."""
    return {"repo": work_dir, "claims": {}, "done_ids": []}


def unbuildable(work_dir):
    raise G.Unbuildable("no seam exists for this class in the current door")


def door_that_passes_everything(ctx):
    return [{"id": "U1", "state": "verified", "reason": ""}]


def door_that_refuses_everything(ctx):
    return [{"id": "U1", "state": "refused", "reason": "seeded refusal"}]


def case(case_id, build=stub_build, judge=G.judge_refuse):
    return {"id": case_id, "condition": "weak test", "expected": "refuse",
            "build": build, "judge": judge}


class TheRateCountsWhatTheDoorLetThrough(unittest.TestCase):

    def _record(self, cases, door):
        results, excluded, no_verdict = G.run_gauntlet(cases=cases, door=door)
        return G.record_for(results, excluded, no_verdict, "abc123", False)

    def test_a_door_that_passes_every_seeded_case_reads_one_hundred_percent(
            self):
        record = self._record((case("a"), case("b")),
                              door_that_passes_everything)
        self.assertEqual(record["n"], 2)
        self.assertEqual(record["false_greens"], 2)
        self.assertEqual(record["rate_percent"], 100.0)
        self.assertEqual(record["false_green_cases"], ["a", "b"])
        out = io.StringIO()
        G.report(record, stream=out)
        self.assertIn("false-green rate: 2 of 2 (100.0 percent)",
                      out.getvalue())
        self.assertIn(G.FALSE_GREEN, out.getvalue())

    def test_a_door_that_refuses_every_seeded_case_reads_zero(self):
        record = self._record((case("a"), case("b")),
                              door_that_refuses_everything)
        self.assertEqual(record["n"], 2)
        self.assertEqual(record["false_greens"], 0)
        self.assertEqual(record["rate_percent"], 0.0)
        out = io.StringIO()
        G.report(record, stream=out)
        self.assertIn("false-green rate: 0 of 2 (0.0 percent)", out.getvalue())

    def test_an_unbuildable_class_is_no_data_and_leaves_n(self):
        record = self._record((case("a"), case("b", build=unbuildable)),
                              door_that_refuses_everything)
        self.assertEqual(record["n"], 1)
        self.assertEqual([c["case"] for c in record["cases"]], ["a"])
        self.assertEqual([e["case"] for e in record["excluded_no_data"]], ["b"])
        # NO-DATA is never counted as RIGHT: the excluded case appears in
        # neither the scored rows nor the numerator.
        self.assertNotIn("b", [r["case"] for r in record["cases"]])
        out = io.StringIO()
        G.report(record, stream=out)
        self.assertIn("NO-DATA: b (weak test)", out.getvalue())
        self.assertIn("excluded from n", out.getvalue())
        self.assertIn("false-green rate: 0 of 1", out.getvalue())

    def test_no_case_at_all_reports_no_rate(self):
        record = self._record((case("b", build=unbuildable),),
                              door_that_refuses_everything)
        self.assertEqual(record["n"], 0)
        self.assertIsNone(record["rate_percent"])
        out = io.StringIO()
        G.report(record, stream=out)
        self.assertIn("no case could be built", out.getvalue())
        self.assertNotIn("false-green rate:", out.getvalue())

    def test_a_case_that_produces_no_verdict_refuses_the_whole_rate(self):
        """The row's own done_check: a run refuses to report a rate when any
        case produced no verdict. A door that hands back no receipt for the
        unit is exactly that."""
        record = self._record((case("a"),), lambda ctx: [])
        self.assertEqual(record["no_verdict"], ["a"])
        out = io.StringIO()
        G.report(record, stream=out)
        self.assertIn("produced no verdict, so no rate is reported",
                      out.getvalue())
        self.assertNotIn("false-green rate:", out.getvalue())


class TheExitCodeSaysTheSameThingAsThePrintedTable(unittest.TestCase):
    """A verdict read off stdout and an exit code that disagrees with it is
    how a red gate lands green in a battery. Driven all three ways."""

    def _record(self, cases, door):
        results, excluded, no_verdict = G.run_gauntlet(cases=cases, door=door)
        return G.record_for(results, excluded, no_verdict, "abc123", False)

    def test_a_false_green_exits_one(self):
        self.assertEqual(G.exit_code(self._record(
            (case("a"),), door_that_passes_everything)), 1)

    def test_a_clean_run_exits_zero(self):
        self.assertEqual(G.exit_code(self._record(
            (case("a"),), door_that_refuses_everything)), 0)

    def test_no_rate_exits_three_and_never_zero(self):
        self.assertEqual(G.exit_code(self._record(
            (case("b", build=unbuildable),), door_that_refuses_everything)), 3)
        self.assertEqual(G.exit_code(self._record(
            (case("a"),), lambda ctx: [])), 3)


class TheFrozenCorpusGuardRefusesAMovedSpec(unittest.TestCase):
    """gauntlet_frozen.check() runs on the spec before any case is scored.
    Row S27's guard must actually be wired in here, not only present in its
    own module, so this drives it against a temp copy of the real spec
    (never the tree's own file) both ways: unmutated scores normally,
    mutated refuses before a single case is built."""

    def setUp(self):
        fd, self.spec_path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        shutil.copyfile(G.SPEC_PATH, self.spec_path)
        self._real_spec_path = G.SPEC_PATH
        G.SPEC_PATH = self.spec_path

    def tearDown(self):
        G.SPEC_PATH = self._real_spec_path
        os.unlink(self.spec_path)

    def test_unmutated_copy_scores_normally(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = G.main(["--no-write"])
        self.assertIn("frozen: OK", out.getvalue())
        self.assertNotIn("REFUSED", out.getvalue())
        self.assertEqual(code, 0)

    def test_a_mutated_seed_definition_is_refused_before_scoring(self):
        with open(self.spec_path, encoding="utf-8") as fh:
            spec = json.load(fh)
        spec["seeded_conditions_note"] = (
            spec.get("seeded_conditions_note", "") + " (mutated)")
        with open(self.spec_path, "w", encoding="utf-8") as fh:
            json.dump(spec, fh)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = G.main(["--no-write"])
        self.assertIn("REFUSED: corpus hash moved", out.getvalue())
        self.assertNotIn("frozen: OK", out.getvalue())
        self.assertEqual(code, 1)


class TheRealDoorOnARealSeededCase(unittest.TestCase):
    """One case built for real and run through brother_run and receipt_door,
    so the stubs above are known to stand in for something that works. The
    weak test is the one row S9 names by hand: it must read NO-DATA, never a
    pass."""

    def test_the_seeded_weak_test_reads_no_data_rather_than_a_pass(self):
        tmp = tempfile.mkdtemp(prefix="gauntlet-weak-test-")
        ctx = G.build_weak_test(tmp)
        receipts = G.run_door(ctx)
        self.assertEqual(receipts[0]["state"], "no-data")
        self.assertIn("already passed before the work",
                      receipts[0]["reason"])
        word, observed, _reason = G.judge_refuse(ctx, receipts)
        self.assertEqual((word, observed), (G.RIGHT, "no-data"))

    def test_the_same_case_reads_false_green_with_the_doors_rule_removed(self):
        """The case is RIGHT because of ONE rule, and this names it. With
        `check_passed_before` rewritten to False (the fact the engine's own
        precheck measured, taken away), the identical fixture comes back
        verified and the runner counts a FALSE-GREEN. Without this, a 0
        percent rate could just as well mean the corpus cannot detect
        anything."""
        tmp = tempfile.mkdtemp(prefix="gauntlet-weak-test-blinded-")
        ctx = G.build_weak_test(tmp)
        with open(ctx["record_path"], "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        doc["rows"][0]["check_passed_before"] = False
        work_record.write_record(ctx["record_path"], doc)
        receipts = G.run_door(ctx)
        self.assertEqual(receipts[0]["state"], "verified")
        word, _observed, _reason = G.judge_refuse(ctx, receipts)
        self.assertEqual(word, G.FALSE_GREEN)


if __name__ == "__main__":
    unittest.main(verbosity=2)
