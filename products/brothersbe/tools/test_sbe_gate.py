#!/usr/bin/env python3
"""I33.a: an unreadable receipt binding is reported as a FAIL, never dropped.

`sbe_gate._receipt_still_binds` reads the receipt freshness rule out of
`brothersbe.evidence`. Before this change its handler swallowed a failed read
and answered `False`, and the read failure itself was invisible: the caller
could only read the answer as "the receipt does not bind". It now answers a
FAIL problem string carrying the exception's own text, so a run under
`--strict` blocks on a binding it could not read instead of exiting 0. This
class pins that answer, and pins the NO-DATA marker the FAIL string must never
wear.
"""
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SBE_GATE_PATH = os.path.join(HERE, "sbe_gate.py")
# The exact FAIL sentence the fixed clause answers with, so a drift in that
# sentence is a red test rather than a quietly different verdict.
I33_BINDING_PREFIX = "FAIL: the receipt's binding could not be read: "


class I33AUnreadableReceiptIsReportedNotDropped(unittest.TestCase):
    """An unreadable receipt binding yields a FAIL problem, never None."""

    def _unreadable_binding(self):
        """The problem an unreadable binding answers with; the module under test is kept on self.sbe_gate.

        One value, not a (module, problem) pair: the no data class lint reads any 2-tuple return in tools/ as a
        possible (verdict, evidence) producer, and this helper is not one (found 2026-10-22, when the pair failed
        no-data-class-lints on the release line).

        The scratch directory is a throwaway that is not a git work tree:
        `GIT_CEILING_DIRECTORIES` is its parent and `HOME` is the directory
        itself, so git discovers no repository above it whatever the ambient
        temporary directory happens to be. The binding read itself is then made
        to fail on purpose, by handing the reader a raising answer: what this
        class pins is the handler's own answer to a read that failed, and a
        fixture that leaned on the ambient git, the ambient HOME or the
        reader's own leniency about a directory that is not a work tree would
        pass here and fail beside a real checkout. Nothing outside the scratch
        directory is read or written.
        """
        self.assertTrue(os.path.isfile(SBE_GATE_PATH),
                        "sbe_gate.py must sit beside this test: %s" % SBE_GATE_PATH)
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, True)
        full = os.path.join(root, "numbers-manifest.json")
        if HERE not in sys.path:
            sys.path.insert(0, HERE)
        import sbe_gate
        from brothersbe import evidence as evidence_mod
        env = {"GIT_CEILING_DIRECTORIES": os.path.dirname(root), "HOME": root}
        with mock.patch.dict(os.environ, env):
            with mock.patch.object(evidence_mod, "commit_binding_holds",
                                   side_effect=OSError("the HEAD read failed")):
                problem = sbe_gate._receipt_still_binds({"headCommit": "a" * 40}, root, full)
        self.sbe_gate = sbe_gate
        return problem

    def test_a_record_that_cannot_be_read_is_reported_not_dropped(self) -> None:
        problem = self._unreadable_binding()
        self.assertIsNotNone(problem)
        self.assertTrue(problem.startswith(I33_BINDING_PREFIX),
                        "the problem must carry the FAIL sentence: %r" % (problem,))
        self.assertGreater(len(problem), len(I33_BINDING_PREFIX),
                           "the exception's own text must be present: %r" % (problem,))

    def test_the_problem_is_fail_shaped_so_a_strict_run_blocks_it(self) -> None:
        problem = self._unreadable_binding()
        sbe_gate = self.sbe_gate
        self.assertIsNotNone(problem)
        self.assertTrue(problem.startswith("FAIL: "),
                        "a strict run must read this as a FAIL: %r" % (problem,))
        self.assertFalse(problem.startswith(sbe_gate.UNANSWERED),
                         "the FAIL string must not wear the NO-DATA marker: %r" % (problem,))
        self.assertEqual(sbe_gate.UNANSWERED, "NO-DATA: ",
                         "the NO-DATA marker the classification hinges on moved")


if __name__ == "__main__":
    unittest.main()
