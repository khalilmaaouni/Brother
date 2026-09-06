"""test_clean_install_e2e.py: proves scripts/clean_install_e2e.sh actually
does what it claims, by running the real script rather than reimplementing
its logic here.

Four things are proven:

  1. A real, hermetic run (stub decomposer, stub worker) installs the real
     bundle into a throwaway CLAUDE_CONFIG_DIR and HOME, resolves the real
     installed launcher from the plugin cache, and integrates one unit into
     a fresh target repository: every ledger line reads PASS and the script
     exits 0. Skipped as NO-DATA when the real, unstubbed pinned tag cannot
     be resolved right now (a genuine candidate tree, or the public remote
     is unreachable): a real install cannot be driven in either state, and
     that is not a defect in this script (see case 2 below, which proves
     the candidate shape itself without needing a real install to run).
  2. THE CUT PRECEDES THE TAG (three shapes, each driven through the
     CLEAN_INSTALL_E2E_LS_REMOTE_CMD seam so none of them touch the network
     or a real tag): the pinned tag missing and newer than the remote's
     newest tag reads the exact NO-DATA line at exit 2; the pinned tag
     missing and NOT newer (a real packaging defect) reads a named FAIL at
     exit 1; the remote itself unreachable reads its own NO-DATA line
     naming the network, at exit 2.
  3. A forced bad state -- the installed launcher deleted mid-run, through
     the CLEAN_INSTALL_E2E_SABOTAGE=delete-launcher seam the script exposes
     for exactly this -- reads as a named FAIL line naming the launcher,
     never a raw stack trace, and the script exits nonzero.

NEEDS NETWORK (claude plugin install resolves brothermode and brothersbe
from GitHub) and the real `claude` CLI. Skipped, not failed, when the
binary is absent, matching this estate's own NO-DATA-is-not-a-fail
convention; a missing claude binary is an environment gap, not a defect in
this script. The three CLEAN_INSTALL_E2E_LS_REMOTE_CMD cases in group 2
above run before the script ever reaches the network, so they need no
network themselves; they still sit behind the same claude-binary gate
because the script's own BLOCKED check for that runs first.
"""
import json
import os
import re
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "clean_install_e2e.sh")
PLUGIN_JSON = os.path.join(HERE, "..", "bundle", ".claude-plugin", "plugin.json")
REMOTE = "https://github.com/khalilmaaouni/Brother"


def sh(args, env=None, timeout=180):
    return subprocess.run(args, capture_output=True, text=True,
                          timeout=timeout, env=env)


def _claude_present():
    return subprocess.run(["sh", "-c", "command -v claude"],
                          capture_output=True).returncode == 0


def _pinned_version():
    with open(PLUGIN_JSON) as fh:
        return json.load(fh)["version"]


def _real_tag_shape(version, remote=REMOTE):
    """"present" / "candidate" / "defect" / "unreachable": the REAL,
    unstubbed shape scripts/clean_install_e2e.sh would see against the
    public remote right now, for `version`. Used only to decide whether
    the real-install test below can run; the three stubbed cases proving
    the script's own verdict for each shape need no network and live on
    their own."""
    proc = subprocess.run(["git", "ls-remote", "--tags", remote],
                          capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        return "unreachable"
    if "refs/tags/v%s\n" % version in proc.stdout or \
       proc.stdout.rstrip("\n").endswith("refs/tags/v%s" % version):
        return "present"
    tags = re.findall(r"refs/tags/v(\d+)\.(\d+)\.(\d+)$", proc.stdout, re.M)
    if not tags:
        return "defect"
    newest = max(tuple(int(g) for g in t) for t in tags)
    pinned = tuple(int(p) for p in version.split("."))
    return "candidate" if pinned > newest else "defect"


@unittest.skipUnless(_claude_present(),
                     "no claude binary on PATH; this proof needs a real client")
class CleanInstallEndToEnd(unittest.TestCase):
    def setUp(self):
        self._version = _pinned_version()
        self._shape = _real_tag_shape(self._version)

    def _skip_unless_a_real_install_can_run(self):
        # Both tests below need a REAL `claude plugin install` to reach the
        # steps they check (marketplace-add through sabotage), which the
        # script's own cut-precedes-the-tag precheck now refuses to attempt
        # in the "candidate" and "unreachable" shapes (see the module
        # docstring). That is not a defect in either test; it is the
        # ordinary state of an in-progress release. The "candidate" and
        # "defect" shapes THEMSELVES are proven, with no network, by the
        # three CLEAN_INSTALL_E2E_LS_REMOTE_CMD tests below.
        if self._shape in ("candidate", "unreachable"):
            self.skipTest(
                "NO-DATA: the pinned tag v%s cannot be resolved against "
                "the public remote right now (%s); a real install cannot "
                "run until the tag is pushed or the network returns"
                % (self._version, self._shape))

    def test_a_real_install_integrates_one_unit_and_prints_a_pass_ledger(self):
        self._skip_unless_a_real_install_can_run()
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
        self._skip_unless_a_real_install_can_run()
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

    # -----------------------------------------------------------------
    # THE CUT PRECEDES THE TAG: three shapes, each driven through
    # CLEAN_INSTALL_E2E_LS_REMOTE_CMD (the same "a full command line
    # replaces the real one" seam DOOR_MODEL_CMD / MODEL_WORKER_CMD
    # already use in the script), so none of these three need the
    # network or a real tag. They run BEFORE the script's own claude
    # binary check ever matters to the outcome, but stay in this class
    # since the class-level skip already requires claude present.
    # -----------------------------------------------------------------
    def test_ls_remote_candidate_reads_the_exact_no_data_line(self):
        env = dict(os.environ)
        env["CLEAN_INSTALL_E2E_LS_REMOTE_CMD"] = (
            r"printf 'deadbeef\trefs/tags/v0.0.1\n'")
        proc = sh(["sh", SCRIPT], env=env)
        out = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 2, out)
        self.assertIn(
            "NO-DATA: the cut precedes the tag: the manifests pin v%s "
            "and the public remote's newest tag is v0.0.1"
            % self._version, out, out)
        self.assertNotIn("Traceback", out, out)

    def test_ls_remote_defect_reads_a_named_fail(self):
        major, minor, patch = (int(p) for p in self._version.split("."))
        newer = "%d.%d.%d" % (major, minor, patch + 1)
        env = dict(os.environ)
        env["CLEAN_INSTALL_E2E_LS_REMOTE_CMD"] = (
            r"printf 'deadbeef\trefs/tags/v%s\n'" % newer)
        proc = sh(["sh", SCRIPT], env=env)
        out = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 1, out)
        self.assertIn("FAIL: pinned tag v%s is missing" % self._version,
                     out, out)
        self.assertIn("packaging defect", out, out)
        self.assertNotIn("NO-DATA", out, out)
        self.assertNotIn("Traceback", out, out)

    def test_ls_remote_unreachable_reads_its_own_no_data_naming_the_network(self):
        env = dict(os.environ)
        env["CLEAN_INSTALL_E2E_LS_REMOTE_CMD"] = (
            "sh -c 'echo network unreachable >&2; exit 1'")
        proc = sh(["sh", SCRIPT], env=env)
        out = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 2, out)
        self.assertIn("NO-DATA: the public remote could not be read", out, out)
        self.assertIn("network unreachable", out, out)
        self.assertNotIn("Traceback", out, out)


if __name__ == "__main__":
    unittest.main()
