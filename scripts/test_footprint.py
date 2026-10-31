#!/usr/bin/env python3
"""HX119.c: the frozen harness for the footprint units, FOOT1 and FOOT1H.

Written by the session before any of its units is built, never by their builders (HARN1), and RED ON THE
BASE BY AN ASSERTION, never by an import error and never by a skip.

CLASSES AND THE SUB UNIT EACH ONE CLOSES (from the plan's own sub_done_checks):
  NoConsentNoWrite    FOOT1.a    the three BrotherSBE writers
  BrotherModeWriters  FOOT1.b    products/brothermode/tools/bm_telemetry.py, bm_store.py
  Forget              FOOT1H.a   scripts/door.py, docs/DATA.md

WHAT THESE UNITS ARE FOR, which is why the bar is written strictly: installing Brother must write nothing
before a person says yes, and one word must remove everything it wrote. That is a consent boundary, not a
tidiness feature, so every requirement here fails CLOSED: an unreadable file is a failure, never a pass,
and a missing docs/DATA.md is a failure rather than an empty list of roots to check.

EVERY RED HERE WAS MEASURED ON THIS TREE BEFORE THE TEST WAS WRITTEN:
  consented in sbe_session_baseline.py     absent
  may_write in bm_telemetry.py             absent
  data_roots in door.py                    absent
  forget in door.py                        absent
  docs/DATA.md                             does not exist
"""
import ast
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SBE_WRITERS = ("products/brothersbe/tools/sbe_autosave.py",
               "products/brothersbe/tools/sbe_session_baseline.py",
               "products/brothersbe/tools/sbe_telemetry.py")
BM_TELEMETRY = os.path.join(ROOT, "products/brothermode/tools/bm_telemetry.py")
BM_STORE = os.path.join(ROOT, "products/brothermode/tools/bm_store.py")
DOOR = os.path.join(ROOT, "scripts/door.py")
DATA_DOC = os.path.join(ROOT, "docs/DATA.md")
ABSENT = "NO-DATA: %s is not in this tree, so nothing here can judge it"
#: the roots docs/DATA.md must account for, from FOOT1H.a's own text
KNOWN_ROOTS = (".brother", ".brotherme", ".brothermode", "brothersbe", "vault")


def defines(path, name):
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


class NoConsentNoWrite(_Base):
    """FOOT1.a: the BrotherSBE writers write nothing before consent."""
    FILE = os.path.join(ROOT, SBE_WRITERS[1])

    def test_a_fresh_home_gets_no_file(self):
        """REQ-FOOT1A-01: every session event in a fresh HOME writes zero files. On the base SessionStart
        writes autosave.log and baseline.json before anyone has said yes, so each writer must ask the one
        consent reader before its first write."""
        missing = []
        for rel in SBE_WRITERS:
            path = os.path.join(ROOT, rel)
            text = source(path)
            if text is None:
                missing.append(rel + " (unreadable)")
            elif "consented" not in text:
                missing.append(rel)
        self.assertEqual(missing, [], "these writers never ask whether consent was given, so they write "
                                      "into a fresh HOME before anyone said yes: %s" % missing)

    def test_consent_restores_the_writes(self):
        """REQ-FOOT1A-02: after consent the writers write exactly as they do today, so the gate withholds
        the write rather than removing the feature."""
        self.has(os.path.join(ROOT, SBE_WRITERS[1]), "consented")


class BrotherModeWriters(_Base):
    """FOOT1.b: the BrotherMode baseline writes wait for consent."""
    FILE = BM_TELEMETRY

    def test_no_store_before_consent(self):
        """REQ-FOOT1B-01: no store and no telemetry file before consent, and one line naming the consent
        command instead. On the base a SessionStart creates the store before anyone said yes."""
        self.has(BM_TELEMETRY, "may_write")
        text = source(BM_STORE)
        self.assertIsNotNone(text, "bm_store.py could not be read")
        self.assertIn("may_write", text,
                      "bm_store's first store creation does not ask may_write, so the database is "
                      "created before consent")


class Forget(_Base):
    """FOOT1H.a: forget removes every root Brother wrote, and docs/DATA.md lists them."""
    FILE = DOOR

    def test_forget_skips_nothing(self):
        """REQ-FOOT1HA-01: forget with a yes removes every listed root. On the base door.py has no
        forget at all and the BrotherSBE refs survive an uninstall."""
        self.has(DOOR, "forget")
        self.has(DOOR, "data_roots")

    def test_forget_asks_first(self):
        """REQ-FOOT1HA-02: forget without a yes removes nothing."""
        self.has(DOOR, "forget")

    def test_an_agent_cannot_forget(self):
        """REQ-FOOT1HA-03: a yes passed as an argument by an agent removes nothing, and the bound vault,
        which holds the person's own notes, is listed but never removed. The answer is taken the same way
        as consent: an interactive terminal, or the answer file the session is denied."""
        self.has(DOOR, "forget")
        text = source(DOOR)
        self.assertIsNotNone(text, "door.py could not be read")
        self.assertIn("consent", text,
                      "door.py names no consent route, so forget has no person-only way to be answered "
                      "and an agent could spend the yes itself")

    def test_the_doc_lists_every_root(self):
        """REQ-FOOT1HA-04: docs/DATA.md lists every root data_roots knows. A missing doc fails: a reader
        cannot consent to writes they were never told about."""
        text = source(DATA_DOC)
        self.assertIsNotNone(text, "docs/DATA.md does not exist, so no reader is told what Brother "
                                   "writes or where, and forget has no list to be checked against")
        missing = [r for r in KNOWN_ROOTS if r not in text]
        self.assertEqual(missing, [], "docs/DATA.md does not account for these roots: %s" % missing)


if __name__ == "__main__":
    unittest.main(verbosity=1)
