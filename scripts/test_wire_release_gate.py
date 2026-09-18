#!/usr/bin/env python3
"""Tests for WIRE-05, scripts/wire_release_gate.py.

Each test below is named after one rule in docs/plan/ORCH-1020-WORKER-
CONTRACT.md's brief for this unit (see that file for the numbering), plus
the two named edge cases (zero Tier A parts, and "a green run that found
nothing to look at") and the machinery-refusal cases (missing, malformed,
duplicate part, unknown tier, unknown status, missing required key).

Fixtures are built as plain dicts and written to a tmp file per test
(tempfile.TemporaryDirectory), never against the real
docs/generated/ASSURANCE-COVERAGE.json: this file must stay correct even
after another unit regenerates that real file to a different shape.
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import assurance_coverage
import wire_release_gate as gate


def _part(name, tier, status, **extra):
    row = {"part": name, "tier": tier, "status": status}
    row.update(extra)
    return row


def _payload(parts, **top_level):
    payload = {
        "generated_by": "scripts/assurance_coverage.py",
        "part_count": len(parts),
        "parts": parts,
    }
    payload.update(top_level)
    return payload


class GateTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.coverage_path = os.path.join(self._tmp.name, "COVERAGE.json")
        self.scripts_dir = os.path.join(self._tmp.name, "scripts")
        os.makedirs(self.scripts_dir)
        self.log_path = os.path.join(self._tmp.name, "refusals.log")

    def _write(self, payload):
        with open(self.coverage_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)

    def _make_scripts(self, count):
        """count non-test .py files in self.scripts_dir, so part_count can
        be made to match or mismatch it on purpose."""
        for i in range(count):
            open(os.path.join(self.scripts_dir, "mod_%d.py" % i), "w").close()

    def check(self, payload):
        self._write(payload)
        self._make_scripts(payload.get("part_count", len(payload["parts"])))
        return gate.check(self.coverage_path, self.scripts_dir)


class TestRule1TierANoDataNamesEveryBlocker(GateTestCase):
    def test_one_tier_a_no_data_refuses_and_names_it(self):
        payload = _payload([
            _part("claim_score", "A", assurance_coverage.NO_DATA),
            _part("other_a", "A", assurance_coverage.STATUS_WIRED),
        ])
        result = self.check(payload)
        self.assertFalse(result.allowed)
        self.assertEqual(result.blockers, ["claim_score"])
        self.assertIn("claim_score", result.reason)
        # Rule 1: names every blocker, not a bare count.
        self.assertNotIn("1 Tier A part(s) report NO-DATA: \n", result.reason)

    def test_every_blocker_is_named_not_just_counted(self):
        payload = _payload([
            _part("alpha", "A", assurance_coverage.NO_DATA),
            _part("bravo", "A", assurance_coverage.NO_DATA),
            _part("charlie", "A", assurance_coverage.STATUS_WIRED),
        ])
        result = self.check(payload)
        self.assertFalse(result.allowed)
        self.assertEqual(result.blockers, ["alpha", "bravo"])
        self.assertIn("alpha", result.reason)
        self.assertIn("bravo", result.reason)
        self.assertNotIn("charlie", result.reason)


class TestRule2TierBAndTierCSurviveNoData(GateTestCase):
    def test_tier_b_no_data_does_not_block(self):
        payload = _payload([
            _part("supporting", "B", assurance_coverage.NO_DATA),
            _part("strategic", "A", assurance_coverage.STATUS_WIRED),
        ])
        result = self.check(payload)
        self.assertTrue(result.allowed, result.reason)
        self.assertEqual(result.blockers, [])

    def test_tier_c_no_data_does_not_block(self):
        payload = _payload([
            _part("reporter", "C", assurance_coverage.NO_DATA),
            _part("strategic", "A", assurance_coverage.STATUS_WIRED),
        ])
        result = self.check(payload)
        self.assertTrue(result.allowed, result.reason)
        self.assertEqual(result.blockers, [])


class TestRule3Unclassified(GateTestCase):
    """Chosen rule: UNCLASSIFIED reporting NO-DATA BLOCKS, exactly like
    Tier A, because worker-contract rule 2 forbids a permissive default for
    an unresolved enum value, and UNCLASSIFIED is exactly that: an unknown
    tier, not a known-harmless one."""

    def test_unclassified_no_data_blocks(self):
        payload = _payload([
            _part("mystery", "UNCLASSIFIED", assurance_coverage.NO_DATA),
            _part("strategic", "A", assurance_coverage.STATUS_WIRED),
        ])
        result = self.check(payload)
        self.assertFalse(result.allowed)
        self.assertEqual(result.unclassified_blockers, ["mystery"])
        self.assertEqual(result.unclassified_count, 1)
        self.assertIn("mystery", result.reason)
        # Never merged into the Tier A blockers list: readers must be able
        # to tell a known gap from an unresolved one.
        self.assertEqual(result.blockers, [])

    def test_unclassified_wired_does_not_block(self):
        payload = _payload([
            _part("mystery", "UNCLASSIFIED", assurance_coverage.STATUS_WIRED),
            _part("strategic", "A", assurance_coverage.STATUS_WIRED),
        ])
        result = self.check(payload)
        self.assertTrue(result.allowed, result.reason)
        self.assertEqual(result.unclassified_count, 0)


class TestRule4MissingCoverageFileRefuses(GateTestCase):
    def test_missing_file_raises_and_names_missing(self):
        os.remove(self.coverage_path) if os.path.exists(self.coverage_path) else None
        missing_path = os.path.join(self._tmp.name, "does-not-exist.json")
        with self.assertRaises(gate.ReleaseGateRefused) as ctx:
            gate.check(missing_path, self.scripts_dir)
        self.assertIn("missing", str(ctx.exception).lower())


class TestRule5MalformedDistinctFromMissing(GateTestCase):
    def test_malformed_json_raises_distinct_reason(self):
        with open(self.coverage_path, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        with self.assertRaises(gate.ReleaseGateRefused) as ctx:
            gate.check(self.coverage_path, self.scripts_dir)
        message = str(ctx.exception).lower()
        self.assertIn("malformed", message)
        self.assertNotIn("missing", message)

    def test_empty_file_is_malformed(self):
        open(self.coverage_path, "w").close()
        with self.assertRaises(gate.ReleaseGateRefused) as ctx:
            gate.check(self.coverage_path, self.scripts_dir)
        self.assertIn("malformed", str(ctx.exception).lower())

    def test_missing_and_malformed_messages_are_distinct(self):
        missing_path = os.path.join(self._tmp.name, "nope.json")
        try:
            gate.check(missing_path, self.scripts_dir)
        except gate.ReleaseGateRefused as exc:
            missing_message = str(exc)

        with open(self.coverage_path, "w", encoding="utf-8") as handle:
            handle.write("not json at all")
        try:
            gate.check(self.coverage_path, self.scripts_dir)
        except gate.ReleaseGateRefused as exc:
            malformed_message = str(exc)

        self.assertNotEqual(missing_message, malformed_message)


class TestRule6Staleness(GateTestCase):
    def test_matching_count_is_not_stale(self):
        payload = _payload([_part("a", "A", assurance_coverage.STATUS_WIRED)])
        result = self.check(payload)
        self.assertFalse(result.stale)

    def test_drifted_count_is_stale_but_still_allowed(self):
        self._write(_payload([_part("a", "A", assurance_coverage.STATUS_WIRED)]))
        # Live tree has more modules than the coverage file declared.
        self._make_scripts(5)
        result = gate.check(self.coverage_path, self.scripts_dir)
        self.assertTrue(result.stale)
        self.assertIn("STALE", result.reason)
        # Staleness alone never refuses (RULE 6): no blockers here.
        self.assertTrue(result.allowed, result.reason)

    def test_stale_and_blocked_still_reports_both(self):
        self._write(_payload([
            _part("claim_score", "A", assurance_coverage.NO_DATA),
        ]))
        self._make_scripts(9)
        result = gate.check(self.coverage_path, self.scripts_dir)
        self.assertTrue(result.stale)
        self.assertFalse(result.allowed)
        self.assertIn("claim_score", result.reason)
        self.assertIn("STALE", result.reason)

    def test_missing_part_count_field_is_not_assessed_as_stale(self):
        payload = _payload([_part("a", "A", assurance_coverage.STATUS_WIRED)])
        del payload["part_count"]
        self._write(payload)
        result = gate.check(self.coverage_path, self.scripts_dir)
        self.assertFalse(result.stale)


class TestRule7RefusalsAreLoggedAndCountable(GateTestCase):
    def test_business_refusal_is_logged_and_countable(self):
        payload = _payload([
            _part("claim_score", "A", assurance_coverage.NO_DATA),
        ])
        self._write(payload)
        self._make_scripts(1)
        self.assertEqual(gate.count_refusals(self.log_path), 0)
        rc = gate.main([
            "--coverage", self.coverage_path,
            "--scripts-dir", self.scripts_dir,
            "--log", self.log_path,
        ])
        self.assertEqual(rc, 1)
        self.assertEqual(gate.count_refusals(self.log_path), 1)

    def test_machinery_refusal_is_logged_and_countable(self):
        missing_path = os.path.join(self._tmp.name, "absent.json")
        rc = gate.main([
            "--coverage", missing_path,
            "--scripts-dir", self.scripts_dir,
            "--log", self.log_path,
        ])
        self.assertEqual(rc, 2)
        self.assertEqual(gate.count_refusals(self.log_path), 1)

    def test_allowed_run_does_not_add_to_the_log(self):
        payload = _payload([_part("a", "A", assurance_coverage.STATUS_WIRED)])
        self._write(payload)
        self._make_scripts(1)
        rc = gate.main([
            "--coverage", self.coverage_path,
            "--scripts-dir", self.scripts_dir,
            "--log", self.log_path,
        ])
        self.assertEqual(rc, 0)
        self.assertEqual(gate.count_refusals(self.log_path), 0)

    def test_corrupt_log_raises_rather_than_reporting_zero(self):
        with open(self.log_path, "w", encoding="utf-8") as handle:
            handle.write("not json\n")
        with self.assertRaises(gate.ReleaseGateRefused):
            gate.count_refusals(self.log_path)

    def test_count_refusals_cli_flag(self):
        gate._log_refusal(self.log_path, "one")
        gate._log_refusal(self.log_path, "two")
        rc = gate.main(["--log", self.log_path, "--count-refusals"])
        self.assertEqual(rc, 0)


class TestEdgeCases(GateTestCase):
    def test_zero_tier_a_parts_refuses(self):
        payload = _payload([
            _part("b_part", "B", assurance_coverage.STATUS_WIRED),
            _part("c_part", "C", assurance_coverage.STATUS_WIRED),
        ])
        result = self.check(payload)
        self.assertFalse(result.allowed)
        self.assertEqual(result.tier_a_examined, 0)
        self.assertIn("0 Tier A", result.reason)

    def test_duplicate_part_name_raises(self):
        payload = _payload([
            _part("dup", "A", assurance_coverage.STATUS_WIRED),
            _part("dup", "A", assurance_coverage.NO_DATA),
        ])
        self._write(payload)
        with self.assertRaises(gate.ReleaseGateRefused) as ctx:
            gate.check(self.coverage_path, self.scripts_dir)
        self.assertIn("twice", str(ctx.exception))

    def test_unknown_tier_raises(self):
        payload = _payload([_part("x", "Z", assurance_coverage.STATUS_WIRED)])
        self._write(payload)
        with self.assertRaises(gate.ReleaseGateRefused):
            gate.check(self.coverage_path, self.scripts_dir)

    def test_missing_status_field_raises(self):
        row = {"part": "x", "tier": "A"}
        self._write(_payload([row]))
        with self.assertRaises(gate.ReleaseGateRefused) as ctx:
            gate.check(self.coverage_path, self.scripts_dir)
        self.assertIn("status", str(ctx.exception))

    def test_status_neither_no_data_nor_wired_raises(self):
        payload = _payload([_part("x", "A", "MAYBE")])
        self._write(payload)
        with self.assertRaises(gate.ReleaseGateRefused):
            gate.check(self.coverage_path, self.scripts_dir)

    def test_generated_by_different_tool_version_is_a_note_not_a_refusal(self):
        payload = _payload(
            [_part("a", "A", assurance_coverage.STATUS_WIRED)],
            generated_by="scripts/assurance_coverage_v2.py",
        )
        result = self.check(payload)
        self.assertTrue(result.allowed, result.reason)
        self.assertIn("assurance_coverage_v2.py", result.reason)

    def test_no_parts_list_at_all_is_malformed(self):
        self._write({"generated_by": "x"})
        with self.assertRaises(gate.ReleaseGateRefused):
            gate.check(self.coverage_path, self.scripts_dir)


class TestPositiveExaminationRequired(GateTestCase):
    """THE BAD STATE A GREEN RUN WOULD ALSO PASS: a gate that says allowed
    because it found nothing to look at. This test is the one a
    universal-allower (allowed whenever blockers is empty, full stop)
    cannot pass, mirroring coe_gate.py's own universal-refuser test in
    spirit but for the opposite failure direction."""

    def test_allowed_requires_examining_at_least_one_tier_a_part(self):
        payload = _payload([])
        self._write(payload)
        self._make_scripts(0)
        result = gate.check(self.coverage_path, self.scripts_dir)
        self.assertFalse(result.allowed)
        self.assertEqual(result.tier_a_examined, 0)

    def test_pass_case_names_what_was_examined(self):
        payload = _payload([
            _part("strategic_one", "A", assurance_coverage.STATUS_WIRED),
            _part("strategic_two", "A", assurance_coverage.STATUS_WIRED),
            _part("mystery", "UNCLASSIFIED", assurance_coverage.STATUS_WIRED),
        ])
        result = self.check(payload)
        self.assertTrue(result.allowed, result.reason)
        self.assertEqual(result.tier_a_examined, 2)
        self.assertIn("2 Tier A part(s) examined", result.reason)


class TestCoverageGeneratedAt(GateTestCase):
    def test_generated_at_is_set_from_file_mtime(self):
        payload = _payload([_part("a", "A", assurance_coverage.STATUS_WIRED)])
        result = self.check(payload)
        self.assertIsNotNone(result.coverage_generated_at)
        self.assertIn("T", result.coverage_generated_at)  # ISO-8601 shape


class TestNegativeGreenBySilenceIsCaught(GateTestCase):
    """Names the failure a naive implementation would ship: silently
    treating a status this module has never seen as NO_DATA-like-safe
    (i.e. "anything that isn't the WIRED string is fine"). That default
    would let a typo'd status ("NO DATA", "no-data", "N/A") sail through as
    an allowed release. This test proves the real module raises instead."""

    def test_typo_status_value_is_never_treated_as_safe(self):
        payload = _payload([_part("a", "A", "N/A")])
        self._write(payload)
        with self.assertRaises(gate.ReleaseGateRefused):
            gate.check(self.coverage_path, self.scripts_dir)


if __name__ == "__main__":
    unittest.main()
