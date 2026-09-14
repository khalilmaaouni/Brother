#!/usr/bin/env python3
"""Release State Tracker (WBS-30.09): TestFlight / release-state evidence.

Roadmap text: "TestFlight / release-state evidence. Track separately:
uploaded; processing; available; installed; accepted/released. A successful
upload is not release. A release is not product success."

Five explicit, ordered states -- never a single boolean "released" flag
anywhere in this module's public shape. Mirrors mobile_reference_lock.py's
artifact_identity/source_provenance split: separate facts recorded side by
side, never conflated into one field.

Only INSTALLED can be measured for real tonight, via the same
devicectl-observation pattern mobile_reference_lock.py already uses
(mobile_workflow.app_observation, reused not duplicated). The other four
states need App Store Connect / TestFlight API credentials this session
does not have, so each returns an honest NO-DATA naming exactly what
access would be needed -- never a guessed or inferred PASS.

Never build a device cloud or a TestFlight client here. Never infer
UPLOADED/PROCESSING/AVAILABLE/ACCEPTED_RELEASED from an INSTALLED
observation: those are separate facts owned by a separate system, and a
device having the app installed says nothing about whether it was ever
uploaded through this session's view, still less released to production.
"""
import argparse
import datetime
import json
import sys

import mobile_workflow as MW

STATE_UPLOADED = "UPLOADED"
STATE_PROCESSING = "PROCESSING"
STATE_AVAILABLE = "AVAILABLE"
STATE_INSTALLED = "INSTALLED"
STATE_ACCEPTED_RELEASED = "ACCEPTED_RELEASED"
STATES = (STATE_UPLOADED, STATE_PROCESSING, STATE_AVAILABLE, STATE_INSTALLED,
          STATE_ACCEPTED_RELEASED)

EVIDENCE_SCHEMA = "brother-release-state-evidence-v1"
RECORD_SCHEMA = "brother-release-state-record-v1"


def _state_uploaded(**kwargs):
    return "NO-DATA", {"reason": (
        "App Store Connect API access (an API key with the builds/uploads "
        "endpoint) is required to confirm an upload was accepted; no "
        "credential is configured this session.")}


def _state_processing(**kwargs):
    return "NO-DATA", {"reason": (
        "App Store Connect API access (the build processing-status "
        "endpoint) is required to confirm processing succeeded or failed; "
        "no credential is configured this session.")}


def _state_available(**kwargs):
    return "NO-DATA", {"reason": (
        "App Store Connect / TestFlight API access (the beta group or "
        "track assignment endpoint) is required to confirm a build is "
        "assigned and visible to its intended testers; no credential is "
        "configured this session.")}


def _state_installed(observation=None, bundle_id=None, capture_method="devicectl", **kwargs):
    """The one real state: reuses mobile_workflow.app_observation, the same
    walk mobile_reference_lock.py already performs on a devicectl
    --json-output observation. PASS only when a real observation was
    actually supplied and matched exactly one app; NO-DATA otherwise,
    never inferred or guessed."""
    if observation is None:
        return "NO-DATA", {"reason": (
            "No device observation was supplied (xcrun devicectl device "
            "info apps --json-output <path>).")}
    if bundle_id is None:
        return "NO-DATA", {"reason": "No bundle id was supplied to match in the observation."}
    try:
        obs = MW.app_observation(observation, bundle_id)
    except MW.Refusal as exc:
        return "NO-DATA", {"reason": "Device observation did not match: %s" % exc}
    return "PASS", {
        "reason": "real device observation matched",
        "bundle_id": obs["bundle_id"],
        "version": obs["version"],
        "build": obs["build"],
        "capture": {"method": capture_method,
                    "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat()},
    }


def _state_accepted_released(**kwargs):
    return "NO-DATA", {"reason": (
        "App Store Connect API access (the phased-release / rollout-"
        "percentage status endpoint, or store listing status) is required "
        "to confirm production release and acceptance; no credential is "
        "configured this session.")}


STATE_FUNCS = {
    STATE_UPLOADED: _state_uploaded,
    STATE_PROCESSING: _state_processing,
    STATE_AVAILABLE: _state_available,
    STATE_INSTALLED: _state_installed,
    STATE_ACCEPTED_RELEASED: _state_accepted_released,
}


def state_evidence(state_name, **kwargs):
    """The WBS-30.09 seam: one function per state, dispatched by name.
    Refuses an unknown state name rather than silently picking one (mirrors
    device_matrix.physical_device_evidence's adapter dispatch)."""
    if state_name not in STATE_FUNCS:
        raise ValueError("unknown state %r, must be one of %s" % (state_name, STATES))
    verdict, evidence = STATE_FUNCS[state_name](**kwargs)
    return {
        "schema": EVIDENCE_SCHEMA,
        "state": state_name,
        "verdict": verdict,
        "evidence": evidence,
        "observed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }


def compose_release_record(evidence_by_state=None):
    """All five states, side by side, each independently verdicted. Most
    states are NO-DATA tonight since only INSTALLED has a live signal; a
    missing entry is filled with an honest 'not supplied' NO-DATA rather
    than silently omitted -- an omitted field reads as 'not applicable',
    which this is not.

    Structural guarantee: reading one state's verdict never reads
    another's -- INSTALLED=PASS never implies ACCEPTED_RELEASED=PASS. The
    roadmap's own "a successful upload is not release" rule is enforced by
    this record's shape (five independent fields), not by convention."""
    evidence_by_state = evidence_by_state or {}
    states = {}
    for name in STATES:
        record = evidence_by_state.get(name)
        if record is None:
            record = {
                "schema": EVIDENCE_SCHEMA,
                "state": name,
                "verdict": "NO-DATA",
                "evidence": {"reason": "No evidence was supplied for this state."},
                "observed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            }
        states[name] = record
    return {
        "schema": RECORD_SCHEMA,
        "states": states,
        "order": list(STATES),
        "caveat": (
            "Five independent states, each verdicted on its own evidence. A "
            "PASS on one state is never evidence for another: an upload "
            "accepted is not processed, a build installed on one device is "
            "not released to production. Read every field; never collapse "
            "this record into one boolean."),
        "observed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--observation", default=None,
                     help="path to a device observation JSON for the INSTALLED state "
                          "(xcrun devicectl device info apps --json-output <path>)")
    ap.add_argument("--bundle-id", default=None,
                     help="bundle id to match in the observation, for the INSTALLED state")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)

    observation = None
    if args.observation:
        try:
            observation = MW.read_json(args.observation)
        except MW.Refusal as exc:
            print("NO-DATA: %s" % exc)
            return 2

    evidence_by_state = {
        STATE_INSTALLED: state_evidence(
            STATE_INSTALLED, observation=observation, bundle_id=args.bundle_id),
    }
    for name in STATES:
        if name != STATE_INSTALLED:
            evidence_by_state[name] = state_evidence(name)

    record = compose_release_record(evidence_by_state)
    text = json.dumps(record, indent=1)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
