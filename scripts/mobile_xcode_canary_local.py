#!/usr/bin/env python3
"""mobile_xcode_canary_local: a real, local canary for Brother's mobile
evidence pipeline (M0.04).

WHY THIS EXISTS. scripts/test_mobile_workflow.py says so itself: "These
fixtures prove orchestration and refusal behavior, not Xcode compatibility."
Every self-test in the mobile spine drives a fake xcodebuild/simctl on PATH.
This script closes that gap by driving a real Xcode project (the founder's
own iOS app, or, when none is given, the tiny checked-in fixture app at
scripts/fixtures/mobile_canary_app) through the exact same steps
scripts/mobile_workflow.py's ``run`` action performs for a real project:
build, test, resolve the built .app, install, launch, screenshot, and bind
every step to scripts/native_evidence.py's evidence record. It reuses that
existing machinery (native_evidence.record, mobile_workflow.doctor/invoke/
identity/screenshot_identity/lease) rather than re-implementing it.

GENERIC BY DESIGN, like mobile_workflow.py itself: the target repo, project,
scheme, app target, and expected test identities are all CLI parameters,
never hardcoded. This file never names any specific product; a caller with
a real iOS project passes its own path, scheme and one (or more) exact test
identities at the command line, which is data, not something this script or
any file in this repository commits.

This is a LOCAL, hand-run (or local pre-push/pre-merge hook) canary. It is
NOT wired into GitHub Actions macOS CI. Brother's own enforced cost-shield
rule (~/.claude/hooks/github_cost_wall.py) refuses any `.github/workflows/*.yml`
using a macos-* runner under any trigger, including workflow_dispatch,
without a separate founder decision naming an expected monthly cost. The
roadmap's original M0.04 text ("on macOS CI") stays BLOCKED, not done, until
the founder makes that decision; see docs/plan/MOBILE-PLATFORM-ROADMAP-1.0.18.md.

DERIVED DATA. By default no -derivedDataPath is passed, so Xcode uses its
own shared default (~/Library/Developer/Xcode/DerivedData); pass --derived-data
only when a caller's own project rules require an isolated root, and only to
a path under that shared Xcode cache, never into a repository or ~/Documents.

Exit status: 0 PASS, 1 FAIL, 2 NO-DATA. NO-DATA (no Xcode/simulator toolchain,
no simulator to target, or another mobile run holding the native tool lease)
is never a pass. This script never falls back to a fixture command in place
of the real one: every listed step runs real xcodebuild/simctl/xcresulttool,
or the run is FAIL/NO-DATA and says which step and why.
"""
import argparse
import datetime
import json
import subprocess
import sys
import time
from pathlib import Path

import native_evidence as N
from mobile_workflow import (
    Refusal, require, write_new, digest, outside, lease, invoke,
    json_output, identity, same_identity, screenshot_identity, doctor,
)

SCHEMA = "brother-mobile-xcode-canary-local-v1"
FIXTURE_PROJECT = "scripts/fixtures/mobile_canary_app/CanaryFixture.xcodeproj"
FIXTURE_SCHEME = "CanaryFixture"
FIXTURE_EXPECTED_TESTS = [
    "test://com.apple.xcode/CanaryFixture/CanaryFixtureTests/CanaryFixtureTests/testArithmeticSanity",
    "test://com.apple.xcode/CanaryFixture/CanaryFixtureTests/CanaryFixtureTests/testCanaryLabelExists",
]


def booted_simulator():
    """The first booted iOS Simulator UDID, or None. Real simctl only."""
    try:
        proc = subprocess.run(["xcrun", "simctl", "list", "devices", "booted", "-j"],
                              capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    try:
        data = json.loads(proc.stdout)
    except ValueError:
        return None
    for runtime, group in data.get("devices", {}).items():
        if "iOS" not in runtime or not isinstance(group, list):
            continue
        for entry in group:
            if isinstance(entry, dict) and entry.get("state") == "Booted" and entry.get("udid"):
                return entry["udid"]
    return None


def run_canary(repo, out, simulator_id, project=None, scheme=None, app_target=None,
                expected_tests=None, only_testing=None, derived_data=None):
    """Drive one real xcodebuild build+test+install+launch+screenshot.

    repo: the source repository this run's evidence is bound to (native_evidence
    snapshots it before and after). project/scheme/app_target/expected_tests
    default to the checked-in fixture app when omitted, so this also serves as
    Brother's own always-available proof that the pipeline still works.
    """
    repo, out = Path(repo).resolve(), Path(out).resolve()
    require(outside(out, repo), "Canary output must stay outside the source repository")
    require(not out.exists(), "Canary output already exists; preserve it and use a fresh path")
    project_path = Path(project) if project else (repo / FIXTURE_PROJECT)
    if not project_path.is_absolute():
        project_path = repo / project_path
    scheme = scheme or (FIXTURE_SCHEME if not project else None)
    app_target = app_target or scheme
    expected_tests = list(expected_tests) if expected_tests else (list(FIXTURE_EXPECTED_TESTS) if not project else None)
    require(bool(scheme), "A scheme is required for a real project")
    require(bool(expected_tests), "At least one --expected-test identity is required for a real project")
    require(project_path.is_dir(), "Project is missing: %s" % project_path)
    with lease():
        out.mkdir(parents=True)
        stages = []
        report = {"schema": SCHEMA, "status": "FAIL", "stages": stages,
                  "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  "project": str(project_path), "scheme": scheme, "app_target": app_target,
                  "requested_simulator": simulator_id,
                  "limits": ["Proves the real toolchain executes the pipeline, not application quality.",
                             "One project on one simulator is not the full device matrix.",
                             "No phone install, device erase, or store upload was performed."]}
        try:
            doctor_result = doctor(repo, out / "doctor.json")
            report["doctor"] = digest(out / "doctor.json")
            missing = [name for name in ("xcodebuild", "simctl", "xcresulttool")
                      if doctor_result["tools"][name]["status"] != "PASS"]
            require(not missing, "Toolchain unavailable: %s" % ", ".join(missing), "NO-DATA")
            raw = invoke(["xcrun", "simctl", "list", "devices", "available", "-j"], repo, out, stages, "simulators")
            devices = json_output(raw, "simctl")
            require(isinstance(devices, dict) and isinstance(devices.get("devices"), dict), "Malformed simulator list")
            found = [(runtime, d) for runtime, group in devices["devices"].items() if isinstance(group, list)
                     for d in group if isinstance(d, dict) and d.get("udid") == simulator_id]
            require(len(found) == 1, "Selected simulator is unavailable", "NO-DATA")
            runtime, device = found[0]
            report["simulator"] = {"runtime": runtime, **device}
            if device.get("state") != "Booted":
                invoke(["xcrun", "simctl", "boot", simulator_id], repo, out, stages, "boot")
            invoke(["xcrun", "simctl", "bootstatus", simulator_id, "-b"], repo, out, stages, "boot-ready", timeout=240)

            derived_data_args = ["-derivedDataPath", str(derived_data)] if derived_data else []
            destination = "platform=iOS Simulator,id=" + simulator_id
            command = (["xcodebuild", "-project", str(project_path), "-scheme", scheme,
                        "-destination", destination] + derived_data_args +
                       ["test", "-parallel-testing-enabled", "NO",
                        "-resultBundlePath", str(out / "canary.xcresult")] +
                       ["-only-testing:" + value for value in (only_testing or [])])
            native_args = argparse.Namespace(repo=str(repo), out=str(out / "native-evidence.json"),
                result_bundle=str(out / "canary.xcresult"), command=command, requirement=[], artifact=[],
                log=str(out / "test.log"), tests_json=str(out / "tests.json"), expected_test=expected_tests,
                xcrun="xcrun", timeout=1800)
            verdict, detail = N.record(native_args)
            report["native_evidence"] = digest(out / "native-evidence.json")
            stages.append({"name": "native-tests", "status": verdict, "detail": detail, "argv": command})
            require(verdict == "PASS", "Native evidence did not pass: " + "; ".join(detail), verdict)

            settings_raw = invoke(["xcodebuild", "-project", str(project_path), "-scheme", scheme,
                                    "-destination", destination] + derived_data_args +
                                   ["-showBuildSettings", "-json"], repo, out, stages, "product-path", timeout=180)
            settings = json_output(settings_raw, "build settings")
            require(isinstance(settings, list), "Build settings must be an array")
            matches = [item.get("buildSettings") for item in settings if isinstance(item, dict) and item.get("target") == app_target]
            require(len(matches) == 1 and isinstance(matches[0], dict), "Built app target is ambiguous or missing")
            values = matches[0]
            require(all(isinstance(values.get(k), str) and values[k] for k in ("TARGET_BUILD_DIR", "FULL_PRODUCT_NAME")), "Built app path is incomplete")
            product_name = values["FULL_PRODUCT_NAME"]
            require(Path(product_name).name == product_name and product_name.endswith(".app"), "Target product is not an app")
            app = Path(values["TARGET_BUILD_DIR"]) / product_name
            built = identity(app)
            report["built_app"] = built
            bundle_id = built["bundle_id"]

            invoke(["xcrun", "simctl", "install", simulator_id, str(app)], repo, out, stages, "install", timeout=180)
            installed_path = invoke(["xcrun", "simctl", "get_app_container", simulator_id, bundle_id, "app"], repo, out, stages, "installed-path").strip()
            installed = identity(installed_path)
            require(same_identity(built, installed) and built["executable"]["sha256"] == installed["executable"]["sha256"]
                    and built["plist"]["sha256"] == installed["plist"]["sha256"], "Installed app differs from the tested build")
            report["installed_app"] = installed

            invoke(["xcrun", "simctl", "launch", "--terminate-running-process", simulator_id, bundle_id], repo, out, stages, "launch")
            # ponytail: real launch->render is async; a fixed settle window beats a
            # flaky race, upgrade to polling the accessibility tree if this ever flakes.
            time.sleep(2)
            invoke(["xcrun", "simctl", "io", simulator_id, "screenshot", str(out / "preview.png")], repo, out, stages, "capture")
            report["screenshot"] = screenshot_identity(out / "preview.png")

            report["status"] = "PASS"
        except (Refusal, OSError, ValueError) as exc:
            report["status"] = getattr(exc, "status", "FAIL")
            report["reason"] = str(exc)
        finally:
            receipt = out / "receipt.json"
            write_new(receipt, report)
        return report, receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--repo", default=".",
                        help="Source repo this run's evidence binds to; defaults to the fixture's own repo")
    parser.add_argument("--out", required=True)
    parser.add_argument("--simulator-id", default=None,
                        help="Exact simulator UDID; defaults to the first booted iOS Simulator")
    parser.add_argument("--project", default=None,
                        help="Path to a real .xcodeproj/.xcworkspace; omit to run the checked-in fixture app")
    parser.add_argument("--scheme", default=None)
    parser.add_argument("--app-target", default=None, help="Defaults to --scheme")
    parser.add_argument("--expected-test", action="append", default=[],
                        help="Exact test://... nodeIdentifierURL expected to execute; repeatable")
    parser.add_argument("--only-testing", action="append", default=[],
                        help="Passed through as -only-testing:VALUE; repeatable")
    parser.add_argument("--derived-data", default=None,
                        help="Omit to use Xcode's own shared default; pass only a path under "
                             "~/Library/Developer/Xcode/DerivedData if a caller's project requires isolation")
    args = parser.parse_args(argv)
    simulator_id = args.simulator_id or booted_simulator()
    if not simulator_id:
        print("mobile_xcode_canary_local: NO-DATA: no --simulator-id given and no iOS Simulator is booted")
        return 2
    try:
        report, receipt = run_canary(args.repo, args.out, simulator_id, project=args.project,
            scheme=args.scheme, app_target=args.app_target, expected_tests=args.expected_test,
            only_testing=args.only_testing, derived_data=args.derived_data)
    except (Refusal, OSError, ValueError) as exc:
        status = getattr(exc, "status", "FAIL")
        print("mobile_xcode_canary_local: %s: %s" % (status, exc))
        return 2 if status == "NO-DATA" else 1
    print("mobile_xcode_canary_local: %s: %s" % (report["status"], report.get("reason",
          "Real xcodebuild build+test, install, launch and screenshot all verified against the real toolchain.")))
    print(receipt)
    return {"PASS": 0, "FAIL": 1, "NO-DATA": 2}[report["status"]]


if __name__ == "__main__":
    sys.exit(main())
