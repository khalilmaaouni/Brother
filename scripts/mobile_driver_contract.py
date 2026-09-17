#!/usr/bin/env python3
"""EPIC M3.01 Mobile Driver Contract: validate a driver advertisement record
against docs/schema/mobile-driver-contract-v1.json.

Reuses contract_check.validate for the same enforced JSON-schema keyword
subset the other mobile contracts already use (type, required, properties,
enum, const, items, minItems, additionalProperties) rather than a second
structural checker. Adds one hand rule the keyword subset cannot express:
two-way coverage between supported_actions and risk_classes -- every
supported action needs exactly one risk_classes entry, and every
risk_classes entry must name a supported action.

Deliberately does NOT cross-check action_vocabulary_ref against a real file
the way mobile_journey_contract.py checks outcome_contract_ref: that
vocabulary (M3.02, a sibling unit tonight) may not have landed yet, and a
valid driver contract must not start failing just because a sibling unit's
file does not exist. The ref is a name/version pointer only.
"""
import argparse
import os
import sys

import contract_check as CC

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_SCHEMA = os.path.join(ROOT, "docs", "schema", "mobile-driver-contract-v1.json")


def _str_list(value):
    if not isinstance(value, list):
        return []
    return [v for v in value if isinstance(v, str)]


def hand_rules(record):
    """The one rule the keyword subset cannot express: risk_classes must
    cover supported_actions exactly, both directions."""
    problems = []
    if not isinstance(record, dict):
        return problems

    actions = _str_list(record.get("supported_actions"))
    entries = record.get("risk_classes")
    entries = entries if isinstance(entries, list) else []

    counts = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        action = entry.get("action")
        if not isinstance(action, str):
            continue
        counts[action] = counts.get(action, 0) + 1

    for action in actions:
        n = counts.get(action, 0)
        if n == 0:
            problems.append(
                "risk_classes: supported action %r has no risk_classes entry" % action)
        elif n > 1:
            problems.append(
                "risk_classes: supported action %r has %d risk_classes entries, want 1"
                % (action, n))

    for action in sorted(counts):
        if action not in actions:
            problems.append(
                "risk_classes: action %r is not in supported_actions" % action)

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
    ap.add_argument("record", help="path to the mobile-driver-contract JSON record")
    ap.add_argument("--schema", default=DEFAULT_SCHEMA)
    args = ap.parse_args(argv)
    try:
        record = CC.load_json(args.record, "driver contract record")
        schema = CC.load_json(args.schema, "driver contract schema")
    except CC.NoData as exc:
        print("NO-DATA: %s" % exc)
        return 2
    problems = check(record, schema)
    if problems:
        print("FAIL: %d problem(s)" % len(problems))
        for p in problems:
            print(" -", p)
        return 1
    print("PASS: %s validates as mobile-driver-contract-v1" % args.record)
    return 0


if __name__ == "__main__":
    sys.exit(main())
