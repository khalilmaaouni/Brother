#!/usr/bin/env python3
"""Tests for docs/plan/TIER-OVERRIDES.json, the fifteen-part human-in-the-
loop tier classification wire06 wrote for the UNCLASSIFIED+NO-DATA parts
scripts/wire_release_gate.py refuses a release on.

WHY THIS FILE EXISTS, AND WHAT IT DOES NOT DO. This file is not a loader
hook: wire_release_gate.py's own module docstring says it has no override
switch, on purpose, and a fresh grep of this tree (run at classification
time, see the JSON file's own "note" field) found nothing anywhere that
reads a file at docs/plan/TIER-OVERRIDES.json. This test suite therefore
never asserts that the override changes what the gate does; it only
asserts that the RECORD OF THE CLASSIFICATION WORK is honest and complete
enough to be trusted if a loader is ever built. Adding that loader is
explicitly out of scope for this unit (the worker brief says to STOP
rather than build one).

THE BAD STATE A GREEN CHECK WOULD ALSO PASS: a validator that only checks
"is this valid JSON with a list of objects" passes on a file where every
record says tier: C, tier_reason: "Tier C", evidence: "", confidence:
"high" -- the cheap, worthless shape the worker brief names directly as
the failure this unit exists to avoid ("the cheap path is to mark all
fifteen Tier C ... a false Tier C is permanent"). validate_records()
below is the test that catches that shape: it fails a record whose reason
merely restates its own tier, fails a Tier C record with empty evidence,
and fails a Tier C record whose confidence is not high. test_worthless_
shape_is_rejected below builds exactly that all-Tier-C-no-evidence file
and asserts the suite refuses it, so this test file cannot itself become
the kind of check that passes anything shaped like a JSON list.

Plain unittest, runnable directly:
    python3 scripts/test_tier_overrides.py -v

Python 3.9, standard library only.
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OVERRIDES_PATH = os.path.join(REPO_ROOT, "docs", "plan", "TIER-OVERRIDES.json")

REQUIRED_FIELDS = (
    "part",
    "tier",
    "tier_reason",
    "evidence",
    "reviewed_by",
    "reviewed_at",
    "confidence",
)

KNOWN_TIERS = frozenset(("A", "B", "C", "UNCLASSIFIED"))

# The exact fifteen parts this unit was briefed to classify. A record
# naming anything else is not part of this unit's authority and is
# refused rather than silently accepted (worker contract rule 2: unknown
# input raises).
THE_FIFTEEN = frozenset((
    "brother_wastage_census",
    "cursor_plugin_install",
    "fault_barrier",
    "graph_value_experiment",
    "host_capability",
    "lesson_severity",
    "memory_ab_runner",
    "optimization_loop",
    "readme_receipt_sample",
    "report_complexity_gate_experiment",
    "score_vault_recall",
    "self_check_staged",
    "tmp_sandbox",
    "vault_clock_backfill",
    "verify_task_estate",
))

# A tier_reason whose entire content, once lowercased and stripped of
# trailing punctuation, is nothing but the tier's own name or its label
# word. Catches "Tier C.", "C", "informational" etc: a reason a green
# check would accept as "present" but that carries zero evidence.
_RESTATEMENTS = {
    "A": {"a", "tier a", "strategic"},
    "B": {"b", "tier b", "supporting"},
    "C": {"c", "tier c", "informational"},
}

_MIN_REASON_LEN = 15


def validate_records(records):
    """Returns a list of problem strings; empty means the records are
    valid. Never raises on bad input, a caller (a future loader hook)
    would need a verdict it can act on, not a traceback.
    """
    problems = []
    seen_parts = set()

    if not isinstance(records, list):
        return ["records is not a list: %r" % (type(records),)]

    for i, record in enumerate(records):
        label = "records[%d]" % (i,)
        if not isinstance(record, dict):
            problems.append("%s is not an object: %r" % (label, record))
            continue

        missing_or_empty = [
            key for key in REQUIRED_FIELDS
            if not isinstance(record.get(key), str) or not record.get(key).strip()
        ]
        if missing_or_empty:
            problems.append(
                "%s is missing or has an empty value for: %s"
                % (label, missing_or_empty)
            )
            # Further checks below assume the fields exist; skip them for
            # this record rather than raising on a missing key.
            continue

        part = record["part"]
        label = "part %r" % (part,)

        if part in seen_parts:
            problems.append("%s appears more than once" % (label,))
        seen_parts.add(part)

        if part not in THE_FIFTEEN:
            problems.append(
                "%s is not one of the fifteen parts this unit was briefed "
                "to classify" % (label,)
            )

        tier = record["tier"]
        if tier not in KNOWN_TIERS:
            problems.append(
                "%s carries unknown tier %r, not one of %s"
                % (label, tier, sorted(KNOWN_TIERS))
            )
            continue

        reason = record["tier_reason"].strip()
        if len(reason) < _MIN_REASON_LEN:
            problems.append(
                "%s tier_reason is too short to be a real reason: %r"
                % (label, reason)
            )
        normalized = reason.lower().rstrip(".! ")
        if normalized in _RESTATEMENTS.get(tier, ()):
            problems.append(
                "%s tier_reason %r merely restates the tier, carries no "
                "evidence of its own" % (label, reason)
            )

        # Evidence itself is already required to be a non-empty string for
        # every record by the missing_or_empty check above (a Tier C
        # record with blank evidence never reaches this line at all); the
        # remaining Tier C rule below is the one thing that check cannot
        # see: confidence must be HIGH, never merely present.
        confidence = record["confidence"].strip().lower()
        if tier == "C" and confidence != "high":
            problems.append(
                "%s is Tier C but confidence is %r, not high (Tier C "
                "requires high confidence, see worker contract)"
                % (label, record["confidence"])
            )

    return problems


def _load_real_file():
    with open(OVERRIDES_PATH, encoding="utf-8") as handle:
        return json.load(handle)


def _good_record(**overrides):
    record = {
        "part": "brother_wastage_census",
        "tier": "C",
        "tier_reason": "No importer anywhere in the tree and no consumer "
                       "of its output reads it into any decision.",
        "evidence": "grep -rlw brother_wastage_census . outside its own "
                    "file returns only generated listing artifacts.",
        "reviewed_by": "wire06 automated review",
        "reviewed_at": "2026-09-17T23:01:17Z",
        "confidence": "high",
    }
    record.update(overrides)
    return record


class ValidateRecordsTestCase(unittest.TestCase):
    """Tests against synthetic fixtures, never the real file: this file
    must keep working if the real classification ever changes."""

    def test_tier_c_empty_evidence_fails(self):
        problems = validate_records([_good_record(evidence="")])
        self.assertTrue(problems)
        self.assertTrue(any("evidence" in p for p in problems))

    def test_well_formed_fixture_passes(self):
        problems = validate_records([
            _good_record(),
            _good_record(
                part="fault_barrier",
                tier="A",
                tier_reason="Imported directly by claim_store.py, "
                            "integrate.py and loop_bridge.py, the "
                            "execution spine itself.",
                evidence="Real import statements at claim_store.py:78, "
                         "integrate.py:78, loop_bridge.py:72.",
                confidence="high",
            ),
        ])
        self.assertEqual(problems, [])

    def test_missing_field_detected(self):
        record = _good_record()
        del record["reviewed_at"]
        problems = validate_records([record])
        self.assertTrue(any("reviewed_at" in p for p in problems))

    def test_every_required_field_is_checked(self):
        for field in REQUIRED_FIELDS:
            record = _good_record()
            record[field] = ""
            problems = validate_records([record])
            self.assertTrue(
                problems, "an empty %r should have been rejected" % (field,)
            )

    def test_empty_tier_reason_fails(self):
        problems = validate_records([_good_record(tier_reason="   ")])
        self.assertTrue(problems)

    def test_tier_reason_restating_tier_is_refused(self):
        for restatement in ("C", "Tier C", "tier c.", "informational"):
            with self.subTest(restatement=restatement):
                problems = validate_records(
                    [_good_record(tier_reason=restatement)]
                )
                self.assertTrue(
                    problems,
                    "%r should have been refused as a bare restatement"
                    % (restatement,),
                )

    def test_tier_c_requires_high_confidence(self):
        problems = validate_records([_good_record(confidence="low")])
        self.assertTrue(problems)
        self.assertTrue(any("confidence" in p for p in problems))

    def test_tier_b_does_not_require_high_confidence(self):
        # Rule: "anything less than Tier C's bar is Tier B", so a
        # low-confidence Tier B record is a legitimate, unrestricted shape.
        problems = validate_records([
            _good_record(
                tier="B",
                tier_reason="Indirectly affects a Tier A part through an "
                            "import chain that could not be fully traced.",
                confidence="low",
            )
        ])
        self.assertEqual(problems, [])

    def test_part_outside_the_fifteen_is_refused(self):
        problems = validate_records([_good_record(part="not_one_of_them")])
        self.assertTrue(problems)
        self.assertTrue(any("fifteen" in p for p in problems))

    def test_duplicate_part_is_refused(self):
        problems = validate_records([_good_record(), _good_record()])
        self.assertTrue(any("more than once" in p for p in problems))

    def test_unknown_tier_value_raises_a_problem(self):
        problems = validate_records([_good_record(tier="D")])
        self.assertTrue(any("unknown tier" in p for p in problems))

    def test_worthless_shape_is_rejected(self):
        """The exact failure the worker brief warns against: every part
        marked Tier C with a reason that is just the tier name and no
        evidence. A validator that passed this would be exactly the
        "cheap path" the brief calls worse than a red gate."""
        worthless = [
            _good_record(part=name, tier_reason="Tier C", evidence="")
            for name in sorted(THE_FIFTEEN)
        ]
        problems = validate_records(worthless)
        self.assertTrue(problems)
        # Every single record should have been caught, not just the first.
        self.assertGreaterEqual(len(problems), len(THE_FIFTEEN))


class RealFileTestCase(unittest.TestCase):
    """Tests against the actual docs/plan/TIER-OVERRIDES.json this unit
    produced. If a future edit weakens it, this suite is the thing that
    notices."""

    def setUp(self):
        self.payload = _load_real_file()
        self.records = self.payload["records"]

    def test_real_file_has_records_list(self):
        self.assertIsInstance(self.records, list)
        self.assertTrue(self.records)

    def test_real_file_parts_are_a_subset_of_the_fifteen(self):
        parts = {r.get("part") for r in self.records}
        self.assertTrue(parts.issubset(THE_FIFTEEN))

    def test_real_file_has_no_duplicate_parts(self):
        parts = [r.get("part") for r in self.records]
        self.assertEqual(len(parts), len(set(parts)))

    def test_real_file_passes_validation(self):
        problems = validate_records(self.records)
        self.assertEqual(problems, [], "\n".join(problems))

    def test_real_file_leaves_no_part_resolved_without_reason(self):
        # Every record in the file (whether A, B, or C) must carry a real,
        # specific tier_reason; UNCLASSIFIED parts are simply absent from
        # this file rather than present with a hollow reason (rule: a part
        # that cannot be resolved stays UNCLASSIFIED in the coverage file
        # itself, not faked into a record here).
        for record in self.records:
            self.assertNotIn(record.get("tier"), (None, "", "UNCLASSIFIED"))


if __name__ == "__main__":
    unittest.main()
