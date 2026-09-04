#!/usr/bin/env python3
"""Fixture for the vault-scoping fix carried over into tools/sbe_coldstart.py.

Run: python3 tools/test_sbe_coldstart_vault.py

THE DEFECT: sbe_coldstart.py computed
  VAULT = os.environ.get("BROTHERSBE_VAULT", os.path.expanduser("~/BrotherSBEVault"))
the same class already fixed in tools/sbe_telemetry.py. With BROTHERSBE_VAULT
unset, RECEIPT resolved to a path under the operator's real home directory
(<HOME>/BrotherSBEVault/99-System/telemetry/coldstart.jsonl), and a completed
(non-dry-run) batch would create that directory and write into it, reaching
outside the repository and outside any vault the operator actually configured.

THE FIX: mirrors tools/sbe_telemetry.py exactly. An unset (or empty)
BROTHERSBE_VAULT resolves VAULT to the module's own NO_VAULT_SENTINEL, an
absolute path under the filesystem root that no real vault can ever be, and
VAULT_CONFIGURED is False. Nothing derives from os.path.expanduser("~") at
all.

CALIBRATION: test_env_unset_vault_resolves_to_a_sentinel_not_the_home_default
was run once against the pre-fix line above (restored by hand) with HOME
pointed at a throwaway fake-home directory. It went RED: vault_seen printed
"<fake-home>/BrotherSBEVault" and configured_seen printed "True" (a
non-empty os.environ.get(..., default) result is still just the default
value, but the assertions below specifically pin the sentinel/False shape
the fix produces, which the old expression cannot). With the fix restored,
the same test is GREEN.
"""
import os
import subprocess
import sys
import unittest

# E100: one sandbox for every temp tree this process makes, removed at exit.
import os as _e100_os, sys as _e100_sys  # noqa: E402
_e100_sys.path.append(_e100_os.path.join(
    _e100_os.path.dirname(_e100_os.path.abspath(__file__)), '../../../scripts'))
try:  # noqa: E402
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    # A packager (scripts/export_public.py, make_benchmark_bundle.py)
    # can copy this test without scripts/tmp_sandbox.py beside it. Say
    # so rather than dying: the sandbox is hygiene, not the subject.
    _e100_sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % _e100_os.path.basename(__file__))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, "tools")
RUNNER = os.path.join(TOOLS, "sbe_coldstart.py")


def _run(argv, cwd=None, env=None, timeout=60):
    out = subprocess.run(argv, capture_output=True, text=True, cwd=cwd, env=env,
                         stdin=subprocess.DEVNULL, timeout=timeout)
    return out.returncode, out.stdout + out.stderr, out.stderr


class ColdstartVaultDefault(unittest.TestCase):
    def _base_env(self, fake_home):
        env = dict(os.environ)
        env.pop("BROTHERSBE_VAULT", None)
        env["HOME"] = fake_home
        return env

    def test_env_unset_vault_resolves_to_a_sentinel_not_the_home_default(self):
        """With BROTHERSBE_VAULT unset and HOME pointed at a throwaway
        directory (so this test never depends on, or touches, the real
        machine's actual home), sbe_coldstart.VAULT must equal its own
        NO_VAULT_SENTINEL and VAULT_CONFIGURED must be False. Neither may be
        derived from HOME."""
        fake_home = os.path.join(self._tmp(), "fake-home")
        os.makedirs(fake_home)
        env = self._base_env(fake_home)
        code, out, err = _run(
            [sys.executable, "-c",
             "import sys; sys.path.insert(0, %r); import sbe_coldstart as c; "
             "print(c.VAULT); print(c.VAULT_CONFIGURED); print(c.NO_VAULT_SENTINEL); "
             "print(c.RECEIPT)" % TOOLS],
            env=env)
        self.assertEqual(0, code, "probe script failed: %s" % (out + err))
        lines = out.strip().splitlines()
        self.assertEqual(4, len(lines), out)
        vault_seen, configured_seen, sentinel_seen, receipt_seen = lines
        fake_default = os.path.join(fake_home, "BrotherSBEVault")
        self.assertNotEqual(fake_default, vault_seen,
                            "VAULT still resolved to expanduser('~/BrotherSBEVault') under "
                            "the fake HOME this test set: %r" % out)
        self.assertNotIn(fake_home, vault_seen,
                         "VAULT still derives from HOME with BROTHERSBE_VAULT unset: %r"
                         % out)
        self.assertNotIn(fake_home, receipt_seen,
                         "RECEIPT still derives from HOME with BROTHERSBE_VAULT unset: %r"
                         % out)
        self.assertEqual("False", configured_seen)
        self.assertEqual(sentinel_seen, vault_seen,
                         "VAULT unset did not resolve to NO_VAULT_SENTINEL: %r" % out)

    def test_expanduser_is_never_called_building_the_default(self):
        """Direct proxy for 'no home-directory path is built': patch
        os.path.expanduser to raise if it is ever called with the old
        default argument, then import a fresh copy of sbe_coldstart with
        BROTHERSBE_VAULT unset. Pre-fix, importing the module called
        expanduser("~/BrotherSBEVault") to compute the fallback default and
        this test would fail with the patched RuntimeError. Post-fix,
        nothing in the module calls expanduser at all, so the import
        succeeds."""
        fake_home = os.path.join(self._tmp(), "fake-home-2")
        os.makedirs(fake_home)
        env = self._base_env(fake_home)
        probe = (
            "import os, sys\n"
            "real_expanduser = os.path.expanduser\n"
            "def guarded(p):\n"
            "    if 'BrotherSBEVault' in p:\n"
            "        raise RuntimeError('expanduser called building the home-directory default: ' + p)\n"
            "    return real_expanduser(p)\n"
            "os.path.expanduser = guarded\n"
            "sys.path.insert(0, %r)\n"
            "import sbe_coldstart as c\n"
            "print('import-ok')\n"
            "print(c.VAULT_CONFIGURED)\n"
        ) % TOOLS
        code, out, err = _run([sys.executable, "-c", probe], env=env)
        self.assertEqual(0, code, "guarded import failed: %s" % (out + err))
        self.assertIn("import-ok", out, out)
        self.assertIn("False", out, out)

    def test_env_unset_dry_run_never_creates_the_real_home_vault(self):
        """Behavioral proxy running the real tool: `sbe_coldstart.py
        --dry-run`, BROTHERSBE_VAULT unset, HOME pointed at a throwaway,
        empty directory. Pre-fix the module-level RECEIPT path sat under
        that fake home the moment the module was imported; this asserts the
        directory a real (non-dry-run) batch would later write into never
        gets built merely by running the tool."""
        fake_home = os.path.join(self._tmp(), "fake-home-3")
        os.makedirs(fake_home)
        env = self._base_env(fake_home)
        code, out, _err = _run(
            [sys.executable, RUNNER, "--max-budget-usd", "2", "--dry-run"],
            env=env)
        self.assertEqual(0, code, out)
        fake_vault = os.path.join(fake_home, "BrotherSBEVault")
        self.assertFalse(os.path.exists(fake_vault),
                         "sbe_coldstart.py created %s: an unset BROTHERSBE_VAULT reached "
                         "the home-directory default. Output: %s" % (fake_vault, out))

    # ------------------------------------------------------------------
    def _tmp(self):
        import tempfile
        tmp = tempfile.mkdtemp(prefix="sbe-coldstart-vault-")
        self.addCleanup(__import__("shutil").rmtree, tmp, True)
        return tmp


if __name__ == "__main__":
    unittest.main()
