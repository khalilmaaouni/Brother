#!/usr/bin/env python3
"""L4b.1 required fast pass keep: scripts/required_fast.sh PASS keeps.

Specification lines proved here:
  RQ-RF-PASS-KEEP   every PASS check writes its full stdout plus stderr to a
                    durable keep file, and the summary line names the handle.
  RQ-KEEP-NAMING    the keep key is the worktree key plus the pid, so two
                    concurrent lanes never share a file.
  RQ-SUMMARY-STABLE the PASS verdict, the exit code capture order, the counts
                    and the NO-DATA semantics are unchanged; the summary line
                    gains only the handle suffix.

No command runner path is named for this unit and a test file may not import
subprocess, so nothing here starts a process. The real script is read as bytes
and every marker below is one line of the specification. The PASS arm is also
shown able to go red by named mutations, and the PASS verdict, the counters
and the FAIL keep lines are asserted unchanged.
"""

import os
import re
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REQUIRED_FAST = os.path.join(HERE, "required_fast.sh")

WORKTREE_KEY = 'worktree_key="$(basename "$(pwd)")-$$"'
PASS_KEEP_PATH = 'pass_keep="${TMPDIR:-/tmp}/required-fast-pass-$name-$worktree_key.txt"'
PASS_KEEP_WRITE = "printf '%s' \"$out\" > \"$pass_keep\""
PASS_KEEP_HANDLE = 'last="$last  [full: $pass_keep]"'
PASS_COUNTER = 'pass=$((pass+1));   verdict="PASS   "'
NODATA_ARM = '2) nodata=$((nodata+1)); verdict="NO-DATA"; nodata_names="$nodata_names $name"'
FAIL_KEEP_PATH = 'keep="${TMPDIR:-/tmp}/required-fast-fail-$name-$worktree_key.txt"'
FAIL_KEEP_WRITE = "printf '%s\\n' \"$out\" > \"$keep\" || exit 1"
FAIL_DETAIL_GUARD = 'if [ -n "$keep" ] && [ -f "$keep" ]'
PASS_KEEP_PRINTED = 'tail -80 "$pass_keep"'
PASS_KEEP_READ = '$(cat "$pass_keep")'


def read_script_bytes(path):
    """The one validation point: every script read routes through here.

    Refused with ValueError, never a raw TypeError and never a pass: a value
    that is not a str (None, a bool, a number, NaN, bytes, a list, a dict),
    an empty path, a directory, an unreadable path, a NUL byte in the path.
    """
    if not isinstance(path, str):
        raise ValueError(
            "required_fast path must be a str, got %s" % (type(path).__name__,))
    if path == "":
        raise ValueError("required_fast path is empty")
    if os.path.isdir(path):
        raise ValueError("required_fast path is a directory, not a file: %r" % (path,))
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except OSError as exc:
        raise ValueError("required_fast path is unreadable: %s" % (exc,))


def read_script_text(path):
    """Read a shell script as bytes, then decode it as UTF-8."""
    raw = read_script_bytes(path)
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("required_fast script is not valid UTF-8: %s" % (exc,))


def _pass_arm(text):
    """The PASS arm of the exit code case, from its counter to its ';;'."""
    start = text.find(PASS_COUNTER)
    if start < 0:
        return None
    end = text.find(";;", start)
    if end < 0:
        return None
    return text[start:end]


def has_required_fast_pass_keep(text):
    """True only when every L4b.1 marker is present in the script text."""
    if not isinstance(text, str):
        raise ValueError("script text must be a str, got %s" % (type(text).__name__,))
    for marker in (WORKTREE_KEY, PASS_KEEP_PATH, PASS_KEEP_WRITE,
                   PASS_KEEP_HANDLE, PASS_COUNTER, NODATA_ARM,
                   FAIL_KEEP_PATH, FAIL_KEEP_WRITE, FAIL_DETAIL_GUARD):
        if marker not in text:
            return False
    if PASS_KEEP_PRINTED in text or PASS_KEEP_READ in text:
        return False
    arm = _pass_arm(text)
    if arm is None:
        return False
    if "pass_keep=" not in arm:
        return False
    if re.search(r"(?:^|[\s;])keep=", arm):
        return False
    return True


def check_rf_pass_keep(path):
    """L4b.1 control point: True when the script at path keeps every PASS."""
    return has_required_fast_pass_keep(read_script_text(path))


class RequiredFastPassKeepTest(unittest.TestCase):

    def setUp(self):
        self.text = read_script_text(REQUIRED_FAST)

    def test_required_fast_pass_keep_is_implemented(self):
        self.assertTrue(check_rf_pass_keep(REQUIRED_FAST))

    def test_pass_keep_path_carries_worktree_key_and_pid(self):
        self.assertIn(WORKTREE_KEY, self.text)
        self.assertIn("-$$", WORKTREE_KEY)
        self.assertIn(PASS_KEEP_PATH, self.text)
        self.assertNotIn("required-fast-pass-$name.txt", self.text)

    def test_pass_keep_write_is_truncating_and_exact(self):
        self.assertIn(PASS_KEEP_WRITE, self.text)
        self.assertNotIn('>> "$pass_keep"', self.text)

    def test_pass_summary_names_the_handle(self):
        self.assertIn(PASS_KEEP_HANDLE, self.text)

    def test_pass_verdict_and_count_are_unchanged(self):
        self.assertIn(PASS_COUNTER, self.text)
        self.assertIn("pass=$((pass+1))", self.text)

    def test_no_data_arm_is_unchanged(self):
        self.assertIn(NODATA_ARM, self.text)

    def test_fail_keep_path_is_unchanged(self):
        self.assertIn(FAIL_KEEP_PATH, self.text)
        self.assertIn(FAIL_KEEP_WRITE, self.text)
        self.assertIn(FAIL_DETAIL_GUARD, self.text)

    def test_pass_keeps_are_named_only_never_printed(self):
        self.assertNotIn(PASS_KEEP_PRINTED, self.text)
        self.assertNotIn(PASS_KEEP_READ, self.text)
        arm = _pass_arm(self.text)
        self.assertIsNotNone(arm)
        self.assertIn("pass_keep=", arm)
        self.assertIsNone(re.search(r"(?:^|[\s;])keep=", arm))

    def test_removing_the_pass_keep_turns_red(self):
        mutated = self.text.replace(PASS_KEEP_HANDLE + "\n", "", 1)
        self.assertNotEqual(mutated, self.text)
        self.assertFalse(has_required_fast_pass_keep(mutated))

    def test_pass_keep_without_the_pid_key_turns_red(self):
        mutated = self.text.replace(
            "required-fast-pass-$name-$worktree_key.txt",
            "required-fast-pass-$name.txt", 1)
        self.assertNotEqual(mutated, self.text)
        self.assertFalse(has_required_fast_pass_keep(mutated))

    def test_pass_arm_without_the_write_is_not_a_pass(self):
        fixture = (
            "#!/bin/sh\n"
            + WORKTREE_KEY + "\n"
            + 'case "$code" in\n'
            + "  " + PASS_COUNTER + " ;;\n"
            + "esac\n"
        )
        self.assertFalse(has_required_fast_pass_keep(fixture))

    def test_hostile_paths_are_refused(self):
        hostile = [None, True, False, 17, 3.5, float("nan"),
                   b"required_fast.sh", ["required_fast.sh"],
                   {"path": "required_fast.sh"}, "", "a\x00b",
                   os.path.join(HERE, "no-such-l4b1-required-fast.sh")]
        for bad in hostile:
            with self.assertRaises(ValueError):
                check_rf_pass_keep(bad)
        with self.assertRaises(ValueError):
            check_rf_pass_keep(HERE)

    def test_hostile_script_text_is_refused(self):
        for bad in (None, 17, b"text", ["text"]):
            with self.assertRaises(ValueError):
                has_required_fast_pass_keep(bad)

    def test_non_utf8_script_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = os.path.join(tmp, "required_fast.sh")
            with open(bad, "wb") as handle:
                handle.write(b"\xff\xfe\x00not utf-8\x80")
            with self.assertRaises(ValueError):
                check_rf_pass_keep(bad)

    def test_empty_script_is_not_a_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty = os.path.join(tmp, "required_fast.sh")
            with open(empty, "wb") as handle:
                handle.write(b"")
            self.assertFalse(check_rf_pass_keep(empty))


if __name__ == "__main__":
    unittest.main()
