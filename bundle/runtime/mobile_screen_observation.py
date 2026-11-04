#!/usr/bin/env python3
"""EPIC M5.01 Mobile Screen Observation: build and validate one look at one
screen against docs/schema/mobile-screen-observation-v1.json.

The 'sees it' half of M5's exit criterion, and only that half. This module
perceives nothing, decides nothing, acts on nothing, promotes nothing, and
calls no model. It reads records and files and returns problems. If it ever
needs to touch a device, the unit has been misread.

NOTHING A CALLER SAYS IS RECORDED AS A FACT. observe() records a screenshot
or a tree only after really reading the file, through the same
mobile_workflow helpers a driver's own CAPTURE evidence uses
(screenshot_identity() and digest()), so a screenshot a driver captured and
one an observer graded are identified the same way. A file that could not be
read becomes a null plus a FAIL status plus a stated reason, never a
present-but-unverified object. This is the M3.06 defect that is deliberately
not repeated here: that adapter once set evidence.semantic_tree_provided from
`semantic_tree_path is not None` without ever opening the path, so a
nonexistent tree reported as provided (recorded in
docs/plan/M3-CHAIN-FIX-BRIEF-2026-09-16.md, and since fixed in that module).

Reuses contract_check.validate for the same enforced JSON-schema keyword
subset every sibling mobile contract uses (type, required, properties, enum,
const, items, minItems, additionalProperties) rather than a second structural
checker. contract_check exposes no shared non-dict guard, so hand_rules()
returns cleanly on a non-dict record the way
mobile_driver_contract.hand_rules() already does, rather than reaching .get
on a list or a string.

The rules the keyword subset cannot express, enforced by hand below and
documented in the schema's own description so a reader of either file finds
the same account:

  TREE KIND MUST BE ADVERTISED. A tree kind the named driver does not list in
  its own mobile-driver-contract-v1 observations enum is refused. Skipped,
  not failed, when no driver contract record is supplied, following
  mobile_driver_contract.py's own stated reasoning about never failing on a
  sibling unit's absent file.

  EVERY COORDINATE MUST BE FINITE. One shared predicate over every numeric
  key (geometry point x/y, geometry bbox x0/y0/x1/y1, viewport width/height
  and scale), applying math.isfinite explicitly. `value < lo` is vacuously
  False for NaN, which is exactly how NaN passed every range check in M3.05
  and M3.06. json.dumps also emits NaN and Infinity as bare tokens that are
  not valid RFC 8259 JSON, so a record carrying one breaks any strict reader
  downstream rather than failing here.

  GEOMETRY ALONE NEVER JUSTIFIES A STABLE TARGET. A target whose stability is
  anything other than visual_only must carry a real selector_type and a real
  non-empty value. Promotion of an observed target into a governed selector
  is EPIC M6 and this module structurally never performs it.

  VIEWPORT UNIT AND SCALE. unit 'points' requires a non-null, finite,
  positive scale; unit 'pixels' with a null scale is legal and means the
  record is unconverted. No code path here converts one into the other.

  A NULL SCREENSHOT WITH TARGETS IS A CONTRADICTION. Targets have to have
  been seen on something.

Exit contract, mirroring scripts/mobile_driver_contract.py and this estate's
other gates:
  0  PASS      the record satisfies the schema and the hand rules
  1  FAIL      one or more problems, each printed on its own line
  2  NO-DATA   the record or the schema could not be read as JSON

NO-DATA IS NOT A PASS: a record this script could not open or parse is
"could not look", never "looked and found nothing wrong".

Python 3, standard library only. No network, no subprocess, no device call.
"""
import argparse
import datetime
import math
import os
import sys

import contract_check as CC
import mobile_workflow as MW

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_SCHEMA = os.path.join(
    ROOT, "docs", "schema", "mobile-screen-observation-v1.json")
DRIVER_SCHEMA = os.path.join(
    ROOT, "docs", "schema", "mobile-driver-contract-v1.json")

SCHEMA_VERSION = "mobile-screen-observation-v1"

#: The two values of mobile-driver-contract-v1's observations enum that can
#: name a tree. Spelled once, here, and read from nowhere else.
TREE_KINDS = ("semantic_tree", "accessibility_tree")

#: The one stability value that pixel geometry alone can justify, matching
#: mobile-visual-grounding-v1's const-pinned field of the same name.
VISUAL_ONLY = "visual_only"


def _coordinate_problem(label, key, value, allow_null=False):
    """One shared predicate for every numeric key in the record, returning a
    problem string or None.

    Shared rather than per-field on purpose: a per-field range check is what
    let NaN through twice on this estate, because `value < lo` is vacuously
    False for NaN and a check written per field is a check that can be
    forgotten per field."""
    if value is None:
        if allow_null:
            return None
        return "%s.%s: required, got null" % (label, key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "%s.%s: requires a number, got %r" % (label, key, value)
    if not math.isfinite(value):
        return ("%s.%s: requires a finite number, got %r: NaN and Infinity are "
                "not valid JSON numbers" % (label, key, value))
    if value < 0:
        return "%s.%s: requires a non-negative number, got %r" % (label, key, value)
    return None


def _geometry_problems(index, geometry):
    """point/bbox shape plus finiteness, in mobile-visual-grounding-v1's own
    shape. The keyword subset has no oneOf, so the union is checked here."""
    problems = []
    label = "targets[%d].geometry" % index
    if not isinstance(geometry, dict):
        problems.append("%s: must be an object or null, got %r" % (label, geometry))
        return problems
    kind = geometry.get("kind")
    if kind == "point":
        keys = ("x", "y")
    elif kind == "bbox":
        keys = ("x0", "y0", "x1", "y1")
    else:
        problems.append("%s.kind: must be 'point' or 'bbox', got %r" % (label, kind))
        return problems
    for key in keys:
        problem = _coordinate_problem(label, key, geometry.get(key))
        if problem is not None:
            problems.append(problem)
    return problems


def hand_rules(record, driver_contract=None):
    """The rules the enforced keyword subset cannot express, listed in the
    module docstring and in the schema's description. Returns every problem
    found, never stopping at the first."""
    problems = []
    if not isinstance(record, dict):
        # A non-dict record is already a structural FAIL from validate(); the
        # hand rules must not add a traceback on top of it. This crash class
        # was found three times on this estate (mobile_state_fixture.py,
        # mobile_test_route.py, mobile_canonical_action.py).
        return problems

    screenshot = record.get("screenshot")
    tree = record.get("tree")
    viewport = record.get("viewport")
    targets = record.get("targets")

    # TREE KIND MUST BE ADVERTISED, when a driver contract is supplied.
    if isinstance(tree, dict) and isinstance(driver_contract, dict):
        advertised = driver_contract.get("observations")
        advertised = advertised if isinstance(advertised, list) else []
        kind = tree.get("kind")
        if kind in TREE_KINDS and kind not in advertised:
            problems.append(
                "tree.kind: driver %r does not advertise %r in its own driver "
                "contract observations %r"
                % (record.get("driver_id"), kind, advertised))

    # VIEWPORT UNIT AND SCALE, plus finiteness of its own numbers.
    if isinstance(viewport, dict):
        for key in ("width", "height"):
            problem = _coordinate_problem("viewport", key, viewport.get(key))
            if problem is not None:
                problems.append(problem)
        scale = viewport.get("scale")
        problem = _coordinate_problem("viewport", "scale", scale, allow_null=True)
        if problem is not None:
            problems.append(problem)
        unit = viewport.get("unit")
        if unit == "points" and scale is None:
            problems.append(
                "viewport.scale: a viewport in points requires a real scale: a "
                "point size nobody can trace back to a measured scale factor is "
                "a guess wearing a unit label")
        elif unit == "points" and isinstance(scale, (int, float)) \
                and not isinstance(scale, bool) and math.isfinite(scale) \
                and scale <= 0:
            problems.append(
                "viewport.scale: must be greater than zero, got %r" % (scale,))

    # TARGETS: geometry shape and finiteness, the stability rule, and the
    # null-screenshot contradiction.
    if isinstance(targets, list):
        if targets and screenshot is None:
            problems.append(
                "targets: a non-empty targets list with a null screenshot is a "
                "contradiction: %d target(s) were recorded as seen, with nothing "
                "recorded as having been looked at" % len(targets))
        for index, target in enumerate(targets):
            if not isinstance(target, dict):
                continue
            geometry = target.get("geometry")
            if geometry is not None:
                problems.extend(_geometry_problems(index, geometry))
            stability = target.get("stability")
            if stability is not None and stability != VISUAL_ONLY:
                selector_type = target.get("selector_type")
                value = target.get("value")
                if not isinstance(selector_type, str) or not selector_type:
                    problems.append(
                        "targets[%d].selector_type: stability %r requires a real "
                        "selector, got %r: pixel geometry alone can never justify "
                        "a target stronger than %r"
                        % (index, stability, selector_type, VISUAL_ONLY))
                if not isinstance(value, str) or not value.strip():
                    problems.append(
                        "targets[%d].value: stability %r requires a real non-empty "
                        "selector value, got %r"
                        % (index, stability, value))

    return problems


def check(record, schema, driver_contract=None):
    """Every problem found, structural then hand rules, deduplicated but
    order preserved."""
    problems = []
    CC.validate(record, schema, "", problems)
    problems.extend(hand_rules(record, driver_contract))
    seen, out = set(), []
    for p in problems:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def observe(driver_id, screenshot_path=None, tree_path=None, tree_kind=None,
            locale=None, targets=None, observed_at=None):
    """Build one observation record, reading every file it claims to have
    read.

    Never raises for a bad path: an unreadable screenshot or tree becomes a
    null plus status FAIL plus a stated reason, which is the honest record of
    "I could not look at that", and is what a caller can act on. The viewport
    is derived from the screenshot the PNG header really carried, so its unit
    is 'pixels' and its scale is null: no scale factor is observable anywhere
    on this branch, and inventing one to reach points would be exactly the
    conversion this schema forbids."""
    record = {
        "schema_version": SCHEMA_VERSION,
        "observed_at": observed_at or datetime.datetime.now(
            datetime.timezone.utc).isoformat(),
        "driver_id": driver_id,
        "status": "OBSERVED",
        "screenshot": None,
        "tree": None,
        "viewport": None,
        "locale": locale,
        "targets": list(targets) if isinstance(targets, list) else [],
        "detail": "screen observed",
    }
    notes = []

    if screenshot_path is not None:
        try:
            record["screenshot"] = MW.screenshot_identity(screenshot_path)
        except OSError as exc:
            record["status"] = "FAIL"
            notes.append("screenshot not found or unreadable: %s: %s"
                         % (screenshot_path, exc))
        except MW.Refusal as exc:
            record["status"] = "FAIL"
            notes.append("screenshot is not a valid PNG: %s" % exc)
        else:
            record["viewport"] = {
                "width": record["screenshot"]["width"],
                "height": record["screenshot"]["height"],
                "unit": "pixels",
                "scale": None,
            }

    if tree_path is not None:
        if tree_kind not in TREE_KINDS:
            record["status"] = "FAIL"
            notes.append("tree kind must be one of %r, got %r"
                         % (list(TREE_KINDS), tree_kind))
        else:
            try:
                identity = MW.digest(tree_path)
            except (OSError, MW.Refusal) as exc:
                record["status"] = "FAIL"
                notes.append("tree not found or unreadable: %s: %s"
                             % (tree_path, exc))
            else:
                record["tree"] = {
                    "kind": tree_kind,
                    "identity": {
                        "path": identity["path"],
                        "size": identity["size"],
                        "sha256": identity["sha256"],
                    },
                }

    if record["status"] == "FAIL" and record["screenshot"] is None:
        # No screenshot means nothing was looked at, so nothing can honestly
        # be claimed as seen on it.
        record["targets"] = []
    if notes:
        record["detail"] = "; ".join(notes)
    return record


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("record", help="path to the mobile-screen-observation JSON record")
    ap.add_argument("--schema", default=DEFAULT_SCHEMA)
    ap.add_argument("--driver-contract", default=None,
                    help="optional path to the named driver's own "
                         "mobile-driver-contract-v1 record, so the tree kind "
                         "can be checked against what that driver actually "
                         "advertises. Omitted means that one rule is skipped, "
                         "never failed.")
    args = ap.parse_args(argv)
    try:
        record = CC.load_json(args.record, "screen observation record")
        schema = CC.load_json(args.schema, "screen observation schema")
        driver_contract = None
        if args.driver_contract is not None:
            driver_contract = CC.load_json(
                args.driver_contract, "driver contract record")
    except CC.NoData as exc:
        print("NO-DATA: %s" % exc)
        return 2
    problems = check(record, schema, driver_contract)
    if problems:
        print("FAIL: %d problem(s)" % len(problems))
        for p in problems:
            print(" -", p)
        return 1
    print("PASS: %s validates as %s" % (args.record, SCHEMA_VERSION))
    return 0


if __name__ == "__main__":
    sys.exit(main())
