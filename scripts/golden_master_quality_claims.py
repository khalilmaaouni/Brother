#!/usr/bin/env python3
"""WBS-50.04 MDM quality claims. The roadmap's own words (docs/plan, WBS-50.04):

    Golden Master Contract should register claims before publication, for
    example:
    - precision floor;
    - recall floor;
    - catastrophic false-merge tolerance;
    - segment floor;
    - residual normalization error;
    - reconciliation tolerance.
    These are frozen before the Golden Master is accepted.
    Later operational evidence resolves them.

A thin binding layer, not a reimplementation: this module never re-derives
claim_lifecycle.py's state machine, never re-scores anything
golden_master_contract.py, matcher_boundary.py, risk_review_orchestrator.py
or survivorship_lineage.py already own. It ties three already-built pieces
together:

  REGISTRATION AND CONTENT. golden-master-contract-v1 already names the hook
  point ("quality_claim_ids: Claim IDs this master record will feed once
  published... This contract does not score them itself"). register_claims()
  turns a Golden Master record plus a caller-given set of thresholds into
  claim-lifecycle-v1 DRAFT records (scripts/claim_lifecycle.py's own shape,
  reused not reinvented) plus their content: a new schema,
  docs/schema/claim-mdm-quality-v1.json, since claim-lifecycle-v1 is
  explicit that "a claim's own content... is deferred to later WBS-50
  units" and claim-score-v1 (WBS-50.03) is a probabilistic-forecast shape
  (interval/probability), not the floor/tolerance shape these six example
  claims need.

  FREEZING. freeze_claims() advances a DRAFT claim to FROZEN by walking
  claim_lifecycle.py's own chain (CL.next_state, CL.check_transition) --
  golden_master_contract.py itself has no publication workflow to hook a
  freeze step into (it validates one record's own shape, plus a cross-file
  check against its outcome_contract_ref, nothing more), so
  check_registered_before_publication() is this unit's own minimal
  integration: given a Golden Master record and the claims registered for
  it, refuse unless every quality_claim_ids entry is FROZEN or later.

  RESOLUTION. Real operational evidence only, per this project's rule:
  never a fabricated PASS. Of the six example dimensions, exactly two have
  a real evidence source already built in this codebase:
  catastrophic_false_merge_tolerance (risk_review_orchestrator.py's own
  route_batch: does a HIGH/CATASTROPHIC-cost candidate ever get routed away
  from CLERICAL_REVIEW) and reconciliation_tolerance (survivorship_lineage.py's
  own resolve_field: what fraction of real field-reconciliation attempts
  fail). matcher_boundary.py explicitly never scores or matches ("Brother
  does not become the matcher" is its own stated non-goal), so
  precision_floor and recall_floor have no real evidence source in this
  codebase; segment_floor and residual_normalization_error likewise have
  none. resolve_no_evidence_available() names the honest reason for each,
  per this module's NO_EVIDENCE_REASONS -- never a made-up measured_value.
"""
import argparse
import datetime
import json
import os
import sys

import claim_lifecycle as CL
import contract_check as CC
import evidence_obligation
import risk_review_orchestrator as RRO
import survivorship_lineage as SL

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_SCHEMA = os.path.join(ROOT, "docs", "schema", "claim-mdm-quality-v1.json")

VERDICTS = evidence_obligation.VERDICTS  # ("PASS", "FAIL", "NO-DATA")

#: Reused, never redeclared: claim_lifecycle.py's own chain and its FROZEN
#: index.
STATES = CL.STATES
FROZEN_INDEX = CL.FROZEN_INDEX

#: The six example dimensions the roadmap names for WBS-50.04, in the
#: roadmap's own order. Mirrors docs/schema/claim-mdm-quality-v1.json's own
#: quality_dimension enum exactly -- the same by-hand pairing
#: claim_lifecycle.py's STATES tuple already keeps with claim-lifecycle-v1's
#: state enum (test_dimensions_match_the_schema_enum in the test suite
#: catches drift between the two).
DIMENSIONS = (
    "precision_floor", "recall_floor", "catastrophic_false_merge_tolerance",
    "segment_floor", "residual_normalization_error", "reconciliation_tolerance",
)

#: Mirrors the schema's own comparison enum.
COMPARISONS = ("gte", "lte")

#: The four DIMENSIONS with no real operational evidence source anywhere in
#: this codebase yet, each with the honest reason why (which sibling module
#: was checked, and what it explicitly does not do). Read by
#: resolve_no_evidence_available().
NO_EVIDENCE_REASONS = {
    "precision_floor": (
        "matcher_boundary.py explicitly never scores or matches (its own "
        "stated non-goal, WBS-40.04: 'Brother does not become the "
        "matcher'); no precision computation exists anywhere in this "
        "codebase yet"),
    "recall_floor": (
        "matcher_boundary.py explicitly never scores or matches; no recall "
        "computation exists anywhere in this codebase yet"),
    "segment_floor": (
        "no per-segment breakdown of match quality exists anywhere in "
        "this codebase yet"),
    "residual_normalization_error": (
        "normalization_trace.py records each transform's lossy flag and "
        "reason per step, but computes no aggregate residual-error metric"),
}


class RegistrationError(ValueError):
    """Raised when register_claims, freeze_claims, or a resolve_* function
    is asked to do something the roadmap's own rules refuse: an
    unrecognized dimension or comparison, a non-numeric threshold, a
    mismatched quality_dimension passed to the wrong resolver, or a
    transition claim_lifecycle.py itself would not validate. Mirrors
    risk_review_orchestrator.py's own RiskReviewError -- a caller mistake,
    never a data-quality NO-DATA."""


def _parse_iso(value):
    """Same tolerant ISO-8601 parse claim_score.py's own hand_rules uses
    (ported, not imported: claim_score.py does not expose it as a public
    helper). Returns None for anything that is not a non-empty string or
    does not parse; callers treat that as "no timestamp", never a crash."""
    if not isinstance(value, str) or not value:
        return None
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        return datetime.datetime.fromisoformat(text)
    except ValueError:
        return None


def _now_iso(now=None):
    if now is not None:
        return now
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def hand_rules(record):
    """The rules a single claim-mdm-quality-v1 record's schema keywords
    cannot express: measured_value/resolved_at must be null together or
    non-null together (mirrors claim-score-v1's own resolved_at/
    outcome_value pairing), and resolved_at must be a parseable ISO-8601
    timestamp when present."""
    problems = []
    measured_value = record.get("measured_value")
    resolved_at = record.get("resolved_at")
    if (measured_value is None) != (resolved_at is None):
        problems.append(
            "measured_value/resolved_at: must be null together or non-null "
            "together, got measured_value=%r resolved_at=%r"
            % (measured_value, resolved_at))
    if resolved_at is not None and _parse_iso(resolved_at) is None:
        problems.append("resolved_at: not a parseable ISO-8601 timestamp: %r" % resolved_at)
    return problems


def check(record, schema):
    """Structural validation against claim-mdm-quality-v1, plus the hand
    rules a single record can carry. check_matches_lifecycle and
    check_matches_master compare two records and are called directly, not
    from here."""
    problems = []
    CC.validate(record, schema, "", problems)
    problems.extend(hand_rules(record))
    seen, out = set(), []
    for p in problems:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def check_matches_lifecycle(quality_record, lifecycle_record):
    """A claim-mdm-quality-v1 record documents one exact claim revision: its
    claim_id and revision_id must match the claim-lifecycle-v1 record it
    was registered for. Mirrors claim_score.py's own
    check_matches_lifecycle and claim_evidence.py's check_matches_claim.
    Returns a list of problems, empty when they match."""
    problems = []
    if quality_record.get("claim_id") != lifecycle_record.get("claim_id"):
        problems.append(
            "claim_id: quality record names %r, lifecycle record is %r"
            % (quality_record.get("claim_id"), lifecycle_record.get("claim_id")))
    if quality_record.get("revision_id") != lifecycle_record.get("revision_id"):
        problems.append(
            "revision_id: quality record names %r, lifecycle record is %r "
            "(scoring must resolve the exact historical revision, never a "
            "rewritten successor -- WBS-50.01's own scoring-target rule)"
            % (quality_record.get("revision_id"), lifecycle_record.get("revision_id")))
    return problems


def check_matches_master(quality_record, golden_master_record):
    """The binding this whole unit exists for: a quality claim must belong
    to the golden-master-contract-v1 record's own master_id, and its
    claim_id must actually appear in that record's own quality_claim_ids
    array -- the hook point the schema already names ('Claim IDs this
    master record will feed once published'), never a second hook invented
    here. A quality record whose claim_id the parent contract never listed
    is not really "feeding" it. Returns a list of problems, empty when both
    hold."""
    problems = []
    if quality_record.get("master_id") != golden_master_record.get("master_id"):
        problems.append(
            "master_id: quality record names %r, golden master record is %r"
            % (quality_record.get("master_id"), golden_master_record.get("master_id")))
    claim_id = quality_record.get("claim_id")
    quality_claim_ids = golden_master_record.get("quality_claim_ids")
    if isinstance(quality_claim_ids, list) and claim_id not in quality_claim_ids:
        problems.append(
            "claim_id: %r does not appear in the golden master record's own "
            "quality_claim_ids %r" % (claim_id, quality_claim_ids))
    return problems


def register_claims(golden_master_record, thresholds):
    """"Golden Master Contract should register claims before publication."
    Build one claim-lifecycle-v1 DRAFT record plus one
    claim-mdm-quality-v1 content record per entry in `thresholds`, a dict
    {quality_dimension: {"threshold": number, "comparison": "gte"|"lte"}}.
    Not required to name all six DIMENSIONS -- the roadmap's list is stated
    as examples ("for example:"), so a caller registers whichever subset
    applies to this Golden Master.

    claim_id is deterministic ("<master_id>:<dimension>"), never random:
    the same Golden Master registering the same dimension twice names the
    same claim (a re-registration is a correction per claim_lifecycle.py's
    own rules, not a fresh identity -- this function does not itself build
    the correction, see claim_lifecycle.check_correction for that shape).
    revision_id is that claim's first revision ("<claim_id>:r1").

    Returns a list of (lifecycle_record, quality_record) pairs, in the
    order `thresholds` was given. Raises RegistrationError for a caller
    mistake (unknown master_id, unknown dimension, unknown comparison,
    non-numeric threshold) -- never silently drops or reinterprets one."""
    master_id = golden_master_record.get("master_id")
    if not isinstance(master_id, str) or not master_id:
        raise RegistrationError(
            "golden_master_record: master_id must be a non-empty string, got %r"
            % (master_id,))

    pairs = []
    for dimension, spec in thresholds.items():
        if dimension not in DIMENSIONS:
            raise RegistrationError(
                "quality_dimension %r is not one of %r" % (dimension, DIMENSIONS))
        if not isinstance(spec, dict):
            raise RegistrationError(
                "thresholds[%r]: must be an object with threshold/comparison, "
                "got %r" % (dimension, spec))
        threshold = spec.get("threshold")
        comparison = spec.get("comparison")
        if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
            raise RegistrationError(
                "thresholds[%r].threshold: must be numeric, got %r" % (dimension, threshold))
        if comparison not in COMPARISONS:
            raise RegistrationError(
                "thresholds[%r].comparison: must be one of %r, got %r"
                % (dimension, COMPARISONS, comparison))

        claim_id = "%s:%s" % (master_id, dimension)
        revision_id = "%s:r1" % claim_id
        lifecycle_record = {
            "schema_version": "claim-lifecycle-v1",
            "claim_id": claim_id,
            "revision_id": revision_id,
            "state": "DRAFT",
            "supersedes": None,
        }
        quality_record = {
            "schema_version": "claim-mdm-quality-v1",
            "claim_id": claim_id,
            "revision_id": revision_id,
            "master_id": master_id,
            "quality_dimension": dimension,
            "comparison": comparison,
            "threshold": threshold,
            "measured_value": None,
            "resolved_at": None,
            "evidence_note": None,
        }
        pairs.append((lifecycle_record, quality_record))
    return pairs


def freeze_claims(pairs):
    """"These are frozen before the Golden Master is accepted." Advances
    every lifecycle_record in `pairs` from DRAFT to FROZEN by walking
    claim_lifecycle.py's own chain (CL.next_state), validating each step
    with CL.check_transition -- reusing the roadmap's one enforced state
    machine rather than a second, parallel one here. quality_record is
    carried through unchanged (freezing is a lifecycle-only operation; the
    quality content was already frozen at register_claims time by simply
    never being mutated again until a resolve_* function runs).

    Returns a new list of (frozen_lifecycle_record, quality_record) pairs,
    same order as `pairs`. Raises RegistrationError if any pair is not
    currently at DRAFT, or if claim_lifecycle.py itself refuses a step
    (defensive: CL.next_state/check_transition are reused precisely so
    this should never happen for a pair register_claims produced, but a
    hand-built pair from elsewhere is checked for real, never trusted)."""
    frozen_pairs = []
    for lifecycle_record, quality_record in pairs:
        state = lifecycle_record.get("state")
        if state != "DRAFT":
            raise RegistrationError(
                "claim_id %r: freeze_claims expects state DRAFT, got %r"
                % (lifecycle_record.get("claim_id"), state))
        current = dict(lifecycle_record)
        while current["state"] != "FROZEN":
            next_state = CL.next_state(current["state"])
            if next_state is None:
                raise RegistrationError(
                    "claim_id %r: claim_lifecycle.py's chain has no state "
                    "after %r, cannot reach FROZEN"
                    % (current.get("claim_id"), current["state"]))
            candidate = dict(current, state=next_state)
            problems = CL.check_transition(current, candidate)
            if problems:
                raise RegistrationError(
                    "claim_id %r: claim_lifecycle.py refused %r -> %r: %s"
                    % (current.get("claim_id"), current["state"], next_state,
                       "; ".join(problems)))
            current = candidate
        frozen_pairs.append((current, quality_record))
    return frozen_pairs


def check_registered_before_publication(golden_master_record, lifecycle_records_by_claim_id):
    """golden_master_contract.py has no natural hook point of its own to
    call directly: it validates one record's own shape plus its
    outcome_contract_ref cross-file check, and does not orchestrate a
    publication workflow this could plug a freeze-enforcement step into
    (per the dispatch brief for this unit, "note this honestly"). This
    function is the minimal defensible integration built instead: given a
    golden-master-contract-v1 record and a {claim_id: claim-lifecycle-v1
    record} map, verify that every claim_id the Golden Master's own
    quality_claim_ids names is registered and at FROZEN state or later. A
    caller (a future publish step, or a human reviewer) runs this before
    accepting the Golden Master; it deliberately does not fold into
    golden_master_contract.check(), since that function validates one
    record in isolation and quality_claim_ids may legitimately be empty
    before any claims are registered at all (the schema's own words:
    "This contract does not score them itself... May be empty before
    publish").

    This function's own explicit inference, since the roadmap does not
    define "accepted" for a Golden Master record: "before the Golden
    Master is accepted" is read as "before quality_claim_ids is relied
    on", independent of any outcome-contract-v1 state -- the layered
    outcome contract's own state machine (draft/contracted/planned/
    in-flight/delivered/superseded) names no "accepted" state either, so
    this check stays independent of it rather than guessing which outcome
    state the roadmap meant.

    Returns a list of problems, empty when every claim is registered and
    FROZEN or later."""
    problems = []
    quality_claim_ids = golden_master_record.get("quality_claim_ids")
    if not isinstance(quality_claim_ids, list):
        problems.append("quality_claim_ids: must be a list, got %r" % (quality_claim_ids,))
        return problems
    for claim_id in quality_claim_ids:
        lifecycle_record = lifecycle_records_by_claim_id.get(claim_id)
        if lifecycle_record is None:
            problems.append(
                "claim_id %r: named in quality_claim_ids but no "
                "claim-lifecycle-v1 record was registered for it" % claim_id)
            continue
        state = lifecycle_record.get("state")
        if state not in STATES or STATES.index(state) < FROZEN_INDEX:
            problems.append(
                "claim_id %r: state is %r, must be FROZEN or later before "
                "the Golden Master is accepted" % (claim_id, state))
    return problems


def _resolved_copy(quality_record, measured_value, note, now=None):
    rec = dict(quality_record)
    rec["measured_value"] = measured_value
    rec["resolved_at"] = _now_iso(now)
    rec["evidence_note"] = note
    return rec


def _unresolved_copy(quality_record, note):
    rec = dict(quality_record)
    rec["evidence_note"] = note
    return rec


def _verdict_for(measured_value, threshold, comparison):
    if comparison == "gte":
        return "PASS" if measured_value >= threshold else "FAIL"
    return "PASS" if measured_value <= threshold else "FAIL"


def resolve_catastrophic_false_merge_tolerance(quality_record, candidates, now=None):
    """Real operational evidence: risk_review_orchestrator.py's own
    route_batch, called here rather than reimplemented. A claim registered
    with quality_dimension='catastrophic_false_merge_tolerance' is resolved
    by measuring, over a real batch of routed candidates, what fraction of
    the HIGH/CATASTROPHIC false_merge_cost_class candidates (RRO's own
    HIGH_RISK_COST_CLASSES, reused not redeclared) were routed anywhere
    other than CLERICAL_REVIEW -- the exact property
    risk_review_orchestrator.py's own module docstring names as what it
    defends: "a catastrophic-cost-if-wrong candidate never silently
    auto-accepts on score alone." A candidate route_candidate refuses
    (routing=NO-DATA, e.g. a malformed score) counts as a violation too:
    it was not safely routed to review either.

    `candidates` is a list of candidate dicts in route_batch's own input
    shape (candidate_id, score, false_merge_cost_class,
    missed_match_cost_class, merge_threshold, review_threshold).

    Returns (resolved_or_unresolved_quality_record, verdict). NO-DATA
    (quality_record returned unresolved) when there is nothing high-risk in
    this batch to measure tolerance against -- an all-low-risk batch proves
    nothing about catastrophic tolerance either way."""
    if quality_record.get("quality_dimension") != "catastrophic_false_merge_tolerance":
        raise RegistrationError(
            "resolve_catastrophic_false_merge_tolerance: quality_dimension "
            "must be 'catastrophic_false_merge_tolerance', got %r"
            % quality_record.get("quality_dimension"))

    decisions, _summary = RRO.route_batch(candidates)
    high_risk = [
        (c, d) for c, d in zip(candidates, decisions)
        if c.get("false_merge_cost_class") in RRO.HIGH_RISK_COST_CLASSES
    ]
    if not high_risk:
        return _unresolved_copy(
            quality_record,
            "no HIGH/CATASTROPHIC false_merge_cost_class candidate in this "
            "batch of %d to measure tolerance against" % len(candidates),
        ), "NO-DATA"

    violations = [(c, d) for c, d in high_risk if d["routing"] != "CLERICAL_REVIEW"]
    violation_rate = len(violations) / len(high_risk)
    note = (
        "risk_review_orchestrator.route_batch over %d HIGH/CATASTROPHIC-cost "
        "candidate(s) of %d total: %d routed away from CLERICAL_REVIEW"
        % (len(high_risk), len(candidates), len(violations))
    )
    resolved = _resolved_copy(quality_record, violation_rate, note, now)
    verdict = _verdict_for(violation_rate, quality_record["threshold"], quality_record["comparison"])
    return resolved, verdict


def run_reconciliation_batch(fields):
    """Real operational evidence generator for reconciliation_tolerance:
    runs survivorship_lineage.py's own resolve_field over a batch of
    (field_name, candidates, rule) entries, never reimplementing the
    comparison logic itself. survivorship_lineage.resolve_field raises
    ValueError for a field it cannot reconcile (no candidates, or an
    explicit-priority rule naming no source present among candidates --
    see test_survivorship_lineage.py's own
    test_explicit_priority_list_raises_when_no_priority_source_present);
    that raise IS the real evidence of a reconciliation failure, caught
    here and reported as success=False rather than left to crash the
    caller.

    Returns a list of {"field_name": ..., "success": bool, "lineage": dict
    or None, "error": str or None} dicts, one per entry, in order."""
    attempts = []
    for field_name, candidates, rule in fields:
        try:
            _decision, lineage = SL.resolve_field(field_name, candidates, rule)
        except (ValueError, TypeError) as exc:
            attempts.append({
                "field_name": field_name, "success": False,
                "lineage": None, "error": str(exc),
            })
        else:
            attempts.append({
                "field_name": field_name, "success": True,
                "lineage": lineage, "error": None,
            })
    return attempts


def resolve_reconciliation_tolerance(quality_record, reconciliation_attempts, now=None):
    """Real operational evidence: the failure rate over a real batch of
    survivorship_lineage.py reconciliation attempts (see
    run_reconciliation_batch above), never a fabricated number.

    Returns (resolved_or_unresolved_quality_record, verdict). NO-DATA
    (unresolved) when `reconciliation_attempts` is empty -- nothing was
    actually reconciled yet to measure against."""
    if quality_record.get("quality_dimension") != "reconciliation_tolerance":
        raise RegistrationError(
            "resolve_reconciliation_tolerance: quality_dimension must be "
            "'reconciliation_tolerance', got %r" % quality_record.get("quality_dimension"))

    if not reconciliation_attempts:
        return _unresolved_copy(
            quality_record, "no reconciliation attempts run yet to measure against",
        ), "NO-DATA"

    failures = [a for a in reconciliation_attempts if not a["success"]]
    failure_rate = len(failures) / len(reconciliation_attempts)
    note = (
        "survivorship_lineage.resolve_field over %d field reconciliation "
        "attempt(s): %d failed" % (len(reconciliation_attempts), len(failures))
    )
    resolved = _resolved_copy(quality_record, failure_rate, note, now)
    verdict = _verdict_for(failure_rate, quality_record["threshold"], quality_record["comparison"])
    return resolved, verdict


def resolve_no_evidence_available(quality_record):
    """Honest NO-DATA for the four quality_dimension values this codebase
    has no real operational evidence source for yet (precision_floor,
    recall_floor, segment_floor, residual_normalization_error -- see
    NO_EVIDENCE_REASONS above, one entry per dimension, each naming the
    sibling module checked and why it does not produce this figure). Never
    fabricates a measured_value; leaves the record unresolved with an
    honest reason recorded in evidence_note.

    Returns (unresolved_quality_record, "NO-DATA")."""
    dimension = quality_record.get("quality_dimension")
    reason = NO_EVIDENCE_REASONS.get(
        dimension, "no real operational evidence source exists yet for %r" % dimension)
    return _unresolved_copy(quality_record, reason), "NO-DATA"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="command", required=True)

    check_p = sub.add_parser("check", help="validate one claim-mdm-quality-v1 record")
    check_p.add_argument("record", help="path to a claim-mdm-quality-v1 JSON record")
    check_p.add_argument("--schema", default=DEFAULT_SCHEMA)
    check_p.add_argument("--lifecycle-record", default=None,
                          help="optional path to the matching claim-lifecycle-v1 record")
    check_p.add_argument("--master-record", default=None,
                          help="optional path to the golden-master-contract-v1 record")

    register_p = sub.add_parser(
        "register", help="register and freeze WBS-50.04 quality claims for a Golden Master")
    register_p.add_argument("master", help="path to a golden-master-contract-v1 JSON record")
    register_p.add_argument(
        "thresholds", help="path to a JSON object {dimension: {threshold, comparison}}")
    register_p.add_argument("--out", default=None)

    gate_p = sub.add_parser(
        "gate", help="run check_registered_before_publication for a Golden Master")
    gate_p.add_argument("master", help="path to a golden-master-contract-v1 JSON record")
    gate_p.add_argument("claims", help="path to a JSON list of claim-lifecycle-v1 records")

    args = ap.parse_args(argv)

    if args.command == "check":
        try:
            record = CC.load_json(args.record, "claim mdm quality record")
            schema = CC.load_json(args.schema, "claim mdm quality schema")
            lifecycle_record = (
                CC.load_json(args.lifecycle_record, "claim lifecycle record")
                if args.lifecycle_record else None)
            master_record = (
                CC.load_json(args.master_record, "golden master record")
                if args.master_record else None)
        except CC.NoData as exc:
            print("%s: %s" % (VERDICTS[2], exc))
            return 2
        problems = check(record, schema)
        if lifecycle_record is not None:
            problems.extend(check_matches_lifecycle(record, lifecycle_record))
        if master_record is not None:
            problems.extend(check_matches_master(record, master_record))
        if problems:
            print("%s: %d problem(s)" % (VERDICTS[1], len(problems)))
            for p in problems:
                print(" -", p)
            return 1
        print("%s: %s validates as claim-mdm-quality-v1" % (VERDICTS[0], args.record))
        return 0

    if args.command == "register":
        try:
            master_record = CC.load_json(args.master, "golden master record")
            thresholds = CC.load_json(args.thresholds, "thresholds")
        except CC.NoData as exc:
            print("%s: %s" % (VERDICTS[2], exc))
            return 2
        try:
            pairs = freeze_claims(register_claims(master_record, thresholds))
        except RegistrationError as exc:
            print("%s: %s" % (VERDICTS[1], exc))
            return 1
        out = [{"lifecycle": lc, "quality": qc} for lc, qc in pairs]
        text = json.dumps(out, indent=2, ensure_ascii=False, sort_keys=True)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as handle:
                handle.write(text + "\n")
        else:
            print(text)
        return 0

    # gate
    try:
        master_record = CC.load_json(args.master, "golden master record")
        claim_records = CC.load_json(args.claims, "claim lifecycle records")
    except CC.NoData as exc:
        print("%s: %s" % (VERDICTS[2], exc))
        return 2
    if not isinstance(claim_records, list):
        print("%s: claims: top level must be a JSON list" % VERDICTS[2])
        return 2
    by_claim_id = {r.get("claim_id"): r for r in claim_records if isinstance(r, dict)}
    problems = check_registered_before_publication(master_record, by_claim_id)
    if problems:
        print("%s: %d problem(s)" % (VERDICTS[1], len(problems)))
        for p in problems:
            print(" -", p)
        return 1
    print("%s: every claim in quality_claim_ids is registered and FROZEN or later" % VERDICTS[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
