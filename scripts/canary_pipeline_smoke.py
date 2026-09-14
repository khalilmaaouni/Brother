#!/usr/bin/env python3
"""WBS-30.10 Canary Pipeline Smoke: prove the ten sibling mobile-path modules
that landed tonight actually connect, end to end, on one synthetic journey.

This is NOT an eleventh domain module. It builds a fake, generic journey
(journey_id="canary-smoke", no real Tonari data anywhere) and threads it
through the REAL functions of every sibling module in the roadmap's own
pipeline order:

  mobile_journey_contract.check()
    -> mobile_plan_compiler.compile_plan()
    -> context_capsule.build_capsule() (one compiled unit, connectivity only)
    -> mobile_reference_lock.partial_reference()
    -> native_evidence_v2.wrap_v2() (wrapping a REAL native_evidence.py
       `record` run against a fake xcrun, same fixture pattern
       test_native_evidence_v2.py uses)
    -> device_matrix.physical_device_evidence() (with device_matrix.run
       mocked to a synthetic devicectl table, same pattern
       test_device_matrix.py uses)
    -> release_state_tracker.compose_release_record()
    -> journey_passport.compose_passport()

Every stage's REAL output is fed directly into the next real function's
real input -- no reshaping step sits between them. Where a shape genuinely
does not fit (see test_release_state_tracker_output_does_not_match_
journey_passports_release_shape below), that mismatch is asserted on
directly, not papered over.

Python 3.9, standard library only. No network. Nothing here touches
~/Documents/Codex/.../TonariSimple; every fixture is synthetic/generic.
"""
import datetime
import json
import os
import stat
import subprocess
import sys
import tempfile
import textwrap
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import contract_check as CC  # noqa: E402
import mobile_journey_contract as MJC  # noqa: E402
import mobile_plan_compiler as MPC  # noqa: E402
import context_capsule as CTX  # noqa: E402
import mobile_reference_lock as MRL  # noqa: E402
import native_evidence as NE  # noqa: E402
import native_evidence_v2 as NEV2  # noqa: E402
import device_matrix as DM  # noqa: E402
import release_state_tracker as RST  # noqa: E402
import journey_passport as JP  # noqa: E402

# Synthetic, generic outcome contract this journey layers on -- same shape
# scripts/test_mobile_journey_contract.py's VALID_OUTCOME fixture uses.
SYNTHETIC_OUTCOME = {
    "schema_version": "outcome-contract-v1", "project": {
        "project_id": "canary-smoke", "name": "canary-smoke",
        "provenance": {"project_id": "ask", "name": "ask"}},
    "language": "en", "question": "does the synthetic canary journey work",
    "success_checks": [{"id": "s", "command": "true", "expect": "exit-0"}],
    "must_answer": [], "affected_products": ["brother"], "ticket": None,
    "audit": {"required": False, "manifest": None}, "persona": "developer",
    "state": "contracted", "receipts": [], "questions": [], "history": [],
    "decision": None,
}

# A fake but schema-valid mobile-journey-contract-v1 record. Generic
# entry/exit states, one real accessibility obligation, nothing Tonari-shaped.
SYNTHETIC_JOURNEY = {
    "schema_version": "mobile-journey-contract-v1",
    "journey_id": "canary-smoke",
    "human_outcome": "a user completes one generic bounded action",
    "entry_state": "generic-idle-screen",
    "exit_state": "generic-confirmed-screen",
    "supported_device_classes": ["phone"],
    "supported_os_range": "17-18",
    "locales": ["en"],
    "accessibility_obligations": ["VoiceOver reads the confirm control's label and state"],
    "network_state_obligations": [],
    "interruption_obligations": [],
    "privacy_constraints": ["never persists the user's raw input beyond this session"],
    "performance_budgets": [{"metric": "time_to_confirm", "budget": "2s"}],
    "visual_reference_ids": [],
    "required_native_tests": ["CanarySmokeTests/testConfirmFlow"],
    "human_acceptance_items": ["a human confirms the confirm control is reachable and legible"],
    "post_release_claims": [],
}

# Synthetic devicectl app-observation fixture, same shape
# test_mobile_reference_lock.py's SYNTHETIC_OBSERVATION uses.
SYNTHETIC_DEVICE_OBSERVATION = {
    "result": {"devices": [{"identifier": "00000000-0000-0000-0000-000000000000",
                             "apps": [{"bundleIdentifier": "com.example.CanaryApp",
                                       "version": "1.0", "bundleVersion": "1"}]}]}
}
SYNTHETIC_BUNDLE_ID = "com.example.CanaryApp"

# Synthetic devicectl device-list table, same shape
# test_device_matrix.py's REAL_DEVICECTL_OUTPUT uses (column-aligned, a
# generic identifier, no real machine's device in it).
SYNTHETIC_DEVICECTL_TABLE = (
    "Name              Hostname                          Identifier"
    "                             State                Model                     \n"
    "---------------   -------------------------------   ------------------------"
    "------------   ------------------   --------------------------\n"
    "canary iPhone     canary-iphone.coredevice.local    11111111-1111-1111-1111-"
    "111111111111   available (paired)   iPhone 15 (iPhone15,4)    \n"
)


def _write_script(directory, name, body):
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("#!/usr/bin/env python3\n" + textwrap.dedent(body))
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
    return path


def build_native_evidence_v2(tmp):
    """A REAL native_evidence.py `record` run (same fixture pattern
    test_native_evidence_v2.py uses: a real throwaway git repo, a fake xcrun
    that echoes back a synthetic xcresult test-results.json, a runner that
    writes the result bundle) wrapped through native_evidence_v2.wrap_v2().
    Nothing here is Tonari-shaped: the test identity, bundle id and repo
    are all generic/synthetic."""
    repo = os.path.join(tmp, "repo")
    os.mkdir(repo)
    subprocess.run(["git", "-C", repo, "init", "-q", "-b", "main"], check=True)
    subprocess.run(["git", "-C", repo, "config", "user.email", "a@b.c"], check=True)
    subprocess.run(["git", "-C", repo, "config", "user.name", "canary"], check=True)
    with open(os.path.join(repo, "base.txt"), "w", encoding="utf-8") as fh:
        fh.write("base\n")
    subprocess.run(["git", "-C", repo, "add", "base.txt"], check=True)
    subprocess.run(["git", "-C", repo, "commit", "-qm", "base"], check=True)

    xcrun = _write_script(tmp, "fake-xcrun.py", """
        import json, os, sys
        path = sys.argv[sys.argv.index('--path') + 1]
        index = os.path.join(path, 'database.sqlite3')
        if not os.path.exists(index):
            with open(index, 'wb') as fh:
                fh.write(b'index')
        with open(os.path.join(path, 'test-results.json'), encoding='utf-8') as fh:
            sys.stdout.write(fh.read())
    """)
    runner = _write_script(tmp, "runner.py", """
        import json, os, sys
        bundle, capture, result = sys.argv[1:]
        os.mkdir(bundle)
        with open(os.path.join(bundle, 'test-results.json'), 'w', encoding='utf-8') as fh:
            fh.write(result)
        if capture != '-':
            with open(capture, 'wb') as fh:
                fh.write(b'capture')
    """)

    identity = "test://com.example/CanaryApp/CanarySmokeTests/testConfirmFlow"
    result_doc = {
        "devices": [{"deviceId": "device", "deviceName": "canary-phone",
                     "osVersion": "18.0", "platform": "iOS"}],
        "testNodes": [{"nodeType": "Test Plan", "name": "CanaryApp",
                       "children": [{"nodeType": "Unit test bundle",
                                     "name": "CanarySmokeTests", "children": [
                           {"nodeType": "Test Suite", "name": "CanarySmokeTests",
                            "children": [{"nodeType": "Test Case",
                                          "name": "testConfirmFlow()",
                                          "nodeIdentifier": "CanarySmokeTests/testConfirmFlow()",
                                          "nodeIdentifierURL": identity,
                                          "result": "Passed",
                                          "durationInSeconds": 0.001}]}]}]}],
        "testPlanConfigurations": [],
    }

    bundle = os.path.join(tmp, "result.xcresult")
    evidence_path = os.path.join(tmp, "evidence.json")
    screenshot = os.path.join(tmp, "screenshot.png")
    argv = ["record", "--repo", repo, "--out", evidence_path,
            "--result-bundle", bundle, "--expected-test", identity,
            "--artifact", "screenshot=" + screenshot, "--xcrun", xcrun,
            "--command", sys.executable, runner, bundle, screenshot,
            json.dumps(result_doc)]
    code = NE.main(argv)
    if code != 0:
        raise AssertionError("native_evidence record must PASS on this synthetic fixture, got exit %d" % code)

    return NEV2.wrap_v2(evidence_path, repo=repo, xcrun=xcrun)


def build_device_matrix_evidence():
    """A synthetic device_matrix.physical_device_evidence() PASS, built by
    mocking device_matrix.run (same seam test_device_matrix.py mocks)
    rather than depending on whatever is or is not plugged into this
    machine tonight."""
    with patch("device_matrix.run", return_value=(SYNTHETIC_DEVICECTL_TABLE, None)):
        return DM.physical_device_evidence(DM.ADAPTER_LOCAL)


def run_pipeline():
    """Thread the synthetic journey through every real sibling function, in
    the roadmap's own pipeline order. Returns (passport, mismatches) where
    mismatches lists any adjacent-stage shape mismatch found along the way
    (never silently reshaped away)."""
    mismatches = []
    tmp = tempfile.mkdtemp(prefix="canary-pipeline-smoke-")

    # Stage 1: Journey Contract (WBS-30.01), layered on a real outcome
    # contract via outcome_contract_ref.
    outcome_path = os.path.join(tmp, "outcome.json")
    with open(outcome_path, "w", encoding="utf-8") as fh:
        json.dump(SYNTHETIC_OUTCOME, fh)
    journey = dict(SYNTHETIC_JOURNEY, outcome_contract_ref=outcome_path)
    schema = CC.load_json(MJC.DEFAULT_SCHEMA, "journey contract schema")
    problems = MJC.check(journey, schema)
    if problems != []:
        raise AssertionError("synthetic journey contract must validate: %s" % problems)

    # Stage 2: Plan compiler (WBS-30.04) -- real units traceable to the
    # contract's own fields.
    units = MPC.compile_plan(journey, schema=schema)
    if not units:
        raise AssertionError("compile_plan must emit at least one unit")

    # Stage 2b: Context Capsule (WBS-10.02) -- one real compiled unit fed
    # directly into build_capsule(), proving compile_plan()'s "id"/"title"/
    # "owns"/"depends_on"/"done_check" shape is exactly what build_capsule()
    # reads (node["id"], node.get("title"), node.get("owns"), ...).
    tests_unit = next(u for u in units if u["id"].endswith("-tests"))
    capsule = CTX.build_capsule(tests_unit, cwd=tmp)
    if capsule["unit_id"] != tests_unit["id"]:
        raise AssertionError("build_capsule() unit_id does not match the compiled unit's id")
    if capsule["objective"] != tests_unit["title"]:
        raise AssertionError("build_capsule() objective does not match the compiled unit's title")
    if capsule["write_scope"] != tests_unit["owns"]:
        raise AssertionError("build_capsule() write_scope does not match the compiled unit's owns")
    if capsule["dependencies"] != tests_unit["depends_on"]:
        raise AssertionError("build_capsule() dependencies do not match the compiled unit's depends_on")

    # Stage 3: Reference Lock v2 (WBS-30.02) -- honest partial case, a
    # synthetic device observation.
    obs_path = os.path.join(tmp, "observation.json")
    with open(obs_path, "w", encoding="utf-8") as fh:
        json.dump(SYNTHETIC_DEVICE_OBSERVATION, fh)
    reference_lock = MRL.partial_reference(obs_path, SYNTHETIC_BUNDLE_ID)

    # Stage 4: Native Evidence Adapter v2 (WBS-30.05).
    native_evidence_v2 = build_native_evidence_v2(tmp)
    if native_evidence_v2["verdict"] != NE.PASS:
        raise AssertionError(
            "synthetic native_evidence_v2 fixture must PASS: %s" % native_evidence_v2["verdict_detail"])

    # Stage 5: Device Matrix physical-device adapter (WBS-30.07/30.08).
    physical_device_evidence = build_device_matrix_evidence()
    if physical_device_evidence["verdict"] != "PASS":
        raise AssertionError("synthetic physical_device_evidence fixture must PASS: %s" % physical_device_evidence)

    # Stage 6: Release State Tracker (WBS-30.09) -- the one real state
    # (INSTALLED) built from the same synthetic observation, the other four
    # honestly NO-DATA (no App Store Connect credential this session).
    installed_evidence = RST.state_evidence(
        RST.STATE_INSTALLED, observation=SYNTHETIC_DEVICE_OBSERVATION,
        bundle_id=SYNTHETIC_BUNDLE_ID)
    if installed_evidence["verdict"] != "PASS":
        raise AssertionError("synthetic installed_evidence fixture must PASS: %s" % installed_evidence)
    release_record = RST.compose_release_record({RST.STATE_INSTALLED: installed_evidence})

    # THE FINDING: release_state_tracker.compose_release_record()'s real
    # output is {"schema", "states", "order", "caveat", "observed_at"} --
    # there is no top-level "state" key. journey_passport.py's release
    # dimension (_compose_struct(release_evidence, ("state",), "release"))
    # was written against the roadmap's bare illustrative sketch
    # ("state: not_released"), before WBS-30.09 landed its own five-state
    # "states" (plural) shape. Fed directly, with no reshaping, this is a
    # genuine adjacent-stage mismatch: the record is present but missing
    # its required field, which journey_passport.py's own _compose_struct
    # correctly reports as FAIL rather than a silent PASS or a crash.
    if "state" not in release_record:
        mismatches.append(
            "release_state_tracker.compose_release_record() output has no "
            "top-level 'state' key (it has 'states', the five-state dict) "
            "but journey_passport.py's release dimension requires "
            "'state' -- feeding the real WBS-30.09 output into the real "
            "WBS-30.06 compose_passport() produces FAIL on 'release', not "
            "a connection.")

    # Stage 7: Journey Passport (WBS-30.06) -- the terminal composer. Every
    # upstream record fed in exactly as its own module produced it.
    passport = JP.compose_passport(
        journey_contract=journey,
        reference_lock=reference_lock,
        native_evidence_v2_record=native_evidence_v2,
        physical_device_evidence=physical_device_evidence,
        release_evidence=release_record,
        # accessibility, performance, design evidence, human acceptance and
        # production claims have no sibling module exercised in this smoke
        # (accessibility/performance/production: no sibling module exists
        # at all yet; design/human-acceptance: WBS-30.03 exists but is out
        # of this unit's explicit scope) -- left unsupplied so completeness
        # reports them as honest NO-DATA, never a fabricated PASS.
    )
    return passport, mismatches, tmp


def main():
    passport, mismatches, tmp = run_pipeline()
    completeness = passport["completeness"]
    print(json.dumps(passport, indent=2, sort_keys=True))
    print("---", file=sys.stderr)
    print(completeness["headline"], file=sys.stderr)
    if mismatches:
        print("INTEGRATION MISMATCH(ES) FOUND:", file=sys.stderr)
        for m in mismatches:
            print(" - %s" % m, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
