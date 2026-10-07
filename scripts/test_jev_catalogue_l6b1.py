"""Tests for L6b.1 contracts and parsing."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import jev_catalogue_l6b1 as jc

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CATALOGUE_PATH = os.path.join(REPO_ROOT, "docs", "plan", "JEV-USE-CASE-CATALOGUE.md")

def _read_catalogue_bytes():
    with open(CATALOGUE_PATH, "rb") as f:
        return f.read()

def _base_entry():
    return {
        "nn": 11,
        "qid": "vault_manifest_valid_json",
        "domain": "Vault",
        "state_cmd": "cmd",
        "state_output_verbatim": "out",
        "question": "q?",
        "true_criteria": "t",
        "false_criteria": "f",
        "noul_value": 0.5,
        "holder_id": "jev-catalogue-11-vault-manifest",
        "reservation_id": "jev-catalogue-11-vault-manifest-1790000000000-1001",
    }

needs_live_catalogue = unittest.skipUnless(os.path.isfile(CATALOGUE_PATH), 'the live catalogue is a plan document the export tree does not ship')


class TestL6b1(unittest.TestCase):
    @needs_live_catalogue
    def test_the_live_catalogue_meets_the_units_own_bar(self):
        """Was test_marker_count_matches_seed, and it asserted the LIVE catalogue held exactly 10 entries.

        That is a test pinned to the BAD state: L6b's whole goal is a catalogue of 70 or more, so this was
        guaranteed to go red at the exact moment the unit succeeded, and it did (AssertionError: 70 != 10). It
        also sat in scripts/check_all.sh, so one unfinished unit's inverted test refused every landing on the
        branch. Corrected 2026-09-21 to assert the bar rather than the shortfall. It still goes red if entries
        are deleted, so it has not been weakened into something that cannot fail."""
        data = _read_catalogue_bytes()
        text = data.decode("utf-8")
        self.assertGreaterEqual(jc.count_markers(text), 70)

    @needs_live_catalogue
    def test_unique_ids_match_markers(self):
        data = _read_catalogue_bytes()
        text = data.decode("utf-8")
        # The real invariant, and it holds at ANY count: every marker carries its own distinct reservation id.
        self.assertEqual(jc.recount_unique_ids(text), jc.count_markers(text))
        # The bar, not the shortfall. This read == 10 and went red when the unit succeeded; see the note above.
        self.assertGreaterEqual(jc.recount_unique_ids(text), 70)

    def test_validate_rejects_missing_field(self):
        entry = _base_entry()
        del entry["noul_value"]
        self.assertFalse(jc.validate_entry(entry))
        entry = _base_entry()
        del entry["nn"]
        self.assertFalse(jc.validate_entry(entry))
        self.assertTrue(jc.validate_entry(_base_entry()))

    def test_reservation_regex_rejects_bad(self):
        self.assertEqual(jc.extract_reservation_id("not-an-id"), "")
        self.assertEqual(jc.extract_reservation_id("jev-catalogue-1-name-123-456"), "")
        self.assertEqual(jc.extract_reservation_id("jev-catalogue-01-Name-123-456"), "")
        self.assertEqual(jc.extract_reservation_id("jev-catalogue-01-name-123"), "")

    def test_extract_reservation_id_accepts_valid(self):
        text = "Ledger: `jev-catalogue-11-vault-manifest-1790000000000-1001`"
        self.assertEqual(
            jc.extract_reservation_id(text),
            "jev-catalogue-11-vault-manifest-1790000000000-1001",
        )

    def test_recount_unique_ids_blocks_duplicate(self):
        text = (
            "Ledger: `jev-catalogue-11-a-1-1`\n"
            "Ledger: `jev-catalogue-11-a-1-1`\n"
        )
        self.assertEqual(jc.count_markers(text), 2)
        self.assertEqual(jc.recount_unique_ids(text), 1)

    def test_validate_rejects_out_of_range_noul(self):
        entry = _base_entry()
        entry["noul_value"] = 1.5
        self.assertFalse(jc.validate_entry(entry))

    def test_validate_rejects_bad_domain(self):
        entry = _base_entry()
        entry["domain"] = "Other"
        self.assertFalse(jc.validate_entry(entry))

    def test_validate_rejects_bad_holder(self):
        entry = _base_entry()
        entry["holder_id"] = "bad-holder"
        self.assertFalse(jc.validate_entry(entry))

    def test_validate_rejects_bad_nn(self):
        for bad in (10, 1000, True, "11", None):
            entry = _base_entry()
            entry["nn"] = bad
            self.assertFalse(jc.validate_entry(entry))

    def test_validate_rejects_uppercase_qid(self):
        entry = _base_entry()
        entry["qid"] = "Vault_Manifest"
        self.assertFalse(jc.validate_entry(entry))

    def test_validate_rejects_path_shaped_qid(self):
        entry = _base_entry()
        entry["qid"] = "vault/manifest"
        self.assertFalse(jc.validate_entry(entry))

    def test_validate_rejects_unhashable_key(self):
        entry = _base_entry()
        entry["domain"] = ["Vault"]
        self.assertFalse(jc.validate_entry(entry))
        entry = _base_entry()
        entry["verdict"] = ["yes"]
        self.assertFalse(jc.validate_entry(entry))
        entry = _base_entry()
        entry["qid"] = ["vault"]
        self.assertFalse(jc.validate_entry(entry))

    def test_validate_rejects_hostile_input(self):
        for bad in (None, [], 42, True, b"bytes"):
            self.assertFalse(jc.validate_entry(bad))

    def test_count_markers_refuses_non_text(self):
        for bad in (None, 42, [], {}, b"bytes", float("inf"), (x for x in [])):
            with self.assertRaises(ValueError):
                jc.count_markers(bad)
        self.assertEqual(jc.count_markers(""), 0)
        self.assertEqual(jc.count_markers("ledger: `jev-catalogue-11-a-1-1`"), 0)

    def test_recount_unique_ids_refuses_non_text(self):
        for bad in (None, 42, [], {}, b"bytes", float("inf"), (x for x in [])):
            with self.assertRaises(ValueError):
                jc.recount_unique_ids(bad)
        self.assertEqual(jc.recount_unique_ids(""), 0)
        self.assertEqual(jc.recount_unique_ids("ledger: `jev-catalogue-11-a-1-1`"), 0)

    def test_extract_reservation_id_refuses_non_text(self):
        self.assertEqual(jc.extract_reservation_id(None), "")
        self.assertEqual(jc.extract_reservation_id(42), "")
        self.assertEqual(jc.extract_reservation_id(b"bytes"), "")

if __name__ == "__main__":
    unittest.main()
