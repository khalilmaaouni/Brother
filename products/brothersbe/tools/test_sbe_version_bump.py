#!/usr/bin/env python3
"""Fixtures for `sbe version bump`. Run: python3 tools/test_sbe_version_bump.py

Every test builds a stand-in repository root in a temporary directory carrying
all four declaration sites and runs the real command against it, because the
defect this command exists for is a HAND edit missing one of the sites, and a
fixture that mocked the files would test the mock. The suite never runs
against this repository's own root: a passing test must not be able to move
the live version by accident.

The runner helper returns three values (exit code, stdout, stderr), the house
shape the honesty meta-test can trust: a helper collapsing to two values has
been rejected by that sweep four times in this repository's history.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
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

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
SBE = os.path.join(ROOT, "bin", "sbe")

OLD = "1.0.0-rc.8"
NEW = "1.0.0-rc.9"


def sbe_version(cwd, *args):
    """Run `sbe version <args>` in cwd. Returns (exit code, stdout, stderr),
    three values so a caller can never mistake silence for a verdict."""
    out = subprocess.run([sys.executable, SBE, "version"] + list(args),
                         cwd=cwd, capture_output=True, text=True)
    return out.returncode, out.stdout, out.stderr


def make_root(version=OLD, market_second=None, digest_version=None):
    """A stand-in repo root with the four declaration sites. market_second
    and digest_version let a fixture plant a deliberate disagreement."""
    root = tempfile.mkdtemp(prefix="sbe-vbump-")
    os.makedirs(os.path.join(root, ".claude-plugin"))
    with open(os.path.join(root, "VERSION"), "w") as fh:
        fh.write(version + "\n")
    with open(os.path.join(root, ".claude-plugin", "plugin.json"), "w") as fh:
        json.dump({"name": "brothersbe", "version": version}, fh, indent=2)
    with open(os.path.join(root, ".claude-plugin", "marketplace.json"), "w") as fh:
        json.dump({"name": "stand-in", "version": version,
                   "plugins": [{"name": "brothersbe",
                                "version": market_second or version}]}, fh, indent=2)
    with open(os.path.join(root, "DIGEST.md"), "w") as fh:
        fh.write("STAND-IN DIGEST, version %s (first line carries the pin)\n"
                 "second line stays untouched, version %s mentioned here too\n"
                 % (digest_version or version, OLD))
    return root


def read_sites(root):
    with open(os.path.join(root, "VERSION")) as fh:
        v = fh.read().strip()
    with open(os.path.join(root, ".claude-plugin", "plugin.json")) as fh:
        p = json.load(fh)["version"]
    with open(os.path.join(root, ".claude-plugin", "marketplace.json")) as fh:
        m = json.load(fh)
    with open(os.path.join(root, "DIGEST.md")) as fh:
        d = fh.readline()
    return v, p, m["version"], m["plugins"][0]["version"], d


class TestHappyPath(unittest.TestCase):
    def setUp(self):
        self.root = make_root()
        self.addCleanup(shutil.rmtree, self.root)

    def test_bump_moves_all_five_declarations_and_rereads(self):
        code, out, err = sbe_version(self.root, "bump", NEW)
        self.assertEqual(code, 0, "stdout=%r stderr=%r" % (out, err))
        v, p, m1, m2, d = read_sites(self.root)
        self.assertEqual((v, p, m1, m2), (NEW, NEW, NEW, NEW))
        self.assertIn("version " + NEW, d)
        self.assertIn("re-read: all 5 declaration(s) now read " + NEW, out)

    def test_digest_second_line_and_prose_survive(self):
        code, out, err = sbe_version(self.root, "bump", NEW)
        self.assertEqual(code, 0, "stdout=%r stderr=%r" % (out, err))
        with open(os.path.join(self.root, "DIGEST.md")) as fh:
            lines = fh.read().splitlines()
        self.assertIn(OLD, lines[1], "only line 1 is a declaration site; "
                                     "line 2 is historical prose and must keep the old text")

    def test_reminders_name_the_three_undone_steps(self):
        code, out, err = sbe_version(self.root, "bump", NEW)
        self.assertEqual(code, 0, "stdout=%r stderr=%r" % (out, err))
        for token in ("CHANGELOG.md", "replay_book.py --write", "checksums.sh"):
            self.assertIn(token, out, "reminder for %s missing" % token)

    def test_dry_run_writes_nothing(self):
        code, out, err = sbe_version(self.root, "bump", NEW, "--dry-run")
        self.assertEqual(code, 0, "stdout=%r stderr=%r" % (out, err))
        self.assertIn("would edit", out)
        v, p, m1, m2, d = read_sites(self.root)
        self.assertEqual((v, p, m1, m2), (OLD, OLD, OLD, OLD))
        self.assertIn("version " + OLD, d)


class TestRefusals(unittest.TestCase):
    def test_malformed_version_refused_by_name(self):
        root = make_root()
        self.addCleanup(shutil.rmtree, root)
        code, out, err = sbe_version(root, "bump", "v1.0.0-rc.9")
        self.assertEqual(code, 2, "stdout=%r stderr=%r" % (out, err))
        self.assertIn("REFUSED", err)
        self.assertIn("v1.0.0-rc.9", err)
        self.assertEqual(read_sites(root)[0], OLD, "a refusal must write nothing")

    def test_disagreeing_sites_refused_and_each_named(self):
        root = make_root(market_second="1.0.0-rc.7")
        self.addCleanup(shutil.rmtree, root)
        code, out, err = sbe_version(root, "bump", NEW)
        self.assertEqual(code, 1, "stdout=%r stderr=%r" % (out, err))
        self.assertIn("already disagree", err)
        self.assertIn("1.0.0-rc.7", err)
        self.assertIn(OLD, err)
        self.assertEqual(read_sites(root)[0], OLD, "a refusal must write nothing")

    def test_same_version_refused_as_a_no_op(self):
        root = make_root()
        self.addCleanup(shutil.rmtree, root)
        code, out, err = sbe_version(root, "bump", OLD)
        self.assertEqual(code, 1, "stdout=%r stderr=%r" % (out, err))
        self.assertIn("succeeds at nothing", err)

    def test_missing_site_refused_by_path(self):
        root = make_root()
        self.addCleanup(shutil.rmtree, root)
        os.remove(os.path.join(root, "DIGEST.md"))
        code, out, err = sbe_version(root, "bump", NEW)
        self.assertEqual(code, 1, "stdout=%r stderr=%r" % (out, err))
        self.assertIn("DIGEST.md", err)
        with open(os.path.join(root, "VERSION")) as fh:
            self.assertEqual(fh.read().strip(), OLD, "a refusal must write nothing")

    def test_unparseable_digest_line_reads_as_disagreement_not_a_pass(self):
        root = make_root()
        self.addCleanup(shutil.rmtree, root)
        with open(os.path.join(root, "DIGEST.md"), "w") as fh:
            fh.write("a first line with no version pin at all\n")
        code, out, err = sbe_version(root, "bump", NEW)
        self.assertEqual(code, 1, "stdout=%r stderr=%r" % (out, err))
        self.assertIn("UNREADABLE", err)

    def test_bare_version_still_prints_and_exits_zero(self):
        code, out, err = sbe_version(ROOT)
        self.assertEqual(code, 0, "stdout=%r stderr=%r" % (out, err))
        self.assertIn("evidence schema", out)


class TestNonUtf8CodePage(unittest.TestCase):
    """Windows audit finding: this module was the ONE file in the package that
    opened text files with no encoding= argument, so it read and wrote in the
    process's locale encoding rather than UTF-8. On a non-UTF-8 code page the
    read goes wrong in one of two ways, checked against the real codecs
    rather than asserted: a strict locale raises UnicodeDecodeError and the
    release command dies with a traceback instead of a refusal, while a
    permissive page silently mis-decodes (this class's own fixture decodes to
    mojibake under both 1252 and 932), corrupting text with no traceback.

    The C locale takes the raising path and is what makes the finding
    testable without a Windows box: with LC_ALL=C and UTF-8 mode off,
    Python's preferred encoding is ANSI_X3.4-1968 (ASCII), and an unencoded
    open() of a file holding a UTF-8 byte raises. What this does NOT prove is
    Windows itself, which stays UNVERIFIED until the engineer re-runs; it
    proves the mechanism (the raising half of it) and pins the fix, which
    closes both halves by never consulting the locale at all."""

    #: Non-ASCII in a spot a real repository carries it: DIGEST.md is prose,
    #: and prose acquires an accented name or a curly quote sooner or later.
    ACCENTED = "an accented name in the prose: Björn Kjær"

    def _c_locale_env(self):
        env = dict(os.environ)
        env.update(LC_ALL="C", LANG="C", LANGUAGE="",
                   PYTHONUTF8="0", PYTHONCOERCECLOCALE="0")
        return env

    def _run(self, cwd, *args):
        out = subprocess.run([sys.executable, SBE, "version"] + list(args),
                             cwd=cwd, capture_output=True, text=True,
                             env=self._c_locale_env())
        return out.returncode, out.stdout, out.stderr

    def _root_with_non_ascii(self):
        root = make_root()
        self.addCleanup(shutil.rmtree, root)
        path = os.path.join(root, "DIGEST.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("STAND-IN DIGEST, version %s (first line carries the pin)\n"
                     "%s\n" % (OLD, self.ACCENTED))
        return root

    def test_a_non_ascii_digest_bumps_instead_of_raising_on_a_c_locale(self):
        root = self._root_with_non_ascii()
        code, out, err = self._run(root, "bump", NEW)
        self.assertNotIn("UnicodeDecodeError", err,
                         "the bump died decoding its own declaration site "
                         "instead of reading it as UTF-8:\n%s" % err)
        self.assertNotIn("Traceback", err,
                         "a non-ASCII byte produced a traceback rather than a "
                         "verdict:\n%s" % err)
        self.assertEqual(code, 0, "stdout=%r stderr=%r" % (out, err))
        v, p, m1, m2, d = read_sites(root)
        self.assertEqual((v, p, m1, m2), (NEW, NEW, NEW, NEW))
        self.assertIn("version " + NEW, d)

    def test_the_seal_list_names_the_sandbox_repin(self):
        """The reminder list is the ONLY place a releaser is told what remains,
        so a step missing from it is a step that does not happen.

        Measured cost of the gap: the 3.4.0 release followed this list exactly
        and still failed the gate battery at command 42 of 52, because
        docs/guides/00-sandbox.md quotes two commit hashes from a deterministic
        driven run built from this repository's own files. Moving the version
        moves the tree, which moves the hash. The guide's own prose predicts it
        and tools/regen_sandbox_guide.py exists to repin it; nothing told the
        releaser to run it.

        Asserted on the printed output rather than on the constant, because
        what matters is what a releaser actually reads."""
        root = make_root()
        self.addCleanup(shutil.rmtree, root)
        code, out, err = self._run(root, "bump", NEW)
        self.assertEqual(code, 0, "stdout=%r stderr=%r" % (out, err))
        self.assertIn("regen_sandbox_guide.py", out,
                      "the seal list does not name the sandbox guide repin, so "
                      "a release that follows it exactly still fails the "
                      "battery at the doc-truth command")

    def test_the_seal_list_names_the_booklet_rebind(self):
        """The second missing step, found by the 3.4.1 battery one release
        after the first was fixed.

        The booklet's provenance section is RENDERED FROM VERSION, so moving
        the version stales it and `sbe book --check --strict` failed the
        battery at command 21 of 52 with "the section 'provenance' was
        rendered from VERSION, which has changed since".

        This is NOT covered by the replay_book line already in the list.
        replay_book patches echoes of live OUTPUT into the chapters; `sbe
        book` rebinds generated SECTIONS to their sources. Running one does
        not do the other, and the list had only ever named one of them, which
        is why a release could follow it exactly and still fail.

        Asserted on the printed output rather than on the constant, because
        what matters is what a releaser actually reads."""
        root = make_root()
        self.addCleanup(shutil.rmtree, root)
        code, out, err = self._run(root, "bump", NEW)
        self.assertEqual(code, 0, "stdout=%r stderr=%r" % (out, err))
        self.assertIn("sbe book", out,
                      "the seal list does not name the booklet rebind, so a "
                      "release that follows it exactly still fails the "
                      "battery at the book-check command")
        self.assertLess(out.index("sbe book"), out.index("checksums.sh"),
                        "the rebind writes tracked files, so it must be "
                        "printed before the manifest step")

    def test_the_seal_list_keeps_checksums_last(self):
        """Ordering is load bearing, not cosmetic. The repin EDITS a tracked
        file, so a manifest generated before it ships stale. This asserts the
        repin is printed before the checksums line, which is the whole reason
        the new step was inserted third rather than appended."""
        root = make_root()
        self.addCleanup(shutil.rmtree, root)
        code, out, err = self._run(root, "bump", NEW)
        self.assertEqual(code, 0, "stdout=%r stderr=%r" % (out, err))
        self.assertLess(out.index("regen_sandbox_guide.py"),
                        out.index("checksums.sh"),
                        "the manifest step must stay last; a repin after it "
                        "leaves the manifest describing the previous bytes")

    def test_the_non_ascii_prose_survives_the_rewrite_byte_for_byte(self):
        """The write half of the same defect: writing back in the locale
        encoding would either mangle those letters or refuse. Read back as
        UTF-8, explicitly, so the assertion cannot pass by the test making the
        same locale assumption the fix removed."""
        root = self._root_with_non_ascii()
        code, out, err = self._run(root, "bump", NEW)
        self.assertEqual(code, 0, "stdout=%r stderr=%r" % (out, err))
        with open(os.path.join(root, "DIGEST.md"), encoding="utf-8") as fh:
            body = fh.read()
        self.assertIn(self.ACCENTED, body,
                      "the rewrite lost or mangled the non-ASCII prose it was "
                      "not asked to touch")


if __name__ == "__main__":
    unittest.main(verbosity=1)
