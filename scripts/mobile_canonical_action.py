#!/usr/bin/env python3
"""EPIC M3.02 Mobile Canonical Action: validate an action record against
docs/schema/mobile-canonical-action-v1.json.

Reuses contract_check.validate for the same enforced JSON-schema keyword
subset outcome-contract-v1 and mobile-journey-contract-v1 already use (type,
required, properties, enum, const, items, minItems, additionalProperties)
rather than a second structural checker. Adds one thing that subset cannot
express: this vocabulary is a UNION of 20 action shapes (OPEN_APP needs
params.app_id, TAP_TARGET needs a target, and so on) and the subset has no
oneOf keyword to pin a shape to a specific enum value. hand_rules() below is
the per-action shape check, documented in the schema's own description so a
reader of either file finds the same account.

This module defines vocabulary and validation only. It builds no driver
adapter, no router, and does not talk to any device or tool -- that is
M3.03+'s scope, not this unit's.
"""
import argparse
import math
import os
import re
import sys

import contract_check as CC

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_SCHEMA = os.path.join(ROOT, "docs", "schema", "mobile-canonical-action-v1.json")

#: One entry per action name in the schema's own enum. Each entry says what
#: is required beyond the base envelope: "target" (the target object must be
#: present), a list of dotted params keys (e.g. "params.text"), or "reason"
#: (the top-level reason field). "any_of" means at least one of the named
#: requirements must be satisfied rather than all of them (WAIT_FOR only).
ACTION_RULES = {
    "OPEN_APP": {"params": ["app_id"]},
    "TAP_TARGET": {"target": True},
    "LONG_PRESS_TARGET": {"target": True},
    "TYPE_TEXT": {"params": ["text"]},
    "SWIPE": {"params": ["direction"]},
    "SCROLL_TO": {"target": True},
    "BACK": {},
    "HOME": {},
    "ROTATE": {"params": ["orientation"]},
    "SET_PERMISSION": {"params": ["permission", "state"]},
    "SET_NETWORK": {"params": ["condition"]},
    "SET_LOCATION": {"params": ["latitude", "longitude"]},
    "DEEPLINK": {"params": ["uri"]},
    "WAIT_FOR": {"any_of": [{"target": True}, {"params": ["duration_ms"]}]},
    "ASSERT_VISIBLE": {"target": True},
    "ASSERT_STATE": {"params": ["key", "expected"]},
    "CAPTURE": {},
    "FINISH": {"params": ["status"]},
    "NEED_HUMAN": {"reason": True},
    "IMPOSSIBLE": {"reason": True},
}

#: Enum-constrained params values the keyword subset also cannot pin per
#: action (params is additionalProperties: true throughout). Checked only
#: when the key is actually present, so a missing key still reports through
#: the "required" problem above rather than a confusing enum problem.
PARAMS_ENUM = {
    "SWIPE": {"direction": ["up", "down", "left", "right"]},
    "ROTATE": {"orientation": [
        "portrait", "landscape", "portrait_upside_down",
        "landscape_left", "landscape_right"]},
    "SET_PERMISSION": {"state": ["allow", "deny", "ask"]},
    "SET_NETWORK": {"condition": [
        "online", "offline", "wifi_only", "cellular_only", "slow"]},
    "FINISH": {"status": ["success", "failure"]},
}


def _is_blank(value):
    """None, or a string that is empty/whitespace-only. A non-string value
    (a number, bool, dict) is never blank by this check -- 0, False and ""
    are meaningfully different values a caller may legitimately send, so
    only the string-emptiness case (the one real bug: "   " previously
    slipped past an exact `== ""` check) is treated as missing."""
    return value is None or (isinstance(value, str) and not value.strip())


def _missing(record, requirement):
    """True when `requirement` ({"target": True} / {"params": [...]} /
    {"reason": True}) is NOT satisfied by record. Used directly for a
    single requirement and per-branch for WAIT_FOR's any_of."""
    if requirement.get("target"):
        target = record.get("target")
        if (not isinstance(target, dict)
                or _is_blank(target.get("selector_type"))
                or _is_blank(target.get("value"))):
            return True
    if requirement.get("reason"):
        if _is_blank(record.get("reason")):
            return True
    params = record.get("params")
    for key in requirement.get("params", []):
        if not isinstance(params, dict) or _is_blank(params.get(key)):
            return True
    return False


#: Ceiling for a single WAIT_FOR's params.duration_ms. A floor with no
#: ceiling let duration_ms=604800000 (7 days) validate clean, and an adapter
#: genuinely slept the raw value. Deliberately a generous sanity bound
#: rather than any one backend's timing: the schema states that this
#: vocabulary must not encode one execution backend's timing assumptions, so
#: an adapter still applies its own, tighter execution deadline on top (see
#: mobile_native_ios_adapter.DEFAULT_WAIT_TIMEOUT_MS).
WAIT_FOR_MAX_DURATION_MS = 3600000

#: Ceiling for LONG_PRESS_TARGET's params.duration_ms, a param an adapter
#: invented rather than one this vocabulary ever bounded: duration_ms=
#: 86400000 validated clean and would hold a touch down on a real device for
#: 24 hours, and a negative value passed just as silently. Bounded here, at
#: the vocabulary, so every adapter implementing LONG_PRESS_TARGET inherits
#: it rather than each handler re-deriving its own cap.
LONG_PRESS_MAX_DURATION_MS = 60000

#: (min, max) range for params keys that must be a real number (not bool,
#: since Python's bool is an int subclass) once present. Checked only when
#: the key is present -- a missing key is already caught by ACTION_RULES.
PARAMS_NUMBER_RANGE = {
    "SET_LOCATION": {"latitude": (-90, 90), "longitude": (-180, 180)},
    "WAIT_FOR": {"duration_ms": (1, WAIT_FOR_MAX_DURATION_MS)},
    "LONG_PRESS_TARGET": {"duration_ms": (1, LONG_PRESS_MAX_DURATION_MS)},
}

#: params keys that must be a real string once present (not a number, bool,
#: list, or dict) -- params is additionalProperties: true throughout, so the
#: keyword subset never checks these; PARAMS_ENUM already type-guards its own
#: keys (a non-string enum value is never in the allowed string list), this
#: covers the remaining free-text keys the schema's per-action shape names.
#: ASSERT_STATE.expected is deliberately excluded: asserting a numeric or
#: boolean state value is legitimate, not a bug.
PARAMS_STRING = {
    "OPEN_APP": ["app_id"],
    "TYPE_TEXT": ["text"],
    "DEEPLINK": ["uri"],
    "SET_PERMISSION": ["permission"],
    "ASSERT_STATE": ["key"],
}

#: (min, max) range for top-level numeric fields every action shares (not
#: nested under params like PARAMS_NUMBER_RANGE, since timeout_ms sits at
#: the record's own top level per the schema).
TOP_LEVEL_NUMBER_RANGE = {
    "timeout_ms": (1, None),
}


def _check_number_range(container, key, lo, hi, path):
    """Appends a problem string to the caller's list when container[key] is
    not a real number in [lo, hi] (None means unbounded on that side).
    Shared by the params-level and top-level-field range checks below."""
    value = container[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return ["%s: must be a number, got %r" % (path, value)]
    if not math.isfinite(value):
        # NaN slipped through every range check below because BOTH
        # `value < lo` and `value > hi` are vacuously False for NaN, so a
        # nan latitude validated clean and reached a real driver call, and
        # json.dumps then emitted a non-RFC-8259 `NaN` token that breaks
        # any strict JSON reader. Infinity was only caught by accident,
        # where a range happened to have an upper bound (inf > 90); it
        # still passed clean for any key whose ceiling was None. isfinite
        # closes both, for every key and every adapter, in one place.
        return ["%s: must be a finite number, got %r" % (path, value)]
    if (lo is not None and value < lo) or (hi is not None and value > hi):
        return ["%s: must be between %r and %r, got %r" % (path, lo, hi, value)]
    return []


#: value must look like "number,number" when target.selector_type is
#: coordinates -- the one selector_type whose value has an actual shape,
#: not just any non-blank string (the keyword subset has no `pattern`).
_COORDINATES_RE = re.compile(r"^\s*-?\d+(\.\d+)?\s*,\s*-?\d+(\.\d+)?\s*$")


def hand_rules(record):
    """The one rule the keyword subset cannot express: each action names a
    different required shape (a union the subset has no oneOf to check).
    Plus a handful of value checks the subset also cannot express (a
    number's range, a bool masquerading as an int, a coordinates string's
    shape) -- each caught by adversarial review of an earlier draft."""
    problems = []
    if not isinstance(record, dict):
        # A non-dict record (a JSON list, string, number or null) reaches
        # here whenever a caller loads a malformed record file. Every line
        # below assumes a mapping, so `record.get` previously raised an
        # uncaught AttributeError, which surfaced as a raw traceback
        # indistinguishable from a legitimate failure exit. Returning no
        # problems here is safe and is NOT a silent pass: check() runs
        # CC.validate first, which already reports the type honestly
        # ("record: must be of type 'object', got list"), so a non-dict
        # still FAILs with a real reason. Mirrors the same first line in
        # mobile_driver_contract.hand_rules(), this module's sister
        # validator, rather than adding a different shape of guard.
        return problems
    action = record.get("action")
    rule = ACTION_RULES.get(action)
    if rule is None:
        return problems  # unknown action already caught by the enum check

    if "any_of" in rule:
        if all(_missing(record, branch) for branch in rule["any_of"]):
            problems.append(
                "%s: requires at least one of target or params.duration_ms" % action)
    elif _missing(record, rule):
        parts = []
        if rule.get("target"):
            parts.append("target")
        if rule.get("reason"):
            parts.append("reason")
        for key in rule.get("params", []):
            parts.append("params.%s" % key)
        problems.append("%s: requires %s" % (action, ", ".join(parts)))

    if _is_blank(record.get("action_id")):
        problems.append("action_id: must not be empty or whitespace-only")

    target = record.get("target")
    if (isinstance(target, dict) and target.get("selector_type") == "coordinates"
            and isinstance(target.get("value"), str)
            and not _COORDINATES_RE.match(target["value"])):
        problems.append(
            "target.value: coordinates selector requires 'number,number', got %r"
            % target["value"])

    params = record.get("params")
    if isinstance(params, dict):
        for key, allowed in PARAMS_ENUM.get(action, {}).items():
            if key in params and params[key] not in allowed:
                problems.append(
                    "params.%s: must be one of %r, got %r"
                    % (key, allowed, params[key]))
        for key, (lo, hi) in PARAMS_NUMBER_RANGE.get(action, {}).items():
            if key in params:
                problems.extend(
                    _check_number_range(params, key, lo, hi, "params.%s" % key))
        for key in PARAMS_STRING.get(action, []):
            if key in params and not isinstance(params[key], str):
                problems.append(
                    "params.%s: must be a string, got %r" % (key, params[key]))

    for key, (lo, hi) in TOP_LEVEL_NUMBER_RANGE.items():
        if key in record:
            problems.extend(_check_number_range(record, key, lo, hi, key))

    return problems


def check(record, schema):
    problems = []
    CC.validate(record, schema, "", problems)
    if not isinstance(record, dict):
        # CC.validate already appended a type problem above; hand_rules()
        # below does record.get(...) and would crash on a list/string/None
        # body instead of returning that clean problem list.
        return problems
    problems.extend(hand_rules(record))
    seen, out = set(), []
    for p in problems:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("record", help="path to the mobile-canonical-action JSON record")
    ap.add_argument("--schema", default=DEFAULT_SCHEMA)
    args = ap.parse_args(argv)
    try:
        record = CC.load_json(args.record, "canonical action record")
        schema = CC.load_json(args.schema, "canonical action schema")
    except CC.NoData as exc:
        print("NO-DATA: %s" % exc)
        return 2
    problems = check(record, schema)
    if problems:
        print("FAIL: %d problem(s)" % len(problems))
        for p in problems:
            print(" -", p)
        return 1
    print("PASS: %s validates as mobile-canonical-action-v1" % args.record)
    return 0


if __name__ == "__main__":
    sys.exit(main())
