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


if __name__ == "__main__":
    unittest.main()
