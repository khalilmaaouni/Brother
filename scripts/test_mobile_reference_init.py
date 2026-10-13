"""MB2.b: reference-init records the current clean checkout as the working
reference: built, installed on the profile's simulator, read back from the
simulator's own container, and bound with the engine's record_reference.

The fake tools are the ones scripts/test_mobile_workflow.py uses, extended
here with a build action, an argv log and two install faults. They prove
orchestration and refusal behaviour, not Xcode itself.
"""
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
import test_mobile_workflow as W


def extend(tool, anchor, addition, before=True):
    assert tool.count(anchor) == 1, anchor
    return tool.replace(anchor, addition + anchor if before else anchor + addition)


TOOL = W.TOOL
TOOL = extend(TOOL, "installed=root/'installed.app'\n",
              "open(root/'argv.log','a').write(json.dumps([pathlib.Path(sys.argv[0]).name]+args)+'\\n')\n", before=False)
TOOL = extend(TOOL, " if 'test' in args:\n",
              " if 'build' in args:\n"
              "  if os.environ.get('FIXTURE_BUILD_FAIL'): print('build failed');sys.exit(65)\n"
              "  if os.environ.get('FIXTURE_NO_PRODUCT'): shutil.rmtree(app)\n"
              "  if os.environ.get('FIXTURE_SOURCE_CHANGE'): pathlib.Path('base.txt').write_text('changed')\n"
              "  print('fixture build completed');sys.exit(0)\n")
TOOL = extend(TOOL, " if os.environ.get('FIXTURE_WRONG_INSTALL'):\n",
              " if os.environ.get('FIXTURE_WRONG_EXEC'): (installed/'Sample').write_bytes(b'other executable')\n")

STATEMENT = ("This working reference is the source checkout itself, built from revision %s and installed on "
             "simulator %s. It is not a store release, not a distributed binary and not a physical device install.")


class ReferenceInitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="brother-mobile-reference-init-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        lock = patch.object(M.tempfile, "gettempdir", return_value=str(self.root))
        lock.start(); self.addCleanup(lock.stop)
        self.repo = self.root / "repo"; self.repo.mkdir()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        (self.repo / "base.txt").write_text("base")
        (self.repo / "Sample.xcodeproj").mkdir()
        (self.repo / "Sample.xcodeproj/project.pbxproj").write_text("fixture")
        self.git("add", "."); self.git("commit", "-qm", "base")
        self.sha = self.git("rev-parse", "HEAD").strip()
        app = self.root / "built.app"; app.mkdir()
        (app / "Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": "example.Sample", "CFBundleShortVersionString": "1.0",
                                                         "CFBundleVersion": "1", "CFBundleExecutable": "Sample"}))
        (app / "Sample").write_bytes(b"fixture executable")
        self.bin = self.root / "bin"; self.bin.mkdir()
        for name in ("xcodebuild", "xcrun"):
            path = self.bin / name; path.write_text(TOOL); path.chmod(0o755)
        env = patch.dict(os.environ, {"PATH": str(self.bin) + os.pathsep + os.environ["PATH"], "FIXTURE_ROOT": str(self.root),
                                       "BM_DEVICE_LEASE_DB": str(self.root / "leases.sqlite3")})  # never the machine lease store
        env.start(); self.addCleanup(env.stop)
        self.profile = {"schema": M.PROFILE_SCHEMA, "project": "Sample.xcodeproj", "scheme": "Sample", "target": "Sample",
                        "configuration": "Debug", "simulator_id": W.SIM, "bundle_id": "example.Sample", "version": "1.0",
                        "build": "1", "expected_tests": [W.TEST], "only_testing": ["SampleTests/RoomTests/testOpen"],
                        "timeout_seconds": 20}
        self.profile_path = self.root / "profile.json"
        self.profile_path.write_text(json.dumps(self.profile))
        self.out = self.root / "refs" / "working.json"

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.repo), *args], text=True, stderr=subprocess.DEVNULL)

    def side(self, name):
        return self.out.parent / ("working-" + name)

    def argv_log(self):
        path = self.root / "argv.log"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def refused(self, status, pattern, out=None, **faults):
        out = out or self.out
        with patch.dict(os.environ, faults):
            with self.assertRaises(M.Refusal) as raised:
                M.reference_init(self.repo, self.profile_path, out)
        self.assertEqual(raised.exception.status, status, str(raised.exception))
        self.assertRegex(str(raised.exception), pattern)
        self.assertFalse(out.exists(), "a refusal wrote the reference")
        return str(raised.exception)

    # REQ-MB2-08, REQ-MB2-11
    def test_reference_binds_clean_revision_installed_identity_and_run_accepts_it(self):
        ref = M.reference_init(self.repo, self.profile_path, self.out)
        self.assertTrue(self.out.is_file())
        self.assertEqual(ref["base_revision"], self.sha)
        installed = str((self.root / "installed.app").resolve())
        self.assertEqual(ref["identity"]["app"], installed)
        observation = json.loads(self.side("observation.json").read_text())
        self.assertEqual(observation["result"]["apps"], [{"bundleIdentifier": "example.Sample", "version": "1.0", "bundleVersion": "1"}])
        self.assertIn(installed, observation["source"]); self.assertIn(W.SIM, observation["source"])
        mapping = self.side("mapping.md").read_text()
        self.assertIn(STATEMENT % (self.sha, W.SIM), mapping)
        self.assertIn("example.Sample", mapping)
        stages = json.loads(self.side("stages.json").read_text())
        self.assertEqual(stages["status"], "PASS")
        names = [s["name"] for s in stages["stages"]]
        self.assertEqual(names[:3], ["simulators", "build", "product-path"])
        self.assertIn("installed-path", names)
        build = [a for a in self.argv_log() if a[0] == "xcodebuild" and "build" in a][0]
        self.assertIn("platform=iOS Simulator,id=" + W.SIM, build)
        M.verify_reference(self.out, self.repo)
        (self.repo / "change.txt").write_text("candidate"); self.git("add", "."); self.git("commit", "-qm", "candidate")
        report, _receipt = M.run_workflow(self.repo, self.profile_path, self.out, self.root / "run")
        self.assertEqual(report["technical_status"], "PASS", report.get("reason"))

    def test_main_prints_the_reference_path_last_and_exits_0(self):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = M.main(["reference-init", "--repo", str(self.repo), "--profile", str(self.profile_path),
                           "--out", str(self.out), "--source-revision", self.sha])
        self.assertEqual(code, 0, buffer.getvalue())
        self.assertEqual(buffer.getvalue().strip().splitlines()[-1], str(self.out))

    # REQ-MB2-09
    def test_dirty_checkout_refuses_before_any_tool(self):
        (self.repo / "base.txt").write_text("dirty")
        self.refused("FAIL", "clean committed")
        self.assertEqual(self.argv_log(), [])

    def test_wrong_or_malformed_source_revision_refuses_before_any_tool(self):
        for value, pattern in (("0" * 40, "not the checkout"), ("abc", "full source revision")):
            with self.assertRaisesRegex(M.Refusal, pattern):
                M.reference_init(self.repo, self.profile_path, self.out, source_revision=value)
        self.assertFalse(self.out.exists())
        self.assertEqual(self.argv_log(), [])

    def test_invalid_or_empty_profile_refuses_before_any_tool(self):
        bad = copy.deepcopy(self.profile); bad["simulator_id"] = "booted"
        self.profile_path.write_text(json.dumps(bad))
        self.refused("FAIL", "simulator UUID")
        self.profile_path.write_text("")
        self.refused("FAIL", "Cannot read JSON")
        self.assertEqual(self.argv_log(), [])

    # REQ-MB2-10
    def test_failed_build_fails_and_leaves_its_stage_log(self):
        self.refused("FAIL", "build exited 65", FIXTURE_BUILD_FAIL="1")
        stages = json.loads(self.side("stages.json").read_text())
        self.assertEqual((stages["status"], stages["stages"][-1]["name"]), ("FAIL", "build"))
        self.assertFalse((self.root / "install-called").exists())

    def test_build_without_product_fails(self):
        self.refused("FAIL", "App bundle is missing", FIXTURE_NO_PRODUCT="1")

    def test_missing_simulator_is_no_data(self):
        self.refused("NO-DATA", "Selected simulator is unavailable", FIXTURE_NO_DEVICE="1")
        self.assertFalse(any("build" in a for a in self.argv_log()))

    def test_installed_copy_with_other_build_fails(self):
        self.refused("FAIL", "Installed app differs from the build", FIXTURE_WRONG_INSTALL="1")

    def test_installed_copy_with_other_executable_fails(self):
        self.refused("FAIL", "Installed app differs from the build", FIXTURE_WRONG_EXEC="1")

    def test_stale_profile_version_refuses(self):
        stale = copy.deepcopy(self.profile); stale["build"] = "7"
        self.profile_path.write_text(json.dumps(stale))
        self.refused("FAIL", "contradicts the profile")
        self.assertFalse((self.root / "install-called").exists())

    def test_partial_write_names_the_kept_files(self):
        message = self.refused("FAIL", "clean committed", FIXTURE_SOURCE_CHANGE="1")
        for name in ("observation.json", "mapping.md"):
            self.assertTrue(self.side(name).is_file())
            self.assertIn(str(self.side(name)), message)

    # REQ-MB2-12
    def test_inside_or_existing_output_refuses(self):
        self.refused("FAIL", "outside", out=self.repo / "working.json")
        self.assertEqual(self.argv_log(), [])
        M.reference_init(self.repo, self.profile_path, self.out)
        before = self.out.read_bytes()
        with self.assertRaisesRegex(M.Refusal, "already exists"):
            M.reference_init(self.repo, self.profile_path, self.out)
        self.assertEqual(self.out.read_bytes(), before)
        later = M.reference_init(self.repo, self.profile_path, self.root / "refs" / "second.json")
        self.assertEqual(later["base_revision"], self.sha)

    # REQ-MB2-13
    def test_cannot_race_a_running_workflow(self):
        with M.lease():
            self.refused("NO-DATA", "lease")
        self.assertEqual(self.argv_log(), [])


if __name__ == "__main__":
    unittest.main()
