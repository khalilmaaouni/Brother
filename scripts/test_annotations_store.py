"""What the annotations store must keep true.

The whole point of "annotate to remember" is that a correction survives past
the one record it was typed on. These tests drive the store's actual
persistence and its dedupe rule, using a temp directory as ROOT so nothing
here ever touches the project's real docs/decisions/annotations.json.
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import annotations_store as AS  # noqa: E402


class Roundtrip(unittest.TestCase):
    def test_an_empty_store_loads_as_an_empty_list(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(AS.load_annotations(d), [])

    def test_save_then_load_roundtrips(self):
        with tempfile.TemporaryDirectory() as d:
            AS.save_annotations(d, [{"id": "a1", "option": "x"}])
            self.assertEqual(AS.load_annotations(d), [{"id": "a1", "option": "x"}])

    def test_a_malformed_store_file_loads_as_empty_not_a_crash(self):
        with tempfile.TemporaryDirectory() as d:
            path = AS.store_path(d)
            os.makedirs(os.path.dirname(path))
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("{ not json")
            self.assertEqual(AS.load_annotations(d), [])


class AddFromRecord(unittest.TestCase):
    def record(self, annotations):
        return {"title": "T", "annotations": annotations}

    def write_record(self, tmp, annotations):
        path = os.path.join(tmp, "r.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.record(annotations), fh)
        return path

    def test_adding_a_records_annotations_lands_in_the_store(self):
        with tempfile.TemporaryDirectory() as d:
            path = self.write_record(d, [{"option": "A", "criterion": "tokens",
                                          "note": "was a guess", "persona": "lead",
                                          "time": "2026-09-06T00:00:00Z"}])
            added, skipped, error = AS.add_from_record(d, path)
            self.assertIsNone(error)
            self.assertEqual((added, skipped), (1, 0))
            entries = AS.load_annotations(d)
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["at"], "2026-09-06T00:00:00Z")
            self.assertEqual(entries[0]["record"], "r.json")

    def test_dedupes_on_option_criterion_note(self):
        with tempfile.TemporaryDirectory() as d:
            path = self.write_record(d, [{"option": "A", "criterion": "tokens",
                                          "note": "same fix", "persona": "lead",
                                          "time": "t1"}])
            AS.add_from_record(d, path)
            added, skipped, _err = AS.add_from_record(d, path)
            self.assertEqual((added, skipped), (0, 1))
            self.assertEqual(len(AS.load_annotations(d)), 1)

    def test_a_record_with_no_annotations_adds_nothing_and_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as d:
            path = self.write_record(d, [])
            added, skipped, error = AS.add_from_record(d, path)
            self.assertEqual((added, skipped, error), (0, 0, None))

    def test_an_unreadable_record_is_NO_DATA(self):
        with tempfile.TemporaryDirectory() as d:
            added, skipped, error = AS.add_from_record(d, os.path.join(d, "nope.json"))
            self.assertIsNotNone(error)

    def test_ids_are_assigned_and_increment(self):
        with tempfile.TemporaryDirectory() as d:
            path = self.write_record(d, [
                {"option": "A", "criterion": "a", "note": "n1"},
                {"option": "A", "criterion": "b", "note": "n2"},
            ])
            AS.add_from_record(d, path)
            entries = AS.load_annotations(d)
            self.assertEqual([e["id"] for e in entries], ["a1", "a2"])


class Remove(unittest.TestCase):
    def test_removing_an_existing_id_returns_true_and_drops_it(self):
        with tempfile.TemporaryDirectory() as d:
            AS.save_annotations(d, [{"id": "a1"}, {"id": "a2"}])
            self.assertTrue(AS.remove(d, "a1"))
            self.assertEqual([e["id"] for e in AS.load_annotations(d)], ["a2"])

    def test_removing_an_unknown_id_returns_false(self):
        with tempfile.TemporaryDirectory() as d:
            AS.save_annotations(d, [{"id": "a1"}])
            self.assertFalse(AS.remove(d, "zzz"))


class AMalformedStoreIsNeverRewritten(unittest.TestCase):
    """OpenRouter review lane L7, reproduced 2026-09-11: a hand-edited store
    with one JSON typo loaded as [], so the next add wrote a one-entry file
    over every stored correction and still reported success."""

    def malformed(self, d):
        path = AS.store_path(d)
        os.makedirs(os.path.dirname(path))
        text = '[{"id": "a1", "option": "A"}, {"id": "a2", "option": "B"},]'
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path, text

    def test_add_refuses_and_leaves_the_file_byte_identical(self):
        with tempfile.TemporaryDirectory() as d:
            path, text = self.malformed(d)
            rec = os.path.join(d, "r.json")
            with open(rec, "w", encoding="utf-8") as fh:
                json.dump({"annotations": [{"option": "C", "criterion": "k",
                                            "note": "n"}]}, fh)
            added, skipped, error = AS.add_from_record(d, rec)
            self.assertEqual((added, skipped), (0, 0))
            self.assertIsNotNone(error)
            with open(path, encoding="utf-8") as fh:
                self.assertEqual(fh.read(), text)

    def test_remove_refuses_and_leaves_the_file_byte_identical(self):
        with tempfile.TemporaryDirectory() as d:
            path, text = self.malformed(d)
            real_root = AS.ROOT
            AS.ROOT = d
            try:
                self.assertEqual(AS.main(["remove", "a1"]), 2)
            finally:
                AS.ROOT = real_root
            with open(path, encoding="utf-8") as fh:
                self.assertEqual(fh.read(), text)

    def test_a_missing_store_still_accepts_the_first_add(self):
        with tempfile.TemporaryDirectory() as d:
            rec = os.path.join(d, "r.json")
            with open(rec, "w", encoding="utf-8") as fh:
                json.dump({"annotations": [{"option": "C", "criterion": "k",
                                            "note": "n"}]}, fh)
            self.assertEqual(AS.add_from_record(d, rec), (1, 0, None))


class AnnotationFor(unittest.TestCase):
    """The render-side lookup: matched on option and criterion alone, never
    on which record an entry first came from."""

    def test_matches_on_option_and_criterion_regardless_of_record(self):
        entries = [{"option": "A", "criterion": "tokens", "note": "n", "record": "other.json"}]
        found = AS.annotation_for(entries, "A", "tokens")
        self.assertEqual(found["note"], "n")

    def test_no_match_returns_none(self):
        entries = [{"option": "A", "criterion": "tokens", "note": "n"}]
        self.assertIsNone(AS.annotation_for(entries, "A", "turns"))

    def test_the_most_recently_added_entry_wins(self):
        entries = [{"option": "A", "criterion": "tokens", "note": "old"},
                   {"option": "A", "criterion": "tokens", "note": "new"}]
        self.assertEqual(AS.annotation_for(entries, "A", "tokens")["note"], "new")


class TheCLI(unittest.TestCase):
    def test_list_on_an_empty_store_is_NO_DATA(self):
        with tempfile.TemporaryDirectory() as d:
            cwd = os.getcwd()
            AS.ROOT = d  # ponytail: swap the module-level ROOT for this call only
            try:
                self.assertEqual(AS.main(["list"]), 2)
            finally:
                AS.ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            os.chdir(cwd)

    def test_remove_of_unknown_id_is_NO_DATA(self):
        with tempfile.TemporaryDirectory() as d:
            real_root = AS.ROOT
            AS.ROOT = d
            try:
                self.assertEqual(AS.main(["remove", "zzz"]), 2)
            finally:
                AS.ROOT = real_root


if __name__ == "__main__":
    unittest.main()
