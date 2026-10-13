#!/usr/bin/env python3
"""mobile_xcode_canary_local: a real, local canary for Brother's mobile
evidence pipeline (M0.04, MB4).

WHAT IT RUNS. The door's own sequence, in process, never a copy of it:
mobile_workflow.doctor, then profile_init, then reference_init, then
run_workflow, against the checked-in fixture app at
scripts/fixtures/mobile_canary_app (copied into a throwaway clean git
repository under <out>/source, so a maintainer's dirty checkout cannot refuse
it) or against a real project passed with --repo/--project/--scheme and one
or more exact --expected-test identities. Its green therefore means the path a
user is taught works; a defect in run_workflow shows here. It takes no lease
of its own: each of the three workflow steps takes the per user lease in turn.

GENERIC BY DESIGN: project, scheme and expected tests are parameters, never
hardcoded product facts. Simulated devices only: the target is always an id
from `xcrun simctl list devices available`, so a physical device cannot be
selected. Output stays outside the repository; nothing leaves the machine.

LOCAL ONLY. This is a hand-run canary, not wired into GitHub Actions macOS CI
(the cost shield refuses macOS runners without a founder decision).

DERIVED DATA. --derived-data is refused by name: the workflow run uses
Xcode's own shared DerivedData and adds no override.

Exit status: 0 PASS, 1 FAIL, 2 NO-DATA. A missing platform, tool, simulator
or an unreadable simulator list prints one NO-DATA line naming the missing
piece and writes nothing. A skipped or unexecuted run is never 0. The canary
receipt path is the last line printed whenever a receipt was written.
"""
import argparse
import datetime
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, List, Optional

try:
    import mobile_workflow as W
    from mobile_workflow import Refusal, require, write_new, digest, outside, doctor
    IMPORT_ERROR = None
except ImportError as exc:  # e.g. no fcntl off macOS and Linux
    W = None
    IMPORT_ERROR = exc

SCHEMA = "brother-mobile-xcode-canary-local-v1"
PREFIX = "mobile_xcode_canary_local: "
REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = REPO_ROOT / "scripts" / "fixtures" / "mobile_canary_app"
FIXTURE_PROJECT = "scripts/fixtures/mobile_canary_app/CanaryFixture.xcodeproj"
FIXTURE_SCHEME = "CanaryFixture"
FIXTURE_EXPECTED_TESTS = [
    "test://com.apple.xcode/CanaryFixture/CanaryFixtureTests/CanaryFixtureTests/testArithmeticSanity",
    "test://com.apple.xcode/CanaryFixture/CanaryFixtureTests/CanaryFixtureTests/testCanaryLabelExists",
]
STEPS = ["doctor", "profile-init", "reference-init", "run"]
IOS_RUNTIME = re.compile(r"(?:^|[.])iOS-")
# A throwaway identity and no signing or hooks, so no global git setting decides the fixture commit.
GIT = ["git", "-c", "user.name=Brother Canary", "-c", "user.email=canary@example.invalid",
       "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null"]


def preflight(platform: str = "", which: Optional[Callable] = None) -> Optional[str]:
    """None when this is macOS with xcodebuild and xcrun on PATH, else the first missing piece."""
    platform = platform or sys.platform
    which = which or shutil.which
    if platform != "darwin":
        return "not macOS (sys.platform is %s)" % platform
    for tool in ("xcodebuild", "xcrun"):
        if not which(tool):
            return "%s is not on PATH" % tool
    return None


def available_simulators() -> Optional[List[dict]]:
    """The iOS simulators `simctl list devices available` names, or None when unreadable."""
    try:
        proc = subprocess.run(["xcrun", "simctl", "list", "devices", "available", "-j"],
                              capture_output=True, timeout=60)
        data = json.loads(proc.stdout) if proc.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("devices"), dict):
        return None
    return [{"udid": d["udid"], "name": d.get("name"), "state": d.get("state")}
            for runtime, group in sorted(data["devices"].items())
            if IOS_RUNTIME.search(runtime) and isinstance(group, list)
            for d in group if isinstance(d, dict) and isinstance(d.get("udid"), str)]


def booted_simulator():
    """The first booted available iOS simulator UDID, or None."""
    booted = [d["udid"] for d in available_simulators() or [] if d["state"] == "Booted"]
    return booted[0] if booted else None


def fixture_source(out):
    """A clean one-commit git repository holding a copy of the fixture app."""
    source = out / "source"
    shutil.copytree(FIXTURE_DIR, source)
    for args in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "canary fixture"]):
        proc = subprocess.run(GIT + ["-C", str(source)] + args, capture_output=True, text=True, timeout=60)
        require(proc.returncode == 0, "fixture repository: git %s exited %s: %s"
                % (args[0], proc.returncode, (proc.stderr or proc.stdout).strip()[:300]), "NO-DATA")
    return source


def run_canary(repo, out, simulator_id, project=None, scheme=None, app_target=None,
               expected_tests=None, only_testing=None, derived_data=None):
    """Run doctor, profile-init, reference-init and run on one simulator.

    repo None: the fixture app, copied into <out>/source. A Refusal from any
    step becomes the canary status with its message as the reason."""
    require(derived_data is None, "--derived-data is not supported; the canary runs the workflow, "
            "which uses Xcode's shared DerivedData")
    out = Path(out).resolve()
    fixture = repo is None
    if fixture:
        require(outside(out, REPO_ROOT), "Canary output must stay outside the source repository")
        require(FIXTURE_DIR.is_dir(), "Project is missing: %s" % FIXTURE_DIR)
    else:
        repo = Path(repo).resolve()
        require(outside(out, repo), "Canary output must stay outside the source repository")
    require(not out.exists(), "Canary output already exists; preserve it and use a fresh path")
    scheme = scheme or (FIXTURE_SCHEME if not project else None)
    require(bool(scheme), "A scheme is required for a real project")
    expected_tests = list(expected_tests or ()) or (list(FIXTURE_EXPECTED_TESTS) if not project else [])
    if not fixture:
        project_path = Path(project) if project else repo / FIXTURE_PROJECT
        project_path = project_path if project_path.is_absolute() else repo / project_path
        require(project_path.is_dir(), "Project is missing: %s" % project_path)
        require(project_path.resolve().is_relative_to(repo), "Project is outside the repository: %s" % project_path)
        project = str(project_path.resolve().relative_to(repo))
    out.mkdir(parents=True)
    report = {"schema": SCHEMA, "status": "FAIL", "stages": [], "steps": list(STEPS),
              "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "scheme": scheme, "app_target": app_target, "requested_simulator": simulator_id,
              "limits": ["Proves the real toolchain executes the door's sequence, not application quality.",
                         "One project on one simulator is not the full device matrix.",
                         "No phone install, device erase, or store upload was performed."]}
    step = "source"
    try:
        if fixture:
            repo = fixture_source(out)
            project = "CanaryFixture.xcodeproj"
        report["repo"], report["project"] = str(repo), project
        step = "doctor"
        doctor_result = doctor(repo, out / "doctor.json")
        report["doctor"] = digest(out / "doctor.json")
        missing = [name for name in ("xcodebuild", "simctl", "xcresulttool")
                   if doctor_result["tools"][name]["status"] != "PASS"]
        require(not missing, "Toolchain unavailable: %s" % ", ".join(missing), "NO-DATA")
        step = "profile-init"
        profile = W.profile_init(repo, out / "profile.json", project=project, scheme=scheme, simulator_id=simulator_id,
                                 expected_tests=expected_tests, only_testing=only_testing or ())
        report["profile"] = digest(out / "profile.json")
        require(app_target in (None, profile["target"]),
                "--app-target %s is not the project's app target %s" % (app_target, profile["target"]))
        step = "reference-init"
        W.reference_init(repo, out / "profile.json", out / "reference.json")
        report["reference"] = digest(out / "reference.json")
        step = "run"
        run, run_receipt = W.run_workflow(repo, out / "profile.json", out / "reference.json", out / "run")
        report["run_receipt"] = digest(run_receipt)
        for key in ("stages", "simulator", "built_app", "installed_app", "screenshot", "reason"):
            if key in run:
                report[key] = run[key]
        report["limits"] += run.get("limits", [])
        report["status"] = run["technical_status"]
        if report["status"] != "PASS":
            report["failed_step"] = step
    except (Refusal, OSError, ValueError) as exc:
        report["status"] = getattr(exc, "status", "FAIL")
        report["reason"] = str(exc)
        report["failed_step"] = step
    finally:
        receipt = out / "receipt.json"
        write_new(receipt, report)
    return report, receipt


def no_data(message):
    print(PREFIX + "NO-DATA: " + message)
    return 2


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--repo", default=None,
                        help="A real project's repository; omit to run the checked-in fixture app")
    parser.add_argument("--out", required=True, help="A new folder outside the repository")
    parser.add_argument("--simulator-id", default=None,
                        help="Exact available simulator UDID; defaults to the booted iOS simulator")
    parser.add_argument("--project", default=None, help="Path to the .xcodeproj/.xcworkspace inside --repo")
    parser.add_argument("--scheme", default=None)
    parser.add_argument("--app-target", default=None, help="Checked against the project's one app target")
    parser.add_argument("--expected-test", action="append", default=[],
                        help="Exact test://... nodeIdentifierURL expected to execute; repeatable")
    parser.add_argument("--only-testing", action="append", default=[],
                        help="Passed through as -only-testing:VALUE; repeatable")
    parser.add_argument("--derived-data", default=None, help="Refused: the workflow uses Xcode's shared DerivedData")
    args = parser.parse_args(argv)
    if args.derived_data is not None:
        print(PREFIX + "FAIL: --derived-data is not supported; the canary runs the workflow, "
              "which uses Xcode's shared DerivedData")
        return 1
    if IMPORT_ERROR is not None:
        return no_data("cannot import the workflow module (%s)" % IMPORT_ERROR)
    missing = preflight()
    if missing:
        return no_data(missing)
    devices = available_simulators()
    if devices is None:
        return no_data("the simulator list could not be read")
    if not devices:
        return no_data("no iOS simulator is installed for any runtime")
    simulator_id = args.simulator_id
    if simulator_id is not None:
        if simulator_id.lower() not in {d["udid"].lower() for d in devices}:
            return no_data("selected simulator %s is not an available simulator" % simulator_id)
    else:
        booted = [d["udid"] for d in devices if d["state"] == "Booted"]
        if not booted:
            return no_data("no iOS simulator is booted; boot one or pass --simulator-id")
        simulator_id = booted[0]
    try:
        report, receipt = run_canary(args.repo, args.out, simulator_id, project=args.project,
            scheme=args.scheme, app_target=args.app_target, expected_tests=args.expected_test,
            only_testing=args.only_testing)
    except (Refusal, OSError, ValueError) as exc:
        status = getattr(exc, "status", "FAIL")
        print(PREFIX + "%s: %s" % (status, exc))
        return 2 if status == "NO-DATA" else 1
    print(PREFIX + "%s: %s" % (report["status"], report.get("reason",
          "doctor, profile-init, reference-init and run all passed against the real toolchain")))
    print(receipt)
    return {"PASS": 0, "FAIL": 1, "NO-DATA": 2}.get(report["status"], 1)


if __name__ == "__main__":
    sys.exit(main())
