#!/usr/bin/env python3
"""Tests for the autonomy block (the screen, not the policy). Structure and
imports mirror scripts/test_autonomy_dial.py, the sibling that tests the
module this one renders: plain unittest, sys.path insert off this file's own
directory, one class per question, no framework.

The question these tests exist to answer is HONESTY. A block that prints a
confident sentence over a fail-open fence is worse than no block, so the
fail-open fence, the enforcing fence, the unrecognized mode and an
unreadable policy each have a test that reads the rendered text. No em or en
dashes."""

import contextlib
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import autonomy_block
from autonomy_block import block_data, render, main, NO_DATA, EXIT_NO_DATA
from autonomy_dial import ORDER, ACTIONS, A3_FLAGS, DEFAULT_DIAL

#: A stock machine: neither variable set. Passed explicitly rather than read
#: from os.environ, because the shell that runs these tests may itself export
#: BM_FENCE_MODE, and a test whose result depends on the tester's shell
#: proves nothing about the code.
STOCK = {}


def run_main(argv):
    """main(), with its printing swallowed so a suite log carries the test
    result rather than three copies of the block. The EXIT CODE is what these
    tests assert on; a printed verdict is not a verdict."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = main(argv)
    return code, buf.getvalue()


class TestDialRenders(unittest.TestCase):

    def test_every_dial_level_renders_its_own_setting(self):
        for level in ORDER:
            with self.subTest(dial=level):
                text = render({"BROTHER_AUTONOMY_DIAL": level})
                self.assertIn("Dial: %s" % level, text)
                self.assertIn("BROTHER_AUTONOMY_DIAL=%s" % level, text)

    def test_unset_dial_says_default_not_most_permissive(self):
        text = render(STOCK)
        self.assertIn("Dial: %s" % DEFAULT_DIAL, text)
        self.assertIn("unset, default %s" % DEFAULT_DIAL, text)

    def test_the_seven_boundary_names_are_all_on_the_screen(self):
        text = render(STOCK)
        for flag in A3_FLAGS:
            with self.subTest(flag=flag):
                self.assertIn(flag, text)

    def test_a3_refuses_at_the_most_permissive_dial(self):
        data = block_data({"BROTHER_AUTONOMY_DIAL": "A0"})
        self.assertEqual(data["dial"]["decisions"]["A3"],
                         ACTIONS["A3"])
        self.assertIn(ACTIONS["A3"], render(data=data))

    def test_the_dial_adds_ceremony_and_never_removes_it(self):
        # At A2 an action whose own class is A0 must be shown asking, not
        # continuing: the screen shows the effective decision, not the
        # action's own class.
        data = block_data({"BROTHER_AUTONOMY_DIAL": "A2"})
        self.assertEqual(data["dial"]["decisions"]["A0"], ACTIONS["A2"])


class TestFenceHonesty(unittest.TestCase):

    def test_fail_open_fence_says_recording_only(self):
        text = render(STOCK)
        self.assertIn("RECORDING ONLY", text)
        self.assertIn("recorded, not refused", text)
        self.assertIn("BM_FENCE_MODE=enforced", text)
        self.assertNotIn("REFUSING", text)

    def test_explicit_advisory_is_also_recording_only(self):
        text = render({"BM_FENCE_MODE": "advisory"})
        self.assertIn("RECORDING ONLY", text)

    def test_enforcing_fence_says_refusing(self):
        text = render({"BM_FENCE_MODE": "enforced"})
        self.assertIn("REFUSING", text)
        self.assertNotIn("RECORDING ONLY", text)

    def test_unrecognized_mode_shows_the_hooks_own_warning(self):
        # bm_fence_hook runs advisory on a typo and warns; the block must
        # carry both halves rather than picking the cheerful one.
        text = render({"BM_FENCE_MODE": "enfroced"})
        self.assertIn("RECORDING ONLY", text)
        self.assertIn("not a recognized mode", text)

    def test_an_unreadable_fence_is_no_data_not_a_verdict(self):
        original = autonomy_block._fence_module
        autonomy_block._fence_module = lambda: (None, "fixture: no fence")
        try:
            data = block_data(STOCK)
            text = render(data=data)
        finally:
            autonomy_block._fence_module = original
        self.assertIsNone(data["fence"]["enforcing"])
        self.assertIn("Fence: %s" % NO_DATA, text)
        self.assertNotIn("REFUSING", text)
        self.assertNotIn("RECORDING ONLY", text)


class TestUnreadablePolicy(unittest.TestCase):

    def test_no_policy_is_no_data_never_a_cheerful_default(self):
        original = autonomy_block._dial_module
        autonomy_block._dial_module = lambda: (None, "fixture: no policy")
        try:
            data = block_data(STOCK)
            text = render(data=data)
        finally:
            autonomy_block._dial_module = original
        self.assertTrue(data["no_data"])
        self.assertIsNone(data["dial"])
        self.assertIn(NO_DATA, text)
        self.assertNotIn("Dial: A", text)
        for flag in A3_FLAGS:
            self.assertNotIn(flag, text)

    def test_no_policy_exits_non_zero(self):
        original = autonomy_block._dial_module
        autonomy_block._dial_module = lambda: (None, "fixture: no policy")
        try:
            code, printed = run_main([])
        finally:
            autonomy_block._dial_module = original
        self.assertEqual(code, EXIT_NO_DATA)
        self.assertNotEqual(code, 0)
        self.assertIn(NO_DATA, printed)

    def test_an_unreadable_scope_audit_is_no_data_on_its_own_line(self):
        original = autonomy_block._scope_module
        autonomy_block._scope_module = lambda: (None, "fixture: no audit")
        try:
            data = block_data(STOCK)
            text = render(data=data)
        finally:
            autonomy_block._scope_module = original
        self.assertIsNone(data["no_data"])
        self.assertIn("After the run: %s" % NO_DATA, text)


class TestCallableShapes(unittest.TestCase):

    def test_render_returns_a_string_and_prints_nothing(self):
        text = render(STOCK)
        self.assertIsInstance(text, str)
        self.assertTrue(text.startswith("Autonomy,"))

    def test_standalone_run_exits_zero_when_the_policy_reads(self):
        code, printed = run_main([])
        self.assertEqual(code, 0)
        self.assertIn("Autonomy,", printed)

    def test_json_carries_the_same_values_as_the_text(self):
        code, printed = run_main(["--json"])
        self.assertEqual(code, 0)
        self.assertIn('"always_refused"', printed)
        data = block_data({"BROTHER_AUTONOMY_DIAL": "A2",
                           "BM_FENCE_MODE": "enforced"})
        self.assertEqual(data["dial"]["dial"], "A2")
        self.assertTrue(data["fence"]["enforcing"])
        self.assertEqual(data["dial"]["always_refused"], list(A3_FLAGS))

    def test_no_dashes_in_the_rendered_block(self):
        for env in (STOCK, {"BM_FENCE_MODE": "enforced"},
                    {"BM_FENCE_MODE": "enfroced"}):
            with self.subTest(env=env):
                text = render(env)
                self.assertNotIn(chr(0x2014), text)  # em dash
                self.assertNotIn(chr(0x2013), text)  # en dash


if __name__ == "__main__":
    unittest.main()
