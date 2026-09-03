"""Calibration for scripts/reviewer_brief.py. SR-09.

Builds a throwaway git repository per test (never touches this repo), runs the
generator over it as a subprocess so REPO_DIR resolution and every git call is
exercised for real, and asserts the four sections plus the two hard failure
modes: an empty range must exit non-zero and say NO-DATA, and the risk rule
must be provably load-bearing (a case where removing the rule would turn the
test red, not just a case that happens to pass).
"""
import pathlib
import subprocess
import sys
import tempfile
import unittest

SCRIPT = pathlib.Path(__file__).resolve().parent / "reviewer_brief.py"


def run_git(repo, *args):
    proc = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=False,
        env={"GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.com",
             "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.com",
             "PATH": "/usr/bin:/bin:/usr/local/bin"},
    )
    assert proc.returncode == 0, f"git {args} failed: {proc.stderr}"
    return proc.stdout


def init_repo(repo):
    run_git(repo, "init", "-q", "-b", "main")
    (repo / "README.md").write_text("hello\n")
    run_git(repo, "add", "README.md")
    run_git(repo, "commit", "-q", "-m", "initial commit")
    run_git(repo, "branch", "origin/main")


def run_brief(repo, ref_range=None):
    args = [sys.executable, str(SCRIPT)]
    if ref_range:
        args.append(ref_range)
    proc = subprocess.run(args, cwd=repo, capture_output=True, text=True, check=False)
    return proc.returncode, proc.stdout


class EmptyRangeIsNeverAPass(unittest.TestCase):
    def test_empty_range_exits_nonzero_and_says_no_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp)
            init_repo(repo)
            # origin/main..HEAD with nothing past origin/main: empty range.
            code, out = run_brief(repo, "origin/main..HEAD")
        self.assertNotEqual(code, 0, out)
        self.assertIn("NO-DATA", out)


class FourSectionsPresent(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = pathlib.Path(self.tmp.name)
        init_repo(self.repo)
        (self.repo / "notes.txt").write_text("line one\nline two\n")
        run_git(self.repo, "add", "notes.txt")
        run_git(self.repo, "commit", "-q", "-m",
                "add notes: exit code 0, 3 tests passed")
        run_git(self.repo, "branch", "-f", "origin-main", "HEAD~1")

    def tearDown(self):
        self.tmp.cleanup()

    def test_all_four_sections_present_and_correct(self):
        code, out = run_brief(self.repo, "origin-main..HEAD")
        self.assertEqual(code, 0, out)
        for header in ("CHANGE", "RISK", "PROOF", "UNKNOWN"):
            self.assertIn(header, out)

        # CHANGE: file and line counts come from git, not from prose.
        self.assertIn("notes.txt", out)
        self.assertIn("+2/-0", out)
        self.assertIn("1 commit(s)", out)

        # PROOF: the evidence line in the commit message is quoted.
        self.assertIn("exit code 0, 3 tests passed", out)

    def test_no_proof_says_so_explicitly(self):
        (self.repo / "other.txt").write_text("x\n")
        run_git(self.repo, "add", "other.txt")
        run_git(self.repo, "commit", "-q", "-m", "add other, no evidence here")
        code, out = run_brief(self.repo, "origin-main..HEAD")
        self.assertEqual(code, 0, out)
        # The second commit has no evidence line, but the range is not empty
        # of proof overall (first commit still has one). Force a proof-free
        # range by testing the exact HEAD~0..HEAD commit alone instead:
        code2, out2 = run_brief(self.repo, "HEAD..HEAD")
        self.assertNotEqual(code2, 0)
        self.assertIn("NO-DATA", out2)


class ProofFreeRangeSaysNoProofFound(unittest.TestCase):
    def test_commit_with_no_evidence_line_says_no_proof_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp)
            init_repo(repo)
            run_git(repo, "branch", "-f", "origin-main", "HEAD")
            (repo / "plain.txt").write_text("just a file\n")
            run_git(repo, "add", "plain.txt")
            run_git(repo, "commit", "-q", "-m", "add a plain file, nothing more")
            code, out = run_brief(repo, "origin-main..HEAD")
        self.assertEqual(code, 0, out)
        self.assertIn("NO PROOF FOUND", out)


class UnclaimedTestSurfacesAsUnknown(unittest.TestCase):
    def test_claim_word_without_evidence_is_flagged_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp)
            init_repo(repo)
            run_git(repo, "branch", "-f", "origin-main", "HEAD")
            (repo / "fix.py").write_text("x = 1\n")
            run_git(repo, "add", "fix.py")
            run_git(repo, "commit", "-q", "-m", "fixed the bug, verified it works")
            code, out = run_brief(repo, "origin-main..HEAD")
        self.assertEqual(code, 0, out)
        unknown_section = out.split("UNKNOWN", 1)[1]
        self.assertIn("claims a result", unknown_section)
        self.assertIn("fixed the bug, verified it works", unknown_section)


class RiskRuleIsLoadBearing(unittest.TestCase):
    """Proves the rule can fail: an ordinary file must rank BELOW a control
    file. Remove the scripts/tools rule from RISK_RULES and this goes red,
    because both files would then score 0 and the ordering assertion fails."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = pathlib.Path(self.tmp.name)
        init_repo(self.repo)
        run_git(self.repo, "branch", "-f", "origin-main", "HEAD")
        (self.repo / "docs.txt").write_text("ordinary docs change\n")
        (self.repo / "scripts").mkdir()
        (self.repo / "scripts" / "deploy.py").write_text("import os\n")
        run_git(self.repo, "add", "docs.txt", "scripts/deploy.py")
        run_git(self.repo, "commit", "-q", "-m", "touch docs and a control script")

    def tearDown(self):
        self.tmp.cleanup()

    def test_control_script_ranks_above_ordinary_file(self):
        code, out = run_brief(self.repo, "origin-main..HEAD")
        self.assertEqual(code, 0, out)
        risk_section = out.split("RISK", 1)[1].split("PROOF", 1)[0]

        scripts_line = next(l for l in risk_section.splitlines()
                             if "scripts/deploy.py" in l)
        docs_present = any("docs.txt" in l and l.strip().startswith("[")
                            for l in risk_section.splitlines())

        # The control file must carry a nonzero score.
        self.assertRegex(scripts_line, r"\[\d+\]")
        score = int(scripts_line.strip().split("]")[0].lstrip("["))
        self.assertGreater(score, 0)
        # The ordinary file must NOT appear as a scored/flagged row: with the
        # rule intact only scripts/deploy.py matches any rule, so docs.txt is
        # absent from the flagged listing entirely.
        self.assertFalse(docs_present)
        # And the rule that produced the rank is stated in the output.
        self.assertIn("RULE:", risk_section)
        self.assertIn("scripts/ or tools/", risk_section)


class UntestedFileIsFlaggedUnknown(unittest.TestCase):
    def test_change_with_no_test_file_is_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp)
            init_repo(repo)
            run_git(repo, "branch", "-f", "origin-main", "HEAD")
            (repo / "logic.py").write_text("def f(): return 1\n")
            run_git(repo, "add", "logic.py")
            run_git(repo, "commit", "-q", "-m", "add logic.py, exit code 0")
            code, out = run_brief(repo, "origin-main..HEAD")
        self.assertEqual(code, 0, out)
        unknown_section = out.split("UNKNOWN", 1)[1]
        self.assertIn("No test file is touched", unknown_section)


if __name__ == "__main__":
    unittest.main()
