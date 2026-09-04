"""What identity_guard must keep true, driven against real scratch git
repositories rather than mocked git output: the whole point of this guard is
`git config` and commit trailers, which only a real repository actually has.

Every term used here is FAKE ("WIDGETCO"). Real ones live outside every
repository, at ~/.brothersbe-private-names, never in a fixture a test
commits: a scanner's own fixtures publishing what it exists to stop is the
exact mistake private_terms_scan.py's docstring records.
"""
import contextlib
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import identity_guard as G  # noqa: E402

# E100: one sandbox for every temp tree this process makes, removed at exit.
import os as _e100_os, sys as _e100_sys  # noqa: E402
_e100_sys.path.append(_e100_os.path.join(
    _e100_os.path.dirname(_e100_os.path.abspath(__file__)), '.'))
try:  # noqa: E402
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    # A packager (scripts/export_public.py, make_benchmark_bundle.py)
    # can copy this test without scripts/tmp_sandbox.py beside it. Say
    # so rather than dying: the sandbox is hygiene, not the subject.
    _e100_sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % _e100_os.path.basename(__file__))


def _run(cmd, cwd=None, env=None):
    return subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)


def _git_env(name, email):
    env = os.environ.copy()
    env["GIT_AUTHOR_NAME"] = name
    env["GIT_AUTHOR_EMAIL"] = email
    env["GIT_COMMITTER_NAME"] = name
    env["GIT_COMMITTER_EMAIL"] = email
    return env


@contextlib.contextmanager
def _terms_env(path):
    """BROTHER_PRIVATE_TERMS points at `path` for the duration of the block,
    restored after, so a fixture list never leaks into another test."""
    old = os.environ.get("BROTHER_PRIVATE_TERMS")
    os.environ["BROTHER_PRIVATE_TERMS"] = path
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("BROTHER_PRIVATE_TERMS", None)
        else:
            os.environ["BROTHER_PRIVATE_TERMS"] = old


class ScratchRepo(object):
    """A bare 'origin' plus a clone with origin/HEAD set: the same shape a
    real push target has, so outgoing_range has a real origin/main..HEAD to
    compute rather than a guess."""

    def __init__(self):
        self.root = tempfile.mkdtemp(prefix="identity-guard-")
        self.bare = os.path.join(self.root, "origin.git")
        self.clone = os.path.join(self.root, "clone")
        self._n = 0
        _run(["git", "init", "--quiet", "--bare", self.bare])
        _run(["git", "clone", "--quiet", self.bare, self.clone])
        env = _git_env("Scratch Author", "scratch@example.com")
        with open(os.path.join(self.clone, "README"), "w") as fh:
            fh.write("seed\n")
        _run(["git", "add", "README"], cwd=self.clone, env=env)
        _run(["git", "commit", "--quiet", "-m", "seed"], cwd=self.clone, env=env)
        _run(["git", "branch", "-M", "main"], cwd=self.clone, env=env)
        _run(["git", "push", "--quiet", "-u", "origin", "main"], cwd=self.clone, env=env)
        # init --bare has no branches at clone time, so the clone never got
        # origin/HEAD set the way a clone of a populated remote would. This
        # is what `git remote set-head origin -a` does right after a push.
        _run(["git", "remote", "set-head", "origin", "-a"], cwd=self.clone, env=env)

    def commit(self, name, email, message):
        """A new, NOT YET PUSHED commit authored and committed as
        `name`/`email`. Returns its short SHA."""
        self._n += 1
        env = _git_env(name, email)
        with open(os.path.join(self.clone, "file_%d.txt" % self._n), "w") as fh:
            fh.write(message + "\n")
        _run(["git", "add", "."], cwd=self.clone, env=env)
        _run(["git", "commit", "--quiet", "-m", message], cwd=self.clone, env=env)
        return _run(["git", "rev-parse", "--short", "HEAD"], cwd=self.clone).stdout.strip()

    def set_local_identity(self, name, email):
        _run(["git", "config", "user.name", name], cwd=self.clone)
        _run(["git", "config", "user.email", email], cwd=self.clone)

    def terms_file(self, *terms):
        path = os.path.join(self.root, "terms.txt")
        with open(path, "w") as fh:
            fh.write("\n".join(terms) + "\n")
        return path

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)


class IdentityGuardOnARealRepo(unittest.TestCase):
    def setUp(self):
        self.repo = ScratchRepo()
        self.repo.set_local_identity("Scratch Author", "scratch@example.com")

    def tearDown(self):
        self.repo.cleanup()

    def test_a_clean_identity_passes(self):
        terms = self.repo.terms_file("WIDGETCO")
        with _terms_env(terms):
            code, lines = G.run_guard(cwd=self.repo.clone)
        self.assertEqual(code, G.EXIT_CLEAN, lines)
        self.assertTrue(any(l.startswith("PASS") for l in lines), lines)

    def test_a_commit_with_a_listed_domain_email_in_the_outgoing_range_fails_naming_the_sha(self):
        terms = self.repo.terms_file("WIDGETCO")
        sha = self.repo.commit("Someone", "someone@widgetco-example.com", "bad commit")
        with _terms_env(terms):
            code, lines = G.run_guard(cwd=self.repo.clone)
        self.assertEqual(code, G.EXIT_FOUND, lines)
        self.assertTrue(any(sha in l for l in lines), lines)

    def test_config_level_listed_domain_fails_even_with_no_new_commits(self):
        terms = self.repo.terms_file("WIDGETCO")
        self.repo.set_local_identity("Someone", "someone@widgetco-example.com")
        with _terms_env(terms):
            code, lines = G.run_guard(cwd=self.repo.clone)
        self.assertEqual(code, G.EXIT_FOUND, lines)
        self.assertTrue(any("config identity" in l for l in lines), lines)

    def test_missing_terms_file_is_no_data_not_a_pass(self):
        missing = os.path.join(self.repo.root, "no-such-file.txt")
        with _terms_env(missing):
            code, lines = G.run_guard(cwd=self.repo.clone)
        self.assertEqual(code, G.EXIT_NO_DATA, lines)
        self.assertTrue(any(l.startswith("NO-DATA") for l in lines), lines)

    def test_the_term_and_the_address_never_appear_in_the_output(self):
        """FAIL and PASS lines both carry only NAME-N, a SHA, and a count:
        never the term itself or the address it matched."""
        terms = self.repo.terms_file("WIDGETCO")
        self.repo.commit("Someone", "leak@widgetco-example.com", "bad commit 2")
        with _terms_env(terms):
            code, lines = G.run_guard(cwd=self.repo.clone)
        self.assertEqual(code, G.EXIT_FOUND, lines)
        joined = "\n".join(lines)
        self.assertNotIn("widgetco", joined.lower())
        self.assertNotIn("leak@widgetco-example.com", joined)


class PureMatchingHelpers(unittest.TestCase):
    """Cheap, no-repo checks on the two building blocks the repo-level tests
    above rely on."""

    def test_domain_of_splits_on_the_last_at(self):
        self.assertEqual(G.domain_of("a@b@widgetco.example"), "widgetco.example")
        self.assertEqual(G.domain_of("no-at-sign"), "")

    def test_pattern_for_is_whole_token_and_case_insensitive(self):
        self.assertTrue(G.hit_terms("user@widgetco-example.com", ["WIDGETCO"]))
        self.assertTrue(G.hit_terms("user@WIDGETCO-example.com", ["widgetco"]))
        self.assertFalse(G.hit_terms("user@notwidgetcoish.com", ["WIDGETCO"]))

    def test_default_terms_file_is_the_estates_law_list(self):
        # check_all.sh runs this guard standalone, no environment, so the
        # default alone must resolve to the same list export_public.py,
        # private_terms_scan.py and cleanse.sh already use.
        self.assertEqual(G.DEFAULT_TERMS_FILE,
                          os.path.expanduser("~/.brothersbe-private-names"))


if __name__ == "__main__":
    unittest.main()
