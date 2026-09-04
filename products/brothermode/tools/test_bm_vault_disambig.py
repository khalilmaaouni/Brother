#!/usr/bin/env python3
"""Calibration for the VB2-08 entity-disambiguation pass in bm_vault._search.

A blind adversarial Japanese corpus scored the negative (disambiguation) class
1/13: the fused ranker floated the WRONG member of a near-duplicate entity pair
to the top. The pass this file guards drops that decoy after fusion. These
tests use INVENTED company names (not the frozen corpus, which is external and
must not be tuned against) so they prove the mechanism generalises, and they
assert the two directions that matter: the decoy is dropped, and an honest
single-entity query loses nothing.

Run: python3 -m unittest test_bm_vault_disambig -v

No em or en dashes anywhere in this file.
"""
import importlib.util
import os
import sqlite3
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


def _load_bm():
    spec = importlib.util.spec_from_file_location(
        "bm_vault", os.path.join(HERE, "bm_vault.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class DisambigBase(unittest.TestCase):
    #: (stem, title, body) for a small confusable estate. Kanji-only content on
    #: purpose: the pass keys on kanji/katakana content words.
    NOTES = [
        ("chuo_bank", u"中央銀行株式会社",
         u"中央銀行株式会社は名古屋市に本店を置く地方銀行。頭取は佐藤一郎。"),
        ("chuo_shinkin", u"中央信用金庫",
         u"中央信用金庫は岐阜市に本店を置く信用金庫。中央銀行株式会社とは別法人である。理事長は鈴木二郎。"),
        ("koda_seiko", u"甲田精工株式会社",
         u"甲田精工株式会社は甲府市の精密部品メーカー。医療機器部品を製造する。代表は甲田三郎。"),
        ("otsu_seiko", u"乙川精工株式会社",
         u"乙川精工株式会社は岡谷市の精密部品メーカー。時計部品の製造で知られる。代表は乙川四郎。"),
        ("hokuto_denki", u"北都電気株式会社",
         u"北都電気株式会社は家庭用電化製品の卸売業者。代表は北都五郎。"),
        ("hokuto_denshi", u"北都電子株式会社",
         u"北都電子株式会社は電子機器メーカー。主力は型番ZZ-900のセンサーユニット。代表は北都六郎。"),
        ("filler1", u"みどり物流株式会社",
         u"みどり物流株式会社は倉庫及び配送を手掛ける物流会社である。"),
    ]

    def setUp(self):
        self.bm = _load_bm()
        self.con = sqlite3.connect(":memory:")
        self.con.row_factory = sqlite3.Row
        self.bm._schema(self.con)
        self.stem = {}
        for i, (stem, title, body) in enumerate(self.NOTES):
            path = "disambig/%s.md" % stem
            self.bm._upsert_note(self.con, path, title, "", "test",
                                 "lesson", float(i), body)
            row = self.con.execute("SELECT id FROM notes WHERE path=?",
                                   (path,)).fetchone()
            self.stem[stem] = row["id"]
        self._vault = tempfile.mkdtemp(prefix="bm-disambig-")
        self._orig = self.bm._default_vault
        self.bm._default_vault = lambda: self._vault

    def tearDown(self):
        self.bm._default_vault = self._orig
        self.con.close()

    def served(self, query, limit=10):
        fused, _why = self.bm._search(self.con, text=query, limit=limit, fast=True)
        return {nid for nid, _s in fused}


class TestDecoyDropped(DisambigBase):
    def test_org_type_sibling_is_dropped(self):
        # Query names the bank; the shinkin (shares the 中央 stem, even names the
        # bank in a disclaimer) must not be served.
        served = self.served(u"中央銀行の頭取は誰ですか")
        self.assertNotIn(self.stem["chuo_shinkin"], served)
        self.assertIn(self.stem["chuo_bank"], served)

    def test_attribute_contradiction_drops_named_entity(self):
        # Names 甲田精工 but asks about 時計部品, which 乙川精工 makes: the named
        # but contradicted 甲田 is dropped.
        served = self.served(u"甲田精工株式会社の時計部品について教えてください")
        self.assertNotIn(self.stem["koda_seiko"], served)

    def test_surname_collision_is_dropped(self):
        # Query fuses 北都電子 (denshi's type) with denshi's product ZZ-900; the
        # 北都電気 (denki) note rides only the 北都 fragment and owns no attribute.
        served = self.served(u"北都電子(型番ZZ-900のセンサーユニット)の代表は誰ですか")
        self.assertNotIn(self.stem["hokuto_denki"], served)
        self.assertIn(self.stem["hokuto_denshi"], served)


class TestHonestQueryUntouched(DisambigBase):
    def test_entity_with_its_own_attribute_survives(self):
        # 甲田精工 asked about ITS OWN product (医療機器) must be served: the
        # disambiguation pass must not drop a consistent single-entity match.
        served = self.served(u"甲田精工は医療機器部品を製造していますか")
        self.assertIn(self.stem["koda_seiko"], served)

    def test_plain_lookup_returns_the_named_entity(self):
        served = self.served(u"乙川精工の代表は誰ですか")
        self.assertIn(self.stem["otsu_seiko"], served)

    def test_both_siblings_survive_when_neither_is_named(self):
        # A generic industry query names no entity and no attribute contradiction:
        # the pass must not drop either 精工 note.
        served = self.served(u"精密部品メーカーについて教えてください")
        self.assertIn(self.stem["koda_seiko"], served)
        self.assertIn(self.stem["otsu_seiko"], served)


class NamedAbsentBase(DisambigBase):
    """JA13. The estate above plus a 物産 family: one member owns an address and a
    trade, its peers own neither. The queries below NAME a 物産 company that the
    vault does not hold, so the family cannot be resolved by name at all."""
    NOTES = DisambigBase.NOTES + [
        ("teihara_bussan", u"丁原物産株式会社",
         u"丁原物産株式会社は東京都港区赤坂に本社を置く総合商社。皮革製品の輸出入を手掛ける。代表は丁原七郎。"),
        ("boya_bussan", u"戊野物産株式会社",
         u"戊野物産株式会社は秋田県秋田市の水産物卸売業者。代表は戊野八郎。"),
    ]

    #: names a 物産 company absent from the estate, and describes it with the address
    #: and trade that belong to 丁原物産.
    QUERY = u"丙沢物産株式会社は東京都港区赤坂に本社を置く皮革商社ですか"


class TestDescribedButNotNamed(NamedAbsentBase):
    def test_description_match_is_not_served_for_an_absent_name(self):
        # The asker named 丙沢物産. Nothing in the estate is 丙沢物産, so the address
        # and trade in the query are unverified claims about a company nobody holds.
        # Serving 丁原物産 because it fits that description answers a question the
        # asker did not ask.
        served = self.served(self.QUERY)
        self.assertNotIn(self.stem["teihara_bussan"], served)

    def test_the_dominator_is_the_note_this_rule_drops(self):
        # The mechanism claim, asserted on the pass's own trace rather than on the
        # served set: with the name absent, R2 stops electing a survivor by the
        # attributes (no "attribute mismatch" is recorded at all) and instead drops
        # the note that owns them. What happens to the remaining family members is
        # R3's business, unchanged by this rule and not asserted here.
        explain = []
        self.bm._search(self.con, text=self.QUERY, limit=10, fast=True,
                        explain=explain)
        drops = [line for line in explain if "disambiguation: dropped" in line]
        mine = [line for line in drops if "described but not named" in line]
        self.assertEqual(
            len(mine), 1, "expected exactly one described-but-not-named drop, got %r"
            % drops)
        self.assertIn("note %d " % self.stem["teihara_bussan"], mine[0])
        self.assertFalse([line for line in drops if "attribute mismatch" in line],
                         "the domination arm must not also elect a survivor: %r"
                         % drops)

    def test_a_named_and_present_company_still_wins_its_family(self):
        # The inversion is gated on the named company being ABSENT. Name one the
        # estate holds and R2's original direction must stand: the subject is served
        # and is not dropped as its own decoy.
        served = self.served(
            u"丁原物産株式会社は東京都港区赤坂に本社を置く皮革商社ですか")
        self.assertIn(self.stem["teihara_bussan"], served)

    def test_no_legal_form_leaves_the_original_direction_alone(self):
        # The rule reads the company the asker WROTE WITH A LEGAL FORM. A query that
        # names no company that way is outside it, and R2 keeps electing a survivor
        # from the attributes: 丁原物産 is served.
        served = self.served(u"東京都港区赤坂の皮革商社について教えてください")
        self.assertIn(self.stem["teihara_bussan"], served)


class TestNamedAbsentMutation(NamedAbsentBase):
    """The mutation control for the rule above: disable it and the case must fail
    again. _ja_query_named returning nothing is exactly 'the query names no company',
    which is the one input the inversion is gated on, so patching it restores the
    pre-JA13 behaviour without touching any other signal."""

    def test_disabling_the_rule_serves_the_decoy_again(self):
        self.bm._ja_query_named = lambda qnorm: []
        served = self.served(self.QUERY)
        self.assertIn(
            self.stem["teihara_bussan"], served,
            "with _ja_query_named disabled the decoy must come back; if it does "
            "not, this test is no longer proving the inversion is what drops it")

    def test_the_rule_enabled_drops_it(self):
        # The other half of the same control, in one file: same fixture, same query,
        # rule left alone.
        served = self.served(self.QUERY)
        self.assertNotIn(self.stem["teihara_bussan"], served)


if __name__ == "__main__":
    unittest.main()
