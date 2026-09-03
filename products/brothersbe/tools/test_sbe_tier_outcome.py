#!/usr/bin/env python3
"""Tests for tools/sbe_tier_outcome.py (H5), driven backwards from three
cases:
  1. a T1 change later hit by a defect naming its row shows the link counted
  2. a tier with no closures reads NO-DATA
  3. a malformed intake file is skipped with a named warning, never a crash

Run: python3 tools/test_sbe_tier_outcome.py
"""
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
SBE = os.path.join(ROOT, "bin", "sbe")
sys.path.insert(0, os.path.join(ROOT, "src"))

_spec = importlib.util.spec_from_file_location(
    "sbe_tier_outcome", os.path.join(HERE, "sbe_tier_outcome.py"))
tro = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tro)


def _run(argv, cwd=None):
    out = subprocess.run(argv, capture_output=True, text=True, cwd=cwd,
                         stdin=subprocess.DEVNULL, timeout=180)
    return {"code": out.returncode, "stdout": out.stdout, "stderr": out.stderr}


def _git_repo(path):
    subprocess.run(["git", "-C", path, "init", "-q"], check=True)
    subprocess.run(["git", "-C", path, "config", "user.email", "e@e"], check=True)
    subprocess.run(["git", "-C", path, "config", "user.name", "T"], check=True)
    with io.open(os.path.join(path, "seed.txt"), "w", encoding="utf-8") as fh:
        fh.write("seed\n")
    subprocess.run(["git", "-C", path, "add", "-A"], check=True)
    subprocess.run(["git", "-C", path, "commit", "-qm", "seed"], check=True)


class TierOutcomeCase(unittest.TestCase):
    """One real repo per test: `sbe task open`/`close` for the closed-task
    side, hand-written `00-intake.json` files (H4's `origin`, H8's
    `openedAt`) for the tier and defect-link side, exactly the fixture
    shape `tools/test_sbe_decisions.py`'s `TestCloseDurationsByTier` already
    builds."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sbe-tier-outcome-")
        _git_repo(self.tmp)

    def _write_intake(self, change, tier, opened_at, origin=None):
        dossier = os.path.join(self.tmp, "design", change)
        os.makedirs(dossier)
        payload = {"answers": {}, "tier": tier, "override": None,
                  "override_reason": None, "openedAt": opened_at}
        if origin is not None:
            payload["origin"] = origin
        with io.open(os.path.join(dossier, "00-intake.json"), "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        subprocess.run(["git", "-C", self.tmp, "add", "-A"], check=True)
        subprocess.run(["git", "-C", self.tmp, "commit", "-qm", "intake " + change], check=True)

    def _open_and_close(self, task_id, change, owns):
        base = subprocess.run(["git", "-C", self.tmp, "rev-parse", "HEAD"],
                              capture_output=True, text=True, check=True).stdout.strip()
        _run([sys.executable, SBE, "task", "open", "--id", task_id, "--agent", "a",
             "--role", "writer", "--base", base, "--verify", "true",
             "--change", change, "--owns", owns, "--cwd", self.tmp], cwd=self.tmp)
        with io.open(os.path.join(self.tmp, owns), "w", encoding="utf-8") as fh:
            fh.write("in scope\n")
        subprocess.run(["git", "-C", self.tmp, "add", "-A"], check=True)
        subprocess.run(["git", "-C", self.tmp, "commit", "-qm", task_id], check=True)
        result = _run([sys.executable, SBE, "task", "close", task_id, "--change", change,
                       "--cwd", self.tmp], cwd=self.tmp)
        self.assertEqual(result["code"], 0, result["stdout"] + result["stderr"])

    def test_a_t1_change_later_hit_by_a_defect_naming_its_row_shows_the_link_counted(self):
        self._write_intake("change-a", "T1", "2020-01-01T00:00:00Z")
        self._open_and_close("w1", "change-a", "owned.txt")
        # A later defect intake names the closed change's own dossier name.
        self._write_intake("fix-of-a", "T1", "2020-02-01T00:00:00Z",
                           origin={"type": "defect", "fixes": "regression in change-a"})
        lines = tro.render(self.tmp)
        text = "\n".join(lines)
        self.assertRegex(text, r"T1: 1 closed, median \d+s close duration, 1 defect-linked",
                         text)

    def test_a_tier_with_closures_and_no_defect_link_prints_the_zero_as_a_count(self):
        self._write_intake("change-b", "T2", "2020-01-01T00:00:00Z")
        self._open_and_close("w2", "change-b", "owned2.txt")
        lines = tro.render(self.tmp)
        text = "\n".join(lines)
        self.assertRegex(text, r"T2: 1 closed, median \d+s close duration, 0 defect-linked",
                         text)
        self.assertNotIn("T2: NO-DATA", text)

    def test_a_tier_with_no_closures_reads_no_data(self):
        lines = tro.render(self.tmp)
        text = "\n".join(lines)
        self.assertIn("T3: NO-DATA", text)

    def test_a_malformed_intake_file_is_skipped_with_a_named_warning_never_a_crash(self):
        self._write_intake("change-c", "T1", "2020-01-01T00:00:00Z")
        self._open_and_close("w3", "change-c", "owned3.txt")
        broken = os.path.join(self.tmp, "design", "broken-dossier")
        os.makedirs(broken)
        with io.open(os.path.join(broken, "00-intake.json"), "w", encoding="utf-8") as fh:
            fh.write("{not valid json")
        lines = tro.render(self.tmp)
        text = "\n".join(lines)
        self.assertIn("WARNING", text)
        self.assertIn("broken-dossier", text)
        # A malformed sibling never prevents the good tier line from printing.
        self.assertRegex(text, r"T1: 1 closed, median \d+s close duration, 0 defect-linked",
                         text)

    def test_main_always_exits_zero_over_a_real_repo(self):
        result = _run([sys.executable, os.path.join(ROOT, "tools", "sbe_tier_outcome.py"),
                       self.tmp], cwd=self.tmp)
        self.assertEqual(result["code"], 0, result["stdout"] + result["stderr"])
        self.assertIn("TIER VS OUTCOME", result["stdout"])

    def test_a_crash_inside_render_is_caught_by_safe_render_never_raises(self):
        orig = tro.closed_changes_by_tier
        try:
            def boom(root):
                raise RuntimeError("boom")
            tro.closed_changes_by_tier = boom
            lines = tro.safe_render(self.tmp)
            text = "\n".join(lines)
            self.assertIn("NO-DATA", text)
            self.assertIn("boom", text)
        finally:
            tro.closed_changes_by_tier = orig


if __name__ == "__main__":
    unittest.main()
