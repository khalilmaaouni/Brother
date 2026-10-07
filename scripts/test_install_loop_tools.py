#!/usr/bin/env python3
"""install_loop_tools' own suite. Hermetic: every case builds its own source and bin directories
under a temp root, and the one case that exercises the default location runs in a subprocess with
HOME pointed at an empty directory. Nothing here reads or writes the real ~/.claude/bin.

The cases are the edges, named: no installation at all, no source, an empty source, one drifted
file, a dangling link, a link to the wrong file, a tool installed as a copy, a shell tool without
its executable bit, cached bytecode that CPython would accept while the source has moved, a bin
file this source has no opinion about, and an apply that would destroy the newer edit.

Run: python3 scripts/test_install_loop_tools.py
"""
import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)                      # this suite's subject lives beside it, one copy only
import install_loop_tools as T                # noqa: E402

TOOL = os.path.join(HERE, "install_loop_tools.py")


def run(*argv):
    """Exit code and output of one invocation, with no pipe between us and the verdict."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = T.main(list(argv))
    return code, buf.getvalue()


class Fixture(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="installtools-")
        self.src = os.path.join(self.root, "loop")
        self.bin = os.path.join(self.root, "bin")
        os.makedirs(self.src)
        self.write("alpha.py", "VALUE = 1\n")
        os.chmod(self.write("runner.sh", "#!/bin/sh\necho hi\n"), 0o755)  # git mode 100755

    def write(self, name, body, where=None):
        p = os.path.join(where or self.src, name)
        with open(p, "w") as f:
            f.write(body)
        return p

    def check(self, *extra):
        return run("--check", "--source", self.src, "--bin", self.bin, *extra)

    def apply(self, *extra):
        return run("--apply", "--source", self.src, "--bin", self.bin, *extra)


class WhatItSaysWhenItCannotSay(Fixture):
    def test_no_installation_is_not_applicable_never_a_pass_word(self):
        code, out = self.check()
        self.assertEqual(code, 0)
        self.assertIn("NOT APPLICABLE", out)
        self.assertNotIn("PASS", out)

    def test_missing_source_is_no_data_and_nonzero(self):
        code, out = run("--check", "--source", os.path.join(self.root, "gone"), "--bin", self.bin)
        self.assertEqual(code, 3)
        self.assertIn("NO-DATA", out)

    def test_empty_source_is_no_data_not_a_vacuous_pass(self):
        empty = os.path.join(self.root, "empty")
        os.makedirs(empty)
        code, out = run("--check", "--source", empty, "--bin", self.bin)
        self.assertEqual(code, 3)
        self.assertIn("NO-DATA", out)

    def test_default_bin_follows_HOME_so_an_empty_home_is_not_applicable(self):
        home = os.path.join(self.root, "emptyhome")
        os.makedirs(home)
        env = dict(os.environ, HOME=home)
        env.pop("PYTHONPATH", None)
        r = subprocess.run([sys.executable, "-B", TOOL, "--check", "--source", self.src],
                           capture_output=True, text=True, env=env, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("NOT APPLICABLE", r.stdout)


class WhatItRefuses(Fixture):
    def test_drift_fails_and_names_the_file(self):
        self.apply("--mode", "copy")
        self.write("alpha.py", "VALUE = 2\n", where=self.bin)
        code, out = self.check()
        self.assertEqual(code, 1)
        self.assertIn("DRIFT", out)
        self.assertIn("alpha.py", out)

    def test_a_dangling_link_fails_rather_than_reading_as_installed(self):
        self.apply()
        os.remove(os.path.join(self.src, "alpha.py"))
        self.write("alpha.py", "VALUE = 1\n")            # source restored, link still points at it
        os.remove(os.path.join(self.bin, "alpha.py"))
        os.symlink(os.path.join(self.root, "nowhere.py"), os.path.join(self.bin, "alpha.py"))
        code, out = self.check()
        self.assertEqual(code, 1)
        self.assertIn("LINK-BROKEN", out)

    def test_a_link_to_another_file_fails(self):
        self.apply()
        other = self.write("other.py", "VALUE = 99\n", where=self.root)
        os.remove(os.path.join(self.bin, "alpha.py"))
        os.symlink(other, os.path.join(self.bin, "alpha.py"))
        code, out = self.check()
        self.assertEqual(code, 1)
        self.assertIn("LINK-ELSEWHERE", out)

    def test_apply_refuses_to_overwrite_a_differing_installed_copy(self):
        self.apply("--mode", "copy")
        self.write("alpha.py", "EDITED_IN_BIN = 1\n", where=self.bin)
        code, out = self.apply()
        self.assertEqual(code, 1)
        self.assertIn("REFUSED", out)
        with open(os.path.join(self.bin, "alpha.py")) as f:
            self.assertIn("EDITED_IN_BIN", f.read())     # the newer edit survived the refusal

    def test_force_overwrites_the_differing_copy(self):
        self.apply("--mode", "copy")
        self.write("alpha.py", "EDITED_IN_BIN = 1\n", where=self.bin)
        code, _ = self.apply("--force")
        self.assertEqual(code, 0)
        self.assertTrue(os.path.islink(os.path.join(self.bin, "alpha.py")))

    def test_link_from_a_disposable_worktree_is_refused_by_name(self):
        wt = os.path.join(self.root, ".claude", "worktrees", "agent-x", "scripts", "loop")
        os.makedirs(wt)
        self.write("alpha.py", "VALUE = 1\n", where=wt)
        code, out = run("--apply", "--source", wt, "--bin", self.bin)
        self.assertEqual(code, 1)
        self.assertIn("REFUSED", out)
        self.assertFalse(os.path.exists(os.path.join(self.bin, "alpha.py")))

    def test_a_source_shell_tool_without_the_bit_is_refused_not_silently_chmodded(self):
        # The source is the repository. Fixing its mode here would hide the defect until the next
        # clone, so apply refuses and names the file.
        os.chmod(os.path.join(self.src, "runner.sh"), 0o644)
        code, out = self.apply()
        self.assertEqual(code, 1)
        self.assertIn("REFUSED", out)
        self.assertIn("runner.sh", out)

    def test_require_link_fails_on_a_plain_copy(self):
        self.apply("--mode", "copy")
        self.assertEqual(self.check()[0], 0)              # copies alone are a pass
        code, out = self.check("--require-link")
        self.assertEqual(code, 1)
        self.assertIn("COPY", out)


class TheChannelsBytesCannotSee(Fixture):
    def test_copy_gives_a_fresh_mtime_because_copy2_would_keep_a_stale_pyc_valid(self):
        old = 1600000000
        os.utime(os.path.join(self.src, "alpha.py"), (old, old))
        self.apply("--mode", "copy")
        self.assertNotEqual(int(os.stat(os.path.join(self.bin, "alpha.py")).st_mtime), old)

    def test_cached_bytecode_that_no_longer_matches_its_source_fails(self):
        self.apply("--mode", "copy")
        installed = os.path.join(self.bin, "alpha.py")
        import py_compile
        py_compile.compile(installed, doraise=True)
        st = os.stat(installed)
        with open(installed, "w") as f:
            f.write("VALUE = 9\n")                        # same length, so only the bytes moved
        os.utime(installed, (st.st_atime, st.st_mtime))   # what copy2 or `cp -p` would leave
        self.write("alpha.py", "VALUE = 9\n")             # source agrees: bytes are in parity
        code, out = self.check()
        self.assertEqual(code, 1, out)
        self.assertIn("BAD-INSTALL", out)
        self.assertIn(".pyc", out)

    def test_apply_removes_the_cached_bytecode_it_would_otherwise_inherit(self):
        self.apply("--mode", "copy")
        import py_compile
        py_compile.compile(os.path.join(self.bin, "alpha.py"), doraise=True)
        cache = os.path.join(self.bin, "__pycache__")
        self.assertTrue([e for e in os.listdir(cache) if e.startswith("alpha.")])
        self.write("alpha.py", "VALUE = 3\n")
        self.apply("--mode", "copy", "--force")
        self.assertEqual([e for e in os.listdir(cache) if e.startswith("alpha.")], [])

    def test_a_shell_tool_without_its_executable_bit_fails(self):
        self.apply("--mode", "copy")
        os.chmod(os.path.join(self.bin, "runner.sh"), 0o644)
        code, out = self.check()
        self.assertEqual(code, 1)
        self.assertIn("runner.sh", out)

    def test_a_python_tool_without_the_bit_is_deliberately_not_a_failure(self):
        # Stated rather than implied: every call site spells `python3 <path>`, so the bit on a .py
        # changes nothing, and the one real mismatch measured in this estate is a .py.
        self.apply("--mode", "copy")
        os.chmod(os.path.join(self.bin, "alpha.py"), 0o644)
        self.assertEqual(self.check()[0], 0)


class WhatItLeavesAlone(Fixture):
    def test_a_bin_file_this_source_never_heard_of_is_untouched(self):
        os.makedirs(self.bin, exist_ok=True)
        stranger = self.write("someone_elses.py", "KEEP = 1\n", where=self.bin)
        self.apply()
        with open(stranger) as f:
            self.assertEqual(f.read(), "KEEP = 1\n")

    def test_a_source_tool_that_is_not_installed_is_reported_not_failed(self):
        self.apply()
        os.remove(os.path.join(self.bin, "runner.sh"))
        code, out = self.check()
        self.assertEqual(code, 0, out)
        self.assertIn("not installed", out)


class TheWholeRoundTrip(Fixture):
    def test_link_then_check_is_green_and_the_two_paths_are_one_file(self):
        self.assertEqual(self.apply()[0], 0)
        code, out = self.check()
        self.assertEqual(code, 0, out)
        self.assertIn("PASS", out)
        a = os.path.join(self.bin, "alpha.py")
        self.assertTrue(os.path.islink(a))
        with open(a, "w") as f:                           # mutate THROUGH the installed path
            f.write("VALUE = 42\n")
        with open(os.path.join(self.src, "alpha.py")) as f:
            self.assertEqual(f.read(), "VALUE = 42\n")    # the source saw it: one file, two names
        self.assertEqual(self.check()[0], 0)              # and parity cannot be broken by an edit

    def test_copy_is_the_rollback_from_link(self):
        self.apply()
        self.assertTrue(os.path.islink(os.path.join(self.bin, "alpha.py")))
        self.assertEqual(self.apply("--mode", "copy")[0], 0)
        self.assertFalse(os.path.islink(os.path.join(self.bin, "alpha.py")))
        self.assertEqual(self.check()[0], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
