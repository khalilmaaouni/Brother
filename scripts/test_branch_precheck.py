#!/usr/bin/env python3
"""scripts/branch_precheck.sh (C2.a): a branch tip gets PASS, FAIL or NO-DATA per check in a record named by its sha.

Every run is against a fixture repository built in a temp folder: the script's record, its scratch worktree and the
fixture's own git state all live there, and nothing in this tree is written.

Run: python3 -B scripts/test_branch_precheck.py
"""
import math, os, re, shutil, subprocess, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "branch_precheck.sh")
SHA = re.compile(r"[0-9a-f]{40}")
VERDICTS = ("PASS", "FAIL", "NO-DATA")
PASSING = "import unittest\n\nclass T(unittest.TestCase):\n    def test_ok(self):\n        self.assertTrue(True)\n\n" \
          "if __name__ == \"__main__\":\n    unittest.main()\n"
FAILING = PASSING.replace("assertTrue(True)", "assertTrue(False)")


def _clean_env(home):
    """The caller's environment without git's own variables (a fixture is its own repository), HOME and TMPDIR inside it."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_") and k != "BROTHER_SCRATCH"}
    env.update(HOME=home, TMPDIR=home, PYTHONDONTWRITEBYTECODE="1", GIT_CONFIG_NOSYSTEM="1",
               GIT_AUTHOR_NAME="fixture", GIT_AUTHOR_EMAIL="fixture@example.invalid",
               GIT_COMMITTER_NAME="fixture", GIT_COMMITTER_EMAIL="fixture@example.invalid")
    return env


def run_precheck(repo, branch, done_check, scratch):
    """Run the script in `repo` on `branch` with `done_check`; `scratch` is BROTHER_SCRATCH, None leaves it unset.
    Answers (exit code, record text), the text '' when no record names the branch tip. Bad input raises ValueError."""
    if not isinstance(repo, str) or not repo or not os.path.isdir(repo):
        raise ValueError("repo must be an existing folder")
    if not isinstance(branch, str) or not isinstance(done_check, str):
        raise ValueError("branch and done_check must be str")
    if scratch is not None and not isinstance(scratch, str):
        raise ValueError("scratch must be str or None")
    if "\0" in branch + done_check + (scratch or ""):
        raise ValueError("a NUL byte cannot pass through argv")
    env = _clean_env(repo)
    if scratch is not None:
        env["BROTHER_SCRATCH"] = scratch
    r = subprocess.run(["/bin/sh", SCRIPT, branch, done_check], cwd=repo, env=env, capture_output=True, text=True,
                       timeout=300)
    tip = subprocess.run(["git", "rev-parse", "--verify", "--quiet", branch + "^{commit}"], cwd=repo, env=env,
                         capture_output=True, text=True) if branch and not branch.startswith("-") else None
    text = ""
    if tip is not None and tip.returncode == 0:
        path = os.path.join(repo, "logs", "precheck-%s.txt" % tip.stdout.strip())
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
    return r.returncode, text


def record_rows(text):
    """The record as a list: ("row", name, verdict, exit) per check line and ("sha", hex) for the sha line.
    Anything else, a verdict outside PASS, FAIL, NO-DATA or a sha that is not 40 hex, is corrupt and raises ValueError."""
    if not isinstance(text, str):
        raise ValueError("the record must be str")
    out = []
    for line in text.splitlines():
        parts = line.split(" | ")
        if len(parts) == 4 and parts[0] == "row" and parts[1] and parts[2] in VERDICTS \
                and re.fullmatch(r"exit (\d+|none)", parts[3]):
            out.append(("row", parts[1], parts[2], parts[3][5:]))
        elif len(parts) == 2 and parts[0] == "sha" and SHA.fullmatch(parts[1]):
            out.append(("sha", parts[1]))
        else:
            raise ValueError("corrupt record line: %r" % line)
    return out


def _git(repo, env, *args):
    """Fixture git, argv literal at every call site (`args` are constants in this file)."""
    r = subprocess.run(["git"] + list(args), cwd=repo, env=env, capture_output=True, text=True)
    if r.returncode != 0:
        raise AssertionError("fixture git %s failed: %s" % (args[0], r.stderr))
    return r.stdout.strip()


def _write(repo, rel, body):
    path = os.path.join(repo, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)


class Fixture(unittest.TestCase):
    """A repository with branch green (every check passes), red (its done check fails) and bare (no canary)."""

    @classmethod
    def setUpClass(cls):
        cls.top = tempfile.mkdtemp(prefix="precheck-")
        cls.repo = os.path.join(cls.top, "repo")
        os.makedirs(cls.repo)
        env = _clean_env(cls.repo)
        _git(cls.repo, env, "init", "-q")
        _write(cls.repo, "scripts/test_ok.py", PASSING)
        _write(cls.repo, "scripts/test_deploy_canary.py", PASSING)
        _write(cls.repo, "scripts/test_loop_tool_parity.py", PASSING)
        _write(cls.repo, "pkg/__init__.py", "")
        _write(cls.repo, "pkg/test_mod.py", PASSING)
        _write(cls.repo, ".gitignore", "logs/\n")
        _git(cls.repo, env, "add", "-A")
        _git(cls.repo, env, "commit", "-q", "-m", "base")
        _git(cls.repo, env, "branch", "green")
        _git(cls.repo, env, "checkout", "-q", "-b", "red")
        _write(cls.repo, "scripts/test_red.py", FAILING)
        _git(cls.repo, env, "add", "-A")
        _git(cls.repo, env, "commit", "-q", "-m", "red")
        _git(cls.repo, env, "checkout", "-q", "-b", "bare", "green")
        os.remove(os.path.join(cls.repo, "scripts", "test_deploy_canary.py"))
        _git(cls.repo, env, "add", "-A")
        _git(cls.repo, env, "commit", "-q", "-m", "bare")
        _git(cls.repo, env, "checkout", "-q", "green")
        cls.env = env

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.top, ignore_errors=True)

    def setUp(self):
        self.scratch = tempfile.mkdtemp(prefix="scratch ", dir=self.top)   # a space in the path (S7)
        shutil.rmtree(os.path.join(self.repo, "logs"), ignore_errors=True)

    def tip(self, branch):
        return _git(self.repo, self.env, "rev-parse", branch)

    def no_record(self):
        return not os.path.isdir(os.path.join(self.repo, "logs")) or not os.listdir(os.path.join(self.repo, "logs"))

    def assertNoWorktreeLeft(self):
        self.assertEqual(os.listdir(self.scratch), [])
        listed = _git(self.repo, self.env, "worktree", "list", "--porcelain")
        self.assertEqual(listed.count("worktree "), 1, listed)


class Verdicts(Fixture):
    def test_a_red_done_check_gives_fail(self):
        code, text = run_precheck(self.repo, "red", "python3 -B scripts/test_red.py", self.scratch)
        self.assertEqual(code, 1)
        rows = record_rows(text)
        self.assertEqual(rows[0][:3], ("row", "done_check", "FAIL"))
        self.assertEqual(rows[0][3], "1")
        self.assertEqual([r[2] for r in rows[1:3]], ["PASS", "PASS"])
        self.assertNoWorktreeLeft()

    def test_a_missing_test_is_no_data(self):
        code, text = run_precheck(self.repo, "green", "python3 -B scripts/test_absent.py", self.scratch)
        self.assertEqual(code, 3)
        self.assertEqual(record_rows(text)[0], ("row", "done_check", "NO-DATA", "none"))
        code, text = run_precheck(self.repo, "green", "python3 -B -m unittest pkg.test_gone", self.scratch)
        self.assertEqual(code, 3)
        self.assertEqual(record_rows(text)[0], ("row", "done_check", "NO-DATA", "none"))
        code, text = run_precheck(self.repo, "bare", "python3 -B scripts/test_ok.py", self.scratch)
        self.assertEqual(code, 3)
        self.assertIn(("row", "canary", "NO-DATA", "none"), record_rows(text))
        self.assertNoWorktreeLeft()

    def test_the_record_names_the_sha(self):
        code, text = run_precheck(self.repo, "green", "python3 -B scripts/test_ok.py", self.scratch)
        self.assertEqual(code, 0)
        rows = record_rows(text)
        self.assertIn(("sha", self.tip("green")), rows)
        self.assertEqual([r[1:3] for r in rows if r[0] == "row"],
                         [("done_check", "PASS"), ("canary", "PASS"), ("parity", "PASS")])
        self.assertEqual(os.listdir(os.path.join(self.repo, "logs")), ["precheck-%s.txt" % self.tip("green")])
        self.assertNoWorktreeLeft()

    def test_a_module_done_check_and_a_rerun_rewrite_one_record(self):
        for _ in range(2):
            code, text = run_precheck(self.repo, "green", "python3 -B -m unittest pkg.test_mod.T -v", self.scratch)
            self.assertEqual(code, 0)
            self.assertEqual(record_rows(text)[0], ("row", "done_check", "PASS", "0"))
        code, _ = run_precheck(self.repo, "red", "python3 -B scripts/test_ok.py", self.scratch)
        self.assertEqual(code, 0)
        self.assertEqual(sorted(os.listdir(os.path.join(self.repo, "logs"))),
                         sorted("precheck-%s.txt" % self.tip(b) for b in ("green", "red")))   # stale: another file
        self.assertNoWorktreeLeft()


class Refusals(Fixture):
    def test_no_scratch_refuses(self):
        for scratch in (None, "", "relative/scratch"):
            code, text = run_precheck(self.repo, "green", "python3 -B scripts/test_ok.py", scratch)
            self.assertEqual(code, 3, scratch)
            self.assertEqual(text, "")
            self.assertTrue(self.no_record())

    def test_a_chain_is_refused(self):
        for check in ("python3 -B scripts/test_ok.py && python3 -B scripts/test_ok.py",
                      "python3 -B scripts/test_ok.py ; true", "python3 -B scripts/test_ok.py | true",
                      "python3 -B scripts/test_ok.py\ntrue", "python3 -c print", "sh scripts/test_ok.py",
                      "python3 -B -m unittest -k ok", "python3 scripts/../test_ok.py", ""):
            code, text = run_precheck(self.repo, "green", check, self.scratch)
            self.assertEqual(code, 3, check)
            self.assertTrue(self.no_record(), check)

    def test_a_branch_that_cannot_resolve_exits_3(self):
        for branch in ("", "no-such-branch", "-h", "--all"):
            code, text = run_precheck(self.repo, branch, "python3 -B scripts/test_ok.py", self.scratch)
            self.assertEqual(code, 3, branch)
            self.assertTrue(self.no_record(), branch)
        self.assertNoWorktreeLeft()


class HostileInput(unittest.TestCase):
    VALUES = (None, 0, True, -1, math.nan, "", "x", b"x", [], ["x"], {}, {"a": 1}, (), {1, 2}, object())

    def test_every_public_function_returns_or_refuses(self):
        for fn, arity in ((run_precheck, 4), (record_rows, 1)):
            for v in self.VALUES:
                try:
                    fn(*([v] * arity))
                except (ValueError, LookupError):
                    pass

    def test_corrupt_records_raise(self):
        for text in ("row | a | MAYBE | exit 0", "sha | 1234", "row | a | PASS", "junk"):
            with self.assertRaises(ValueError):
                record_rows(text)


if __name__ == "__main__":
    unittest.main()
