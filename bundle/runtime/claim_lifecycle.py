#!/usr/bin/env python3
"""WBS-50.01 BrotherDS Claim Contract v2, Claim lifecycle: the explicit
state machine the roadmap names for a BrotherDS claim revision --

    DRAFT -> CHECKED -> FROZEN -> DECISION_USED -> OUTCOME_AVAILABLE
          -> RESOLVED -> SCORED

Mirrors golden_master_contract.py's shape: a schema-backed record checker
(reusing contract_check.validate for the same enforced JSON-schema keyword
subset outcome-contract-v1, mobile-journey-contract-v1 and
golden-master-contract-v1 already use) plus hand rules for what the
keyword subset cannot express. Generic, no client-specific data: a claim's
own asserted content is out of scope here and deferred to later WBS-50
units (Claim Evidence references, MDM quality claims, mobile product
claims); this module only enforces the lifecycle itself.

Three rules the roadmap states, none expressible as a single record's
JSON-schema shape, each enforced here by a function comparing two records:

  MUTATION GUARD (check_mutation). "FROZEN claim revision cannot be
  silently mutated": once a revision has reached FROZEN or later, every
  field but state must stay byte-identical between two snapshots of the
  same revision_id; a state change must itself be exactly the next state
  in the chain.

  CORRECTION (check_correction). "correction creates a new revision;
  link with supersedes; old claim remains auditable": a correction is a
  new record, never a rewrite of the old one -- new revision_id, same
  claim_id, supersedes pointing at the corrected revision_id, and state
  back at DRAFT (a correction starts its own lifecycle; it does not
  inherit the corrected revision's progress).

  SCORING TARGET (check_scoring_target). "scoring resolves the historical
  claim, not its rewritten successor": the revision marked SCORED for a
  given decision must be the exact revision_id that reached
  DECISION_USED for that decision, never a later correction of it.

PASS/FAIL/NO-DATA verdicts are imported from evidence_obligation.py, the
one definition site (matching receipt_attest.py's own import), never
redeclared here.
"""
import argparse
import os
import sys

import contract_check as CC
import evidence_obligation

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_SCHEMA = os.path.join(ROOT, "docs", "schema", "claim-lifecycle-v1.json")

VERDICTS = evidence_obligation.VERDICTS  # ("PASS", "FAIL", "NO-DATA")

#: The roadmap's own chain, in order. A transition is valid only to the
#: very next entry; nothing here allows skipping ahead or moving back.
STATES = (
    "DRAFT", "CHECKED", "FROZEN", "DECISION_USED", "OUTCOME_AVAILABLE",
    "RESOLVED", "SCORED",
)
FROZEN_INDEX = STATES.index("FROZEN")


def next_state(state):
    """The one state the roadmap's chain allows after `state`, or None
    once state is SCORED (the chain's end) or an unknown value."""
    if state not in STATES:
        return None
    i = STATES.index(state)
    return STATES[i + 1] if i + 1 < len(STATES) else None


def is_valid_transition(from_state, to_state):
    """True only when to_state is exactly the next state after
    from_state. Refuses skipping ahead, moving backward, and staying put
    (a "transition" to the same state is not a transition)."""
    return next_state(from_state) == to_state


def check_transition(old, new):
    """Validate one revision progressing its own state in place: same
    claim_id, same revision_id, same supersedes, and the state change is
    exactly the next step in the chain. Returns a list of problems,
    empty when the transition is valid."""
    problems = []
    if old.get("claim_id") != new.get("claim_id"):
        problems.append("claim_id: must stay %r across a state transition, "
                        "got %r" % (old.get("claim_id"), new.get("claim_id")))
    if old.get("revision_id") != new.get("revision_id"):
        problems.append("revision_id: a state transition applies to one "
                        "revision in place; got %r, expected %r (a "
                        "different revision_id is a correction, not a "
                        "transition)" % (new.get("revision_id"), old.get("revision_id")))
    if old.get("supersedes") != new.get("supersedes"):
        problems.append("supersedes: must not change during a state "
                        "transition, was %r, got %r"
                        % (old.get("supersedes"), new.get("supersedes")))
    old_state, new_state = old.get("state"), new.get("state")
    if not is_valid_transition(old_state, new_state):
        expected = next_state(old_state)
        problems.append(
            "state: %r is not a valid transition from %r (expected %r)"
            % (new_state, old_state, expected))
    return problems


def check_mutation(old, new):
    """"FROZEN claim revision cannot be silently mutated": once `old` is
    at FROZEN or later, `new` (the same revision_id) may differ only in
    state, and only by a valid transition -- every other field must stay
    byte-identical. Compares every key either record carries (not a
    hardcoded field list) so this fails closed as new content fields are
    added (WBS-50.02+), rather than silently letting an unlisted field
    mutate. Returns a list of problems. A record whose revision_id
    differs is not a mutation of this one at all (see check_correction),
    so this function only applies once the two share a revision_id."""
    problems = []
    if old.get("revision_id") != new.get("revision_id"):
        problems.append("revision_id: check_mutation compares two "
                        "snapshots of the same revision; got %r and %r"
                        % (old.get("revision_id"), new.get("revision_id")))
        return problems
    old_state = old.get("state")
    if old_state not in STATES:
        problems.append("state: unrecognized state %r; cannot tell whether "
                        "this revision has reached FROZEN, so the mutation "
                        "guard cannot be applied -- fix the state before "
                        "trusting this record" % old_state)
        return problems
    if STATES.index(old_state) < FROZEN_INDEX:
        return problems  # not yet FROZEN: ordinary field edits are unrestricted
    for field in sorted(set(old) | set(new)):
        if field != "state" and old.get(field) != new.get(field):
            problems.append(
                "%s: a FROZEN (or later) claim revision cannot be silently "
                "mutated; %s changed from %r to %r without a new revision_id"
                % (field, field, old.get(field), new.get(field)))
    if old.get("state") != new.get("state"):
        problems.extend(check_transition(old, new))
    return problems


def check_correction(old, new, seen_revision_ids=frozenset()):
    """"correction creates a new revision; link with supersedes; old claim
    remains auditable": validate that `new` is a well-formed correction of
    `old` -- same claim_id, a fresh revision_id never used before,
    supersedes pointing at old's revision_id, and state reset to DRAFT (a
    correction starts its own lifecycle, it does not inherit the
    corrected revision's progress). `seen_revision_ids` is an optional
    collection of every revision_id already used by an earlier revision
    (of this or any claim); when given, a correction that reuses one is
    refused even when it is not simply `old`'s own revision_id (the
    schema's own field description: "Unique per revision, never reused").
    Returns a list of problems."""
    problems = []
    if old.get("claim_id") != new.get("claim_id"):
        problems.append("claim_id: a correction must keep the same "
                        "claim_id, was %r, got %r"
                        % (old.get("claim_id"), new.get("claim_id")))
    if new.get("revision_id") == old.get("revision_id"):
        problems.append("revision_id: a correction needs a new "
                        "revision_id; got the same value %r as the "
                        "revision it corrects (that is a mutation, not a "
                        "correction)" % old.get("revision_id"))
    elif new.get("revision_id") in seen_revision_ids:
        problems.append("revision_id: %r has already been used by an "
                        "earlier revision; revision_id must be unique per "
                        "revision and never reused"
                        % new.get("revision_id"))
    if new.get("supersedes") != old.get("revision_id"):
        problems.append("supersedes: a correction must link to the "
                        "revision it corrects via supersedes; expected "
                        "%r, got %r" % (old.get("revision_id"), new.get("supersedes")))
    if new.get("state") != "DRAFT":
        problems.append("state: a correction starts a fresh lifecycle at "
                        "DRAFT, got %r" % new.get("state"))
    return problems


def check_scoring_target(decision_used, scored):
    """"scoring resolves the historical claim, not its rewritten
    successor": the revision now being marked SCORED for a decision must
    be the exact revision_id that reached DECISION_USED for that
    decision -- not merely a record that names the same revision_id
    string, since revision_id is only unique when nothing let it be
    reused (see check_correction's seen_revision_ids). `decision_used` is
    the revision record as it stood when it reached DECISION_USED;
    `scored` is the revision record being marked SCORED. Returns a list
    of problems."""
    problems = []
    if decision_used.get("state") != "DECISION_USED":
        problems.append("decision_used: the reference record's state must "
                        "be 'DECISION_USED', got %r" % decision_used.get("state"))
    if scored.get("state") != "SCORED":
        problems.append("scored: the record being checked must be in "
                        "state 'SCORED', got %r" % scored.get("state"))
    if decision_used.get("revision_id") != scored.get("revision_id"):
        problems.append(
            "revision_id: scoring must resolve the historical revision "
            "that reached DECISION_USED (%r), not %r -- a corrected "
            "successor is a different claim revision and cannot be scored "
            "in its place"
            % (decision_used.get("revision_id"), scored.get("revision_id")))
    if decision_used.get("claim_id") != scored.get("claim_id"):
        problems.append("claim_id: the scored revision must belong to the "
                        "same claim, expected %r, got %r"
                        % (decision_used.get("claim_id"), scored.get("claim_id")))
    if decision_used.get("supersedes") != scored.get("supersedes"):
        problems.append(
            "supersedes: the scored record must be the same revision "
            "that reached DECISION_USED, not a rewritten successor that "
            "reused its revision_id -- supersedes was %r at DECISION_USED, "
            "is %r now"
            % (decision_used.get("supersedes"), scored.get("supersedes")))
    return problems


def hand_rules(record):
    """The one rule a single record's schema keywords cannot express:
    supersedes must not name the record's own revision_id."""
    problems = []
    supersedes = record.get("supersedes")
    revision_id = record.get("revision_id")
    if supersedes is not None and supersedes == revision_id:
        problems.append("supersedes: must not equal this record's own "
                        "revision_id %r (a revision cannot supersede "
                        "itself)" % revision_id)
    return problems


def check(record, schema):
    """Structural validation against claim-lifecycle-v1, plus the one
    hand rule a single record can carry. Cross-record rules
    (check_transition, check_mutation, check_correction,
    check_scoring_target) compare two records and are called directly,
    not from here."""
    problems = []
    CC.validate(record, schema, "", problems)
    problems.extend(hand_rules(record))
    seen, out = set(), []
    for p in problems:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("record", help="path to a claim-lifecycle-v1 JSON record")
    ap.add_argument("--schema", default=DEFAULT_SCHEMA)
    args = ap.parse_args(argv)
    try:
        record = CC.load_json(args.record, "claim lifecycle record")
        schema = CC.load_json(args.schema, "claim lifecycle schema")
    except CC.NoData as exc:
        print("%s: %s" % (VERDICTS[2], exc))
        return 2
    problems = check(record, schema)
    if problems:
        print("%s: %d problem(s)" % (VERDICTS[1], len(problems)))
        for p in problems:
            print(" -", p)
        return 1
    print("%s: %s validates as claim-lifecycle-v1" % (VERDICTS[0], args.record))
    return 0


if __name__ == "__main__":
    sys.exit(main())
