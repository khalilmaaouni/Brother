"""native_evidence_v2: prove the wrapper reuses native_evidence.py's own
pass/fail signal and never claims a guarantee its input doesn't carry.

Fixtures are built the same way scripts/test_native_evidence.py builds them:
a real git repo plus a real native_evidence.py `record` run against a fake
xcrun and a real command, so the evidence JSON `wrap_v2` reads is the actual
schema native_evidence.py produces, never a guessed one.
"""
import json
import os
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import native_evidence as N  # noqa: E402
import native_evidence_v2 as V2  # noqa: E402
from evidence_obligation import VERDICTS  # noqa: E402

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


class NativeEvidenceV2Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="native-evidence-v2-")
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

    def record(self, result=None, expected=IDENTITY, capture=True, extra=None, tag="a"):
        bundle = os.path.join(self.tmp, "result-%s.xcresult" % tag)
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

    def test_pass_case_maps_to_shared_pass_and_names_real_proof(self):
        code, evidence, _capture = self.record()
        self.assertEqual(code, 0, "native_evidence itself must record a PASS fixture")

        v2 = V2.wrap_v2(evidence, repo=self.repo, xcrun=self.xcrun)

        self.assertEqual(v2["schema"], "brother-native-evidence-v2")
        self.assertIn(v2["verdict"], VERDICTS)
        self.assertEqual(v2["verdict"], N.PASS)
        # the original is embedded whole, never discarded
        self.assertEqual(v2["native_evidence"]["schema"], N.SCHEMA)
        self.assertEqual(v2["native_evidence"]["tests"]["executed_count"], 1)
        self.assertEqual(v2["native_evidence_path"], os.path.abspath(evidence))

        scope_text = " ".join(v2["proof_scope"])
        self.assertIn(IDENTITY, scope_text)
        self.assertIn("simulator", scope_text.lower())
        self.assertIn("does NOT prove", scope_text)

        carried = {row["guarantee"]: row["carried"] for row in v2["preserved_guarantees"]}
        self.assertTrue(carried["fresh result bundle"])
        self.assertTrue(carried["exact test leaves"])
        self.assertTrue(carried["candidate before/after identity"])
        self.assertTrue(carried["command/log"])
        self.assertTrue(carried["artifact hashes"])
        self.assertTrue(carried["zero-test refusal"])
        self.assertTrue(carried["skipped/failed test refusal"])
        self.assertTrue(carried["stale candidate refusal"])

    def test_zero_tests_refusal_maps_to_shared_fail_and_never_claims_leaves(self):
        code, evidence, _capture = self.record(result={"testNodes": []}, tag="zero")
        self.assertEqual(code, 1, "native_evidence itself must refuse a zero-test run")

        v2 = V2.wrap_v2(evidence, repo=self.repo, xcrun=self.xcrun)

        self.assertIn(v2["verdict"], VERDICTS)
        self.assertEqual(v2["verdict"], N.FAIL)
        self.assertTrue(v2["verdict_detail"], "a FAIL record must carry its own reasons")

        carried = {row["guarantee"]: row["carried"] for row in v2["preserved_guarantees"]}
        # this record has zero test leaves: it must never claim it carries
        # evidence for the leaves-dependent guarantees
        self.assertFalse(carried["exact test leaves"])
        self.assertFalse(carried["skipped/failed test refusal"])
        # the zero-test refusal itself DID fire (that's why this is a FAIL
        # record at all), so this is the one guarantee this exact record
        # demonstrates most directly
        self.assertTrue(carried["zero-test refusal"])
        # candidate identity and command/log are unrelated to the test count
        # and this run still recorded them honestly
        self.assertTrue(carried["candidate before/after identity"])
        self.assertTrue(carried["command/log"])

    def test_unreadable_evidence_path_is_shared_fail_not_a_crash(self):
        v2 = V2.wrap_v2(os.path.join(self.tmp, "does-not-exist.json"))
        self.assertIn(v2["verdict"], VERDICTS)
        self.assertEqual(v2["verdict"], N.FAIL)
        self.assertIsNone(v2["native_evidence"])
        self.assertEqual(v2["preserved_guarantees"], [])

    def test_cli_wrap_exit_codes_match_the_shared_triple(self):
        code, evidence, _capture = self.record(tag="cli")
        self.assertEqual(code, 0)
        out_path = os.path.join(self.tmp, "v2.json")
        exit_code = V2.main(["wrap", "--evidence", evidence, "--repo", self.repo,
                             "--xcrun", self.xcrun, "--out", out_path])
        self.assertEqual(exit_code, 0)
        with open(out_path, encoding="utf-8") as fh:
            written = json.load(fh)
        self.assertEqual(written["verdict"], N.PASS)


if __name__ == "__main__":
    unittest.main()
