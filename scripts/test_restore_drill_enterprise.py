"""What restore_drill_enterprise.py's command line must keep true.

Driven backwards against the pre-fix script, where --help ran the whole
enterprise drill (two temp tenants, about seven seconds) and printed the
result JSON instead of usage: this test failed there and passes after.
"""
import os
import subprocess
import sys
import unittest

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "restore_drill_enterprise.py")


class HelpNeverRunsTheDrill(unittest.TestCase):

    def test_help_prints_usage_and_exits_zero_without_running(self):
        proc = subprocess.run([sys.executable, SCRIPT, "--help"],
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, timeout=60)
        out = proc.stdout
        self.assertEqual(proc.returncode, 0, out)
        self.assertIn("usage", out, out)
        self.assertNotIn("checks_total", out, out)


class PortableScrubsEveryMachinePath(unittest.TestCase):
    """portable() is the only thing between a drill run and a machine path in a tracked, exported record
    (attack B1 2026-09-30: nothing called it with a path, so three guards were caught only by a script hash)."""
    def setUp(self):
        import tempfile
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
        import restore_drill_enterprise as R
        self.R = R
        self.tmp = tempfile.mkdtemp(prefix="portable-")
        self.real = os.path.join(self.tmp, "real"); os.makedirs(os.path.join(self.real, "repo"))
        self.link = os.path.join(self.tmp, "link"); os.symlink(self.real, self.link)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_resolved_spelling_is_scrubbed(self):
        roots = {self.link: "<home>"}
        self.assertEqual(self.R.portable(os.path.realpath(self.link) + "/x", roots), "<home>/x")

    def test_longest_root_wins(self):
        roots = {self.real: "<home>", os.path.join(self.real, "repo"): "<repo>"}
        self.assertEqual(self.R.portable(os.path.join(self.real, "repo", "f.py"), roots), "<repo>/f.py")

    def test_a_root_is_replaced_only_at_a_path_boundary(self):
        roots = {self.real: "<home>", os.path.join(self.real, "repo"): "<repo>"}
        self.assertEqual(self.R.portable(os.path.join(self.real, "repo2", "f"), roots), "<home>/repo2/f")

    def test_dict_keys_and_tuples_are_scrubbed(self):
        roots = {self.real: "<home>"}
        got = self.R.portable({os.path.join(self.real, "k"): (os.path.join(self.real, "v"), 3, None)}, roots)
        self.assertEqual(got, {"<home>/k": ("<home>/v", 3, None)})


if __name__ == "__main__":
    unittest.main()
