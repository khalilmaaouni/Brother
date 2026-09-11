#!/usr/bin/env python3
"""D4, 2026-09-10: the verified-at staleness label printed on every surfaced note.

WHY THIS EXISTS. An independent competitive review ranked "a note that is confidently
wrong and still ranks first" as the single most FELT memory gap, ahead of semantic
search. The vault's own frontmatter already carries verified_at (bm_vault_temporal.py's
bi-temporal contract), and bm_vault_staleness.py already classifies it against a
per-type horizon for the authority-demotion seam inside _search -- this unit only makes
that SAME classification visible on the served note itself, at the moment of use,
instead of only felt as a silent rank change after the fact.

THE THRESHOLD is bm_vault_staleness.DEFAULT_HORIZONS, unchanged, not reinvented here:
180 days for a "decision" note (a call that ages fast), 365 for "failure"/"reference"
and the default, session-log exempt. Reusing it rather than picking a new number means
the label can never disagree with what the demotion seam already did to the note's
rank -- a second, independently-chosen threshold would let the label say "fresh" on a
note the seam already demoted as stale, which is worse than no label at all.

A note with NO verified_at is UNKNOWN, never silently fresh or stale: that distinction
(bm_vault_staleness's own "unverified_no_clock" state) is the point of this unit, named
explicitly in the WBS row this closes.

Run: python3 tools/test_bm_vault_staleness_label.py      (unittest output, exit 0 or 1)
"""
import datetime
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "bm_vault.py")
HOOK = os.path.join(HERE, "vault_recall_hook.py")

# E100: one sandbox for every temp tree this process makes, removed at exit, the
# same convention test_bm_vault.py already follows.
sys.path.append(os.path.join(HERE, "../../../scripts"))
try:
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % os.path.basename(__file__))

# bm_vault.py and bm_vault_staleness.py, loaded by path (the same pattern
# test_bm_vault.py already uses for bm_vault itself), so the unit tests below can
# call _verified_label directly rather than only through the CLI.
_bv_spec = importlib.util.spec_from_file_location("bm_vault", TOOL)
bm_vault = importlib.util.module_from_spec(_bv_spec)
_bv_spec.loader.exec_module(bm_vault)

_stale_spec = importlib.util.spec_from_file_location(
    "bm_vault_staleness", os.path.join(HERE, "bm_vault_staleness.py"))
bm_vault_staleness = importlib.util.module_from_spec(_stale_spec)
_stale_spec.loader.exec_module(bm_vault_staleness)

# vault_recall_hook.py, loaded the same way, for test (d): this suite must prove the
# real parser still parses this tool's real output, never assert against a string
# this suite wrote itself.
_vrh_spec = importlib.util.spec_from_file_location("vault_recall_hook_for_D4", HOOK)
vault_recall_hook = importlib.util.module_from_spec(_vrh_spec)
_vrh_spec.loader.exec_module(vault_recall_hook)


def run(argv, env):
    p = subprocess.run([sys.executable, TOOL] + argv, env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return p.returncode, (p.stdout + p.stderr).decode("utf-8", "replace")


TODAY = datetime.date.today()
STALE_DATE = (TODAY - datetime.timedelta(days=400)).isoformat()   # > 180d decision horizon
FRESH_DATE = (TODAY - datetime.timedelta(days=5)).isoformat()     # well inside it

# No "See X.swift"-style citation in any of these three: an unanchored note carries
# nothing for bm_freshness to disprove and is served exactly as always (_print_hits's
# own docstring), which keeps this suite's subject -- the verified-at label -- decoupled
# from the separate freshness-revalidation machinery test_bm_freshness.py already owns.
STALE_NOTE = """---
name: zamboni-glacier-protocol
description: a decision about the ice resurfacer schedule, verified long ago
type: decision
verified_at: %s
---
The zamboni glacier protocol runs the ice resurfacer twice per period, a decision
made long ago and never re-checked since.
""" % STALE_DATE

FRESH_NOTE = """---
name: peppercorn-orbit-manual
description: a decision about the peppercorn orbit manual, verified recently
type: decision
verified_at: %s
---
The peppercorn orbit manual schedules the satellite pass every third orbit, a
decision re-checked a few days ago.
""" % FRESH_DATE

UNKNOWN_NOTE = """---
name: tumbleweed-cactus-ledger
description: a decision about the tumbleweed cactus ledger, never verified at all
type: decision
---
The tumbleweed cactus ledger tracks desert supply counts, a decision nobody has
ever checked against its source.
"""


class VerifiedAtLabelUnit(unittest.TestCase):
    """Unit-level cases against _verified_label directly, calling
    bm_vault_staleness.classify the same way _print_hits does. Fast, and
    exercises exactly the four branches the WBS row names without paying for
    an index build per case."""

    def test_a_a_stale_note_carries_the_stale_indication(self):
        label = bm_vault._verified_label(bm_vault_staleness, STALE_NOTE)
        self.assertIn("STALE", label, label)
        self.assertIn(STALE_DATE, label, label)

    def test_b_a_recently_verified_note_does_not(self):
        label = bm_vault._verified_label(bm_vault_staleness, FRESH_NOTE)
        self.assertNotIn("STALE", label, label)
        self.assertIn(FRESH_DATE, label, label)

    def test_c_a_note_with_no_verified_at_reports_unknown_not_stale_or_fresh(self):
        label = bm_vault._verified_label(bm_vault_staleness, UNKNOWN_NOTE)
        self.assertIn("unknown", label, label)
        self.assertNotIn("STALE", label, label)
        # "fresh" only ever appears here as part of the word "unknown"/"unavailable"
        # never as bm_vault_staleness's own fresh-state wording, which this label
        # only prints as a bare date (see test_b above): assert the actual fresh
        # branch text never leaked in.
        self.assertNotIn("days ago)", label, label)


class VerifiedAtLabelOnRecall(unittest.TestCase):
    """Integration level: the label as bm_vault.py's own `recall` subcommand
    actually prints it, and (d) the same stdout still parses under
    vault_recall_hook.py's real parsing functions, never a string this suite
    wrote by hand."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="bm-vault-staleness-label-")
        cls.vault = os.path.join(cls.tmp, "vault")
        os.makedirs(cls.vault)
        for fn, text in (("stale.md", STALE_NOTE), ("fresh.md", FRESH_NOTE),
                         ("unknown.md", UNKNOWN_NOTE)):
            with open(os.path.join(cls.vault, fn), "w") as f:
                f.write(text)
        cls.env = dict(os.environ)
        cls.env["HOME"] = cls.tmp
        cls.env["BROTHERMODE_ROOT"] = cls.tmp
        os.makedirs(os.path.join(cls.tmp, ".claude"))
        cls.index_code, cls.index_out = run(["index", "--vault", cls.vault], cls.env)
        cls.recall_code, cls.recall_out = run(
            ["recall", "--query",
             "zamboni glacier resurfacer, peppercorn orbit satellite, "
             "tumbleweed cactus desert supply", "--limit", "5"], cls.env)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _block(self, name):
        out = self.recall_out
        start = out.index("\n  %s  [" % name)
        end = out.find("\n\n", start + 1)
        return out[start:end if end != -1 else len(out)]

    def test_01_the_corpus_indexed_and_recalled_clean(self):
        self.assertEqual(self.index_code, 0, self.index_out)
        self.assertEqual(self.recall_code, 0, self.recall_out)

    def test_02_the_stale_note_is_served_with_the_stale_indication(self):
        block = self._block("zamboni-glacier-protocol")
        self.assertIn("verified: %s" % STALE_DATE, block, block)
        self.assertIn("STALE", block, block)
        self.assertNotIn("WITHHELD", block, block)

    def test_03_the_fresh_note_is_served_without_it(self):
        block = self._block("peppercorn-orbit-manual")
        self.assertIn("verified: %s" % FRESH_DATE, block, block)
        self.assertNotIn("STALE", block, block)

    def test_04_the_unverified_note_reports_unknown(self):
        block = self._block("tumbleweed-cactus-ledger")
        self.assertIn("verified: unknown", block, block)
        self.assertNotIn("STALE", block, block)

    def test_05_vault_recall_hook_still_parses_this_output(self):
        """(d): exercise the REAL parser, not a hand-written string. All three
        notes were served (none withheld, asserted above), so the hook's own
        served/withheld counter must agree, and the hook's own title extractor
        must still find all three titles with the new label line present."""
        titles = vault_recall_hook._note_titles(self.recall_out)
        joined = "\n".join(titles)
        self.assertIn("zamboni-glacier-protocol", joined, self.recall_out[:2000])
        self.assertIn("peppercorn-orbit-manual", joined, self.recall_out[:2000])
        self.assertIn("tumbleweed-cactus-ledger", joined, self.recall_out[:2000])
        served, withheld = vault_recall_hook._served_and_withheld_titles(self.recall_out)
        self.assertEqual(withheld, 0, self.recall_out[:2000])
        self.assertEqual(len(served), 3, self.recall_out[:2000])
        # (d) also covers the load-bearing detail vault_recall_hook._block_path
        # depends on: the note's path stays the LAST indented line of its block,
        # i.e. the new "verified: ..." line must sit BEFORE it, never after.
        lines = self.recall_out.split("\n")
        starts = [i for i, ln in enumerate(lines)
                  if vault_recall_hook._NOTE_START_RE.match(ln)]
        for k, idx in enumerate(starts):
            end = starts[k + 1] if k + 1 < len(starts) else len(lines)
            path_line = vault_recall_hook._block_path(lines, idx, end)
            self.assertNotEqual(path_line, "unknown",
                               "block starting %r lost its path line" % lines[idx])


if __name__ == "__main__":
    unittest.main(verbosity=2)
