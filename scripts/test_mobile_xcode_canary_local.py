"""Discriminating guards for mobile_xcode_canary_local.py with deterministic
tool fixtures, the same shape scripts/test_mobile_workflow.py uses.

These fixtures prove orchestration and refusal behavior (missing toolchain,
missing simulator, a failed build, a bad screenshot, a clean full run), not
Xcode compatibility. A real native run against the checked-in fixture app at
scripts/fixtures/mobile_canary_app is what scripts/mobile_xcode_canary_local.py
itself performs by hand, or through the BROTHER_REAL_XCODE_CANARY=1 lane in
scripts/check_all.sh; neither is deterministic or CI-safe, so neither runs here.
"""
import base64
import copy
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
if name=='xcodebuild':
 if '-version' in args: print('Xcode fixture'); sys.exit(0)
 if '-showBuildSettings' in args:
  rows=[{'target':'CanaryFixture','buildSettings':{'TARGET_BUILD_DIR':str(root),'FULL_PRODUCT_NAME':'built.app'}}]
  print(json.dumps(rows));sys.exit(0)
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
        self.assertFalse((self.root / "install-called").exists())

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


if __name__ == "__main__":
    unittest.main()
