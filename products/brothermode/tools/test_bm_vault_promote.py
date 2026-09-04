#!/usr/bin/env python3
"""Tests for bm_vault_promote, on tiny synthetic fixture vaults.

Run: python3 tools/test_bm_vault_promote.py      (unittest output, exit 0 or 1)
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

TOOL_DIR = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(TOOL_DIR, "bm_vault_promote.py")


def run(argv):
    p = subprocess.run([sys.executable, TOOL] + argv,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return p.returncode, (p.stdout + p.stderr).decode("utf-8", "replace")


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


def make_vault(files):
    tmp = tempfile.mkdtemp(prefix="bm-vault-promote-")
    vault = os.path.join(tmp, "vault")
    for rel, text in files.items():
        write(os.path.join(vault, rel), text)
    return tmp, vault


FAILURE_NOTE = "---\ntype: failure\nstatus: standing\ncreated: %s\ntags: [x]\n---\nBody.\n"
FINDING_NOTE = "---\ntype: finding\nstatus: open\ncreated: %s\n---\nBody.\n"
SESSION_LOG = "---\ntype: session-log\nstatus: standing\ncreated: %s\n---\nBody.\n"


class EmptyVault(unittest.TestCase):
    def test_no_vault_directory_is_no_data_never_a_crash(self):
        tmp = tempfile.mkdtemp(prefix="bm-vault-promote-missing-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        code, out = run(["check", "--vault", os.path.join(tmp, "nope")])
        self.assertTrue(code == 3 and "NO-DATA" in out, out)


class NeverDistilled(unittest.TestCase):
    """No 40-Failures note exists at all: every session and every raw note found
    counts toward the nudge, and low thresholds must fire it."""

    @classmethod
    def setUpClass(cls):
        cls.tmp, cls.vault = make_vault({
            "10-Projects/x/Sessions/2026-08-20-a.md": SESSION_LOG % "2026-08-20",
            "10-Projects/x/Sessions/2026-08-21-b.md": SESSION_LOG % "2026-08-21",
        })

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_last_distillation_is_none_and_the_nudge_fires_at_a_low_threshold(self):
        code, out = run(["check", "--vault", self.vault, "--session-threshold", "1", "--json"])
        self.assertEqual(code, 0, out)
        data = json.loads(out)
        self.assertIsNone(data["last_distillation"], out)
        self.assertEqual(data["sessions_since"], 2, out)
        self.assertTrue(data["nudge"], out)


class UnderThreshold(unittest.TestCase):
    """A recent distillation and few sessions since: no nudge."""

    @classmethod
    def setUpClass(cls):
        cls.tmp, cls.vault = make_vault({
            "40-Failures/recent.md": FAILURE_NOTE % "2026-08-28",
            "10-Projects/x/Sessions/2026-08-29-a.md": SESSION_LOG % "2026-08-29",
        })

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_one_session_since_a_recent_distillation_does_not_nudge(self):
        code, out = run(["check", "--vault", self.vault, "--json"])
        self.assertEqual(code, 0, out)
        data = json.loads(out)
        self.assertEqual(data["last_distillation"], "2026-08-28", out)
        self.assertEqual(data["sessions_since"], 1, out)
        self.assertFalse(data["nudge"], out)


class SessionThresholdCrossed(unittest.TestCase):
    """Enough sessions since the last distillation crosses the session threshold even
    though the raw-note threshold alone would not."""

    @classmethod
    def setUpClass(cls):
        cls.tmp, cls.vault = make_vault({
            "40-Failures/old.md": FAILURE_NOTE % "2026-08-01",
            "10-Projects/x/Sessions/2026-08-10-a.md": SESSION_LOG % "2026-08-10",
            "10-Projects/x/Sessions/2026-08-11-b.md": SESSION_LOG % "2026-08-11",
            "10-Projects/y/Sessions/2026-08-12-c.md": SESSION_LOG % "2026-08-12",
        })

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_three_sessions_since_crosses_a_threshold_of_two(self):
        code, out = run(["check", "--vault", self.vault, "--session-threshold", "2",
                         "--note-threshold", "50", "--json"])
        self.assertEqual(code, 0, out)
        data = json.loads(out)
        self.assertEqual(data["sessions_since"], 3, out)
        self.assertTrue(data["nudge"], out)


class PreDateExcluded(unittest.TestCase):
    """A session log dated before the last distillation must not count toward
    "since", the exact defect this counter would have if it summed everything instead
    of filtering by date."""

    @classmethod
    def setUpClass(cls):
        cls.tmp, cls.vault = make_vault({
            "40-Failures/mid.md": FAILURE_NOTE % "2026-08-15",
            "10-Projects/x/Sessions/2026-08-10-before.md": SESSION_LOG % "2026-08-10",
            "10-Projects/x/Sessions/2026-08-20-after.md": SESSION_LOG % "2026-08-20",
        })

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_only_the_after_session_counts(self):
        code, out = run(["check", "--vault", self.vault, "--json"])
        self.assertEqual(code, 0, out)
        data = json.loads(out)
        self.assertEqual(data["sessions_since"], 1, out)


class RoutingPagesExcluded(unittest.TestCase):
    """Failures-Index.md and Failures-by-Symptom.md are routing pages, regenerated
    routinely; if they counted as distillations, an index refresh would silently reset
    the counter without a single new lesson actually being written down."""

    @classmethod
    def setUpClass(cls):
        cls.tmp, cls.vault = make_vault({
            "40-Failures/Failures-Index.md": FAILURE_NOTE % "2026-08-29",
            "40-Failures/Failures-by-Symptom.md": FAILURE_NOTE % "2026-08-29",
            "40-Failures/real.md": FAILURE_NOTE % "2026-08-01",
            "10-Projects/x/Sessions/2026-08-20-a.md": SESSION_LOG % "2026-08-20",
        })

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_the_index_pages_todays_date_does_not_win_as_last_distillation(self):
        code, out = run(["check", "--vault", self.vault, "--json"])
        self.assertEqual(code, 0, out)
        data = json.loads(out)
        self.assertEqual(data["last_distillation"], "2026-08-01", out)


class NoteThresholdCrossed(unittest.TestCase):
    """Findings/Decisions/Failures notes since the last distillation cross the
    raw-note threshold even with sessions_since at zero."""

    @classmethod
    def setUpClass(cls):
        cls.tmp, cls.vault = make_vault({
            "40-Failures/old.md": FAILURE_NOTE % "2026-08-01",
            "10-Projects/x/Findings/f1.md": FINDING_NOTE % "2026-08-10",
            "10-Projects/x/Findings/f2.md": FINDING_NOTE % "2026-08-11",
            "10-Projects/x/Decisions/d1.md": FINDING_NOTE % "2026-08-12",
        })

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_three_raw_notes_crosses_a_threshold_of_two_with_zero_sessions(self):
        code, out = run(["check", "--vault", self.vault, "--note-threshold", "2",
                         "--session-threshold", "50", "--json"])
        self.assertEqual(code, 0, out)
        data = json.loads(out)
        self.assertEqual(data["sessions_since"], 0, out)
        self.assertEqual(data["notes_since"], 3, out)
        self.assertTrue(data["nudge"], out)


class NeverBlocks(unittest.TestCase):
    """Whatever the counters say, exit code is always 0 (a real vault dir found): this
    tool nudges, per the constitution's own append-only law, it must never block a
    session the way a check-class gate elsewhere in this estate legitimately can."""

    @classmethod
    def setUpClass(cls):
        cls.tmp, cls.vault = make_vault({
            "10-Projects/x/Sessions/2026-01-01-a.md": SESSION_LOG % "2026-01-01",
        })

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_exit_is_zero_even_when_the_nudge_fires(self):
        code, out = run(["check", "--vault", self.vault, "--session-threshold", "0"])
        self.assertEqual(code, 0, out)
        self.assertIn("NUDGE", out, out)


if __name__ == "__main__":
    unittest.main(verbosity=1)
