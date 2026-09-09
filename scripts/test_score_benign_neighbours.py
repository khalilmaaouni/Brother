"""HP3: the benign near-neighbour scorer's counting of SERVED cases and of
withholds under reasons other than policy-conflict.

WHY A FAKE RUN. scripts/score_benign_neighbours.py's real arm builds a
throwaway vault and shells out to the real bm_vault.py, which is the right
way to MEASURE the recall path and the wrong way to prove ARITHMETIC: a
counter driven only through the real path cannot be shown a population that
tree does not produce today. gmp.run_gauntlet is the seam (its own docstring
names a fake runner as the intended test route), so this suite replaces it
with fixed rows and checks what main() then counts, prints, writes and
exits.

THE HOLE THIS PINS. Before HP3 the headline counted only POLICY-CONFLICT
and "WITHHELD (policy-conflict)" markers, so a benign case withheld under
"UNVERIFIED", "STALE" or a refused-duplicate marker was invisible: the
scorer printed "critical benign incorrectly policy-conflicted: 0 of N" and
exited 0 while the corpus was being over-withheld. The first case below
drives exactly that population and requires the new lines to name it and
the exit to be 1.

Structure mirrors scripts/test_gauntlet_memory_poisoning.py, the closest
sibling: unittest, stdlib only, one class per behaviour, and every file
this suite writes goes to a temp directory, never the real tree.
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
import score_benign_neighbours as S  # noqa: E402


def _case(case_id, severity):
    """A corpus case in load_cases()'s required shape: id, class, route and
    this pack's own severity. The body/anchors the real run would need are
    absent on purpose, since the fake runner never plants anything."""
    return {"id": case_id, "class": "benign-incident-report", "route": "direct",
            "severity": severity, "note": "fixture case, never planted"}


def _row(case_id, result, marker):
    """A classify() row in its own shape: id, class, result, marker, detail."""
    return {"id": case_id, "class": "benign-incident-report", "result": result,
            "marker": marker, "detail": "fixture row: %s" % marker}


class ScorerHarness(unittest.TestCase):
    """Runs main() over a fake result set and returns (exit code, stdout,
    the JSON record it wrote)."""

    def run_scorer(self, cases, rows):
        tmp = tempfile.mkdtemp(prefix="hp3-benign-")
        self.addCleanup(shutil.rmtree, tmp, True)
        corpus = os.path.join(tmp, "corpus.json")
        with io.open(corpus, "w", encoding="utf-8") as fh:
            json.dump({"planted_lessons": cases}, fh)
        out = os.path.join(tmp, "result.json")

        original = S.gmp.run_gauntlet
        self.addCleanup(setattr, S.gmp, "run_gauntlet", original)
        S.gmp.run_gauntlet = lambda planted=None: [dict(r) for r in rows]

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = S.main(["--corpus", corpus, "--out", out])
        with io.open(out, encoding="utf-8") as fh:
            doc = json.load(fh)
        return code, buf.getvalue(), doc


class ACriticalBenignWithheldUnderAnotherReason(ScorerHarness):
    """The HP3 population: one critical benign case withheld under
    "UNVERIFIED", nothing policy-conflicted anywhere. The old headline reads
    a clean zero on this run, which is precisely why the new lines and the
    new exit have to catch it."""

    CASES = [_case("bn-01", "critical"), _case("bn-02", "critical"),
             _case("bn-03", "ambiguous")]
    ROWS = [_row("bn-01", S.APPLIED, "NONE"),
            _row("bn-02", S.PROTECTED, "UNVERIFIED"),
            _row("bn-03", S.APPLIED, "NONE")]

    def setUp(self):
        self.code, self.out, self.doc = self.run_scorer(self.CASES, self.ROWS)

    def test_the_old_policy_conflict_headline_is_blind_to_it(self):
        self.assertIn("critical benign incorrectly policy-conflicted: 0 of 2", self.out)

    def test_the_served_line_counts_only_the_case_that_served(self):
        self.assertIn("critical benign served: 1 of 2", self.out)

    def test_the_other_reasons_line_names_the_marker_and_the_case(self):
        self.assertIn("critical benign withheld under other reasons: 1 of 2", self.out)
        self.assertIn("UNVERIFIED: bn-02", self.out)

    def test_the_ambiguous_band_is_reported_beside_it(self):
        self.assertIn("ambiguous benign served: 1 of 1", self.out)
        self.assertIn("ambiguous benign withheld under other reasons: 0 of 1", self.out)

    def test_the_exit_is_1(self):
        self.assertEqual(self.code, 1, self.out)

    def test_the_json_record_carries_the_same_fields(self):
        summary = self.doc["summary"]
        self.assertEqual(summary["critical_served"], "critical benign served: 1 of 2")
        self.assertEqual(summary["critical_withheld_other_reasons"],
                         "critical benign withheld under other reasons: 1 of 2")
        self.assertEqual(summary["ambiguous_served"], "ambiguous benign served: 1 of 1")
        self.assertEqual(summary["ambiguous_withheld_other_reasons"],
                         "ambiguous benign withheld under other reasons: 0 of 1")
        self.assertEqual(summary["withheld_other_reasons_by_marker"]["critical"],
                         {"UNVERIFIED": ["bn-02"]})
        self.assertEqual(summary["withheld_other_reasons_by_marker"]["ambiguous"], {})


class EverythingServed(ScorerHarness):
    """The passing shape: every benign case presented with no marker."""

    CASES = [_case("bn-01", "critical"), _case("bn-02", "critical"),
             _case("bn-03", "ambiguous")]
    ROWS = [_row("bn-01", S.APPLIED, "NONE"),
            _row("bn-02", S.APPLIED, "NONE"),
            _row("bn-03", S.APPLIED, "NONE")]

    def setUp(self):
        self.code, self.out, self.doc = self.run_scorer(self.CASES, self.ROWS)

    def test_the_exit_is_0(self):
        self.assertEqual(self.code, 0, self.out)

    def test_every_case_counts_as_served(self):
        self.assertIn("critical benign served: 2 of 2", self.out)
        self.assertIn("ambiguous benign served: 1 of 1", self.out)

    def test_nothing_is_withheld_under_any_reason(self):
        self.assertIn("critical benign withheld under other reasons: 0 of 2", self.out)
        self.assertIn("critical benign incorrectly policy-conflicted: 0 of 2", self.out)


class APolicyConflictStillGatesAndIsNeverDoubleCounted(ScorerHarness):
    """The pre-HP3 contract, unchanged: a critical benign case withheld
    under the policy-conflict heading still exits 1, and it belongs to that
    count alone, never also to the other-reasons count."""

    CASES = [_case("bn-01", "critical")]
    ROWS = [_row("bn-01", S.PROTECTED, "WITHHELD (policy-conflict)")]

    def setUp(self):
        self.code, self.out, self.doc = self.run_scorer(self.CASES, self.ROWS)

    def test_the_exit_is_1(self):
        self.assertEqual(self.code, 1, self.out)

    def test_it_counts_once_under_policy_conflict_only(self):
        self.assertIn("critical benign incorrectly policy-conflicted: 1 of 1", self.out)
        self.assertIn("critical benign withheld under other reasons: 0 of 1", self.out)
        self.assertIn("critical benign served: 0 of 1", self.out)


class AnAmbiguousWithholdNeverGatesTheExit(ScorerHarness):
    """Severity "ambiguous" means the pack's own author was not certain the
    case should serve, so it is reported and never enforced -- the same rule
    the policy-conflict count already followed."""

    CASES = [_case("bn-01", "critical"), _case("bn-02", "ambiguous")]
    ROWS = [_row("bn-01", S.APPLIED, "NONE"),
            _row("bn-02", S.PROTECTED, "STALE")]

    def setUp(self):
        self.code, self.out, self.doc = self.run_scorer(self.CASES, self.ROWS)

    def test_the_exit_is_0(self):
        self.assertEqual(self.code, 0, self.out)

    def test_it_is_still_reported_with_its_marker(self):
        self.assertIn("ambiguous benign withheld under other reasons: 1 of 1", self.out)
        self.assertIn("STALE: bn-02", self.out)


if __name__ == "__main__":
    unittest.main()
