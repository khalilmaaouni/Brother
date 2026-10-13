#!/usr/bin/env python3
"""scripts/plugin_runtime_fast_discover.py, driven at its entry point on a
fixture tree it discovers the way it discovers the real one.

WHY. Hub canary run 38148660856 (GitHub Linux, 2026-10-11) read this row FAIL,
exit 1, and its kept tail never named the failing test: unittest's headers
had gone to stderr first and the tests' buffered stdout chatter came last.
The two failing tests were facade tests reading this Mac's ~/.brotherme
consent file, which the runner's home folder lacks. Each case below goes red on
the discoverer as it was before that night.
"""
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest

DISCOVER = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "plugin_runtime_fast_discover.py")


class FastDiscover(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

    def write(self, rel, body=""):
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(textwrap.dedent(body))
        return path

    def package(self):
        for rel in ("plugin", "plugin/runtime", "plugin/runtime/brother"):
            self.write(rel + "/__init__.py")

    def run_discover(self, **env):
        full = dict(os.environ, **env)
        r = subprocess.run([sys.executable, DISCOVER], cwd=self.root, env=full,
                           capture_output=True, text=True, timeout=120)
        lines = r.stdout.strip().splitlines()
        return r, (lines[-1] if lines else "")

    def test_last_line_names_the_failing_test_behind_stdout_chatter(self):
        self.package()
        self.write("plugin/runtime/brother/test_chatter.py", """
            import unittest
            class Chatter(unittest.TestCase):
                def test_boom(self):
                    self.assertEqual(1, 2)
                def test_talks(self):
                    for i in range(120):
                        print("chatter line %d" % i)
            """)
        r, last = self.run_discover()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("FAIL exit 1", last)
        self.assertIn("FAIL test_boom", last)
        self.assertIn("Ran 2 tests", last)

    def test_an_import_error_is_named_as_an_error(self):
        self.package()
        self.write("plugin/runtime/brother/test_broken.py", "raise ImportError('no such seam')\n")
        r, last = self.run_discover()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("ERROR ", last)
        self.assertIn("test_broken", last)

    def test_suite_runs_under_a_fresh_empty_home_that_is_removed_after(self):
        self.package()
        seen = os.path.join(self.root, "home-seen.txt")
        self.write("plugin/runtime/brother/test_home.py", """
            import os, unittest
            class Home(unittest.TestCase):
                def test_home_is_empty_and_no_consent_leaks_in(self):
                    home = os.environ["HOME"]
                    with open(%r, "w") as fh:
                        fh.write(home)
                    self.assertNotEqual(home, %r)
                    self.assertEqual(os.listdir(home), [])
                    self.assertNotIn("BROTHERME_CONFIG", os.environ)
            """ % (seen, os.environ.get("HOME", "")))
        # a heavy file is skipped here, so its failure must not count
        self.write("plugin/runtime/brother/test_leases.py", """
            import unittest
            class Heavy(unittest.TestCase):
                def test_never_runs_here(self):
                    self.fail("HEAVY files run in the full battery only")
            """)
        r, last = self.run_discover(BROTHERME_CONFIG=os.path.join(self.root, "real.json"))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(last.startswith("plugin_runtime_fast_discover: PASS exit 0, Ran 1 test"), last)
        with open(seen, encoding="utf-8") as fh:
            self.assertFalse(os.path.exists(fh.read()), "the empty HOME was left behind")

    def test_no_test_module_is_a_failure(self):
        r, _ = self.run_discover()
        self.assertEqual(r.returncode, 1)
        self.assertIn("no test modules found", r.stderr)


if __name__ == "__main__":
    unittest.main()
