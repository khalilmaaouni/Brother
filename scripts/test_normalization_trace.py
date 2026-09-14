import hashlib
import json
import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "normalization_trace.py")

sys.path.insert(0, HERE)
import normalization_trace as nt  # noqa: E402


class NFKCTransformTests(unittest.TestCase):
    def test_fullwidth_alnum_becomes_halfwidth(self):
        value, lossy, reason = nt.NFKCTransform().apply("ＡＢＣ１２３")
        self.assertEqual(value, "ABC123")
        self.assertTrue(lossy)
        self.assertIn("NFKC", reason)

    def test_halfwidth_choon_folds_to_fullwidth_choon(self):
        # Real example: half-width katakana prolonged-sound mark U+FF70
        # compatibility-decomposes to the real chōonpu U+30FC.
        value, lossy, reason = nt.NFKCTransform().apply("ｺｰﾋｰ")
        self.assertEqual(value, "コーヒー")
        self.assertTrue(lossy)

    def test_already_normalized_is_a_noop(self):
        value, lossy, reason = nt.NFKCTransform().apply("テスト")
        self.assertEqual(value, "テスト")
        self.assertFalse(lossy)


class SpaceTransformTests(unittest.TestCase):
    def test_collapses_fullwidth_and_repeated_spaces(self):
        value, lossy, reason = nt.SpaceTransform().apply("　テスト　　会社　")
        self.assertEqual(value, "テスト 会社")
        self.assertTrue(lossy)

    def test_no_whitespace_is_a_noop(self):
        value, lossy, reason = nt.SpaceTransform().apply("テスト会社")
        self.assertEqual(value, "テスト会社")
        self.assertFalse(lossy)


class LongVowelTransformTests(unittest.TestCase):
    def test_dash_lookalikes_after_kana_become_choonpu(self):
        # Real example: em dash and horizontal bar used in place of the
        # chōonpu after katakana, the documented mis-entry pattern.
        value, lossy, reason = nt.LongVowelTransform().apply("コ\u2014ヒ―")
        self.assertEqual(value, "コーヒー")
        self.assertTrue(lossy)

    def test_hyphen_not_after_kana_is_untouched(self):
        # A plain date/number hyphen must not be mistaken for a chōonpu.
        value, lossy, reason = nt.LongVowelTransform().apply("2024-09-13")
        self.assertEqual(value, "2024-09-13")
        self.assertFalse(lossy)

    def test_already_correct_choonpu_is_a_noop(self):
        value, lossy, reason = nt.LongVowelTransform().apply("コーヒー")
        self.assertEqual(value, "コーヒー")
        self.assertFalse(lossy)


class SeamTransformTests(unittest.TestCase):
    def test_itaiji_returns_unchanged_with_honest_reason(self):
        value, lossy, reason = nt.ItaijiTransform().apply("髙橋")
        self.assertEqual(value, "髙橋")
        self.assertFalse(lossy)
        self.assertEqual(reason, "no mapping table configured yet")

    def test_small_ke_returns_unchanged_with_honest_reason(self):
        value, lossy, reason = nt.SmallKeTransform().apply("三ヶ日")
        self.assertEqual(value, "三ヶ日")
        self.assertFalse(lossy)
        self.assertEqual(reason, "no mapping table configured yet")


class CorporateFormTransformTests(unittest.TestCase):
    def test_strips_leading_full_form(self):
        value, lossy, reason = nt.CorporateFormTransform().apply("株式会社テスト")
        self.assertEqual(value, "テスト")
        self.assertTrue(lossy)

    def test_strips_trailing_full_form(self):
        value, lossy, reason = nt.CorporateFormTransform().apply("テスト株式会社")
        self.assertEqual(value, "テスト")
        self.assertTrue(lossy)

    def test_strips_parenthesized_short_form(self):
        value, lossy, reason = nt.CorporateFormTransform().apply("(株)テスト")
        self.assertEqual(value, "テスト")
        self.assertTrue(lossy)

    def test_no_corporate_form_is_a_noop(self):
        value, lossy, reason = nt.CorporateFormTransform().apply("テスト")
        self.assertEqual(value, "テスト")
        self.assertFalse(lossy)


class PhoneTransformTests(unittest.TestCase):
    def test_strips_hyphens_and_parentheses(self):
        value, lossy, reason = nt.PhoneTransform().apply("(03)-1234-5678")
        self.assertEqual(value, "0312345678")
        self.assertTrue(lossy)

    def test_no_separators_is_a_noop(self):
        value, lossy, reason = nt.PhoneTransform().apply("0312345678")
        self.assertEqual(value, "0312345678")
        self.assertFalse(lossy)


class TraceTests(unittest.TestCase):
    def test_raw_value_preserved_as_first_step_never_overwritten(self):
        raw = "　株式会社テスト　"
        record = nt.trace(raw, nt.default_chain(), "ja-JP")
        self.assertEqual(record["raw_value"], raw)
        self.assertEqual(record["steps"][0]["transform"], "raw")
        self.assertEqual(record["steps"][0]["after"], raw)
        self.assertEqual(record["steps"][0]["before"], None)
        self.assertFalse(record["steps"][0]["lossy"])
        # Later steps append; the raw entry itself is untouched.
        self.assertEqual(record["steps"][0]["after"], raw)

    def test_default_chain_generic_example(self):
        raw = "　株式会社テスト　"
        record = nt.trace(raw, nt.default_chain(), "ja-JP")
        self.assertEqual(record["matching_key"], "テスト")
        self.assertEqual(len(record["steps"]), 1 + len(nt.DEFAULT_CHAIN))
        for step in record["steps"]:
            self.assertEqual(step["locale_profile"], "ja-JP")

    def test_sensitive_value_is_hashed_not_plaintext(self):
        raw = "株式会社テスト"
        record = nt.trace(raw, [nt.NFKCTransform()], "ja-JP", sensitive=True)
        expected_hash = "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()
        self.assertEqual(record["raw_value"], expected_hash)
        self.assertEqual(record["steps"][0]["after"], expected_hash)
        for step in record["steps"]:
            self.assertNotEqual(step["before"], raw)
            self.assertNotEqual(step["after"], raw)
        # The matching key stays usable (real value): only the audit trail
        # (raw_value / steps) is hashed, since real matching needs the string.
        self.assertEqual(record["matching_key"], raw)

    def test_non_sensitive_value_stays_plaintext(self):
        raw = "テスト"
        record = nt.trace(raw, [nt.NFKCTransform()], "ja-JP", sensitive=False)
        self.assertEqual(record["raw_value"], raw)
        self.assertFalse(record["sensitive"])


class CLITests(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, SCRIPT] + list(args),
            capture_output=True, text=True, check=False,
        )

    def test_default_chain_prints_json_trace(self):
        result = self.run_cli("株式会社テスト", "--locale-profile", "ja-JP")
        self.assertEqual(result.returncode, 0, result.stderr)
        record = json.loads(result.stdout)
        self.assertEqual(record["matching_key"], "テスト")
        self.assertEqual(record["locale_profile"], "ja-JP")

    def test_unknown_transform_is_no_data(self):
        result = self.run_cli("テスト", "--transforms", "not_a_real_transform")
        self.assertEqual(result.returncode, 2)
        self.assertIn("NO-DATA", result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
