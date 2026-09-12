#!/usr/bin/env python3
"""Refute false release readiness with real tiny subprocesses and bad inputs."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import subprocess
import unittest
from unittest import mock

try:
    from scripts import evad_release_smoke as smoke
except ModuleNotFoundError:
    import evad_release_smoke as smoke


class RequiredEvidenceCannotDisappear(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "scripts").mkdir()
        self.script = self.root / "scripts/test_fixture.py"
        self.script.write_text(
            "import unittest\nclass Example(unittest.TestCase):\n"
            "    def test_measured(self): self.assertEqual(2 + 2, 4)\n"
            "if __name__ == '__main__': unittest.main()\n")
        self.case = {"id": "t2", "required": True,
                     "command": ["python3", "scripts/test_fixture.py", "Example", "-v"],
                     "expected_tests": 1, "timeout_seconds": 2}

    def test_real_test_result_preserves_both_streams_exit_and_identity(self):
        self.script.write_text("print('fixture stdout')\n" + self.script.read_text())
        result = smoke.run_case(self.case, self.root)
        self.assertEqual(result["verdict"], "PASS", result)
        self.assertEqual(result["exit_code"], 0)
        self.assertIn("fixture stdout", result["stdout"])
        self.assertIn("Ran 1 test", result["stderr"])
        self.assertEqual(len(result["test_file_sha256"]), 64)
        self.assertIn("started_at", result)

    def test_assertion_failure_remains_fail_with_its_output(self):
        self.script.write_text(self.script.read_text().replace("2 + 2, 4", "2 + 2, 5"))
        result = smoke.run_case(self.case, self.root)
        self.assertEqual(result["verdict"], "FAIL")
        self.assertNotEqual(result["exit_code"], 0)
        self.assertIn("AssertionError", result["stderr"])

    def test_exit_zero_without_running_tests_is_no_data(self):
        self.script.write_text("print('everything is fine')\n")
        self.assertEqual(smoke.run_case(self.case, self.root)["verdict"], "NO-DATA")

    def test_explicit_no_data_exit_never_becomes_a_contradiction(self):
        self.script.write_text("print('NO-DATA: dependency unavailable')\nraise SystemExit(2)\n")
        result = smoke.run_case(self.case, self.root)
        self.assertEqual(result["verdict"], "NO-DATA")
        self.assertEqual(result["exit_code"], 2)
        self.assertIn("dependency unavailable", result["stdout"])

    def test_zero_discovered_tests_is_no_data(self):
        self.script.write_text("import unittest\nunittest.main()\n")
        case = dict(self.case, command=["python3", "scripts/test_fixture.py", "-v"])
        self.assertEqual(smoke.run_case(case, self.root)["verdict"], "NO-DATA")

    def test_a_skipped_required_test_is_no_data(self):
        self.script.write_text(self.script.read_text().replace(
            "    def test_measured", "    @unittest.skip('no evidence')\n    def test_measured"))
        self.assertEqual(smoke.run_case(self.case, self.root)["verdict"], "NO-DATA")

    def test_a_removed_test_file_is_no_data(self):
        self.script.unlink()
        result = smoke.run_case(self.case, self.root)
        self.assertEqual(result["verdict"], "NO-DATA")
        self.assertIsNone(result["exit_code"])

    def test_partial_timeout_output_never_becomes_a_pass(self):
        self.script.write_text("import time\nprint('partial evidence', flush=True)\ntime.sleep(10)\n")
        result = smoke.run_case(dict(self.case, timeout_seconds=0.5), self.root)
        self.assertEqual(result["verdict"], "NO-DATA")
        self.assertIn("partial evidence", result["stdout"])
        self.assertLess(result["duration_seconds"], 3)

    def family(self):
        return {"cases": [dict(self.case, id=name, command=list(self.case["command"]))
                          for name in sorted(smoke.REQUIRED_IDS)]}

    def write_family(self, data):
        path = self.root / "family.json"
        path.write_text(json.dumps(data))
        return path

    def test_missing_duplicate_and_malformed_families_never_execute(self):
        missing = self.family()
        missing["cases"].pop()
        duplicate = self.family()
        duplicate["cases"][-1]["id"] = "t2"
        bad_command = self.family()
        bad_command["cases"][0]["command"] = ["sh", "-c", "true"]
        for data in ([], {}, missing, duplicate, bad_command):
            with self.subTest(data=data), mock.patch.object(smoke, "run_case") as execute:
                report = smoke.run_family(self.write_family(data), self.root)
                self.assertEqual(report["verdict"], "NO-DATA")
                self.assertFalse(report["required_transition_ready"])
                execute.assert_not_called()

    def test_one_missing_case_blocks_transition_despite_other_real_passes(self):
        family = self.family()
        family["cases"][0]["command"][1] = "scripts/test_missing.py"
        with mock.patch.object(smoke, "identity", return_value={"commit": "abc"}):
            report = smoke.run_family(self.write_family(family), self.root)
        self.assertEqual([r["verdict"] for r in report["cases"]].count("PASS"), 3)
        self.assertEqual(report["verdict"], "NO-DATA")
        self.assertFalse(report["required_transition_ready"])

    def test_source_change_during_green_tests_blocks_transition(self):
        with mock.patch.object(smoke, "identity", side_effect=[{"commit": "before"},
                                                               {"commit": "after"}]):
            report = smoke.run_family(self.write_family(self.family()), self.root)
        self.assertTrue(all(r["verdict"] == "PASS" for r in report["cases"]))
        self.assertEqual(report["verdict"], "NO-DATA")
        self.assertFalse(report["required_transition_ready"])

    def test_editing_an_untracked_source_changes_identity(self):
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        subprocess.run(["git", "-c", "user.name=test", "-c", "user.email=test@example.invalid",
                        "commit", "--allow-empty", "-qm", "fixture"], cwd=self.root, check=True)
        before = smoke.identity(self.root)
        self.script.write_text(self.script.read_text() + "\n# changed fixture\n")
        after = smoke.identity(self.root)
        self.assertEqual(before["commit"], after["commit"])
        self.assertEqual(before["status_sha256"], after["status_sha256"])
        self.assertNotEqual(before["untracked_tree_sha256"], after["untracked_tree_sha256"])

    def test_failure_wins_over_unavailable_evidence(self):
        with mock.patch.object(smoke, "run_case", side_effect=[
                {"verdict": "NO-DATA"}, {"verdict": "FAIL"},
                {"verdict": "PASS"}, {"verdict": "PASS"}]):
            report = smoke.run_family(self.write_family(self.family()), self.root)
        self.assertEqual(report["verdict"], "FAIL")
        self.assertFalse(report["required_transition_ready"])

    def test_cli_retains_no_data_and_refuses_an_unwritable_receipt(self):
        report = {"cases": [], "verdict": "NO-DATA", "required_transition_ready": False}
        output = self.root / "evidence.json"
        with mock.patch.object(smoke, "run_family", return_value=report), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(smoke.main(["--output", str(output)]), 2)
            self.assertEqual(json.loads(output.read_text())["verdict"], "NO-DATA")
            self.assertEqual(smoke.main(["--output", str(self.root)]), 1)


if __name__ == "__main__":
    unittest.main()
