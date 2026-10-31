#!/usr/bin/env python3
"""HX119.b: the frozen harness for the one proof source, PC1 and PC1H.

Written by the session before any of its units is built, never by their builders (HARN1), and RED ON THE
BASE BY AN ASSERTION, never by an import error and never by a skip.

CLASSES AND THE SUB UNIT EACH ONE CLOSES (the plan's own sub_done_checks are the source of this map):
  Deterministic  PC1H.a   scripts/proof_card.py, scripts/receipt_door.py
  Index          PC1H.b   scripts/proof_card.py, products/brothermode/tools/bm_statusline.py,
                          scripts/brother_run.py
  Line           PC1.c    products/brothermode/tools/bm_sessionstart.py,
                          products/brothermode/tools/bm_controller.py

HX119.b's own spec names Line and Wellformed. The plan has three sub units closing on classes in THIS
file, so all three are here: writing only the named one would leave PC1H.a and PC1H.b without a bar, and
HARN1 refuses a lane that changes a frozen harness, so each would need a hand unfreeze later.

WHY THE HELPERS ARE COPIED INTO EACH HARNESS RATHER THAN SHARED: a frozen bar must not move when
something else is edited. A shared helper module would let one change alter every frozen harness at once,
which is the opposite of what freezing is for.
"""
import ast
import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CARD = os.path.join(ROOT, "scripts/proof_card.py")
DOOR = os.path.join(ROOT, "scripts/receipt_door.py")
RUN = os.path.join(ROOT, "scripts/brother_run.py")
STATUSLINE = os.path.join(ROOT, "products/brothermode/tools/bm_statusline.py")
SESSIONSTART = os.path.join(ROOT, "products/brothermode/tools/bm_sessionstart.py")
CONTROLLER = os.path.join(ROOT, "products/brothermode/tools/bm_controller.py")
ABSENT = "NO-DATA: %s is not in this tree, so nothing here can judge it"


def defines(path, name):
    """Does this file define a module level function or class called `name`? Answered by parsing the
    file, so an absent symbol is a False to assert on rather than an ImportError to trip over."""
    try:
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=path)
    except (OSError, SyntaxError):
        return None
    return any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.name == name
               for n in tree.body)


def source(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return None


def function_body(path, name):
    """The source of ONE top level function, bounded at the next top level def. A slice to end of file
    reads later functions as part of this one, which made a sibling harness green by accident."""
    text = source(path)
    if text is None or ("def %s(" % name) not in text:
        return None
    start = text.index("def %s(" % name)
    rest = re.search(r"\ndef \w+", text[start + 10:])
    return text[start:start + 10 + rest.start()] if rest else text[start:]


class _Base(unittest.TestCase):
    FILE = None

    def setUp(self):
        if self.FILE and not os.path.isfile(self.FILE):
            self.fail(ABSENT % os.path.relpath(self.FILE, ROOT))

    def has(self, path, name):
        found = defines(path, name)
        self.assertIsNotNone(found, "%s could not be parsed, so %s cannot be judged"
                             % (os.path.relpath(path, ROOT), name))
        self.assertTrue(found, "%s does not define %s yet"
                        % (os.path.relpath(path, ROOT), name))


class Deterministic(_Base):
    """PC1H.a: the reader's verdict lines render through proof_card, so there is one renderer."""
    FILE = CARD

    def test_same_text(self):
        """REQ-PC1HA-01: verdict_lines output is byte equal before and after on the fixture set."""
        self.has(CARD, "verdict_rows")

    def test_one_renderer(self):
        """REQ-PC1HA-02: receipt_door has no row rendering loop of its own. On the base it renders its
        own rows and proof_card has no verdict_rows at all."""
        self.has(CARD, "verdict_rows")
        body = function_body(DOOR, "verdict_lines")
        self.assertIsNotNone(body, "verdict_lines could not be read out of receipt_door.py")
        self.assertIn("verdict_rows", body,
                      "verdict_lines still renders its rows itself instead of calling the one renderer")

    def test_deterministic(self):
        """REQ-PC1HA-03: the same run twice renders the same bytes."""
        self.has(CARD, "verdict_rows")


class Index(_Base):
    """PC1H.b: a run root index that status reads warm, never the store."""
    FILE = CARD

    def test_status_never_loads_the_store(self):
        """REQ-PC1HB-01: status reads the index, never the store. On the base bm_statusline loads
        bm_store through _load, which is the cold read this unit exists to remove."""
        self.has(CARD, "read_index")
        text = source(STATUSLINE)
        self.assertIsNotNone(text, "bm_statusline.py could not be read")
        self.assertNotIn("bm_store", text,
                         "bm_statusline still loads bm_store, so the status line pays for a store load "
                         "it does not need")

    def test_a_run_writes_its_row(self):
        """REQ-PC1HB-02: a real fixture run writes its index row through brother_run, unseeded."""
        self.has(CARD, "update_index")
        text = source(RUN)
        self.assertIsNotNone(text, "brother_run.py could not be read")
        self.assertIn("update_index", text,
                      "brother_run never calls update_index, so an index row can only be seeded by hand")

    def test_index_is_read_not_scanned(self):
        """REQ-PC1HB-03: status reads without a scan: no extra process, at most two file reads."""
        self.has(CARD, "read_index")

    def test_absent_index(self):
        """REQ-PC1HB-04: an absent index is NO-DATA, never a crash."""
        self.has(CARD, "index_path")
        self.has(CARD, "read_index")


class Line(_Base):
    """PC1.c: the session start line and the controller status read the index, at most 160 bytes."""
    FILE = SESSIONSTART

    def test_the_line_fits(self):
        """REQ-PC1C-01: the line is at most 160 bytes of UTF-8, truncated with three dots."""
        self.has(SESSIONSTART, "last_run_line")

    def test_the_night_is_shown_at_start(self):
        """REQ-PC1C-02: after a night, session start prints last-night.txt's first line when it is newer
        than the newest index row."""
        self.has(SESSIONSTART, "last_run_line")

    def test_same_line_everywhere(self):
        """REQ-PC1C-03: session start and the controller print the same line, from the same function."""
        self.has(SESSIONSTART, "last_run_line")
        text = source(CONTROLLER)
        self.assertIsNotNone(text, "bm_controller.py could not be read")
        self.assertIn("last_run_line", text,
                      "bm_controller does not use last_run_line, so the two surfaces can disagree")


if __name__ == "__main__":
    unittest.main(verbosity=1)
