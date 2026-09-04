#!/usr/bin/env python3
"""Calibration for tools/bm_vault_enrich_index.py, WBS VB11-03: the retrieval
enrichment pre-compute lane.

The property under test is the row's own observable, driven backwards:
a promoted alias makes a previously-dense query resolve lexically, an
unpromoted draft changes nothing, provenance names the model, and a
tampered promotion record (missing promoted_by) drops back out of the
index exactly like an unpromoted draft would.

Run: python3 -m unittest test_bm_vault_enrich_index -v

No em or en dashes anywhere in this file.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bm_vault_enrich as enrich            # noqa: E402
import bm_vault_enrich_index as eix         # noqa: E402
import bm_vault_lifecycle as lc             # noqa: E402
import bm_vault_promotions as promo         # noqa: E402

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

CATALOG_TOOL = os.path.join(HERE, "bm_vault_catalog.py")

TARGET_NOTE = """---
type: reference
status: open
created: 2026-08-30
---

# the target note

This note discusses nothing that resembles the alias term below; a plain
lexical search for that term must miss this note until a promoted alias
says otherwise.
"""

# The query is the EXACT drafted alias phrase: a genuine alias search asks
# for the alias words, not for words the note's own prose happens to use.
# All of the query's significant tokens must land, together, on something
# that names the note (see bm_vault_enrich_index.lexical_hit), so the alias
# and the query are the same phrase by construction here.
QUERY = "zorblewax retrieval console"
ALIAS_LINE = "zorblewax retrieval console\n"


def bake(vault):
    """(returncode, output) from the real bm_vault_catalog.py CLI, the same
    entry point an operator runs, so this test proves the wired-up bake
    path and not only the internal helper function."""
    p = subprocess.run([sys.executable, CATALOG_TOOL, "bake", "--vault", vault],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return p.returncode, (p.stdout + p.stderr).decode("utf-8", "replace")


class VaultFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-enrich-index-")
        self.vault = os.path.join(self.tmp, "vault")
        self.project_dir = os.path.join(self.vault, "10-Projects", "demo")
        os.makedirs(self.project_dir)
        self.note_relpath = "10-Projects/demo/target.md"
        with open(os.path.join(self.project_dir, "target.md"), "w",
                  encoding="utf-8") as fh:
            fh.write(TARGET_NOTE)
        self.from_file = os.path.join(self.tmp, "aliases.txt")
        with open(self.from_file, "w", encoding="utf-8") as fh:
            fh.write("# a comment, skipped\n")
            fh.write(ALIAS_LINE)
            fh.write("Q: what is the zorblewax about\n")
        self.queries_path = os.path.join(self.tmp, "queries.json")
        with open(self.queries_path, "w", encoding="utf-8") as fh:
            json.dump([{"query": QUERY, "expected_note": self.note_relpath}], fh)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _draft(self):
        return eix.draft_aliases(self.vault, self.note_relpath, "test-model-x",
                                  self.from_file)

    def _promote_to_canonical(self, rel):
        rc1 = promo.cmd_promote(self.vault, rel, "validated", "khalil",
                                "2026-08-30", apply_changes=True)
        self.assertEqual(rc1, 0)
        rc2 = promo.cmd_promote(self.vault, rel, "canonical", "khalil",
                                "2026-08-30", apply_changes=True)
        self.assertEqual(rc2, 0)


class ADraftedAliasLandsAsAMachineDraftedCandidate(VaultFixture):
    def test_provenance_names_the_model_through_the_enrichment_readers_own_reader(self):
        ok, messages = self._draft()
        self.assertTrue(ok, messages)
        rows = enrich.list_drafts(self.vault, self.note_relpath, field="alias")
        self.assertEqual(len(rows), 1, rows)
        rel, state, model, kind = rows[0]
        self.assertEqual(state, "candidate")
        self.assertEqual(model, "test-model-x")
        self.assertEqual(kind, "machine")
        qrows = enrich.list_drafts(self.vault, self.note_relpath, field="question_form")
        self.assertEqual(len(qrows), 1, qrows)
        self.assertEqual(qrows[0][2], "test-model-x")


class AnUnpromotedDraftChangesNothingAtBake(VaultFixture):
    def test_bake_then_the_dense_query_still_misses_and_the_index_carries_no_alias(self):
        ok, messages = self._draft()
        self.assertTrue(ok, messages)
        rc, out = bake(self.vault)
        self.assertEqual(rc, 0, out)
        catalog_path = os.path.join(self.project_dir, "Catalog.md")
        with open(catalog_path, encoding="utf-8") as fh:
            catalog_text = fh.read()
        self.assertNotIn("zorblewax", catalog_text.lower(),
                         "an unpromoted draft's alias text reached the baked index:\n%s"
                         % catalog_text)
        hits, total, detail = eix.measure(self.vault, self.queries_path)
        self.assertEqual((hits, total), (0, 1), detail)


class APromotedAliasMakesADenseQueryResolveLexically(VaultFixture):
    def test_before_zero_after_one_measured_and_the_delta_is_positive(self):
        ok, messages = self._draft()
        self.assertTrue(ok, messages)
        rows = enrich.list_drafts(self.vault, self.note_relpath, field="alias")
        rel = rows[0][0]

        # BEFORE: bake with the draft still a candidate.
        rc, out = bake(self.vault)
        self.assertEqual(rc, 0, out)
        before_hits, before_total, before_detail = eix.measure(self.vault, self.queries_path)
        self.assertEqual((before_hits, before_total), (0, 1), before_detail)

        # Promote through the REAL ceremony (tools/bm_vault_promotions.py), a
        # named human in the record, never re-implemented here.
        self._promote_to_canonical(rel)

        # AFTER: re-bake so the now-canonical alias joins the index.
        rc, out = bake(self.vault)
        self.assertEqual(rc, 0, out)
        after_hits, after_total, after_detail = eix.measure(self.vault, self.queries_path)
        self.assertEqual((after_hits, after_total), (1, 1), after_detail)

        delta = after_hits - before_hits
        self.assertEqual(delta, 1)
        print("MEASURE before: hit-rate: %d/%d (0%%)" % (before_hits, before_total))
        print("MEASURE after:  hit-rate: %d/%d (100%%)  delta: +%d" %
              (after_hits, after_total, delta))

        catalog_path = os.path.join(self.project_dir, "Catalog.md")
        with open(catalog_path, encoding="utf-8") as fh:
            catalog_text = fh.read()
        self.assertIn("zorblewax", catalog_text.lower())
        self.assertIn("khalil", self._draft_text(rel), "the promoter was not recorded")

    def _draft_text(self, rel):
        with open(os.path.join(self.vault, rel), encoding="utf-8") as fh:
            return fh.read()


class ATamperedPromotionRecordDropsBackOutOfTheIndex(VaultFixture):
    def test_removing_promoted_by_excludes_the_alias_from_the_next_bake(self):
        ok, messages = self._draft()
        self.assertTrue(ok, messages)
        rows = enrich.list_drafts(self.vault, self.note_relpath, field="alias")
        rel = rows[0][0]
        self._promote_to_canonical(rel)
        rc, out = bake(self.vault)
        self.assertEqual(rc, 0, out)
        hits, total, detail = eix.measure(self.vault, self.queries_path)
        self.assertEqual((hits, total), (1, 1), detail)

        full = os.path.join(self.vault, rel)
        with open(full, encoding="utf-8") as fh:
            text = fh.read()
        state, record, problems = lc.read_promotion(text)
        self.assertEqual(state, "canonical")
        self.assertEqual(problems, [])
        tampered = text.replace("promoted_by: khalil\n", "")
        self.assertNotEqual(tampered, text, "the fixture did not actually remove the field")
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(tampered)

        state2, _record2, problems2 = lc.read_promotion(tampered)
        self.assertEqual(state2, "canonical")
        self.assertTrue(problems2, "a canonical record missing promoted_by must be a finding")

        rc, out = bake(self.vault)
        self.assertEqual(rc, 0, out)
        hits2, total2, detail2 = eix.measure(self.vault, self.queries_path)
        self.assertEqual((hits2, total2), (0, 1), detail2)


class MeasureOnAMissingQuerySetIsNoData(VaultFixture):
    def test_missing_path_reports_no_data_naming_the_path(self):
        missing = os.path.join(self.tmp, "does-not-exist.json")
        hits, total, detail = eix.measure(self.vault, missing)
        self.assertIsNone(hits)
        self.assertIsNone(total)
        self.assertEqual(len(detail), 1)
        self.assertIn("NO-DATA", detail[0])
        self.assertIn(missing, detail[0])

    def test_empty_query_set_reports_no_data(self):
        empty = os.path.join(self.tmp, "empty.json")
        with open(empty, "w", encoding="utf-8") as fh:
            fh.write("[]")
        hits, total, detail = eix.measure(self.vault, empty)
        self.assertIsNone(hits)
        self.assertIn("NO-DATA", detail[0])


if __name__ == "__main__":
    unittest.main()
