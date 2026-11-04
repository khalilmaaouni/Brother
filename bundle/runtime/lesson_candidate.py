#!/usr/bin/env python3
"""WBS-60.03 Vault convergence, Lesson candidate contract: validate a lesson
candidate record against docs/schema/lesson-candidate-v1.json.

Roadmap text (BROTHER_1.0.17_CONVERGENCE_ROADMAP_2026-09-14, WBS-60.03,
"Lesson candidate contract"):

    Every candidate includes:
    - observed symptom;
    - context;
    - mistaken action if any;
    - root cause;
    - correction;
    - evidence IDs;
    - affected domain;
    - generalizable Core lesson?
    - acceptance/reality status.

Mirrors claim_lifecycle.py / golden_master_contract.py's shape: reuses
contract_check.validate for the same enforced JSON-schema keyword subset
(type, required, properties, enum, const, additionalProperties) rather
than a second structural checker, plus one hand rule the schema's
keywords cannot express by themselves.

Two inferences this session made explicit (also documented in the schema
file itself, and named again here so a reader of either file finds the
same account): affected_domain is restricted to the WBS-60.01 domain-tag
closed set (core, mobile, mdm, brotherds); acceptance_status's three-value
vocabulary (PENDING, ACCEPTED, REJECTED) is not named anywhere in the
roadmap, only the concept "acceptance/reality status" is.

HAND RULE. "ACCEPTED evidence" is section 6.7's own phrase for when a
candidate may exist at all ("Create lesson candidates only when supported
by: accepted evidence; repeated failure; resolved BrotherDS outcome;
explicit human decision"), so a record whose acceptance_status is ACCEPTED
but whose evidence_ids is empty is refused here: an accepted candidate
naming no evidence contradicts the roadmap's own stated support
requirement. This does NOT implement the full promotion policy (which
allows non-evidence grounds too, such as an explicit human decision) --
that cross-record policy is WBS-60.04, scripts/vault_promotion_policy.py.
"""
import argparse
import os
import sys

import contract_check as CC

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_SCHEMA = os.path.join(ROOT, "docs", "schema", "lesson-candidate-v1.json")


def hand_rules(record):
    """The one rule the schema's keywords cannot express by themselves:
    an ACCEPTED candidate must name at least one evidence_id."""
    problems = []
    if record.get("acceptance_status") == "ACCEPTED" and not record.get("evidence_ids"):
        problems.append(
            "evidence_ids: an ACCEPTED candidate must name at least one "
            "evidence ID; the roadmap's own support requirement (section "
            "6.7) names 'accepted evidence' as one of the conditions a "
            "candidate needs before promotion is even considered")
    return problems


def check(record, schema):
    problems = []
    CC.validate(record, schema, "", problems)
    problems.extend(hand_rules(record))
    seen, out = set(), []
    for p in problems:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


BASE = {
    "schema_version": "lesson-candidate-v1",
    "candidate_id": "lc-selftest-1",
    "observed_symptom": "a check reported clean while the underlying script was broken",
    "context": "release battery run",
    "mistaken_action": "trusted the cached PASS without re-running the check",
    "root_cause": "the battery reused a stale result bundle instead of re-executing",
    "correction": "invalidate cached evidence when its declared dependency changes",
    "evidence_ids": ["ev-001"],
    "affected_domain": "core",
    "generalizable_core_lesson": True,
    "acceptance_status": "PENDING",
}


def run_selftest():
    failures = []
    schema = CC.load_json(DEFAULT_SCHEMA, "schema")

    def rec(**overrides):
        r = dict(BASE)
        r.update(overrides)
        return r

    def expect(name, record, want_ok):
        problems = check(record, schema)
        got_ok = not problems
        if got_ok != want_ok:
            failures.append("%s: expected ok=%r, got problems=%r"
                             % (name, want_ok, problems))

    expect("a fully-formed PENDING candidate passes", rec(), True)
    expect("mistaken_action may be null (wisdom, not a mistake)",
           rec(mistaken_action=None), True)
    expect("empty evidence_ids is fine while PENDING",
           rec(evidence_ids=[]), True)
    expect("ACCEPTED with evidence passes",
           rec(acceptance_status="ACCEPTED"), True)
    expect("ACCEPTED with no evidence is refused",
           rec(acceptance_status="ACCEPTED", evidence_ids=[]), False)
    expect("unknown affected_domain is refused",
           rec(affected_domain="ios"), False)
    expect("unknown acceptance_status is refused",
           rec(acceptance_status="MAYBE"), False)
    expect("missing required field is refused",
           {k: v for k, v in rec().items() if k != "root_cause"}, False)
    expect("additional property is refused",
           rec(**{"not_a_real_field": "x"}), False)

    for msg in failures:
        print("selftest:", msg)
    return not failures


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("record", nargs="?", help="path to a lesson-candidate-v1 JSON record")
    ap.add_argument("--schema", default=DEFAULT_SCHEMA)
    ap.add_argument("--selftest", action="store_true",
                     help="run the built-in self check and exit")
    args = ap.parse_args(argv)

    if args.selftest:
        if run_selftest():
            print("lesson_candidate.py selftest: PASS")
            return 0
        print("lesson_candidate.py selftest: FAIL", file=sys.stderr)
        return 1

    if not args.record:
        ap.error("record is required unless --selftest")

    try:
        record = CC.load_json(args.record, "lesson candidate record")
        schema = CC.load_json(args.schema, "lesson candidate schema")
    except CC.NoData as exc:
        print("NO-DATA: %s" % exc)
        return 2
    problems = check(record, schema)
    if problems:
        print("FAIL: %d problem(s)" % len(problems))
        for p in problems:
            print(" -", p)
        return 1
    print("PASS: %s validates as lesson-candidate-v1" % args.record)
    return 0


if __name__ == "__main__":
    sys.exit(main())
