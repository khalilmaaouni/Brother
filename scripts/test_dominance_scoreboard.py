"""What the Dominance scoreboard must keep true.

Its whole reason to exist is one property: no composite score is produced
while any mandatory metric is unmeasured, where "mandatory" is decided by
mandatory_ids, a set declared OUTSIDE the sweep, never by a record's own
"mandatory" field. Round 2 found the escape a self-declaring sweep has: omit
a mandatory metric's record entirely, or mark it "mandatory": false on
itself, and the naive version scored anyway. Every test in
NoCompositeWhileAnyMandatoryMetricIsUnmeasured and TheMandatorySetIsPinnedOutsideTheSweep
must fail red if that guard is removed or weakened; that is the bad state a
naive scoreboard would also pass.

Run: python3 scripts/test_dominance_scoreboard.py -v
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import dominance_scoreboard as D  # noqa: E402

try:
    import tmp_sandbox
    tmp_sandbox.install()
except ImportError:
    sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % os.path.basename(__file__))


def metric(metric_id="M1", decision="whether to ship it", weight=1.0,
           value=0.8, evidence="a real run", label=None, mandatory=None):
    """A metric record. mandatory is an optional self-declaration (None
    omits the field entirely): gating comes from mandatory_ids passed to
    evaluate()/main(), never from this field alone."""
    rec = {
        "metric_id": metric_id,
        "decision": decision,
        "weight": weight,
    }
    if value is not None:
        rec["value"] = value
    if evidence is not None:
        rec["evidence"] = evidence
    if label is not None:
        rec["label"] = label
    if mandatory is not None:
        rec["mandatory"] = mandatory
    return rec


def unmeasured(**kw):
    kw.setdefault("value", None)
    kw.setdefault("evidence", None)
    return metric(**kw)


class EveryMetricNamesItsDecisionAtBirth(unittest.TestCase):
    def test_missing_decision_is_refused(self):
        rec = metric()
        del rec["decision"]
        with self.assertRaises(D.DominanceError) as ctx:
            D.validate_metric(rec)
        self.assertIn("decision", str(ctx.exception))

    def test_empty_decision_is_refused(self):
        with self.assertRaises(D.DominanceError):
            D.validate_metric(metric(decision="   "))

    def test_missing_decision_is_refused_even_when_unmeasured(self):
        rec = unmeasured()
        del rec["decision"]
        with self.assertRaises(D.DominanceError):
            D.validate_metric(rec)

    def test_missing_metric_id_is_refused(self):
        rec = metric()
        del rec["metric_id"]
        with self.assertRaises(D.DominanceError):
            D.validate_metric(rec)

    def test_mandatory_field_is_optional_now(self):
        rec = metric()
        self.assertNotIn("mandatory", rec)
        self.assertIsNone(D.validate_metric(rec))

    def test_mandatory_not_a_bool_is_refused(self):
        rec = metric(mandatory="yes")
        with self.assertRaises(D.DominanceError):
            D.validate_metric(rec)

    def test_unrecognized_field_is_refused(self):
        rec = metric()
        rec["extra"] = "surprise"
        with self.assertRaises(D.DominanceError):
            D.validate_metric(rec)

    def test_out_of_range_value_is_refused(self):
        with self.assertRaises(D.DominanceError):
            D.validate_metric(metric(value=1.5))

    def test_nonpositive_weight_is_refused(self):
        with self.assertRaises(D.DominanceError):
            D.validate_metric(metric(weight=0))

    def test_bool_value_is_not_a_number(self):
        rec = metric()
        rec["value"] = True
        with self.assertRaises(D.DominanceError):
            D.validate_metric(rec)


class AValueWithNoEvidenceIsNotAMeasurement(unittest.TestCase):
    def test_value_present_evidence_missing_is_unmeasured(self):
        rec = metric()
        del rec["evidence"]
        self.assertFalse(D.is_measured(rec))

    def test_value_present_evidence_blank_is_unmeasured(self):
        self.assertFalse(D.is_measured(metric(evidence="   ")))

    def test_value_and_evidence_present_is_measured(self):
        self.assertTrue(D.is_measured(metric()))

    def test_no_value_at_all_is_unmeasured(self):
        self.assertFalse(D.is_measured(unmeasured()))


class TheMandatorySetIsPinnedOutsideTheSweep(unittest.TestCase):
    """Round 2's fix. mandatory_ids is the only authority for what is
    mandatory; a sweep can neither omit its way out nor talk its way out."""

    def test_omitted_mandatory_metric_blocks_the_composite(self):
        # B is never given a record at all, only named in mandatory_ids.
        # This is the exact escape found in review: a sweep that simply
        # leaves a mandatory metric out entirely used to score.
        metrics = [metric(metric_id="A", value=1.0, decision="d1")]
        result = D.evaluate(metrics, mandatory_ids=["A", "B"])
        self.assertEqual(result["verdict"], D.NODATA)
        self.assertIsNone(result["composite"])
        gap = {(r["metric_id"], r["present"]) for r in result["mandatory_gap"]}
        self.assertEqual(gap, {("B", False)})

    def test_the_omitted_row_names_no_decision_and_is_marked_absent(self):
        result = D.evaluate(
            [metric(metric_id="A", value=1.0, decision="d1")],
            mandatory_ids=["A", "B"])
        [absent] = [r for r in result["mandatory_gap"] if r["metric_id"] == "B"]
        self.assertFalse(absent["present"])
        self.assertIsNone(absent["decision"])

    def test_self_demoted_mandatory_metric_is_refused(self):
        # A's own record says "mandatory": false, but the registry says A
        # is mandatory. A record may not overrule the registry that named
        # it.
        metrics = [metric(metric_id="A", value=1.0, decision="d1",
                           mandatory=False)]
        with self.assertRaises(D.DominanceError) as ctx:
            D.evaluate(metrics, mandatory_ids=["A"])
        self.assertIn("demote", str(ctx.exception))

    def test_self_declared_mandatory_true_does_not_widen_the_registry(self):
        # B claims "mandatory": true on itself, but the registry (the only
        # authority) does not name it. B is optional, and an unmeasured B
        # must not block the composite: the registry, not the record,
        # decides.
        metrics = [
            metric(metric_id="A", value=0.5, decision="d1"),
            unmeasured(metric_id="B", decision="d2", mandatory=True),
        ]
        result = D.evaluate(metrics, mandatory_ids=[])
        self.assertEqual(result["verdict"], D.SCORED)
        self.assertEqual(result["mandatory_gap"], [])
        self.assertEqual([r["metric_id"] for r in result["optional_gap"]], ["B"])

    def test_no_mandatory_ids_argument_at_all_is_refused(self):
        with self.assertRaises(D.DominanceError):
            D.evaluate([metric()], mandatory_ids=None)

    def test_mandatory_ids_with_non_string_entry_is_refused(self):
        with self.assertRaises(D.DominanceError):
            D.evaluate([metric()], mandatory_ids=[123])

    def test_mandatory_ids_with_duplicate_entry_is_refused(self):
        with self.assertRaises(D.DominanceError):
            D.evaluate([metric()], mandatory_ids=["A", "A"])

    def test_empty_mandatory_ids_is_a_legal_declaration_of_none_mandatory(self):
        result = D.evaluate([metric(metric_id="A", value=0.4)],
                             mandatory_ids=[])
        self.assertEqual(result["verdict"], D.SCORED)
        self.assertEqual(result["mandatory_gap"], [])


class NoCompositeWhileAnyMandatoryMetricIsUnmeasured(unittest.TestCase):
    """The deciding property. Every test in this class must fail red if the
    gating guard in evaluate() is removed or weakened."""

    def test_one_unmeasured_mandatory_metric_blocks_the_whole_composite(self):
        result = D.evaluate(
            [metric(metric_id="A", value=1.0),
             unmeasured(metric_id="B", decision="whether to widen scope")],
            mandatory_ids=["B"])
        self.assertEqual(result["verdict"], D.NODATA)
        self.assertIsNone(result["composite"])

    def test_the_gap_names_the_metric_and_its_decision(self):
        result = D.evaluate(
            [unmeasured(metric_id="B", decision="whether to widen scope")],
            mandatory_ids=["B"])
        self.assertEqual(len(result["mandatory_gap"]), 1)
        self.assertEqual(result["mandatory_gap"][0]["metric_id"], "B")
        self.assertEqual(result["mandatory_gap"][0]["decision"],
                          "whether to widen scope")

    def test_missing_mandatory_metric_is_never_read_as_zero(self):
        # If the guard were replaced by "treat missing as 0.0 and average
        # anyway", this composite would come out to 50.0 (average of 1.0
        # and 0.0) instead of refusing to score. Catch that regression.
        result = D.evaluate(
            [metric(metric_id="A", value=1.0, weight=1.0),
             unmeasured(metric_id="B", weight=1.0)],
            mandatory_ids=["B"])
        self.assertIsNone(result["composite"])
        self.assertNotEqual(result["composite"], 50.0)

    def test_missing_mandatory_metric_is_never_dropped_from_the_average(self):
        # If the guard were replaced by "drop missing mandatory metrics
        # from the denominator", this composite would come out to 100.0
        # (A alone) instead of refusing to score. Catch that regression too.
        result = D.evaluate(
            [metric(metric_id="A", value=1.0, weight=1.0),
             unmeasured(metric_id="B", weight=3.0)],
            mandatory_ids=["B"])
        self.assertIsNone(result["composite"])
        self.assertNotEqual(result["composite"], 100.0)

    def test_all_mandatory_measured_scores_even_with_an_unmeasured_optional(self):
        result = D.evaluate([
            metric(metric_id="A", value=1.0),
            unmeasured(metric_id="C", decision="whether to add a fourth runtime"),
        ], mandatory_ids=["A"])
        self.assertEqual(result["verdict"], D.SCORED)
        self.assertAlmostEqual(result["composite"], 100.0)
        self.assertEqual(len(result["mandatory_gap"]), 0)
        self.assertEqual(len(result["optional_gap"]), 1)

    def test_weighted_average_of_fully_measured_metrics(self):
        result = D.evaluate([
            metric(metric_id="A", value=1.0, weight=1.0),
            metric(metric_id="B", value=0.0, weight=1.0),
        ], mandatory_ids=["A", "B"])
        self.assertEqual(result["verdict"], D.SCORED)
        self.assertAlmostEqual(result["composite"], 50.0)

    def test_unmeasured_optional_excluded_from_the_composite_math(self):
        scored_without = D.evaluate([metric(metric_id="A", value=0.6)],
                                     mandatory_ids=["A"])
        scored_with_optional_gap = D.evaluate([
            metric(metric_id="A", value=0.6),
            unmeasured(metric_id="D"),
        ], mandatory_ids=["A"])
        self.assertAlmostEqual(scored_without["composite"],
                                scored_with_optional_gap["composite"])

    def test_no_mandatory_metrics_and_none_measured_is_still_no_data(self):
        result = D.evaluate(
            [unmeasured(metric_id="A", decision="whether to add a fourth runtime")],
            mandatory_ids=[])
        self.assertEqual(result["verdict"], D.NODATA)
        self.assertIsNone(result["composite"])
        self.assertEqual(result["mandatory_gap"], [])


class StructuralIntegrity(unittest.TestCase):
    def test_empty_metric_list_is_refused(self):
        with self.assertRaises(D.DominanceError):
            D.evaluate([], mandatory_ids=[])

    def test_duplicate_metric_id_is_refused(self):
        with self.assertRaises(D.DominanceError):
            D.evaluate([metric(metric_id="A"), metric(metric_id="A")],
                       mandatory_ids=[])

    def test_malformed_metric_raises_not_reported_as_gap(self):
        # A metric missing its decision is a defect in the sweep itself,
        # never silently folded into the NO-DATA gap list.
        rec = metric()
        del rec["decision"]
        with self.assertRaises(D.DominanceError):
            D.evaluate([rec], mandatory_ids=[])


class TheCommandLine(unittest.TestCase):
    def _write(self, metrics):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "dominance.json")
        with open(p, "w", encoding="utf-8") as fh:
            json.dump({"metrics": metrics}, fh)
        return p

    def _write_mandatory(self, ids):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "mandatory.json")
        with open(p, "w", encoding="utf-8") as fh:
            json.dump({"mandatory_ids": ids}, fh)
        return p

    def test_no_mandatory_flag_at_all_is_no_data_not_a_score(self):
        p = self._write([metric(metric_id="A", value=0.9)])
        self.assertEqual(D.main(["--source", p]), 1)

    def test_exits_zero_when_every_mandatory_metric_is_measured(self):
        p = self._write([metric(metric_id="A", value=0.9)])
        m = self._write_mandatory(["A"])
        self.assertEqual(D.main(["--source", p, "--mandatory", m]), 0)

    def test_exits_one_when_a_mandatory_metric_is_omitted_from_the_sweep(self):
        p = self._write([metric(metric_id="A", value=0.9)])
        m = self._write_mandatory(["A", "B"])
        self.assertEqual(D.main(["--source", p, "--mandatory", m]), 1)

    def test_exits_two_on_an_unreadable_source(self):
        m = self._write_mandatory(["A"])
        self.assertEqual(
            D.main(["--source", "/no/such/dominance.json", "--mandatory", m]),
            2)

    def test_exits_two_on_an_unreadable_mandatory_file(self):
        p = self._write([metric(metric_id="A", value=0.9)])
        self.assertEqual(
            D.main(["--source", p, "--mandatory", "/no/such/mandatory.json"]),
            2)

    def test_exits_two_on_a_malformed_metric_in_the_file(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "dominance.json")
        with open(p, "w", encoding="utf-8") as fh:
            json.dump({"metrics": [{"metric_id": "A"}]}, fh)  # no decision
        m = self._write_mandatory(["A"])
        self.assertEqual(D.main(["--source", p, "--mandatory", m]), 2)

    def test_exits_two_when_a_record_self_demotes_a_registry_mandatory_id(self):
        p = self._write([metric(metric_id="A", value=0.9, mandatory=False)])
        m = self._write_mandatory(["A"])
        self.assertEqual(D.main(["--source", p, "--mandatory", m]), 2)

    def test_json_output_mirrors_the_exit_code(self):
        p = self._write([unmeasured(metric_id="A", decision="whether to ship")])
        m = self._write_mandatory(["A"])
        self.assertEqual(D.main(["--source", p, "--mandatory", m, "--json"]), 1)

    def test_json_output_when_no_mandatory_flag_names_the_reason(self):
        p = self._write([metric(metric_id="A", value=0.9)])
        self.assertEqual(D.main(["--source", p, "--json"]), 1)

    def test_all_optional_and_unmeasured_exits_one_not_zero(self):
        # No mandatory metric at all, so mandatory_gap is empty, but
        # nothing was measured either: there is no average to report. A
        # main() that gates on "mandatory_gap truthy" alone would read
        # this as scored and exit 0 with a None composite; that is the
        # false pass this test exists to catch.
        p = self._write([unmeasured(metric_id="A",
                                     decision="whether to add a fourth runtime")])
        m = self._write_mandatory([])
        self.assertEqual(D.main(["--source", p, "--mandatory", m]), 1)


if __name__ == "__main__":
    unittest.main()
