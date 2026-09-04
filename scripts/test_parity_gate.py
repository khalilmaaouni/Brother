"""What the parity gate must keep true.

The table this replaced carried numbers nobody could trace. So every test here
is aimed at one property: a level is granted by evidence, never by assertion,
and an unassessed capability lowers the score rather than inheriting a guess.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import parity_gate as P  # noqa: E402


def cell(level=3, evidence="a real thing", weight=10, critical=False, name="X"):
    return {"capability": name, "level": level, "evidence": evidence,
            "weight": weight, "critical": critical}


class ALevelIsGrantedByEvidenceNeverByAssertion(unittest.TestCase):
    def test_a_level_with_no_evidence_earns_nothing(self):
        frac, note = P.credit(cell(level=4, evidence=""))
        self.assertEqual(frac, 0.0)
        self.assertIn("never by assertion", note)

    def test_whitespace_is_not_evidence(self):
        self.assertEqual(P.credit(cell(level=4, evidence="   \n "))[0], 0.0)

    def test_an_unassessed_cell_scores_zero_not_a_guess(self):
        frac, note = P.credit(cell(level=None))
        self.assertEqual(frac, 0.0)
        self.assertIn("rather than an optimistic guess", note)

    def test_claiming_a_high_level_without_evidence_cannot_raise_the_score(self):
        honest = [cell(level=3), cell(level=0, evidence="nothing built")]
        flattering = [cell(level=3), cell(level=4, evidence="")]
        self.assertEqual(P.score(honest)[0], P.score(flattering)[0])

    def test_an_out_of_range_level_is_refused(self):
        self.assertEqual(P.credit(cell(level=9))[0], 0.0)


class TheCurveIsSteepWhereItMatters(unittest.TestCase):
    """L3 is the first level a teammate would actually meet, so a proof in a
    controlled slice must not read like a product."""

    def test_a_slice_earns_much_less_than_the_product_path(self):
        self.assertLess(P.LEVEL_CREDIT[2], P.LEVEL_CREDIT[3] - 0.3)

    def test_documented_only_earns_nothing_at_all(self):
        self.assertEqual(P.LEVEL_CREDIT[0], 0.0)

    def test_only_surviving_adversity_earns_full_credit(self):
        self.assertEqual(P.LEVEL_CREDIT[4], 1.0)
        self.assertLess(P.LEVEL_CREDIT[3], 1.0)


class TheGateNamesWhatBlocksIt(unittest.TestCase):
    def test_a_critical_cell_below_L3_blocks(self):
        _pct, _rows, blocking = P.score([cell(level=2, critical=True, name="exec")])
        self.assertEqual([b["capability"] for b in blocking], ["exec"])

    def test_a_noncritical_cell_below_L3_does_not_block(self):
        self.assertEqual(P.score([cell(level=0, critical=False)])[2], [])

    def test_a_critical_cell_at_L3_does_not_block(self):
        self.assertEqual(P.score([cell(level=3, critical=True)])[2], [])

    def test_an_unassessed_critical_cell_blocks(self):
        """Unknown is not permission to open a gate."""
        self.assertEqual(len(P.score([cell(level=None, critical=True)])[2]), 1)

    def test_the_command_exits_nonzero_while_the_gate_is_shut(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "parity.json")
        with open(p, "w", encoding="utf-8") as fh:
            json.dump({"capabilities": [cell(level=1, critical=True)]}, fh)
        self.assertEqual(P.main(["--source", p]), 1)

    def test_it_exits_zero_only_when_every_critical_cell_is_on_the_path(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "parity.json")
        with open(p, "w", encoding="utf-8") as fh:
            json.dump({"capabilities": [cell(level=3, critical=True)]}, fh)
        self.assertEqual(P.main(["--source", p]), 0)


class NoDataIsNeverAPass(unittest.TestCase):
    def test_an_unreadable_source_exits_NO_DATA(self):
        self.assertEqual(P.main(["--source", "/no/such/parity.json"]), 2)

    def test_no_weights_at_all_is_NO_DATA_rather_than_zero_percent(self):
        self.assertIsNone(P.score([cell(weight=0)])[0])

    def test_the_bar_shows_NO_DATA_rather_than_an_empty_bar(self):
        self.assertIn(P.NODATA, P.bar(None))


class TheShippedAssessmentIsHonest(unittest.TestCase):
    """These run against the real file, because the risk is not that the code is
    wrong but that somebody later writes a cheerful level into the data."""

    def load(self):
        with open(P.SOURCE, encoding="utf-8") as fh:
            return json.load(fh)["capabilities"]

    def test_every_capability_names_evidence(self):
        bare = [c["capability"] for c in self.load()
                if not str(c.get("evidence", "")).strip()]
        self.assertEqual(bare, [], "levels claimed with no evidence: %s" % bare)

    def test_the_weights_are_the_directive_s_own_and_sum_to_one_hundred_and_seven(self):
        """The original twelve summed to 100. E56 added a thirteenth row,
        point-of-need memory and repeat prevention, at weight 7 (matching the
        other critical mid-tier rows: scope auditing and crash recovery are
        also weight 7). The gate itself normalises by the total weight
        (parity_gate.score divides by sum(weight)), so the sum need not stay
        100 for the percentages to read correctly; this test just pins the
        number so a silent weight edit is caught."""
        self.assertEqual(sum(c["weight"] for c in self.load()), 107)

    def test_the_thirteenth_capability_prints_with_its_level(self):
        """E56: the parity file had no memory-and-learning row. Prove the CLI
        actually surfaces it, not just that the JSON holds it. The plain-text
        table truncates a capability name to 24 characters (parity_gate.main:
        str(r["capability"])[:24]), so check the truncated form there and the
        full name plus level in --json, which does not truncate."""
        name = "Point-of-need memory and repeat prevention"
        row = next(c for c in self.load() if c["capability"] == name)

        text = subprocess.run([sys.executable, P.__file__],
                               capture_output=True, text=True, check=False)
        self.assertIn(name[:24], text.stdout)

        as_json = subprocess.run([sys.executable, P.__file__, "--json"],
                                  capture_output=True, text=True, check=False)
        payload = json.loads(as_json.stdout)
        printed = next(r for r in payload["rows"] if r["capability"] == name)
        self.assertEqual(printed["level"], row["level"])

    def test_every_named_script_or_module_path_resolves_on_disk(self):
        """A level is granted by evidence, and evidence that cites a file
        which does not exist is worse than no evidence: it looks checkable
        and is not. Scan every evidence string for a scripts/x.py or
        products/x/y.py path and require it to resolve relative to the repo
        root. A miss is reported, not silently skipped or auto-fixed."""
        pattern = re.compile(r"\b((?:scripts|products)/[A-Za-z0-9_./-]+\.py)\b")
        missing = []
        for c in self.load():
            for path in sorted(set(pattern.findall(c.get("evidence", "")))):
                if not os.path.exists(os.path.join(P.ROOT, path)):
                    missing.append("%s: %s" % (c["capability"], path))
        self.assertEqual(missing, [],
                          "evidence names a path that does not resolve on "
                          "disk (finding, not auto-fixed): %s" % missing)

    def test_every_level_is_in_range(self):
        for c in self.load():
            self.assertIn(c.get("level"), P.LEVEL_CREDIT, c["capability"])

    def test_an_L3_or_higher_claim_names_something_runnable_or_landed(self):
        """The cheapest way for this board to start lying is a level 3 whose
        evidence is a sentence of intent."""
        for c in self.load():
            if c["level"] >= 3:
                ev = c["evidence"].lower()
                self.assertTrue(
                    any(k in ev for k in ("scripts/", ".py", "exit 0", "landed",
                                          "/brother", "tests")),
                    "%s claims L%d without naming anything runnable"
                    % (c["capability"], c["level"]))

    def test_the_gate_state_is_consistent_with_its_own_blocking_list(self):
        """CORRECTED 2026-08-30: the original pinned 'the gate is currently
        shut', a point-in-time fact that expired the day every critical
        capability reached level 3. The durable invariant is consistency:
        the blocking list holds exactly the criticals below level 3, and the
        percentage reads 100 only when every capability sits at the top
        level. A test that pins today's state goes red on the day the
        product succeeds, which is the inverted-check failure this estate
        has already recorded once."""
        caps = self.load()
        pct, _rows, blocking = P.score(caps)
        expected_blocking = sorted(c["capability"] for c in caps
                                   if c.get("critical") and c["level"] < 3)
        self.assertEqual(sorted(blocking), expected_blocking)
        if any(c["level"] < max(P.LEVEL_CREDIT) for c in caps):
            self.assertLess(pct, 100)


if __name__ == "__main__":
    unittest.main()
