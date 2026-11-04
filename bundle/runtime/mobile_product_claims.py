#!/usr/bin/env python3
"""WBS-50.05 Mobile product claims. The roadmap's own words (docs/plan,
WBS-50.05, confirmed directly against
~/Downloads/BROTHER_1.0.17_CONVERGENCE_ROADMAP_2026-09-14.md lines 1790-1799):

    Journey Passport may reference claims like:
    - completion;
    - crash-free rate;
    - latency;
    - return usage.
    Mobile technical PASS never silently resolves product-success claims.

A thin binding layer, not a reimplementation: this module never re-derives
claim_lifecycle.py's state machine, never re-scores anything
journey_passport.py itself already owns. It ties three already-built pieces
together, mirroring golden_master_quality_claims.py's (WBS-50.04) exact
structural pattern for the mobile domain:

  REGISTRATION AND CONTENT. journey_passport.py already names the hook
  point for this module in its own docstring: "production, accessibility,
  and performance... still have no sibling module (accessibility and
  performance verdicts and a production-claims module are not listed at all
  yet)." register_claims() is that production-claims module: it turns a
  composed journey-passport-v1 record (journey_passport.compose_passport's
  own output) plus a caller-given set of thresholds into claim-lifecycle-v1
  DRAFT records (scripts/claim_lifecycle.py's own shape, reused not
  reinvented) plus their content: a new schema, docs/schema/
  claim-mobile-product-v1.json, since claim-lifecycle-v1 is explicit that
  "a claim's own content... is deferred to later WBS-50 units" and
  claim-score-v1 (WBS-50.03) is a probabilistic-forecast shape
  (interval/probability), not the floor/ceiling shape these four example
  claims need -- mirroring claim-mdm-quality-v1's (WBS-50.04) shape
  instead, not reinventing a third one.

  FREEZING. freeze_claims() advances a DRAFT claim to FROZEN by walking
  claim_lifecycle.py's own chain (CL.next_state, CL.check_transition) --
  journey_passport.py itself has no publication workflow to hook a freeze
  step into (it composes a view over evidence, nothing more), so
  check_referenced_by_passport() is this unit's own minimal integration,
  mirroring golden_master_quality_claims.check_registered_before_publication
  exactly: given a composed journey-passport-v1 record and the claims
  registered for it, refuse unless every claim_id in the passport's own
  production.record.claim_ids is FROZEN or later.

  RESOLUTION -- THE CRITICAL RULE. Real product/analytics evidence only,
  per this project's rule: never a fabricated PASS, and never a technical
  PASS standing in for product evidence. Grepped scripts/ for
  analytics|BrotherDS|completion_rate|crash_free|return_usage|latency (the
  same check WBS-50.04's builder ran for precision_floor/recall_floor):
  no real operational evidence source exists in this codebase for any of
  the four example dimensions -- journey_passport.py's own technical
  dimensions (contract, build_identity, functional, accessibility,
  performance, visual, physical_device, release) measure whether the
  reference build itself installs, launches, and runs correctly; none of
  them measures whether real production users complete a journey, stay
  crash-free, see acceptable latency, or return. resolve_no_evidence_
  available() names the honest reason for each, per this module's
  NO_EVIDENCE_REASONS, and structurally never reads a passport's
  field_verdicts to set measured_value or the returned verdict -- even
  when every single technical dimension reads PASS, this function still
  returns the record unresolved and "NO-DATA". Never a made-up
  measured_value.

Explicit inference this unit makes, since the roadmap does not name a
schema for journey_passport.py's own composed output (there is no
docs/schema/journey-passport-v1.json; journey_passport.py's shape is
enforced structurally by its own compose_passport, never by a separate
schema file): register_claims/check_referenced_by_passport/
check_matches_passport work with the composed passport dict structurally
(reading passport["journey"] and passport["production"]["record"]
["claim_ids"]), the same way golden_master_quality_claims.py works with a
golden-master-contract-v1 record's own fields without re-validating it
against golden-master-contract-v1.json itself.
"""
import argparse
import datetime
import json
import os
import sys

import claim_lifecycle as CL
import contract_check as CC
import evidence_obligation

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_SCHEMA = os.path.join(ROOT, "docs", "schema", "claim-mobile-product-v1.json")

VERDICTS = evidence_obligation.VERDICTS  # ("PASS", "FAIL", "NO-DATA")

#: Reused, never redeclared: claim_lifecycle.py's own chain and its FROZEN
#: index.
STATES = CL.STATES
FROZEN_INDEX = CL.FROZEN_INDEX

#: The four example dimensions the roadmap names for WBS-50.05, in the
#: roadmap's own order. Mirrors docs/schema/claim-mobile-product-v1.json's
#: own product_dimension enum exactly (test_dimensions_match_the_schema_enum
#: in the test suite catches drift between the two, the same by-hand pairing
#: golden_master_quality_claims.py's DIMENSIONS keeps with its own schema).
DIMENSIONS = ("completion", "crash_free_rate", "latency", "return_usage")

#: Mirrors the schema's own comparison enum.
COMPARISONS = ("gte", "lte")

#: All four DIMENSIONS have no real operational evidence source anywhere in
#: this codebase yet, each with the honest reason why -- which journey_
#: passport.py technical dimension was checked, and why it is not product
#: evidence. Read by resolve_no_evidence_available().
NO_EVIDENCE_REASONS = {
    "completion": (
        "no analytics/BrotherDS evidence source exists anywhere in this "
        "codebase yet; journey_passport.py's technical dimensions "
        "(contract, build_identity, functional, accessibility, performance, "
        "physical_device, release) measure whether the reference mobile "
        "build installs, launches, and runs correctly -- never whether "
        "real production users complete this journey"),
    "crash_free_rate": (
        "no analytics/BrotherDS evidence source exists anywhere in this "
        "codebase yet; journey_passport.py's own 'functional' dimension "
        "(native_evidence_v2.py, WBS-30.05) is a scoped test-run verdict "
        "over one candidate revision, not a production crash-free rate "
        "over real usage -- a technical PASS there is not evidence real "
        "users do not crash"),
    "latency": (
        "no analytics/BrotherDS evidence source exists anywhere in this "
        "codebase yet; journey_passport.py's own 'performance' dimension "
        "is caller-supplied structured evidence shape-checked for a "
        "'checks' key only (per journey_passport.py's own docstring, "
        "'performance verdicts... are not listed at all yet' as a real "
        "sibling module) -- it is not a real measured production latency "
        "figure"),
    "return_usage": (
        "no analytics/BrotherDS evidence source exists anywhere in this "
        "codebase yet; nothing in this repository observes whether a real "
        "user returns to the app after first use"),
}


class RegistrationError(ValueError):
    """Raised when register_claims, freeze_claims, or a resolve_* function
    is asked to do something the roadmap's own rules refuse: an
    unrecognized dimension or comparison, a non-numeric threshold, a
    mismatched product_dimension passed to the wrong resolver, or a
    transition claim_lifecycle.py itself would not validate. Mirrors
    golden_master_quality_claims.RegistrationError -- a caller mistake,
    never a data-quality NO-DATA."""


def _parse_iso(value):
    """Same tolerant ISO-8601 parse golden_master_quality_claims.py's own
    hand_rules uses (ported, not imported, the same convention that module
    itself documents for claim_score.py: "does not expose it as a public
    helper"). Returns None for anything that is not a non-empty string or
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
    """The rules a single claim-mobile-product-v1 record's schema keywords
    cannot express: measured_value/resolved_at must be null together or
    non-null together (mirrors claim-mdm-quality-v1's own pairing), and
    resolved_at must be a parseable ISO-8601 timestamp when present."""
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
    """Structural validation against claim-mobile-product-v1, plus the hand
    rules a single record can carry. check_matches_lifecycle and
    check_matches_passport compare two records and are called directly, not
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


def check_matches_lifecycle(product_record, lifecycle_record):
    """A claim-mobile-product-v1 record documents one exact claim revision:
    its claim_id and revision_id must match the claim-lifecycle-v1 record it
    was registered for. Mirrors golden_master_quality_claims.py's own
    check_matches_lifecycle. Returns a list of problems, empty when they
    match."""
    problems = []
    if product_record.get("claim_id") != lifecycle_record.get("claim_id"):
        problems.append(
            "claim_id: product record names %r, lifecycle record is %r"
            % (product_record.get("claim_id"), lifecycle_record.get("claim_id")))
    if product_record.get("revision_id") != lifecycle_record.get("revision_id"):
        problems.append(
            "revision_id: product record names %r, lifecycle record is %r "
            "(scoring must resolve the exact historical revision, never a "
            "rewritten successor -- WBS-50.01's own scoring-target rule)"
            % (product_record.get("revision_id"), lifecycle_record.get("revision_id")))
    return problems


def check_matches_passport(product_record, journey_passport_record):
    """The binding this whole unit exists for: a mobile product claim must
    belong to the journey_passport's own journey_id, and its claim_id must
    actually appear in that passport's own production.record.claim_ids
    array -- the hook point journey_passport.py's own docstring already
    names ("a production-claims module... not listed at all yet"), never a
    second hook invented here. A product record whose claim_id the parent
    passport never listed is not really "feeding" it. Returns a list of
    problems, empty when both hold."""
    problems = []
    journey_id = journey_passport_record.get("journey")
    if product_record.get("journey_id") != journey_id:
        problems.append(
            "journey_id: product record names %r, journey passport is %r"
            % (product_record.get("journey_id"), journey_id))
    claim_id = product_record.get("claim_id")
    production = journey_passport_record.get("production") or {}
    claim_ids = (production.get("record") or {}).get("claim_ids")
    if isinstance(claim_ids, list) and claim_id not in claim_ids:
        problems.append(
            "claim_id: %r does not appear in the journey passport's own "
            "production.record.claim_ids %r" % (claim_id, claim_ids))
    return problems


def register_claims(journey_passport_record, thresholds):
    """"Journey Passport may reference claims like: completion; crash-free
    rate; latency; return usage." Build one claim-lifecycle-v1 DRAFT record
    plus one claim-mobile-product-v1 content record per entry in
    `thresholds`, a dict {product_dimension: {"threshold": number,
    "comparison": "gte"|"lte"}}. Not required to name all four DIMENSIONS --
    the roadmap's list is stated as examples ("claims like"), so a caller
    registers whichever subset applies to this journey.

    journey_id is read from the composed passport itself
    (journey_passport_record["journey"]), never accepted as a bare
    parameter -- the same discipline journey_passport.compose_passport
    itself documents for its own "journey" and "candidate_revision" fields.

    claim_id is deterministic ("<journey_id>:<dimension>"), never random:
    the same journey registering the same dimension twice names the same
    claim (a re-registration is a correction per claim_lifecycle.py's own
    rules, not a fresh identity -- this function does not itself build the
    correction, see claim_lifecycle.check_correction for that shape).
    revision_id is that claim's first revision ("<claim_id>:r1").

    Returns a list of (lifecycle_record, product_record) pairs, in the
    order `thresholds` was given. Raises RegistrationError for a caller
    mistake (missing/NO-DATA journey, unknown dimension, unknown
    comparison, non-numeric threshold) -- never silently drops or
    reinterprets one."""
    journey_id = journey_passport_record.get("journey")
    if not isinstance(journey_id, str) or not journey_id or journey_id == "NO-DATA":
        raise RegistrationError(
            "journey_passport_record: journey must be a non-empty, "
            "resolved journey_id, got %r" % (journey_id,))

    pairs = []
    for dimension, spec in thresholds.items():
        if dimension not in DIMENSIONS:
            raise RegistrationError(
                "product_dimension %r is not one of %r" % (dimension, DIMENSIONS))
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

        claim_id = "%s:%s" % (journey_id, dimension)
        revision_id = "%s:r1" % claim_id
        lifecycle_record = {
            "schema_version": "claim-lifecycle-v1",
            "claim_id": claim_id,
            "revision_id": revision_id,
            "state": "DRAFT",
            "supersedes": None,
        }
        product_record = {
            "schema_version": "claim-mobile-product-v1",
            "claim_id": claim_id,
            "revision_id": revision_id,
            "journey_id": journey_id,
            "product_dimension": dimension,
            "comparison": comparison,
            "threshold": threshold,
            "measured_value": None,
            "resolved_at": None,
            "evidence_note": None,
        }
        pairs.append((lifecycle_record, product_record))
    return pairs


def freeze_claims(pairs):
    """Advances every lifecycle_record in `pairs` from DRAFT to FROZEN by
    walking claim_lifecycle.py's own chain (CL.next_state), validating each
    step with CL.check_transition -- reusing the roadmap's one enforced
    state machine rather than a second, parallel one here. Mirrors
    golden_master_quality_claims.freeze_claims exactly, for the mobile
    domain. product_record is carried through unchanged (freezing is a
    lifecycle-only operation; the product content was already frozen at
    register_claims time by simply never being mutated again until a
    resolve_* function runs).

    Returns a new list of (frozen_lifecycle_record, product_record) pairs,
    same order as `pairs`. Raises RegistrationError if any pair is not
    currently at DRAFT, or if claim_lifecycle.py itself refuses a step
    (defensive: CL.next_state/check_transition are reused precisely so
    this should never happen for a pair register_claims produced, but a
    hand-built pair from elsewhere is checked for real, never trusted)."""
    frozen_pairs = []
    for lifecycle_record, product_record in pairs:
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
        frozen_pairs.append((current, product_record))
    return frozen_pairs


def check_referenced_by_passport(journey_passport_record, lifecycle_records_by_claim_id):
    """journey_passport.py has no natural hook point of its own to call
    directly beyond the "production" field its own docstring already names
    as the reserved slot for "a production-claims module... not listed at
    all yet." This function is that module's own minimal integration, built
    exactly as golden_master_quality_claims.check_registered_before_
    publication was for the Golden Master domain: given a composed
    journey-passport-v1 record and a {claim_id: claim-lifecycle-v1 record}
    map, verify that every claim_id the passport's own
    production.record.claim_ids names is registered and at FROZEN state or
    later. A caller (a future release-gate step, or a human reviewer) runs
    this before treating those product claims as relied upon.

    Deliberately never reads any OTHER field on the passport (contract,
    build_identity, functional, accessibility, performance, visual,
    physical_device, release): those are technical dimensions, and per the
    roadmap's own critical rule a technical PASS must never silently
    resolve or gate a product-success claim. Only production.record.
    claim_ids -- the passport's own explicit, caller-supplied claim
    references -- is read here.

    Returns a list of problems, empty when every referenced claim is
    registered and FROZEN or later (including when production is absent or
    names no claim_ids at all: nothing to check is not a violation, mirrors
    golden_master_quality_claims's own empty quality_claim_ids case)."""
    problems = []
    production = journey_passport_record.get("production") or {}
    record = production.get("record")
    if record is None:
        return problems  # no production claim_ids supplied yet: nothing to check
    claim_ids = record.get("claim_ids")
    if not isinstance(claim_ids, list):
        problems.append("production.record.claim_ids: must be a list, got %r" % (claim_ids,))
        return problems
    for claim_id in claim_ids:
        lifecycle_record = lifecycle_records_by_claim_id.get(claim_id)
        if lifecycle_record is None:
            problems.append(
                "claim_id %r: named in journey_passport's production.claim_ids "
                "but no claim-lifecycle-v1 record was registered for it" % claim_id)
            continue
        state = lifecycle_record.get("state")
        if state not in STATES or STATES.index(state) < FROZEN_INDEX:
            problems.append(
                "claim_id %r: state is %r, must be FROZEN or later before "
                "this product claim is relied on" % (claim_id, state))
    return problems


def _resolved_copy(product_record, measured_value, note, now=None):
    rec = dict(product_record)
    rec["measured_value"] = measured_value
    rec["resolved_at"] = _now_iso(now)
    rec["evidence_note"] = note
    return rec


def _unresolved_copy(product_record, note):
    rec = dict(product_record)
    rec["evidence_note"] = note
    return rec


def resolve_no_evidence_available(product_record, journey_passport_record=None):
    """Honest NO-DATA for every claim-mobile-product-v1 dimension
    (completion, crash_free_rate, latency, return_usage): none has a real
    operational evidence source anywhere in this codebase yet (see
    NO_EVIDENCE_REASONS above, one entry per dimension, each naming the
    journey_passport.py technical dimension checked and why it is not
    product evidence). Never fabricates a measured_value; leaves the record
    unresolved with an honest reason recorded in evidence_note.

    THE CRITICAL STRUCTURAL RULE (the roadmap's own words: "Mobile
    technical PASS never silently resolves product-success claims"):
    `journey_passport_record`, when given, is read ONLY to compose the
    evidence_note's own honest account of what technical evidence was
    observed (for audit transparency) -- its field_verdicts (functional,
    build_identity, contract, accessibility, performance, physical_device,
    release, ...) are NEVER used to set measured_value, resolved_at, or the
    returned verdict. Even a journey_passport where every single technical
    dimension reads PASS resolves nothing here: this function always
    returns the record unresolved (measured_value/resolved_at stay None)
    and "NO-DATA", because a technical PASS is evidence the reference build
    runs correctly, not evidence real production users completed the
    journey, stayed crash-free, saw acceptable latency, or returned -- a
    fundamentally different kind of evidence this codebase does not yet
    collect.

    Returns (unresolved_product_record, "NO-DATA")."""
    dimension = product_record.get("product_dimension")
    reason = NO_EVIDENCE_REASONS.get(
        dimension, "no real operational evidence source exists yet for %r" % dimension)
    if journey_passport_record is not None:
        technical = journey_passport_record.get("field_verdicts", {})
        reason = reason + (
            " (journey_passport's technical field_verdicts observed at "
            "resolution time were %r; not read to resolve this product "
            "claim -- a technical PASS is not product evidence, per the "
            "roadmap's own rule)" % (technical,))
    return _unresolved_copy(product_record, reason), "NO-DATA"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="command", required=True)

    check_p = sub.add_parser("check", help="validate one claim-mobile-product-v1 record")
    check_p.add_argument("record", help="path to a claim-mobile-product-v1 JSON record")
    check_p.add_argument("--schema", default=DEFAULT_SCHEMA)
    check_p.add_argument("--lifecycle-record", default=None,
                          help="optional path to the matching claim-lifecycle-v1 record")
    check_p.add_argument("--passport-record", default=None,
                          help="optional path to the composed journey-passport-v1 record")

    register_p = sub.add_parser(
        "register", help="register and freeze WBS-50.05 mobile product claims for a journey")
    register_p.add_argument("passport", help="path to a composed journey-passport-v1 JSON record")
    register_p.add_argument(
        "thresholds", help="path to a JSON object {product_dimension: {threshold, comparison}}")
    register_p.add_argument("--out", default=None)

    gate_p = sub.add_parser(
        "gate", help="run check_referenced_by_passport for a journey passport")
    gate_p.add_argument("passport", help="path to a composed journey-passport-v1 JSON record")
    gate_p.add_argument("claims", help="path to a JSON list of claim-lifecycle-v1 records")

    args = ap.parse_args(argv)

    if args.command == "check":
        try:
            record = CC.load_json(args.record, "mobile product claim record")
            schema = CC.load_json(args.schema, "mobile product claim schema")
            lifecycle_record = (
                CC.load_json(args.lifecycle_record, "claim lifecycle record")
                if args.lifecycle_record else None)
            passport_record = (
                CC.load_json(args.passport_record, "journey passport record")
                if args.passport_record else None)
        except CC.NoData as exc:
            print("%s: %s" % (VERDICTS[2], exc))
            return 2
        problems = check(record, schema)
        if lifecycle_record is not None:
            problems.extend(check_matches_lifecycle(record, lifecycle_record))
        if passport_record is not None:
            problems.extend(check_matches_passport(record, passport_record))
        if problems:
            print("%s: %d problem(s)" % (VERDICTS[1], len(problems)))
            for p in problems:
                print(" -", p)
            return 1
        print("%s: %s validates as claim-mobile-product-v1" % (VERDICTS[0], args.record))
        return 0

    if args.command == "register":
        try:
            passport_record = CC.load_json(args.passport, "journey passport record")
            thresholds = CC.load_json(args.thresholds, "thresholds")
        except CC.NoData as exc:
            print("%s: %s" % (VERDICTS[2], exc))
            return 2
        try:
            pairs = freeze_claims(register_claims(passport_record, thresholds))
        except RegistrationError as exc:
            print("%s: %s" % (VERDICTS[1], exc))
            return 1
        out = [{"lifecycle": lc, "product": pc} for lc, pc in pairs]
        text = json.dumps(out, indent=2, ensure_ascii=False, sort_keys=True)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as handle:
                handle.write(text + "\n")
        else:
            print(text)
        return 0

    # gate
    try:
        passport_record = CC.load_json(args.passport, "journey passport record")
        claim_records = CC.load_json(args.claims, "claim lifecycle records")
    except CC.NoData as exc:
        print("%s: %s" % (VERDICTS[2], exc))
        return 2
    if not isinstance(claim_records, list):
        print("%s: claims: top level must be a JSON list" % VERDICTS[2])
        return 2
    by_claim_id = {r.get("claim_id"): r for r in claim_records if isinstance(r, dict)}
    problems = check_referenced_by_passport(passport_record, by_claim_id)
    if problems:
        print("%s: %d problem(s)" % (VERDICTS[1], len(problems)))
        for p in problems:
            print(" -", p)
        return 1
    print("%s: every claim in production.record.claim_ids is registered and FROZEN or later" % VERDICTS[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
