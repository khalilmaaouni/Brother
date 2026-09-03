"""test_clean_install_e2e.py: proves scripts/clean_install_e2e.sh actually
does what it claims, by running the real script rather than reimplementing
its logic here.

Two things are proven:

  1. A real, hermetic run (stub decomposer, stub worker) installs the real
     bundle into a throwaway CLAUDE_CONFIG_DIR and HOME, resolves the real
     installed launcher from the plugin cache, and integrates one unit into
     a fresh target repository: every ledger line reads PASS and the script
     exits 0.
  2. A forced bad state -- the installed launcher deleted mid-run, through
     the CLEAN_INSTALL_E2E_SABOTAGE=delete-launcher seam the script exposes
     for exactly this -- reads as a named FAIL line naming the launcher,
     never a raw stack trace, and the script exits nonzero.

NEEDS NETWORK (claude plugin install resolves brothermode and brothersbe
from GitHub) and the real `claude` CLI. Skipped, not failed, when the
binary is absent, matching this estate's own NO-DATA-is-not-a-fail
convention; a missing claude binary is an environment gap, not a defect in
this script.
"""
import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "clean_install_e2e.sh")


def sh(args, env=None, timeout=180):
    return subprocess.run(args, capture_output=True, text=True,
                          timeout=timeout, env=env)


def _claude_present():
    return subprocess.run(["sh", "-c", "command -v claude"],
                          capture_output=True).returncode == 0


@unittest.skipUnless(_claude_present(),
                     "no claude binary on PATH; this proof needs a real client")
class CleanInstallEndToEnd(unittest.TestCase):
    def test_a_real_install_integrates_one_unit_and_prints_a_pass_ledger(self):
        proc = sh(["sh", SCRIPT])
        out = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 0, out)
        self.assertIn("clean-install-e2e: ledger", out, out)
        for line in ("PASS   marketplace-add",
                     "PASS   bundle-install",
                     "PASS   launcher-resolve:",
                     "PASS   exit-0",
                     "PASS   delivery-report-names-units:",
                     "PASS   git-log-has-units:",
                     "PASS   no-checkout-leak:",
                     "PASS   no-internal-command:"):
            self.assertIn(line, out, out)
        self.assertNotIn("verdict: FAIL", out, out)
        self.assertNotIn("verdict: NO-DATA", out, out)
        self.assertIn("verdict: PASS", out, out)
        self.assertIn("0 FAIL", out, out)
        self.assertIn("0 NO-DATA", out, out)
        self.assertNotIn("Traceback", out, out)

    def test_a_deleted_installed_launcher_reads_as_a_named_fail(self):
        env = dict(os.environ)
        env["CLEAN_INSTALL_E2E_SABOTAGE"] = "delete-launcher"
        proc = sh(["sh", SCRIPT], env=env)
        out = proc.stdout + proc.stderr
        self.assertNotEqual(proc.returncode, 0, out)
        self.assertIn("FAIL", out, out)
        self.assertIn("launcher", out, out)
        # THE POINT OF THE TEST: a missing file reads as a named ledger
        # line, never an uncaught exception bubbling out of the shell.
        self.assertNotIn("Traceback", out, out)
        self.assertNotIn("command not found", out, out)


if __name__ == "__main__":
    unittest.main()
