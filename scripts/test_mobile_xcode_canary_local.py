"""Discriminating guards for mobile_xcode_canary_local.py with deterministic
tool fixtures, the same shape scripts/test_mobile_workflow.py uses.

These fixtures prove orchestration and refusal behavior (missing toolchain,
missing simulator, a failed build, a bad screenshot, a clean full run), not
Xcode compatibility. The canary runs mobile_workflow's doctor, profile_init,
reference_init and run_workflow in process, so these fake tools answer the
calls of all four. A real native run against the checked-in fixture app at
scripts/fixtures/mobile_canary_app is scripts/mobile_xcode_canary_local.py run
by hand; it is not deterministic or CI-safe, so it never runs here.
"""
import base64
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mobile_workflow as M
import mobile_xcode_canary_local as C

SIM = "11111111-2222-3333-4444-555555555555"

TOOL = r'''#!/usr/bin/env python3
import base64,json,os,pathlib,plistlib,shutil,sys
args=sys.argv[1:]; root=pathlib.Path(os.environ['FIXTURE_ROOT']); app=root/'built.app'; installed=root/'installed.app'
name=pathlib.Path(sys.argv[0]).name
open(root/'argv.log','a').write(json.dumps([name]+args)+'\n')
if name=='xcodebuild':
 if '-version' in args: print('Xcode fixture'); sys.exit(0)
 if '-list' in args:
  print(json.dumps({'project':{'name':'CanaryFixture','schemes':['CanaryFixture']}}));sys.exit(0)
 if '-showBuildSettings' in args:
  rows=[{'target':'CanaryFixture','buildSettings':{'TARGET_BUILD_DIR':str(root),'FULL_PRODUCT_NAME':'built.app',
         'PRODUCT_BUNDLE_IDENTIFIER':'com.brothermode.canaryfixture','MARKETING_VERSION':'1.0','CURRENT_PROJECT_VERSION':'1'}}]
  print(json.dumps(rows));sys.exit(0)
 if 'build' in args:
  if os.environ.get('FIXTURE_BUILD_FAIL'): print('build failed');sys.exit(65)
  print('fixture build completed');sys.exit(0)
 if 'test' in args:
  if os.environ.get('FIXTURE_BUILD_FAIL'): print('build failed');sys.exit(65)
  bundle=pathlib.Path(args[args.index('-resultBundlePath')+1]);bundle.mkdir()
  result=os.environ.get('FIXTURE_TEST_RESULT','Passed')
  leaves=[{'nodeType':'Test Case','result':result,
           'nodeIdentifierURL':'test://com.apple.xcode/CanaryFixture/CanaryFixtureTests/CanaryFixtureTests/testArithmeticSanity'},
          {'nodeType':'Test Case','result':result,
           'nodeIdentifierURL':'test://com.apple.xcode/CanaryFixture/CanaryFixtureTests/CanaryFixtureTests/testCanaryLabelExists'}]
  (bundle/'tests.json').write_text(json.dumps({'testNodes':leaves}));(bundle/'marker').touch()
  print('fixture test completed');sys.exit(0)
if os.environ.get('FIXTURE_NO_XCRESULTTOOL') and args[:1]==['--find'] and args[-1]=='xcresulttool':
 sys.exit(1)
if args[:1]==['--find']: print('/fixture/'+args[-1]);sys.exit(0)
if args[:3]==['xcresulttool','get','test-results']:
 print((pathlib.Path(args[args.index('--path')+1])/'tests.json').read_text());sys.exit(0)
if args[:2]==['devicectl','list']:
 print(json.dumps({'result':{'devices':[{'identifier':os.environ.get('FIXTURE_PHONE','')}]}}));sys.exit(0)
if args[:3]==['simctl','list','devices'] and os.environ.get('FIXTURE_CORRUPT_LIST'): print('not json at all');sys.exit(0)
if args[:3]==['simctl','list','devices'] and args[3] in ('available','booted'):
 devices=[] if os.environ.get('FIXTURE_NO_DEVICE') else [{'udid':os.environ['FIXTURE_SIM'],'state':'Booted','name':'Fixture Phone'}]
 print(json.dumps({'devices':{'iOS-fixture':devices}}));sys.exit(0)
if args[:2] in (['simctl','boot'],['simctl','bootstatus']):sys.exit(0)
if args[:2]==['simctl','install']:
 (root/'install-called').write_text('yes')
 if installed.exists():shutil.rmtree(installed)
 shutil.copytree(app,installed)
 if os.environ.get('FIXTURE_WRONG_INSTALL'):
  data=plistlib.loads((installed/'Info.plist').read_bytes());data['CFBundleVersion']='old';(installed/'Info.plist').write_bytes(plistlib.dumps(data))
 sys.exit(0)
if args[:2]==['simctl','get_app_container']:print(installed);sys.exit(0)
if args[:2]==['simctl','launch']:print('canary: 123');sys.exit(0)
if args[:2]==['simctl','io']:
 path=pathlib.Path(args[-1])
 if os.environ.get('FIXTURE_BAD_PNG'): path.write_text('not png')
 else:path.write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jWZkAAAAASUVORK5CYII='))
 sys.exit(0)
print('unexpected fixture command',args);sys.exit(99)
'''


class MobileXcodeCanaryLocalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="brother-canary-tests-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        # Fake native tools must not contend with a real machine-wide run.
        self.lock_directory = patch.object(M.tempfile, "gettempdir", return_value=str(self.root))
        self.lock_directory.start(); self.addCleanup(self.lock_directory.stop)
        self.repo = self.root / "repo"; self.repo.mkdir()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        (self.repo / "base.txt").write_text("base")
        (self.repo / C.FIXTURE_PROJECT).mkdir(parents=True)
        self.git("add", "."); self.git("commit", "-qm", "base")
        self.make_app("built.app")
        self.bin = self.root / "bin"; self.bin.mkdir()
        for name in ("xcodebuild", "xcrun"):
            path = self.bin / name; path.write_text(TOOL); path.chmod(0o755)
        self.environment = patch.dict(os.environ, {
            "PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
            "FIXTURE_ROOT": str(self.root),
            "FIXTURE_SIM": SIM,
            "BM_DEVICE_LEASE_DB": str(self.root / "leases.sqlite3"),  # never the machine lease store
        })
        self.environment.start(); self.addCleanup(self.environment.stop)

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.repo), *args], text=True, stderr=subprocess.DEVNULL)

    def make_app(self, name):
        path = self.root / name; path.mkdir()
        (path / "Info.plist").write_bytes(plistlib.dumps({
            "CFBundleIdentifier": "com.brothermode.canaryfixture", "CFBundleShortVersionString": "1.0",
            "CFBundleVersion": "1", "CFBundleExecutable": "CanaryFixture"}))
        (path / "CanaryFixture").write_bytes(b"fixture executable")
        return path

    def argv_log(self):
        path = self.root / "argv.log"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def run_installed(self, report):
        # reference-init installs the reference build before the run; what
        # must never happen after a failed test is the RUN's install stage.
        return any(s.get("name") == "install" for s in report.get("stages", []))

    def main(self, *argv, **extra_env):
        buffer = io.StringIO()
        with patch.dict(os.environ, extra_env), contextlib.redirect_stdout(buffer):
            code = C.main(list(argv))
        return code, buffer.getvalue().strip().splitlines()

    def run_canary(self, **extra_env):
        with patch.dict(os.environ, extra_env):
            return C.run_canary(self.repo, self.root / "run", SIM)

    def test_full_fixture_run_binds_test_install_and_capture(self):
        report, receipt = self.run_canary()
        self.assertEqual(report["status"], "PASS", report.get("reason"))
        self.assertTrue(receipt.is_file())
        self.assertEqual(report["built_app"]["executable"]["sha256"], report["installed_app"]["executable"]["sha256"])
        self.assertEqual(report["screenshot"]["width"], 1)
        self.assertTrue((self.root / "install-called").exists())

    def test_missing_native_tool_is_no_data_before_any_simulator_call(self):
        report, receipt = self.run_canary(FIXTURE_NO_XCRESULTTOOL="1")
        self.assertEqual(report["status"], "NO-DATA")
        self.assertIn("xcresulttool", report["reason"])
        self.assertTrue(receipt.is_file())
        self.assertFalse((self.root / "install-called").exists())

    def test_no_simulator_available_is_no_data(self):
        report, _ = self.run_canary(FIXTURE_NO_DEVICE="1")
        self.assertEqual(report["status"], "NO-DATA")
        self.assertFalse((self.root / "install-called").exists())

    def test_failed_build_cannot_pass(self):
        report, _ = self.run_canary(FIXTURE_BUILD_FAIL="1")
        self.assertEqual(report["status"], "FAIL")
        self.assertFalse((self.root / "install-called").exists())

    def test_skipped_test_prevents_installation(self):
        report, _ = self.run_canary(FIXTURE_TEST_RESULT="Skipped")
        self.assertEqual(report["status"], "FAIL")
        self.assertIn("native-tests", [s.get("name") for s in report["stages"]])
        self.assertFalse(self.run_installed(report))

    def test_wrong_installed_build_is_failure(self):
        report, _ = self.run_canary(FIXTURE_WRONG_INSTALL="1")
        self.assertEqual(report["status"], "FAIL")
        self.assertIn("Installed app differs", report["reason"])

    def test_bad_capture_cannot_be_accepted(self):
        report, _ = self.run_canary(FIXTURE_BAD_PNG="1")
        self.assertEqual(report["status"], "FAIL")
        self.assertIn("not a PNG", report["reason"])

    def test_output_must_stay_outside_repo(self):
        with self.assertRaisesRegex(M.Refusal, "outside"):
            C.run_canary(self.repo, self.repo / "run", SIM)

    def test_missing_fixture_project_refuses(self):
        empty_repo = self.root / "other"; empty_repo.mkdir()
        subprocess.check_output(["git", "-C", str(empty_repo), "init", "-q", "-b", "main"], stderr=subprocess.DEVNULL)
        with self.assertRaisesRegex(M.Refusal, "Project is missing"):
            C.run_canary(empty_repo, self.root / "elsewhere", SIM)

    def test_booted_simulator_helper_reads_real_simctl_shape(self):
        self.assertEqual(C.booted_simulator(), SIM)

    def test_explicit_project_scheme_and_expected_tests_override_the_fixture_defaults(self):
        # Proves the generalized path a real external project drives (any repo,
        # project, scheme, app target and expected test identities), not just
        # the built-in fixture defaults exercised by every other test here.
        expected = [
            "test://com.apple.xcode/CanaryFixture/CanaryFixtureTests/CanaryFixtureTests/testArithmeticSanity",
            "test://com.apple.xcode/CanaryFixture/CanaryFixtureTests/CanaryFixtureTests/testCanaryLabelExists",
        ]
        with patch.dict(os.environ, {}):
            report, receipt = C.run_canary(
                self.repo, self.root / "explicit-run", SIM,
                project=str(self.repo / C.FIXTURE_PROJECT), scheme="CanaryFixture",
                app_target="CanaryFixture", expected_tests=expected,
                only_testing=["CanaryFixtureTests"])
        self.assertEqual(report["status"], "PASS", report.get("reason"))
        self.assertEqual(report["scheme"], "CanaryFixture")
        self.assertTrue(any("-only-testing:CanaryFixtureTests" in s.get("argv", []) for s in report["stages"]))

    def test_missing_scheme_for_a_real_project_refuses(self):
        with self.assertRaisesRegex(M.Refusal, "scheme is required"):
            C.run_canary(self.repo, self.root / "no-scheme-run", SIM, project=str(self.repo / C.FIXTURE_PROJECT))

    def test_booted_simulator_helper_is_none_without_a_device(self):
        with patch.dict(os.environ, {"FIXTURE_NO_DEVICE": "1"}):
            self.assertIsNone(C.booted_simulator())

    # REQ-MB4-01
    def test_the_canary_runs_the_workflow_not_a_copy(self):
        source = Path(C.__file__).read_text()
        for call in ("run_workflow(", "profile_init(", "reference_init("):
            self.assertIn(call, source)
        for copy_marker in ("N.record", "native_args", "time.sleep"):
            self.assertNotIn(copy_marker, source)
        report, receipt = self.run_canary()
        self.assertEqual(report["status"], "PASS", report.get("reason"))
        actions = [a[1:] for a in self.argv_log() if a[0] == "xcodebuild"]
        order = [k for a in actions for k in ("-list", "build", "test") if k in a]
        self.assertEqual(order, ["-list", "build", "test"])
        stored = json.loads(receipt.read_text())
        self.assertEqual(stored["steps"], ["doctor", "profile-init", "reference-init", "run"])
        run_receipt = self.root / "run" / "run" / "receipt" / "receipt.json"
        self.assertEqual(stored["run_receipt"]["path"], str(run_receipt.resolve()))
        self.assertEqual(stored["run_receipt"], M.digest(run_receipt))
        for key in ("profile", "reference"):
            self.assertEqual(stored[key], M.digest(self.root / "run" / (key + ".json")))

    # REQ-MB4-05
    def test_no_own_lease_so_the_run_is_not_refused_by_itself(self):
        report, _ = self.run_canary()
        self.assertEqual(report["status"], "PASS", report.get("reason"))
        self.assertNotIn("owns the native tool lease", report.get("reason", ""))

    # REQ-MB4-02, REQ-MB4-03
    def test_off_macos_is_no_data_naming_the_platform(self):
        out = self.root / "off-mac"
        with patch.object(sys, "platform", "linux"):
            code, lines = self.main("--repo", str(self.repo), "--out", str(out))
        self.assertEqual(code, 2)
        self.assertEqual(lines[-1], "mobile_xcode_canary_local: NO-DATA: not macOS (sys.platform is linux)")
        self.assertFalse(out.exists())
        self.assertEqual(self.argv_log(), [])

    def test_missing_xcodebuild_is_no_data_naming_it(self):
        (self.bin / "xcodebuild").unlink()
        out = self.root / "no-xcodebuild"
        code, lines = self.main("--repo", str(self.repo), "--out", str(out), PATH=str(self.bin))
        self.assertEqual(code, 2)
        self.assertEqual(lines[-1], "mobile_xcode_canary_local: NO-DATA: xcodebuild is not on PATH")
        self.assertFalse(out.exists())
        self.assertEqual(C.preflight("darwin", which=lambda name: None if name == "xcrun" else "/x/" + name),
                         "xcrun is not on PATH")
        self.assertIsNone(C.preflight("darwin", which=lambda name: "/x/" + name))

    def test_no_simulator_installed_is_no_data(self):
        out = self.root / "none-installed"
        code, lines = self.main("--repo", str(self.repo), "--out", str(out), FIXTURE_NO_DEVICE="1")
        self.assertEqual(code, 2)
        self.assertEqual(lines[-1], "mobile_xcode_canary_local: NO-DATA: no iOS simulator is installed for any runtime")
        self.assertFalse(out.exists())

    def test_an_unlisted_simulator_id_is_no_data(self):
        other = "99999999-8888-7777-6666-555555555555"
        out = self.root / "unlisted"
        code, lines = self.main("--repo", str(self.repo), "--out", str(out), "--simulator-id", other)
        self.assertEqual(code, 2)
        self.assertEqual(lines[-1], "mobile_xcode_canary_local: NO-DATA: selected simulator %s is not an available simulator" % other)
        self.assertFalse(out.exists())

    # REQ-MB4-06
    def test_a_physical_device_cannot_be_selected(self):
        phone = "00008110-000A11112222801E"
        out = self.root / "phone"
        code, lines = self.main("--repo", str(self.repo), "--out", str(out), "--simulator-id", phone, FIXTURE_PHONE=phone)
        self.assertEqual(code, 2)
        self.assertIn("selected simulator %s is not an available simulator" % phone, lines[-1])
        self.assertFalse(out.exists())
        self.assertFalse(any("devicectl" in a for a in self.argv_log()))

    # REQ-MB4-03, REQ-MB4-04
    def test_the_last_line_is_the_receipt_path(self):
        code, lines = self.main("--repo", str(self.repo), "--out", str(self.root / "pass-run"))
        self.assertEqual(code, 0, lines)
        self.assertEqual(lines[-1], str((self.root / "pass-run" / "receipt.json").resolve()))
        self.assertTrue(lines[-2].startswith("mobile_xcode_canary_local: PASS"))
        code, lines = self.main("--repo", str(self.repo), "--out", str(self.root / "fail-run"), FIXTURE_BAD_PNG="1")
        self.assertEqual(code, 1, lines)
        self.assertEqual(lines[-1], str((self.root / "fail-run" / "receipt.json").resolve()))
        self.assertTrue(lines[-2].startswith("mobile_xcode_canary_local: FAIL"))
        code, lines = self.main("--repo", str(self.repo), "--out", str(self.root / "nd-run"), FIXTURE_NO_XCRESULTTOOL="1")
        self.assertEqual(code, 2, lines)
        self.assertEqual(lines[-1], str((self.root / "nd-run" / "receipt.json").resolve()))
        self.assertTrue(lines[-2].startswith("mobile_xcode_canary_local: NO-DATA"))
        for env in ({"FIXTURE_NO_DEVICE": "1"}, {"FIXTURE_CORRUPT_LIST": "1"}):
            code, lines = self.main("--repo", str(self.repo), "--out", str(self.root / "pre"), **env)
            self.assertEqual(code, 2)
            self.assertTrue(lines[-1].startswith("mobile_xcode_canary_local: NO-DATA: "), lines)
            self.assertFalse(any("PASS" in line for line in lines))

    def test_derived_data_is_refused_by_name(self):
        out = self.root / "dd"
        code, lines = self.main("--repo", str(self.repo), "--out", str(out), "--derived-data", str(self.root / "dd-cache"))
        self.assertEqual(code, 1)
        self.assertEqual(lines[-1], "mobile_xcode_canary_local: FAIL: --derived-data is not supported; the canary runs "
                                    "the workflow, which uses Xcode's shared DerivedData")
        self.assertFalse(out.exists())

    # REQ-MB4-07
    def test_the_dirty_checkout_does_not_matter_for_the_fixture(self):
        (self.repo / "base.txt").write_text("dirty")
        out = self.root / "fixture-run"
        old = os.getcwd()
        os.chdir(self.repo); self.addCleanup(os.chdir, old)
        code, lines = self.main("--out", str(out))
        self.assertEqual(code, 0, lines)
        stored = json.loads((out / "receipt.json").read_text())
        self.assertEqual(stored["repo"], str((out / "source").resolve()))
        self.assertTrue((out / "source" / "CanaryFixture.xcodeproj" / "project.pbxproj").is_file())
        profile = json.loads((out / "profile.json").read_text())
        self.assertEqual(profile["project"], "CanaryFixture.xcodeproj")
        self.assertEqual(profile["expected_tests"], C.FIXTURE_EXPECTED_TESTS)

    # REQ-MB4-06
    def test_nothing_leaves_the_machine(self):
        for module in (C, M):
            source = Path(module.__file__).read_text()
            for name in ("urllib", "socket", "http", "requests"):
                self.assertNotRegex(source, r"(?m)^\s*(import|from)\s+%s\b" % name)
        report, receipt = self.run_canary()
        self.assertEqual(report["status"], "PASS", report.get("reason"))
        out = (self.root / "run").resolve()
        stored = json.loads(receipt.read_text())
        for key in ("run_receipt", "profile", "reference", "doctor"):
            self.assertTrue(Path(stored[key]["path"]).is_relative_to(out), key)
        self.assertTrue(receipt.resolve().is_relative_to(out))

    def test_a_corrupt_simulator_list_is_no_data(self):
        out = self.root / "corrupt"
        code, lines = self.main("--repo", str(self.repo), "--out", str(out), FIXTURE_CORRUPT_LIST="1")
        self.assertEqual(code, 2)
        self.assertEqual(lines[-1], "mobile_xcode_canary_local: NO-DATA: the simulator list could not be read")
        self.assertFalse(out.exists())
        with patch.dict(os.environ, {"FIXTURE_CORRUPT_LIST": "1"}):
            self.assertIsNone(C.available_simulators())

    def test_available_simulators_lists_ios_devices_only(self):
        self.assertEqual(C.available_simulators(), [{"udid": SIM, "name": "Fixture Phone", "state": "Booted"}])
        with patch.dict(os.environ, {"FIXTURE_NO_DEVICE": "1"}):
            self.assertEqual(C.available_simulators(), [])


if __name__ == "__main__":
    unittest.main()
