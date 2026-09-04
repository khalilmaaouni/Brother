#!/usr/bin/env python3
"""E2.3 (2026-08-31): first contact never prints this machine's own paths, and
never puts an internal noun ahead of the first real value.

THE FINDING, measured by an uncoached external stranger's first ten minutes
of using BrotherSBE: the default `sbe verify` run printed the author's own
absolute home path (`/Users/.../Documents/Kay Vault/...`) into a stranger's
terminal, because 14 of its 15 scored checks read this installation's own
vault, session ledger or source tree rather than the repository the stranger
actually asked about. Separately, the first output a first-time reader saw
was internal jargon (dossier, WAIVED, tier, fence-hygiene, coldstart-receipt)
before anything about their own code.

`sbe_score.py`'s new `--repo-only` flag (which `sbe verify` now passes by
default; see `src/brothersbe/cli.py::_cmd_verify`) stops those 14 checks from
running at all on a first-contact call, which is the mechanism this file
proves. `_name_the_change` (same file) is the mechanism behind the
plain-language first line.

Run standalone: python3 tools/test_sbe_first_contact_paths.py
"""
import io
import os
import re
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
SBE = os.path.join(HERE, "..", "bin", "sbe")

# A UNIX absolute path token: a slash, then path characters, with no path
# character immediately BEFORE the leading slash (so "a/b/c" is not
# mis-split into a bogus "/b/c" hit). Trailing punctuation a sentence would
# carry (a period, a comma, a closing paren) is stripped by the caller.
_ABS_PATH = re.compile(r"(?<![\w./-])(/[\w][\w./-]*)")


def foreign_absolute_paths(transcript, inside):
    """Every absolute path token in `transcript` that does not resolve
    inside `inside`.

    `inside` is realpath'd on both sides of the comparison because macOS's
    `/tmp` is a symlink to `/private/tmp`: a naive string-prefix compare
    would flag the run's own fixture directory as foreign depending on
    which spelling a given subprocess happened to print, which is exactly
    the kind of platform-coupled false alarm that trains people to ignore a
    real one.
    """
    inside_real = os.path.realpath(inside)
    hits = []
    for m in _ABS_PATH.finditer(transcript):
        token = m.group(1).rstrip(".,;:)")
        if not token or token == "/":
            continue
        real = os.path.realpath(token)
        if real == inside_real or real.startswith(inside_real + os.sep):
            continue
        hits.append(token)
    return hits


class FirstRunTranscriptStaysInsideTheRepo(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sbe-first-contact-")
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(self.repo)
        self._git("init", "-q")
        self._git("config", "user.email", "fixture@example.invalid")
        self._git("config", "user.name", "fixture")
        with io.open(os.path.join(self.repo, "app.py"), "w", encoding="utf-8") as f:
            f.write("def f():\n    return 1\n")
        self._git("add", "-A")
        self._git("commit", "-qm", "seed")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _git(self, *args):
        r = subprocess.run(["git", "-C", self.repo] + list(args),
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, "git %s failed: %s" % (args, r.stdout + r.stderr))

    def _first_run_env(self):
        # A FRESH MACHINE has none of these set, and a throwaway HOME keeps
        # this test from depending on, or leaking, whatever the real
        # machine running it happens to have configured -- the same
        # isolation test_sbe_vault_scope.py's ReviewScenario uses.
        env = dict(os.environ)
        fake_home = os.path.join(self.tmp, "fake-home")
        os.makedirs(fake_home, exist_ok=True)
        env["HOME"] = fake_home
        for k in ("BROTHERSBE_VAULT", "BROTHERSBE_REGISTRIES", "SBE_LINT_ROOT",
                  "SBE_CITATION_ROOT"):
            env.pop(k, None)
        return env

    def _transcript(self):
        r = subprocess.run([sys.executable, SBE, "verify", self.repo],
                           cwd=self.tmp, env=self._first_run_env(),
                           capture_output=True, text=True, stdin=subprocess.DEVNULL,
                           timeout=120)
        return r.stdout + r.stderr

    def test_no_absolute_path_outside_the_fixture_repo(self):
        """THE GREP TEST. A fresh-machine `sbe verify` run over a fixture
        repository must never print an absolute path pointing anywhere
        outside that repository: not this installation's own tree (the
        citation-inventory check's unset-SBE_CITATION_ROOT fallback), not a
        vault, not $HOME. This is the exact shape of the measured finding:
        the author's own home path reached a stranger's terminal."""
        out = self._transcript()
        hits = foreign_absolute_paths(out, self.repo)
        self.assertEqual(
            hits, [],
            "absolute path(s) outside the scanned repository reached user-facing "
            "output: %s" % hits)

    def test_the_grep_actually_catches_a_seeded_vendor_path(self):
        """Drive it backwards: a detector that never fires has no teeth.
        Take the real, clean transcript and splice in one line shaped
        exactly like the original finding, then confirm the SAME function
        that just found nothing now finds exactly that."""
        out = self._transcript()
        vendor_path = "/Users/khalil.maaouni/Documents/BrotherSBE"
        seeded = out + ("\ncitation-inventory  PASS  137 URL(s) scanned under %s\n"
                        % vendor_path)
        hits = foreign_absolute_paths(seeded, self.repo)
        self.assertIn(vendor_path, hits,
                     "the detector did not catch a seeded vendor path: %s" % hits)

    def test_the_first_line_is_plain_language_about_the_change_not_jargon(self):
        """The other half of E2.3: no internal noun before the first real
        value. The first line of a default `sbe verify` run must be about
        the user's own change, in plain words, before dossier, WAIVED,
        tier, fence-hygiene, coldstart-receipt or any other internal noun
        this project's own README admits a first hour never has to see."""
        out = self._transcript()
        lines = out.splitlines()
        self.assertTrue(lines, "sbe verify printed nothing at all")
        first_line = lines[0]
        self.assertIn("your change is commit", first_line, first_line)
        for noun in ("dossier", "WAIVED", "tier", "fence-hygiene", "coldstart-receipt",
                     "numbers-manifest", "ran-receipt"):
            self.assertNotIn(noun, first_line,
                             "an internal noun (%r) reached the first line: %s"
                             % (noun, first_line))


if __name__ == "__main__":
    unittest.main()
