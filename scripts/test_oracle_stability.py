import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import oracle_stability as osx  # noqa: E402
import benchmark_harness as bh  # noqa: E402

ADD_TASK = bh.task_by_id("add-two-ints")

CORRECT_ADD_ARM = [
    sys.executable, "-c",
    "open('solution.py', 'w').write('def add(a, b):\\n    return a + b\\n')",
]

# Hardcodes the visible case only: stably passes visible, stably fails hidden.
GAMING_ARM = [
    sys.executable, "-c",
    "open('solution.py', 'w').write('def add(a, b):\\n    return 5\\n')",
]


def _fresh_git_fixture(root):
    """A minimal, throwaway git repo -- never the real Brother checkout --
    used to prove the mutation-detection wiring without touching anything
    real and without the disk-floor cost of cloning the actual repo."""
    os.makedirs(os.path.join(root, "scripts"), exist_ok=True)
    shutil.copy2(os.path.join(HERE, "benchmark_harness.py"), os.path.join(root, "scripts", "benchmark_harness.py"))
    shutil.copy2(os.path.join(HERE, "evidence_obligation.py"), os.path.join(root, "scripts", "evidence_obligation.py"))
    subprocess.run(["git", "init", "-q", root], check=True, stdin=subprocess.DEVNULL)
    subprocess.run(["git", "-C", root, "add", "-A"], check=True, stdin=subprocess.DEVNULL)
    subprocess.run(
        ["git", "-C", root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "t"],
        check=True, stdin=subprocess.DEVNULL,
    )


class ClassifyStabilityTests(unittest.TestCase):
    def test_agreement_on_expected_is_pass(self):
        verdict, reason = osx.classify_stability(["PASS", "PASS", "PASS"], "PASS")
        self.assertEqual(verdict, "PASS")

    def test_agreement_on_wrong_verdict_is_fail_not_no_data(self):
        """Stably wrong is a broken oracle, not a flaky one."""
        verdict, reason = osx.classify_stability(["FAIL", "FAIL", "FAIL"], "PASS")
        self.assertEqual(verdict, "FAIL")

    def test_disagreement_is_no_data(self):
        verdict, reason = osx.classify_stability(["PASS", "FAIL", "PASS"], "PASS")
        self.assertEqual(verdict, "NO-DATA")
        self.assertIn("flaky", reason)

    def test_empty_verdicts_is_no_data(self):
        verdict, reason = osx.classify_stability([], "PASS")
        self.assertEqual(verdict, "NO-DATA")


class RunStabilityCheckTests(unittest.TestCase):
    def test_stable_correct_arm_matches_expected_pass(self):
        result = osx.run_stability_check(ADD_TASK, "correct", CORRECT_ADD_ARM, "PASS", runs=3)
        self.assertEqual(result["verdict"], "PASS")
        self.assertEqual(result["run_verdicts"], ["PASS", "PASS", "PASS"])
        self.assertEqual(osx.exit_code_for_verdict(result["verdict"]), 0)

    def test_stable_wrong_arm_matching_expected_fail_is_pass(self):
        """The GAMING_ARM deterministically fails the hidden test every
        time -- if the caller correctly expected FAIL, that is a stable,
        trustworthy fixture."""
        result = osx.run_stability_check(ADD_TASK, "gaming", GAMING_ARM, "FAIL", runs=3)
        self.assertEqual(result["verdict"], "PASS")
        self.assertEqual(set(result["run_verdicts"]), {"FAIL"})

    def test_stable_but_mismatched_expectation_is_fail(self):
        result = osx.run_stability_check(ADD_TASK, "gaming", GAMING_ARM, "PASS", runs=3)
        self.assertEqual(result["verdict"], "FAIL")

    def test_flaky_arm_is_no_data(self):
        """An arm whose output alternates deterministically via an
        external counter file (not randomness, so this test itself is not
        flaky) -- run_stability_check must report NO-DATA, never a
        fabricated PASS or a silent FAIL that hides the disagreement."""
        counter_path = os.path.join(tempfile.mkdtemp(prefix="oracle-stability-flaky-"), "n.txt")
        flaky_arm = [
            sys.executable, "-c",
            "import os\n"
            "path = %r\n"
            "n = int(open(path).read()) if os.path.exists(path) else 0\n"
            "open(path, 'w').write(str(n + 1))\n"
            "body = ('def add(a, b):\\n    return a + b\\n' if n %% 2 == 0 "
            "else 'def add(a, b):\\n    return 0\\n')\n"
            "open('solution.py', 'w').write(body)\n" % counter_path,
        ]
        try:
            result = osx.run_stability_check(ADD_TASK, "flaky", flaky_arm, "PASS", runs=4)
            self.assertEqual(result["verdict"], "NO-DATA")
            self.assertIn("flaky", result["verdict_reason"])
            self.assertGreater(len(set(result["run_verdicts"])), 1)
        finally:
            shutil.rmtree(os.path.dirname(counter_path), ignore_errors=True)

    def test_leaked_temp_state_downgrades_a_would_be_pass_to_no_data(self):
        with mock.patch.object(osx, "_leftover_harness_temp_dirs", side_effect=[set(), {"bench-ws-leaked"}]):
            result = osx.run_stability_check(ADD_TASK, "correct", CORRECT_ADD_ARM, "PASS", runs=1)
        self.assertEqual(result["verdict"], "NO-DATA")
        self.assertEqual(result["temp_state_leak"], ["bench-ws-leaked"])
        self.assertIn("leftover temp state", result["verdict_reason"])

    def test_repo_mutation_downgrades_a_would_be_pass_to_no_data(self):
        with mock.patch.object(osx, "_git_status_porcelain", side_effect=["", " M some/file.py"]):
            result = osx.run_stability_check(ADD_TASK, "correct", CORRECT_ADD_ARM, "PASS", runs=1)
        self.assertEqual(result["verdict"], "NO-DATA")
        self.assertTrue(result["repo_mutation_detected"])
        self.assertIn("config mutation", result["verdict_reason"])

    def test_unchecked_git_status_is_none_not_a_false_mutation(self):
        """A repo_root git cannot inspect (git missing, not a repo) must
        report repo_mutation_detected as None, not silently downgrade a
        real PASS -- None is "could not check", not "clean"."""
        with mock.patch.object(osx, "_git_status_porcelain", return_value=None):
            result = osx.run_stability_check(ADD_TASK, "correct", CORRECT_ADD_ARM, "PASS", runs=1)
        self.assertIsNone(result["repo_mutation_detected"])
        self.assertEqual(result["verdict"], "PASS")

    def test_clean_clone_not_requested_by_default_is_reported_honestly(self):
        result = osx.run_stability_check(ADD_TASK, "correct", CORRECT_ADD_ARM, "PASS", runs=1)
        self.assertEqual(result["clean_clone"], "NOT-REQUESTED")


class CheckCleanCloneTests(unittest.TestCase):
    def test_not_a_git_repo_is_no_data(self):
        plain_dir = tempfile.mkdtemp(prefix="oracle-stability-not-git-")
        try:
            result = osx.check_clean_clone(plain_dir, ADD_TASK, "correct", CORRECT_ADD_ARM, "PASS", timeout=30)
            self.assertEqual(result["verdict"], "NO-DATA")
            self.assertIn("not a git repository", result["reason"])
        finally:
            shutil.rmtree(plain_dir, ignore_errors=True)

    def test_tiny_fixture_repo_clean_clone_reproduces_expected_verdict(self):
        """Never clones the real Brother checkout (disk-floor cost, and
        not the point) -- a minimal synthetic repo carrying only
        benchmark_harness.py + evidence_obligation.py is enough to prove
        the clone-and-rerun mechanics work end to end."""
        fixture_root = tempfile.mkdtemp(prefix="oracle-stability-fixture-repo-")
        try:
            _fresh_git_fixture(fixture_root)
            result = osx.check_clean_clone(fixture_root, ADD_TASK, "correct", CORRECT_ADD_ARM, "PASS", timeout=30)
            self.assertEqual(result["verdict"], "PASS", result)
            self.assertEqual(result["clone_verdict"], "PASS")
        finally:
            shutil.rmtree(fixture_root, ignore_errors=True)

    def test_tiny_fixture_repo_clean_clone_disagreement_is_fail(self):
        fixture_root = tempfile.mkdtemp(prefix="oracle-stability-fixture-repo-")
        try:
            _fresh_git_fixture(fixture_root)
            result = osx.check_clean_clone(fixture_root, ADD_TASK, "gaming", GAMING_ARM, "PASS", timeout=30)
            self.assertEqual(result["verdict"], "FAIL")
            self.assertEqual(result["clone_verdict"], "FAIL")
        finally:
            shutil.rmtree(fixture_root, ignore_errors=True)


class CliTests(unittest.TestCase):
    def test_list_tasks_exits_zero(self):
        self.assertEqual(osx.main(["--list-tasks"]), 0)

    def test_missing_task_errors(self):
        with self.assertRaises(SystemExit):
            osx.main(["--expected-verdict", "PASS"])

    def test_missing_expected_verdict_errors(self):
        with self.assertRaises(SystemExit):
            osx.main(["--task", "add-two-ints"])

    def test_unknown_task_id_is_no_data_exit_2(self):
        exit_code = osx.main(["--task", "does-not-exist", "--expected-verdict", "PASS"])
        self.assertEqual(exit_code, 2)


if __name__ == "__main__":
    unittest.main()
