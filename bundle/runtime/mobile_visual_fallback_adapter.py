#!/usr/bin/env python3
"""EPIC M3.06 Visual Fallback Adapter: given a screenshot, an optional
accessibility/semantic tree, and a target description, propose a grounded
on-screen target plus a confidence score plus an evidence reference, for
whichever of M3.02's canonical actions carry a `target` (TAP_TARGET,
LONG_PRESS_TARGET, SCROLL_TO, ASSERT_VISIBLE, WAIT_FOR when it carries a
target rather than only duration_ms -- see TARGET_BEARING_ACTIONS below,
derived from mobile_canonical_action.ACTION_RULES rather than a second
hand-typed list).

Model-neutral: this module names no specific vision model and makes no
network call. It defines the real dispatch contract -- ground_target() and
the _call_vision_model() seam a future unit wires up -- but that seam always
returns None tonight, per M3.06's own scope: `grep -rln "vision"
scripts/*.py` (excluding "revision"/"provision"/"division"/"envision") found
nothing else in this repo to call into, so there is no existing vision
capability to reuse, and adding a live OpenRouter vision call is explicitly
out of scope for this unit. Every call therefore returns status
NOT_ATTEMPTED, confidence null, target null -- never a guessed target or a
confidence defaulted to a plausible-looking number.

CRITICAL CONSTRAINT (source: docs/plan/MOBILE-EPIC-M3-UNITS.md's M3.06 row):
"A visual action must never self-promote into a stable selector without
going through crystallization." Enforced structurally, not by convention:
  1. Every record this module produces carries stability="visual_only",
     and docs/schema/mobile-visual-grounding-v1.json pins that field to
     that one const value -- no record that validates against the schema
     can claim a different stability.
  2. This module never writes a file. main() only prints JSON to stdout;
     execute_action()/ground_target() take a screenshot path to read and
     return a dict, nothing more. There is therefore no code path here that
     could persist a target as a stable selector -- that is EPIC M6's job
     (crystallization and selector governance, docs/plan/
     MOBILE-PLATFORM-ROADMAP-1.0.18.md), a separate, not-yet-built unit.
  3. hand_rules() below refuses any record where confidence or target is
     set but status is not GROUNDED/LOW_CONFIDENCE, and refuses any
     GROUNDED/LOW_CONFIDENCE record whose confidence is missing or out of
     [0.0, 1.0] -- so a future model integration cannot silently default a
     high confidence in place of real uncertainty without failing this
     check.
test_mobile_visual_fallback_adapter.py proves all three directly, including
a source-level check that this module contains no file-write call at all.
"""
import argparse
import json
import math
import sys
from pathlib import Path

import contract_check as CC
import mobile_canonical_action as ACT
import mobile_driver_contract as DC
import mobile_workflow as MW

DRIVER_ID = "visual-fallback"
DRIVER_NAME = "Visual fallback adapter (screenshot to grounded target)"
ACTION_VOCABULARY_REF = "mobile-canonical-action-v1"

ROOT = str(Path(__file__).resolve().parent.parent)
DEFAULT_SCHEMA = str(Path(ROOT) / "docs" / "schema" / "mobile-visual-grounding-v1.json")

# Loaded once at import, same reasoning as mobile_native_ios_adapter.py:
# fixed repo files, and a caller may drive many actions in a loop.
_ACTION_SCHEMA = ACT.CC.load_json(ACT.DEFAULT_SCHEMA, "canonical action schema")
_DRIVER_SCHEMA = DC.CC.load_json(DC.DEFAULT_SCHEMA, "driver contract schema")
_GROUNDING_SCHEMA = CC.load_json(DEFAULT_SCHEMA, "visual grounding schema")

#: Every canonical action whose hand rule (mobile_canonical_action.
#: ACTION_RULES) requires or allows a `target` -- derived from that one
#: source of truth rather than a second hand-typed list, so this stays in
#: lockstep if M3.02's vocabulary ever changes which actions carry a
#: target. WAIT_FOR is included (its any_of allows a target) even though a
#: valid WAIT_FOR record may instead carry only params.duration_ms and
#: have no target at all -- execute_action() handles that case explicitly.
TARGET_BEARING_ACTIONS = sorted(
    action for action, rule in ACT.ACTION_RULES.items()
    if rule.get("target") or any(branch.get("target") for branch in rule.get("any_of", ())))

_STATUSES_REQUIRING_GROUNDING = ("GROUNDED", "LOW_CONFIDENCE")
_STATUSES_FORBIDDING_GROUNDING = ("NOT_ATTEMPTED", "UNSUPPORTED", "FAIL")


def _call_vision_model(screenshot_path, target_description, semantic_tree_path, model):
    """The one seam a future unit wires a real vision-model call into.
    Returns None always, tonight: no live model call is in scope for
    M3.06 (see module docstring). A future implementation returns
    {"target": {...}, "confidence": <0..1>, "model_call": {...}} instead of
    None; ground_target() below already handles that shape via
    classify_confidence(), so wiring a real model needs no change to the
    dispatch contract, only to this one function."""
    return None


def classify_confidence(confidence, threshold):
    """Pure threshold decision: GROUNDED at or above `threshold`,
    LOW_CONFIDENCE below it, NOT_ATTEMPTED when there is no confidence to
    classify. Proven independently of any live model (no call produces a
    real confidence value in this repo tonight -- see _call_vision_model).

    `threshold` has no module-level default. Which number is right is
    flagged in M3.06's own unit definition as needing real product/design
    judgment (docs/plan/MOBILE-EPIC-M3-UNITS.md: "flag genuine ambiguity
    rather than guessing a threshold") -- so this function refuses to guess
    one; a caller must supply it explicitly wherever a real confidence
    value needs classifying."""
    if confidence is None:
        return "NOT_ATTEMPTED"
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        raise ValueError("confidence must be a number or None, got %r" % (confidence,))
    if not 0.0 <= confidence <= 1.0:
        raise ValueError("confidence must be within [0.0, 1.0], got %r" % (confidence,))
    if threshold is None:
        raise ValueError(
            "a real confidence value cannot be classified without a threshold, and this "
            "function does not guess one -- see its own docstring")
    return "GROUNDED" if confidence >= threshold else "LOW_CONFIDENCE"


def ground_target(screenshot_path, target_description, semantic_tree_path=None,
                   confidence_threshold=None, model=None):
    """Attempt to locate `target_description` in the screenshot at
    `screenshot_path`. Never raises for a missing model or a missing
    threshold -- both are honest NOT_ATTEMPTED/None outcomes, not errors --
    but does report FAIL for a screenshot that cannot actually be read, so
    a caller can tell "nothing to ground yet" from "the input was bad"."""
    screenshot_path = Path(screenshot_path)
    # semantic_tree_provided starts False and is set only after the file has
    # actually been read below. It used to be `semantic_tree_path is not
    # None`, a caller's unverified CLAIM: the path was never opened, read or
    # checked, so a nonexistent path still reported the tree as provided.
    # The screenshot argument next to it has always been held to a real
    # read (MW.screenshot_identity FAILs honestly on an unreadable file);
    # this field is now held to the same standard rather than a weaker one.
    evidence = {"screenshot": None, "semantic_tree_provided": False, "model_call": None}
    try:
        evidence["screenshot"] = MW.screenshot_identity(screenshot_path)
    except OSError as exc:
        return {"status": "FAIL", "confidence": None, "target": None, "evidence": evidence,
                "detail": "screenshot not found or unreadable: %s: %s" % (screenshot_path, exc)}
    except MW.Refusal as exc:
        return {"status": "FAIL", "confidence": None, "target": None, "evidence": evidence,
                "detail": "screenshot is not a valid PNG: %s" % exc}

    if semantic_tree_path is not None:
        # Path.read_bytes() deliberately, never the builtin file-opening
        # call: this module's ephemerality guarantee (no code path here may
        # write a file) is proved at source level by its own test suite,
        # which bans that builtin's spelling from this file outright. A read
        # is not a write, and this reads only to confirm the file is really
        # there and readable; the tree's content is deliberately not
        # embedded in the evidence, per the schema's own description.
        try:
            Path(semantic_tree_path).read_bytes()
        except OSError as exc:
            return {"status": "FAIL", "confidence": None, "target": None, "evidence": evidence,
                    "detail": "semantic tree not found or unreadable: %s: %s"
                              % (semantic_tree_path, exc)}
        evidence["semantic_tree_provided"] = True

    grounded = _call_vision_model(screenshot_path, target_description, semantic_tree_path, model)
    if grounded is None:
        return {"status": "NOT_ATTEMPTED", "confidence": None, "target": None, "evidence": evidence,
                "detail": "no vision model is wired into this adapter yet (M3.06 scope: interface "
                          "and dispatch contract only, see module docstring); nothing was grounded, "
                          "and no target or confidence was guessed"}

    # Unreachable tonight (_call_vision_model always returns None above),
    # kept so classify_confidence's threshold logic has a real caller the
    # moment a model is wired, rather than being dead code until then. A
    # malformed future implementation (missing "confidence", an
    # out-of-range value, no threshold supplied) must become a clean FAIL
    # here, never an uncaught exception or a silently accepted guess --
    # found by adversarial self-review of the first draft of this branch.
    try:
        confidence = grounded["confidence"]
        status = classify_confidence(confidence, confidence_threshold)
    except (KeyError, ValueError) as exc:
        return {"status": "FAIL", "confidence": None, "target": None, "evidence": evidence,
                "detail": "vision model call returned an unusable result: %s" % exc}

    evidence["model_call"] = grounded.get("model_call")
    if status not in _STATUSES_REQUIRING_GROUNDING:
        return {"status": status, "confidence": None, "target": None, "evidence": evidence,
                "detail": "vision model call produced no usable confidence to ground with (status=%s)" % status}
    return {"status": status, "confidence": confidence, "target": grounded.get("target"), "evidence": evidence,
            "detail": "vision model returned a target at confidence %.3f" % confidence}


def _result(action_id, action, status, detail):
    """An empty grounding record carrying one structural status. UNSUPPORTED
    means "this driver does not do that action"; FAIL means "this record is
    broken". The distinction is load-bearing for M3.07's router, which
    treats UNSUPPORTED as a reason to try the next driver."""
    return {"schema_version": "mobile-visual-grounding-v1", "action_id": action_id, "action": action,
            "stability": "visual_only", "status": status, "confidence": None, "target": None,
            "target_description": None, "evidence": {}, "detail": detail}


def _unsupported(action_id, action, detail):
    return _result(action_id, action, "UNSUPPORTED", detail)


def execute_action(record, screenshot_path, evidence_root, stages, semantic_tree_path=None,
                    confidence_threshold=None, model=None):
    """Ground `record`'s target in the screenshot at `screenshot_path`.
    This adapter never taps, scrolls, types, or otherwise changes device
    state itself -- M3.03/M3.04/M3.05's own adapters do that, using the
    target this function proposes. `evidence_root` and `stages` are
    accepted for signature parity with mobile_native_ios_adapter.
    execute_action() (a future M3.07 router calls any driver's
    execute_action the same way) but are not used: this adapter makes no
    subprocess call of its own to log as a stage, and writes no file of
    its own into evidence_root."""
    if not isinstance(record, dict):
        # FAIL, not UNSUPPORTED: a malformed record is broken for EVERY
        # driver, so reporting it as "unsupported here" invites M3.07's
        # router to retry the same dead record against every other adapter
        # in turn. mobile_native_ios_adapter and mobile_appium_adapter both
        # already return FAIL for this exact input with this exact detail
        # string; this was the one divergence, found by
        # test_mobile_driver_gauntlet.py (M3.08) on its first run.
        return _result(None, None, "FAIL",
                       "canonical action record must be a JSON object, got %s"
                       % type(record).__name__)

    action = record.get("action")
    action_id = record.get("action_id")
    problems = ACT.check(record, _ACTION_SCHEMA)
    if problems:
        return {"schema_version": "mobile-visual-grounding-v1", "action_id": action_id, "action": action,
                "stability": "visual_only", "status": "FAIL", "confidence": None, "target": None,
                "target_description": None, "evidence": {},
                "detail": "invalid canonical action record: " + "; ".join(problems)}

    if action not in TARGET_BEARING_ACTIONS:
        return _unsupported(action_id, action,
                             "this adapter only grounds target-bearing actions (%s); %s carries no target"
                             % (", ".join(TARGET_BEARING_ACTIONS), action))

    target = record.get("target")
    if not isinstance(target, dict):
        # WAIT_FOR's any_of lets a valid record supply params.duration_ms
        # instead of a target (mobile_canonical_action.ACTION_RULES) --
        # nothing for this adapter to ground in that case.
        return _unsupported(action_id, action,
                             "record has no target to ground (e.g. a duration_ms-only WAIT_FOR)")

    target_description = target.get("description") or target.get("value")
    grounding = ground_target(screenshot_path, target_description, semantic_tree_path,
                               confidence_threshold=confidence_threshold, model=model)
    result = {"schema_version": "mobile-visual-grounding-v1", "action_id": action_id, "action": action,
              "stability": "visual_only", "status": grounding["status"], "confidence": grounding["confidence"],
              "target": grounding["target"], "target_description": target_description,
              "evidence": grounding["evidence"], "detail": grounding["detail"]}

    # Validate HERE, where the record is produced, not only in main().
    # main()'s check() covered the CLI path alone, while the documented
    # in-process caller (M3.07's router) calls execute_action() directly and
    # so received an unvalidated grounding record -- exactly the path a real
    # vision model's malformed output (a NaN coordinate, a confidence out of
    # range) would travel once the _call_vision_model seam is wired.
    problems = check(result, _GROUNDING_SCHEMA)
    if problems:
        return {"schema_version": "mobile-visual-grounding-v1", "action_id": action_id,
                "action": action, "stability": "visual_only", "status": "FAIL",
                "confidence": None, "target": None, "target_description": target_description,
                "evidence": grounding["evidence"],
                "detail": "grounding result violates mobile-visual-grounding-v1: "
                          + "; ".join(problems)}
    return result


#: One risk_class per TARGET_BEARING_ACTIONS entry. All "safe": this driver
#: never touches a device itself, only proposes a target -- worst case a
#: wrong grounding is a wrong-but-inert coordinate pair, never a device
#: state change (unlike mobile_native_ios_adapter's OPEN_APP/DEEPLINK/etc,
#: which are "reversible" because they do change device state).
_RISK_CLASSES = {a: "safe" for a in TARGET_BEARING_ACTIONS}


def describe():
    """This driver's self-description, checked against
    docs/schema/mobile-driver-contract-v1.json by main()'s "describe"
    subcommand before it is ever printed, and by the test suite. Built
    from TARGET_BEARING_ACTIONS/_RISK_CLASSES, never a second hand-typed
    action list."""
    assert set(_RISK_CLASSES) == set(TARGET_BEARING_ACTIONS), \
        "_RISK_CLASSES has drifted from TARGET_BEARING_ACTIONS"
    supported = sorted(_RISK_CLASSES)
    return {
        "schema_version": "mobile-driver-contract-v1",
        "driver_id": DRIVER_ID,
        "driver_name": DRIVER_NAME,
        "action_vocabulary_ref": ACTION_VOCABULARY_REF,
        "supported_actions": supported,
        "platforms": ["ios", "android", "web", "cross-platform"],
        "device_modes": ["simulator", "real_device"],
        "remote_sessions_supported": False,
        "observations": ["screenshot"],
        "deterministic_selector_support": False,
        "visual_grounding_support": True,
        "risk_classes": [{"action": a, "risk_class": _RISK_CLASSES[a]} for a in supported],
    }


def _coordinate_problem(kind, key, value):
    """One shared predicate for every coordinate key of a grounded target
    (point x/y and bbox x0/y0/x1/y1), returning a problem string or None.

    `confidence` already got a real range check; coordinates only got an
    isinstance() type check, with no domain check at all, so NaN, Infinity
    and negative coordinates all validated clean. NaN and Infinity are the
    sharp end: json.dumps emits them as bare `NaN`/`Infinity` tokens, which
    are not valid RFC 8259 JSON, so a grounding record carrying one breaks
    any strict JSON reader downstream rather than failing here. A negative
    coordinate cannot name a pixel on a screenshot either."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "target.%s: %s target requires a numeric %s, got %r" % (key, kind, key, value)
    if not math.isfinite(value):
        return ("target.%s: %s target requires a finite %s, got %r -- NaN and Infinity are not "
                "valid JSON numbers" % (key, kind, key, value))
    if value < 0:
        return ("target.%s: %s target requires a non-negative %s, got %r"
                % (key, kind, key, value))
    return None


def hand_rules(record):
    """The one rule the keyword subset cannot express: confidence/target
    must be null unless status is GROUNDED/LOW_CONFIDENCE, and when it IS
    GROUNDED/LOW_CONFIDENCE, confidence must be a real number in
    [0.0, 1.0] and target must carry a real numeric point or bbox. This is
    the structural half of the "no silently defaulted confidence" and "no
    guessed target" guarantees -- checked here, not merely by convention in
    the code that produces these records, so a record that violates it
    fails validation regardless of how it was produced."""
    problems = []
    if not isinstance(record, dict):
        return problems
    status = record.get("status")
    confidence = record.get("confidence")
    target = record.get("target")

    if status in _STATUSES_FORBIDDING_GROUNDING:
        if confidence is not None:
            problems.append(
                "confidence: must be null when status is %r, got %r -- a visual grounding result "
                "must never claim a confidence value it did not actually compute" % (status, confidence))
        if target is not None:
            problems.append(
                "target: must be null when status is %r, got %r -- an unattempted, unsupported, or "
                "failed grounding must never guess a target" % (status, target))
    elif status in _STATUSES_REQUIRING_GROUNDING:
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            problems.append("confidence: must be a real number when status is %r, got %r"
                             % (status, confidence))
        elif not 0.0 <= confidence <= 1.0:
            problems.append("confidence: must be within [0.0, 1.0], got %r" % (confidence,))
        if not isinstance(target, dict):
            problems.append("target: must be an object when status is %r, got %r" % (status, target))
        else:
            kind = target.get("kind")
            if kind == "point":
                for key in ("x", "y"):
                    problem = _coordinate_problem("point", key, target.get(key))
                    if problem is not None:
                        problems.append(problem)
            elif kind == "bbox":
                for key in ("x0", "y0", "x1", "y1"):
                    problem = _coordinate_problem("bbox", key, target.get(key))
                    if problem is not None:
                        problems.append(problem)
            else:
                problems.append("target.kind: must be 'point' or 'bbox', got %r" % (kind,))

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
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("describe")
    run = sub.add_parser("run")
    run.add_argument("--record", required=True, help="path to a mobile-canonical-action-v1 JSON record")
    run.add_argument("--screenshot", required=True, help="path to the screenshot PNG to ground the target in")
    run.add_argument("--tree", default=None, help="optional path to an accessibility/semantic tree JSON file")
    run.add_argument("--out", required=True, help="evidence directory (created if missing; unused today, kept for parity with other drivers' CLI shape)")
    run.add_argument("--confidence-threshold", type=float, default=None,
                      help="Open product/design decision, not guessed by this unit -- see module "
                           "docstring. Omit unless a real number has been decided; it has no effect "
                           "tonight since no vision-model call produces a confidence to threshold.")
    run.add_argument("--model", default=None,
                      help="Vision model id, accepted for interface parity; no call is made yet "
                           "(M3.06 scope, see module docstring).")
    args = parser.parse_args(argv)

    if args.command == "describe":
        self_description = describe()
        problems = DC.check(self_description, _DRIVER_SCHEMA)
        if problems:
            print("mobile_visual_fallback_adapter: FAIL: self-description violates mobile-driver-contract-v1:")
            for p in problems:
                print(" -", p)
            return 1
        print(json.dumps(self_description, indent=2, sort_keys=True))
        return 0

    try:
        record = ACT.CC.load_json(args.record, "canonical action record")
    except ACT.CC.NoData as exc:
        print("mobile_visual_fallback_adapter: NO-DATA: %s" % exc)
        return 2
    Path(args.out).mkdir(parents=True, exist_ok=True)
    stages = []
    result = execute_action(record, args.screenshot, Path(args.out), stages,
                             semantic_tree_path=args.tree,
                             confidence_threshold=args.confidence_threshold, model=args.model)
    problems = check(result, _GROUNDING_SCHEMA)
    if problems:
        print("mobile_visual_fallback_adapter: FAIL: result violates mobile-visual-grounding-v1:")
        for p in problems:
            print(" -", p)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return {"GROUNDED": 0, "LOW_CONFIDENCE": 0, "NOT_ATTEMPTED": 0,
            "UNSUPPORTED": 1, "FAIL": 1}.get(result["status"], 1)


if __name__ == "__main__":
    sys.exit(main())
