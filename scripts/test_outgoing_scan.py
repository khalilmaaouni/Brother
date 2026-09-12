import os
import subprocess
import sys
import tempfile
import unittest


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SCAN_SCRIPT = os.path.join(SCRIPT_DIR, "outgoing_scan.py")


def run(cmd, cwd=None, check=True):
    result = subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if check and result.returncode != 0:
        raise AssertionError(
            "command failed: %s\nstdout:\n%s\nstderr:\n%s"
            % (" ".join(cmd), result.stdout, result.stderr)
        )
    return result


def git(repo, *args):
    return run(["git", "-C", repo] + list(args))


class OutgoingScanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = self.tmp.name
        run(["git", "init", "-q", self.repo])
        git(self.repo, "config", "user.name", "t")
        git(self.repo, "config", "user.email", "t@t")
        git(self.repo, "checkout", "-q", "-b", "main")

    def tearDown(self):
        self.tmp.cleanup()

    def commit_file(self, name, content, message):
        path = os.path.join(self.repo, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content)
        git(self.repo, "add", name)
        git(self.repo, "commit", "-q", "-m", message)
        return git(self.repo, "rev-parse", "HEAD").stdout.strip()

    def scan(self, rev_range, terms_file=None, repo=None):
        repo = repo or self.repo
        if terms_file is None:
            terms_file = os.path.join(self.tmp.name, "terms")
            with open(terms_file, "w", encoding="utf-8") as handle:
                handle.write("")
        cmd = [
            sys.executable,
            SCAN_SCRIPT,
            rev_range,
            "--repo",
            repo,
            "--terms-file",
            terms_file,
        ]
        return run(cmd, check=False)

    def test_clean_commit_passes(self):
        base = self.commit_file("base.txt", "base\n", "base")
        self.commit_file("clean.txt", "hello world\n", "clean message")
        result = self.scan(base + "..HEAD")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("outgoing_scan: 0 hit(s)", result.stdout)

    def test_fake_secret_key_fails_and_is_masked(self):
        base = self.commit_file("base.txt", "base\n", "base")
        key = "sk_" + "a" * 24
        self.commit_file("key.txt", key + "\n", "add key")
        result = self.scan(base + "..HEAD")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("secret", result.stdout)
        self.assertNotIn(key, result.stdout)
        self.assertNotIn(key, result.stderr)

    def test_word_starting_with_task_lightweight_is_not_a_key(self):
        base = self.commit_file("base.txt", "base\n", "base")
        self.commit_file(
            "word.txt",
            "task_lightweight_xxxxxxxxxxxxxxxx\n",
            "add word",
        )
        result = self.scan(base + "..HEAD")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_co_authored_by_claude_fails(self):
        base = self.commit_file("base.txt", "base\n", "base")
        self.commit_file(
            "a.txt",
            "content\n",
            "subject\n\n" + "Co-" + "Authored-By: Claude <no" + "reply@" + "anthropic.com>\n",
        )
        result = self.scan(base + "..HEAD")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("attribution", result.stdout)

    def test_en_dash_in_message_fails(self):
        base = self.commit_file("base.txt", "base\n", "base")
        self.commit_file("a.txt", "content\n", "subject \u2013 dash\n")
        result = self.scan(base + "..HEAD")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("dash", result.stdout)

    def test_private_term_fails_and_is_masked(self):
        base = self.commit_file("base.txt", "base\n", "base")
        term = "VeryPrivateProjectName"
        terms_file = os.path.join(self.tmp.name, "terms")
        with open(terms_file, "w", encoding="utf-8") as handle:
            handle.write(term + "\n")
        self.commit_file("a.txt", "content " + term + "\n", "add private thing")
        result = self.scan(base + "..HEAD", terms_file=terms_file)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("private", result.stdout)
        self.assertNotIn(term, result.stdout)
        self.assertNotIn(term, result.stderr)

    def test_empty_range_exits_2(self):
        self.commit_file("a.txt", "content\n", "first")
        result = self.scan("HEAD..HEAD")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("NO-DATA", result.stderr)

    def test_missing_terms_file_exits_2(self):
        base = self.commit_file("base.txt", "base\n", "base")
        self.commit_file("a.txt", "content\n", "first")
        missing = os.path.join(self.tmp.name, "does-not-exist")
        result = self.scan(base + "..HEAD", terms_file=missing)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("NO-DATA", result.stderr)

    def test_clean_merge_of_clean_side_branch_exits_0(self):
        base = self.commit_file("base.txt", "base\n", "base")
        git(self.repo, "checkout", "-q", "-b", "side")
        self.commit_file("side.txt", "side\n", "side")
        git(self.repo, "checkout", "-q", "main")
        self.commit_file("main.txt", "main\n", "main")
        git(self.repo, "merge", "-q", "--no-ff", "side", "-m", "merge side")
        result = self.scan(base + "..HEAD")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_merge_conflict_resolution_with_en_dash_exits_1(self):
        base = self.commit_file("base.txt", "base\n", "base")
        git(self.repo, "checkout", "-q", "-b", "side")
        self.commit_file("conflict.txt", "side\n", "side")
        git(self.repo, "checkout", "-q", "main")
        self.commit_file("conflict.txt", "main\n", "main")
        run(["git", "-C", self.repo, "merge", "--no-ff", "side"], check=False)
        resolved = os.path.join(self.repo, "conflict.txt")
        with open(resolved, "w", encoding="utf-8") as handle:
            handle.write("resolved \u2013 dash\n")
        git(self.repo, "add", "conflict.txt")
        git(self.repo, "commit", "-q", "-m", "merge with dash")
        result = self.scan(base + "..HEAD")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("dash", result.stdout)

    def test_added_line_with_key_and_en_dash_masks_key(self):
        base = self.commit_file("base.txt", "base\n", "base")
        key = "sk_" + "b" * 24
        self.commit_file("both.txt", key + " \u2013 dash\n", "add both")
        result = self.scan(base + "..HEAD")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("secret", result.stdout)
        self.assertIn("dash", result.stdout)
        self.assertNotIn(key, result.stdout)
        self.assertNotIn(key, result.stderr)


if __name__ == "__main__":
    unittest.main()
