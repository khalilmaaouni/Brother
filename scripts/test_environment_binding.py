"""Calibration for scripts/environment_binding.py.

Every case that checks main()'s outcome asserts the EXIT CODE, never only
the printed text. That is this estate's most expensive recorded lesson: a
gate once printed FAIL and exited 0, and eleven tests passed over it because
every one of them asserted the verdict STRING instead of the code a wrapper
or && chain actually reads.

The cases below are built directly from the edges named in this unit's
brief: a symlinked cwd (macOS's /tmp against /private/tmp), a repository
root that differs because the check ran in the wrong tree, PYTHONPATH
pointing at a different checkout, the interpreter changing between bind and
verify, a machine with no git, a path that contains a space, and volatile
environment variables (TERM, a session id) that must never register as
drift.
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import environment_binding as eb


def _init_repo(path):
    subprocess.run(["git", "init", "-q", path], check=True, timeout=30)


class BindShape(unittest.TestCase):

    def test_bind_has_exactly_the_pinned_fields(self):
        binding = eb.bind()
        self.assertEqual(set(binding.keys()), set(eb._FIELDS))

    def test_bind_is_json_serialisable(self):
        binding = eb.bind()
        round_tripped = json.loads(json.dumps(binding))
        self.assertEqual(round_tripped, binding)

    def test_cwd_is_resolved_through_realpath(self):
        binding = eb.bind()
        self.assertEqual(binding["cwd"], os.path.realpath(os.getcwd()))

    def test_interpreter_is_resolved_through_realpath(self):
        binding = eb.bind()
        self.assertEqual(binding["interpreter"], os.path.realpath(sys.executable))


class ImmediateVerify(unittest.TestCase):
    """Nothing has changed between bind() and verify(): this must always
    agree with itself. A binding that cannot pass its own immediate
    self-check is not evidence of anything."""

    def test_verify_immediately_after_bind_agrees(self):
        binding = eb.bind()
        ok, mismatches = eb.verify(binding)
        self.assertTrue(ok)
        self.assertEqual(mismatches, [])

    def test_verify_raises_on_a_binding_missing_a_field(self):
        binding = eb.bind()
        del binding["repo_root"]
        with self.assertRaises(KeyError):
            eb.verify(binding)

    def test_verify_raises_on_an_empty_dict(self):
        with self.assertRaises(KeyError):
            eb.verify({})


class SymlinkedCwd(unittest.TestCase):
    """THE NAMED EDGE: macOS resolves /tmp as a symlink to /private/tmp. A
    binding taken from inside the symlinked path must read as the SAME
    place as one taken from the real path, never as drift."""

    def test_cwd_through_a_symlink_resolves_to_the_real_directory(self):
        with tempfile.TemporaryDirectory() as real_dir:
            real_dir = os.path.realpath(real_dir)
            link_dir = real_dir + "-symlink"
            os.symlink(real_dir, link_dir)
            saved_cwd = os.getcwd()
            try:
                os.chdir(link_dir)
                binding = eb.bind()
                self.assertEqual(binding["cwd"], real_dir)
                self.assertNotEqual(binding["cwd"], link_dir)
            finally:
                os.chdir(saved_cwd)
                os.remove(link_dir)

    def test_binding_from_the_symlink_and_the_real_path_agree(self):
        with tempfile.TemporaryDirectory() as real_dir:
            real_dir = os.path.realpath(real_dir)
            link_dir = real_dir + "-symlink"
            os.symlink(real_dir, link_dir)
            saved_cwd = os.getcwd()
            try:
                os.chdir(real_dir)
                from_real = eb.bind()
                os.chdir(link_dir)
                ok, mismatches = eb.verify(from_real)
                self.assertTrue(ok, mismatches)
            finally:
                os.chdir(saved_cwd)
                os.remove(link_dir)


class RepositoryRoot(unittest.TestCase):
    """THE NAMED EDGE: a green produced from the wrong repository root
    looks exactly like a red being cleared, unless the root itself is
    pinned and checked."""

    def test_repo_root_is_this_worktrees_own_toplevel(self):
        binding = eb.bind()
        expected = subprocess.run(
            ["git", "-C", os.getcwd(), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=30, check=True,
        ).stdout.strip()
        self.assertEqual(binding["repo_root"], os.path.realpath(expected))

    def test_a_different_repository_root_is_caught_as_a_mismatch(self):
        saved_cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as other_repo:
            _init_repo(other_repo)
            try:
                os.chdir(saved_cwd)
                bound_here = eb.bind()
                os.chdir(other_repo)
                ok, mismatches = eb.verify(bound_here)
            finally:
                os.chdir(saved_cwd)
        self.assertFalse(ok)
        self.assertTrue(any(m.startswith("repo_root:") for m in mismatches))

    def test_not_a_repository_reports_none_not_an_exception(self):
        with tempfile.TemporaryDirectory() as plain_dir:
            self.assertIsNone(eb._repo_root(plain_dir))

    def test_missing_git_binary_reports_none_not_an_exception(self):
        with mock.patch("subprocess.run", side_effect=FileNotFoundError("no git")):
            self.assertIsNone(eb._repo_root(os.getcwd()))

    def test_git_timeout_reports_none_not_an_exception(self):
        with mock.patch("subprocess.run",
                         side_effect=subprocess.TimeoutExpired(cmd="git", timeout=30)):
            self.assertIsNone(eb._repo_root(os.getcwd()))


class PythonPathDrift(unittest.TestCase):
    """THE NAMED EDGE: a suite run with PYTHONPATH pointing at a different
    checkout tests that checkout's own copy of the code, not the one the
    caller believes it is testing."""

    def test_pythonpath_pointing_elsewhere_is_caught_as_a_mismatch(self):
        with tempfile.TemporaryDirectory() as checkout_a, \
             tempfile.TemporaryDirectory() as checkout_b:
            with mock.patch.dict(os.environ, {"PYTHONPATH": checkout_a}):
                bound = eb.bind()
            with mock.patch.dict(os.environ, {"PYTHONPATH": checkout_b}):
                ok, mismatches = eb.verify(bound)
            self.assertFalse(ok)
            self.assertTrue(any(m.startswith("pythonpath:") for m in mismatches))

    def test_empty_pythonpath_entries_are_dropped(self):
        with mock.patch.dict(os.environ, {"PYTHONPATH": ""}):
            self.assertEqual(eb._pythonpath_entries(), [])

    def test_pythonpath_entries_are_realpath_resolved(self):
        with tempfile.TemporaryDirectory() as real_dir:
            real_dir = os.path.realpath(real_dir)
            link_dir = real_dir + "-symlink"
            os.symlink(real_dir, link_dir)
            try:
                with mock.patch.dict(os.environ, {"PYTHONPATH": link_dir}):
                    self.assertEqual(eb._pythonpath_entries(), [real_dir])
            finally:
                os.remove(link_dir)


class InterpreterDrift(unittest.TestCase):
    """THE NAMED EDGE: the interpreter changed between bind and verify."""

    def test_a_different_interpreter_is_caught_as_a_mismatch(self):
        bound = eb.bind()
        with mock.patch.object(sys, "executable", "/usr/bin/python2.7"):
            ok, mismatches = eb.verify(bound)
        self.assertFalse(ok)
        self.assertTrue(any(m.startswith("interpreter:") for m in mismatches))


class VolatileEnvIsIgnored(unittest.TestCase):
    """THE NAMED EDGE: TERM, a random session id, or any other volatile
    environment variable must never register as drift, because they vary
    between two correct runs of the same work."""

    def test_changing_term_never_produces_a_mismatch(self):
        bound = eb.bind()
        with mock.patch.dict(os.environ, {"TERM": "a-completely-different-term"}):
            ok, mismatches = eb.verify(bound)
        self.assertTrue(ok, mismatches)

    def test_changing_a_random_session_id_never_produces_a_mismatch(self):
        bound = eb.bind()
        with mock.patch.dict(os.environ, {"SOME_SESSION_ID": "abc123"}):
            ok, mismatches = eb.verify(bound)
        self.assertTrue(ok, mismatches)


class PathWithSpaces(unittest.TestCase):
    """THE NAMED EDGE: unittest rewrites sys.argv[0], and a sandbox path
    prefix that contains a space has broken a caller that quoted it by
    hand. This module never builds a shell string: every subprocess call
    passes an argv list, so a space in the path must never split it."""

    def test_repo_root_resolves_correctly_under_a_path_containing_a_space(self):
        with tempfile.TemporaryDirectory() as parent:
            spaced = os.path.join(parent, "a directory with spaces")
            os.mkdir(spaced)
            _init_repo(spaced)
            root = eb._repo_root(spaced)
            self.assertEqual(root, os.path.realpath(spaced))

    def test_bind_and_verify_agree_from_a_path_containing_a_space(self):
        saved_cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as parent:
            spaced = os.path.join(parent, "a directory with spaces")
            os.mkdir(spaced)
            _init_repo(spaced)
            try:
                os.chdir(spaced)
                bound = eb.bind()
                ok, mismatches = eb.verify(bound)
            finally:
                os.chdir(saved_cwd)
        self.assertTrue(ok, mismatches)


def run_main():
    """Return (exit_code, stdout)."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = eb.main()
    return code, buf.getvalue()


class MainEntryPoint(unittest.TestCase):

    def test_main_exits_zero_and_prints_the_binding_as_json(self):
        code, out = run_main()
        self.assertEqual(code, 0)
        printed = json.loads(out)
        self.assertEqual(set(printed.keys()), set(eb._FIELDS))

    def test_main_exits_one_on_an_internal_self_mismatch(self):
        # A bad state a green check would also pass: if main() ever stopped
        # actually comparing the two bind() calls (for example, always
        # returning ok=True), this is the case that would go red and it
        # would not. Forcing bind() to answer differently on its two calls
        # inside main() is the only way to observe that without editing the
        # module under test.
        first = {"cwd": "/one", "interpreter": "/bin/py", "repo_root": "/one",
                  "pythonpath": []}
        second = {"cwd": "/two", "interpreter": "/bin/py", "repo_root": "/one",
                   "pythonpath": []}
        with mock.patch.object(eb, "bind", side_effect=[first, second]):
            code, out = run_main()
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
