"""LAT0.b: the process count per event, asserted from an injected fixture.

Beside the module it tests (scripts/hook_latency.py), like the frozen harness.
It builds its own fixture in memory: it reads no repository file and writes
nothing, so it runs in the export tree and under a boxed HOME.

REQ-LAT0B-01: a fixture with two programs on one event fails.
REQ-LAT0B-02: the shipped row is NO-DATA by name until HD1.
"""
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import hook_latency as _hl


def _group(cmds, matcher=None):
    g = {"hooks": [{"type": "command", "command": c} for c in cmds]}
    if matcher is not None:
        g["matcher"] = matcher
    return g


def _doc(bash=1):
    """One program per hook event, and `bash` programs on PreToolUse Bash."""
    return {"hooks": {
        "PreToolUse": [_group(["true"] * bash, "Bash"), _group(["true"], "Edit|Write")],
        "PostToolUse": [_group(["true"], "Bash")],
        "Stop": [_group(["true"])],
        "SessionStart": [_group(["true"])],
        "SessionEnd": [_group(["true"])],
        "PreCompact": [_group(["true"])],
    }}


_HOSTILE = (None, 0, True, -1, float("nan"), "", "x", b"x", [], ["x"], {}, {"a": 1}, (), {1, 2}, object())


class Bundle(unittest.TestCase):
    """The two LAT0.b requirements, under the names the specification names."""

    def test_a_two_program_event_fails(self):
        self.assertEqual(_hl.process_count("PreToolUse Bash", _doc(bash=1)), 1)
        self.assertEqual(_hl.process_count("PreToolUse Bash", _doc(bash=2)), 2,
                         "two programs on one event read as one")
        self.assertEqual(_hl.process_count("PreToolUse Edit and Write", _doc(bash=2)), 1)

    def test_a_non_hook_event_starts_no_program(self):
        self.assertEqual(_hl.process_count("Portable pack", _doc()), 0)

    def test_shipped_row_is_named(self):
        self.assertTrue(_hl.SHIPPED_ROW_NO_DATA)
        self.assertEqual(_hl.SHIPPED_ROW_NO_DATA, "NO-DATA until HD1",
                         "the shipped row must be named, never judged PASS")
        self.assertNotIn("PASS", _hl.SHIPPED_ROW_NO_DATA)
        self.assertIsInstance(_hl.process_count("PreToolUse Bash", _doc(bash=2)), int)

    def test_hostile_input_is_refused_or_returns_an_int(self):
        for value in _HOSTILE:
            self._one(value, _doc())
            self._one("PreToolUse Bash", value)

    def _one(self, event, hooks_doc):
        try:
            result = _hl.process_count(event, hooks_doc)
        except ValueError:
            return
        self.assertNotIsInstance(result, bool)
        self.assertIsInstance(result, int)


if __name__ == "__main__":
    unittest.main()
