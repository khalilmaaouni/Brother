"""native_evidence: prove a recorded native test cannot be a green label only."""
import json
import os
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import native_evidence as N  # noqa: E402


IDENTITY = "test://com.apple.xcode/App/AppTests/FooTests/testThing"


def result_doc(result="Passed"):
    return {"devices": [{"deviceId": "device", "deviceName": "phone",
                         "osVersion": "26.0", "platform": "iOS"}],
            "testNodes": [{"nodeType": "Test Plan", "name": "App",
                           "children": [{"nodeType": "Unit test bundle",
                                         "name": "AppTests", "children": [
                               {"nodeType": "Test Suite", "name": "FooTests",
                                "children": [{"nodeType": "Test Case",
                                              "name": "testThing()",
                                              "nodeIdentifier": "FooTests/testThing()",
                                              "nodeIdentifierURL": IDENTITY,
                                              "result": result,
                                              "durationInSeconds": 0.001}]}]}]}],
            "testPlanConfigurations": []}


class NativeEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="native-evidence-")
        self.repo = os.path.join(self.tmp, "repo")
        os.mkdir(self.repo)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "a@b.c")
        self.git("config", "user.name", "t")
        with open(os.path.join(self.repo, "base.txt"), "w", encoding="utf-8") as fh:
            fh.write("base\n")
        self.git("add", "base.txt")
        self.git("commit", "-qm", "base")
        self.xcrun = self.write_script("fake-xcrun.py", """
            import json, os, sys
            path = sys.argv[sys.argv.index('--path') + 1]
            index = os.path.join(path, 'database.sqlite3')
            if not os.path.exists(index):
                with open(index, 'wb') as fh:
                    fh.write(b'index')
            with open(os.path.join(path, 'test-results.json'), encoding='utf-8') as fh:
                sys.stdout.write(fh.read())
        """)
        self.runner = self.write_script("runner.py", """
            import json, os, sys
            bundle, capture, result = sys.argv[1:]
            os.mkdir(bundle)
            with open(os.path.join(bundle, 'test-results.json'), 'w', encoding='utf-8') as fh:
                fh.write(result)
            if capture != '-':
                with open(capture, 'wb') as fh:
                    fh.write(b'capture')
        """)

    def git(self, *args):
        return subprocess.run(["git", "-C", self.repo] + list(args),
                              check=True, capture_output=True)

    def write_script(self, name, body):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("#!/usr/bin/env python3\n" + textwrap.dedent(body))
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
        return path

    def record(self, result=None, expected=IDENTITY, capture=True, extra=None):
        bundle = os.path.join(self.tmp, "result-%d.xcresult" % len(
            [p for p in os.listdir(self.tmp) if p.endswith(".xcresult")]))
        evidence = bundle + ".json"
        screenshot = bundle + ".png"
        result = result if result is not None else result_doc()
        args = ["record", "--repo", self.repo, "--out", evidence,
                "--result-bundle", bundle, "--expected-test", expected,
                "--artifact", "screenshot=" + screenshot, "--xcrun", self.xcrun]
        for item in extra or []:
            args.extend(item)
        args += ["--command", sys.executable, self.runner, bundle,
                 screenshot if capture else "-", json.dumps(result)]
        return N.main(args), evidence, screenshot

    @unittest.skipUnless(os.name == "posix", "Process group cancellation is a POSIX contract")
    def test_timeout_stops_wrapper_descendants(self):
        late = os.path.join(self.tmp, "orphan-write")
        child = self.write_script("late.py", """
            import pathlib, sys, time
            time.sleep(1.2)
            pathlib.Path(sys.argv[1]).write_text('orphan')
        """)
        wrapper = self.write_script("wrapper.py", """
            import subprocess, sys, time
            subprocess.Popen([sys.executable, sys.argv[1], sys.argv[2]])
            time.sleep(10)
        """)
        code = N.main(["record", "--repo", self.repo, "--out", os.path.join(self.tmp, "timeout.json"),
                       "--result-bundle", os.path.join(self.tmp, "timeout.xcresult"),
                       "--expected-test", IDENTITY, "--timeout", "1", "--xcrun", self.xcrun,
                       "--command", sys.executable, wrapper, child, late])
        self.assertEqual(code, 1)
        time.sleep(0.6)
        self.assertFalse(os.path.exists(late), "Timed out build descendants survived")

    def test_pass_requeries_xcresult_and_keeps_quality_unassessed(self):
        code, evidence, _capture = self.record()
        self.assertEqual(code, 0)
        doc, error = N.read_json(evidence)
        self.assertIsNone(error)
        self.assertEqual(doc["tests"]["executed_count"], 1)
        self.assertTrue(any(item["path"] == "database.sqlite3"
                            for item in doc["result_bundle"]["hash"]["files"]))
        self.assertEqual(doc["boundaries"]["application_quality"], "NOT-ASSESSED")
        self.assertEqual(N.main(["validate", "--evidence", evidence,
                                 "--xcrun", self.xcrun]), 0)

    def test_wrong_expected_filter_is_rejected(self):
        code, _evidence, _capture = self.record(expected=IDENTITY + "/other")
        self.assertEqual(code, 1)

    def test_zero_executed_tests_are_rejected(self):
        code, _evidence, _capture = self.record(result={"testNodes": []})
        self.assertEqual(code, 1)

    def test_missing_claimed_screenshot_is_rejected(self):
        code, _evidence, _capture = self.record(capture=False)
        self.assertEqual(code, 1)

    def test_a_changed_capture_and_a_stale_sha_are_rejected(self):
        code, evidence, capture = self.record()
        self.assertEqual(code, 0)
        with open(capture, "wb") as fh:
            fh.write(b"different")
        self.assertEqual(N.main(["validate", "--evidence", evidence,
                                 "--xcrun", self.xcrun]), 1)

    def test_a_changed_candidate_revision_is_rejected(self):
        code, evidence, _capture = self.record()
        self.assertEqual(code, 0)
        with open(os.path.join(self.repo, "base.txt"), "a", encoding="utf-8") as fh:
            fh.write("changed\n")
        self.assertEqual(N.main(["validate", "--evidence", evidence,
                                 "--xcrun", self.xcrun]), 1)

    def test_malformed_result_data_is_rejected(self):
        code, _evidence, _capture = self.record(result={"testNodes": "bad"})
        self.assertEqual(code, 1)

    def test_a_failed_or_skipped_test_cannot_pass(self):
        for result in ("Failed", "Skipped"):
            code, _evidence, _capture = self.record(result=result_doc(result))
            self.assertEqual(code, 1, result)

    def test_required_missing_device_evidence_is_no_data(self):
        code, _evidence, _capture = self.record(extra=[
            ("--requirement", "physical-device=NO-DATA:device unavailable")])
        self.assertEqual(code, 2)

    def test_reused_result_bundle_is_refused_before_command_runs(self):
        bundle = os.path.join(self.tmp, "already.xcresult")
        os.mkdir(bundle)
        code = N.main(["record", "--repo", self.repo, "--out", bundle + ".json",
                       "--result-bundle", bundle, "--expected-test", IDENTITY,
                       "--xcrun", self.xcrun, "--command", "true"])
        self.assertEqual(code, 1)

    def test_adversarial_schema_types_fail_without_crashing(self):
        code, evidence, _capture = self.record()
        self.assertEqual(code, 0)
        doc, error = N.read_json(evidence)
        self.assertIsNone(error)
        for field, value in (("command", []), ("candidate_after", []),
                             ("artifacts", None), ("expected_tests", None),
                             ("tests", []), ("result_bundle", [])):
            broken = dict(doc)
            broken[field] = value
            verdict, _lines = N.validate_record(broken, xcrun=self.xcrun)
            self.assertEqual(verdict, N.FAIL, field)

    def test_adversarial_nested_types_fail_before_paths_or_subprocesses(self):
        code, evidence, _capture = self.record()
        self.assertEqual(code, 0)
        doc, error = N.read_json(evidence)
        self.assertIsNone(error)
        cases = []
        broken_log = json.loads(json.dumps(doc))
        broken_log["command"]["log_hash"]["path"] = None
        cases.append(("log hash path", broken_log))
        broken_repo = json.loads(json.dumps(doc))
        broken_repo["candidate_after"]["repo"] = []
        cases.append(("candidate repo", broken_repo))
        broken_verdict = json.loads(json.dumps(doc))
        broken_verdict["requirements"] = [{"name": "device", "verdict": {},
                                            "detail": "bad"}]
        cases.append(("requirement verdict", broken_verdict))
        broken_detail = json.loads(json.dumps(doc))
        broken_detail["requirements"] = [{"name": "device", "verdict": "NO-DATA",
                                           "detail": 4}]
        cases.append(("requirement detail", broken_detail))
        broken_bundle = json.loads(json.dumps(doc))
        broken_bundle["result_bundle"]["path"] = []
        cases.append(("bundle path", broken_bundle))
        for name, broken in cases:
            verdict, _lines = N.validate_record(broken, xcrun=self.xcrun)
            self.assertEqual(verdict, N.FAIL, name)

    def test_result_bundle_allows_empty_marker_but_rejects_symlink_directory(self):
        bundle = os.path.join(self.tmp, "bundle.xcresult")
        os.mkdir(bundle)
        with open(os.path.join(bundle, "marker"), "wb") as fh:
            fh.write(b"")
        with open(os.path.join(bundle, "payload"), "wb") as fh:
            fh.write(b"data")
        self.assertNotIn("error", N.hash_tree(bundle))
        os.symlink(self.tmp, os.path.join(bundle, "linked"))
        self.assertIn("error", N.hash_tree(bundle))


if __name__ == "__main__":
    unittest.main()
