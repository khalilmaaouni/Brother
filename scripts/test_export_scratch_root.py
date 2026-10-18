"""C9: every export and hermetic folder lives under the one scratch root, and an unreadable path folder is named.

Driven at the entry points (hermetic_test_check.check and slow_host, export_public.main and push_appended,
brother_paths.claude_for_check) with tempfile.mkdtemp recorded: TemporaryDirectory routes through it on 3.9 and 3.13,
so a site that still omits dir= shows up as a folder outside the fixture's scratch root. TMPDIR points at a second
fixture folder standing in for the system temp folder, so a fallback lands somewhere this test can see and refuse.
"""
import importlib.util
import os
import shutil
import stat
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import export_public as E  # noqa: E402
import hermetic_test_check as H  # noqa: E402

REAL_MKDTEMP = tempfile.mkdtemp
PATHS_COPIES = ("scripts/brother_paths.py", "products/brothersbe/tools/brother_paths.py",
                "products/brothermode/tools/brother_paths.py")


def load_paths(rel):
    spec = importlib.util.spec_from_file_location("bp_" + rel.split("/")[1], os.path.join(ROOT, rel))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Stop(Exception):
    pass


def fixture_box(prefix):
    """The fixture's own folder, under the real scratch root (the footprint law binds tests too)."""
    base = H._scratch_root()
    os.makedirs(base, exist_ok=True)
    return os.path.realpath(REAL_MKDTEMP(prefix=prefix, dir=base))


class Fixture(unittest.TestCase):
    def setUp(self):
        self.box = fixture_box("c9-")
        self.addCleanup(shutil.rmtree, self.box, True)
        self.scratch = os.path.join(self.box, "scratch")
        self.systmp = os.path.join(self.box, "systmp")
        os.makedirs(self.systmp)
        env = mock.patch.dict(os.environ, {"BROTHER_SCRATCH": self.scratch, "TMPDIR": self.systmp,
                                           "HOME": os.path.join(self.box, "home"),
                                           "LOCAL_SLOTS": "1"})
        env.start()
        self.addCleanup(env.stop)
        tempfile.tempdir = None            # gettempdir caches; the fixture's TMPDIR must be the one it reads
        self.addCleanup(setattr, tempfile, "tempdir", None)
        self.made = []

        def recording(*a, **kw):
            path = REAL_MKDTEMP(*a, **kw)
            self.made.append(os.path.realpath(path))
            return path
        rec = mock.patch.object(tempfile, "mkdtemp", recording)
        rec.start()
        self.addCleanup(rec.stop)

    def assert_all_under_scratch(self, at_least):
        self.assertGreaterEqual(len(self.made), at_least, self.made)
        root = os.path.realpath(self.scratch) + os.sep
        for path in self.made:
            self.assertTrue(path.startswith(root), "%s is outside the scratch root %s" % (path, root))
            self.assertFalse(os.path.exists(path), "%s was not removed at the end" % path)
        self.assertEqual(os.listdir(self.systmp), [], "something landed in the system temp folder")


def ships(*names):
    def build(dest):
        os.makedirs(os.path.join(dest, "scripts"))
        for name in names:
            open(os.path.join(dest, "scripts", name), "w").close()
    return build


class ExportsLandUnderTheScratchRoot(Fixture):
    def test_exports_land_under_the_scratch_root(self):
        root = os.path.join(self.box, "hub")
        os.makedirs(os.path.join(root, "scripts"))
        # check: the export tree and its HOME (a test the tree does not ship runs nothing, so no sandbox is needed)
        rows = H.check(root, ["scripts/test_absent.py"], runner=None, build=ships())
        self.assertEqual(rows[0][0], H.OK, rows)
        # slow_host: the slow path's two folders
        rows = H.slow_host(root, ["scripts/test_absent.py"], 0, repeats=1, build=ships(), held="0",
                           hog_starter=lambda n: [])
        self.assertEqual(rows[0][0], H.OK, rows)
        # export_public.main: the candidate, identity and baseline folders
        allow = os.path.join(self.box, "allowlist")
        with open(allow, "w") as fh:
            fh.write("README.md\n")

        def stop(tree, name):
            raise ValueError("fixture stop after the folders were made")
        with mock.patch.object(E, "_claim_world", stop), mock.patch("sys.stdout"):
            self.assertEqual(E.main(["--allowlist", allow, "--root", root]), E.EXIT_NODATA)
        # export_public.push_appended: the push folder

        def run(cmd, cwd, *a, **kw):
            raise Stop(cwd)
        with self.assertRaises(Stop):
            E.push_appended(["README.md"], "origin-fixture", "main", root=root, run=run)
        self.assert_all_under_scratch(2 + 2 + 3 + 1)

    def test_a_half_made_pair_removes_the_folder_it_made(self):
        real = E.scratch_dir
        first = []

        def once_then_refuse(prefix):
            if first:
                raise PermissionError(13, "fixture: the second folder cannot be made")
            first.append(real(prefix))
            return first[0]
        with mock.patch.object(E, "scratch_dir", once_then_refuse):
            rows = H.check(os.path.join(self.box), ["scripts/test_absent.py"], build=ships())
        self.assertEqual(rows[0][0], H.NODATA, rows)
        self.assertEqual(len(first), 1)
        self.assertFalse(os.path.exists(first[0]), "a failed export left its first folder behind")

    def test_two_exports_get_distinct_folders_and_a_rerun_a_fresh_one(self):
        a, b = E.scratch_dir("x-"), E.scratch_dir("x-")
        self.addCleanup(shutil.rmtree, a, True)
        self.addCleanup(shutil.rmtree, b, True)
        self.assertNotEqual(a, b)
        for path in (a, b):
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o700)
            self.assertTrue(os.path.basename(path).startswith("run-x-"), "the scratch age pass prunes run-* only")
        self.assertEqual(stat.S_IMODE(os.stat(self.scratch).st_mode), 0o700)

    def test_an_empty_or_relative_or_dotted_root_resolves(self):
        with mock.patch.dict(os.environ, {"BROTHER_SCRATCH": ""}):
            self.assertEqual(E.scratch_root(), os.path.join(self.box, "home", ".claude", "brother-scratch"))
        with mock.patch.dict(os.environ, {"BROTHER_SCRATCH": os.path.join(self.box, "a", "..", "scratch")}):
            self.assertEqual(E.scratch_root(), self.scratch)
        here = os.getcwd()
        os.chdir(self.box)
        self.addCleanup(os.chdir, here)
        with mock.patch.dict(os.environ, {"BROTHER_SCRATCH": "rel"}):
            # samefile, not a string compare: getcwd answers the folder's on disk case, which macOS need not share
            self.assertTrue(os.path.samefile(E.scratch_root(), os.path.join(self.box, "rel")))
            self.assertTrue(os.path.isabs(E.scratch_root()))


class ASandboxedSuiteKeepsItsScratchInside(unittest.TestCase):
    def test_suite_env_puts_the_scratch_root_under_the_suites_home(self):
        # a sandboxed suite may write only under its tree and HOME, so the run's own root (outside both) would turn every
        # export folder into "Operation not permitted"; suite_env is the one place every sandboxed suite's env is made
        sys.path.insert(0, os.path.join(HERE, "loop"))
        import loop_switches as LS
        env = LS.suite_env({"BROTHER_SCRATCH": "/the/run/root"}, "/suite/home")
        self.assertEqual(env["BROTHER_SCRATCH"], os.path.join("/suite/home", "scratch"))


class NoSystemTempFallback(Fixture):
    def refuses(self):
        with self.assertRaises(OSError):
            E.scratch_dir("x-")
        rows = H.check(os.path.join(ROOT), ["scripts/test_absent.py"], build=ships())
        self.assertEqual(rows[0][0], H.NODATA, rows)
        self.assertIn("scratch", rows[0][2])
        rows = H.slow_host(ROOT, ["scripts/test_absent.py"], 0, repeats=1, build=ships(), held="0",
                           hog_starter=lambda n: [])
        self.assertEqual(rows[0][0], H.NODATA, rows)
        with mock.patch("sys.stdout") as out:
            code = E.main(["--allowlist", os.path.join(ROOT, "README.md")])
        self.assertEqual(code, E.EXIT_REFUSED)
        self.assertIn("scratch root", "".join(str(c) for c in out.write.call_args_list))
        self.assertEqual(os.listdir(self.systmp), [], "a refusal fell back to the system temp folder")
        self.assertFalse(any(not p.startswith(self.box) for p in self.made), self.made)

    def test_no_system_temp_fallback(self):
        with open(self.scratch, "w") as fh:
            fh.write("a file, not a folder\n")
        self.refuses()

    def test_a_root_without_write_permission_refuses(self):
        os.makedirs(self.scratch)
        os.chmod(self.scratch, 0o500)
        self.addCleanup(os.chmod, self.scratch, 0o700)
        self.refuses()

    def test_a_root_that_is_a_symlink_to_slash_refuses(self):
        os.symlink("/", self.scratch)
        self.refuses()


class AnUnreadableFolderIsNamed(unittest.TestCase):
    def setUp(self):
        self.box = fixture_box("c9-paths-")
        self.addCleanup(shutil.rmtree, self.box, True)
        self.home = os.path.join(self.box, "home")
        self.desk = os.path.join(self.home, "Library", "Application Support", "Claude", "claude-code")
        self.locked = os.path.join(self.desk, "9.9.9")
        os.makedirs(os.path.join(self.desk, "1.0.0"))
        os.makedirs(self.locked)
        os.chmod(self.locked, 0)
        self.addCleanup(os.chmod, self.locked, 0o700)

    def test_an_unreadable_folder_is_named(self):
        env = {"HOME": self.home, "PATH": "", "BROTHER_PROGRAM_RECORD": os.path.join(self.box, "none.json")}
        for rel in PATHS_COPIES:
            M = load_paths(rel)
            notes = []
            M.desktop_bundles(self.desk, notes)
            self.assertEqual(len(notes), 1, (rel, notes))
            self.assertIn(self.locked, notes[0])
            self.assertEqual(notes[0], M.unreadable_note(self.locked, PermissionError(13, "Permission denied")))
            self.assertIn("PermissionError", notes[0])
            self.assertEqual(M.desktop_bundles(self.desk), M.desktop_bundles(self.desk, []), "the list is unchanged")
            # the caller that prints: claude_for_check's reason names the folder it could not read
            M.PACKAGE_BIN_DIRS = ()
            path, reason = M.claude_for_check(env)
            self.assertIsNone(path, rel)
            self.assertIn(self.locked, reason, rel)


if __name__ == "__main__":
    unittest.main()
