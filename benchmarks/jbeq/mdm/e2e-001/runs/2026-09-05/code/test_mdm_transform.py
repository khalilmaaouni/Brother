"""src/mdm_transform.py の検証。

要件の一文ごとにテストを一つ置いています。どのテストも、要件に違反した
実装なら落ちる形にしてあります (合格を書くのではなく、違反を捕まえる)。

出力はすべて一時ディレクトリに書きます。data/ 以下のソースは読むだけです。
"""
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import mdm_transform as mt

DATA = os.path.join(ROOT, "data")


class Normalization(unittest.TestCase):

    def test_houjin_bangou_normalizes_width_and_separators(self):
        self.assertEqual(mt.normalize_number("８１３0001000101"), "8130001000101")
        self.assertEqual(mt.normalize_number("8130-0010-00101"), "8130001000101")
        self.assertEqual(mt.normalize_number("  8130001000101  "), "8130001000101")

    def test_commercial_name_is_not_otherwise_touched(self):
        """営業側の名称は前後の空白以外は変えない。全角半角の統一もしない。"""
        self.assertEqual(mt.normalize_name(" アオバ 丸の内店 "), "アオバ 丸の内店")


class MergeRefusal(unittest.TestCase):

    def test_store_level_merge_directive_is_refused(self):
        row = {"store_id": "S001", "merge_directive": "MERGE_INTO:C001"}
        self.assertTrue(mt.merge_is_forbidden(row))

    def test_a_row_with_no_directive_is_not_refused(self):
        row = {"store_id": "S001", "merge_directive": ""}
        self.assertFalse(mt.merge_is_forbidden(row))


class Pipeline(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.out = os.path.join(self._tmp.name, "out")
        self.result = mt.run(DATA, self.out, inject_contradiction=True)
        self.golden = {r["customer_id"]: r for r in self.result["golden"]}

    def test_the_injected_contradiction_is_rejected_not_merged(self):
        self.assertNotIn("C008", self.golden)
        codes = [r["reason_code"] for r in self.result["rejects"]]
        self.assertEqual(codes, ["MERGE_FORBIDDEN_STORE_LEVEL"])

    def test_four_customers_share_one_corporate_number_and_none_are_merged(self):
        """法人番号が一致しても店舗単位の取引先は統合しない。"""
        same = [r for r in self.result["golden"]
                if r["houjin_bangou"] == "8130001000101"]
        self.assertEqual(len(same), 4)
        for row in same:
            self.assertEqual(row["merged_from"], "")

    def test_legal_name_comes_from_the_register(self):
        self.assertEqual(self.golden["C002"]["legal_name"],
                         "株式会社青葉ホールディングス")
        self.assertEqual(self.golden["C002"]["legal_name_source"], "registry")

    def test_commercial_name_is_the_existing_sales_facing_name(self):
        self.assertEqual(self.golden["C002"]["commercial_name"], "アオバ 丸の内店")
        self.assertNotEqual(self.golden["C002"]["commercial_name"],
                            self.golden["C002"]["legal_name"])

    def test_the_hierarchy_points_from_the_corporate_record_down(self):
        links = {r["child_customer_id"]: r for r in self.result["links"]}
        self.assertEqual(sorted(links), ["C002", "C003", "C005", "C007"])
        self.assertEqual(links["C002"]["parent_customer_id"], "C001")
        self.assertEqual(self.golden["C001"]["record_type"], "CORPORATE")
        self.assertEqual(self.golden["C002"]["record_type"], "STORE")

    def test_the_closed_store_keeps_its_own_record_and_closing_date(self):
        closed = self.golden["C003"]
        self.assertEqual(closed["status"], "CLOSED")
        self.assertEqual(closed["valid_to"], "2025-03-31")
        self.assertEqual(closed["store_id"], "S002")

    def test_the_new_store_is_a_record_of_its_own_not_a_substitute(self):
        """住所表記が似ていても、閉店した S002 を S004 で置き換えない。"""
        new = self.golden["C007"]
        self.assertEqual(new["store_id"], "S004")
        self.assertEqual(new["valid_from"], "2025-04-01")
        self.assertIn("C003", self.golden)

    def test_historical_transactions_stay_on_the_closed_store(self):
        by_store = {}
        for row in self.result["transactions"]:
            by_store[row["store_id"]] = by_store.get(row["store_id"], 0) + 1
        self.assertEqual(by_store["S002"], 3)
        self.assertNotIn("S004", by_store)

    def test_the_link_period_follows_the_store_period(self):
        links = {r["child_customer_id"]: r for r in self.result["links"]}
        self.assertEqual(links["C003"]["effective_to"], "2025-03-31")
        self.assertEqual(links["C007"]["effective_from"], "2025-04-01")

    def test_the_two_open_questions_are_carried_as_unknown(self):
        rules = {r["id"]: r for r in mt.rule_book()["rules"]}
        self.assertEqual(rules["R10"]["status"], "UNKNOWN")
        self.assertEqual(rules["R11"]["status"], "UNKNOWN")
        self.assertTrue(rules["R10"]["open_question"].strip())
        self.assertTrue(rules["R11"]["open_question"].strip())

    def test_payment_terms_are_carried_through_unchanged(self):
        """未解決の項目に既定値を入れない。ソースの値をそのまま運ぶ。"""
        self.assertEqual(self.golden["C003"]["payment_terms_days"], "45")
        self.assertEqual(self.golden["C004"]["payment_terms_days"], "60")

    def test_every_output_file_is_written(self):
        for name in ("golden.csv", "links.csv", "rejects.csv",
                     "transactions_out.csv", "source_customers.csv",
                     "mapping.json", "decisions.json"):
            self.assertTrue(os.path.isfile(os.path.join(self.out, name)), name)


class WithoutTheInjection(unittest.TestCase):

    def test_a_clean_run_rejects_nothing(self):
        result = mt.transform(DATA, inject_contradiction=False)
        self.assertEqual(result["rejects"], [])
        self.assertEqual(len(result["golden"]), 7)


if __name__ == "__main__":
    unittest.main(verbosity=2)
