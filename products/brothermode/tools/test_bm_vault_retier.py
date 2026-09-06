#!/usr/bin/env python3
"""Calibration for bm_vault_retier.py, the one-time re-tag tool for legacy
vault notes under the strict-withhold flip (row P0.1, 2026-09-06).

Six fixture notes in a temp vault, against a temp "repository" directory
that stands in for --repo-root: with-evidence (a resolving path: field),
without-evidence (no evidence-shaped field at all), dangling-evidence (an
evidence field naming a file that does not exist), already-tiered (status:
already declared, left untouched), malformed (opens with --- and never
closes), no-frontmatter (does not open with --- at all). Plus idempotence
(a second real run over the same vault changes 0 notes) and dry-run (writes
nothing, reports the same counts a real run would).

No em or en dashes anywhere in this file.
"""
import datetime
import importlib.util
import io
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL_PATH = os.path.join(HERE, "bm_vault_retier.py")

sys.path.append(os.path.join(HERE, "../../../scripts"))
try:  # noqa: E402
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % os.path.basename(__file__))


def load_tool():
    """A fresh import of bm_vault_retier.py by path, the same pattern every
    sibling test in this directory uses to load a same-folder module
    (test_bm_vault_evidence_tier.py's own load_hook())."""
    spec = importlib.util.spec_from_file_location("bm_vault_retier_under_test", TOOL_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


NOTE = """---
name: %s
description: %s
type: project
%s---

%s
"""


class RetierFixtureVault(unittest.TestCase):
    def setUp(self):
        self.tool = load_tool()
        self.repo = tempfile.mkdtemp()
        self.vault = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.repo, ignore_errors=True)
        self.addCleanup(shutil.rmtree, self.vault, ignore_errors=True)
        # The file a with-evidence note's own path: field points at,
        # resolving under --repo-root (self.repo), never under the vault.
        with open(os.path.join(self.repo, "evidence_target.py"), "w", encoding="utf-8") as fh:
            fh.write("X = 1\n")

    def _write(self, stem, extra_frontmatter="", body="fixture lesson body, never advice this test wants followed"):
        path = os.path.join(self.vault, stem + ".md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(NOTE % (stem, stem, extra_frontmatter, body))
        return path

    def _seed_six(self):
        with_evidence = self._write(
            "with-evidence", "path: evidence_target.py\n")
        without_evidence = self._write("without-evidence")
        dangling_evidence = self._write(
            "dangling-evidence", "source: does-not-exist.py\n")
        already_tiered = self._write(
            "already-tiered", "status: verified\n")
        malformed_path = os.path.join(self.vault, "malformed.md")
        with open(malformed_path, "w", encoding="utf-8") as fh:
            fh.write("---\nname: broken\nno closing fence here\n")
        no_frontmatter_path = os.path.join(self.vault, "no-frontmatter.md")
        with open(no_frontmatter_path, "w", encoding="utf-8") as fh:
            fh.write("Just prose, never fenced frontmatter at all.\n")
        return {
            "with_evidence": with_evidence,
            "without_evidence": without_evidence,
            "dangling_evidence": dangling_evidence,
            "already_tiered": already_tiered,
            "malformed": malformed_path,
            "no_frontmatter": no_frontmatter_path,
        }

    def _read(self, path):
        with open(path, encoding="utf-8") as fh:
            return fh.read()

    # A note with a path: field that resolves under --repo-root gets
    # evidence_locator: path:<relpath> plus the two status lines.
    def test_with_evidence_note_gets_a_resolving_locator(self):
        paths = self._seed_six()
        plan = self.tool.plan_vault(self.vault, self.repo)
        self.tool.apply_plan(plan)
        text = self._read(paths["with_evidence"])
        self.assertIn("evidence_locator: path:evidence_target.py", text)
        self.assertIn("status: legacy-untiered", text)
        self.assertIn("retiered_at: %s" % datetime.date.today().isoformat(), text)

    # A note with no evidence-shaped field at all gets only the two status
    # lines, never a fabricated evidence_locator.
    def test_without_evidence_note_gets_status_only(self):
        paths = self._seed_six()
        plan = self.tool.plan_vault(self.vault, self.repo)
        self.tool.apply_plan(plan)
        text = self._read(paths["without_evidence"])
        self.assertNotIn("evidence_locator", text)
        self.assertIn("status: legacy-untiered", text)

    # A note whose evidence field names a file that does not exist under
    # --repo-root is treated exactly like no evidence at all: status-only,
    # never a locator this tool cannot vouch for.
    def test_dangling_evidence_note_gets_status_only(self):
        paths = self._seed_six()
        plan = self.tool.plan_vault(self.vault, self.repo)
        self.tool.apply_plan(plan)
        text = self._read(paths["dangling_evidence"])
        self.assertNotIn("evidence_locator", text)
        self.assertIn("status: legacy-untiered", text)

    # A note that already declares status: is never touched: no
    # retiered_at, no evidence_locator, byte-identical to what was written.
    def test_already_tiered_note_is_left_untouched(self):
        paths = self._seed_six()
        before = self._read(paths["already_tiered"])
        plan = self.tool.plan_vault(self.vault, self.repo)
        self.tool.apply_plan(plan)
        after = self._read(paths["already_tiered"])
        self.assertEqual(before, after)
        self.assertNotIn("retiered_at", after)

    # A file that opens with --- but never closes is refused with a named
    # reason, and left byte-identical on disk.
    def test_malformed_frontmatter_is_refused_with_a_named_reason(self):
        paths = self._seed_six()
        before = self._read(paths["malformed"])
        plan = self.tool.plan_vault(self.vault, self.repo)
        reasons = dict(plan["refused"])
        self.assertIn(paths["malformed"], reasons)
        self.assertIn("malformed frontmatter", reasons[paths["malformed"]])
        self.tool.apply_plan(plan)
        self.assertEqual(before, self._read(paths["malformed"]))

    # A file with no frontmatter block at all is refused with its own
    # distinct named reason, never guessed into a new fence.
    def test_no_frontmatter_is_refused_with_a_named_reason(self):
        paths = self._seed_six()
        before = self._read(paths["no_frontmatter"])
        plan = self.tool.plan_vault(self.vault, self.repo)
        reasons = dict(plan["refused"])
        self.assertIn(paths["no_frontmatter"], reasons)
        self.assertIn("no frontmatter block", reasons[paths["no_frontmatter"]])
        self.tool.apply_plan(plan)
        self.assertEqual(before, self._read(paths["no_frontmatter"]))

    # dry-run: the report prints the same counts a real run would, and
    # writes nothing at all, to any of the six fixtures.
    def test_dry_run_writes_nothing(self):
        paths = self._seed_six()
        before = {name: self._read(p) for name, p in paths.items()}
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = self.tool.main(["--dry-run", "--repo-root", self.repo, self.vault])
        self.assertEqual(rc, 0)
        out = buf.getvalue()
        self.assertIn("3 notes would change (1 evidenced, 2 status-only)", out)
        for name, p in paths.items():
            self.assertEqual(before[name], self._read(p), name)

    # Idempotence: a real run followed by a second real run over the same
    # vault changes 0 notes on the second pass, with the required line.
    def test_second_real_run_changes_nothing(self):
        self._seed_six()
        buf1 = io.StringIO()
        with redirect_stdout(buf1):
            rc1 = self.tool.main(["--repo-root", self.repo, self.vault])
        self.assertEqual(rc1, 0)
        self.assertIn("3 notes changed (1 evidenced, 2 status-only)", buf1.getvalue())

        buf2 = io.StringIO()
        with redirect_stdout(buf2):
            rc2 = self.tool.main(["--repo-root", self.repo, self.vault])
        self.assertEqual(rc2, 0)
        self.assertIn("0 notes changed", buf2.getvalue())


if __name__ == "__main__":
    unittest.main()
