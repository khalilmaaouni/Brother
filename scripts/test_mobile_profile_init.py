"""MB2.a: profile-init writes a valid run profile from the project and the
simulator list, and refuses by name whenever a fact is missing or ambiguous.

Fake xcodebuild and xcrun on PATH read the case from FIXTURE_ROOT/case.json
and log every argv, so the read only property is checked, not assumed.
These fixtures prove orchestration and refusal behaviour, not Xcode itself.
"""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mobile_project_profile as P
import mobile_workflow as M

SIM = "11111111-2222-3333-4444-555555555555"
SIM2 = "66666666-7777-8888-9999-aaaaaaaaaaaa"
TEST = "test://com.apple.xcode/Sample/SampleTests/RoomTests/testOpen"
RUNTIME = "com.apple.CoreSimulator.SimRuntime.iOS-26-4"
RUNTIME2 = "com.apple.CoreSimulator.SimRuntime.iOS-18-6"

TOOL = r'''#!/usr/bin/env python3
import json,os,pathlib,sys
args=sys.argv[1:];root=pathlib.Path(os.environ['FIXTURE_ROOT']);name=pathlib.Path(sys.argv[0]).name
with open(root/'argv.log','a') as fh: fh.write(json.dumps([name]+args)+'\n')
case=json.loads((root/'case.json').read_text())
if name=='xcodebuild':
 if '-list' in args:
  if 'list_raw' in case: print(case['list_raw']);sys.exit(0)
  key='workspace' if '-workspace' in args else 'project'
  print(json.dumps({key:{'name':'Sample','schemes':case['schemes'],'configurations':['Debug','Release']}}));sys.exit(0)
 if '-showBuildSettings' in args: print(json.dumps(case['settings']));sys.exit(0)
if args[:4]==['simctl','list','devices','available']: print(json.dumps({'devices':case['devices']}));sys.exit(0)
if args[:3]==['xcresulttool','get','test-results']:
 if case.get('xcresult_fail'): sys.stderr.write('xcresult unreadable fixture\n');sys.exit(1)
 print((pathlib.Path(args[args.index('--path')+1])/'tests.json').read_text());sys.exit(0)
print('unexpected fixture command',args);sys.exit(99)
'''


def app_row(target="Sample", product="Sample.app", **overrides):
    settings = {"FULL_PRODUCT_NAME": product, "PRODUCT_BUNDLE_IDENTIFIER": "example." + target,
                "MARKETING_VERSION": "1.0", "CURRENT_PROJECT_VERSION": "1"}
    settings.update(overrides)
    return {"target": target, "buildSettings": settings}


class ProfileInitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="brother-mobile-profile-init-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.repo = self.root / "repo"; self.repo.mkdir()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        (self.repo / "Sample.xcodeproj").mkdir()
        (self.repo / "Sample.xcodeproj/project.pbxproj").write_text("fixture")
        self.git("add", "."); self.git("commit", "-qm", "base")
        self.bin = self.root / "bin"; self.bin.mkdir()
        for name in ("xcodebuild", "xcrun"):
            path = self.bin / name; path.write_text(TOOL); path.chmod(0o755)
        self.env = patch.dict(os.environ, {"PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
                                           "FIXTURE_ROOT": str(self.root)})
        self.env.start(); self.addCleanup(self.env.stop)
        self.case = {"schemes": ["Sample"],
                     "devices": {RUNTIME: [{"udid": SIM, "name": "iPhone 17", "state": "Booted"}]},
                     "settings": [app_row(), {"target": "SampleTests", "buildSettings": {"FULL_PRODUCT_NAME": "SampleTests.xctest"}}]}
        self.out = self.root / "profile.json"

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.repo), *args], text=True, stderr=subprocess.DEVNULL)

    def init(self, **kwargs):
        (self.root / "case.json").write_text(json.dumps(self.case))
        kwargs.setdefault("expected_tests", [TEST])
        return M.profile_init(self.repo, kwargs.pop("out", self.out), **kwargs)

    def refused(self, status, pattern, **kwargs):
        with self.assertRaises(M.Refusal) as raised:
            self.init(**kwargs)
        self.assertEqual(raised.exception.status, status, str(raised.exception))
        self.assertRegex(str(raised.exception), pattern)
        self.assertFalse(Path(kwargs.get("out", self.out)).exists(), "a refusal wrote the profile")
        return str(raised.exception)

    def argv_log(self):
        path = self.root / "argv.log"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    # REQ-MB2-01
    def test_happy_path_writes_exactly_the_reported_facts(self):
        profile = self.init()
        expected = {"schema": M.PROFILE_SCHEMA, "project": "Sample.xcodeproj", "scheme": "Sample", "target": "Sample",
                    "configuration": "Debug", "simulator_id": SIM, "bundle_id": "example.Sample", "version": "1.0",
                    "build": "1", "expected_tests": [TEST]}
        self.assertEqual(profile, expected)
        self.assertEqual(json.loads(self.out.read_text()), expected)
        M.validate_profile(json.loads(self.out.read_text()), self.repo)
        settings_call = [a for a in self.argv_log() if "-showBuildSettings" in a][0]
        self.assertIn("platform=iOS Simulator,id=" + SIM, settings_call)

    def test_same_actor_twice_gives_identical_profile(self):
        first = self.init()
        self.assertEqual(self.init(out=self.root / "second.json"), first)

    def test_only_testing_included_only_when_given_and_validated(self):
        profile = self.init(only_testing=["SampleTests/RoomTests"])
        self.assertEqual(profile["only_testing"], ["SampleTests/RoomTests"])
        self.refused("FAIL", "test filters", out=self.root / "bad.json", only_testing=["Sample Tests;rm"])

    def test_explicit_choices_are_used_when_listed(self):
        self.case["schemes"] = ["Sample", "Other"]
        self.case["devices"] = {RUNTIME: [{"udid": SIM, "name": "iPhone 17", "state": "Shutdown"},
                                          {"udid": SIM2, "name": "iPhone Air", "state": "Shutdown"}]}
        profile = self.init(scheme="Other", simulator_name="iPhone Air")
        self.assertEqual((profile["scheme"], profile["simulator_id"]), ("Other", SIM2))
        self.assertEqual(self.init(out=self.root / "b.json", scheme="Sample", simulator_id=SIM2)["simulator_id"], SIM2)

    def test_xcresult_supplies_passed_leaves_only(self):
        bundle = self.root / "tests.xcresult"; bundle.mkdir()
        other = "test://com.apple.xcode/Sample/SampleTests/RoomTests/testClose"
        (bundle / "tests.json").write_text(json.dumps({"testNodes": [
            {"nodeType": "Test Case", "nodeIdentifierURL": TEST, "result": "Passed"},
            {"nodeType": "Test Case", "nodeIdentifierURL": other, "result": "Failed"}]}))
        profile = self.init(expected_tests=(), xcresult=str(bundle))
        self.assertEqual(profile["expected_tests"], [TEST])

    # REQ-MB2-02
    def test_two_projects_refuse_naming_both(self):
        (self.repo / "Other.xcworkspace").mkdir()
        message = self.refused("NO-DATA", "--project")
        self.assertIn("Sample.xcodeproj", message); self.assertIn("Other.xcworkspace", message)

    def test_two_schemes_refuse_naming_both(self):
        self.case["schemes"] = ["Sample", "SampleDev"]
        message = self.refused("NO-DATA", "--scheme")
        self.assertIn("Sample", message); self.assertIn("SampleDev", message)

    def test_unlisted_scheme_refuses(self):
        self.refused("NO-DATA", "Nope", scheme="Nope")

    def test_two_booted_simulators_refuse_naming_both(self):
        self.case["devices"] = {RUNTIME: [{"udid": SIM, "name": "iPhone 17", "state": "Booted"},
                                          {"udid": SIM2, "name": "iPhone Air", "state": "Booted"}]}
        message = self.refused("NO-DATA", "simulator")
        self.assertIn(SIM, message); self.assertIn(SIM2, message)

    def test_no_booted_simulator_refuses_listing_candidates(self):
        self.case["devices"][RUNTIME][0]["state"] = "Shutdown"
        self.assertIn(SIM, self.refused("NO-DATA", "booted"))

    def test_one_name_on_two_runtimes_refuses_with_runtime_and_id(self):
        self.case["devices"] = {RUNTIME: [{"udid": SIM, "name": "iPhone 16", "state": "Shutdown"}],
                                RUNTIME2: [{"udid": SIM2, "name": "iPhone 16", "state": "Shutdown"}]}
        message = self.refused("NO-DATA", "iPhone 16", simulator_name="iPhone 16")
        for value in (SIM, SIM2, RUNTIME, RUNTIME2):
            self.assertIn(value, message)

    def test_simulator_id_malformed_or_unlisted_refuses(self):
        self.refused("FAIL", "UUID", simulator_id="booted")
        self.refused("NO-DATA", "not listed", simulator_id=SIM2)

    def test_non_ios_runtime_is_not_a_candidate(self):
        self.case["devices"] = {"com.apple.CoreSimulator.SimRuntime.watchOS-11-0": [{"udid": SIM, "name": "Watch", "state": "Booted"}]}
        self.refused("NO-DATA", "no simulator is available for any installed iOS runtime")

    def test_two_app_targets_refuse_naming_both(self):
        self.case["settings"] = [app_row(), app_row("Widget", "Widget.app")]
        message = self.refused("NO-DATA", "app target")
        self.assertIn("Sample", message); self.assertIn("Widget", message)

    # REQ-MB2-03
    def test_no_project_is_no_data_with_next_step(self):
        self.git("rm", "-rq", "Sample.xcodeproj")
        self.refused("NO-DATA", "no Xcode project or workspace found under the repository")

    def test_no_simulator_is_no_data(self):
        self.case["devices"] = {}
        self.refused("NO-DATA", "no simulator is available for any installed iOS runtime")

    def test_empty_scheme_list_is_no_data(self):
        self.case["schemes"] = []
        self.refused("NO-DATA", "scheme")

    def test_empty_version_setting_is_no_data_naming_it(self):
        self.case["settings"] = [app_row(MARKETING_VERSION="")]
        self.refused("NO-DATA", "MARKETING_VERSION")

    def test_unresolved_variable_setting_is_no_data_naming_it(self):
        self.case["settings"] = [app_row(CURRENT_PROJECT_VERSION="$(MARKETING_BUILD)")]
        self.refused("NO-DATA", "CURRENT_PROJECT_VERSION")
        self.case["settings"] = [app_row(MARKETING_VERSION="$(MARKETING_VERSION)")]
        self.refused("NO-DATA", "MARKETING_VERSION")

    def test_no_test_list_is_no_data_and_writes_nothing(self):
        self.refused("NO-DATA", "profile-init does not run your tests and does not guess identities", expected_tests=())

    def test_bad_test_identities_refuse(self):
        self.refused("FAIL", "test://", expected_tests=["SampleTests/testOpen"])
        self.refused("FAIL", "unique", expected_tests=[TEST, TEST])

    def test_unreadable_result_bundle_carries_the_tool_message(self):
        self.case["xcresult_fail"] = True
        bundle = self.root / "broken.xcresult"; bundle.mkdir()
        self.refused("FAIL", "xcresult unreadable fixture", expected_tests=(), xcresult=str(bundle))

    def test_corrupt_tool_output_fails(self):
        self.case["list_raw"] = "Information about project Sample: not json"
        self.refused("FAIL", "malformed JSON")
        del self.case["list_raw"]
        self.case["settings"] = [app_row(), "not an object"]
        self.refused("FAIL", "non object")

    def test_configuration_with_space_refuses(self):
        self.refused("FAIL", "configuration", configuration="Debug Local")

    def test_main_maps_no_data_to_exit_2_and_pass_to_0(self):
        (self.root / "case.json").write_text(json.dumps(self.case))
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = M.main(["profile-init", "--repo", str(self.repo), "--out", str(self.out)])
        self.assertEqual(code, 2); self.assertIn("NO-DATA", buffer.getvalue())
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = M.main(["profile-init", "--repo", str(self.repo), "--out", str(self.out), "--expected-test", TEST])
        self.assertEqual(code, 0)
        lines = buffer.getvalue().strip().splitlines()
        self.assertEqual(lines[-2:], ["mobile_workflow: PASS: profile written", str(self.out)])

    # REQ-MB2-04
    def test_converter_refuses_each_missing_fact(self):
        good = {"schema_version": "mobile-project-profile-v1", "platforms": ["ios"], "build_systems": ["xcodebuild"], "confidence": "low"}
        self.assertEqual(P.to_run_profile_seed(good, "Sample.xcodeproj"), {"schema": M.PROFILE_SCHEMA, "project": "Sample.xcodeproj"})
        for key, value, word in (("platforms", ["android"], "ios"), ("build_systems", ["spm"], "xcodebuild"),
                                 ("confidence", "none", "confidence"), ("schema_version", "other", "mobile-project-profile-v1")):
            with self.assertRaisesRegex(ValueError, word):
                P.to_run_profile_seed(dict(good, **{key: value}), "Sample.xcodeproj")
        self.assertIn("brother-mobile-profile-v1", P.__doc__)
        self.assertIn("mobile-project-profile-v1", P.__doc__)
        self.assertIn("to_run_profile_seed", P.__doc__)

    def test_profile_init_routes_through_detection(self):
        android = {"schema_version": "mobile-project-profile-v1", "platforms": ["android"], "build_systems": ["gradle"], "confidence": "low"}
        with patch.object(P, "detect", return_value=android) as detect:
            self.refused("NO-DATA", "ios")
        detect.assert_called_once()
        self.assertEqual(Path(detect.call_args[0][0]).resolve(), self.repo)

    # REQ-MB2-05
    def test_output_inside_repo_or_existing_refuses_and_writes_nothing(self):
        inside = self.repo / "profile.json"
        self.refused("FAIL", "outside", out=inside)
        self.out.write_text("keep")
        with self.assertRaisesRegex(M.Refusal, "exists"):
            self.init()
        self.assertEqual(self.out.read_text(), "keep")
        self.assertEqual(self.argv_log(), [], "output refusals must precede every tool call")

    # REQ-MB2-06
    def test_doctor_and_profile_init_share_one_walk(self):
        deep = self.repo
        for depth in range(1, 8):
            deep = deep / ("d%d" % depth)
            (deep / ("P%d.xcodeproj" % depth)).mkdir(parents=True)
        (self.repo / "node_modules/Skip.xcodeproj").mkdir(parents=True)
        (self.repo / "Sample.xcodeproj/project.xcworkspace").mkdir()
        found = M.find_projects(self.repo)
        self.assertEqual(found[0], "Sample.xcodeproj")
        self.assertIn(os.path.join("d1", "d2", "d3", "d4", "d5", "P5.xcodeproj"), found)
        self.assertNotIn(os.path.join("d1", "d2", "d3", "d4", "d5", "d6", "P6.xcodeproj"), found)
        self.assertFalse(any("node_modules" in p or "project.xcworkspace" in p for p in found))
        with patch.object(M.subprocess, "run", side_effect=OSError("no tools in this case")):
            listing = M.doctor(self.repo, self.root / "doctor.json")["projects"]
        self.assertEqual(listing, found)

    def test_one_parser_accepts_the_new_command_line(self):
        args = M.build_parser().parse_args(["profile-init", "--repo", "R", "--out", "F", "--project", "A.xcodeproj",
                                            "--scheme", "S", "--configuration", "Release", "--simulator-name", "iPhone 17",
                                            "--expected-test", TEST, "--expected-test", TEST + "2", "--xcresult", "X",
                                            "--only-testing", "T/A", "--only-testing", "T/B"])
        self.assertEqual((args.action, args.expected_test, args.only_testing, args.simulator_name),
                         ("profile-init", [TEST, TEST + "2"], ["T/A", "T/B"], "iPhone 17"))
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            M.build_parser().parse_args(["profile-init", "--repo", "R", "--out", "F", "--simulator-id", SIM, "--simulator-name", "N"])

    # REQ-MB2-07
    def test_only_read_only_tools_run(self):
        self.init()
        calls = self.argv_log()
        self.assertTrue(calls)
        for argv in calls:
            self.assertFalse({"boot", "install", "test", "build", "launch"} & set(argv), argv)


if __name__ == "__main__":
    unittest.main()
