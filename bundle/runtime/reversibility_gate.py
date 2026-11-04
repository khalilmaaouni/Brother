#!/usr/bin/env python3
"""WBS-40.08 Reversibility Gate: require evidence a merge CAN be undone
before it is allowed to happen.

Generic Core capability, usable for any entity type (customer, product,
supplier, ...), never entity-specific and never a named client's own data.
"Never mutate raw truth" is already the governing rule elsewhere in this
WBS (scripts/normalization_trace.py: the raw value is recorded once and
never overwritten). This module enforces the same rule at the merge
decision point: the pre-merge snapshot is captured once, before anything
is combined, and the inverse operation is "restore that snapshot" -- never
an attempt to reverse-derive the sources from the merged output, which has
already lost per-source structure.

Real design input checked in the main lane before this was built (per
docs/plan/1.0.17/WAVE-2-DESIGN-CRITIQUES-2026-09-13.md's "Reversibility
gate (WBS-40.08)" section, two independent generic AI passes, secondary
input not a verdict):

  KEPT from the Deepseek draft: capture an immutable pre-merge snapshot
  before merging; require a named inverse operation and affected entity
  set; the rollback test must actually run the merge, run the rollback,
  and compare checksums against the pre-merge state, not just check that a
  rollback script exists; run the rollback twice to prove idempotence.

  KEPT from the Muse hostile review: "technically reversible, practically
  not" once a merge has already propagated downstream (state fork,
  derived data, external propagation, identity loss on foreign keys) is a
  real, separate failure mode from "can I undo the write." Added a
  downstream_propagation_status field and refuse to PASS a propagated
  merge with no recorded compensation plan, exactly as the roadmap's own
  field list names ("downstream propagation status") and WBS-40.10
  (publish and reconciliation) exists to check the same boundary later.

  CHANGED / NOT BUILT: the Deepseek draft's isolated-schema/DB sandbox,
  signed manifests, hashed idempotency keys, and lock/precondition system
  are out of scope here -- this gate proves the reversibility PROPERTY
  (can the exact pre-merge state be reconstructed) with real in-process
  data, not a production merge pipeline. WBS-40.09 survivorship lineage
  (per-attribute winner/loser/rule provenance) is a distinct decision the
  roadmap explicitly warns not to conflate with entity matching or build
  here; this gate only references what survivorship lineage WOULD need to
  exist (a per-field decision naming the winning value) so a real merge
  candidate can be evaluated, and does not compute or store lineage itself.

Per docs/decisions/evidence-vocabulary-2026-09-13.json's EV-3 ruling, the
PASS/FAIL/NO-DATA triple is imported from scripts/evidence_obligation.py,
not redeclared here.
"""
import argparse
import copy
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from evidence_obligation import VERDICTS, exit_code_for_verdict  # noqa: E402


def sha256_of_obj(obj):
    """Checksum of a canonical (sorted-key) JSON serialization, so two
    structurally-equal dicts always hash equal regardless of key order."""
    canonical = json.dumps(obj, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def run_rollback_cycle(pre_merge_records, survivorship_decisions, inverse_operation_fn=None):
    """The real merge -> rollback -> verify cycle. No mocks: this actually
    mutates a real post-merge working copy from the given per-field
    decisions, then rolls back that mutated copy using ONLY the pre-merge
    snapshot (the inverse operation under test), then verifies the rollback
    reconstructs the exact originals.

    pre_merge_records: {record_id: {field: value, ...}, ...} -- the
        immutable pre-merge snapshot, captured before any merge step.
    survivorship_decisions: {field: {"value": ..., "winner_source_id": ...}}
        -- which value won per field. This gate does not decide winners
        (that is WBS-40.09's job); it only consumes a decision already made.
    inverse_operation_fn: optional callable(post_merge_state, snapshot) ->
        restored_state, for tests that need to prove a BROKEN inverse is
        caught. Defaults to the real inverse this system uses: clear the
        post-merge working copy and repopulate it from the snapshot. This
        hook is what makes matches_pre_state genuinely falsifiable -- with
        no real post-merge state to restore FROM, a rollback that always
        hands back a copy of its own snapshot can never be told apart from
        one that actually restores anything.

    Returns a recovery_verification dict, always carrying "ran": True so a
    caller can tell "verified and it failed" apart from "never verified".
    """
    # The pre-merge snapshot: taken once, before the merge step runs.
    snapshot = copy.deepcopy(pre_merge_records)
    checksum_pre = sha256_of_obj(snapshot)

    # The merge itself: really combines fields per the given decisions into
    # one golden record. This is a real merge, not a stand-in -- it is the
    # thing being rolled back from.
    golden_record = {field: decision["value"] for field, decision in survivorship_decisions.items()}

    # The post-merge state: a real, separate working copy the merge step
    # actually writes into (every record's fields collapse onto the golden
    # values), so there is a genuine merged artifact to roll back FROM,
    # not just an untouched snapshot handed back to itself.
    post_merge_state = copy.deepcopy(pre_merge_records)
    for record in post_merge_state.values():
        record.update(golden_record)

    def default_inverse(state, snap):
        # Deliberately never derives the sources from golden_record --
        # that information (which source had which raw value) is already
        # gone once fields are combined, which is exactly why the snapshot
        # has to exist before the merge, not be reconstructed after it.
        state.clear()
        state.update(copy.deepcopy(snap))
        return state

    inverse = inverse_operation_fn or default_inverse

    def rollback():
        # The inverse operation runs against the real post-merge state,
        # so a rollback that fails to actually restore it shows up here.
        return copy.deepcopy(inverse(post_merge_state, snapshot))

    restored_once = rollback()
    restored_twice = rollback()  # run twice: prove the inverse is idempotent

    checksum_rollback = sha256_of_obj(restored_once)
    matches_pre_state = (
        restored_once == pre_merge_records
        and checksum_rollback == checksum_pre
    )
    idempotent = (
        restored_once == restored_twice
        and sha256_of_obj(restored_twice) == checksum_rollback
    )

    return {
        "ran": True,
        "matches_pre_state": matches_pre_state,
        "idempotent": idempotent,
        "checksum_pre": checksum_pre,
        "checksum_rollback": checksum_rollback,
        "golden_record_produced": golden_record,
    }


def build_reversible_action(
    affected_entity_set,
    pre_merge_records,
    survivorship_decisions,
    downstream_propagation_status="not_propagated",
    compensation_plan=None,
    inverse_operation="restore the pre-merge snapshot captured before this merge ran",
):
    """Build a reversible_action record: the roadmap's named Core capability
    fields (inverse operation, affected entity set, pre-state reference,
    recovery verification, downstream propagation status), plus one field
    this gate needs to tell "reversible upstream but not downstream" apart
    (compensation_plan), per the Muse hostile-review finding above.

    Runs the real rollback cycle itself so recovery_verification is never
    hand-typed -- a caller cannot claim a verification that did not run.
    """
    recovery_verification = run_rollback_cycle(pre_merge_records, survivorship_decisions)
    return {
        "inverse_operation": inverse_operation,
        "affected_entity_set": list(affected_entity_set),
        "pre_state_reference": pre_merge_records,
        "recovery_verification": recovery_verification,
        "downstream_propagation_status": downstream_propagation_status,
        "compensation_plan": compensation_plan,
    }


def evaluate_merge_candidate(candidate):
    """Decide whether a merge candidate carries the evidence needed to be
    reversibly undone. Returns (verdict, reason) with verdict drawn from
    evidence_obligation.VERDICTS.

    candidate is a reversible_action-shaped dict (see
    build_reversible_action). Missing evidence is NO-DATA, not a silent
    PASS; a rollback that ran and did not reconstruct the exact pre-merge
    state (or was not proven idempotent) is FAIL; a merge already
    propagated downstream with no recorded compensation plan is FAIL even
    if the upstream rollback itself verified cleanly (the Muse finding:
    technically reversible is not practically reversible once downstream
    has consumed the merged output).
    """
    if not candidate.get("pre_state_reference"):
        return "NO-DATA", "no pre-merge snapshot captured: nothing to roll back to"
    if not candidate.get("affected_entity_set"):
        return "NO-DATA", "no affected entity set named"
    if not candidate.get("inverse_operation"):
        return "NO-DATA", "no inverse operation named"

    verification = candidate.get("recovery_verification")
    if not verification or not verification.get("ran"):
        return "NO-DATA", "no recovery verification has been run for this candidate"

    if not verification.get("matches_pre_state"):
        return "FAIL", "rollback ran but did not reconstruct the exact pre-merge state"
    if not verification.get("idempotent"):
        return "FAIL", "rollback ran but was not proven idempotent (second application diverged)"

    if candidate.get("downstream_propagation_status") == "propagated" and not candidate.get("compensation_plan"):
        return "FAIL", (
            "merge already propagated downstream with no recorded compensation plan: "
            "reversible upstream is not the same as reversible downstream"
        )

    return "PASS", "pre-merge snapshot captured, inverse operation named, rollback verified byte-for-byte and idempotent, downstream propagation accounted for"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("candidate", help="path to a reversible_action-shaped JSON record (see build_reversible_action)")
    args = parser.parse_args(argv)

    try:
        with open(args.candidate, "r", encoding="utf-8") as handle:
            candidate = json.load(handle)
    except FileNotFoundError:
        print("NO-DATA: %s: no such file" % args.candidate)
        return 2
    except json.JSONDecodeError as exc:
        print("NO-DATA: %s: not valid JSON: %s" % (args.candidate, exc))
        return 2

    verdict, reason = evaluate_merge_candidate(candidate)
    if verdict not in VERDICTS:
        raise AssertionError("reversibility verdict %r outside the shared triple" % verdict)
    print(json.dumps({"verdict": verdict, "verdict_reason": reason}, indent=2, ensure_ascii=False))
    return exit_code_for_verdict(verdict)


if __name__ == "__main__":
    sys.exit(main())
