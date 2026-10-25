"""Frozen harness for LAT0 (1.1.8): scripts/hook_latency.py measures the 17 rows of the roadmap's latency table (5b).

Written by the session before the night (council C1, 2026-10-23); the builder never edits it. Every class is red on
the base BY AN ASSERTION (the grader reads a skipped test as green): the first test of each class asserts that
scripts/hook_latency.py exists, so the base fails there, by name.

THE CONTRACT this harness pins (the spec, docs/plan/specs/1.1.8/LAT0.md, says the same):
- events() returns EVENTS below, exactly, in order. The first seven are hook events; the other ten are moments no
  hooks.json command produces, and their wall, process and byte cells are always "NO-DATA".
- A hook event's commands come from a hooks document shaped like bundle/hooks/hooks.json ({"hooks": {event: [groups]}},
  a group {"matcher": regex or absent, "hooks": [{"type": "command", "command": text}]}). "PreToolUse Bash" takes every
  PreToolUse group whose matcher matches "Bash"; "PreToolUse Edit and Write" every group whose matcher matches "Edit" or
  "Write"; each other hook row takes every group of its event (PostToolUse, Stop, SessionStart, SessionEnd, PreCompact).
- measure(event, doc, runs) returns a dict with keys "event", "p50", "p95", "processes", "bytes". When
  os.getloadavg()[0] is above os.cpu_count() at the start, "p50" and "p95" are the string "NO-DATA", never a number.
- process_count(event, doc) is the number of command strings that event starts (sh and python3 alike).
- main([path to a hooks document]) prints one line per row, then one last line exactly
  "PASS <n> FAIL <n> NO-DATA <n>", where a row is NO-DATA when any of its wall cells is NO-DATA, PASS when both are
  numbers. NO-DATA never counts as PASS.

Run: python3 -B scripts/test_hook_latency.py Selftest Bundle (LAT0). LAT0H's Floors and CheckDoc live in
scripts/test_hook_floors.py since 2026-10-24: a landing runs a touched test file whole, so one file holds one unit.
"""
import contextlib
import importlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MODULE = os.path.join(HERE, "hook_latency.py")
EVENTS = ["PreToolUse Bash", "PreToolUse Edit and Write", "PostToolUse", "Stop", "SessionStart", "SessionEnd",
          "PreCompact", "Bytes read before the first tool call", "End card after the last check", "Accept, reject",
          "Failure visible after detection", "Portable pack", "Status line warm / brother status cold",
          "Tonight preflight", "The consent question", "A parked irreversible step", "A rerun check"]
HOOK_EVENTS = EVENTS[:7]


def group(cmds, matcher=None):
    g = {"hooks": [{"type": "command", "command": c} for c in cmds]}
    if matcher is not None:
        g["matcher"] = matcher
    return g


def doc(bash=1):
    """A hooks document with one trivial program per hook event, and `bash` programs on PreToolUse Bash."""
    return {"hooks": {"PreToolUse": [group(["true"] * bash, "Bash"), group(["true"], "Edit|Write")],
                      "PostToolUse": [group(["true"], "Bash")], "Stop": [group(["true"])],
                      "SessionStart": [group(["true"])], "SessionEnd": [group(["true"])], "PreCompact": [group(["true"])]}}


class Harness(unittest.TestCase):
    def setUp(self):
        self.assertTrue(os.path.isfile(MODULE), "scripts/hook_latency.py does not exist (red on the base)")
        sys.path.insert(0, HERE)
        self.addCleanup(sys.path.remove, HERE)
        sys.modules.pop("hook_latency", None)
        self.hl = importlib.import_module("hook_latency")
        self.home = tempfile.mkdtemp(prefix="hook-latency-")
        self.addCleanup(shutil.rmtree, self.home, True)

    def load(self, value):
        """os.getloadavg patched to `value` for the module, however it imported the function."""
        stack = contextlib.ExitStack()
        stack.enter_context(mock.patch("os.getloadavg", lambda: (value, value, value)))
        if hasattr(self.hl, "getloadavg"):
            stack.enter_context(mock.patch.object(self.hl, "getloadavg", lambda: (value, value, value)))
        return stack


class Selftest(Harness):
    """LAT0.a: one tool measures and prints the 17 rows."""

    def test_seventeen_rows(self):
        self.assertEqual(self.hl.events(), EVENTS)
        with self.load(0.0):
            got = self.hl.rows(doc())
        self.assertEqual([r["event"] for r in got], EVENTS)
        for r in got:
            self.assertTrue({"event", "p50", "p95", "processes", "bytes"} <= set(r), r)
        for r in got[7:]:
            self.assertEqual((r["p50"], r["p95"], r["processes"], r["bytes"]), ("NO-DATA",) * 4, r["event"])

    def test_load_gives_no_data(self):
        with self.load(float(os.cpu_count() or 1) * 50):
            high = self.hl.measure("PreToolUse Bash", doc(), runs=1)
        self.assertEqual((high["p50"], high["p95"]), ("NO-DATA", "NO-DATA"), "a loaded machine gave a number")
        with self.load(0.0):
            low = self.hl.measure("PreToolUse Bash", doc(), runs=3)
        for k in ("p50", "p95"):
            self.assertTrue(isinstance(low[k], (int, float)) and not isinstance(low[k], bool), "an idle machine gave no number: %r" % low)

    def test_no_data_is_not_pass(self):
        path = os.path.join(self.home, "hooks.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(doc(), fh)
        out = io.StringIO()
        with self.load(float(os.cpu_count() or 1) * 50), contextlib.redirect_stdout(out):
            self.hl.main([path])
        lines = out.getvalue().strip().splitlines()
        self.assertTrue(lines, "main printed nothing")
        m = re.fullmatch(r"PASS (\d+) FAIL (\d+) NO-DATA (\d+)", lines[-1].strip())
        self.assertIsNotNone(m, "the last line is not the summary: %r" % lines[-1])
        self.assertEqual((int(m.group(1)), int(m.group(3))), (0, 17), "NO-DATA was counted as PASS: %r" % lines[-1])
        self.assertEqual(len(lines), 18, "one line per row, then the summary")


class Bundle(Harness):
    """LAT0.b: the process count per event, asserted from an injected fixture."""

    def test_a_two_program_event_fails(self):
        self.assertEqual(self.hl.process_count("PreToolUse Bash", doc(bash=1)), 1)
        self.assertEqual(self.hl.process_count("PreToolUse Bash", doc(bash=2)), 2, "two programs on one event read as one")
        self.assertEqual(self.hl.process_count("PreToolUse Edit and Write", doc(bash=2)), 1)

    def test_shipped_row_is_named(self):
        with open(os.path.join(REPO, "bundle", "hooks", "hooks.json"), encoding="utf-8") as fh:
            shipped = json.load(fh)
        n = self.hl.process_count("PreToolUse Bash", shipped)
        self.assertTrue(isinstance(n, int) and n >= 1, n)
        # NO-DATA BY NAME until HD1 (one dispatcher per event) lands: the shipped count is printed, never judged here
        print("NO-DATA until HD1: the shipped PreToolUse Bash row starts %d program(s)" % n)


if __name__ == "__main__":
    unittest.main()
