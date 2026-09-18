#!/usr/bin/env python3
"""Tests for verdict_snapshot.py. Temp dirs and real files only: no network,
no git, since the module itself never shells out to either."""
import os
import tempfile
import unittest

import verdict_snapshot as vs


def write(path, content):
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)


class TakeSnapshot(unittest.TestCase):
    def test_empty_paths_is_no_data(self):
        # Deciding edge: an empty change set cannot bind a verdict to
        # anything, and is far more often a caller bug than a genuine
        # zero-file change, so it refuses rather than binding to nothing.
        with self.assertRaises(vs.SnapshotUnavailable):
            vs.take_snapshot([])

    def test_missing_file_is_no_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = os.path.join(tmp, "nope.txt")
            with self.assertRaises(vs.SnapshotUnavailable):
                vs.take_snapshot([missing])

    def test_hashes_content_not_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = os.path.join(tmp, "a.txt")
            write(a, "same")
            snap = vs.take_snapshot([a])
            self.assertEqual(set(snap["files"]), {a})
            self.assertTrue(snap["files"][a])

    def test_exclude_keeps_receipt_out_of_hashed_set(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "src.py")
            receipt = os.path.join(tmp, "receipt.json")
            write(src, "code")
            write(receipt, "first receipt")
            snap = vs.take_snapshot([src, receipt], exclude=[receipt])
            self.assertEqual(set(snap["files"]), {src})
            self.assertEqual(snap["excluded"], [receipt])


class BindAndIsCurrent(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.src = os.path.join(self.tmp.name, "src.py")
        self.other = os.path.join(self.tmp.name, "other.py")
        self.receipt = os.path.join(self.tmp.name, "receipt.json")
        write(self.src, "v1")
        write(self.other, "unrelated v1")
        write(self.receipt, "receipt v1")

    def bound_record(self, paths=None, exclude=None):
        snap = vs.take_snapshot(paths or [self.src], exclude=exclude or [self.receipt])
        return vs.bind("PASS", snap)

    def test_bind_rejects_unknown_verdict(self):
        snap = vs.take_snapshot([self.src])
        with self.assertRaises(ValueError):
            vs.bind("MAYBE", snap)

    def test_current_when_nothing_changed(self):
        record = self.bound_record()
        snap_now = vs.take_snapshot([self.src], exclude=[self.receipt])
        self.assertEqual(vs.is_current(record, snap_now), ("CURRENT", None))

    def test_stale_names_the_changed_tested_file(self):
        record = self.bound_record()
        write(self.src, "v2")  # the tested file itself changes after binding
        snap_now = vs.take_snapshot([self.src], exclude=[self.receipt])
        status, path = vs.is_current(record, snap_now)
        self.assertEqual(status, "STALE")
        self.assertEqual(path, self.src)

    def test_file_added_after_binding_is_stale(self):
        record = self.bound_record(paths=[self.src])
        added = os.path.join(self.tmp.name, "added.py")
        write(added, "new file")
        snap_now = vs.take_snapshot([self.src, added], exclude=[self.receipt])
        status, path = vs.is_current(record, snap_now)
        self.assertEqual(status, "STALE")
        self.assertEqual(path, added)

    def test_file_deleted_after_binding_is_stale(self):
        record = self.bound_record(paths=[self.src, self.other])
        # "deleted" as seen by is_current: it simply is not in the fresh
        # snapshot's file set, whatever caused that (take_snapshot itself
        # would refuse with NO-DATA if asked to re-hash a vanished path;
        # this exercises is_current's own half of the contract directly).
        snap_now = {"revision": None, "files": {self.src: record["files"][self.src]},
                    "excluded": []}
        status, path = vs.is_current(record, snap_now)
        self.assertEqual(status, "STALE")
        self.assertEqual(path, self.other)

    def test_identical_content_at_a_different_path_is_stale(self):
        # A naive "does this hash exist anywhere" check would wave this
        # through as unchanged. Keying on path, not content, is the point.
        record = self.bound_record(paths=[self.src])
        moved = os.path.join(self.tmp.name, "moved.py")
        os.rename(self.src, moved)
        snap_now = vs.take_snapshot([moved], exclude=[self.receipt])
        status, path = vs.is_current(record, snap_now)
        self.assertEqual(status, "STALE")
        # Both the vanished old path and the new path are real differences;
        # sorted order decides which is reported first, but it must be one
        # of the two, never neither.
        self.assertIn(path, (self.src, moved))

    def test_change_outside_the_tested_change_does_not_stale(self):
        record = self.bound_record(paths=[self.src])  # other.py never included
        write(self.other, "unrelated v2")  # changes, but caller never re-includes it
        snap_now = vs.take_snapshot([self.src], exclude=[self.receipt])
        self.assertEqual(vs.is_current(record, snap_now), ("CURRENT", None))

    def test_receipt_changing_does_not_stale(self):
        record = self.bound_record()
        write(self.receipt, "receipt v2 written after the verdict was recorded")
        snap_now = vs.take_snapshot([self.src], exclude=[self.receipt])
        self.assertEqual(vs.is_current(record, snap_now), ("CURRENT", None))

    def test_is_current_rejects_malformed_input(self):
        with self.assertRaises(ValueError):
            vs.is_current({"not": "a snapshot"}, {"files": {}})


class Cli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.src = os.path.join(self.tmp.name, "src.py")
        self.record = os.path.join(self.tmp.name, "record.json")
        write(self.src, "v1")

    def test_bind_then_check_current_is_exit_0(self):
        self.assertEqual(
            vs.main(["bind", "--verdict", "PASS", "--paths", self.src, "--out", self.record]),
            0)
        self.assertEqual(vs.main(["check", "--record", self.record, "--paths", self.src]), 0)

    def test_check_after_change_is_exit_1(self):
        vs.main(["bind", "--verdict", "PASS", "--paths", self.src, "--out", self.record])
        write(self.src, "v2")
        self.assertEqual(vs.main(["check", "--record", self.record, "--paths", self.src]), 1)

    def test_check_on_vanished_path_is_exit_2(self):
        vs.main(["bind", "--verdict", "PASS", "--paths", self.src, "--out", self.record])
        os.remove(self.src)
        self.assertEqual(vs.main(["check", "--record", self.record, "--paths", self.src]), 2)

    def test_bind_on_empty_change_is_exit_2(self):
        # argparse nargs="+" needs at least one token; use a path that will
        # be excluded, leaving an effectively empty hashed set instead.
        self.assertEqual(
            vs.main(["bind", "--verdict", "PASS", "--paths", self.src, "--exclude", self.src,
                     "--out", self.record]),
            2)


if __name__ == "__main__":
    unittest.main()
