#!/usr/bin/env python3
"""H4.c REQ-H-RESTORE: a restore whose git checkout failed is counted as failed.

The module under test is scripts/loop/grade_build.py. The mutation loop
undid each test run's leftovers; a git checkout that exited non zero was
counted as undone, so the next mutation ran on a dirty tree. H4.c makes that
path a NAMED failure: restore_tree_report() returns (undone, failed), and a
non empty failed refuses the next mutation with the paths named. A hostile
argument (a wrong type, an empty or NUL root, a before that is not a set of
strings, a tree state that is not a set of strings, a status line that would
escape the root) raises RestoreRefused, never crashes, never reads as safe.

A failed checkout is exercised with a stub, never with a read only tree: the
fixture is the return code. No subprocess is imported or run here.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import grade_build as G  # noqa: E402


class _Result(object):
    """The only two attributes the restore path reads from a finished call."""

    def __init__(self, returncode, stdout=""):
        self.returncode = returncode
        self.stdout = stdout


class AFailedCheckoutIsCountedAndThePathIsNamed(unittest.TestCase):
    """One named test per property: every mutation of the fix turns one of these red."""

    def setUp(self):
        self.calls = []

    def _status_only(self, argv, **kwargs):
        self.calls.append(list(argv))
        if "status" in argv:
            return _Result(0, " M d.txt\n")
        return _Result(0)

    def _status_and_failed_checkout(self, argv, **kwargs):
        self.calls.append(list(argv))
        if "status" in argv:
            return _Result(0, " M d.txt\n")
        if "checkout" in argv:
            return _Result(1)
        return _Result(0)

    def test_a_failed_checkout_is_counted_and_the_path_is_named(self):
        with mock.patch.object(G.subprocess, "run", side_effect=self._status_and_failed_checkout):
            undone, failed = G.restore_tree_report("/tmp/h4c-nowhere", set())
        self.assertEqual(undone, 0, "a path with a failed checkout cannot count as undone")
        self.assertEqual(list(failed), ["d.txt"], "the failed checkout was not named")
        self.assertTrue(any("checkout" in call for call in self.calls),
                        "no checkout was attempted, so nothing could be counted")

    def test_a_successful_checkout_counts_undone_and_names_nothing(self):
        with mock.patch.object(G.subprocess, "run", side_effect=self._status_only):
            undone, failed = G.restore_tree_report("/tmp/h4c-nowhere", set())
        self.assertEqual(undone, 1)
        self.assertEqual(list(failed), [])

    def test_restore_tree_keeps_its_none_contract_on_a_failed_checkout(self):
        with mock.patch.object(G.subprocess, "run", side_effect=self._status_and_failed_checkout):
            self.assertIsNone(G.restore_tree("/tmp/h4c-nowhere", set()))

    def test_no_state_to_restore_to_is_a_named_failure(self):
        with mock.patch.object(G.subprocess, "run", side_effect=self._status_only):
            undone, failed = G.restore_tree_report("/tmp/h4c-nowhere", None)
        self.assertEqual(undone, 0)
        self.assertEqual(len(failed), 1, "no state to restore to read as the safe case")

    def test_an_unreadable_tree_state_is_a_named_failure(self):
        def always_fails(argv, **kwargs):
            return _Result(1, "")
        with mock.patch.object(G.subprocess, "run", side_effect=always_fails):
            undone, failed = G.restore_tree_report("/tmp/h4c-nowhere", set())
        self.assertEqual(undone, 0)
        self.assertEqual(len(failed), 1, "an unreadable tree state read as the safe case")


class HostileArgumentsAreRefusedNotCrashedOn(unittest.TestCase):
    """REQ-H-RESTORE: unknown or corrupt input BLOCKS, never the safe case."""

    def test_a_corrupt_tree_state_is_refused_not_crashed_on(self):
        for corrupt in (True, False, 0, 1, [], [" M d.txt"], " M d.txt", 2.5):
            with self.subTest(corrupt=corrupt):
                with mock.patch.object(G, "tree_state", return_value=corrupt):
                    with self.assertRaises(G.RestoreRefused):
                        G.restore_tree_report("/tmp/h4c-nowhere", set())

    def test_a_corrupt_tree_state_is_refused_through_restore_tree_too(self):
        for corrupt in (True, 0, [], " M d.txt"):
            with self.subTest(corrupt=corrupt):
                with mock.patch.object(G, "tree_state", return_value=corrupt):
                    with self.assertRaises(G.RestoreRefused):
                        G.restore_tree("/tmp/h4c-nowhere", set())

    def test_a_tree_state_with_a_non_string_element_is_refused(self):
        with mock.patch.object(G, "tree_state", return_value={" M d.txt", 7}):
            with self.assertRaises(G.RestoreRefused):
                G.restore_tree_report("/tmp/h4c-nowhere", set())

    def test_a_hostile_root_is_refused(self):
        nan = float("nan")
        for bad in (None, "", 0, True, [], {}, b"x", nan):
            with self.subTest(root=bad):
                with mock.patch.object(G, "tree_state", return_value=set()):
                    with self.assertRaises(G.RestoreRefused):
                        G.restore_tree_report(bad, set())

    def test_a_hostile_before_is_refused(self):
        nan = float("nan")
        for bad in ("not a set", 0, [], {}, [(" M d.txt",)], nan, b"x"):
            with self.subTest(before=bad):
                with self.assertRaises(G.RestoreRefused):
                    G.restore_tree_report("/tmp/h4c-nowhere", bad)

    def test_a_before_set_with_a_non_string_element_is_refused(self):
        for bad in ({1}, {b"x"}, {None}, {(" M d.txt",)}):
            with self.subTest(before=bad):
                with self.assertRaises(G.RestoreRefused):
                    G.restore_tree_report("/tmp/h4c-nowhere", bad)

    def test_a_tree_state_line_that_escapes_the_root_is_refused(self):
        for escape in ("?? ../../etc/passwd", " M ../outside.txt", "?? /etc/hosts"):
            with self.subTest(escape=escape):
                with mock.patch.object(G, "tree_state", return_value={escape}):
                    with self.assertRaises(G.RestoreRefused):
                        G.restore_tree_report("/tmp/h4c-nowhere", set())


if __name__ == "__main__":
    unittest.main()
