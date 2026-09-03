#!/usr/bin/env python3
"""GATE 8: a comma joined --files list is refused, never stored as one path.

Why this test exists, in the words of the failure it closes. On 2026-08-27 two lanes on one
estate each claimed a fence by typing "--files a.swift,b.swift". --files is SPACE separated, so
each stored ONE path containing commas, which matches no file on disk. The fence therefore owned
nothing, _find_overlap could never fire against it, and both the dashboard and STATE.md printed
the record as though it were protecting those files. The lanes then edited the same files, and
one lane's release buried the other's. The store's overlap machinery was never broken; it was
never given real paths.

Run: python3 tools/test_bm_store_comma_files.py     (unittest output, exit 0 or 1)
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

STORE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bm_store.py")


def run(cwd, *args):
    p = subprocess.run([sys.executable, STORE] + list(args), cwd=cwd,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return p.returncode, (p.stdout + p.stderr).decode("utf-8", "replace")


class GateEightCommaJoinedFiles(unittest.TestCase):
    """One store, built once, walked through the cases in order.

    The methods are NUMBERED, and that is load bearing rather than decorative:
    the overlap case asserts that claiming a.py a second time is refused, which
    is only a meaningful assertion once the earlier case has actually stored
    a.py. unittest runs methods in alphabetical order, not source order, so the
    ordering has to live in the names.
    """

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="bm-gate8-")
        subprocess.run(["git", "init", "-q", "."], cwd=cls.tmp, check=True)
        for name in ("a.py", "b.py", "weird,name.py"):
            with open(os.path.join(cls.tmp, name), "w") as f:
                f.write("x\n")
        subprocess.run(["git", "add", "-A"], cwd=cls.tmp, check=True)
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                        "commit", "-qm", "init"], cwd=cls.tmp, check=True)
        run(cls.tmp, "init")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _claim(self, label, argv, want_zero):
        code, out = run(self.tmp, *argv)
        ok = (code == 0) if want_zero else (code != 0)
        self.assertTrue(ok, "%s: exit %d\n%s" % (label, code, out.strip()[:400]))

    def test_01_comma_joined_list_is_refused(self):
        self._claim("comma joined list is refused",
                    ["claim", "c1", "--files", "a.py,b.py"], False)

    def test_02_space_separated_list_is_accepted(self):
        self._claim("space separated list is accepted",
                    ["claim", "c2", "--files", "a.py", "b.py"], True)

    def test_03_real_filename_containing_a_comma_is_accepted(self):
        # A comma IS legal in a filename, so a path that exists is never second guessed.
        self._claim("real filename containing a comma is accepted",
                    ["claim", "c3", "--files", "weird,name.py"], True)

    def test_04_genuine_overlap_is_still_refused(self):
        # The pre-existing overlap refusal must still fire, and must fire for the RIGHT
        # reason: this is what the comma bug had been silently disabling estate wide.
        self._claim("genuine overlap is still refused",
                    ["claim", "c4", "--files", "a.py"], False)

    def test_05_a_claim_with_no_files_at_all_is_accepted(self):
        self._claim("a claim with no files at all is accepted",
                    ["claim", "c5", "--objective", "none"], True)

    def test_06_a_refused_claim_left_no_record_behind(self):
        # The refused claim must leave NO record behind: a refusal that still wrote would be the
        # same silent success in a different coat.
        code, out = run(self.tmp, "dashboard")
        for absent in ("- c1 ", "- c4 "):
            self.assertNotIn(absent, out,
                             "refused claim was stored anyway: %s" % absent.strip())

    def test_07_every_accepted_claim_reached_the_dashboard(self):
        code, out = run(self.tmp, "dashboard")
        for present in ("- c2 ", "- c3 ", "- c5 "):
            self.assertIn(present, out,
                          "accepted claim is missing from the dashboard: %s" % present.strip())


if __name__ == "__main__":
    unittest.main(verbosity=1)
