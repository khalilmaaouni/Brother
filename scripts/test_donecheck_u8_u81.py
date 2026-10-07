#!/usr/bin/env python3
"""U8.1's suite: the marketplace checker is right about what it reads, and refuses what it cannot read.

Hermetic by construction: every case builds its own marketplace document in a temp folder, so this passes in an
export copy with an empty HOME and never consults the real repository. The real marketplace file is NOT read and
is NOT edited by U8.1: retiring a published entry is the owner's act, and this suite only proves that the check
reports the state.

usage: python3 -B scripts/test_donecheck_u8_u81.py
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import donecheck_u8 as U


def market(names, path=None):
    """A marketplace file listing exactly these plugin names."""
    p = path or os.path.join(tempfile.mkdtemp(), "marketplace.json")
    with open(p, "w", encoding="utf-8") as fh:
        json.dump({"plugins": [{"name": n} for n in names]}, fh)
    return p


def raw_market(text):
    """A marketplace file holding exactly this raw text, for documents json.dump cannot express."""
    p = os.path.join(tempfile.mkdtemp(), "marketplace.json")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(text)
    return p


class U81MarketplaceCheck(unittest.TestCase):

    def test_only_brother_is_done(self):
        self.assertEqual(U.main([market(["brother"])]), 0)

    def test_extra_entries_block(self):
        self.assertEqual(U.main([market(["brothermode", "brother", "brotherds"])]), 1)

    def test_unreadable_is_no_data(self):
        self.assertEqual(U.main([raw_market("{not json at all")]), 2)

    def test_plugins_refuses_bytes_path(self):
        with self.assertRaises(ValueError):
            U.plugins(b"\xff\xfe")

    def test_plugins_refuses_missing_path_as_value_error(self):
        p = os.path.join(tempfile.mkdtemp(), "sub", "..", "x")
        with self.assertRaises(ValueError):
            U.plugins(p)

    def test_plugins_refuses_non_path_types(self):
        for bad in (None, 123, 1.5, float("nan"), [], {}):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    U.plugins(bad)

    def test_plugins_refuses_entry_without_name(self):
        p = raw_market('{"plugins": [{}]}')
        with self.assertRaises(ValueError):
            U.plugins(p)

    def test_plugins_refuses_entry_name_not_string(self):
        p = raw_market('{"plugins": [{"name": 5}]}')
        with self.assertRaises(ValueError):
            U.plugins(p)

    def test_plugins_refuses_entry_not_object(self):
        p = raw_market('{"plugins": [{"name": "brother"}, "brotherds"]}')
        with self.assertRaises(ValueError):
            U.plugins(p)

    def test_plugins_refuses_duplicate_names(self):
        with self.assertRaises(ValueError):
            U.plugins(market(["brother", "brother"]))

    def test_duplicate_brother_is_no_data(self):
        self.assertEqual(U.main([market(["brother", "brother"])]), 2)

    def test_duplicate_keys_raw_json_is_no_data(self):
        p = raw_market('{"plugins": [{"name": "brother"}], "plugins": [{"name": "brothermode"}]}')
        self.assertEqual(U.main([p]), 2)

    def test_main_refuses_non_list_argv(self):
        for bad in (123, True, 1.5, float("nan"), "path", b"\xff\xfe", {}, (x for x in [])):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    U.main(bad)

    def test_main_refuses_hostile_path_in_argv(self):
        self.assertEqual(U.main([None]), 2)
        self.assertEqual(U.main([123]), 2)
        self.assertEqual(U.main([b"\xff\xfe"]), 2)

    def test_main_returns_no_data_for_missing_file(self):
        self.assertEqual(U.main(["/no/such/marketplace.json"]), 2)

    def test_main_returns_no_data_for_directory(self):
        self.assertEqual(U.main([tempfile.mkdtemp()]), 2)

    def test_main_returns_no_data_for_binary_file(self):
        p = os.path.join(tempfile.mkdtemp(), "marketplace.json")
        with open(p, "wb") as fh:
            fh.write(b"\xff\xfe")
        self.assertEqual(U.main([p]), 2)

    def test_main_returns_no_data_for_empty_file(self):
        self.assertEqual(U.main([raw_market("")]), 2)

    def test_main_returns_no_data_for_unknown_flag(self):
        self.assertEqual(U.main(["--unknown"]), 2)

    def test_plugins_reads_the_names_it_is_given(self):
        self.assertEqual(U.plugins(market(["a", "b"])), ["a", "b"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
