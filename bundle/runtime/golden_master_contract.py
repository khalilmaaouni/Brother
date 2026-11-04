#!/usr/bin/env python3
"""WBS-40.01 Golden Master Contract: validate a golden-master record against
docs/schema/golden-master-contract-v1.json.

Reuses contract_check.validate for the same enforced JSON-schema keyword
subset outcome-contract-v1 and mobile-journey-contract-v1 already use (type,
required, properties, enum, const, items, minItems, additionalProperties)
rather than a second structural checker. Adds one hand rule the keyword
subset cannot express: outcome_contract_ref must resolve to a real file that
itself validates as outcome-contract-v1 -- "layered on the shared Outcome
Contract, never a second lifecycle" is not just a docstring sentence, it is
enforced here.
"""
import argparse
import json
import os
import sys

import contract_check as CC

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_SCHEMA = os.path.join(ROOT, "docs", "schema", "golden-master-contract-v1.json")


def hand_rules(record):
    """The one rule the keyword subset cannot express: layered-on-the-
    shared-contract is enforced, not just documented."""
    problems = []
    ref = record.get("outcome_contract_ref")
    if not isinstance(ref, str) or not ref.strip():
        return problems  # already caught by the required/type check
    path = ref if os.path.isabs(ref) else os.path.join(ROOT, ref)
    try:
        outcome = CC.load_json(path, "outcome_contract_ref")
    except CC.NoData as exc:
        problems.append("outcome_contract_ref: %s" % exc)
        return problems
    if outcome.get("schema_version") != "outcome-contract-v1":
        problems.append(
            "outcome_contract_ref: %s does not validate as outcome-contract-v1 "
            "(schema_version is %r)" % (ref, outcome.get("schema_version")))
        return problems
    outcome_schema_path = CC.default_schema()
    try:
        outcome_schema = CC.load_json(outcome_schema_path, "outcome-contract-v1 schema")
    except CC.NoData as exc:
        problems.append("outcome_contract_ref: could not load its own schema to check it: %s" % exc)
        return problems
    outcome_problems = CC.check(outcome, outcome_schema)
    if outcome_problems:
        problems.append(
            "outcome_contract_ref: %s fails its own outcome-contract-v1 validation "
            "(%d problem(s), first: %s)" % (ref, len(outcome_problems), outcome_problems[0]))
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


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("record", help="path to the golden-master-contract JSON record")
    ap.add_argument("--schema", default=DEFAULT_SCHEMA)
    args = ap.parse_args(argv)
    try:
        record = CC.load_json(args.record, "golden master contract record")
        schema = CC.load_json(args.schema, "golden master contract schema")
    except CC.NoData as exc:
        print("NO-DATA: %s" % exc)
        return 2
    problems = check(record, schema)
    if problems:
        print("FAIL: %d problem(s)" % len(problems))
        for p in problems:
            print(" -", p)
        return 1
    print("PASS: %s validates as golden-master-contract-v1" % args.record)
    return 0


if __name__ == "__main__":
    sys.exit(main())
