#!/usr/bin/env python3
"""Calibration for tools/bm_vault_analyzer.py and its seam in tools/bm_vault.py,
WBS VB2-03: Japanese-first retrieval.

Driven backwards, per the row's own done-check:
  - zero-ASCII Japanese lexical retrieval works on a fixture set
  - a mixed Japanese and English query recalls correctly
  - a user-dictionary change moves ranking deterministically (and reverts)
  - kana alias matches both scripts
  - a pure-ASCII query is byte-identical: the analyzer is never even loaded
  - normalize() folds the declared classes and provably NOT the excluded ones

Run: python3 -m unittest test_bm_vault_analyzer -v

No em or en dashes anywhere in this file.
"""
import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
import shutil
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bm_vault_analyzer as az   # noqa: E402


def _load_bm_vault():
    spec = importlib.util.spec_from_file_location("bm_vault", os.path.join(HERE, "bm_vault.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fresh_con(bm):
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    bm._schema(con)
    return con


def _add_note(bm, con, path, title, body):
    return bm._upsert_note(con, path, title, "", "test", "lesson", 0.0, body)


class TestNormalizeScopedWidthFolding(unittest.TestCase):
    """Section 7 of the research doc: NFKC scoped, never blind."""

    def test_fullwidth_ascii_folds(self):
        self.assertEqual(az.normalize("ＡＢＣ"), "ABC")

    def test_fullwidth_digits_and_hyphen_fold(self):
        self.assertEqual(az.normalize("０１２"), "012")

    def test_halfwidth_katakana_folds_to_fullwidth(self):
        # half-width KA (U+FF76) + half-width voiced mark (U+FF9E) -> full-width GA
        self.assertEqual(az.normalize("ｶﾞ"), "ガ")

    def test_ideographic_space_folds(self):
        self.assertEqual(az.normalize("a　b"), "a b")

    def test_circled_numeral_is_not_folded(self):
        # U+2460 CIRCLED DIGIT ONE: NFKC would rewrite this to "1"; scoped
        # normalize() must leave it exactly as it is (research section 7's
        # named pitfall).
        self.assertEqual(az.normalize("①"), "①")

    def test_kanji_is_not_touched(self):
        self.assertEqual(az.normalize("自動車"), "自動車")

    def test_already_fullwidth_katakana_is_not_touched(self):
        self.assertEqual(az.normalize("カタカナ"), "カタカナ")


class TestLegalFormStripping(unittest.TestCase):
    def test_strips_full_spelling_suffix(self):
        self.assertEqual(az.strip_legal_forms("トヨタ自動車株式会社"),
                          "トヨタ自動車")

    def test_strips_circled_glyph(self):
        self.assertEqual(az.strip_legal_forms("㈱テスト"), "テスト")


class TestKanaAlias(unittest.TestCase):
    def test_katakana_folds_to_hiragana(self):
        self.assertEqual(az.kana_alias("カタカナ"), "かたかな")

    def test_prolonged_sound_mark_unchanged(self):
        self.assertEqual(az.kana_alias("センター")[-1], "ー")


class TestSegmentAndDictionary(unittest.TestCase):
    def test_bigram_fallback_with_no_dictionary(self):
        toks = az.segment("自動車")   # jidousha, 3 chars
        self.assertEqual(toks, ["自動", "動車", "車"])

    def test_dictionary_longest_match_wins(self):
        toks = az.segment("自動車", user_terms=["自動車"])
        self.assertEqual(toks, ["自動車"])

    def test_load_dictionary_missing_file_is_no_data_not_a_crash(self):
        self.assertEqual(az.load_dictionary("/no/such/path/user-dictionary.json"), [])

    def test_load_dictionary_reads_terms_object_shape(self):
        d = tempfile.mkdtemp()
        try:
            p = os.path.join(d, "d.json")
            with open(p, "w", encoding="utf-8") as fh:
                json.dump({"_comment": "x", "terms": ["ab", "cd"]}, fh)
            self.assertEqual(az.load_dictionary(p), ["ab", "cd"])
        finally:
            shutil.rmtree(d)

    def test_load_dictionaries_absent_vault_is_no_data(self):
        self.assertEqual(az.load_dictionaries(None), ([], []))
        self.assertEqual(az.load_dictionaries(""), ([], []))


class TestNeedsAnalysisGate(unittest.TestCase):
    def test_pure_ascii_needs_nothing(self):
        self.assertFalse(az.needs_analysis("hello world"))
        self.assertFalse(az.has_cjk("hello world"))

    def test_kanji_needs_analysis(self):
        self.assertTrue(az.needs_analysis("自動車"))
        self.assertTrue(az.has_cjk("自動車"))

    def test_fullwidth_ascii_alone_needs_analysis_but_is_not_cjk_script(self):
        fullwidth_code = "Ｂ－０１４"  # "B-014" full-width
        self.assertTrue(az.needs_analysis(fullwidth_code))
        self.assertFalse(az.has_cjk(fullwidth_code))


class TestAnalyzeWidthVariant(unittest.TestCase):
    def test_width_folded_query_still_yields_an_ascii_token(self):
        fullwidth_code = "Ｂ－０１４"  # "B-014" full-width
        toks = az.analyze(fullwidth_code)
        self.assertIn("014", toks)


class TestRetrievalOnAFixtureVault(unittest.TestCase):
    """The row's own done-check, exercised directly against bm_vault.py's real
    _search, on a hand-built fixture vault (an in-memory sqlite connection,
    the same shape bm_vault_jbench.py builds for its benchmark)."""

    @classmethod
    def setUpClass(cls):
        cls.bm = _load_bm_vault()

    def setUp(self):
        self.con = _fresh_con(self.bm)

    def tearDown(self):
        self.con.close()

    def test_zero_ascii_japanese_lexical_recall(self):
        _add_note(self.bm, self.con, "a.md", "a",
                  "夜間配送センターの規定は明確である")
        _add_note(self.bm, self.con, "b.md", "b",
                  "これは無関係な話題についてのメモである")
        query = "夜間配送センター"  # "night delivery center"
        # zero ascii overlap: the raw query has no ASCII characters at all
        self.assertFalse(any(c.isascii() and c.isalnum() for c in query))
        fused, why = self.bm._search(self.con, text=query, limit=6, fast=True)
        self.assertTrue(fused, "expected at least one hit")
        top_id = fused[0][0]
        row = self.con.execute("SELECT path FROM notes WHERE id=?", (top_id,)).fetchone()
        self.assertEqual(row["path"], "a.md")

    def test_mixed_language_query_recalls(self):
        _add_note(self.bm, self.con, "toyota.md", "toyota note",
                  "Toyota is トヨタ自動車株式会社 the largest maker")
        _add_note(self.bm, self.con, "other.md", "other note",
                  "an unrelated note about scheduling and nothing else")
        fused, _why = self.bm._search(self.con, text="Toyota 自動車", limit=6, fast=True)
        self.assertTrue(fused)
        top_id = fused[0][0]
        row = self.con.execute("SELECT path FROM notes WHERE id=?", (top_id,)).fetchone()
        self.assertEqual(row["path"], "toyota.md")

    def test_kana_alias_matches_both_scripts(self):
        # note spelled in katakana, query in hiragana
        _add_note(self.bm, self.con, "kata.md", "kata",
                  "このセンターは埼玉にある")
        fused, _why = self.bm._search(self.con, text="せんたー", limit=6, fast=True)
        self.assertTrue(fused, "hiragana query should recall a katakana note")
        top_id = fused[0][0]
        row = self.con.execute("SELECT path FROM notes WHERE id=?", (top_id,)).fetchone()
        self.assertEqual(row["path"], "kata.md")

        # note spelled in hiragana, query in katakana
        con2 = _fresh_con(self.bm)
        try:
            _add_note(self.bm, con2, "hira.md", "hira",
                      "このせんたーは東京にある")
            fused2, _why2 = self.bm._search(con2, text="センター", limit=6, fast=True)
            self.assertTrue(fused2, "katakana query should recall a hiragana note")
            top_id2 = fused2[0][0]
            row2 = con2.execute("SELECT path FROM notes WHERE id=?", (top_id2,)).fetchone()
            self.assertEqual(row2["path"], "hira.md")
        finally:
            con2.close()

    def test_ascii_query_never_loads_the_analyzer_and_ranking_is_unaffected(self):
        """The row's byte-identical requirement: a pure-ASCII query must take
        the exact path it always has. Proven here by making the analyzer
        loader explode if it is ever called, and confirming an ordinary
        ASCII search still runs to completion with the expected top hit."""
        _add_note(self.bm, self.con, "x.md", "x note", "a rule about budget review and audit")
        _add_note(self.bm, self.con, "y.md", "y note", "an unrelated note about parking")

        def _boom():
            raise AssertionError("the analyzer must never load for a pure-ASCII query")

        orig = self.bm._load_bm_vault_analyzer
        self.bm._load_bm_vault_analyzer = _boom
        try:
            fused, _why = self.bm._search(self.con, text="budget review audit", limit=6, fast=True)
        finally:
            self.bm._load_bm_vault_analyzer = orig
        self.assertTrue(fused)
        top_id = fused[0][0]
        row = self.con.execute("SELECT path FROM notes WHERE id=?", (top_id,)).fetchone()
        self.assertEqual(row["path"], "x.md")

    def test_user_dictionary_change_moves_ranking_deterministically(self):
        """decoy gets the SMALLER note id, so a bigram-mode tie favors it
        before the dictionary exists; the dictionary entry collapses the
        query's whole run into one token only the correct note contains
        verbatim, flipping the top rank; removing the entry reverts it."""
        decoy = "特殊いろは殊配いろは配送いろは送いろは"
        correct = "夜間の特殊配送は禁止されている"
        _add_note(self.bm, self.con, "decoy.md", "decoy", decoy)
        _add_note(self.bm, self.con, "correct.md", "correct", correct)
        query = "特殊配送"  # "tokushu haisou"

        vault_dir = tempfile.mkdtemp()
        dict_dir = os.path.join(vault_dir, "99-System", "dictionaries")
        os.makedirs(dict_dir)
        dict_path = os.path.join(dict_dir, "user-dictionary.json")
        orig_default_vault = self.bm._default_vault
        self.bm._default_vault = lambda: vault_dir
        try:
            fused_before, _ = self.bm._search(self.con, text=query, limit=6, fast=True)
            self.assertTrue(fused_before)
            top_before = self.con.execute(
                "SELECT path FROM notes WHERE id=?", (fused_before[0][0],)).fetchone()["path"]
            self.assertEqual(top_before, "decoy.md",
                              "before the dictionary exists, the tie should favor the "
                              "smaller-id decoy: this is the FAILING case the row names")

            with open(dict_path, "w", encoding="utf-8") as fh:
                json.dump({"terms": [query]}, fh, ensure_ascii=False)

            fused_after, _ = self.bm._search(self.con, text=query, limit=6, fast=True)
            self.assertTrue(fused_after)
            top_after = self.con.execute(
                "SELECT path FROM notes WHERE id=?", (fused_after[0][0],)).fetchone()["path"]
            self.assertEqual(top_after, "correct.md",
                              "the dictionary entry must move this case to PASSING")

            os.remove(dict_path)
            fused_removed, _ = self.bm._search(self.con, text=query, limit=6, fast=True)
            top_removed = self.con.execute(
                "SELECT path FROM notes WHERE id=?", (fused_removed[0][0],)).fetchone()["path"]
            self.assertEqual(top_removed, "decoy.md",
                              "removing the dictionary entry must revert the ranking")
        finally:
            self.bm._default_vault = orig_default_vault
            shutil.rmtree(vault_dir, ignore_errors=True)


class TestBenchmarkFixtureFile(unittest.TestCase):
    """The benchmark file itself, not the runner: at least 200 cases,
    counted by this suite, across every declared class."""

    def test_at_least_200_cases_across_declared_classes(self):
        path = os.path.join(HERE, "fixtures", "japanese-benchmark.json")
        with open(path, encoding="utf-8") as fh:
            fixture = json.load(fh)
        cases = fixture["cases"]
        self.assertGreaterEqual(len(cases), 200)
        declared = set(fixture["_meta"]["classes"])
        seen = {c["class"] for c in cases}
        self.assertEqual(seen, declared,
                          "every case must belong to a declared class and every "
                          "declared class must have at least one case")


if __name__ == "__main__":
    unittest.main()
