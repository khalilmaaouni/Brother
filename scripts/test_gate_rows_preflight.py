#!/usr/bin/env python3
"""R2.6 gate rows and preflight.

Two rules this module proves about the repository's own gates:

  R20/R21: each gate asks the shared-config detective question once, with an
  explicit --repo-root, so the answer is about the captured repository root
  and never about whatever a launched process happened to inherit.
  R22/R23: each gate's unset loop runs BEFORE that row, so an inherited
  GIT_DIR is already gone by the time the row is asked.

Every public function refuses what it cannot use with this module's own
 deliberate error, GateRowRefusal (a ValueError subclass): never a raw
interpreter exception, never a silent accept, and never the safe case. The
verdicts the specification names are BLOCK (a row is missing or out of
order), NO-DATA (the gate file itself cannot be read) and PASS.
"""

import os
import pathlib
import tempfile
import unittest


class GateRowRefusal(ValueError):
    """The deliberate refusal: a gate row is missing or out of order, or a
    value that is not usable text was handed in."""


ROW_REQUIRED_FAST = (
    'run_check "git-location-config" python3 scripts/git_location_guard.py '
    '--check-live-config --repo-root "$(pwd)"'
)
ROW_CHECK_ALL = (
    'run_check "git-location-config" python3 scripts/git_location_guard.py '
    '--check-live-config --repo-root "$ROOT"'
)
UNSET_LOOP = "unset GIT_DIR GIT_WORK_TREE"


def _as_path(value):
    if isinstance(value, GateRowRefusal):
        raise value
    if isinstance(value, str):
        return value
    if isinstance(value, os.PathLike):
        raw = os.fspath(value)
        if isinstance(raw, str):
            return raw
        raise GateRowRefusal(
            "path must resolve to text, not %r" % (type(raw).__name__,)
        )
    raise GateRowRefusal(
        "path must be a str, not %r" % (type(value).__name__,)
    )


def read_text(path):
    """Read the file at path as BYTES and decode it as UTF-8.

    NO-DATA direction: a missing file, a directory, a non-UTF-8 byte
    sequence, or a path that is not usable text is refused with
    GateRowRefusal. Nothing here ever falls back to an empty string, because
    an empty gate file would read as a gate that carries no rows at all.
    """
    resolved = _as_path(path)
    try:
        with open(resolved, "rb") as handle:
            data = handle.read()
    except OSError as exc:
        raise GateRowRefusal(
            "could not read %r: %s" % (resolved, exc)
        ) from None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise GateRowRefusal(
            "%r is not valid UTF-8: %s" % (resolved, exc)
        ) from None


def _as_text(value):
    if isinstance(value, GateRowRefusal):
        raise value
    if not isinstance(value, str):
        raise GateRowRefusal(
            "text must be a str, not %r" % (type(value).__name__,)
        )
    return value


def has_git_location_config_row(text):
    """True when either gate's own explicit-repo-root row is present."""
    body = _as_text(text)
    return ROW_REQUIRED_FAST in body or ROW_CHECK_ALL in body


def first_run_check_index(text):
    """The index of the first `run_check "` call, or -1 when there is none."""
    body = _as_text(text)
    return body.find('run_check "')


def unset_loop_index(text):
    """The index of the unset loop, or -1 when this file carries none."""
    body = _as_text(text)
    return body.find(UNSET_LOOP)


def require_row(text, label, needle):
    """BLOCK when the named gate row is absent; return its index otherwise."""
    body = _as_text(text)
    if needle not in body:
        raise GateRowRefusal(
            "%s is missing its git-location-config row" % (label,)
        )
    return body.index(needle)


def require_unset_before_row(text, label, needle):
    """BLOCK when the unset loop is absent, or comes after the row.

    A control that prevents beats a check that reports: the row must be
    asked only once the inherited location variables are already gone.
    """
    row_index = require_row(text, label, needle)
    loop_index = unset_loop_index(text)
    if loop_index < 0:
        raise GateRowRefusal(
            "%s carries no unset loop, so an inherited GIT_DIR could reach the row"
            % (label,)
        )
    if loop_index >= row_index:
        raise GateRowRefusal(
            "%s asks the git-location-config row at %d before its unset loop at %d"
            % (label, row_index, loop_index)
        )
    return loop_index


def _move_unset_loop_to_the_end(text, label):
    """Fixture builder: the corrupt order the specification says must BLOCK."""
    body = _as_text(text)
    lines = body.splitlines(True)
    kept = [line for line in lines if not line.lstrip().startswith(UNSET_LOOP)]
    moved = [line for line in lines if line.lstrip().startswith(UNSET_LOOP)]
    if not moved:
        raise GateRowRefusal(
            "%s carries no unset loop to move" % (label,)
        )
    return "".join(kept + moved)


HERE = os.path.dirname(os.path.abspath(__file__))
REQUIRED_FAST = os.path.join(HERE, "required_fast.sh")
CHECK_ALL = os.path.join(HERE, "check_all.sh")


class GateRowsPreflightTests(unittest.TestCase):

    def test_required_fast_has_git_location_config_row(self):
        text = read_text(REQUIRED_FAST)
        self.assertTrue(has_git_location_config_row(text))
        self.assertIn(ROW_REQUIRED_FAST, text)

    def test_check_all_has_git_location_config_row(self):
        text = read_text(CHECK_ALL)
        self.assertTrue(has_git_location_config_row(text))
        self.assertIn(ROW_CHECK_ALL, text)

    def test_required_fast_unset_loop_before_git_location_config_row(self):
        text = read_text(REQUIRED_FAST)
        loop_index = unset_loop_index(text)
        row_index = text.find('run_check "git-location-config"')
        self.assertGreaterEqual(loop_index, 0)
        self.assertGreaterEqual(row_index, 0)
        self.assertLess(loop_index, row_index)

    def test_check_all_unset_loop_before_git_location_config_row(self):
        text = read_text(CHECK_ALL)
        loop_index = unset_loop_index(text)
        row_index = text.find('run_check "git-location-config"')
        self.assertGreaterEqual(loop_index, 0)
        self.assertGreaterEqual(row_index, 0)
        self.assertLess(loop_index, row_index)

    def test_healthy_tree_passes_both_guards(self):
        # The control: both guards can go green, so a red is about the tree.
        fast = read_text(REQUIRED_FAST)
        every = read_text(CHECK_ALL)
        self.assertGreaterEqual(
            require_unset_before_row(fast, "required_fast.sh", ROW_REQUIRED_FAST), 0
        )
        self.assertGreaterEqual(
            require_unset_before_row(every, "check_all.sh", ROW_CHECK_ALL), 0
        )
        self.assertNotEqual(first_run_check_index(fast), -1)
        self.assertNotEqual(first_run_check_index(every), -1)
        self.assertEqual(
            require_row(fast, "required_fast.sh", ROW_REQUIRED_FAST),
            fast.index(ROW_REQUIRED_FAST),
        )

    def test_required_fast_missing_row_blocks(self):
        text = read_text(REQUIRED_FAST)
        mutated = text.replace(ROW_REQUIRED_FAST, "")
        self.assertNotIn(ROW_REQUIRED_FAST, mutated)
        with self.assertRaises(GateRowRefusal):
            require_row(mutated, "required_fast.sh", ROW_REQUIRED_FAST)
        with self.assertRaises(GateRowRefusal):
            require_unset_before_row(mutated, "required_fast.sh", ROW_REQUIRED_FAST)

    def test_check_all_missing_row_blocks(self):
        text = read_text(CHECK_ALL)
        mutated = text.replace(ROW_CHECK_ALL, "")
        self.assertNotIn(ROW_CHECK_ALL, mutated)
        with self.assertRaises(GateRowRefusal):
            require_row(mutated, "check_all.sh", ROW_CHECK_ALL)
        with self.assertRaises(GateRowRefusal):
            require_unset_before_row(mutated, "check_all.sh", ROW_CHECK_ALL)

    def test_required_fast_unset_loop_after_row_blocks(self):
        text = read_text(REQUIRED_FAST)
        mutated = _move_unset_loop_to_the_end(text, "required_fast.sh")
        self.assertGreater(
            unset_loop_index(mutated), mutated.index(ROW_REQUIRED_FAST)
        )
        with self.assertRaises(GateRowRefusal):
            require_unset_before_row(mutated, "required_fast.sh", ROW_REQUIRED_FAST)

    def test_check_all_unset_loop_after_row_blocks(self):
        text = read_text(CHECK_ALL)
        mutated = _move_unset_loop_to_the_end(text, "check_all.sh")
        self.assertGreater(
            unset_loop_index(mutated), mutated.index(ROW_CHECK_ALL)
        )
        with self.assertRaises(GateRowRefusal):
            require_unset_before_row(mutated, "check_all.sh", ROW_CHECK_ALL)

    def test_unset_loop_absent_blocks(self):
        text = read_text(REQUIRED_FAST)
        mutated = "\n".join(
            line
            for line in text.splitlines()
            if not line.lstrip().startswith(UNSET_LOOP)
        )
        self.assertNotIn(UNSET_LOOP, mutated)
        self.assertIn(ROW_REQUIRED_FAST, mutated)
        with self.assertRaises(GateRowRefusal):
            require_unset_before_row(mutated, "required_fast.sh", ROW_REQUIRED_FAST)

    def test_empty_text_blocks(self):
        self.assertFalse(has_git_location_config_row(""))
        self.assertEqual(first_run_check_index(""), -1)
        self.assertEqual(unset_loop_index(""), -1)
        with self.assertRaises(GateRowRefusal):
            require_row("", "required_fast.sh", ROW_REQUIRED_FAST)
        with self.assertRaises(GateRowRefusal):
            require_unset_before_row("", "required_fast.sh", ROW_REQUIRED_FAST)
        with self.assertRaises(GateRowRefusal):
            _move_unset_loop_to_the_end("", "required_fast.sh")

    def test_hostile_input_refused(self):
        hostile = [
            None, 0, 1, -1, 3.5, float("nan"), True, False,
            b"", b"x", bytearray(b"x"),
            [], (), {}, set(), object(),
            GateRowRefusal("boom"),
        ]
        for bad in hostile:
            with self.assertRaises(GateRowRefusal):
                read_text(bad)
            with self.assertRaises(GateRowRefusal):
                has_git_location_config_row(bad)
            with self.assertRaises(GateRowRefusal):
                first_run_check_index(bad)
            with self.assertRaises(GateRowRefusal):
                unset_loop_index(bad)
            with self.assertRaises(GateRowRefusal):
                require_row(bad, "gate.sh", ROW_REQUIRED_FAST)
            with self.assertRaises(GateRowRefusal):
                require_unset_before_row(bad, "gate.sh", ROW_REQUIRED_FAST)
            with self.assertRaises(GateRowRefusal):
                _move_unset_loop_to_the_end(bad, "gate.sh")

    def test_filesystem_refusals(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = os.path.join(tmp, "not-here-r26.sh")
            with self.assertRaises(GateRowRefusal):
                read_text(missing)
            with self.assertRaises(GateRowRefusal):
                read_text(tmp)  # a directory, not a file
            notutf8 = os.path.join(tmp, "not-utf8.sh")
            with open(notutf8, "wb") as handle:
                handle.write(b"\xff\xfe\x00")
            with self.assertRaises(GateRowRefusal):
                read_text(notutf8)
            healthy = os.path.join(tmp, "healthy.sh")
            with open(healthy, "wb") as handle:
                handle.write(
                    b"#!/bin/sh\n" + ROW_REQUIRED_FAST.encode("utf-8") + b"\n"
                )
            text = read_text(healthy)
            self.assertTrue(has_git_location_config_row(text))
            # a path-like object is usable text, not hostile input
            self.assertEqual(read_text(pathlib.Path(healthy)), text)


if __name__ == "__main__":
    unittest.main()
