#!/usr/bin/env python3
"""R-6 (2026-09-07, the persona dogfood): `verify` answers about the file the
ask actually named, not only about whatever HEAD happens to be.

THE FINDING, measured over 40 persona transcripts. Two personas asked, in
their own words, whether a specific migration's backfill had a test. Both
runs were honest and neither was an answer: `sbe verify` resolved its
subject from the single latest commit, a newer commit had landed on top, so
the migration two commits back was invisible to the run, and every gate
correctly reported NO-DATA about a dossier the asker had never heard of. One
persona had to dispatch a reviewer subagent by hand to learn that no test
anywhere touched the migration.

`--path PATTERN` and `--since REF` on `sbe verify` are the fix, and this
file is what proves them: `--path` resolves the named file against the
tracked tree, independently of which commit the run is about, and says for
each match whether a check registered in `.sbe/checks.yml` covers it;
`--since` names the change as a range instead of the latest commit. The
mechanism for both is `src/brothersbe/cli.py::_name_the_change` and
`::_answer_about_named_paths`.

Run standalone: python3 tools/test_sbe_verify_path.py
"""
import io
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
SBE = os.path.join(HERE, "..", "bin", "sbe")

MIGRATION = "migrations/0002_wholesaler_payouts.sql"


def _run(argv, cwd=None):
    out = subprocess.run(argv, capture_output=True, text=True, cwd=cwd,
                         stdin=subprocess.DEVNULL, timeout=120)
    # Three values, not two: see `tools/test_sbe_converge.py`'s `_run` for
    # why every runner helper in this project returns three rather than a
    # (verdict, evidence)-shaped pair the honesty meta-test would flag.
    return out.returncode, out.stdout + out.stderr, out.stderr


#: A registered check whose `covers` glob reaches `src/widget.py` and
#: nothing under `migrations/`, so the migration file this suite seeds is
#: TRACKED but UNCOVERED -- the exact shape A2-S3 and B2-S3 both hit.
REGISTRY_NOT_COVERING_MIGRATION = (
    'schemaVersion: "1.0"\n\n'
    'checks:\n'
    '  widget-tests:\n'
    '    kind: "ran"\n'
    '    why: "fixture check that covers something other than the migration"\n'
    '    command:\n'
    '      executable: "python3"\n'
    '      arguments:\n'
    '        - "-c"\n'
    "        - \"print('ok')\"\n"
    '      cwd: "."\n'
    '    covers:\n'
    '      - "src/widget.py"\n'
    '    runnerFiles: []\n'
    '    protectedEvidence: false\n'
)

#: The calibration fixture: same shape, `covers` widened to reach the
#: migration, so the only variable between the two tests below is whether
#: a registered check's glob actually matches the named file.
REGISTRY_COVERING_MIGRATION = (
    'schemaVersion: "1.0"\n\n'
    'checks:\n'
    '  migration-tests:\n'
    '    kind: "migration"\n'
    '    why: "fixture check that covers the migration directory"\n'
    '    command:\n'
    '      executable: "python3"\n'
    '      arguments:\n'
    '        - "-c"\n'
    "        - \"print('ok')\"\n"
    '      cwd: "."\n'
    '    covers:\n'
    '      - "migrations/**"\n'
    '    runnerFiles: []\n'
    '    protectedEvidence: false\n'
)


class VerifyPathScenario(unittest.TestCase):
    """A minimal real git repository per test: a tracked migration file and
    a `.sbe/checks.yml`, and NOTHING else -- no dossier, no `00-intake.json`.
    `--path` has to answer the coverage question from the tracked tree and
    the check registry alone; a fixture that also built a full dossier
    would leave it ambiguous whether a passing assertion came from `--path`
    or from the aggregate gates A2-S3 already showed produce a wall of
    NO-DATA on exactly this kind of repository.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sbe-verify-path-")
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(self.repo)
        self.git("init", "-q")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "user.name", "fixture")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def git(self, *args):
        code, text, _err = _run(["git", "-C", self.repo] + list(args))
        self.assertEqual(code, 0, "git %s failed: %s" % (args, text))
        return text.strip()

    def sbe(self, *args):
        return _run([sys.executable, SBE] + list(args))

    def _write(self, rel, content):
        full = os.path.join(self.repo, rel)
        parent = os.path.dirname(full)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent)
        io.open(full, "w", encoding="utf-8").write(content)

    def _seed(self, registry_yaml):
        self._write(".sbe/checks.yml", registry_yaml)
        self._write(MIGRATION,
                    "-- fixture migration: adds wholesaler_payouts and a "
                    "backfill nothing here has a test for\n"
                    "CREATE TABLE wholesaler_payouts (id INTEGER PRIMARY KEY);\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "seed")

    def _verify_path(self, *patterns):
        argv = ["verify", "--cwd", self.repo, "--no-decisions"]
        for pattern in patterns:
            argv += ["--path", pattern]
        return self.sbe(*argv)


class TestVerifyAnswersAboutANamedPath(VerifyPathScenario):
    def test_verify_answers_about_a_named_path(self):
        """R-6 (2026-09-07): a migration with no covering check registered
        anywhere. `--path` must name it in plain words, "no check covers
        this file", rather than the ask's own file staying unmentioned
        while the aggregate gates print NO-DATA about a dossier that does
        not exist here (A2-S3's exact transcript). Fails today, before the
        fix: `--path` does not parse, so `sbe verify` exits on argparse's
        own "unrecognized arguments: --path ..." instead.
        """
        self._seed(REGISTRY_NOT_COVERING_MIGRATION)
        code, text, _err = self._verify_path(MIGRATION)
        self.assertIn(MIGRATION, text, text)
        self.assertIn("%s: no check covers this file" % MIGRATION, text, text)


class TestACoveredPathPrintsNoSuchLine(VerifyPathScenario):
    def test_a_covered_path_prints_no_such_line(self):
        """Calibration for the fixture above: the identical migration file,
        with a check registered whose `covers` glob actually reaches it.
        `--path` must name the covering check by id and must NOT print "no
        check covers this file" -- coverage comes from `checks.covers_match`
        against the registry, never from the file merely existing.
        """
        self._seed(REGISTRY_COVERING_MIGRATION)
        code, text, _err = self._verify_path(MIGRATION)
        self.assertIn("%s: covered by migration-tests" % MIGRATION, text, text)
        self.assertNotIn("no check covers this file", text, text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
