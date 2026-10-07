#!/usr/bin/env python3
"""U8.1's suite: the marketplace checker is right about what it reads, and refuses what it cannot read.

Hermetic by construction: every case builds its own marketplace file in a temp folder, so this passes in an
export copy with an empty HOME and never consults the real repository.

usage: python3 -B scripts/test_donecheck_u8.py
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


class TheMarketplaceCheck(unittest.TestCase):

    def test_only_brother_is_done(self):
        self.assertEqual(U.main([market(["brother"])]), 0)

    def test_extra_entries_block(self):
        self.assertEqual(U.main([market(["brothermode", "brother", "brotherds"])]), 1)

    def test_brother_absent_blocks(self):
        self.assertEqual(U.main([market(["brothermode"])]), 1)

    def test_empty_list_blocks_rather_than_passing(self):
        self.assertEqual(U.main([market([])]), 1)

    def test_unreadable_is_no_data_never_a_pass(self):
        p = os.path.join(tempfile.mkdtemp(), "marketplace.json")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("{not json at all")
        self.assertEqual(U.main([p]), 2)

    def test_absent_file_is_no_data(self):
        self.assertEqual(U.main(["/no/such/marketplace.json"]), 2)

    def test_plugins_not_a_list_is_no_data(self):
        p = os.path.join(tempfile.mkdtemp(), "marketplace.json")
        with open(p, "w", encoding="utf-8") as fh:
            json.dump({"plugins": "brother"}, fh)
        self.assertEqual(U.main([p]), 2)

    def test_an_entry_that_is_not_an_object_is_no_data(self):
        p = os.path.join(tempfile.mkdtemp(), "marketplace.json")
        with open(p, "w", encoding="utf-8") as fh:
            json.dump({"plugins": [{"name": "brother"}, "brotherds"]}, fh)
        self.assertEqual(U.main([p]), 2)

    def test_plugins_reads_the_names_it_is_given(self):
        self.assertEqual(U.plugins(market(["a", "b"])), ["a", "b"])


def tree(catalogs):
    """A throwaway tree holding one marketplace.json per relative directory in `catalogs` (dir -> names)."""
    root = tempfile.mkdtemp()
    for rel, names in catalogs.items():
        d = os.path.join(root, rel)
        os.makedirs(d, exist_ok=True)
        market(names, os.path.join(d, "marketplace.json"))
    return root


class EveryCatalogInTheTree(unittest.TestCase):
    """H1 (attack 2026-09-30 on F10): the cut step read one catalog while the Cursor catalog still published
    three plugins. Handed a directory, the check enumerates EVERY marketplace.json under it (never a list that
    can miss one) and is done only when each of them publishes exactly brother."""

    def test_every_catalog_in_the_end_state_is_done(self):
        root = tree({".claude-plugin": ["brother"], ".cursor-plugin": ["brother"], ".agents/plugins": ["brother"]})
        self.assertEqual(U.main([root]), 0)
        self.assertEqual(sorted(os.path.relpath(c, root) for c in U.catalogs(root)),
                         [".agents/plugins/marketplace.json", ".claude-plugin/marketplace.json",
                          ".cursor-plugin/marketplace.json"])

    def test_one_catalog_left_behind_refuses(self):
        root = tree({".claude-plugin": ["brother"], ".cursor-plugin": ["brother", "brothermode"],
                     ".agents/plugins": ["brother"]})
        self.assertEqual(U.main([root]), 1)

    def test_a_product_catalog_naming_only_its_product_refuses(self):
        root = tree({".claude-plugin": ["brother"], "products/x/.claude-plugin": ["x"]})
        self.assertEqual(U.main([root]), 1)

    def test_one_unreadable_catalog_is_no_data_whatever_the_others_say(self):
        root = tree({".claude-plugin": ["brother"]})
        os.makedirs(os.path.join(root, ".cursor-plugin"))
        with open(os.path.join(root, ".cursor-plugin", "marketplace.json"), "w", encoding="utf-8") as fh:
            fh.write("{broken")
        self.assertEqual(U.main([root]), 2)

    def test_a_tree_with_no_catalog_is_no_data(self):
        self.assertEqual(U.main([tempfile.mkdtemp()]), 2)

    def test_git_internals_are_never_read_as_catalogs(self):
        root = tree({".claude-plugin": ["brother"], ".git/x": ["brothermode"]})
        self.assertEqual(U.main([root]), 0)

    def test_a_nested_worktree_is_another_checkout_and_is_never_read(self):
        """H3 (second attack 2026-09-30): the owner's checkout holds nested worktrees, each with a .git FILE; the
        real cut read their catalogs and refused while the clean rehearsal read READY."""
        root = tree({".claude-plugin": ["brother"], ".claude/worktrees/w1/.claude-plugin": ["brother", "brothermode"]})
        with open(os.path.join(root, ".claude", "worktrees", "w1", ".git"), "w", encoding="utf-8") as fh:
            fh.write("gitdir: /elsewhere\n")
        self.assertEqual(U.main([root]), 0)
        self.assertEqual([os.path.relpath(c, root) for c in U.catalogs(root)], [".claude-plugin/marketplace.json"])

    def test_a_plain_subfolder_without_its_own_git_is_still_read(self):
        """The other direction: only a folder with its own .git is skipped, so a left-behind catalog still refuses."""
        root = tree({".claude-plugin": ["brother"], ".claude/worktrees/w1/.claude-plugin": ["brother", "brothermode"]})
        self.assertEqual(U.main([root]), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
