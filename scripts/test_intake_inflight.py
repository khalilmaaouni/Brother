"""What the decision-in-progress file must keep true.

open/rewrite/close is a small state machine with two failure modes worth
guarding: reopening (or double-closing) something already in flight silently
discards its history, and closing onto an existing landed record silently
overwrites a decision. Both are tested here by monkeypatching this module's
own directory constants onto a temp tree, so nothing touches the project's
real docs/decisions/.
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import intake_inflight as I  # noqa: E402


class WithTempDirs(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._real_inflight = I.INFLIGHT_DIR
        self._real_decisions = I.DECISIONS_DIR
        I.INFLIGHT_DIR = os.path.join(self._tmp.name, "inflight")
        I.DECISIONS_DIR = os.path.join(self._tmp.name, "decisions")

    def tearDown(self):
        I.INFLIGHT_DIR = self._real_inflight
        I.DECISIONS_DIR = self._real_decisions
        self._tmp.cleanup()


class OpenRewriteClose(WithTempDirs):
    def test_open_writes_one_stamp_at_turn_1(self):
        record, error = I.open_inflight("my-decision")
        self.assertIsNone(error)
        self.assertEqual(len(record["history"]), 1)
        self.assertEqual(record["history"][0]["turn"], 1)
        self.assertTrue(os.path.isfile(I._path("my-decision")))

    def test_open_seeds_from_a_draft(self):
        with tempfile.TemporaryDirectory() as d:
            draft_path = os.path.join(d, "draft.json")
            with open(draft_path, "w", encoding="utf-8") as fh:
                json.dump({"title": "T"}, fh)
            record, error = I.open_inflight("seeded", draft_path)
            self.assertIsNone(error)
            self.assertEqual(record["title"], "T")

    def test_opening_an_already_open_slug_refuses(self):
        I.open_inflight("dup")
        record, error = I.open_inflight("dup")
        self.assertIsNone(record)
        self.assertIsNotNone(error)

    def test_rewrite_appends_a_stamp_and_increments_turn(self):
        I.open_inflight("r1")
        record, error = I.rewrite_inflight("r1", "second turn")
        self.assertIsNone(error)
        self.assertEqual(len(record["history"]), 2)
        self.assertEqual(record["history"][1]["turn"], 2)
        self.assertEqual(record["history"][1]["note"], "second turn")

    def test_rewrite_twice_lands_three_stamps(self):
        I.open_inflight("r2")
        I.rewrite_inflight("r2", "turn two")
        record, _err = I.rewrite_inflight("r2", "turn three")
        self.assertEqual(len(record["history"]), 3)
        self.assertEqual([h["turn"] for h in record["history"]], [1, 2, 3])

    def test_rewrite_merges_a_patchs_top_level_keys(self):
        I.open_inflight("r3")
        with tempfile.TemporaryDirectory() as d:
            patch_path = os.path.join(d, "patch.json")
            with open(patch_path, "w", encoding="utf-8") as fh:
                json.dump({"title": "New title"}, fh)
            record, _err = I.rewrite_inflight("r3", "renamed", patch_path)
            self.assertEqual(record["title"], "New title")

    def test_rewriting_something_not_open_refuses(self):
        record, error = I.rewrite_inflight("never-opened", "x")
        self.assertIsNone(record)
        self.assertIsNotNone(error)

    def test_close_moves_the_file_and_removes_the_inflight_copy(self):
        I.open_inflight("c1")
        I.rewrite_inflight("c1", "turn two")
        dest, error = I.close_inflight("c1")
        self.assertIsNone(error)
        self.assertTrue(os.path.isfile(dest))
        self.assertFalse(os.path.isfile(I._path("c1")))
        with open(dest, encoding="utf-8") as fh:
            landed = json.load(fh)
        self.assertEqual(len(landed["history"]), 2)

    def test_close_refuses_to_overwrite_an_existing_record(self):
        os.makedirs(I.DECISIONS_DIR)
        dest = os.path.join(I.DECISIONS_DIR, "taken.json")
        with open(dest, "w", encoding="utf-8") as fh:
            json.dump({"already": "here"}, fh)
        I.open_inflight("taken")
        result, error = I.close_inflight("taken")
        self.assertIsNone(result)
        self.assertIsNotNone(error)
        with open(dest, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), {"already": "here"})

    def test_closing_something_not_open_refuses(self):
        dest, error = I.close_inflight("never-opened")
        self.assertIsNone(dest)
        self.assertIsNotNone(error)


class TheCLIExitsCorrectly(WithTempDirs):
    def test_open_exits_0(self):
        self.assertEqual(I.main(["open", "cli1"]), 0)

    def test_reopen_exits_2(self):
        I.main(["open", "cli2"])
        self.assertEqual(I.main(["open", "cli2"]), 2)

    def test_rewrite_exits_0(self):
        I.main(["open", "cli3"])
        self.assertEqual(I.main(["rewrite", "cli3", "--note", "n"]), 0)

    def test_close_exits_0(self):
        I.main(["open", "cli4"])
        self.assertEqual(I.main(["close", "cli4"]), 0)

    def test_close_of_unopened_exits_2(self):
        self.assertEqual(I.main(["close", "cli5"]), 2)


if __name__ == "__main__":
    unittest.main()
