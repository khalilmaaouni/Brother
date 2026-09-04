#!/usr/bin/env python3
"""Tests for bm_vault_graph, on tiny synthetic fixture vaults.

Run: python3 tools/test_bm_vault_graph.py      (unittest output, exit 0 or 1)
"""
import importlib.util
import json
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

TOOL_DIR = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(TOOL_DIR, "bm_vault_graph.py")

spec = importlib.util.spec_from_file_location("bm_vault_graph", TOOL)
bvg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bvg)


def run(argv):
    p = subprocess.run([sys.executable, TOOL] + argv,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return p.returncode, (p.stdout + p.stderr).decode("utf-8", "replace")


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


# One of each case the tool has to get right: a resolving vault-relative path link
# (Root -> sub/Target), a relative-to-source-dir link (sub/Source -> sub/Sibling, with a
# root-level "Sibling.md" decoy to prove the relative step outranks basename ambiguity),
# a unique-basename link (sub/Target -> Basename), a broken link (Root -> Ghost), two
# orphan notes among others, and one bad status value.
DIRTY = {
    "Root.md": "---\ntype: index\nstatus: open\n---\nSee [[sub/Target]] and [[Ghost]].\n",
    "sub/Target.md": "---\ntype: reference\nstatus: closed\n---\nSee [[Basename]].\n",
    "sub/Source.md": "---\ntype: failure\nstatus: standing\n---\nSee [[Sibling]].\n",
    "sub/Sibling.md": "---\ntype: overview\nstatus: closed\n---\nNo outgoing links here.\n",
    "Sibling.md": "---\ntype: overview\nstatus: closed\n---\nA decoy, same basename.\n",
    "Basename.md": "---\ntype: finding\nstatus: open\n---\nNo outgoing links here.\n",
    "BadStatus.md": "---\ntype: decision\nstatus: mystery-value\n---\nNo links.\n",
}

CLEAN = {
    "A.md": "---\ntype: reference\nstatus: open\n---\nSee [[B]].\n",
    "B.md": "---\ntype: reference\nstatus: closed\n---\nNo links.\n",
}

# Covers the three resolver changes plus the honesty guard, each in its own corner of one
# fixture: proximity resolution (x.md, resolving both the bare and the project-prefixed
# form), ambiguous-still-broken (y.md, two matches sharing one project), a non-md file
# target resolved both by exact path and by relative-to-source-dir (caller.md), and the
# template link-extraction exemption (Session-Template.md's [[SLUG]] must never be seen).
#
# Plus the exempt-population and nearest-ancestor fixes: no-frontmatter.md carries no
# frontmatter block at all, so it must show up in every one of the three missing counts.
# caller.md also links [[Config]], ambiguous between 99-System/Config.md and
# 30-Other/Config.md (deep enough that neither stem equals the link text itself, so
# this exercises basename-ambiguity resolution rather than a direct exact-path match):
# the old top-level-directory fallback ("99-System/") would have resolved it (only
# 99-System/Config.md sits under that prefix), the nearest-ancestor fallback
# ("99-System/Scripts/") has zero matches in scope, so it now stays broken.
MIXED = {
    "10-Projects/brothermode/Sessions/x.md":
        "---\ntype: session-log\nstatus: closed\n---\nSee [[Open-Items]].\n",
    "10-Projects/brothermode/Open-Items.md":
        "---\ntype: overview\nstatus: open\n---\nNo links.\n",
    "10-Projects/atrium-app/Open-Items.md":
        "---\ntype: overview\nstatus: open\n---\nNo links.\n",
    "10-Projects/atrium-app/Sessions/z.md":
        "---\ntype: session-log\nstatus: closed\n---\nSee [[atrium-app/Open-Items]].\n",
    "10-Projects/atrium-app/Sessions/y.md":
        "---\ntype: session-log\nstatus: closed\n---\nSee [[Notes]].\n",
    "10-Projects/atrium-app/Archive/Notes.md":
        "---\ntype: reference\nstatus: open\n---\nNo links.\n",
    "10-Projects/atrium-app/Docs/Notes.md":
        "---\ntype: reference\nstatus: open\n---\nNo links.\n",
    "99-System/Scripts/caller.md":
        "---\ntype: reference\nstatus: open\n---\n"
        "Run [[99-System/Scripts/session-start.sh]] or just [[session-start.sh]]. "
        "Also see [[Config]].\n",
    "99-System/Scripts/session-start.sh": "#!/bin/sh\necho hi\n",
    "99-System/Templates/Session-Template.md":
        "---\ntype: session-log\nstatus: open\n---\nSee [[SLUG]] which must never count.\n",
    "99-System/Config.md":
        "---\ntype: reference\nstatus: open\n---\nNo links.\n",
    "30-Other/Config.md":
        "---\ntype: reference\nstatus: open\n---\nA decoy elsewhere, same basename.\n",
    "99-System/Scripts/no-frontmatter.md":
        "Plain text, no frontmatter block at all. No links.\n",
}


def make_vault(files):
    tmp = tempfile.mkdtemp(prefix="bm-vault-graph-")
    vault = os.path.join(tmp, "vault")
    for rel, text in files.items():
        write(os.path.join(vault, rel), text)
    return tmp, vault


class LinkParsing(unittest.TestCase):
    """Unit level, independent of any fixture on disk: WIKILINK already stops
    before an unescaped "|" (so the alias never reaches _clean_link at all), and
    _clean_link strips #heading and the table backslash artifact on its own."""

    def test_wikilink_stops_before_an_alias(self):
        self.assertEqual(bvg.WIKILINK.findall("[[Note|alias text]]"), ["Note"],
                         "WIKILINK did not stop before |alias")

    def test_clean_link_strips_a_heading_anchor(self):
        self.assertEqual(bvg._clean_link("Note#Heading"), "Note",
                         "_clean_link did not strip #heading")

    def test_clean_link_strips_a_trailing_table_backslash(self):
        self.assertEqual(bvg._clean_link("Note\\"), "Note",
                         "_clean_link did not strip a trailing table backslash")


class DirtyFixture(unittest.TestCase):
    """Exact numbers, checked by hand against the DIRTY fixture."""

    @classmethod
    def setUpClass(cls):
        cls.tmp, cls.vault = make_vault(DIRTY)
        cls.code, cls.out = run(["measure", "--vault", cls.vault, "--json"])

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _stats(self):
        self.assertEqual(self.code, 0,
                         "measure --json exited %d: %s" % (self.code, self.out[:300]))
        return json.loads(self.out)["counts"]

    def test_counts(self):
        stats = self._stats()
        self.assertEqual(stats["note_count"], 7, "note_count %r != 7" % stats["note_count"])
        self.assertEqual(stats["wikilink_count"], 4,
                         "wikilink_count %r != 4" % stats["wikilink_count"])

    def test_the_broken_link_is_named_exactly(self):
        stats = self._stats()
        self.assertTrue(stats["broken_count"] == 1
                        and stats["broken"] == [{"source": "Root.md", "link": "Ghost"}],
                        "broken list wrong: %r" % stats["broken"])

    def test_orphan_count_and_percentage(self):
        stats = self._stats()
        self.assertEqual(stats["orphan_count"], 4,
                         "orphan_count %r != 4 (Root, sub/Source, Sibling, BadStatus)"
                         % stats["orphan_count"])
        self.assertLess(abs(stats["orphan_pct"] - (400.0 / 7)), 0.01,
                        "orphan_pct %r != ~57.14" % stats["orphan_pct"])

    def test_frontmatter_value_histograms(self):
        stats = self._stats()
        self.assertEqual(stats["status_values"],
                         {"open": 2, "closed": 3, "standing": 1, "mystery-value": 1},
                         "status_values wrong: %r" % stats["status_values"])
        self.assertEqual(stats["type_values"],
                         {"index": 1, "reference": 1, "failure": 1,
                          "overview": 2, "finding": 1, "decision": 1},
                         "type_values wrong: %r" % stats["type_values"])

    def test_check_fails_and_names_both_violations(self):
        code, out = run(["check", "--vault", self.vault])
        self.assertEqual(code, 2,
                         "check on dirty fixture exited %d, want 2: %s" % (code, out[:300]))
        self.assertTrue("Ghost" in out and "mystery-value" in out,
                        "check did not name the broken link or the bad status: %s" % out[:400])


class CleanFixture(unittest.TestCase):
    def test_check_passes_on_a_clean_vault(self):
        tmp, vault = make_vault(CLEAN)
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        code, out = run(["check", "--vault", vault])
        self.assertEqual(code, 0,
                         "check on clean fixture exited %d, want 0: %s" % (code, out[:300]))


class MixedFixture(unittest.TestCase):
    """Proximity resolution, ambiguous-still-broken, non-md file targets, and the
    template link-skip exemption."""

    @classmethod
    def setUpClass(cls):
        cls.tmp, cls.vault = make_vault(MIXED)
        cls.code, cls.out = run(["measure", "--vault", cls.vault, "--json"])

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _stats(self):
        self.assertEqual(self.code, 0,
                         "measure --json on mixed fixture exited %d: %s"
                         % (self.code, self.out[:300]))
        return json.loads(self.out)["counts"]

    def test_note_and_template_skip_counts(self):
        stats = self._stats()
        self.assertEqual(stats["note_count"], 12,
                         "mixed note_count %r != 12" % stats["note_count"])
        self.assertEqual(stats["template_skipped_count"], 1,
                         "mixed template_skipped_count %r != 1"
                         % stats["template_skipped_count"])

    def test_ambiguous_links_resolve_by_proximity(self):
        stats = self._stats()
        self.assertEqual(stats["ambiguous_resolved_count"], 2,
                         "mixed ambiguous_resolved_count %r != 2: %r"
                         % (stats["ambiguous_resolved_count"], stats["ambiguous_resolved"]))
        resolved_to = sorted(a["resolved_to"] for a in stats["ambiguous_resolved"])
        want = sorted(["10-Projects/brothermode/Open-Items",
                       "10-Projects/atrium-app/Open-Items"])
        self.assertEqual(resolved_to, want,
                         "mixed ambiguous_resolved targets wrong: %r" % resolved_to)

    def test_ambiguity_that_cannot_resolve_stays_broken(self):
        stats = self._stats()
        want_broken = sorted([
            {"source": "10-Projects/atrium-app/Sessions/y.md", "link": "Notes"},
            {"source": "99-System/Scripts/caller.md", "link": "Config"},
        ], key=lambda d: d["source"])
        self.assertTrue(stats["broken_count"] == 2
                        and sorted(stats["broken"], key=lambda d: d["source"]) == want_broken,
                        "mixed broken list wrong (want the shared-project Notes ambiguity "
                        "plus the nearest-ancestor Config ambiguity, both still broken): %r"
                        % stats["broken"])

    def test_a_template_notes_links_are_never_extracted(self):
        stats = self._stats()
        self.assertEqual(stats["wikilink_count"], 6,
                         "mixed wikilink_count %r != 6 (SLUG in the template must not count)"
                         % stats["wikilink_count"])
        # A bare basename ([[SLUG]]) targeting nothing would normally be broken; proof
        # the template note's link was never even extracted.
        self.assertFalse(any(b["link"] == "SLUG" for b in stats["broken"]),
                         "template's [[SLUG]] leaked into broken links")

    def test_the_exempt_population_is_visible_in_all_three_buckets(self):
        stats = self._stats()
        self.assertEqual((stats["no_frontmatter_count"], stats["missing_status_count"],
                          stats["missing_type_count"]), (1, 1, 1),
                         "mixed missing-frontmatter counts wrong: no_frontmatter=%r "
                         "missing_status=%r missing_type=%r"
                         % (stats["no_frontmatter_count"], stats["missing_status_count"],
                            stats["missing_type_count"]))

    def test_check_discloses_the_counts_and_gates_on_missing_frontmatter(self):
        # Finding 5: missing status/type is a named violation, exit 2, not merely reported.
        code, out = run(["check", "--vault", self.vault])
        self.assertIn("template notes skipped for links: 1", out,
                      "check did not disclose the template-skip count: %s" % out[:400])
        self.assertIn("missing frontmatter: 1 no-block, 1 missing status:, 1 missing type:",
                      out,
                      "check did not disclose the missing-frontmatter counts: %s" % out[:400])
        self.assertNotIn("missing-frontmatter counts are reported, not yet gated", out,
                         "check still carries the old not-yet-gated line: %s" % out[:400])
        self.assertEqual(code, 2,
                         "check on mixed fixture (has a no-frontmatter note) exited %d, "
                         "want 2: %s" % (code, out[:300]))
        no_fm = "99-System/Scripts/no-frontmatter.md"
        for want in ("no frontmatter block: %s" % no_fm, "missing status: %s" % no_fm,
                     "missing type: %s" % no_fm):
            self.assertIn(want, out,
                          "check did not name the missing-frontmatter violation: %s" % want)


class GateFlip(unittest.TestCase):
    def test_a_frontmatter_block_with_no_status_line_alone_gates_check(self):
        tmp, vault = make_vault({
            "OnlyNote.md": "---\ntype: reference\n---\nFrontmatter block, no status field.\n",
        })
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        code, out = run(["check", "--vault", vault])
        self.assertEqual(code, 2,
                         "gate-flip: missing status: alone exited %d, want 2: %s"
                         % (code, out[:300]))
        self.assertIn("missing status: OnlyNote.md", out,
                      "gate-flip: check did not name the missing-status violation: %s"
                      % out[:300])


class StructuralOrphans(unittest.TestCase):
    """A generated catalog note links to two otherwise-unlinked notes. Raw
    orphan_count sees them as linked (1 orphan: the catalog itself); the
    structural metric ignores links whose source is generated, so both real
    notes come back as orphans too (3 of 3)."""

    @classmethod
    def setUpClass(cls):
        cls.tmp, cls.vault = make_vault({
            "Real.md": "---\ntype: reference\nstatus: open\n---\nNo links.\n",
            "Orphan.md": "---\ntype: reference\nstatus: open\n---\nNo links.\n",
            "Catalog.md": "---\ntype: index\nstatus: standing\n"
                          "tags: [catalog, generated]\n---\nSee [[Orphan]] and [[Real]].\n",
        })

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_the_two_metrics_disagree_on_purpose(self):
        code, out = run(["measure", "--vault", self.vault, "--json"])
        self.assertEqual(code, 0,
                         "measure --json on structural fixture exited %d: %s"
                         % (code, out[:300]))
        stats = json.loads(out)["counts"]
        self.assertEqual(stats["orphan_count"], 1,
                         "structural fixture orphan_count %r != 1 (only Catalog.md, the "
                         "other two are linked from it)" % stats["orphan_count"])
        self.assertEqual(stats["structural_orphan_count"], 3,
                         "structural fixture structural_orphan_count %r != 3 (links from "
                         "a generated note must not count)" % stats["structural_orphan_count"])
        self.assertLess(abs(stats["structural_orphan_pct"] - 100.0), 0.01,
                        "structural fixture structural_orphan_pct %r != 100.0"
                        % stats["structural_orphan_pct"])

    def test_the_human_summary_prints_the_structural_line(self):
        code, out = run(["measure", "--vault", self.vault])
        self.assertIn("structural orphans (ignoring generated catalogs): 3 (100.00%)", out,
                      "measure did not print the structural-orphans line: %s" % out[:400])


class EmptyVault(unittest.TestCase):
    def test_an_empty_vault_is_no_data_never_a_silent_pass(self):
        empty_tmp = tempfile.mkdtemp(prefix="bm-vault-graph-empty-")
        self.addCleanup(shutil.rmtree, empty_tmp, ignore_errors=True)
        code, out = run(["check", "--vault", empty_tmp])
        self.assertTrue(code == 3 and "NO-DATA" in out,
                        "empty vault did not report NO-DATA/exit 3: code=%d out=%s"
                        % (code, out[:200]))


class StagedFileScope(unittest.TestCase):
    """The --paths gate: a vault with one good note and one unrelated bad note.
    A scoped check on the good note alone must pass even though the bad note
    sits untouched in the same tree (the hostage fix), a scoped check on the bad
    note alone must still fail and name it, and the whole-vault check must still
    fail, proving --paths is additive rather than a new default."""

    @classmethod
    def setUpClass(cls):
        cls.tmp, cls.vault = make_vault({
            "Good.md": "---\ntype: reference\nstatus: open\n---\nNo links.\n",
            "Bad.md": "---\ntype: decision\nstatus: executed\n---\nNo links.\n",
        })

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_a_scoped_check_does_not_take_the_rest_of_the_vault_hostage(self):
        code, out = run(["check", "--vault", self.vault, "--paths", "Good.md"])
        self.assertEqual(code, 0,
                         "scoped check on Good.md alone exited %d, want 0 (hostage fix "
                         "broken): %s" % (code, out[:300]))

    def test_a_scoped_check_still_fails_on_the_note_it_names(self):
        code, out = run(["check", "--vault", self.vault, "--paths", "Bad.md"])
        self.assertTrue(code == 2 and "bad status value 'executed': Bad.md" in out,
                        "scoped check on Bad.md alone exited %d or did not name it: %s"
                        % (code, out[:300]))

    def test_the_whole_vault_check_is_unchanged_by_the_flag_existing(self):
        code, out = run(["check", "--vault", self.vault])
        self.assertEqual(code, 2,
                         "whole-vault check (no --paths) exited %d, want 2 (Bad.md still "
                         "dirty): %s" % (code, out[:300]))


class TypedEdges(unittest.TestCase):
    """WBS 16: supersedes: (directed) and relates: (symmetric), both frontmatter
    fields, resolved the same way a plain [[wikilink]] already is. New.md supersedes
    Old.md and relates to Sibling.md; Sibling.md declares the SAME relates: edge from
    its own side, on purpose, to prove a symmetric edge declared from either end reads
    identically rather than needing to be declared twice. Ghost.md is named only by a
    broken supersedes: target, never created, so the honest-broken-edge path has a real
    case to catch. Untouched.md carries neither field, the empty-value case every real
    note in the vault has today (supersedes: with nothing after it)."""

    @classmethod
    def setUpClass(cls):
        cls.tmp, cls.vault = make_vault({
            "New.md": ("---\ntype: reference\nstatus: open\n"
                       "supersedes: [[Old]]\nrelates: [[Sibling]]\n---\n"
                       "Text with no bearing on the edges above.\n"),
            "Old.md": "---\ntype: reference\nstatus: open\n---\nNo links.\n",
            "Sibling.md": ("---\ntype: reference\nstatus: open\n"
                           "relates: [[New]]\n---\nNo links.\n"),
            "Untouched.md": "---\ntype: reference\nstatus: open\nsupersedes:\n---\nNo links.\n",
            "BrokenSupersede.md": ("---\ntype: reference\nstatus: open\n"
                                   "supersedes: [[Ghost]]\n---\nNo links.\n"),
        })

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_supersedes_is_directed_and_the_reverse_index_is_built_from_it(self):
        code, out = run(["edges", "--vault", self.vault, "--note", "New", "--json"])
        self.assertEqual(code, 0, out)
        data = json.loads(out)
        self.assertEqual(data["supersedes"], ["Old"], out)
        code, out = run(["edges", "--vault", self.vault, "--note", "Old", "--json"])
        data = json.loads(out)
        self.assertEqual(data["superseded_by"], ["New"], out)
        self.assertEqual(data["supersedes"], [], "Old must not supersede anything: %s" % out)

    def test_relates_declared_from_either_side_reads_identically_on_both(self):
        code, out = run(["edges", "--vault", self.vault, "--note", "New", "--json"])
        data = json.loads(out)
        self.assertEqual(data["relates"], ["Sibling"], out)
        code, out = run(["edges", "--vault", self.vault, "--note", "Sibling", "--json"])
        data = json.loads(out)
        self.assertEqual(data["relates"], ["New"], out)

    def test_an_empty_supersedes_value_is_zero_edges_not_a_crash(self):
        code, out = run(["edges", "--vault", self.vault, "--note", "Untouched", "--json"])
        self.assertEqual(code, 0, out)
        data = json.loads(out)
        self.assertEqual(data["supersedes"], [], out)
        self.assertEqual(data["relates"], [], out)

    def test_a_supersedes_target_that_does_not_exist_is_named_broken_not_silent(self):
        code, out = run(["measure", "--vault", self.vault, "--json"])
        self.assertEqual(code, 0, out)
        stats = json.loads(out)["counts"]
        broken = stats["typed_broken"]
        self.assertEqual(len(broken), 1, out)
        self.assertEqual(broken[0]["field"], "supersedes", out)
        self.assertEqual(broken[0]["link"], "Ghost", out)
        self.assertEqual(broken[0]["source"], "BrokenSupersede", out)

    def test_check_fails_the_gate_on_a_broken_typed_edge(self):
        code, out = run(["check", "--vault", self.vault])
        self.assertEqual(code, 2, out)
        self.assertIn("broken supersedes: BrokenSupersede -> [[Ghost]]", out, out)

    def test_measure_prints_typed_edge_counts_in_the_summary(self):
        code, out = run(["measure", "--vault", self.vault])
        self.assertEqual(code, 0, out)
        self.assertIn("typed edges: 1 supersedes, 1 relates, 0 contradicts, 1 broken", out, out)


class ContradictsEdges(unittest.TestCase):
    """D10 (vault benchmark v2): contradicts: as a symmetric edge, resolved the same
    way supersedes:/relates: already are. A.md declares contradicts: [[B]]; B.md
    declares nothing back, proving the reverse direction is still visible (symmetric,
    like relates:, not one-sided). C.md names a target that does not exist, so the
    dangling-contradicts case has a real fixture to catch, calibrated the same way
    a broken supersedes: target already is."""

    @classmethod
    def setUpClass(cls):
        cls.tmp, cls.vault = make_vault({
            "A.md": ("---\ntype: reference\nstatus: open\n"
                     "contradicts: [[B]]\n---\nNo bearing text.\n"),
            "B.md": "---\ntype: reference\nstatus: open\n---\nNo links.\n",
            "C.md": ("---\ntype: reference\nstatus: open\n"
                     "contradicts: [[Ghost]]\n---\nNo links.\n"),
        })

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_contradicts_is_symmetric_even_when_declared_from_one_side(self):
        code, out = run(["edges", "--vault", self.vault, "--note", "A", "--json"])
        self.assertEqual(code, 0, out)
        data = json.loads(out)
        self.assertEqual(data["contradicts"], ["B"], out)
        code, out = run(["edges", "--vault", self.vault, "--note", "B", "--json"])
        self.assertEqual(code, 0, out)
        data = json.loads(out)
        self.assertEqual(data["contradicts"], ["A"], out)

    def test_a_dangling_contradicts_target_is_named_broken_not_silent(self):
        code, out = run(["measure", "--vault", self.vault, "--json"])
        self.assertEqual(code, 0, out)
        stats = json.loads(out)["counts"]
        con_broken = [b for b in stats["typed_broken"] if b["field"] == "contradicts"]
        self.assertEqual(len(con_broken), 1, out)
        self.assertEqual(con_broken[0]["link"], "Ghost", out)
        self.assertEqual(con_broken[0]["source"], "C", out)

    def test_check_fails_the_gate_on_a_broken_contradicts_edge(self):
        code, out = run(["check", "--vault", self.vault])
        self.assertEqual(code, 2, out)
        self.assertIn("broken contradicts: C -> [[Ghost]]", out, out)

    def test_measure_prints_the_contradicts_edge_count_in_the_summary(self):
        code, out = run(["measure", "--vault", self.vault])
        self.assertEqual(code, 0, out)
        self.assertIn("typed edges: 0 supersedes, 0 relates, 1 contradicts, 1 broken", out, out)


class RotScan(unittest.TestCase):
    """VB4-07: zero-byte notes, whitespace-only notes (blank after frontmatter), and
    non-md attachments no note references by name, report-only. Anchor.md references
    referenced.png by name so it must never show up as an orphan attachment, proving
    the scan is not just "every non-md file"."""

    @classmethod
    def setUpClass(cls):
        cls.tmp, cls.vault = make_vault({
            "Empty.md": "",
            "Whitespace.md": "---\ntype: reference\nstatus: open\n---\n   \n\t \n",
            "Anchor.md": "---\ntype: reference\nstatus: open\n---\n"
                        "See referenced.png for details.\n",
            "referenced.png": "fake-binary-content\n",
            "orphan.png": "fake-binary-content\n",
        })
        cls.code, cls.out = run(["measure", "--vault", cls.vault, "--json"])

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _stats(self):
        self.assertEqual(self.code, 0,
                         "measure --json on rot fixture exited %d: %s"
                         % (self.code, self.out[:300]))
        return json.loads(self.out)["counts"]

    def test_a_zero_byte_note_is_found(self):
        stats = self._stats()
        self.assertIn("Empty.md", stats["empty_notes"],
                      "zero-byte note not found: %r" % stats["empty_notes"])

    def test_a_whitespace_only_note_is_found(self):
        stats = self._stats()
        self.assertIn("Whitespace.md", stats["whitespace_only_notes"],
                      "whitespace-only note not found: %r" % stats["whitespace_only_notes"])

    def test_an_orphan_attachment_is_found(self):
        stats = self._stats()
        self.assertIn("orphan.png", stats["orphan_attachments"],
                      "orphan attachment not found: %r" % stats["orphan_attachments"])

    def test_a_referenced_attachment_is_never_flagged(self):
        stats = self._stats()
        self.assertNotIn("referenced.png", stats["orphan_attachments"],
                         "referenced attachment wrongly flagged as orphan: %r"
                         % stats["orphan_attachments"])

    def test_the_human_summary_names_all_three(self):
        code, out = run(["measure", "--vault", self.vault])
        self.assertEqual(code, 0, out)
        self.assertIn("empty note: Empty.md", out, out)
        self.assertIn("whitespace-only note: Whitespace.md", out, out)
        self.assertIn("unreferenced attachment: orphan.png", out, out)


class RotScanExcludesTelemetry(unittest.TestCase):
    """VB4-07: 99-System/telemetry/ is machine-written housekeeping data, never
    vault content a human links to, so an unreferenced file under it must
    never be flagged as an orphan attachment. This is the one test that
    actually fails if TELEMETRY_PREFIX's exclusion line is ever deleted;
    without it every telemetry file would show up as rot noise."""

    @classmethod
    def setUpClass(cls):
        cls.tmp, cls.vault = make_vault({
            "Anchor.md": "---\ntype: reference\nstatus: open\n---\nJust an anchor note.\n",
            "99-System/telemetry/run-2026-08-30.json": "{}\n",
        })
        cls.code, cls.out = run(["measure", "--vault", cls.vault, "--json"])

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_unreferenced_telemetry_file_is_never_flagged(self):
        self.assertEqual(self.code, 0, self.out)
        stats = json.loads(self.out)["counts"]
        self.assertNotIn("99-System/telemetry/run-2026-08-30.json",
                         stats["orphan_attachments"],
                         "telemetry file wrongly flagged as orphan: %r"
                         % stats["orphan_attachments"])


class RotScanNeverDeletes(unittest.TestCase):
    """The estate law binds: detection and report only. A mechanical guard, not just
    a stated intent, that the rot-scan code never grows an unlink/remove/rmtree call."""

    def test_the_module_never_calls_a_delete_primitive(self):
        with open(TOOL, encoding="utf-8") as f:
            src = f.read()
        forbidden = ("os.unlink(", "os.remove(", "shutil.rmtree(", ".unlink(")
        hits = [tok for tok in forbidden if tok in src]
        self.assertEqual(hits, [],
                         "bm_vault_graph.py must never call a delete primitive "
                         "(report-only, estate law): found %r" % hits)


class CleanVaultRotSection(unittest.TestCase):
    def test_zero_rot_prints_a_clean_line_never_silence(self):
        tmp, vault = make_vault(CLEAN)
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        code, out = run(["measure", "--vault", vault])
        self.assertEqual(code, 0, out)
        self.assertIn("rot: 0 empty note(s), 0 whitespace-only note(s), "
                      "0 unreferenced attachment(s)", out, out)
        self.assertIn("clean: no rot found", out, out)


class JsonOutput(unittest.TestCase):
    """VB7-02: --json on measure and check. Prose stays byte-identical when
    --json is absent (proven by every prose-mode test above still passing
    unchanged); this class covers what --json adds: valid JSON, a verdict
    matching the exit code, and counts that agree with the prose numbers."""

    @classmethod
    def setUpClass(cls):
        cls.clean_tmp, cls.clean_vault = make_vault(CLEAN)
        cls.dirty_tmp, cls.dirty_vault = make_vault(DIRTY)
        cls.empty_tmp = tempfile.mkdtemp(prefix="bm-vault-graph-empty-json-")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.clean_tmp, ignore_errors=True)
        shutil.rmtree(cls.dirty_tmp, ignore_errors=True)
        shutil.rmtree(cls.empty_tmp, ignore_errors=True)

    def test_measure_pass_json_matches_prose_note_count(self):
        pcode, pout = run(["measure", "--vault", self.clean_vault])
        self.assertEqual(pcode, 0, pout)
        jcode, jout = run(["measure", "--vault", self.clean_vault, "--json"])
        self.assertEqual(jcode, 0, jout)
        data = json.loads(jout)
        self.assertEqual(data["verdict"], "PASS", jout)
        self.assertIn("notes: %d" % data["counts"]["note_count"], pout,
                      "json note_count %r did not match prose" % data["counts"]["note_count"])

    def test_measure_no_data_json_matches_exit_code(self):
        code, out = run(["measure", "--vault", self.empty_tmp, "--json"])
        self.assertEqual(code, 3, out)
        data = json.loads(out)
        self.assertEqual(data["verdict"], "NO-DATA", out)

    def test_check_pass_json_matches_prose(self):
        pcode, pout = run(["check", "--vault", self.clean_vault])
        self.assertEqual(pcode, 0, pout)
        jcode, jout = run(["check", "--vault", self.clean_vault, "--json"])
        self.assertEqual(jcode, 0, jout)
        data = json.loads(jout)
        self.assertEqual(data["verdict"], "PASS", jout)
        self.assertEqual(data["counts"]["violation_count"], 0, jout)
        self.assertEqual(data["findings"], [], jout)

    def test_check_fail_json_matches_prose_violation_count(self):
        pcode, pout = run(["check", "--vault", self.dirty_vault])
        self.assertEqual(pcode, 2, pout)
        want = int(re.search(r"(\d+) violation\(s\)", pout).group(1))
        jcode, jout = run(["check", "--vault", self.dirty_vault, "--json"])
        self.assertEqual(jcode, 2, jout)
        data = json.loads(jout)
        self.assertEqual(data["verdict"], "FAIL", jout)
        self.assertEqual(data["counts"]["violation_count"], want, jout)
        self.assertEqual(len(data["findings"]), want, jout)

    def test_check_no_data_json_matches_exit_code(self):
        code, out = run(["check", "--vault", self.empty_tmp, "--json"])
        self.assertEqual(code, 3, out)
        data = json.loads(out)
        self.assertEqual(data["verdict"], "NO-DATA", out)


class PatternType(unittest.TestCase):
    """pattern_note.py writes type: pattern (see scripts/pattern_note.py note_body):
    a real pattern note must pass the gate, and a misspelt type value must still
    fail it, the same way StagedFileScope proves a bad status value fails."""

    @classmethod
    def setUpClass(cls):
        cls.tmp, cls.vault = make_vault({
            "Good.md": "---\ntype: pattern\nstatus: standing\n---\nNo links.\n",
            "Bad.md": "---\ntype: patern\nstatus: standing\n---\nNo links.\n",
        })

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_type_pattern_passes_the_scoped_gate(self):
        code, out = run(["check", "--vault", self.vault, "--paths", "Good.md"])
        self.assertEqual(code, 0,
                         "type: pattern failed the gate: %s" % out[:300])

    def test_misspelt_type_still_fails_the_gate(self):
        code, out = run(["check", "--vault", self.vault, "--paths", "Bad.md"])
        self.assertTrue(code == 2 and "bad type value 'patern': Bad.md" in out,
                        "misspelt type did not fail as expected: code=%d out=%s"
                        % (code, out[:300]))


if __name__ == "__main__":
    unittest.main(verbosity=1)
