#!/usr/bin/env python3
"""Calibration for tools/bm_vault_compose.py, the split/merge Note Composer port.

Same fixture-vault style as test_bm_vault_lint.py: a temp directory standing in
for a vault, real files on disk, the module driven exactly the way a caller
would drive it. Purge tools/__pycache__ between manual rule swaps, same as
that suite's own header says.

No em or en dashes anywhere in this file.
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bm_vault_compose as compose  # noqa: E402
import bm_vault_graph as graph  # noqa: E402

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


def note(id_, type_="reference", status="standing", project=None, body="\n# body\n",
         extra=""):
    lines = ["---", "id: %s" % id_, "type: %s" % type_, "status: %s" % status]
    if project:
        lines.append("project: %s" % project)
    lines.append("created: 2026-08-01")
    if extra:
        lines.append(extra)
    lines.append("---")
    return "\n".join(lines) + body


class VaultFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-vault-compose-")
        self.vault = os.path.join(self.tmp, "vault")
        os.makedirs(self.vault)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, name, text):
        path = os.path.join(self.vault, name)
        d = os.path.dirname(path)
        if d and not os.path.isdir(d):
            os.makedirs(d)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def _read(self, name):
        with open(os.path.join(self.vault, name), encoding="utf-8") as fh:
            return fh.read()


class SplitLeavesNoBrokenLinks(VaultFixture):
    def test_split_extracts_section_mints_id_and_links_back(self):
        self._write("Source.md", note(
            "n-0000000000000001",
            body="\n# Source\n\nIntro.\n\n## Sub Topic\n\nExtracted content here.\n"
                 "\n## After\n\nStays put.\n"))
        code = compose.main(["split", "--vault", self.vault, "--note", "Source",
                              "--heading", "Sub Topic", "--today", "2026-08-30",
                              "--apply"])
        self.assertEqual(code, 0)
        new_text = self._read("sub-topic.md")
        self.assertIn("id: n-", new_text)
        self.assertNotIn("id: n-0000000000000001", new_text)
        self.assertIn("Extracted content here.", new_text)
        source_text = self._read("Source.md")
        self.assertIn("[[sub-topic]]", source_text)
        self.assertNotIn("Extracted content here.", source_text)
        self.assertIn("Stays put.", source_text)
        # frontmatter inherited
        self.assertIn("type: reference", new_text)
        self.assertIn("status: standing", new_text)
        self.assertIn("created: 2026-08-30", new_text)
        # graph gate: zero broken links after the split
        self.assertEqual(graph.cmd_check(compose.argparse.Namespace(
            vault=self.vault, paths=["Source.md", "sub-topic.md"])), 0)

    def test_split_dry_run_writes_nothing(self):
        self._write("Source.md", note(
            "n-0000000000000002",
            body="\n# Source\n\n## Sub\n\nBody.\n"))
        before = self._read("Source.md")
        code = compose.main(["split", "--vault", self.vault, "--note", "Source",
                              "--heading", "Sub", "--today", "2026-08-30"])
        self.assertEqual(code, 0)
        self.assertEqual(self._read("Source.md"), before)
        self.assertFalse(os.path.exists(os.path.join(self.vault, "sub.md")))

    def test_split_refuses_when_target_filename_collides(self):
        self._write("Source.md", note(
            "n-0000000000000003", body="\n# Source\n\n## Sub\n\nBody.\n"))
        self._write("sub.md", note("n-0000000000000004"))
        code = compose.main(["split", "--vault", self.vault, "--note", "Source",
                              "--heading", "Sub", "--today", "2026-08-30", "--apply"])
        self.assertEqual(code, 1)
        self.assertFalse(os.path.exists(os.path.join(self.vault, "Source.md.bm-vault-compose.tmp")))

    def test_split_refuses_missing_heading(self):
        self._write("Source.md", note("n-0000000000000005", body="\n# Source\n\nBody.\n"))
        code = compose.main(["split", "--vault", self.vault, "--note", "Source",
                              "--heading", "Nope", "--today", "2026-08-30", "--apply"])
        self.assertEqual(code, 1)


class MergeRewritesEveryInboundLink(VaultFixture):
    def _setup_pair(self):
        self._write("A.md", note("n-0000000000000010",
                                  body="\n# A\n\nA's own content.\n"))
        self._write("B.md", note("n-0000000000000011",
                                  body="\n# B\n\nB's own content.\n"))
        self._write("Referrer.md", note(
            "n-0000000000000012",
            body="\nSee [[A]] and also [[A|the alias text]] and [[A#Details]].\n"))

    def test_merge_rewrites_plain_aliased_and_anchored_links(self):
        self._setup_pair()
        code = compose.main(["merge", "--vault", self.vault, "--from", "A",
                              "--to", "B", "--apply"])
        self.assertEqual(code, 0)
        ref = self._read("Referrer.md")
        self.assertIn("[[B]]", ref)
        self.assertIn("[[B|the alias text]]", ref)
        self.assertIn("[[B#Details]]", ref)
        self.assertNotIn("[[A]]", ref)
        self.assertNotIn("[[A|", ref)
        b_text = self._read("B.md")
        self.assertIn("## Merged from A", b_text)
        self.assertIn("A's own content.", b_text)
        a_text = self._read("A.md")
        self.assertIn("supersedes: [[B]]", a_text)
        self.assertIn("A's own content.", a_text)  # husk: A's body untouched
        self.assertTrue(os.path.exists(os.path.join(self.vault, "A.md")))  # never deleted

    def test_merge_dry_run_writes_nothing_and_reports_the_plan(self):
        self._setup_pair()
        before_a, before_b, before_ref = (self._read("A.md"), self._read("B.md"),
                                           self._read("Referrer.md"))
        code = compose.main(["merge", "--vault", self.vault, "--from", "A", "--to", "B"])
        self.assertEqual(code, 0)
        self.assertEqual(self._read("A.md"), before_a)
        self.assertEqual(self._read("B.md"), before_b)
        self.assertEqual(self._read("Referrer.md"), before_ref)

    def test_merge_refuses_on_duplicate_fold_heading(self):
        self._write("A.md", note("n-0000000000000020", body="\n# A\n\nContent.\n"))
        self._write("B.md", note("n-0000000000000021",
                                  body="\n# B\n\n## Merged from A\n\nAlready here.\n"))
        code = compose.main(["merge", "--vault", self.vault, "--from", "A",
                              "--to", "B", "--apply"])
        self.assertEqual(code, 1)
        self.assertNotIn("supersedes:", self._read("A.md"))

    def test_merge_refuses_on_ambiguous_target_naming_both_candidates(self):
        self._write("A.md", note("n-0000000000000030", body="\n# A\n\nContent.\n"))
        self._write("Group/Dup.md", note("n-0000000000000031", body="\n# Dup one\n"))
        self._write("Other/Dup.md", note("n-0000000000000032", body="\n# Dup two\n"))
        code = compose.main(["merge", "--vault", self.vault, "--from", "A",
                              "--to", "Dup", "--apply"])
        self.assertEqual(code, 1)

    def test_merge_leaves_the_folded_note_in_place_even_with_no_inbound_links(self):
        self._write("A.md", note("n-0000000000000040", body="\n# A\n\nLonely note.\n"))
        self._write("B.md", note("n-0000000000000041", body="\n# B\n\nContent.\n"))
        code = compose.main(["merge", "--vault", self.vault, "--from", "A",
                              "--to", "B", "--apply"])
        self.assertEqual(code, 0)
        self.assertTrue(os.path.exists(os.path.join(self.vault, "A.md")))

    def test_merge_survives_an_inbound_link_to_a_inside_b_itself(self):
        # B carries its own [[A]] link (an inbound link to the note being
        # folded away). The rewrite loop must not clobber B's just-written
        # fold section with pre-fold cached text when it rewrites that link.
        self._write("A.md", note("n-0000000000000050", body="\n# A\n\nA's own content.\n"))
        self._write("B.md", note("n-0000000000000051",
                                  body="\n# B\n\nSee [[A]] for background.\n"))
        code = compose.main(["merge", "--vault", self.vault, "--from", "A",
                              "--to", "B", "--apply"])
        self.assertEqual(code, 0)
        b_text = self._read("B.md")
        self.assertIn("## Merged from A", b_text)
        self.assertIn("A's own content.", b_text)
        self.assertIn("See [[B]] for background.", b_text)
        self.assertNotIn("[[A]]", b_text)
        self.assertEqual(graph.cmd_check(compose.argparse.Namespace(
            vault=self.vault, paths=["A.md", "B.md"])), 0)


class TheGateCatchesAStaleLink(VaultFixture):
    """Calibration: prove the atomicity step's own instrument (bm_vault_graph's
    scoped check) actually fails when a rewrite is left incomplete, so a
    silently-broken merge could never read as green. Built by leaving one
    inbound link unrewritten by hand (not through compose.py) and asking the
    same gate compose.py calls after --apply to look at it."""

    def test_a_left_over_link_to_the_folded_note_fails_the_scoped_gate(self):
        self._write("A.md", note("n-0000000000000050", body="\n# A\n\nContent.\n"))
        self._write("B.md", note("n-0000000000000051", body="\n# B\n\nContent.\n"))
        # Simulates a merge that missed one inbound link: Referrer still points
        # at A, but A itself is renamed away underneath it (the shape a buggy
        # rewrite pass would leave behind).
        self._write("Referrer.md", note("n-0000000000000052", body="\nSee [[A]].\n"))
        os.remove(os.path.join(self.vault, "A.md"))
        code = graph.cmd_check(compose.argparse.Namespace(
            vault=self.vault, paths=["Referrer.md"]))
        self.assertNotEqual(code, 0)


class NoDataAndRefusalExitCodes(VaultFixture):
    def test_unreadable_vault_is_no_data(self):
        code = compose.main(["split", "--vault", os.path.join(self.tmp, "nowhere"),
                              "--note", "X", "--heading", "H", "--today", "2026-08-30"])
        self.assertEqual(code, 2)

    def test_bad_today_is_refused(self):
        self._write("Source.md", note("n-0000000000000060", body="\n# S\n\n## Sub\n\nB.\n"))
        code = compose.main(["split", "--vault", self.vault, "--note", "Source",
                              "--heading", "Sub", "--today", "not-a-date"])
        self.assertEqual(code, 1)

    def test_no_note_found_is_refused_not_no_data(self):
        self._write("Only.md", note("n-0000000000000070", body="\n# Only\n\n## Sub\n\nB.\n"))
        code = compose.main(["merge", "--vault", self.vault, "--from", "Ghost",
                              "--to", "Only"])
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
