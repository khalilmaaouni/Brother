#!/usr/bin/env python3
"""EPIC M3.01 Mobile Driver Contract: validate a driver advertisement record
against docs/schema/mobile-driver-contract-v1.json.

Reuses contract_check.validate for the same enforced JSON-schema keyword
subset the other mobile contracts already use (type, required, properties,
enum, const, items, minItems, additionalProperties) rather than a second
structural checker. Adds the hand rules that subset cannot express:

  - two-way coverage between supported_actions and risk_classes -- every
    supported action needs exactly one risk_classes entry, and every
    risk_classes entry must name a supported action.
  - driver_id, driver_name, action_vocabulary_ref, and every
    supported_actions entry must be non-empty after stripping whitespace
    (the enforced subset has no minLength).
  - platforms, device_modes, observations, and supported_actions must
    each be duplicate-free (the enforced subset has no uniqueItems).
  - a driver claiming visual_grounding_support must also list "screenshot"
    in observations (a driver cannot ground an action in a screenshot it
    never produces). Deliberately does NOT also require at least one of
    deterministic_selector_support/visual_grounding_support to be true
    for every driver: cross-checked against the real M3.03 native-iOS
    adapter (2026-09-15), a driver whose supported_actions are entirely
    non-target actions (OPEN_APP, CAPTURE, FINISH, ...) correctly carries
    both false, and mobile-driver-contract-v1 has no structural way to
    know which of a driver's supported_actions are target-bearing without
    coupling to one specific vocabulary's action set, which this module's
    own design (see action_vocabulary_ref below) deliberately avoids.
  - action_vocabulary_ref is cross-checked against a real vocabulary file
    ONLY WHEN one resolves, mirroring mobile_journey_contract.py's own
    resolve-the-ref pattern for outcome_contract_ref: if
    docs/schema/<action_vocabulary_ref>.json exists and defines an
    action enum, every supported_actions entry must be in it; if the file
    does not resolve, or resolves but defines no action enum, this check
    is silently skipped (NO-DATA), never a hard fail -- a valid driver
    contract must not start failing just because a sibling unit's
    vocabulary file (M3.02) has not landed in this tree.
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


def _non_empty_str_problems(record):
    """driver_id, driver_name, action_vocabulary_ref, and every
    supported_actions entry must be non-empty after stripping whitespace.
    Mirrors contract_check.py's own non-empty hand rule for its string
    fields; the enforced schema-keyword subset has no minLength."""
    problems = []
    for field in ("driver_id", "driver_name", "action_vocabulary_ref"):
        value = record.get(field)
        if isinstance(value, str) and not value.strip():
            problems.append(
                "%s: must not be empty after stripping whitespace" % field)

    actions = record.get("supported_actions")
    if isinstance(actions, list):
        for i, value in enumerate(actions):
            if isinstance(value, str) and not value.strip():
                problems.append(
                    "supported_actions[%d]: must not be empty after "
                    "stripping whitespace" % i)
    return problems


def _duplicate_entry_problems(record):
    """platforms, device_modes, observations, and supported_actions must
    each be duplicate-free: a duplicated entry inflates the router's
    breadth/count-based ranking without adding real breadth."""
    problems = []
    for field in ("platforms", "device_modes", "observations", "supported_actions"):
        value = record.get(field)
        if not isinstance(value, list):
            continue
        seen = set()
        reported = set()
        for entry in value:
            if not isinstance(entry, str):
                continue
            if entry in seen and entry not in reported:
                problems.append("%s: duplicate entry %r" % (field, entry))
                reported.add(entry)
            seen.add(entry)
    return problems


def _capability_observation_problems(record):
    """A driver claiming visual grounding must be able to produce the
    screenshot that grounding needs. (Does not also require at least one
    of deterministic_selector_support/visual_grounding_support to be true:
    see the module docstring for why that stricter rule was tried and
    dropped after it rejected a real, correct driver.)"""
    problems = []
    observations = _str_list(record.get("observations"))
    visual = record.get("visual_grounding_support")

    if visual is True and "screenshot" not in observations:
        problems.append(
            "visual_grounding_support: true but observations does not "
            "include 'screenshot'")
    return problems


def _vocabulary_path(ref):
    if os.path.isabs(ref):
        return ref
    return os.path.join(ROOT, "docs", "schema", "%s.json" % ref)


def _vocabulary_problems(record):
    """Cross-check supported_actions against a real vocabulary file, only
    when one resolves. Mirrors mobile_journey_contract.py's resolve-the-ref
    pattern: a missing or enum-less vocabulary file is NO-DATA, silently
    skipped, never a hard fail."""
    problems = []
    ref = record.get("action_vocabulary_ref")
    if not isinstance(ref, str) or not ref.strip():
        return problems  # already caught by the required/type check
    actions = _str_list(record.get("supported_actions"))
    if not actions:
        return problems

    try:
        vocab_schema = CC.load_json(_vocabulary_path(ref), "action_vocabulary_ref")
    except CC.NoData:
        return problems  # vocabulary file does not resolve: NO-DATA, pass

    enum = vocab_schema.get("properties", {}).get("action", {}).get("enum")
    if not isinstance(enum, list):
        return problems  # vocabulary file exists but defines no action enum

    enum_set = set(v for v in enum if isinstance(v, str))
    for action in actions:
        if action not in enum_set:
            problems.append(
                "supported_actions: %r is not a canonical action name in %s"
                % (action, ref))
    return problems


def hand_rules(record):
    """The rules the keyword subset cannot express, listed in the module
    docstring. Returns every problem found, never stopping at the first."""
    problems = []
    if not isinstance(record, dict):
        return problems

    problems.extend(_non_empty_str_problems(record))
    problems.extend(_duplicate_entry_problems(record))
    problems.extend(_capability_observation_problems(record))
    problems.extend(_vocabulary_problems(record))

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
