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


class TestAliasNotation(unittest.TestCase):
    """E121: a dictionary entry written "A=B(reason)" is an ALIAS, not a
    segmentation boundary. Invented names throughout: no term, note or
    query here comes from any benchmark corpus."""

    def test_a_rename_reason_parses_as_an_identity_alias(self):
        self.assertEqual(
            az.parse_alias(u"旭興産=北陽商会(1972年当時の旧社名)"),
            (u"旭興産", u"北陽商会"))

    def test_an_abbreviation_reason_parses_too(self):
        self.assertEqual(
            az.parse_alias(u"緑友会=一般社団法人緑地友好協会(略称)"),
            (u"緑友会", u"一般社団法人緑地友好協会"))

    def test_a_bare_equation_with_no_reason_is_an_identity(self):
        self.assertEqual(az.parse_alias(u"令和8年=2026年"),
                          (u"令和8年", u"2026年"))

    def test_full_width_brackets_are_read_like_ascii_ones(self):
        self.assertEqual(az.parse_alias(u"旭興産=北陽商会（社名変更）"),
                          (u"旭興産", u"北陽商会"))

    def test_a_relationship_reason_is_declined_not_aliased(self):
        """The conservative arm: "=" with a reason that states a
        RELATIONSHIP rather than an identity must not make one company's
        notes answer for another's."""
        self.assertIsNone(
            az.parse_alias(u"東雲物産=東雲商事の関連会社(創業家が同じ)"))

    def test_a_plain_term_is_not_an_alias(self):
        self.assertIsNone(az.parse_alias(u"特殊配送"))
        self.assertIsNone(az.parse_alias(""))
        self.assertIsNone(az.parse_alias(None))

    def test_alias_links_resolve_in_both_directions(self):
        links = az.alias_links([u"旭興産=北陽商会(1972年当時の旧社名)"])
        self.assertEqual(links[u"旭興産"], (u"北陽商会",))
        self.assertEqual(links[u"北陽商会"], (u"旭興産",))

    def test_alias_entry_contributes_both_sides_as_segmentation_terms(self):
        d = tempfile.mkdtemp()
        try:
            path = os.path.join(d, "user-dictionary.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"terms": [u"旭興産=北陽商会(旧社名)", u"特殊配送"]},
                          fh, ensure_ascii=False)
            self.assertEqual(az.load_dictionary(path),
                              [u"旭興産", u"北陽商会", u"特殊配送"])
        finally:
            shutil.rmtree(d)

    def test_a_declined_entry_contributes_no_term(self):
        d = tempfile.mkdtemp()
        try:
            path = os.path.join(d, "user-dictionary.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"terms": [u"東雲物産=東雲商事の関連会社(創業家が同じ)"]},
                          fh, ensure_ascii=False)
            self.assertEqual(az.load_dictionary(path), [])
        finally:
            shutil.rmtree(d)

    def test_expansion_reaches_the_other_name_through_a_legal_form(self):
        links = az.alias_links([u"旭興産=北陽商会(1972年当時の旧社名)"])
        text = az.strip_legal_forms(az.normalize(u"旭興産株式会社の代表者"))
        self.assertEqual(az.alias_expansions(text, links), [u"北陽商会"])

    def test_expansion_finds_a_side_segment_could_never_tokenize(self):
        """A side carrying ASCII ("JX...") is split by segment()'s own run
        boundary, so the expansion is a substring test, not a token one."""
        links = az.alias_links([u"全国緑地協同組合=JX緑地(2019年改称)"])
        self.assertEqual(
            az.alias_expansions(u"JX緑地という名前になる前の組合名", links),
            [u"全国緑地協同組合"])

    def test_analyze_emits_the_counterpart_and_the_mutation_removes_it(self):
        vault_dir = tempfile.mkdtemp()
        dict_dir = os.path.join(vault_dir, "99-System", "dictionaries")
        os.makedirs(dict_dir)
        with open(os.path.join(dict_dir, "user-dictionary.json"),
                  "w", encoding="utf-8") as fh:
            json.dump({"terms": [u"旭興産=北陽商会(1972年当時の旧社名)"]},
                      fh, ensure_ascii=False)
        try:
            toks = az.analyze(u"旭興産株式会社の現在の代表者は誰ですか",
                              vault_dir=vault_dir)
            self.assertIn(u"北陽商会", toks,
                           "the alias counterpart must be a match token")
            self.assertIn(u"旭興産", toks,
                           "the named side must segment whole, not into bigrams")
            self.assertNotIn(u"興産", toks,
                              "the shared bigram every 興産 company owns must "
                              "not survive once the name is a dictionary term")

            orig = az.parse_alias
            az.parse_alias = lambda term: None
            try:
                muted = az.analyze(u"旭興産株式会社の現在の代表者は誰ですか",
                                   vault_dir=vault_dir)
            finally:
                az.parse_alias = orig
            self.assertNotIn(u"北陽商会", muted,
                              "the mutation control must remove the counterpart")
            self.assertIn(u"興産", muted,
                           "and must restore the bigram behavior that shipped "
                           "before the alias notation was read")
        finally:
            shutil.rmtree(vault_dir, ignore_errors=True)


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


class TestAliasRetrievalOnAFixtureVault(unittest.TestCase):
    """E121 end to end, through bm_vault.py's real _search: a query naming a
    company by its FORMER name serves the current company's note, and the
    mutation control (alias reading disabled) puts the decoy back on top.

    Invented names, never a benchmark corpus entry: the decoy is added
    FIRST so it holds the smaller note id and wins any tie, which is what
    makes the before state a real failure rather than an accident."""

    @classmethod
    def setUpClass(cls):
        cls.bm = _load_bm_vault()

    def setUp(self):
        self.con = _fresh_con(self.bm)
        _add_note(self.bm, self.con, "decoy.md", u"大原興産株式会社",
                  u"大原興産株式会社は東京都港区に本社を置く総合商社である。"
                  u"代表取締役社長は大原健一。従業員数は約320名。")
        _add_note(self.bm, self.con, "correct.md", u"北陽商会株式会社",
                  u"北陽商会株式会社は富山県高岡市に本社を置く農産物卸売業者。"
                  u"1972年創業。代表は北陽次郎。")
        self.query = u"旭興産株式会社の現在の代表者は誰ですか"
        self.vault_dir = tempfile.mkdtemp()
        dict_dir = os.path.join(self.vault_dir, "99-System", "dictionaries")
        os.makedirs(dict_dir)
        with open(os.path.join(dict_dir, "user-dictionary.json"),
                  "w", encoding="utf-8") as fh:
            json.dump({"terms": [u"旭興産=北陽商会(1972年当時の旧社名)"]},
                      fh, ensure_ascii=False)
        self._orig_vault = self.bm._default_vault
        self.bm._default_vault = lambda: self.vault_dir

    def tearDown(self):
        self.bm._default_vault = self._orig_vault
        self.con.close()
        shutil.rmtree(self.vault_dir, ignore_errors=True)

    def _top_path(self):
        fused, _why = self.bm._search(self.con, text=self.query, limit=6, fast=True)
        self.assertTrue(fused, "expected at least one hit")
        return self.con.execute(
            "SELECT path FROM notes WHERE id=?", (fused[0][0],)).fetchone()["path"]

    def test_the_former_name_serves_the_current_companys_note(self):
        self.assertEqual(self._top_path(), "correct.md")

    def test_mutation_control_alias_reading_off_serves_the_decoy(self):
        """Disabling exactly one thing (parse_alias declining every entry)
        is the pre-E121 behavior: no alias link, and an entry carrying "="
        contributing no segmentation term, which never matched anything."""
        orig_loader = self.bm._load_bm_vault_analyzer

        def _no_alias_reading():
            mod = orig_loader()
            mod.parse_alias = lambda term: None
            return mod

        self.bm._load_bm_vault_analyzer = _no_alias_reading
        try:
            self.assertEqual(
                self._top_path(), "decoy.md",
                "with alias reading off the query must fall back to the "
                "shared 興産 bigram and serve the wrong company: if this "
                "already passed, the test proves nothing")
        finally:
            self.bm._load_bm_vault_analyzer = orig_loader


class TestRomajiReading(unittest.TestCase):
    """JA78 mechanism 1, at the unit: a Latin word read as its kana.

    Every name here is INVENTED. Nothing in this class names a company, a
    case id or a note from the frozen adversarial corpus, so a passing test
    cannot be a fixture memorised into the code."""

    def test_a_plain_three_mora_name_reads(self):
        self.assertEqual(az.romaji_to_kana("mizuhara"), u"ミズハラ")

    def test_the_moraic_n_is_read_alone_when_no_vowel_follows(self):
        self.assertEqual(az.romaji_to_kana("kanzaki"), u"カンザキ")

    def test_n_before_a_vowel_stays_a_syllable(self):
        self.assertEqual(az.romaji_to_kana("minamoto"), u"ミナモト")

    def test_a_doubled_consonant_is_the_small_tsu(self):
        self.assertEqual(az.romaji_to_kana("hattori"), u"ハットリ")

    def test_a_three_letter_single_mora_does_not_shift_the_table(self):
        """The regression this class exists for. A first draft built the
        syllable table by slicing a kana string positionally per row, which
        mis-aligned every row holding a three-letter SINGLE mora (shi, chi,
        tsu) against the yoon rows where three letters really are two kana,
        and silently produced su -> セ. Pin the morae either side of it."""
        self.assertEqual(az.romaji_to_kana("shisuse"), u"シスセ")
        self.assertEqual(az.romaji_to_kana("chitsute"), u"チツテ")

    def test_a_yoon_is_two_kana(self):
        self.assertEqual(az.romaji_to_kana("kyokusho"), u"キョクショ")

    def test_a_word_that_is_not_hepburn_is_declined_whole(self):
        for word in ("planning", "corporation", "ltd", "export"):
            self.assertEqual(az.romaji_to_kana(word), "",
                              "%r is not readable as Hepburn and must decline" % word)

    def test_a_partial_parse_never_leaks_a_fragment(self):
        # "mizu" reads, "hxq" does not: the whole word declines rather than
        # returning the ミズ prefix, which would match notes at random.
        self.assertEqual(az.romaji_to_kana("mizuhxq"), "")

    def test_digits_decline(self):
        self.assertEqual(az.romaji_to_kana("1965"), "")

    def test_short_tokens_are_below_the_floor(self):
        # "sea" parses as se + a, which is exactly the accidental reading the
        # floor exists to keep out of the match lane.
        self.assertEqual(az.romaji_to_kana("sea"), "")
        self.assertEqual(az.romaji_to_kana("co"), "")

    def test_reading_variants_skips_declines_and_deduplicates(self):
        self.assertEqual(
            az.reading_variants(["mizuhara", "ltd", "mizuhara", "kanzaki"]),
            [u"ミズハラ", u"カンザキ"])

    def test_analyze_emits_both_kana_directions_of_a_reading(self):
        toks = az.analyze(u"Mizuhara の代表は誰ですか")
        self.assertIn(u"ミズハラ", toks)
        self.assertIn(u"みずはら", toks)


class TestJA78MechanismsAtTheSearchSeam(unittest.TestCase):
    """JA78 mechanisms 1 and 2 where they actually decide a result:
    bm_vault._cjk_hits, the analyzer-driven lexical lane. Each is driven BOTH
    WAYS, with a mutation control that disables the mechanism and shows the
    decoy served again, so a passing assertion cannot pass for free.

    Every company name below is INVENTED, and the fixture is built in memory:
    nothing here reads the frozen corpus or this machine's own vault."""

    @classmethod
    def setUpClass(cls):
        cls.bm = _load_bm_vault()

    def setUp(self):
        self.con = _fresh_con(self.bm)
        # An EMPTY vault directory, so the dictionaries this seam loads are
        # empty and the result is a property of the code, never of whatever
        # vault happens to sit on the machine running the suite.
        self.vault_dir = tempfile.mkdtemp(prefix="bm-ja78-vault-")
        self.orig_default_vault = self.bm._default_vault
        self.bm._default_vault = lambda: self.vault_dir

    def tearDown(self):
        self.bm._default_vault = self.orig_default_vault
        shutil.rmtree(self.vault_dir, ignore_errors=True)
        self.con.close()

    def _paths(self, ids):
        out = []
        for nid in ids:
            row = self.con.execute(
                "SELECT path FROM notes WHERE id=?", (nid,)).fetchone()
            out.append(row["path"])
        return out

    def test_a_fullwidth_latin_name_is_reachable_by_a_halfwidth_query(self):
        """Mechanism 2: one normal form on BOTH sides. The right company
        writes its own English designation full-width; the decoy writes only
        the generic legal form CO., LTD. half-width."""
        _add_note(self.bm, self.con, "genkai.md", u"ゲンカイ商会株式会社",
                  u"ゲンカイ商会株式会社の英語表記はＧＥＮＫＡＩ　ＴＲＡＤＩＮＧ"
                  u"　ＣＯ．，ＬＴＤ．。代表は緑川花子。")
        _add_note(self.bm, self.con, "hosho.md", u"ホウショウ企画株式会社",
                  u"ホウショウ企画株式会社は英語表記で HOSHO PLANNING CO., LTD. "
                  u"と称する。代表は青木一郎。")
        query = u"GENKAI Trading Co., Ltd. の代表は誰ですか"
        # Load-bearing: the QUERY carries nothing full-width, so query-side
        # folding is a no-op here and the only thing normalize() can be doing
        # in this test is folding the NOTE.
        self.assertEqual(az.normalize(query), query)

        ranked = self._paths(self.bm._cjk_hits(self.con, query, az))
        self.assertEqual(ranked[0], "genkai.md",
                          "the company whose own English designation the query "
                          "names must outrank one that merely shares CO., LTD.")

        orig_normalize = az.normalize
        az.normalize = lambda t: t or ""
        try:
            muted = self._paths(self.bm._cjk_hits(self.con, query, az))
        finally:
            az.normalize = orig_normalize
        self.assertEqual(muted[0], "hosho.md",
                          "mutation control: with the fold disabled the decoy "
                          "must be served again, or this test proves nothing")

    def test_a_romanised_name_reaches_the_katakana_note_it_reads(self):
        """Mechanism 1: a reading earns candidacy. The decoy is an unrelated
        department note that literally spells the query's generic attribute
        word, which is the whole of its claim."""
        _add_note(self.bm, self.con, "mizuhara.md", u"ミズハラ精機株式会社",
                  u"ミズハラ精機株式会社は電子部品メーカーである。代表は緑川花子。")
        _add_note(self.bm, self.con, "kurogane_hr.md", u"クロガネ商事株式会社 人事部",
                  u"クロガネ商事株式会社の人事部。所在地は本社と同じである。")
        query = u"Mizuhara Seiki Corporation の所在地はどこですか"

        ranked = self._paths(self.bm._cjk_hits(self.con, query, az))
        self.assertIn("mizuhara.md", ranked,
                      "the romanised name must reach the note that reads that way")

        orig_reader = az.romaji_to_kana
        az.romaji_to_kana = lambda word: ""
        try:
            muted = self._paths(self.bm._cjk_hits(self.con, query, az))
        finally:
            az.romaji_to_kana = orig_reader
        self.assertNotIn("mizuhara.md", muted,
                          "mutation control: with reading disabled the right "
                          "note must fall out of the lane entirely")
        self.assertEqual(muted, ["kurogane_hr.md"],
                          "and the generic attribute word must be all that is left")

    def test_a_reading_is_candidacy_and_never_an_identity_claim(self):
        """Section 7: the same kana reading does not imply the same entity.
        Two invented companies read the same way; the reading must recall
        both rather than pick one, so nothing downstream can treat a reading
        as proof of identity."""
        _add_note(self.bm, self.con, "one.md", u"ミズハラ精機株式会社",
                  u"ミズハラ精機株式会社は電子部品メーカーである。")
        _add_note(self.bm, self.con, "two.md", u"ミズハラ運輸株式会社",
                  u"ミズハラ運輸株式会社は貨物輸送業者である。")
        ranked = self._paths(self.bm._cjk_hits(self.con, u"Mizuhara の会社概要", az))
        self.assertEqual(sorted(ranked), ["one.md", "two.md"])


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
