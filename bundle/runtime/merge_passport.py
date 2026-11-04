#!/usr/bin/env python3
"""WBS-40.07 Merge Passport: a VIEW composing evidence that already exists
elsewhere, never a second truth store. Per the roadmap's own words: "Again:
a view over evidence, not a second truth store."

compose_passport() never accepts a raw claim. Every field is either read
directly from a real sibling record passed in, or computed by calling that
sibling's own real function (e.g. rollback.supported comes from actually
calling reversibility_gate.evaluate_merge_candidate() on the caller's
candidate, never a free-standing boolean the caller could assert).

Real design input checked before this was built (per
docs/plan/1.0.17/WAVE-2-DESIGN-CRITIQUES-2026-09-13.md's "Merge Passport
(WBS-40.07)" section, two independent generic AI passes, secondary input
not a verdict):

  KEPT from the Deepseek draft, in spirit: bind the passport to its inputs
  by content hash so it cannot be regenerated later claiming to describe
  evidence that has since changed. Refuse (never silently default) when
  required evidence is missing.

  KEPT from the Muse hostile review, in spirit: cryptographic binding to
  the actual artifacts, not a statement that a merge happened; a passport
  is checked against a re-derived hash, not trusted at face value.

  CHANGED / NOT BUILT: Deepseek's full signed-attestation apparatus
  (DSSE/in-toto/Sigstore signatures, HSM/TEE-backed signing, an
  append-only transparency log, a COMMITTED/ABORTED state machine, a
  trusted-finalizer service) is out of scope here. This repo has no
  signing infrastructure anywhere else in the WBS-40 chain either
  (reversibility_gate.py and master_source_snapshot.py both bind evidence
  by sha256 digest, never by signature), so building one signer for this
  one module would be new, unrequested infrastructure, not a matching
  pattern. What this module builds instead, and what it can actually
  enforce with what exists in this repo: every evidence input is hashed
  at compose time (sha256_of_obj, reused from reversibility_gate.py so
  there is one canonical-hash implementation, not two), the digest is
  embedded in the passport, and verify_passport_evidence() lets a later
  caller re-supply the "same" evidence and get a concrete FAIL, not a
  silent pass, the moment a digest no longer matches. A real signer can
  be layered on top of this later without changing this module's shape.

Per docs/decisions/evidence-vocabulary-2026-09-13.json's EV-3 ruling, the
PASS/FAIL/NO-DATA triple and the OPTIONAL/REQUIRED_FOR_MERGE/
REQUIRED_FOR_RELEASE obligation triple are imported from
scripts/evidence_obligation.py, not redeclared here.

Deviation from the roadmap YAML's illustrative field list, stated plainly
rather than hidden: the roadmap sketches compose_passport as taking
"source_snapshot" (singular) and a bare "authority: publish: HUMAN"
string. The real sibling modules do not return those shapes:

  - master_source_snapshot.build_snapshot() returns ONE record per .xlsx
    file, and a golden master is built from source_systems (plural, per
    docs/schema/golden-master-contract-v1.json), so this module takes
    source_snapshots: a LIST of those records -- never file paths, so
    this module never re-opens the raw private source file itself (that
    would cross master_source_snapshot's own recorded access_boundary,
    "private, vault-only, never public"; the already-produced structural
    record is the only thing a view over evidence should touch again).
  - normalization_trace.trace() returns one chain per derived matching
    key, and a golden master's critical_attributes is plural too, so this
    module takes normalization_traces: a LIST of those records.
  - publish_authority is never accepted as a bare string parameter at
    all -- that is exactly the raw-claim shape rule (1) in the brief for
    this module forbids. It is read only from a real
    golden_master_contract_record, after that record is confirmed to
    actually validate as golden-master-contract-v1 (scripts/
    golden_master_contract.check(), reused rather than re-implemented).
    A record that fails that validation cannot source an authority claim,
    even if it happens to carry a publish_authority key.
  - candidate_generation, risk_assessment, review_record and
    survivorship_decision have no sibling module in this repo yet (the
    roadmap's own WBS-40.04/05/06/09 rows); WBS-40.09 explicitly warns
    entity matching and attribute survivorship are distinct decisions and
    must not be conflated, so this module does not compute either one. It
    accepts these as caller-supplied structured evidence (the roadmap's
    own field names), checks the shape is not empty/malformed, and digests
    it exactly like every other field so the same hollow-pass defense
    covers it once a real sibling module exists to produce it.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from evidence_obligation import LEVELS, VERDICTS, exit_code_for_verdict  # noqa: E402
import golden_master_contract as GMC  # noqa: E402
import contract_check as CC  # noqa: E402
import reversibility_gate as RG  # noqa: E402
from reversibility_gate import sha256_of_obj  # noqa: E402

NO_DATA_DIGEST = "NO-DATA"

# Which passport fields must carry real, valid evidence before the merge
# itself may go ahead, versus which may still be empty (per
# docs/schema/golden-master-contract-v1.json's own "quality_claim_ids ...
# may be empty before publish"). Declared once, here, so "refuse if a
# REQUIRED_FOR_MERGE field is missing" (rule 3) is not a scattered guess.
FIELD_OBLIGATIONS = {
    "source_snapshot": "REQUIRED_FOR_MERGE",
    "normalization_trace": "REQUIRED_FOR_MERGE",
    "candidate_generation": "REQUIRED_FOR_MERGE",
    "risk": "REQUIRED_FOR_MERGE",
    "review": "REQUIRED_FOR_MERGE",
    "rollback": "REQUIRED_FOR_MERGE",
    "survivorship": "REQUIRED_FOR_MERGE",
    "authority": "REQUIRED_FOR_MERGE",
    "evidence": "OPTIONAL",
}
if not set(FIELD_OBLIGATIONS.values()) <= set(LEVELS):
    raise AssertionError("FIELD_OBLIGATIONS uses a level outside LEVELS")


def _worst(verdicts):
    """Combine several VERDICTS into one: FAIL beats NO-DATA beats PASS."""
    verdicts = list(verdicts)
    if not verdicts:
        return "NO-DATA"
    if "FAIL" in verdicts:
        return "FAIL"
    if "NO-DATA" in verdicts:
        return "NO-DATA"
    return "PASS"


def _digest(obj):
    if obj is None:
        return NO_DATA_DIGEST
    return sha256_of_obj(obj)


def _digest_list(objs):
    return [_digest(o) for o in objs]


def _compose_source_snapshot(source_snapshots):
    if not source_snapshots:
        return {
            "records": [], "digests": [], "verdict": "NO-DATA",
            "verdict_reason": "no source snapshots supplied",
        }
    verdicts = [rec.get("verdict", "NO-DATA") for rec in source_snapshots]
    for v in verdicts:
        if v not in VERDICTS:
            raise AssertionError("source snapshot verdict %r outside the shared triple" % v)
    verdict = _worst(verdicts)
    return {
        "records": source_snapshots,
        "digests": _digest_list(source_snapshots),
        "verdict": verdict,
        "verdict_reason": "worst-of %d source snapshot record verdict(s): %s" % (len(verdicts), verdicts),
    }


def _compose_normalization_trace(normalization_traces):
    if not normalization_traces:
        return {
            "records": [], "digests": [], "verdict": "NO-DATA",
            "verdict_reason": "no normalization traces supplied",
        }
    per_record = []
    for rec in normalization_traces:
        if not rec.get("steps") or not rec.get("matching_key"):
            per_record.append("FAIL")
        else:
            per_record.append("PASS")
    verdict = _worst(per_record)
    return {
        "records": normalization_traces,
        "digests": _digest_list(normalization_traces),
        "verdict": verdict,
        "verdict_reason": "%d trace(s); a trace with no steps or no matching_key is FAIL" % len(normalization_traces),
    }


def _compose_struct(evidence, required_keys, label):
    """Shared shape for the fields with no sibling module yet
    (candidate_generation, risk, review, survivorship, evidence): missing
    entirely is NO-DATA, present but missing a required sub-key is FAIL
    (malformed evidence, not absent evidence), otherwise PASS."""
    if not evidence:
        return {
            "record": None, "digest": NO_DATA_DIGEST, "verdict": "NO-DATA",
            "verdict_reason": "no %s evidence supplied" % label,
        }
    missing = [k for k in required_keys if k not in evidence]
    if missing:
        return {
            "record": evidence, "digest": _digest(evidence), "verdict": "FAIL",
            "verdict_reason": "%s evidence missing required field(s): %s" % (label, ", ".join(missing)),
        }
    return {
        "record": evidence, "digest": _digest(evidence), "verdict": "PASS",
        "verdict_reason": "%s evidence present with required fields" % label,
    }


def _compose_review(review_record):
    result = _compose_struct(review_record, ("required", "result"), "review")
    if result["verdict"] == "PASS" and review_record.get("required") and not review_record.get("result"):
        result["verdict"] = "FAIL"
        result["verdict_reason"] = "review.required is true but no review.result was recorded"
    return result


def _normalize_survivorship_decision(survivorship_decision):
    """Accept either the flat evidence shape this module originally expected
    ({"attributes": [{"value", "source_system"}, ...]}) or the REAL per-field
    decision shape scripts/survivorship_lineage.py's resolve_field() actually
    returns ({field_name: {"value": ..., "winner_source_id": ...}, ...}, the
    same shape scripts/reversibility_gate.py's build_reversible_action()
    already consumes as survivorship_decisions).

    WBS-40.09 (survivorship_lineage.py) landed after this module's own field
    list was written, so this module's original shape never matched what its
    real sibling actually produces (scripts/mdm_canary_smoke.py, WBS-40.11,
    surfaced this threading a real resolve_field() decision straight through:
    a genuine adjacent-stage mismatch, the same shape of finding
    scripts/canary_pipeline_smoke.py, WBS-30.10, found between
    release_state_tracker and journey_passport). Normalized here so a real
    survivorship_lineage decision composes cleanly instead of a false FAIL;
    the original flat shape (already covered by test_merge_passport.py) is
    left exactly as it was.
    """
    if not survivorship_decision or "attributes" in survivorship_decision:
        return survivorship_decision
    if all(isinstance(v, dict) and "winner_source_id" in v for v in survivorship_decision.values()):
        return {"attributes": [
            {"field": field, "value": decision.get("value"), "source_system": decision.get("winner_source_id")}
            for field, decision in survivorship_decision.items()
        ]}
    return survivorship_decision


def _compose_survivorship(survivorship_decision):
    survivorship_decision = _normalize_survivorship_decision(survivorship_decision)
    result = _compose_struct(survivorship_decision, ("attributes",), "survivorship")
    if result["verdict"] != "PASS":
        return result
    attributes = survivorship_decision.get("attributes") or []
    if not attributes:
        result["verdict"] = "FAIL"
        result["verdict_reason"] = "survivorship.attributes is present but empty"
        return result
    for attr in attributes:
        if not isinstance(attr, dict) or "value" not in attr or "source_system" not in attr:
            result["verdict"] = "FAIL"
            result["verdict_reason"] = "a survivorship attribute is missing 'value' or 'source_system'"
            return result
    return result


def _compose_rollback(reversibility_candidate):
    if not reversibility_candidate:
        return {
            "supported": False, "mechanism": "NO-DATA",
            "digest": NO_DATA_DIGEST, "verdict": "NO-DATA",
            "verdict_reason": "no reversibility candidate supplied",
        }
    verdict, reason = RG.evaluate_merge_candidate(reversibility_candidate)
    if verdict not in VERDICTS:
        raise AssertionError("reversibility verdict %r outside the shared triple" % verdict)
    return {
        "supported": verdict == "PASS",
        "mechanism": reversibility_candidate.get("inverse_operation", "NO-DATA"),
        "downstream_propagation_status": reversibility_candidate.get("downstream_propagation_status"),
        "digest": _digest(reversibility_candidate),
        "verdict": verdict,
        "verdict_reason": reason,
    }


def _compose_authority(golden_master_contract_record):
    if not golden_master_contract_record:
        return {
            "publish": "NO-DATA", "source": "NO-DATA",
            "digest": NO_DATA_DIGEST, "verdict": "NO-DATA",
            "verdict_reason": "no golden master contract record supplied",
        }
    schema = CC.load_json(GMC.DEFAULT_SCHEMA, "golden master contract schema")
    problems = GMC.check(golden_master_contract_record, schema)
    if problems:
        return {
            "publish": "NO-DATA", "source": "golden_master_contract",
            "digest": _digest(golden_master_contract_record), "verdict": "FAIL",
            "verdict_reason": "golden master contract record does not validate (%d problem(s), first: %s)" % (
                len(problems), problems[0]),
        }
    return {
        "publish": golden_master_contract_record["publish_authority"],
        "source": "golden_master_contract:%s" % golden_master_contract_record.get("master_id", "NO-DATA"),
        "digest": _digest(golden_master_contract_record),
        "verdict": "PASS",
        "verdict_reason": "publish_authority read from a validated golden-master-contract-v1 record",
    }


def compose_passport(
    master_id,
    golden_master_contract_record,
    source_snapshots=None,
    normalization_traces=None,
    candidate_generation=None,
    risk_assessment=None,
    review_record=None,
    reversibility_candidate=None,
    survivorship_decision=None,
    evidence=None,
    source_entities=None,
    reference_entities=None,
    now=None,
):
    """Compose a merge passport as a VIEW over real evidence records already
    produced elsewhere. Never recomputes any of that evidence itself (it
    calls the sibling module's own function where one exists, and only
    checks shape where none exists yet); never accepts publish_authority
    as a bare string. Returns a dict, always carrying an overall "verdict"
    drawn from evidence_obligation.VERDICTS and a "refused" boolean.

    source_entities / reference_entities are plain identifier lists (like
    master_id itself), not evidence claims, so they pass through as given.
    """
    from datetime import datetime, timezone

    fields = {
        "source_snapshot": _compose_source_snapshot(list(source_snapshots or [])),
        "normalization_trace": _compose_normalization_trace(list(normalization_traces or [])),
        "candidate_generation": _compose_struct(candidate_generation, ("pathway", "model", "score"), "candidate_generation"),
        "risk": _compose_struct(risk_assessment, ("false_merge_cost", "shared_key_hub", "cluster_size_after", "bridge_edge"), "risk"),
        "review": _compose_review(review_record),
        "rollback": _compose_rollback(reversibility_candidate),
        "survivorship": _compose_survivorship(survivorship_decision),
        "authority": _compose_authority(golden_master_contract_record),
        "evidence": _compose_struct(evidence, (), "evidence"),
    }

    field_verdicts = {name: rec["verdict"] for name, rec in fields.items()}
    required_verdicts = [v for name, v in field_verdicts.items() if FIELD_OBLIGATIONS[name] == "REQUIRED_FOR_MERGE"]
    overall = _worst(required_verdicts)
    if overall not in VERDICTS:
        raise AssertionError("merge passport overall verdict %r outside the shared triple" % overall)

    passport = {
        "schema_version": "merge-passport-v1",
        "master_id": master_id,
        "composed_at": (now or datetime.now(timezone.utc)).isoformat(),
        "source_entities": list(source_entities or []),
        "reference_entities": list(reference_entities or []),
        "field_obligations": dict(FIELD_OBLIGATIONS),
        "field_verdicts": field_verdicts,
        "verdict": overall,
        "refused": overall != "PASS",
        "status": "COMPOSED" if overall == "PASS" else "REFUSED",
    }
    passport.update(fields)
    return passport


def verify_passport_evidence(
    passport,
    golden_master_contract_record=None,
    source_snapshots=None,
    normalization_traces=None,
    candidate_generation=None,
    risk_assessment=None,
    review_record=None,
    reversibility_candidate=None,
    survivorship_decision=None,
    evidence=None,
):
    """The hollow-pass defense: recompute the digest of whatever CURRENT
    evidence the caller supplies here and compare it to the digest already
    recorded in the passport for that field. A passport can be regenerated
    (or hand-edited) to claim the same conclusion later, but it cannot make
    the recomputed digest of the real evidence match unless the evidence
    itself is byte-for-byte what was actually composed from. Any supplied
    field whose digest no longer matches is a concrete MISMATCH, not a
    silently accepted claim.

    Only fields the caller actually supplies here are checked (checking
    nothing is NO-DATA, not a false PASS); the overall verdict is FAIL the
    moment any supplied field mismatches.
    """
    checks = {}

    def _check_single(field, live_obj):
        recorded = passport.get(field, {}).get("digest")
        live_digest = _digest(live_obj)
        checks[field] = "MATCH" if live_digest == recorded else (
            "MISMATCH: recorded digest %s, recomputed digest %s" % (recorded, live_digest))

    def _check_list(field, live_objs):
        recorded = passport.get(field, {}).get("digests")
        live_digests = _digest_list(live_objs)
        checks[field] = "MATCH" if live_digests == recorded else (
            "MISMATCH: recorded digests %s, recomputed digests %s" % (recorded, live_digests))

    if golden_master_contract_record is not None:
        _check_single("authority", golden_master_contract_record)
    if source_snapshots is not None:
        _check_list("source_snapshot", list(source_snapshots))
    if normalization_traces is not None:
        _check_list("normalization_trace", list(normalization_traces))
    if candidate_generation is not None:
        _check_single("candidate_generation", candidate_generation)
    if risk_assessment is not None:
        _check_single("risk", risk_assessment)
    if review_record is not None:
        _check_single("review", review_record)
    if reversibility_candidate is not None:
        _check_single("rollback", reversibility_candidate)
    if survivorship_decision is not None:
        _check_single("survivorship", survivorship_decision)
    if evidence is not None:
        _check_single("evidence", evidence)

    if not checks:
        return {"checks": {}, "verdict": "NO-DATA", "verdict_reason": "no current evidence supplied to verify against"}

    mismatched = [f for f, r in checks.items() if r != "MATCH"]
    if mismatched:
        return {
            "checks": checks, "verdict": "FAIL",
            "verdict_reason": "digest mismatch on: %s" % ", ".join(sorted(mismatched)),
        }
    return {"checks": checks, "verdict": "PASS", "verdict_reason": "all supplied evidence still matches its recorded digest"}


def _load_json_or_none(path):
    if not path:
        return None
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--master-id", required=True)
    parser.add_argument("--contract", required=True, help="path to a golden-master-contract-v1 JSON record")
    parser.add_argument("--source-snapshot", action="append", default=[], help="path to a master_source_snapshot.py JSON record (repeatable)")
    parser.add_argument("--normalization-trace", action="append", default=[], help="path to a normalization_trace.py JSON record (repeatable)")
    parser.add_argument("--candidate-generation", default=None)
    parser.add_argument("--risk", default=None)
    parser.add_argument("--review", default=None)
    parser.add_argument("--reversibility", default=None, help="path to a reversible_action-shaped JSON record")
    parser.add_argument("--survivorship", default=None)
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)

    try:
        contract = _load_json_or_none(args.contract)
        source_snapshots = [_load_json_or_none(p) for p in args.source_snapshot]
        normalization_traces = [_load_json_or_none(p) for p in args.normalization_trace]
        candidate_generation = _load_json_or_none(args.candidate_generation)
        risk_assessment = _load_json_or_none(args.risk)
        review_record = _load_json_or_none(args.review)
        reversibility_candidate = _load_json_or_none(args.reversibility)
        survivorship_decision = _load_json_or_none(args.survivorship)
    except FileNotFoundError as exc:
        print("NO-DATA: %s" % exc)
        return 2
    except json.JSONDecodeError as exc:
        print("NO-DATA: not valid JSON: %s" % exc)
        return 2

    passport = compose_passport(
        master_id=args.master_id,
        golden_master_contract_record=contract,
        source_snapshots=source_snapshots,
        normalization_traces=normalization_traces,
        candidate_generation=candidate_generation,
        risk_assessment=risk_assessment,
        review_record=review_record,
        reversibility_candidate=reversibility_candidate,
        survivorship_decision=survivorship_decision,
    )

    text = json.dumps(passport, indent=2, ensure_ascii=False, sort_keys=True)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
    else:
        print(text)
    return exit_code_for_verdict(passport["verdict"])


if __name__ == "__main__":
    sys.exit(main())
