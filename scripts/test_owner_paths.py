#!/usr/bin/env python3
"""HX119.d: the frozen harness for the owner path units, VRT2 (and VRT1's classes, which are S11's).

Written by the session before any of its units is built, never by their builders (HARN1), and RED ON THE
BASE BY AN ASSERTION, never by an import error and never by a skip.

CLASSES HERE, AND THE SUB UNIT EACH ONE CLOSES (from the plan's own sub_done_checks):
  VaultDefaultsA  VRT2.a   bm_freshness.py, bm_handover.py, bm_profile.py
  VaultDefaultsB  VRT2.b   bm_vault_attribute_provenance.py, bm_vault_attributes.py, bm_vault_catalog.py
  VaultDefaultsC  VRT2.c   bm_vault_compose.py, bm_vault_contract.py, bm_vault_curate.py
  VaultDefaultsD  VRT2.d   bm_vault_distill.py, bm_vault_graph.py, bm_vault_lint.py
  VaultDefaultsE  VRT2.e   bm_vault_lock.py, bm_vault_promote.py, find_out.py
  VaultDefaultsF  VRT2.f   pattern_note.py, bm_profile_reader.py

TWO CLASSES ARE DELIBERATELY NOT HERE, AND THIS FILE MUST NOT BE FROZEN UNTIL THEY ARE. VaultRoot and
VaultWriters are VRT1's done checks and S11.a's work to add to THIS file, which scripts/test_plan_119.py
already carves out with S11_UNITS. So two units own this one path: HX119 writes it and S11 extends it
later. If HX119.d's harness_sha256 were pinned now, HARN1 would refuse S11's lane and the only way
forward would be a hand unfreeze, which is the failure 1.1.8 spent a night on. That is why no pin is
written, and it is a finding about the plan rather than a choice about this file.

WHAT THESE UNITS ARE FOR, measured on this tree: all seventeen modules below carry a reference to a
Documents folder, which is one person's own layout shipped inside the product. A stranger has no such
folder, so every one of those defaults is both a portability defect and a leak of how the author's
machine is arranged. bm_vault.resolve_vault_root already exists as the one answer; vault_root_or_none,
the single call these modules are meant to make, does not.
"""
import ast
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BM_TOOLS = "products/brothermode/tools"
SBE_TOOLS = "products/brothersbe/tools"
VAULT = os.path.join(ROOT, BM_TOOLS, "bm_vault.py")
#: VRT1's two writers (S11.a): telemetry and the consent flow, the pair that must route through the
#: one resolver instead of naming a folder of their own.
TELEMETRY = os.path.join(ROOT, BM_TOOLS, "bm_telemetry.py")
SETUP = os.path.join(ROOT, "products/brothermode/scripts/setup.py")
#: the default root those two still name, measured 2026-10-26: 4 times in bm_telemetry.py, 2 in setup.py
DEFAULT_VAULT = "BrotherModeVault"
#: the one leaked shape: a default that names the author's own documents folder
OWNER_FOLDER = "Documents"
GROUPS = {
    "A": (BM_TOOLS + "/bm_freshness.py", BM_TOOLS + "/bm_handover.py", BM_TOOLS + "/bm_profile.py"),
    "B": (BM_TOOLS + "/bm_vault_attribute_provenance.py", BM_TOOLS + "/bm_vault_attributes.py",
          BM_TOOLS + "/bm_vault_catalog.py"),
    "C": (BM_TOOLS + "/bm_vault_compose.py", BM_TOOLS + "/bm_vault_contract.py",
          BM_TOOLS + "/bm_vault_curate.py"),
    "D": (BM_TOOLS + "/bm_vault_distill.py", BM_TOOLS + "/bm_vault_graph.py",
          BM_TOOLS + "/bm_vault_lint.py"),
    "E": (BM_TOOLS + "/bm_vault_lock.py", BM_TOOLS + "/bm_vault_promote.py",
          BM_TOOLS + "/find_out.py"),
    "F": (BM_TOOLS + "/pattern_note.py", SBE_TOOLS + "/bm_profile_reader.py"),
}


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


def owner_defaults(group):
    """{relative path: count} for every module in this group still naming the author's documents folder.
    An unreadable module counts as a problem, never as clean: a file that cannot be read has not been
    shown to be free of the default."""
    out = {}
    for rel in GROUPS[group]:
        text = source(os.path.join(ROOT, rel))
        if text is None:
            out[rel] = -1
            continue
        n = text.count(OWNER_FOLDER)
        if n:
            out[rel] = n
    return out


class _Group(unittest.TestCase):
    """One class per VRT2 sub unit. Both of its requirements are checked: no owner default is left, and
    the one resolver call exists for the module to make when no root is bound."""
    GROUP = None

    def setUp(self):
        if self.GROUP is None:
            self.fail("NO-DATA: this class names no group, so it judges nothing")
        if not os.path.isfile(VAULT):
            self.fail("NO-DATA: %s/bm_vault.py is not in this tree, so the resolver cannot be judged"
                      % BM_TOOLS)

    def test_no_owner_default_is_left(self):
        """REQ-VRT2x-01: none of this group's modules names the author's own documents folder."""
        left = owner_defaults(self.GROUP)
        self.assertEqual(left, {}, "these shipped modules still default to one person's own documents "
                                   "folder, which no other machine has: %s" % left)

    def test_nothing_is_written_without_a_bound_root(self):
        """REQ-VRT2x-02: with no bound root these modules write nothing and say so, through the one
        resolver call rather than each module deciding for itself."""
        found = defines(VAULT, "vault_root_or_none")
        self.assertIsNotNone(found, "bm_vault.py could not be parsed, so the resolver cannot be judged")
        self.assertTrue(found, "bm_vault defines no vault_root_or_none, so these modules have no single "
                               "call that answers with nothing when no vault root is bound")


class VaultDefaultsA(_Group):
    GROUP = "A"


class VaultDefaultsB(_Group):
    GROUP = "B"


class VaultDefaultsC(_Group):
    GROUP = "C"


class VaultDefaultsD(_Group):
    GROUP = "D"


class VaultDefaultsE(_Group):
    GROUP = "E"


class VaultDefaultsF(_Group):
    GROUP = "F"


class VaultRoot(unittest.TestCase):
    """VRT1.a: one vault folder bound on consent, and old roots indexed read only.

    S11.a's work, added to HX119.d's file by design: this file's own docstring says these two classes
    are deliberately absent and that S11 extends it later, which is why no harness_sha256 is pinned.

    MEASURED ON THIS TREE BEFORE THESE ASSERTIONS WERE WRITTEN, which is what each one rests on:
      bm_vault.py defines legacy_roots              absent
      bm_vault.py references legacy_roots anywhere  absent, so nothing would call it either
      bm_vault.py reads its config from             "bm_vault.json"
      setup.py writes its config to                 ~/.brotherme/config.json
      bm_vault.py mentions the word legacy          5 times ALREADY, which is why no assertion here
                                                    keys on that word: it would be green while the
                                                    work is open, and test_plan_119.HarnessVRT1
                                                    refuses exactly that.
    """

    def setUp(self):
        for path, what in ((VAULT, "bm_vault.py"), (SETUP, "setup.py")):
            if not os.path.isfile(path):
                self.fail("NO-DATA: %s is not in this tree, so the bound folder cannot be judged" % what)

    def test_the_bound_folder_wins(self):
        """REQ-VRT1A-01: with consent and no environment variable, the bound folder is answered.

        Asserted where it actually breaks, which is not inside the resolver. resolve_vault_root already
        answers a config's "vault" key correctly; the defect is that the file the consent flow WRITES is
        not the file the resolver READS, so a stranger who completes setup gets no vault root at all.
        One fact, two modules, and no shared reader."""
        vault, setup = source(VAULT), source(SETUP)
        self.assertIsNotNone(vault, "bm_vault.py could not be read")
        self.assertIsNotNone(setup, "setup.py could not be read")
        self.assertEqual(('bm_vault.json' in vault, 'bm_vault.json' in setup), (True, True),
                         "the consent flow and the resolver name different config files, so a vault "
                         "bound at setup is not the vault recall reads: bm_vault.py reads bm_vault.json "
                         "while setup.py writes ~/.brotherme/config.json")

    def test_old_roots_are_read_only(self):
        """REQ-VRT1A-02: old roots are indexed and never written, through one named mechanism."""
        found = defines(VAULT, "legacy_roots")
        self.assertIsNotNone(found, "bm_vault.py could not be parsed, so its roots cannot be judged")
        self.assertTrue(found, "bm_vault defines no legacy_roots, so nothing distinguishes a root that "
                               "may be read from one that may be written, and refresh can still write "
                               "under an old root")

    def test_old_roots_are_indexed(self):
        """REQ-VRT1A-03: every existing old root appears in the index, marked legacy.

        A SECOND PROPERTY, not a second spelling of the one above: that mechanism must be WIRED, not
        merely defined. A control nothing calls is not a control, so this asks for a call site rather
        than a definition, and a build that defines legacy_roots without using it fails here and passes
        the test above."""
        text = source(VAULT)
        self.assertIsNotNone(text, "bm_vault.py could not be read")
        calls = text.count("legacy_roots") - (1 if "def legacy_roots" in text else 0)
        self.assertGreater(calls, 0, "nothing in bm_vault calls legacy_roots, so no old root reaches "
                                     "the index and a stranger's earlier notes stay invisible")


class VaultWriters(unittest.TestCase):
    """VRT1.b: telemetry and setup write through the resolver, naming no folder of their own.

    MEASURED ON THIS TREE BEFORE THESE ASSERTIONS WERE WRITTEN:
      bm_telemetry.py defines telemetry_root   absent
      bm_telemetry.py names BrotherModeVault   4 times
      setup.py names BrotherModeVault          2 times
    """

    def setUp(self):
        for path, what in ((TELEMETRY, "bm_telemetry.py"), (SETUP, "setup.py")):
            if not os.path.isfile(path):
                self.fail("NO-DATA: %s is not in this tree, so its writes cannot be judged" % what)

    def test_corrections_land_in_the_bound_vault(self):
        """REQ-VRT1B-01: corrections land in the bound vault, through one named root rather than a path
        each writer assembles for itself."""
        found = defines(TELEMETRY, "telemetry_root")
        self.assertIsNotNone(found, "bm_telemetry.py could not be parsed")
        self.assertTrue(found, "bm_telemetry defines no telemetry_root, so corrections are written "
                               "wherever each call site decides and not into the bound vault")

    def test_no_default_vault_literal(self):
        """REQ-VRT1B-02: no writer names a default vault path. A default is one person's own layout
        shipped inside the product: a stranger has no such folder, so the write lands somewhere nobody
        chose."""
        left = {}
        for path, what in ((TELEMETRY, "bm_telemetry.py"), (SETUP, "setup.py")):
            text = source(path)
            self.assertIsNotNone(text, "%s could not be read" % what)
            n = text.count(DEFAULT_VAULT)
            if n:
                left[what] = n
        self.assertEqual(left, {}, "these writers still name a default vault folder, which no other "
                                   "machine has, instead of asking the one resolver: %s" % left)


if __name__ == "__main__":
    unittest.main(verbosity=1)
