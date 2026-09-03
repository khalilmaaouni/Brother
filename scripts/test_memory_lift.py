"""What the memory measurement must keep honest.

A measurement tool is the last place a NO-DATA may quietly become a zero, because
a zero reads as "memory did nothing" and a NO-DATA reads as "nobody looked", and
those two sentences lead to opposite decisions.
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import memory_lift as M  # noqa: E402


class FakeGuard:
    """Matches a lesson when the text contains 'needle'. Small on purpose: the
    thing under test is the before/after arithmetic, not somebody's matcher."""
    @staticmethod
    def matching_lessons(text):
        return [{"trigger": "needle"}] * str(text).lower().count("needle")


def transcript(blocks):
    fh = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False,
                                     encoding="utf-8")
    for b in blocks:
        fh.write(json.dumps({"message": {"content": [b]}}) + "\n")
    fh.close()
    return fh.name


def write_block(path, content, tool="Write", key="content"):
    return {"type": "tool_use", "name": tool,
            "input": {"file_path": path, key: content}}


class TheArithmeticIsRealNotAsserted(unittest.TestCase):
    def test_content_only_matches_count_as_lift(self):
        rows = [("Write", "/a/b.py", "this body has a needle in it")]
        before, after, lifted = M.measure(FakeGuard, rows)
        self.assertEqual((before, after), (0, 1))
        self.assertEqual(lifted, [("/a/b.py", 1)])

    def test_a_match_already_visible_in_the_path_is_NOT_lift(self):
        """The honesty test. A lesson the old matcher already saw must not be
        counted as something the fix delivered."""
        rows = [("Write", "/a/needle.py", "body with no trigger")]
        before, after, lifted = M.measure(FakeGuard, rows)
        self.assertEqual((before, after), (1, 1))
        self.assertEqual(lifted, [])

    def test_lift_is_the_difference_and_never_the_total(self):
        rows = [("Write", "/a/needle.py", "needle needle")]
        before, after, _ = M.measure(FakeGuard, rows)
        self.assertEqual(before, 1)
        self.assertEqual(after, 3)

    def test_an_empty_body_changes_nothing(self):
        rows = [("Edit", "/a/b.py", "")]
        self.assertEqual(M.measure(FakeGuard, rows), (0, 0, []))


class ItReadsRealPayloads(unittest.TestCase):
    def test_it_finds_writes_and_edits_and_ignores_other_tools(self):
        p = transcript([write_block("/a.py", "x"),
                        write_block("/b.py", "y", tool="Edit", key="new_string"),
                        {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}}])
        try:
            rows = M.payloads(p)
            self.assertEqual([r[0] for r in rows], ["Write", "Edit"])
            self.assertEqual([r[2] for r in rows], ["x", "y"])
        finally:
            os.unlink(p)

    def test_a_malformed_line_does_not_stop_the_read(self):
        p = transcript([write_block("/a.py", "x")])
        with open(p, "a", encoding="utf-8") as fh:
            fh.write("{ not json\n")
        try:
            self.assertEqual(len(M.payloads(p)), 1)
        finally:
            os.unlink(p)


class NoDataIsNeverAZero(unittest.TestCase):
    """Because a zero reads as 'memory did nothing' and a NO-DATA reads as
    'nobody looked', and those lead to opposite decisions."""

    def test_a_missing_transcript_exits_NO_DATA(self):
        self.assertEqual(M.main(["/no/such/transcript.jsonl"]), 2)

    def test_a_transcript_with_no_writes_exits_NO_DATA_not_zero(self):
        p = transcript([{"type": "tool_use", "name": "Bash",
                         "input": {"command": "ls"}}])
        try:
            self.assertEqual(M.main([p]), 2)
        finally:
            os.unlink(p)

    def test_an_unloadable_guard_returns_None_rather_than_an_empty_matcher(self):
        self.assertIsNone(M.load_guard("/no/such/repeat_guard.py"))

    def test_the_real_guard_on_this_machine_loads_or_says_so(self):
        g = M.load_guard()
        if g is None:
            self.skipTest("the repeat guard is not installed here, which is "
                          "NO-DATA rather than a failure of this module")
        self.assertTrue(callable(g.matching_lessons))


if __name__ == "__main__":
    unittest.main()
