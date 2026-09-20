#!/usr/bin/env python3
"""EPIC M3.07 Hybrid Action Router: given a canonical action record
(mobile-canonical-action-v1) and a list of registered driver adapters' real
`describe()` output (mobile-driver-contract-v1, M3.01), select which one
driver should execute the action.

THIS MODULE NEVER EXECUTES AN ACTION ITSELF. select_driver() returns a
driver_id (or a structural NO_DRIVER/FAIL result); the caller is the one
that looks up that driver's own module and calls its execute_action(). See
"CALL-SHAPE GAP" below for why the caller, not this router, must know how
to build that call.

SELECTION IS DRIVEN ONLY BY REAL describe() OUTPUT, NEVER A HARDCODED
ASSUMPTION. Every candidate driver is re-validated against
mobile-driver-contract-v1 here (a description that fails validation is
excluded, never trusted), and every ranking signal (supported_actions,
deterministic_selector_support, visual_grounding_support, platforms,
risk_classes) is read from that description dict, never guessed from a
driver_id or hardcoded per adapter. test_mobile_hybrid_action_router.py
proves this by calling describe() on the real, imported
mobile_native_ios_adapter / mobile_appium_adapter /
mobile_visual_fallback_adapter modules (M3.03/M3.05/M3.06, all merged into
this branch), never a hand-typed fake capability dict, so a real change to
any adapter's describe() changes this router's behavior instead of leaving
it stale.

RANKING, per this unit's brief (docs/plan/MOBILE-EPIC-M3-UNITS.md's M3.07
row, not present in this checkout tonight -- see NO-DATA note in the PR
body -- so this ranking is built from the brief's own restated criteria
plus the source roadmap's priority order quoted directly in the brief:
"API/tool > native semantic action > accessibility/test-id selector >
visual semantic grounding > raw coordinate action"):

  1. HARD FILTER, semantic certainty: a driver whose real supported_actions
     does not include this action is excluded outright, never merely
     deprioritized -- it would return a structural UNSUPPORTED at
     execution, so it is not a candidate at all.
  2. HARD FILTER, platform (only when `platform` is passed): a driver whose
     real platforms does not include it (and does not declare
     "cross-platform") is excluded.
  3. PRIMARY rank, determinism / semantic certainty by target tier: the
     record's own target.selector_type places it in one of five tiers
     (_SELECTOR_TIER below); for the "visual_grounding" tier (selector_type
     "image") a driver's real visual_grounding_support decides; for every
     other tier that carries a target, a driver's real
     deterministic_selector_support decides; for the "api_tool" tier (no
     target at all -- OPEN_APP, DEEPLINK, CAPTURE, FINISH, ...) neither
     signal is meaningful, so this axis is a tie and rank 4 decides.
  4. SECONDARY rank, platform-native fidelity: fewer platforms declared in
     a driver's own describe() output ranks as more native/specialized
     (native-ios-simctl declares exactly ["ios"]; appium declares
     ["ios","android"]; visual-fallback declares four). This also carries
     the "prefer the native adapter for its own platform" rule even when
     no explicit `platform` argument narrows the candidate set.
  5. TERTIARY rank, risk: prefer the driver whose own risk_classes entry
     for this action is the lower-risk class (safe < reversible <
     destructive < irreversible).
  6. Final tiebreak: driver_id, alphabetical, only so the result is
     reproducible when every real signal above is genuinely tied.

CALL-SHAPE GAP found during this unit's own adversarial self-review (see
the PR body): the M3.07 brief assumed the three real adapters "share the
same call shape since they all implement the same contract". They do not.
mobile_native_ios_adapter.execute_action(record, device, evidence_root,
stages) takes a simulator UDID string second; mobile_appium_adapter.
execute_action(record, capabilities, evidence_root, stages, server_url=...)
takes an Appium capabilities dict second; mobile_visual_fallback_adapter.
execute_action(record, screenshot_path, evidence_root, stages,
semantic_tree_path=None, confidence_threshold=None, model=None) takes a
screenshot path string second. mobile-driver-contract-v1 (M3.01) describes
what a driver can do, never its execute_action call shape, so this router
cannot close that gap without inventing a field the frozen M3.01 schema
does not have -- out of scope for this unit. select_driver() therefore
returns only a driver_id; the caller must already know, per driver_id,
which second-argument shape that specific driver module expects.

BRANCH-LEVEL GAP, also found in self-review: mobile-driver-contract-v1
records support per ACTION, not per target.selector_type or per param
branch. A driver whose real supported_actions lists WAIT_FOR may still
return UNSUPPORTED at execution for a target-bearing WAIT_FOR record even
though it never declared duration_ms-only support separately (both
mobile_native_ios_adapter and mobile_appium_adapter are exactly this case
tonight, confirmed by reading their own _wait_for handlers). This router
selects the best real candidate by the information the M3.01 contract
actually carries; it cannot see finer than that contract does.
"""
import argparse
import json
import sys

import contract_check as CC
import mobile_canonical_action as ACT
import mobile_driver_contract as DC

# Loaded once at import, same reasoning as every sibling adapter: fixed
# repo files, and a caller may route many actions in a loop.
_ACTION_SCHEMA = ACT.CC.load_json(ACT.DEFAULT_SCHEMA, "canonical action schema")
_DRIVER_SCHEMA = DC.CC.load_json(DC.DEFAULT_SCHEMA, "driver contract schema")

#: Maps a target's selector_type (mobile-canonical-action-v1's own enum) to
#: one of the five priority tiers the source roadmap names. "api_tool" is
#: not a selector_type value; it is what _target_tier() returns for a
#: record that carries no target at all.
_SELECTOR_TIER = {
    "accessibility_id": "accessibility_selector",
    "testid": "accessibility_selector",
    "xpath": "native_semantic",
    "text": "native_semantic",
    "image": "visual_grounding",
    "coordinates": "raw_coordinate",
}

_TIER_LABEL = {
    "api_tool": "API/tool (no on-screen target)",
    "native_semantic": "native semantic action",
    "accessibility_selector": "accessibility/test-id selector",
    "visual_grounding": "visual semantic grounding",
    "raw_coordinate": "raw coordinate action",
}

#: safe < reversible < destructive < irreversible, per
#: docs/schema/mobile-driver-contract-v1.json's own risk_class enum.
_RISK_RANK = {"safe": 0, "reversible": 1, "destructive": 2, "irreversible": 3}


def _target_tier(record):
    target = record.get("target")
    if not isinstance(target, dict):
        return "api_tool"
    return _SELECTOR_TIER.get(target.get("selector_type"), "api_tool")


def _risk_for_action(driver_desc, action, *, jev_runner=None):
    """The risk_class this driver's own describe() output declares for
    `action`. A validated description (checked by select_driver() before
    this is ever called) always has exactly one matching entry
    (mobile_driver_contract.hand_rules() enforces that both ways); returns
    None only if select_driver() is ever changed to skip that check, so a
    caller reading this defensively never crashes on a bad description.

    JEV-G1 wave-1 seam J097 (registry: mobile action risk classify):
    second-opinions this table lookup via jev_seam.consult(), off by
    default in data/jev-seams.json, called for its side effect only (the
    calibration ledger row and A0.6 audit sample) -- WAVE 1 IS
    SHADOW-ONLY BY CONTRACT (opus-review-seams-g1-g3.md, C1): this
    function ALWAYS returns its own local `answer`, never consult()'s,
    whatever mode says, including "act": a raw noul or score answer is
    not even the right shape for a risk_class, and there is no promoted,
    calibrated evidence for this entry yet to make "act" a real path
    today regardless. `jev_runner` exists only so a test can inject a
    scripted bridge."""
    answer = None
    for entry in driver_desc.get("risk_classes", []):
        if isinstance(entry, dict) and entry.get("action") == action:
            answer = entry.get("risk_class")
            break
    try:
        import jev_g1_seam_cache
        if not jev_g1_seam_cache.is_off("J097"):
            import jev_seam
            jev_seam.consult(
                "J097", {"driver_id": driver_desc.get("driver_id"), "action": action}, answer,
                seams_config=jev_seam.load_seams_config(),
                registry=jev_seam.load_registry(),
                ledger_dir=jev_seam.DEFAULT_LEDGER_DIR, runner=jev_runner,
            )  # C1: return value intentionally discarded, see docstring above
    except Exception:
        pass  # sbe: allow-silent the seam is advisory only, the table-lookup verdict always stands
    return answer


def _selector_match(tier, driver_desc, *, jev_runner=None):
    """True when this driver's real, self-reported capability matches what
    `tier` needs. "api_tool" always matches (no selector is involved, so
    this axis is a deliberate tie -- see rank 4, platform fidelity, for
    what actually decides an API/tool action). "visual_grounding" is the
    one tier where visual_grounding_support is the right signal, never
    deterministic_selector_support (a deterministic-selector driver cannot
    interpret an image reference).

    JEV-G1 wave-1 seam J098 (registry: mobile driver selector match):
    second-opinions this heuristic via jev_seam.consult(), off by
    default in data/jev-seams.json, called for its side effect only (the
    calibration ledger row and A0.6 audit sample) -- WAVE 1 IS
    SHADOW-ONLY BY CONTRACT (opus-review-seams-g1-g3.md, C1): this
    function ALWAYS returns its own local `answer` (a bool), never
    consult()'s, whatever mode says, including "act": a raw jev choice
    answer is a driver-name STRING, not the bool this function's own
    contract promises, and there is no promoted, calibrated evidence for
    this entry yet to make "act" a real path today regardless.
    `jev_runner` exists only so a test can inject a scripted bridge."""
    if tier == "api_tool":
        answer = True
    elif tier == "visual_grounding":
        answer = bool(driver_desc.get("visual_grounding_support"))
    else:
        answer = bool(driver_desc.get("deterministic_selector_support"))
    try:
        import jev_g1_seam_cache
        if not jev_g1_seam_cache.is_off("J098"):
            import jev_seam
            jev_seam.consult(
                "J098", {"tier": tier, "driver_id": driver_desc.get("driver_id")}, answer,
                seams_config=jev_seam.load_seams_config(),
                registry=jev_seam.load_registry(),
                ledger_dir=jev_seam.DEFAULT_LEDGER_DIR, runner=jev_runner,
            )  # C1: return value intentionally discarded, see docstring above
    except Exception:
        pass  # sbe: allow-silent the seam is advisory only, the heuristic's verdict always stands
    return answer


def select_driver(record, driver_descriptions, platform=None):
    """Select which registered driver should execute `record`.

    record: a mobile-canonical-action-v1 dict.
    driver_descriptions: a list of dicts, each a driver's own real
        describe() output (mobile-driver-contract-v1). Every entry is
        re-validated here; an invalid one is excluded, never trusted.
    platform: optional hint ("ios"/"android"/"web"/"cross-platform"). When
        given, a driver whose real platforms does not include it (and does
        not declare "cross-platform") is excluded outright.

    Returns a dict: status is "SELECTED" (driver_id names the winner),
    "NO_DRIVER" (no registered description both supports the action and,
    if given, the platform), or "FAIL" (record itself is invalid). Always
    carries considered/excluded so a caller can audit why a driver did or
    did not win, never just a bare id.

    Also carries the winner's own risk_class and selector_match as top-level
    keys (None unless status is "SELECTED") so a caller can gate on risk
    machine-readably, rather than parsing them back out of the
    human-readable `reason` prose.
    """
    result = {"status": None, "driver_id": None, "reason": None, "tier": None,
              "risk_class": None, "selector_match": None,
              "considered": [], "excluded": []}

    problems = ACT.check(record, _ACTION_SCHEMA)
    if problems:
        result["status"] = "FAIL"
        result["reason"] = "invalid canonical action record: " + "; ".join(problems)
        return result

    action = record["action"]
    tier = _target_tier(record)
    result["tier"] = tier

    # driver_id is an IDENTITY, and nothing upstream enforces that two
    # registered descriptions do not claim the same one. A second
    # description impersonating a real driver's id could otherwise win the
    # ranking for an action the real driver does not support, and then be
    # dispatched by the caller to the real module (which looks the winner up
    # BY that id), where it fails. A duplicated id is ambiguous identity:
    # every description claiming it is excluded, because this router has no
    # way to tell the real driver from the impostor and must never guess.
    id_counts = {}
    for desc in (driver_descriptions or []):
        if isinstance(desc, dict) and isinstance(desc.get("driver_id"), str):
            id_counts[desc["driver_id"]] = id_counts.get(desc["driver_id"], 0) + 1

    candidates = []
    for desc in (driver_descriptions or []):
        driver_id = desc.get("driver_id") if isinstance(desc, dict) else None
        if id_counts.get(driver_id, 0) > 1:
            result["excluded"].append({
                "driver_id": driver_id,
                "reason": "driver_id %r is claimed by %d registered descriptions; ambiguous "
                          "identity is refused outright rather than ranked"
                          % (driver_id, id_counts[driver_id])})
            continue
        desc_problems = DC.check(desc, _DRIVER_SCHEMA)
        if desc_problems:
            result["excluded"].append({
                "driver_id": driver_id,
                "reason": "self-description invalid: " + "; ".join(desc_problems)})
            continue
        if action not in desc.get("supported_actions", []):
            result["excluded"].append({
                "driver_id": driver_id,
                "reason": "%s is not in this driver's real supported_actions" % action})
            continue
        if platform is not None:
            platforms = desc.get("platforms", [])
            if platform not in platforms and "cross-platform" not in platforms:
                result["excluded"].append({
                    "driver_id": driver_id,
                    "reason": "does not declare platform %r (declares %r)" % (platform, platforms)})
                continue
        risk_class = _risk_for_action(desc, action)
        candidates.append({
            "driver_id": driver_id,
            "selector_match": _selector_match(tier, desc),
            "platform_breadth": len(desc.get("platforms", [])),
            "risk_rank": _RISK_RANK.get(risk_class, len(_RISK_RANK)),
            "risk_class": risk_class,
        })
        result["considered"].append(driver_id)

    if not candidates:
        result["status"] = "NO_DRIVER"
        result["reason"] = (
            "no registered driver's real describe() output supports %s%s; see excluded[] "
            "for why each candidate was ruled out"
            % (action, (" for platform %r" % platform) if platform else ""))
        return result

    candidates.sort(key=lambda c: (
        0 if c["selector_match"] else 1,
        c["platform_breadth"],
        c["risk_rank"],
        c["driver_id"] or "",
    ))
    winner = candidates[0]
    result["status"] = "SELECTED"
    result["driver_id"] = winner["driver_id"]
    result["risk_class"] = winner["risk_class"]
    result["selector_match"] = winner["selector_match"]
    result["reason"] = (
        "tier=%s (%s); selector_match=%s; platform_breadth=%d; risk_class=%s; "
        "%d candidate(s) considered, %d excluded"
        % (tier, _TIER_LABEL[tier], winner["selector_match"], winner["platform_breadth"],
           winner["risk_class"], len(candidates), len(result["excluded"])))
    if winner["selector_match"] is False:
        # Never true when a better-matching candidate exists (it would
        # outrank this one on the primary axis) -- flagged loudly rather
        # than silently, both so a caller knows the winner is a
        # least-bad pick, not a real match, and so a future change to
        # _selector_match or the sort key that reopens a silent
        # visual-fallback-as-default path fails LOUD in this field, not
        # quietly.
        result["reason"] += (
            " (WARNING: winning driver has no deterministic/native/visual match for this "
            "tier; no better-matching driver was registered)")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--record", required=True,
                         help="path to a mobile-canonical-action-v1 JSON record")
    parser.add_argument("--driver-describe", action="append", default=[], metavar="PATH",
                         dest="driver_describe",
                         help="path to a driver's real describe() JSON output (repeatable; e.g. "
                              "produced by `python3 scripts/mobile_native_ios_adapter.py describe`)")
    parser.add_argument("--platform", default=None,
                         choices=["ios", "android", "web", "cross-platform"],
                         help="optional platform hint for the platform-native-fidelity tiebreak")
    args = parser.parse_args(argv)

    try:
        record = CC.load_json(args.record, "canonical action record")
        descriptions = [CC.load_json(p, "driver describe() output") for p in args.driver_describe]
    except CC.NoData as exc:
        print("mobile_hybrid_action_router: NO-DATA: %s" % exc)
        return 2

    result = select_driver(record, descriptions, platform=args.platform)
    print(json.dumps(result, indent=2, sort_keys=True))
    return {"SELECTED": 0, "NO_DRIVER": 1, "FAIL": 1}.get(result["status"], 1)


if __name__ == "__main__":
    sys.exit(main())
