#!/usr/bin/env python3
"""Test for mobile_project_profile.py (EPIC M1.01/M1.02). Every fixture
here is a synthetic, throwaway directory: no real project's files or
names are read or referenced anywhere in this file."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mobile_project_profile as MPP  # noqa: E402
import contract_check as CC  # noqa: E402


def _git_init(path):
    subprocess.run(["git", "-C", path, "init", "-q", "-b", "main"], check=True)
    subprocess.run(["git", "-C", path, "config", "user.email", "a@b.c"], check=True)
    subprocess.run(["git", "-C", path, "config", "user.name", "profile-test"], check=True)
    with open(os.path.join(path, "README.md"), "w", encoding="utf-8") as fh:
        fh.write("synthetic fixture\n")
    subprocess.run(["git", "-C", path, "add", "README.md"], check=True)
    subprocess.run(["git", "-C", path, "commit", "-qm", "base"], check=True)


class MobileProjectProfileTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mobile-project-profile-test-")
        self.schema = CC.load_json(MPP.DEFAULT_SCHEMA, "mobile-project-profile-v1 schema")

    def test_empty_directory_is_honest_none_confidence_not_a_guess(self):
        profile = MPP.detect(self.tmp, project_id="test-empty")
        self.assertEqual(profile["platforms"], [])
        self.assertEqual(profile["confidence"], "none")
        self.assertEqual(profile["evidence_refs"], [])
        self.assertEqual(MPP.check(profile, self.schema), [])

    def test_xcodeproj_directory_is_detected_as_ios(self):
        _git_init(self.tmp)
        os.mkdir(os.path.join(self.tmp, "Fixture.xcodeproj"))
        profile = MPP.detect(self.tmp, project_id="test-ios")
        self.assertIn("ios", profile["platforms"])
        self.assertIn("xcodebuild", profile["build_systems"])
        self.assertIn("xctest", profile["test_frameworks"])
        self.assertNotEqual(profile["profiled_revision"], "NO-DATA")
        self.assertTrue(profile["evidence_refs"])
        self.assertIn(profile["confidence"], ("low", "high"))
        self.assertEqual(MPP.check(profile, self.schema), [])

    def test_gradle_files_are_detected_as_android(self):
        with open(os.path.join(self.tmp, "build.gradle"), "w", encoding="utf-8") as fh:
            fh.write("// synthetic\n")
        with open(os.path.join(self.tmp, "settings.gradle"), "w", encoding="utf-8") as fh:
            fh.write("// synthetic\n")
        profile = MPP.detect(self.tmp, project_id="test-android")
        self.assertIn("android", profile["platforms"])
        self.assertIn("gradle", profile["build_systems"])
        self.assertIn("android-gradle", profile["frameworks"])
        self.assertEqual(len(profile["evidence_refs"]), 2)
        self.assertEqual(profile["confidence"], "high")
        self.assertEqual(MPP.check(profile, self.schema), [])

    def test_mixed_ios_and_android_indicators_report_both_platforms(self):
        # Not a claim any real project is both; proves the detector does
        # not silently pick one platform when more than one is present.
        os.mkdir(os.path.join(self.tmp, "Fixture.xcworkspace"))
        with open(os.path.join(self.tmp, "build.gradle"), "w", encoding="utf-8") as fh:
            fh.write("// synthetic\n")
        profile = MPP.detect(self.tmp, project_id="test-mixed")
        self.assertEqual(sorted(profile["platforms"]), ["android", "ios"])
        self.assertEqual(profile["confidence"], "high")

    def test_hand_rule_refuses_confidence_contradicting_its_own_evidence(self):
        good = MPP.detect(self.tmp, project_id="test-contradiction")
        broken = dict(good, confidence="none", evidence_refs=["fake.txt"])
        problems = MPP.hand_rules(broken)
        self.assertTrue(problems)
        self.assertIn("confidence", problems[0])

    def test_missing_project_directory_is_no_data_not_a_crash(self):
        profile = MPP.detect(os.path.join(self.tmp, "does-not-exist"), project_id="test-missing")
        self.assertEqual(profile["confidence"], "none")
        self.assertEqual(profile["platforms"], [])

    def test_cli_prints_valid_json_and_exits_clean_on_a_recognized_project(self):
        _git_init(self.tmp)
        os.mkdir(os.path.join(self.tmp, "Fixture.xcodeproj"))
        result = subprocess.run(
            [sys.executable, MPP.__file__, self.tmp, "--project-id", "test-cli"],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, "stderr: %s" % result.stderr)
        printed = json.loads(result.stdout)
        self.assertEqual(printed["project_id"], "test-cli")


if __name__ == "__main__":
    unittest.main()
