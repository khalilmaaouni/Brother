"""R2.2: tmp_sandbox.install drops git's location variables on EVERY call.

git exports which repository is in play to every hook it runs (git
rev-parse --local-env-vars), and cwd does not decide the repository while one
of those variables is set. Measured 2026-09-20 16:24 JST: a suite launched
from the pre-push hook wrote core.bare=true, a dead core.hooksPath and a
fixture identity into the shared repository config, and every worktree and
every hook stopped.

install() used to hand back the sandbox it already had BEFORE it dropped
those variables, so the second call, the one a process launched from a git
hook makes, kept GIT_DIR and aimed git at the real repository. The tests
below set GIT_DIR, call install twice, and fail unless the second call
removes it.

Hostile input is refused with this module's own ValueError, never accepted
and never a raw interpreter exception: an environment whose key cannot be
hashed, an environment whose keys attribute raises, a guard that cannot read
the mapping it is handed, and a prefix that is a path rather than one bare
name (measured 2026-09-24: a raw PermissionError out of install inside a read
only grade slot) are all refusals here.

Run: python3 -B scripts/test_tmp_sandbox_install_git_location.py
"""
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import git_location_guard  # noqa: E402
import tmp_sandbox  # noqa: E402


class UnhashableKeyEnv(object):
    """An environment whose key cannot be hashed and whose pop insists on
    hashing it. This is the shape that reached the shared guard and came back
    out of this module as a raw TypeError unhashable type 'list' on
    2026-09-24."""

    def keys(self):
        return [["GIT_DIR"]]

    def pop(self, name, default=None):
        raise TypeError("unhashable type: 'list'")


class ExplodingKeys(object):
    """An environment whose own keys attribute raises: reading the keys is a
    refusal here, never the raw TypeError into the caller."""

    @property
    def keys(self):
        raise TypeError("unhashable type: 'list'")


class SandboxFixture(unittest.TestCase):
    """A sandbox and the environment are process wide, so every test here
    saves both and puts them back: no test decides another one's verdict, and
    none leaves a git location variable aimed at the repository the runner
    was launched from."""

    def setUp(self):
        self.saved_env = dict(os.environ)
        self.saved_root = tmp_sandbox._root
        self.saved_tempdir = tempfile.tempdir
        tmp_sandbox._root = None

    def tearDown(self):
        tmp_sandbox.remove()
        tmp_sandbox._root = self.saved_root
        tempfile.tempdir = self.saved_tempdir
        os.environ.clear()
        os.environ.update(self.saved_env)


class InstallDropsGitLocationBeforeItReusesTheSandbox(SandboxFixture):
    """R6: the drop runs before the _root test and before tempfile.tempdir
    or os.environ["TMPDIR"] move, so a second install can never hand back a
    sandbox while GIT_DIR is still set."""

    def test_install_drops_git_location_before_reusing_root(self):
        first = tmp_sandbox.install(prefix="r22-reuse-")
        self.assertTrue(os.path.isdir(first))
        self.assertNotIn("GIT_DIR", os.environ)
        os.environ["GIT_DIR"] = "/nonexistent/hook/repository/.git"
        os.environ["GIT_WORK_TREE"] = "/nonexistent/hook/repository"
        second = tmp_sandbox.install(prefix="r22-second-call-")
        self.assertEqual(second, first)
        self.assertNotIn("GIT_DIR", os.environ)
        self.assertNotIn("GIT_WORK_TREE", os.environ)

    def test_second_install_drops_git_location_when_root_exists(self):
        already = tempfile.mkdtemp(prefix="r22-already-")
        tmp_sandbox._root = already
        os.environ["GIT_DIR"] = "/nonexistent/hook/repository/.git"
        self.assertEqual(tmp_sandbox.install(prefix="r22-never-used-"), already)
        self.assertNotIn("GIT_DIR", os.environ)

    def test_install_still_aims_this_process_at_the_sandbox(self):
        root = tmp_sandbox.install(prefix="r22-wiring-")
        self.assertEqual(tempfile.tempdir, root)
        self.assertEqual(os.environ.get("TMPDIR"), root)
        self.assertEqual(tmp_sandbox.install(prefix="r22-again-"), root)

    def test_install_drops_git_location_before_it_creates_the_sandbox(self):
        seen = {}
        real_mkdtemp = tempfile.mkdtemp

        def spy(**kwargs):
            seen["git_dir"] = os.environ.get("GIT_DIR")
            return real_mkdtemp(**kwargs)

        os.environ["GIT_DIR"] = "/nonexistent/hook/repository/.git"
        tempfile.mkdtemp = spy
        try:
            root = tmp_sandbox.install(prefix="r22-order-")
        finally:
            tempfile.mkdtemp = real_mkdtemp
        self.assertIsNone(seen.get("git_dir"))
        self.assertTrue(os.path.isdir(root))

    def test_no_git_location_variable_survives_install(self):
        os.environ["GIT_DIR"] = "/nonexistent/hook/repository/.git"
        os.environ["GIT_INDEX_FILE"] = "/nonexistent/hook/repository/index"
        os.environ["GIT_CONFIG_PARAMETERS"] = "core.bare=true"
        tmp_sandbox.install(prefix="r22-clean-")
        left = sorted(name for name in tmp_sandbox.GIT_LOCATION_VARS
                      if name in os.environ)
        self.assertEqual(left, [])


class InstallRefusesAHostilePrefix(SandboxFixture):
    """Hostile input is refused with this module's own ValueError, never
    accepted and never a raw interpreter exception: a prefix that is not a
    string, a prefix that is a PATH (tempfile.mkdtemp joins the prefix onto
    the temp root, so a path prefix puts the sandbox outside that root:
    measured 2026-09-24, a raw PermissionError out of install on the path
    .../slot-0-probe/../x8n2t04 inside a read only grade slot), and a temp
    root that cannot be written at all."""

    def test_install_refuses_a_prefix_that_is_not_a_string(self):
        for bad in (123, ["r22-"], True, 3.5, b"r22-"):
            with self.assertRaises(ValueError):
                tmp_sandbox.install(prefix=bad)

    def test_install_refuses_a_prefix_that_is_a_parent_path(self):
        calls = []
        real_mkdtemp = tempfile.mkdtemp

        def spy(**kwargs):
            calls.append(kwargs.get("prefix"))
            return real_mkdtemp(**kwargs)

        tempfile.mkdtemp = spy
        try:
            for bad in ("../r22-escape-", "../x8n2t04", "..", "."):
                with self.assertRaises(ValueError):
                    tmp_sandbox.install(prefix=bad)
        finally:
            tempfile.mkdtemp = real_mkdtemp
        self.assertEqual(calls, [])

    def test_install_refuses_a_prefix_that_is_any_other_path(self):
        for bad in ("r22/escape-", "./r22-", "a/b/"):
            with self.assertRaises(ValueError):
                tmp_sandbox.install(prefix=bad)

    def test_a_refused_prefix_installs_nothing(self):
        with self.assertRaises(ValueError):
            tmp_sandbox.install(prefix="../r22-escape-")
        self.assertIsNone(tmp_sandbox._root)

    def test_a_temp_root_that_cannot_be_written_is_refused(self):
        def refuse(**kwargs):
            raise PermissionError(1, "Operation not permitted")

        real_mkdtemp = tempfile.mkdtemp
        tempfile.mkdtemp = refuse
        try:
            with self.assertRaises(ValueError):
                tmp_sandbox.install(prefix="r22-perm-")
        finally:
            tempfile.mkdtemp = real_mkdtemp
        self.assertIsNone(tmp_sandbox._root)

    def test_install_still_accepts_its_honest_input(self):
        self.assertTrue(os.path.isdir(tmp_sandbox.install(prefix="r22-honest-")))
        self.assertTrue(os.path.isdir(tmp_sandbox.install()))


class RemoveNeverRestoresGitLocation(SandboxFixture):
    """R9: remove MUST NOT recreate or restore any removed git location
    variable."""

    def test_remove_never_restores_a_git_location_variable(self):
        os.environ["GIT_DIR"] = "/nonexistent/hook/repository/.git"
        root = tmp_sandbox.install(prefix="r22-remove-")
        self.assertNotIn("GIT_DIR", os.environ)
        tmp_sandbox.remove()
        self.assertNotIn("GIT_DIR", os.environ)
        self.assertFalse(os.path.exists(root))
        self.assertIsNone(tmp_sandbox._root)


class DropGitLocationRoutesThroughTheGuard(SandboxFixture):
    """R7 and R8: tmp_sandbox keeps one drop, the shared guard's, so the
    sandbox installer, the hook batteries and the preflight cannot drift
    apart, and after install no git location name is set."""

    def test_drop_git_location_calls_the_shared_guard(self):
        calls = []

        def fake_drop(env=None):
            calls.append(env)
            return ["GIT_DIR"]

        real = git_location_guard.drop_git_location
        git_location_guard.drop_git_location = fake_drop
        try:
            removed = tmp_sandbox.drop_git_location(
                {"GIT_DIR": "/x/.git", "HOME": "/h"})
        finally:
            git_location_guard.drop_git_location = real
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0], {"GIT_DIR": "/x/.git", "HOME": "/h"})
        self.assertEqual(removed, ["GIT_DIR"])

    def test_install_routes_through_the_shared_guard_too(self):
        calls = []
        real = git_location_guard.drop_git_location

        def fake_drop(env=None):
            calls.append(env)
            return []

        git_location_guard.drop_git_location = fake_drop
        try:
            root = tmp_sandbox.install(prefix="r22-guard-")
        finally:
            git_location_guard.drop_git_location = real
        self.assertTrue(os.path.isdir(root))
        self.assertEqual(len(calls), 1)

    def test_drop_git_location_with_none_clears_this_process(self):
        os.environ["GIT_DIR"] = "/nonexistent/hook/repository/.git"
        removed = tmp_sandbox.drop_git_location()
        self.assertIn("GIT_DIR", removed)
        self.assertNotIn("GIT_DIR", os.environ)

    def test_drop_git_location_removes_only_the_location_names(self):
        env = {"GIT_DIR": "/x/.git", "GIT_INDEX_FILE": "/x/index",
               "HOME": "/h", "PATH": "/bin"}
        removed = tmp_sandbox.drop_git_location(env)
        self.assertEqual(set(removed), set(["GIT_DIR", "GIT_INDEX_FILE"]))
        self.assertEqual(env, {"HOME": "/h", "PATH": "/bin"})


class DropGitLocationRefusesAHostileEnvironment(SandboxFixture):
    """probe-crash: a hostile environment is REFUSED with this module's own
    ValueError, never a raw interpreter exception and never a silent accept.
    An unhashable key, a keys attribute that raises, and a guard that cannot
    read the mapping are the shapes that came back as a raw TypeError on
    2026-09-24."""

    def test_drop_git_location_refuses_a_hostile_environment(self):
        for bad in (42, "GIT_DIR=/x", ["GIT_DIR"], True, 3.5, b"GIT_DIR"):
            with self.assertRaises(ValueError):
                tmp_sandbox.drop_git_location(bad)

    def test_drop_git_location_refuses_a_non_string_key(self):
        with self.assertRaises(ValueError):
            tmp_sandbox.drop_git_location({1: "x", "GIT_DIR": "/x"})

    def test_drop_git_location_refuses_an_unhashable_key(self):
        calls = []
        real = git_location_guard.drop_git_location

        def spy(env=None):
            calls.append(env)
            return []

        git_location_guard.drop_git_location = spy
        try:
            with self.assertRaises(ValueError):
                tmp_sandbox.drop_git_location(UnhashableKeyEnv())
        finally:
            git_location_guard.drop_git_location = real
        self.assertEqual(calls, [])

    def test_drop_git_location_refuses_keys_it_cannot_read(self):
        with self.assertRaises(ValueError):
            tmp_sandbox.drop_git_location(ExplodingKeys())

    def test_a_guard_that_cannot_read_the_mapping_is_refused(self):
        def exploding_guard(env=None):
            raise TypeError("unhashable type: 'list'")

        real = git_location_guard.drop_git_location
        git_location_guard.drop_git_location = exploding_guard
        try:
            with self.assertRaises(ValueError):
                tmp_sandbox.drop_git_location({"GIT_DIR": "/x/.git"})
        finally:
            git_location_guard.drop_git_location = real

    def test_an_empty_environment_is_the_honest_empty_answer(self):
        self.assertEqual(list(tmp_sandbox.drop_git_location({})), [])


if __name__ == "__main__":
    unittest.main()
