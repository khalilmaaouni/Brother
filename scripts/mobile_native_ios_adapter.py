#!/usr/bin/env python3
"""EPIC M3.03 Native iOS Driver Adapter: translates M3.02's canonical mobile
action vocabulary (mobile-canonical-action-v1) into `xcrun simctl` calls,
self-describing itself against M3.01's mobile-driver-contract-v1 schema.

Reuses scripts/mobile_workflow.py's invoke() (subprocess + evidence log under
an evidence root) and screenshot_identity() (PNG validation) rather than a
second process runner -- the same "simctl control already in
scripts/mobile_workflow.py" the M3.03 unit brief points at. Does not import
scripts/mobile_xcode_canary_local.py: as of this session that module lives
only on an unmerged PR (#703, still open on hub) and everything reusable it
offers (invoke/identity/screenshot_identity/Refusal/lease) already lives in
mobile_workflow.py, which it imports from too; see the PR body for detail.

CONFIRMED via `xcrun simctl --help` / `xcrun simctl help <subcommand>` on
this machine (2026-09-15): simctl has launch, openurl, privacy, location and
io screenshot, but NO subcommand for touch injection, text typing, gestures,
device rotation, network-condition simulation, or reading the accessibility
tree. So this driver's real supported_actions are exactly OPEN_APP, DEEPLINK,
SET_PERMISSION, SET_LOCATION, CAPTURE, WAIT_FOR (duration_ms branch only),
FINISH, NEED_HUMAN, IMPOSSIBLE -- every other canonical action returns a
structural UNSUPPORTED result (see UNSUPPORTED_REASONS), never a silent
no-op. describe()'s supported_actions is built FROM ACTION_HANDLERS, never a
second hand-typed list, so the self-description and the real implementation
cannot drift apart; test_mobile_native_ios_adapter.py proves this both ways.
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

import mobile_canonical_action as ACT
import mobile_driver_contract as DC
import mobile_workflow as MW

DRIVER_ID = "native-ios-simctl"
DRIVER_NAME = "Native iOS Simulator driver (xcrun simctl)"
ACTION_VOCABULARY_REF = "mobile-canonical-action-v1"

# Loaded once at import rather than per execute_action() call -- these are
# fixed repo files, and a caller (M3.07's router) may drive many actions in
# a loop.
_ACTION_SCHEMA = ACT.CC.load_json(ACT.DEFAULT_SCHEMA, "canonical action schema")
_DRIVER_SCHEMA = DC.CC.load_json(DC.DEFAULT_SCHEMA, "driver contract schema")

#: action_id is caller-controlled data (an external JSON record), not a
#: filesystem-safe token -- CAPTURE builds a path from it, so it is
#: sanitized before that use rather than trusted (mirrors mobile_workflow's
#: own escape checks, e.g. identity()'s executable-name guard).
_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9_.-]")

#: xcrun simctl privacy <device> <grant|revoke|reset> <service> [<bundle id>].
#: grant/revoke need a bundle id; reset does not (matches `xcrun simctl help
#: privacy`, checked directly on this machine, not guessed).
_PERMISSION_STATE_TO_SIMCTL_ACTION = {"allow": "grant", "deny": "revoke", "ask": "reset"}

#: Apple's bundle-identifier grammar: reverse-DNS, alphanumerics and hyphens
#: per segment. bundle_id is a param THIS ADAPTER invented -- the canonical
#: vocabulary only requires params.permission/state for SET_PERMISSION -- so
#: nothing upstream validates it and the adapter must, before it becomes an
#: argv element. Uses fullmatch plus \Z rather than match+$, since $ (without
#: re.MULTILINE) still matches just before a trailing newline, so
#: "com.foo\n" would otherwise pass. Same shape as _require_valid_package_id
#: in scripts/device_matrix.py; reused rather than reinvented.
_BUNDLE_ID_RE = re.compile(r"[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+\Z")


def _require_valid_bundle_id(bundle_id):
    """None if bundle_id matches Apple's bundle-identifier grammar, else a
    FAIL reason string -- callers return a FAIL result on a non-None result
    rather than ever reaching subprocess.run with it. A dict, list or
    whitespace-only bundle_id previously passed canonical-action validation
    with zero problems and then raised an uncaught TypeError inside
    subprocess.run (execute_action only catches MW.Refusal and OSError)."""
    if not isinstance(bundle_id, str) or not _BUNDLE_ID_RE.fullmatch(bundle_id):
        return ("params.bundle_id %r is not a valid iOS bundle identifier; refused before any "
                "simctl call" % (bundle_id,))
    return None


def _open_app(record, device, evidence_root, stages):
    app_id = record["params"]["app_id"]
    MW.invoke(["xcrun", "simctl", "launch", "--terminate-running-process", device, app_id],
              None, evidence_root, stages, "open_app")
    return {"status": "PASS", "detail": "launched %s on %s" % (app_id, device)}


def _deeplink(record, device, evidence_root, stages):
    uri = record["params"]["uri"]
    MW.invoke(["xcrun", "simctl", "openurl", device, uri], None, evidence_root, stages, "deeplink")
    return {"status": "PASS", "detail": "opened %s on %s" % (uri, device)}


def _set_permission(record, device, evidence_root, stages):
    permission = record["params"]["permission"]
    state = record["params"]["state"]
    simctl_action = _PERMISSION_STATE_TO_SIMCTL_ACTION[state]
    bundle_id = record["params"].get("bundle_id")
    if simctl_action in ("grant", "revoke") and not bundle_id:
        # The canonical vocabulary only requires params.permission/state for
        # SET_PERMISSION; simctl privacy grant/revoke additionally require a
        # bundle id. Refuse structurally rather than guess one.
        return {"status": "FAIL",
                "detail": "%s %r needs params.bundle_id, which this record does not carry"
                          % (simctl_action, permission)}
    argv = ["xcrun", "simctl", "privacy", device, simctl_action, permission]
    if bundle_id:
        reason = _require_valid_bundle_id(bundle_id)
        if reason is not None:
            return {"status": "FAIL", "detail": reason}
        argv.append(bundle_id)
    MW.invoke(argv, None, evidence_root, stages, "set_permission")
    return {"status": "PASS", "detail": "%s permission %r on %s" % (simctl_action, permission, device)}


def _set_location(record, device, evidence_root, stages):
    lat, lon = record["params"]["latitude"], record["params"]["longitude"]
    argv = ["xcrun", "simctl", "location", device, "set", "%s,%s" % (lat, lon)]
    MW.invoke(argv, None, evidence_root, stages, "set_location")
    return {"status": "PASS", "detail": "set location to %s,%s on %s" % (lat, lon, device)}


def _capture(record, device, evidence_root, stages):
    safe_id = _SAFE_FILENAME.sub("_", record.get("action_id") or "capture")[:80] or "capture"
    out_path = Path(evidence_root) / ("%s-capture.png" % safe_id)
    MW.invoke(["xcrun", "simctl", "io", device, "screenshot", str(out_path)],
              None, evidence_root, stages, "capture")
    return {"status": "PASS", "detail": "captured screenshot on %s" % device,
            "screenshot": MW.screenshot_identity(out_path)}


#: Ceiling for WAIT_FOR's own sleep when the record names no timeout_ms.
#: Deliberately the same 120s mobile_workflow.invoke() enforces on every
#: other action this adapter performs: _wait_for is the one handler that
#: never routes through invoke(), so without this it was the one action with
#: no deadline at all (duration_ms=999999999 with timeout_ms=500 both
#: validated clean and would have slept uninterruptibly for over 11 days).
DEFAULT_WAIT_TIMEOUT_MS = 120000


def _wait_cap_ms(record):
    """The longest this adapter will sleep for one WAIT_FOR: the record's
    own timeout_ms when it carries a usable one, else
    DEFAULT_WAIT_TIMEOUT_MS. bool is excluded explicitly because it is an
    int subclass in Python, so True would otherwise read as a 1ms cap."""
    timeout_ms = record.get("timeout_ms")
    if isinstance(timeout_ms, int) and not isinstance(timeout_ms, bool) and timeout_ms > 0:
        return timeout_ms
    return DEFAULT_WAIT_TIMEOUT_MS


def _wait_for(record, device, evidence_root, stages):
    params = record.get("params") or {}
    duration_ms = params.get("duration_ms")
    if duration_ms is None:
        # A target-based WAIT_FOR needs an accessibility/semantic tree reader
        # this driver does not have (deterministic_selector_support=False).
        return {"status": "UNSUPPORTED",
                "detail": "target-based WAIT_FOR needs an accessibility/semantic tree reader "
                          "this driver does not have; only params.duration_ms is wired"}
    cap_ms = _wait_cap_ms(record)
    if duration_ms > cap_ms:
        return {"status": "FAIL",
                "detail": "WAIT_FOR params.duration_ms=%s exceeds this driver's %sms cap; refused "
                          "rather than sleeping, since this sleep does not run through "
                          "mobile_workflow.invoke() and so is not covered by its timeout"
                          % (duration_ms, cap_ms)}
    time.sleep(duration_ms / 1000.0)
    return {"status": "PASS", "detail": "waited %sms" % duration_ms}


def _finish(record, device, evidence_root, stages):
    status = record["params"]["status"]
    return {"status": "PASS", "detail": "journey finished with status=%s (bookkeeping only, no device call)" % status}


def _need_human(record, device, evidence_root, stages):
    return {"status": "PASS", "detail": "escalated to a human: %s" % record.get("reason")}


def _impossible(record, device, evidence_root, stages):
    return {"status": "PASS", "detail": "marked impossible: %s" % record.get("reason")}


#: One handler per action this driver actually implements. describe() below
#: builds supported_actions from this dict's keys -- never a second,
#: hand-typed list -- so the self-description cannot silently drift from
#: what execute_action() really does.
ACTION_HANDLERS = {
    "OPEN_APP": _open_app,
    "DEEPLINK": _deeplink,
    "SET_PERMISSION": _set_permission,
    "SET_LOCATION": _set_location,
    "CAPTURE": _capture,
    "WAIT_FOR": _wait_for,
    "FINISH": _finish,
    "NEED_HUMAN": _need_human,
    "IMPOSSIBLE": _impossible,
}

#: One specific reason per canonical action this driver does NOT implement,
#: each naming the real gap (confirmed against `xcrun simctl --help`) rather
#: than a generic "not implemented" -- so a caller reading a result learns
#: something, and the conformance gauntlet (M3.08) can tell a real gap from
#: a bug. Kept in sync with mobile_canonical_action's action enum by
#: test_mobile_native_ios_adapter.py (ACTION_HANDLERS | UNSUPPORTED_REASONS
#: == the full 20-verb vocabulary, both directions).
UNSUPPORTED_REASONS = {
    "TAP_TARGET": "xcrun simctl has no touch-injection subcommand; no XCTest/idb touch harness is wired into this driver.",
    "LONG_PRESS_TARGET": "xcrun simctl has no touch-injection subcommand; no XCTest/idb touch harness is wired into this driver.",
    "TYPE_TEXT": "xcrun simctl has no text-input subcommand; keyboard entry needs an XCTest/idb harness, not wired here.",
    "SWIPE": "xcrun simctl has no gesture-injection subcommand.",
    "SCROLL_TO": "scrolling needs touch gestures xcrun simctl cannot perform.",
    "BACK": "iOS has no hardware back button, and xcrun simctl has no gesture-injection to simulate the in-app back affordance.",
    "HOME": "xcrun simctl has no subcommand for the home indicator/button gesture (only app launch/terminate, not device chrome).",
    "ROTATE": "xcrun simctl has no device-orientation subcommand (its `ui` subcommand covers appearance/contrast/content-size only).",
    "SET_NETWORK": "xcrun simctl has no network-condition subcommand; Xcode's Network Link Conditioner is a separate GUI tool.",
    "ASSERT_VISIBLE": "requires reading the on-screen accessibility/semantic tree, which this driver cannot do.",
    "ASSERT_STATE": "app-internal state is not observable through xcrun simctl.",
}


#: One risk_class per ACTION_HANDLERS entry (enum values are safe/reversible/
#: destructive/irreversible per docs/schema/mobile-driver-contract-v1.json --
#: not a free-text scale). describe() asserts this stays in lockstep with
#: ACTION_HANDLERS rather than silently omitting a newly added action.
_RISK_CLASSES = {
    "OPEN_APP": "reversible",
    "DEEPLINK": "reversible",
    "SET_PERMISSION": "reversible",
    "SET_LOCATION": "reversible",
    "CAPTURE": "safe",
    "WAIT_FOR": "safe",
    "FINISH": "safe",
    "NEED_HUMAN": "safe",
    "IMPOSSIBLE": "safe",
}


def describe():
    """This driver's self-description, checked against
    docs/schema/mobile-driver-contract-v1.json by main()'s "describe"
    subcommand before it is ever printed, and by the test suite. Built from
    ACTION_HANDLERS/_RISK_CLASSES, never a second hand-typed action list, so
    the description and the real implementation cannot silently drift."""
    assert set(_RISK_CLASSES) == set(ACTION_HANDLERS), \
        "_RISK_CLASSES has drifted from ACTION_HANDLERS"
    supported = sorted(ACTION_HANDLERS)
    return {
        "schema_version": "mobile-driver-contract-v1",
        "driver_id": DRIVER_ID,
        "driver_name": DRIVER_NAME,
        "action_vocabulary_ref": ACTION_VOCABULARY_REF,
        "supported_actions": supported,
        "platforms": ["ios"],
        "device_modes": ["simulator"],
        "remote_sessions_supported": False,
        "observations": ["screenshot"],
        "deterministic_selector_support": False,
        "visual_grounding_support": False,
        "risk_classes": [{"action": a, "risk_class": _RISK_CLASSES[a]} for a in supported],
    }


def execute_action(record, device, evidence_root, stages):
    """Validate `record` against mobile-canonical-action-v1 first (refusing,
    with no device call, on any problem), then dispatch to the matching
    handler, or return a structural UNSUPPORTED result -- never a silent
    no-op -- when this driver has none. A real simctl failure (nonzero exit,
    missing executable) raises mobile_workflow.Refusal from inside invoke();
    caught here and turned into its own status/detail, never swallowed.
    Creates evidence_root on demand, so a caller need not mkdir first."""
    if not isinstance(record, dict):
        # A boundary case mobile_canonical_action.hand_rules() does not
        # itself guard (it assumes a dict and would raise AttributeError on
        # e.g. a JSON list) -- caught here so a malformed record file never
        # crashes this driver.
        return {"action_id": None, "action": None, "status": "FAIL",
                "detail": "canonical action record must be a JSON object, got %s"
                          % type(record).__name__}

    action = record.get("action")
    action_id = record.get("action_id")
    problems = ACT.check(record, _ACTION_SCHEMA)
    if problems:
        return {"action_id": action_id, "action": action, "status": "FAIL",
                "detail": "invalid canonical action record: " + "; ".join(problems)}

    handler = ACTION_HANDLERS.get(action)
    if handler is None:
        return {"action_id": action_id, "action": action, "status": "UNSUPPORTED",
                "detail": UNSUPPORTED_REASONS.get(action, "no native-ios-simctl implementation for %s" % action)}

    # A caller (a str path is as valid a contract as a Path) always gets a
    # real Path from here on -- mobile_workflow.invoke() does `root / ...`,
    # which raises TypeError on a bare str.
    evidence_root = Path(evidence_root)
    try:
        evidence_root.mkdir(parents=True, exist_ok=True)
        result = handler(record, device, evidence_root, stages)
    except MW.Refusal as exc:
        result = {"status": getattr(exc, "status", "FAIL"), "detail": str(exc)}
    except OSError as exc:
        result = {"status": "NO-DATA", "detail": "evidence directory unavailable: %s" % exc}

    result.setdefault("action_id", action_id)
    result.setdefault("action", action)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("describe")
    run = sub.add_parser("run")
    run.add_argument("--record", required=True, help="path to a mobile-canonical-action-v1 JSON record")
    run.add_argument("--device", required=True, help="exact simulator UDID")
    run.add_argument("--out", required=True, help="evidence directory (created if missing)")
    args = parser.parse_args(argv)

    if args.command == "describe":
        self_description = describe()
        problems = DC.check(self_description, _DRIVER_SCHEMA)
        if problems:
            print("mobile_native_ios_adapter: FAIL: self-description violates mobile-driver-contract-v1:")
            for p in problems:
                print(" -", p)
            return 1
        print(json.dumps(self_description, indent=2, sort_keys=True))
        return 0

    try:
        record = ACT.CC.load_json(args.record, "canonical action record")
    except ACT.CC.NoData as exc:
        print("mobile_native_ios_adapter: NO-DATA: %s" % exc)
        return 2
    stages = []
    result = execute_action(record, args.device, Path(args.out), stages)
    result["stages"] = stages
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return {"PASS": 0, "FAIL": 1, "UNSUPPORTED": 1, "NO-DATA": 2}.get(result["status"], 1)


if __name__ == "__main__":
    sys.exit(main())
