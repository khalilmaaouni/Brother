#!/usr/bin/env python3
"""Regression test for WBS row M24 (docs/plan/QUEUE.json): scripts/
migrate_install.py's subprocess calls to scripts/install.py must not
lose state when a child process cannot even print a non-ASCII path
under a locale that is not UTF-8.

MOVED into tools/ on 2026-08-24 and REGISTERED in tools/test_all.py's
SUITES. It was first written into scripts/ deliberately, reasoning that
_discover only globs tools/test_*.py so a suite placed elsewhere would
not trip the inventory refusal. That reasoning was correct about the
mechanism and wrong about the consequence: a suite the inventory cannot
see is a suite THE BATTERY NEVER RUNS, and it cannot even complain,
because the control that shouts about an unregistered suite is the same
one that only looks in tools/. Passing when run by hand is not the same
as being covered, and the row's done_check naming a direct invocation
does not make the battery's silence acceptable.

Registering it here is also what this repository already does: several
tools/test_*.py suites test scripts/*.py, so the convention is that a
test lives in tools/ even when its target does not.

The underlying gap is left in place and reported rather than widened in
this change: _discover (tools/test_all.py) scans only its own directory,
so a test_*.py under scripts/ is still neither run nor flagged. Nothing
lives there now.

REAL temporary directories and a REAL non-ASCII path throughout: the
defect is about what actually gets removed from disk, so nothing here
is mocked. Both scenarios below run scripts/migrate_install.py as a
real subprocess against a real, throwaway --home fixture whose own path
contains literal Japanese characters and an emoji.

  1. test_normal_locale_round_trips_the_nonascii_name: an ordinary
     (UTF-8) environment. The apply must succeed, the stray marker file
     from the stranded clone must be gone, the freshly installed fence
     hook must be byte-identical to this project's own copy, and the
     non-ASCII directory name itself must still resolve on disk exactly
     as written: content round-trips intact.

  2. test_forced_non_utf8_locale_preserves_directory_on_failure: the
     same fixture, but LC_ALL, LANG, PYTHONCOERCECLOCALE and PYTHONUTF8
     are set so the CHILD scripts/install.py subprocess cannot encode
     the non-ASCII --target path to its own stdout and crashes before
     writing anything. Calibrated by hand against the pre-fix shape of
     main()'s apply branch (preflight moved to AFTER shutil.rmtree):
     that reverted order loses the directory in exactly this scenario
     (returncode 1, skill_dir and marker.txt both gone), which is the
     defect this test now rules out. The migration must exit non-zero,
     must never print DONE, and above all must leave the stranded
     directory and its marker file exactly where they were: deleting
     first and failing second is the one order this row exists to
     forbid.

Python 3.9, standard library only. No em or en dashes anywhere in this
file, its comments, or its output.
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCRIPT = os.path.join(ROOT, "scripts", "migrate_install.py")
INSTALL_PY = os.path.join(ROOT, "scripts", "install.py")

# A real, printable non-ASCII segment: CJK characters plus an emoji, the
# same combination this row's own done_check names ("a child emitting
# non-ASCII").
NONASCII = "日本\U0001f600"  # "日本" + U+1F600 (grinning face)


def _plugin_name():
    manifest_path = os.path.join(ROOT, ".claude-plugin", "plugin.json")
    with io.open(manifest_path, encoding="utf-8") as fh:
        manifest = json.loads(fh.read())
    name = manifest.get("name")
    return name if isinstance(name, str) and name else "brotherme"


def _base_env():
    """The same stripped environment tools/test_bm.py's own
    TestM1MigrateInstallScript._env uses, so this fixture never picks up
    a real BrotherMode session's own state by accident."""
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    for k in ("BROTHERMODE_VAULT", "BROTHERMODE_ROOT", "BROTHERME_CONFIG",
              "BM_FENCE_STRICT", "BM_FENCE_SESSION_ID", "CLAUDE_SESSION_ID"):
        env.pop(k, None)
    return env


def _legacy_locale_env():
    """Forces the CHILD scripts/install.py subprocess (inherited from
    this env, since migrate_install.py's own subprocess.run calls to it
    never override env=) onto Python's own pre-PEP-538 legacy behavior:
    ascii stdout with the surrogateescape error handler. That handler
    forgives already-invalid raw bytes; it does NOT know how to encode a
    genuine, printable non-ASCII character, so install.py's own _out(),
    the first time it prints the --target path, raises
    UnicodeEncodeError and exits non-zero before writing a single file.
    Verified by hand before this test was written: this same forcing,
    on this machine, makes a bare `print("\\u65e5\\u672c\\U0001f600")`
    child raise with sys.stdout.encoding == "ascii" and
    sys.stdout.errors == "surrogateescape"; _demo() below re-proves this
    on every run rather than trusting that note."""
    env = _base_env()
    env["LC_ALL"] = "C"
    env["LANG"] = "C"
    env["PYTHONCOERCECLOCALE"] = "0"
    env["PYTHONUTF8"] = "0"
    env.pop("PYTHONIOENCODING", None)
    return env


def _stranded_home(tmp, plugin_name):
    """The same stranded-clone shape tools/test_bm.py's own
    TestM1MigrateInstallScript._stranded_home builds (a skill_dir with a
    loader-less hooks.json plus a stray marker file), except the whole
    --home path carries a real non-ASCII segment."""
    home = os.path.join(tmp, "home-" + NONASCII)
    skill_dir = os.path.join(home, ".claude", "skills", plugin_name)
    os.makedirs(os.path.join(skill_dir, "hooks"))
    with io.open(os.path.join(skill_dir, "hooks", "hooks.json"),
                 "w", encoding="utf-8") as fh:
        json.dump({"hooks": {}}, fh)
    with io.open(os.path.join(skill_dir, "marker.txt"), "w",
                 encoding="utf-8") as fh:
        fh.write("stray file from the stranded copy\n")
    return home, skill_dir


def _run(env, home):
    return subprocess.run(
        [sys.executable, SCRIPT, "--home", home, "--apply",
         "--i-understand-this-changes-every-session"],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        encoding="utf-8", errors="backslashreplace", timeout=180)


class TestM24MigrateInstallEncodingAndOrdering(unittest.TestCase):

    def setUp(self):
        if not os.path.isfile(SCRIPT):
            self.skipTest("scripts/migrate_install.py not found")
        if not os.path.isfile(INSTALL_PY):
            self.skipTest("scripts/install.py not found")
        self.plugin_name = _plugin_name()

    def test_normal_locale_round_trips_the_nonascii_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            home, skill_dir = _stranded_home(tmp, self.plugin_name)
            r = _run(_base_env(), home)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("DONE", r.stdout, r.stdout + r.stderr)
            self.assertFalse(
                os.path.isfile(os.path.join(skill_dir, "marker.txt")),
                "the stranded copy's own stray file must be gone after "
                "a real apply")
            installed_hook = os.path.join(
                skill_dir, "tools", "bm_fence_hook.py")
            source_hook = os.path.join(ROOT, "tools", "bm_fence_hook.py")
            self.assertTrue(os.path.isfile(installed_hook),
                            "a real install must have landed the fence "
                            "hook at the non-ASCII target path")
            with io.open(installed_hook, "rb") as fh:
                installed_bytes = fh.read()
            with io.open(source_hook, "rb") as fh:
                source_bytes = fh.read()
            self.assertEqual(
                installed_bytes, source_bytes,
                "the installed fence hook must be byte-identical to "
                "this project's own copy: a byte lost anywhere in this "
                "path (including the non-ASCII directory name it was "
                "written under) is content that did not round-trip")
            # The exact non-ASCII path string, unmangled, still resolves.
            self.assertTrue(
                os.path.isdir(skill_dir),
                "the non-ASCII --target path itself must resolve "
                "exactly as constructed, not as some mojibake neighbor")

    def test_forced_non_utf8_locale_preserves_directory_on_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            home, skill_dir = _stranded_home(tmp, self.plugin_name)
            r = _run(_legacy_locale_env(), home)
            # (a) never exits 0 with mangled content.
            self.assertNotEqual(
                r.returncode, 0,
                "a child that cannot even encode the non-ASCII target "
                "path must not be read as a successful migration. "
                "stdout:\n%s\nstderr:\n%s" % (r.stdout, r.stderr))
            self.assertNotIn(
                "DONE", r.stdout,
                "a failed reinstall must never be reported as DONE. "
                "stdout:\n%s" % r.stdout)
            # (b) the whole point of the ordering half of this row: an
            # encode/decode failure on the reinstall side must leave the
            # stranded directory exactly where it was, never removed
            # first and reported second.
            self.assertTrue(
                os.path.isdir(skill_dir),
                "the stranded directory must still be on disk after a "
                "failed apply; removing it before the reinstall is "
                "proven to work is the exact defect this row fixes. "
                "stdout:\n%s\nstderr:\n%s" % (r.stdout, r.stderr))
            self.assertTrue(
                os.path.isfile(os.path.join(skill_dir, "marker.txt")),
                "the stranded copy's own marker file must still be "
                "present too: nothing about it was ever legitimately "
                "read, so nothing about it should have been touched. "
                "stdout:\n%s\nstderr:\n%s" % (r.stdout, r.stderr))


def _demo():
    """The smallest possible sanity check of the locale-forcing trick
    itself, run before the real subprocess-driven tests: a bare child
    process, forced the same way, must crash trying to print NONASCII,
    proving the two tests above exercise a real, reproduced condition on
    this machine rather than an environment change that happens to do
    nothing here."""
    probe = subprocess.run(
        [sys.executable, "-c",
         "print(%r)" % NONASCII],
        env=_legacy_locale_env(), stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, encoding="utf-8",
        errors="backslashreplace", timeout=30)
    assert probe.returncode != 0, (
        "the legacy-locale environment forcing did not reproduce a "
        "genuine encode failure on this machine; the two tests above "
        "would not be testing anything real here. stdout=%r stderr=%r"
        % (probe.stdout, probe.stderr))
    assert "UnicodeEncodeError" in probe.stderr, probe.stderr


if __name__ == "__main__":
    _demo()
    runner = unittest.TextTestRunner(verbosity=0)
    result = runner.run(
        unittest.defaultTestLoader.loadTestsFromTestCase(
            TestM24MigrateInstallEncodingAndOrdering))
    if not result.wasSuccessful():
        sys.exit(1)
    print("OK")
