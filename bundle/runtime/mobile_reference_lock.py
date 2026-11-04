#!/usr/bin/env python3
"""WBS-30.02 Reference Lock v2: the honest partial case.

mobile_workflow.py's record_reference() already handles the FULL case: a
locally built .app, a clean git snapshot at a known source revision, and a
matching device observation, all cross-checked. That function is correct
and is not duplicated here.

This module handles the case record_reference() cannot: a real device
observation exists (an app is genuinely installed, its bundle id, version
and build are known), but no matching local build from a verified source
revision has been made. The installed artifact's identity and its source
provenance are recorded as two separate, explicitly labeled facts, never
conflated, so a reader can never infer a verified source revision from a
version/build number alone -- exactly the roadmap's own rule: "Never claim
source/artifact equivalence from ancestry alone."

Never mutates or opens the observation file's device beyond the same
recursive read mobile_workflow.app_observation already performs.
"""
import argparse
import datetime
import json
import sys

import mobile_workflow as MW

SCHEMA = "brother-mobile-reference-lock-partial-v1"


def installed_artifact_identity(observation, bundle_id, capture_method):
    """The known half: a real device observation, walked the same way
    mobile_workflow.app_observation already does (reused, not duplicated)."""
    obs = MW.app_observation(observation, bundle_id)
    return {
        "status": "observed_installed",
        "bundle_id": obs["bundle_id"],
        "version": obs["version"],
        "build": obs["build"],
        "capture": {"method": capture_method,
                    "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat()},
        "binary_sha256": None,
        "identity_strength": "metadata_only",
    }


def unresolved_source_provenance(reason=None):
    """The explicitly-unresolved half. Never omitted: an omitted field
    reads as "not checked" or "not applicable", which this is neither of."""
    return {
        "status": "UNRESOLVED",
        "verified": False,
        "source_revision": None,
        "source_build_sha256": None,
        "comparison_method": None,
        "reason": reason or (
            "No verified source revision has been built and compared "
            "byte-for-byte against the installed artifact."),
    }


def partial_reference(observation_path, bundle_id, capture_method="devicectl"):
    observation = MW.read_json(observation_path)
    record = {
        "schema": SCHEMA,
        "artifact_identity": installed_artifact_identity(observation, bundle_id, capture_method),
        "source_provenance": unresolved_source_provenance(),
        "caveat": ("Installed artifact metadata is known. Source-to-artifact mapping is "
                   "UNRESOLVED. Version/build numbers alone do not establish the source "
                   "revision."),
    }
    return record


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("observation", help="path to a device observation JSON "
                     "(e.g. xcrun devicectl device info apps --json-output <path>)")
    ap.add_argument("bundle_id")
    ap.add_argument("--capture-method", default="devicectl")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    try:
        record = partial_reference(args.observation, args.bundle_id, args.capture_method)
    except (MW.Refusal, FileNotFoundError) as exc:
        print("NO-DATA: %s" % exc)
        return 2
    text = json.dumps(record, indent=1)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
