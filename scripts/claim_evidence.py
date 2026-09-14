#!/usr/bin/env python3
"""WBS-50.02 BrotherDS Claim Contract v2, Claim Evidence references: the
roadmap's own line is "BrotherDS should consume Evidence v2 IDs/references
rather than copy every raw artifact into claim JSON... A claim still
remains portable enough to explain itself."

No single settled "Evidence v2 ID" format exists yet. docs/plan/1.0.17/
EVIDENCE-V2-DESIGN.md (WBS-20.01) was retracted after an Opus adversarial
review found three competing evidence vocabularies already live in this
repo -- receipt_attest.py's in-toto predicate, receipt_door.py's
verified/no-data state, and the outcome-contract's plain receipts[] --
with no Fable/Opus ruling yet on which one wins. Rather than invent a
fourth vocabulary this unit reuses the one reference grammar that already
exists and is already enforced end to end: docs/schema/
outcome-contract-v1.json's receipts[].ref grammar (file:/evidence:/url:),
enforced by contract_check.py's RECEIPT_REF_PREFIXES (imported here, not
redeclared) and resolved by scripts/receipt_check.py. This is the explicit
inference this unit makes in place of a settled "Evidence v2 ID": a
BrotherDS claim's evidence field is a list of these references plus a
short human note per entry, never a raw artifact copy.

Mirrors claim_lifecycle.py's shape: a schema-backed record checker
(contract_check.validate against docs/schema/claim-evidence-v1.json) plus
hand rules for what the enforced keyword subset cannot express (no
pattern, no minLength):

  REF GRAMMAR. Every evidence[].ref must start with one of
  contract_check.RECEIPT_REF_PREFIXES, the same grammar F4 already
  enforces for outcome-contract-v1's receipts[].ref.

  NON-EMPTY NOTE. Every evidence[].note must be non-empty: "portable
  enough to explain itself" needs an actual explanation, not "".

  UNIQUE IDS. No two entries in one record may share an id.

check_matches_claim(evidence_record, claim_record) is the one cross-record
rule: an evidence-reference record documents one exact claim revision, so
its claim_id and revision_id must match the claim-lifecycle-v1 record they
were assembled for (a correction gets its own revision_id per WBS-50.01,
so it gets its own evidence record too, never a silent edit of this one).

PASS/FAIL/NO-DATA verdicts are imported from evidence_obligation.py, the
one definition site (matching claim_lifecycle.py's own import), never
redeclared here.
"""
import argparse
import os
import sys

import contract_check as CC
import evidence_obligation

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_SCHEMA = os.path.join(ROOT, "docs", "schema", "claim-evidence-v1.json")

VERDICTS = evidence_obligation.VERDICTS  # ("PASS", "FAIL", "NO-DATA")

#: Reused, never redeclared: the same grammar contract_check.py's RECEIPT
#: REF GRAMMAR (F4) rule already enforces for outcome-contract-v1.
RECEIPT_REF_PREFIXES = CC.RECEIPT_REF_PREFIXES


def hand_rules(record):
    """The rules a single record's schema keywords cannot express: ref
    grammar, non-empty note, unique evidence ids. Returns a list of
    problems, empty when the record satisfies all three."""
    problems = []
    evidence = record.get("evidence")
    if not isinstance(evidence, list):
        return problems  # structural check already reported this
    seen_ids = set()
    for i, item in enumerate(evidence):
        if not isinstance(item, dict):
            continue  # structural check already reported this
        prefix = "evidence[%d]" % i
        ref = item.get("ref")
        if isinstance(ref, str) and not ref.startswith(RECEIPT_REF_PREFIXES):
            problems.append(
                "%s.ref: must start with one of %r, got %r"
                % (prefix, RECEIPT_REF_PREFIXES, ref))
        note = item.get("note")
        if isinstance(note, str) and not note.strip():
            problems.append(
                "%s.note: must be non-empty -- a claim stays portable "
                "enough to explain itself only when the explanation is "
                "actually there" % prefix)
        item_id = item.get("id")
        if isinstance(item_id, str):
            if item_id in seen_ids:
                problems.append(
                    "%s.id: %r is not unique within this record"
                    % (prefix, item_id))
            seen_ids.add(item_id)
    return problems


def check(record, schema):
    """Structural validation against claim-evidence-v1, plus the hand
    rules a single record can carry. check_matches_claim compares two
    records and is called directly, not from here."""
    problems = []
    CC.validate(record, schema, "", problems)
    problems.extend(hand_rules(record))
    seen, out = set(), []
    for p in problems:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def check_matches_claim(evidence_record, claim_record):
    """An evidence-reference record documents one exact claim revision: its
    claim_id and revision_id must match the claim-lifecycle-v1 record it
    was assembled for. Returns a list of problems, empty when they match."""
    problems = []
    if evidence_record.get("claim_id") != claim_record.get("claim_id"):
        problems.append(
            "claim_id: evidence record names %r, claim record is %r"
            % (evidence_record.get("claim_id"), claim_record.get("claim_id")))
    if evidence_record.get("revision_id") != claim_record.get("revision_id"):
        problems.append(
            "revision_id: evidence record names %r, claim record is %r "
            "(a corrected revision needs its own evidence record, never a "
            "silent edit of this one)"
            % (evidence_record.get("revision_id"), claim_record.get("revision_id")))
    return problems


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("record", help="path to a claim-evidence-v1 JSON record")
    ap.add_argument("--schema", default=DEFAULT_SCHEMA)
    ap.add_argument("--claim-record",
                     help="optional path to the claim-lifecycle-v1 JSON "
                          "record this evidence documents; when given, "
                          "check_matches_claim also runs")
    args = ap.parse_args(argv)
    try:
        record = CC.load_json(args.record, "claim evidence record")
        schema = CC.load_json(args.schema, "claim evidence schema")
        claim_record = None
        if args.claim_record:
            claim_record = CC.load_json(args.claim_record, "claim lifecycle record")
    except CC.NoData as exc:
        print("%s: %s" % (VERDICTS[2], exc))
        return 2
    problems = check(record, schema)
    if claim_record is not None:
        problems.extend(check_matches_claim(record, claim_record))
    if problems:
        print("%s: %d problem(s)" % (VERDICTS[1], len(problems)))
        for p in problems:
            print(" -", p)
        return 1
    print("%s: %s validates as claim-evidence-v1" % (VERDICTS[0], args.record))
    return 0


if __name__ == "__main__":
    sys.exit(main())
