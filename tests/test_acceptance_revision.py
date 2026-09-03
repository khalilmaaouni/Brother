"""SR-05: a fixture test for the acceptance revision contract.

WHY THIS FILE EXISTS. docs/ACCEPTANCE-REVISION-CONTRACT.md defines four rules
in prose: what identifies an accepted baseline, what revising one means, when
one baseline supersedes another, and when accepted evidence goes stale. A
contract that only exists as prose is not a control, per this estate's own
law that a rule is not a control unless a file enforces it. This file is that
enforcement: it implements each rule as a small, pure function taken directly
from the contract's own wording, then runs a fixture of baseline records
through it, including at least one record built to VIOLATE a rule, so the
check can be seen rejecting something rather than rubber stamping everything
it is handed.

WHAT THIS ASSERTS. Baseline identity (rule 1): two records are the same
baseline only when subject_id, criterion_id, and seq all match. Revision
(rule 2): a new record only counts as revising an old one when it points at
it, increments seq by exactly one, and carries genuinely fresh evidence
rather than the old evidence copied under a new number; the copied-evidence
case is the one built to fail. Superseded (rule 3): status is derived at read
time from the full set of records in a lineage, never stored on a record,
and the older record's own evidence field is never touched. Staleness
(rule 4): a baseline is stale exactly when the subject's content hash at
check time no longer matches the hash recorded at acceptance, and only a
CURRENT baseline can be stale at all.

WHAT THIS DOES NOT DO. It does not read or exercise any product's real
storage layer; BrotherDS's claim store, or any future implementer, is the
place these functions would eventually be ported into. This file proves the
contract is machine-checkable, not that any running system already obeys it.
"""
import unittest


# ---------------------------------------------------------------------------
# The four rules, each a direct translation of one section of
# docs/ACCEPTANCE-REVISION-CONTRACT.md. Kept here rather than imported from a
# product because no product implements this contract yet; this is the
# reference implementation the contract's own prose describes.
# ---------------------------------------------------------------------------


def load_baselines(records):
    """Rule: an empty or missing fixture never counts as a passing check.

    NO-DATA is not a pass (docs/CHARTER.md, the verdict tuple). A fixture
    that silently contains zero records would let every test below iterate
    zero times and report green without having checked anything, which is
    exactly the "quietly upgrades NO-DATA to PASS" failure the charter names.
    So the loader refuses that state outright rather than letting it pass
    through as an empty-but-successful test run.
    """
    if records is None:
        raise ValueError("NO-DATA: no fixture was supplied at all")
    if len(records) == 0:
        raise ValueError("NO-DATA: fixture is empty, refusing to report a pass on nothing")
    return records


def baseline_id(record):
    """Rule 1: a baseline's identity is (subject_id, criterion_id, seq)."""
    return (record["subject_id"], record["criterion_id"], record["seq"])


def same_baseline(a, b):
    """Rule 1: same baseline iff all three identity fields match."""
    return baseline_id(a) == baseline_id(b)


def is_valid_revision(old, new):
    """Rule 2: new revises old only if it points at it, increments seq by
    exactly one, and does not merely copy old's subject_hash and evidence
    forward under a new sequence number.
    """
    if new["subject_id"] != old["subject_id"] or new["criterion_id"] != old["criterion_id"]:
        return False
    if new["seq"] != old["seq"] + 1:
        return False
    if new["revised_from"] != old["id"]:
        return False
    hash_changed = new["subject_hash"] != old["subject_hash"]
    evidence_is_fresh = (
        new.get("evidence") not in (None, "", old.get("evidence"))
        and new["accepted_at"] > old["accepted_at"]
    )
    return hash_changed or evidence_is_fresh


def _lineage(record, all_records):
    return [
        r for r in all_records
        if r["subject_id"] == record["subject_id"] and r["criterion_id"] == record["criterion_id"]
    ]


def is_current(record, all_records):
    """Rule 3: CURRENT/SUPERSEDED is derived at read time from the full set
    of records in a lineage, never stored on the record itself.
    """
    siblings = _lineage(record, all_records)
    return record["seq"] == max(r["seq"] for r in siblings)


def is_superseded(record, all_records):
    return not is_current(record, all_records)


def is_stale(record, all_records, current_subject_hashes):
    """Rule 4: a CURRENT baseline is stale exactly when the subject's
    content hash at check time no longer matches the hash recorded when it
    was accepted. A SUPERSEDED baseline is never reported stale: it has
    already been replaced, which is a different condition than going stale
    while still being the one a claim would read.
    """
    if is_superseded(record, all_records):
        return False
    current_hash = current_subject_hashes.get(record["subject_id"])
    if current_hash is None:
        raise ValueError(
            "NO-DATA: no current subject hash was supplied for %r" % record["subject_id"]
        )
    return current_hash != record["subject_hash"]


# ---------------------------------------------------------------------------
# The fixture. Two lineages: S1/C1 has been revised once (baseline 1
# superseded by baseline 2, subject unchanged since), S2/C1 has one baseline
# whose subject changed after acceptance without ever being revised, so it
# is CURRENT and STALE at once. This combination is what exercises rules 3
# and 4 as genuinely different conditions rather than one implying the other.
# ---------------------------------------------------------------------------

FIXTURE_BASELINES = [
    {
        "id": "S1C1-1",
        "subject_id": "S1",
        "criterion_id": "C1",
        "seq": 1,
        "subject_hash": "hash-s1-v1",
        "accepted_at": "2026-08-01T00:00:00+09:00",
        "revised_from": None,
        "evidence": "check run 2026-08-01: PASS",
    },
    {
        "id": "S1C1-2",
        "subject_id": "S1",
        "criterion_id": "C1",
        "seq": 2,
        "subject_hash": "hash-s1-v2",
        "accepted_at": "2026-08-10T00:00:00+09:00",
        "revised_from": "S1C1-1",
        "evidence": "check run 2026-08-10: PASS",
    },
    {
        "id": "S2C1-1",
        "subject_id": "S2",
        "criterion_id": "C1",
        "seq": 1,
        "subject_hash": "hash-s2-v1",
        "accepted_at": "2026-08-05T00:00:00+09:00",
        "revised_from": None,
        "evidence": "check run 2026-08-05: PASS",
    },
]

# The subject as it stands at check time. S1's subject was re-accepted at
# S1C1-2 and has not changed since, so it matches. S2's subject drifted after
# acceptance and was never revised, so it will not match.
CURRENT_SUBJECT_HASHES = {
    "S1": "hash-s1-v2",
    "S2": "hash-s2-v1-drifted",
}


class TestFixtureItself(unittest.TestCase):
    """The fixture must not be empty or missing; a silent pass on nothing
    would defeat every rule test below without any of them noticing.
    """

    def test_fixture_loads_and_is_not_empty(self):
        records = load_baselines(FIXTURE_BASELINES)
        self.assertGreaterEqual(len(records), 3, "fixture must cover at least two lineages")

    def test_empty_fixture_is_refused_not_passed(self):
        with self.assertRaises(ValueError):
            load_baselines([])

    def test_missing_fixture_is_refused_not_passed(self):
        with self.assertRaises(ValueError):
            load_baselines(None)


class TestBaselineIdentity(unittest.TestCase):
    """Case 1: baseline ID."""

    def test_identical_tuple_is_the_same_baseline(self):
        a = {"subject_id": "S1", "criterion_id": "C1", "seq": 1}
        b = {"subject_id": "S1", "criterion_id": "C1", "seq": 1}
        self.assertTrue(same_baseline(a, b))

    def test_differing_seq_is_a_different_baseline(self):
        b1, b2 = FIXTURE_BASELINES[0], FIXTURE_BASELINES[1]
        self.assertEqual(b1["subject_id"], b2["subject_id"])
        self.assertEqual(b1["criterion_id"], b2["criterion_id"])
        self.assertFalse(
            same_baseline(b1, b2),
            "same subject and criterion but different seq must not be the same baseline",
        )

    def test_differing_subject_with_same_seq_is_a_different_baseline(self):
        s1 = FIXTURE_BASELINES[0]
        s2 = FIXTURE_BASELINES[2]
        self.assertEqual(s1["seq"], s2["seq"], "fixture setup: both are seq 1 in their lineage")
        self.assertFalse(same_baseline(s1, s2))


class TestRevision(unittest.TestCase):
    """Case 2: revision. Includes the case that proves rejection is real."""

    def test_valid_revision_is_accepted(self):
        old, new = FIXTURE_BASELINES[0], FIXTURE_BASELINES[1]
        self.assertTrue(is_valid_revision(old, new))

    def test_revision_that_copies_old_evidence_forward_is_rejected(self):
        """THE CASE THAT PROVES THIS SUITE CAN FAIL A REAL VIOLATION.

        Construct a record that has the right seq, the right revised_from
        link, but reuses the old baseline's subject_hash AND evidence
        unchanged: nothing was actually re-verified, so it is not a
        revision under rule 2, it is the same acceptance wearing a new
        number. If is_valid_revision ever regressed to accept any record
        with a matching seq and revised_from, regardless of evidence, this
        assertion is exactly what would catch it.
        """
        old = FIXTURE_BASELINES[0]
        copied_forward = {
            "id": "S1C1-2-bad",
            "subject_id": old["subject_id"],
            "criterion_id": old["criterion_id"],
            "seq": old["seq"] + 1,
            "revised_from": old["id"],
            "subject_hash": old["subject_hash"],  # copied, not re-earned
            "accepted_at": "2026-08-10T00:00:00+09:00",
            "evidence": old["evidence"],  # copied, not re-earned
        }
        self.assertFalse(
            is_valid_revision(old, copied_forward),
            "a revision that copies the prior subject_hash and evidence forward "
            "unchanged must be rejected: nothing was re-verified",
        )

    def test_revision_pointing_at_the_wrong_prior_baseline_is_rejected(self):
        old = FIXTURE_BASELINES[0]
        wrong_link = dict(FIXTURE_BASELINES[1])
        wrong_link["revised_from"] = "not-the-real-prior-id"
        self.assertFalse(is_valid_revision(old, wrong_link))

    def test_revision_that_skips_a_sequence_number_is_rejected(self):
        old = FIXTURE_BASELINES[0]
        skipped = dict(FIXTURE_BASELINES[1])
        skipped["seq"] = old["seq"] + 2
        self.assertFalse(is_valid_revision(old, skipped))


class TestSuperseded(unittest.TestCase):
    """Case 3: superseded, derived at read time, never mutated on disk."""

    def test_older_baseline_in_a_revised_lineage_is_superseded(self):
        old = FIXTURE_BASELINES[0]
        self.assertTrue(is_superseded(old, FIXTURE_BASELINES))

    def test_newest_baseline_in_a_revised_lineage_is_current(self):
        newest = FIXTURE_BASELINES[1]
        self.assertTrue(is_current(newest, FIXTURE_BASELINES))
        self.assertFalse(is_superseded(newest, FIXTURE_BASELINES))

    def test_a_lineage_with_only_one_baseline_is_current_not_superseded(self):
        only = FIXTURE_BASELINES[2]
        self.assertTrue(is_current(only, FIXTURE_BASELINES))

    def test_superseded_records_own_evidence_field_is_unchanged(self):
        """Status is derived, so the superseded record's own fields never
        get edited. Its evidence is exactly what it was accepted with.
        """
        old = FIXTURE_BASELINES[0]
        self.assertTrue(is_superseded(old, FIXTURE_BASELINES))
        self.assertEqual(old["evidence"], "check run 2026-08-01: PASS")
        self.assertEqual(old["subject_hash"], "hash-s1-v1")


class TestStaleness(unittest.TestCase):
    """Case 4: staleness, an observable content-hash mismatch."""

    def test_baseline_matching_the_current_subject_is_not_stale(self):
        current_of_lineage = FIXTURE_BASELINES[1]  # S1C1-2, current and up to date
        self.assertFalse(
            is_stale(current_of_lineage, FIXTURE_BASELINES, CURRENT_SUBJECT_HASHES)
        )

    def test_baseline_whose_subject_drifted_after_acceptance_is_stale(self):
        drifted = FIXTURE_BASELINES[2]  # S2C1-1, subject changed, never revised
        self.assertTrue(is_stale(drifted, FIXTURE_BASELINES, CURRENT_SUBJECT_HASHES))

    def test_a_superseded_baseline_is_never_reported_stale(self):
        """Rule 4 applies only to a CURRENT baseline. The old S1 baseline's
        subject_hash no longer matches the current subject either, but it
        must read as superseded, not stale, because staleness only
        describes what a claim would otherwise still be reading as live.
        """
        old = FIXTURE_BASELINES[0]
        self.assertTrue(is_superseded(old, FIXTURE_BASELINES))
        self.assertFalse(is_stale(old, FIXTURE_BASELINES, CURRENT_SUBJECT_HASHES))

    def test_staleness_without_a_current_hash_to_check_against_is_no_data(self):
        drifted = FIXTURE_BASELINES[2]
        with self.assertRaises(ValueError):
            is_stale(drifted, FIXTURE_BASELINES, {})


if __name__ == "__main__":
    unittest.main()
