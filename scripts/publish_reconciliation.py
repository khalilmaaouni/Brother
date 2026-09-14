#!/usr/bin/env python3
"""WBS-40.10 Publish Reconciliation: what Brother checks AFTER an external
publish (Stibo/SAP/Dynamics or equivalent) has already happened.

Per the roadmap's own words for this row: "Publish and reconciliation.
Brother should record the boundary but not become Stibo/SAP/Dynamics
deployment machinery unless a narrow adapter is needed." Brother does not
implement a publish adapter and does not perform or simulate a publish
(the explicit non-goal, mirroring scripts/matcher_boundary.py's own framing:
"Brother does not become the matcher" -- here, Brother does not become the
publisher). This module only defines the PublishReconciliationRecord shape
for evidence a caller must supply AFTER a real publish, and
verify_reconciliation() checks that evidence for completeness and internal
consistency. It never talks to Stibo/SAP/Dynamics, never re-derives a count
from a live system, never asserts a publish happened -- it only audits the
evidence a caller hands it.

This is the check that runs downstream of scripts/merge_passport.py: a
merge passport's authority.publish field (read from a validated
golden-master-contract-v1 record's own publish_authority, e.g. "HUMAN")
is the gate that says a publish MAY happen. This module picks up AFTER
that gate, once a publish has actually been carried out, and records/
verifies what happened -- it never reads or re-checks the merge passport
itself, since authorizing a publish and reconciling one are two distinct
questions with two distinct evidence shapes (the same "distinct decisions,
do not conflate" discipline WBS-40.09 states for entity matching versus
attribute survivorship).

Per docs/decisions/evidence-vocabulary-2026-09-13.json's EV-3 ruling, the
PASS/FAIL/NO-DATA triple is imported from scripts/evidence_obligation.py,
not redeclared here.

Exactly seven checks, named by the roadmap row itself, each independently
PASS/FAIL/NO-DATA:
  counts                    records published vs. records attempted must
                             reconcile (attempted == published + rejected);
                             a mismatch is a real FAIL, never silently
                             accepted.
  rejected_entities          a LIST of what was rejected and why, so a
                             rejection is auditable, not just a number.
                             Cross-checked against counts.rejected when
                             both are supplied, so the two fields cannot
                             quietly disagree (the internal-consistency
                             requirement, not just per-field shape checks).
  downstream_reconciliation  structured PER CONSUMER, never one blanket
                             boolean: a named consumer that never confirmed
                             receipt is reported as its own NO-DATA entry
                             in the per-consumer breakdown, never omitted.
  schema_expectation          did the downstream schema match what was
                             published: PASS/FAIL/NO-DATA.
  orphan_references          references left pointing at something that no
                             longer resolves post-publish.
  duplication_recurrence      did a previously-resolved duplicate reappear:
                             a real regression signal for a golden-master
                             system, per the roadmap's own framing.
  publish_timestamp           required; verified to be a real parseable
                             timestamp, not a placeholder (a bare "TBD" or
                             similar is a FAIL, not a PASS).

Deviation, stated plainly (same discipline merge_passport.py and
journey_passport.py use for their own deviations): unlike those two
modules, this one has no sibling module in this repo that PRODUCES any of
this evidence (there is no publish adapter to call, by design -- see the
non-goal above), so there is nothing here to compose FROM the way
merge_passport.py calls reversibility_gate.evaluate_merge_candidate() or
journey_passport.py calls native_evidence_v2.wrap_v2(). Every field is
caller-supplied structured evidence, shape-checked and cross-checked the
way merge_passport.py's own not-yet-built siblings (candidate_generation,
risk, review, survivorship) are shape-checked: missing entirely is
NO-DATA, present but malformed or internally inconsistent is FAIL, never a
bare claim trusted at face value. Likewise, this module does not carry the
two passports' own hash/digest hollow-pass defense (there is no earlier
"composed at time T" record here for a later caller to re-verify against --
a reconciliation record is itself the first and only record of what a
given publish did), so no verify_passport_evidence-style digest replay is
built; a real digest layer can be added later without changing this
module's shape, exactly as merge_passport.py's own docstring notes for its
missing signer.

Mirrors journey_passport.py's own `completeness` design exactly, for the
same reason stated there: a single collapsed "0 Failed | 7 Passed" badge
reads as "basically fine" even when several checks are honestly NO-DATA.
This module carries NO single collapsed verdict/refused/status field.
`completeness` is a mandatory structured object with an explicit
denominator ("N of 7 checks"), named passed/failed/no_data lists, and a
`headline` string that can only read as fully-passing when every one of
the seven checks is genuinely PASS.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from evidence_obligation import VERDICTS  # noqa: E402

SCHEMA_VERSION = "publish-reconciliation-v1"

_PLACEHOLDER_TIMESTAMPS = {"tbd", "pending", "n/a", "na", "none", "null", "todo", "unknown", ""}


def _worst(verdicts):
    """Combine several VERDICTS into one: FAIL beats NO-DATA beats PASS.
    Mirrors merge_passport.py's and journey_passport.py's own _worst."""
    verdicts = list(verdicts)
    if not verdicts:
        return "NO-DATA"
    if "FAIL" in verdicts:
        return "FAIL"
    if "NO-DATA" in verdicts:
        return "NO-DATA"
    return "PASS"


def _check_counts(evidence):
    if not evidence:
        return {"attempted": None, "published": None, "rejected": None,
                "verdict": "NO-DATA", "verdict_reason": "no counts evidence supplied"}
    if not isinstance(evidence, dict):
        return {"attempted": None, "published": None, "rejected": None,
                "verdict": "FAIL", "verdict_reason": "counts evidence is not an object"}
    required = ("attempted", "published", "rejected")
    missing = [k for k in required if k not in evidence]
    if missing:
        return {"attempted": evidence.get("attempted"), "published": evidence.get("published"),
                "rejected": evidence.get("rejected"), "verdict": "FAIL",
                "verdict_reason": "counts evidence missing required field(s): %s" % ", ".join(missing)}
    attempted, published, rejected = evidence["attempted"], evidence["published"], evidence["rejected"]
    for name, value in (("attempted", attempted), ("published", published), ("rejected", rejected)):
        if isinstance(value, bool) or not isinstance(value, int):
            return {"attempted": attempted, "published": published, "rejected": rejected,
                    "verdict": "FAIL", "verdict_reason": "counts.%s %r is not an integer" % (name, value)}
    if attempted != published + rejected:
        return {"attempted": attempted, "published": published, "rejected": rejected,
                "verdict": "FAIL",
                "verdict_reason": "count mismatch: attempted (%d) != published (%d) + rejected (%d)" % (
                    attempted, published, rejected)}
    return {"attempted": attempted, "published": published, "rejected": rejected,
            "verdict": "PASS", "verdict_reason": "attempted reconciles to published + rejected"}


def _check_rejected_entities(evidence, counts_evidence):
    if evidence is None:
        return {"entities": [], "verdict": "NO-DATA", "verdict_reason": "no rejected_entities evidence supplied"}
    if not isinstance(evidence, list):
        return {"entities": [], "verdict": "FAIL", "verdict_reason": "rejected_entities evidence is not a list"}
    for i, entry in enumerate(evidence):
        if not isinstance(entry, dict) or "entity_id" not in entry or "reason" not in entry:
            return {"entities": evidence, "verdict": "FAIL",
                    "verdict_reason": "rejected_entities[%d] is missing entity_id or reason" % i}
    if isinstance(counts_evidence, dict):
        counted_rejected = counts_evidence.get("rejected")
        if isinstance(counted_rejected, int) and not isinstance(counted_rejected, bool):
            if len(evidence) != counted_rejected:
                return {"entities": evidence, "verdict": "FAIL",
                        "verdict_reason": "rejected_entities has %d entrie(s) but counts.rejected says %d" % (
                            len(evidence), counted_rejected)}
    return {"entities": evidence, "verdict": "PASS",
            "verdict_reason": "%d rejected entit%s, each with entity_id and reason" % (
                len(evidence), "y" if len(evidence) == 1 else "ies")}


def _check_downstream_reconciliation(evidence):
    if not evidence:
        return {"per_consumer": {}, "verdict": "NO-DATA",
                "verdict_reason": "no downstream reconciliation evidence supplied"}
    if not isinstance(evidence, dict):
        return {"per_consumer": {}, "verdict": "FAIL",
                "verdict_reason": "downstream reconciliation evidence is not an object of consumer -> receipt"}
    per_consumer = {}
    for consumer, receipt in evidence.items():
        if not isinstance(receipt, dict) or "confirmed" not in receipt:
            per_consumer[consumer] = {"verdict": "NO-DATA",
                                       "verdict_reason": "%s never confirmed receipt" % consumer}
            continue
        confirmed = receipt["confirmed"]
        if confirmed is True:
            per_consumer[consumer] = {"verdict": "PASS",
                                       "verdict_reason": "%s confirmed receipt" % consumer,
                                       "receipt_id": receipt.get("receipt_id", "NO-DATA")}
        elif confirmed is False:
            per_consumer[consumer] = {"verdict": "FAIL",
                                       "verdict_reason": "%s explicitly reported non-receipt" % consumer}
        else:
            per_consumer[consumer] = {"verdict": "FAIL",
                                       "verdict_reason": "%s.confirmed is not boolean: %r" % (consumer, confirmed)}
    verdict = _worst(v["verdict"] for v in per_consumer.values())
    return {"per_consumer": per_consumer, "verdict": verdict,
            "verdict_reason": "worst-of %d consumer(s): %s" % (
                len(per_consumer), {k: v["verdict"] for k, v in per_consumer.items()})}


def _check_schema_expectation(evidence):
    if not evidence:
        return {"expected": None, "observed": None, "verdict": "NO-DATA",
                "verdict_reason": "no schema_expectation evidence supplied"}
    if not isinstance(evidence, dict):
        return {"expected": None, "observed": None, "verdict": "FAIL",
                "verdict_reason": "schema_expectation evidence is not an object"}
    missing = [k for k in ("expected_schema", "observed_schema") if k not in evidence]
    if missing:
        return {"expected": evidence.get("expected_schema"), "observed": evidence.get("observed_schema"),
                "verdict": "FAIL",
                "verdict_reason": "schema_expectation evidence missing required field(s): %s" % ", ".join(missing)}
    expected, observed = evidence["expected_schema"], evidence["observed_schema"]
    if expected != observed:
        return {"expected": expected, "observed": observed, "verdict": "FAIL",
                "verdict_reason": "downstream schema %r does not match expected %r" % (observed, expected)}
    return {"expected": expected, "observed": observed, "verdict": "PASS",
            "verdict_reason": "downstream schema matches expected"}


def _check_orphan_references(evidence):
    if not evidence:
        return {"checked_count": 0, "orphans": [], "verdict": "NO-DATA",
                "verdict_reason": "no orphan_references evidence supplied"}
    if not isinstance(evidence, dict) or "checked_references" not in evidence or "orphans" not in evidence:
        return {"checked_count": 0, "orphans": [], "verdict": "FAIL",
                "verdict_reason": "orphan_references evidence missing checked_references or orphans"}
    checked = evidence["checked_references"]
    orphans = evidence["orphans"]
    if not isinstance(checked, list) or not isinstance(orphans, list):
        return {"checked_count": 0, "orphans": [], "verdict": "FAIL",
                "verdict_reason": "orphan_references.checked_references and orphans must both be lists"}
    for i, orphan in enumerate(orphans):
        if not isinstance(orphan, dict) or "reference_id" not in orphan or "points_to" not in orphan:
            return {"checked_count": len(checked), "orphans": orphans, "verdict": "FAIL",
                    "verdict_reason": "orphans[%d] is missing reference_id or points_to" % i}
    if orphans:
        return {"checked_count": len(checked), "orphans": orphans, "verdict": "FAIL",
                "verdict_reason": "%d orphan reference(s) found: %s" % (
                    len(orphans), [o["reference_id"] for o in orphans])}
    return {"checked_count": len(checked), "orphans": [], "verdict": "PASS",
            "verdict_reason": "%d reference(s) checked post-publish, none orphaned" % len(checked)}


def _check_duplication_recurrence(evidence):
    if not evidence:
        return {"previously_resolved": [], "recurred": [], "verdict": "NO-DATA",
                "verdict_reason": "no duplication_recurrence evidence supplied"}
    if (not isinstance(evidence, dict) or "previously_resolved_duplicate_ids" not in evidence
            or "recurred_ids" not in evidence):
        return {"previously_resolved": [], "recurred": [], "verdict": "FAIL",
                "verdict_reason": "duplication_recurrence evidence missing previously_resolved_duplicate_ids "
                                   "or recurred_ids"}
    previously_resolved = evidence["previously_resolved_duplicate_ids"]
    recurred = evidence["recurred_ids"]
    if not isinstance(previously_resolved, list) or not isinstance(recurred, list):
        return {"previously_resolved": [], "recurred": [], "verdict": "FAIL",
                "verdict_reason": "duplication_recurrence fields must both be lists"}
    if recurred:
        return {"previously_resolved": previously_resolved, "recurred": recurred, "verdict": "FAIL",
                "verdict_reason": "%d previously-resolved duplicate(s) recurred: %s" % (len(recurred), recurred)}
    return {"previously_resolved": previously_resolved, "recurred": [], "verdict": "PASS",
            "verdict_reason": "%d previously-resolved duplicate(s) checked, none recurred" % len(previously_resolved)}


def _check_publish_timestamp(value):
    if value is None:
        return {"timestamp": None, "parsed": None, "verdict": "NO-DATA",
                "verdict_reason": "no publish_timestamp supplied"}
    if not isinstance(value, str):
        return {"timestamp": value, "parsed": None, "verdict": "FAIL",
                "verdict_reason": "publish_timestamp %r is not a string" % (value,)}
    stripped = value.strip()
    if stripped.lower() in _PLACEHOLDER_TIMESTAMPS:
        return {"timestamp": value, "parsed": None, "verdict": "FAIL",
                "verdict_reason": "publish_timestamp %r is a placeholder, not a real timestamp" % value}
    normalized = stripped[:-1] + "+00:00" if stripped.endswith("Z") else stripped
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return {"timestamp": value, "parsed": None, "verdict": "FAIL",
                "verdict_reason": "publish_timestamp %r is not a parseable ISO 8601 timestamp" % value}
    return {"timestamp": value, "parsed": parsed.isoformat(), "verdict": "PASS",
            "verdict_reason": "publish_timestamp parses to a real timestamp"}


def verify_reconciliation(
    master_id,
    counts_evidence=None,
    rejected_entities_evidence=None,
    downstream_reconciliation_evidence=None,
    schema_expectation_evidence=None,
    orphan_references_evidence=None,
    duplication_recurrence_evidence=None,
    publish_timestamp=None,
    now=None,
):
    """Build a PublishReconciliationRecord by checking caller-supplied
    evidence for the seven named checks. Never talks to an external system,
    never asserts a publish happened -- purely an audit of the evidence
    handed to it. Returns a dict; see the module docstring for why there is
    deliberately no single collapsed verdict field (mirrors
    journey_passport.py's own completeness design)."""
    counts = _check_counts(counts_evidence)
    rejected_entities = _check_rejected_entities(rejected_entities_evidence, counts_evidence)
    downstream_reconciliation = _check_downstream_reconciliation(downstream_reconciliation_evidence)
    schema_expectation = _check_schema_expectation(schema_expectation_evidence)
    orphan_references = _check_orphan_references(orphan_references_evidence)
    duplication_recurrence = _check_duplication_recurrence(duplication_recurrence_evidence)
    publish_timestamp_check = _check_publish_timestamp(publish_timestamp)

    field_verdicts = {
        "counts": counts["verdict"],
        "rejected_entities": rejected_entities["verdict"],
        "downstream_reconciliation": downstream_reconciliation["verdict"],
        "schema_expectation": schema_expectation["verdict"],
        "orphan_references": orphan_references["verdict"],
        "duplication_recurrence": duplication_recurrence["verdict"],
        "publish_timestamp": publish_timestamp_check["verdict"],
    }
    for v in field_verdicts.values():
        if v not in VERDICTS:
            raise AssertionError("publish reconciliation field verdict %r outside the shared triple" % v)

    passed = sorted(n for n, v in field_verdicts.items() if v == "PASS")
    failed = sorted(n for n, v in field_verdicts.items() if v == "FAIL")
    no_data = sorted(n for n, v in field_verdicts.items() if v == "NO-DATA")
    total = len(field_verdicts)

    if failed:
        headline = ("BLOCKING FAILURE: %d of %d check(s) FAILED (%s); %d of %d check(s) NOT ASSESSED (%s)." % (
            len(failed), total, ", ".join(failed), len(no_data), total, ", ".join(no_data) if no_data else "none"))
    elif no_data:
        headline = ("INCOMPLETE: %d of %d check(s) NOT ASSESSED (%s); do not treat this reconciliation as clean." % (
            len(no_data), total, ", ".join(no_data)))
    else:
        headline = "ALL %d CHECK(S) ASSESSED: PASS." % total

    completeness = {
        "total_checks": total,
        "passed": passed,
        "failed": failed,
        "no_data": no_data,
        "assessed_count": len(passed) + len(failed),
        "no_data_count": len(no_data),
        "failed_count": len(failed),
        "headline": headline,
    }

    return {
        "schema_version": SCHEMA_VERSION,
        "master_id": master_id,
        "verified_at": (now or datetime.now(timezone.utc)).isoformat(),
        "counts": counts,
        "rejected_entities": rejected_entities,
        "downstream_reconciliation": downstream_reconciliation,
        "schema_expectation": schema_expectation,
        "orphan_references": orphan_references,
        "duplication_recurrence": duplication_recurrence,
        "publish_timestamp": publish_timestamp_check,
        "field_verdicts": field_verdicts,
        "completeness": completeness,
    }


def exit_code_for_completeness(completeness):
    """Mirrors journey_passport.py's own exit_code_for_completeness exactly:
    FAIL beats NO-DATA beats PASS at the process boundary too."""
    if completeness["failed_count"] > 0:
        return 1
    if completeness["no_data_count"] > 0:
        return 2
    return 0


def _load_json_or_none(path):
    if not path:
        return None
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--master-id", required=True)
    parser.add_argument("--counts", default=None, help="path to a JSON object {attempted, published, rejected}")
    parser.add_argument("--rejected-entities", default=None, help="path to a JSON list of {entity_id, reason}")
    parser.add_argument("--downstream-reconciliation", default=None,
                         help="path to a JSON object of consumer name -> {confirmed, receipt_id}")
    parser.add_argument("--schema-expectation", default=None,
                         help="path to a JSON object {expected_schema, observed_schema}")
    parser.add_argument("--orphan-references", default=None,
                         help="path to a JSON object {checked_references, orphans}")
    parser.add_argument("--duplication-recurrence", default=None,
                         help="path to a JSON object {previously_resolved_duplicate_ids, recurred_ids}")
    parser.add_argument("--publish-timestamp", default=None, help="the publish timestamp as a raw ISO 8601 string")
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)

    try:
        counts_evidence = _load_json_or_none(args.counts)
        rejected_entities_evidence = _load_json_or_none(args.rejected_entities)
        downstream_reconciliation_evidence = _load_json_or_none(args.downstream_reconciliation)
        schema_expectation_evidence = _load_json_or_none(args.schema_expectation)
        orphan_references_evidence = _load_json_or_none(args.orphan_references)
        duplication_recurrence_evidence = _load_json_or_none(args.duplication_recurrence)
    except FileNotFoundError as exc:
        print("NO-DATA: %s" % exc)
        return 2
    except json.JSONDecodeError as exc:
        print("NO-DATA: not valid JSON: %s" % exc)
        return 2

    record = verify_reconciliation(
        master_id=args.master_id,
        counts_evidence=counts_evidence,
        rejected_entities_evidence=rejected_entities_evidence,
        downstream_reconciliation_evidence=downstream_reconciliation_evidence,
        schema_expectation_evidence=schema_expectation_evidence,
        orphan_references_evidence=orphan_references_evidence,
        duplication_recurrence_evidence=duplication_recurrence_evidence,
        publish_timestamp=args.publish_timestamp,
    )

    text = json.dumps(record, indent=2, ensure_ascii=False, sort_keys=True)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
    else:
        print(text)
    print(record["completeness"]["headline"], file=sys.stderr)
    return exit_code_for_completeness(record["completeness"])


if __name__ == "__main__":
    sys.exit(main())
