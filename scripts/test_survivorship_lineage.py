import inspect
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import survivorship_lineage as sl  # noqa: E402

# Synthetic fixture data only -- fake generic source system names, never
# the first target estate's data.


class ResolveFieldShapeTests(unittest.TestCase):
    """resolve_field's decision half must be exactly the shape
    reversibility_gate.py's build_reversible_action() already consumes:
    {"value": ..., "winner_source_id": ...} per field."""

    def test_decision_shape_matches_reversibility_gate_consumption(self):
        decision, _ = sl.resolve_field(
            "customer_name",
            {"crm": {"value": "Acme Test Corp", "timestamp": "2026-01-01T00:00:00Z"}},
            sl.MOST_RECENT_SOURCE_WINS,
        )
        self.assertEqual(set(decision.keys()), {"value", "winner_source_id"})
        self.assertEqual(decision["value"], "Acme Test Corp")
        self.assertEqual(decision["winner_source_id"], "crm")

    def test_decision_is_directly_usable_by_reversibility_gate(self):
        # Real cross-module proof, not an assumption: build a
        # survivorship_decisions dict the way a caller of this module
        # would, feed it straight into reversibility_gate's own rollback
        # cycle, and confirm it accepts the shape with no adaptation.
        reversibility_gate = _import_reversibility_gate()
        pre_merge_records = {
            "crm": {"customer_name": "Acme Test Corp", "phone": "03-1234-5678"},
            "pos": {"customer_name": "Acme Test Corp Ltd.", "phone": "0312345678"},
        }
        name_decision, _ = sl.resolve_field(
            "customer_name",
            {
                "crm": {"value": "Acme Test Corp", "timestamp": "2026-02-01T00:00:00Z"},
                "pos": {"value": "Acme Test Corp Ltd.", "timestamp": "2026-01-01T00:00:00Z"},
            },
            sl.MOST_RECENT_SOURCE_WINS,
        )
        phone_decision, _ = sl.resolve_field(
            "phone",
            {"crm": {"value": ""}, "pos": {"value": "0312345678"}},
            sl.MOST_COMPLETE_VALUE_WINS,
        )
        survivorship_decisions = {"customer_name": name_decision, "phone": phone_decision}
        result = reversibility_gate.run_rollback_cycle(pre_merge_records, survivorship_decisions)
        self.assertTrue(result["ran"])
        self.assertTrue(result["matches_pre_state"])
        self.assertTrue(result["idempotent"])
        self.assertEqual(
            result["golden_record_produced"],
            {"customer_name": "Acme Test Corp", "phone": "0312345678"},
        )


class RuleExampleTests(unittest.TestCase):
    """One real, correct example per rule."""

    def test_most_recent_source_wins_picks_the_later_timestamp(self):
        decision, lineage = sl.resolve_field(
            "email",
            {
                "crm": {"value": "j.doe@example.test", "timestamp": "2026-01-05T00:00:00Z"},
                "pos": {"value": "jane.doe@example.test", "timestamp": "2026-03-10T00:00:00Z"},
            },
            sl.MOST_RECENT_SOURCE_WINS,
        )
        self.assertEqual(decision, {"value": "jane.doe@example.test", "winner_source_id": "pos"})
        self.assertIn("2026-03-10T00:00:00Z", lineage["reason"])
        self.assertIn("2026-01-05T00:00:00Z", lineage["reason"])
        self.assertEqual(len(lineage["sources_compared"]), 2)

    def test_most_complete_value_wins_prefers_non_empty(self):
        decision, lineage = sl.resolve_field(
            "phone",
            {"crm": {"value": ""}, "pos": {"value": "03-1234-5678"}},
            sl.MOST_COMPLETE_VALUE_WINS,
        )
        self.assertEqual(decision, {"value": "03-1234-5678", "winner_source_id": "pos"})
        self.assertIn("pos", lineage["reason"])

    def test_most_complete_value_wins_treats_null_as_empty(self):
        decision, _ = sl.resolve_field(
            "phone",
            {"crm": {"value": None}, "pos": {"value": "03-1234-5678"}},
            sl.MOST_COMPLETE_VALUE_WINS,
        )
        self.assertEqual(decision["winner_source_id"], "pos")

    def test_explicit_priority_list_picks_highest_ranked_present_source(self):
        rule = sl.make_explicit_priority_rule(["erp", "pos", "crm"])
        decision, lineage = sl.resolve_field(
            "customer_name",
            {"pos": {"value": "Acme Test Corp"}, "crm": {"value": "Acme Test Corp Ltd."}},
            rule,
        )
        # 'erp' outranks both but is absent; 'pos' is the highest-ranked
        # source actually present among the candidates.
        self.assertEqual(decision, {"value": "Acme Test Corp", "winner_source_id": "pos"})
        self.assertIn("erp", lineage["reason"])  # names the full list, not just the winner

    def test_explicit_priority_list_raises_when_no_priority_source_present(self):
        rule = sl.make_explicit_priority_rule(["erp"])
        with self.assertRaises(ValueError):
            sl.resolve_field("customer_name", {"pos": {"value": "x"}}, rule)


class LineageCompletenessTests(unittest.TestCase):
    """Every source compared must appear in the lineage, not just the
    winner, per the roadmap's field list ("competing values")."""

    def test_lineage_names_every_source_including_losers(self):
        _, lineage = sl.resolve_field(
            "email",
            {
                "crm": {"value": "a@example.test", "timestamp": "2026-01-01T00:00:00Z"},
                "pos": {"value": "b@example.test", "timestamp": "2026-02-01T00:00:00Z"},
                "erp": {"value": "c@example.test", "timestamp": "2026-01-15T00:00:00Z"},
            },
            sl.MOST_RECENT_SOURCE_WINS,
        )
        source_ids = {row["source_id"] for row in lineage["sources_compared"]}
        self.assertEqual(source_ids, {"crm", "pos", "erp"})
        outcomes = {row["source_id"]: row["outcome"] for row in lineage["sources_compared"]}
        self.assertEqual(outcomes["pos"], "chosen")
        self.assertEqual(outcomes["crm"], "lost")
        self.assertEqual(outcomes["erp"], "lost")

    def test_reason_is_specific_not_a_canned_string(self):
        # The reason must substitute real values in, not repeat a static
        # template with nothing filled in.
        _, lineage_a = sl.resolve_field(
            "email",
            {
                "crm": {"value": "a@example.test", "timestamp": "2026-01-01T00:00:00Z"},
                "pos": {"value": "b@example.test", "timestamp": "2026-02-01T00:00:00Z"},
            },
            sl.MOST_RECENT_SOURCE_WINS,
        )
        _, lineage_b = sl.resolve_field(
            "email",
            {
                "crm": {"value": "x@example.test", "timestamp": "2026-05-01T00:00:00Z"},
                "pos": {"value": "y@example.test", "timestamp": "2026-06-01T00:00:00Z"},
            },
            sl.MOST_RECENT_SOURCE_WINS,
        )
        self.assertNotEqual(lineage_a["reason"], lineage_b["reason"])


class AntiDriftTests(unittest.TestCase):
    """The Muse hostile-review finding: lineage must not be a decoupled
    observer that can drift from the engine's real decision."""

    def test_resolve_field_reads_rule_identity_from_the_rule_parameter(self):
        # Inspect resolve_field's own source: rule_name/rule_version must
        # be read directly off the `rule` argument, not off a second,
        # independently-maintained constant or config lookup.
        source = inspect.getsource(sl.resolve_field)
        self.assertIn("rule.name", source)
        self.assertIn("rule.version", source)
        self.assertIn("rule.logic_fingerprint", source)

    def test_lineage_rule_identity_tracks_whichever_rule_object_was_actually_passed(self):
        # Two rule objects that share the exact same comparison LOGIC but
        # declare different names/versions -- simulates asking "what does
        # the lineage say" for two different engine configurations. If
        # resolve_field ever read rule identity from anywhere other than
        # the `rule` object actually passed to it (e.g. a stale module
        # default), this test could not tell the two apart.
        candidates = {"crm": {"value": "Jane Doe", "timestamp": "2026-01-01T00:00:00Z"}}
        rule_v1 = sl.SurvivorshipRule("most_recent_source_wins", "1.0.0", sl._most_recent_compare)
        rule_v2 = sl.SurvivorshipRule("most_recent_source_wins", "2.0.0", sl._most_recent_compare)

        _, lineage_v1 = sl.resolve_field("name", candidates, rule_v1)
        _, lineage_v2 = sl.resolve_field("name", candidates, rule_v2)

        self.assertEqual(lineage_v1["rule_version"], "1.0.0")
        self.assertEqual(lineage_v2["rule_version"], "2.0.0")
        # Same logic -> same fingerprint regardless of the declared
        # version: the fingerprint is a property of the code, not the label.
        self.assertEqual(lineage_v1["rule_logic_fingerprint"], lineage_v2["rule_logic_fingerprint"])

    def test_changing_rule_logic_without_bumping_version_changes_the_fingerprint(self):
        # The exact Muse scenario: engine code is hotfixed (a tie-break
        # exception added) but the declared version string is forgotten.
        # rule_name/rule_version alone would look identical before and
        # after; logic_fingerprint must still expose the drift.
        def compare_before(candidates):
            sid = next(iter(candidates))
            return sl.Comparison(sid, candidates[sid]["value"], "first candidate wins", [])

        def compare_after_hotfix(candidates):
            # different logic: now prefers the LAST candidate instead
            sid = list(candidates)[-1]
            return sl.Comparison(sid, candidates[sid]["value"], "last candidate wins", [])

        rule_before = sl.SurvivorshipRule("custom_rule", "1.0.0", compare_before)
        rule_after_hotfix = sl.SurvivorshipRule("custom_rule", "1.0.0", compare_after_hotfix)  # version NOT bumped

        self.assertEqual(rule_before.name, rule_after_hotfix.name)
        self.assertEqual(rule_before.version, rule_after_hotfix.version)
        self.assertNotEqual(
            rule_before.logic_fingerprint,
            rule_after_hotfix.logic_fingerprint,
            "a hotfixed rule with an unbumped version must still be distinguishable by its logic fingerprint",
        )

    def test_fingerprint_is_stable_for_unchanged_logic(self):
        rule_a = sl.SurvivorshipRule("most_recent_source_wins", "1.0.0", sl._most_recent_compare)
        rule_b = sl.SurvivorshipRule("most_recent_source_wins", "1.0.0", sl._most_recent_compare)
        self.assertEqual(rule_a.logic_fingerprint, rule_b.logic_fingerprint)


class RuleTypeGuardTests(unittest.TestCase):
    def test_resolve_field_rejects_non_rule_objects(self):
        with self.assertRaises(TypeError):
            sl.resolve_field("x", {"crm": {"value": "y"}}, "not_a_rule")

    def test_resolve_field_rejects_empty_candidates(self):
        with self.assertRaises(ValueError):
            sl.resolve_field("x", {}, sl.MOST_RECENT_SOURCE_WINS)


def _import_reversibility_gate():
    import importlib

    return importlib.import_module("reversibility_gate")


if __name__ == "__main__":
    unittest.main(verbosity=2)
