#!/usr/bin/env python3
"""Calibration for tools/bm_vault_survivorship.py, WBS row VB12-01.

The property under test is the row's own sentence: a fixture conflict
resolves per the table, reordering a table copy flips the winner (and would
fail this test if the resolver stopped reading the shared structure), an
override records its history, and the linter and the resolver provably read
one structure (the drift test).

No em or en dashes anywhere in this file.
"""
import importlib.util
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bm_vault_survivorship as surv  # noqa: E402


def note(authority, body):
    return "---\ntype: reference\nstatus: standing\nauthority: %s\n---\n\n%s\n" % (
        authority, body)


def load_from(name, tools_dir):
    path = os.path.join(tools_dir, name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class AFixtureConflictResolvesPerTheTable(unittest.TestCase):
    def setUp(self):
        self.vault = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.vault, ignore_errors=True)
        with open(os.path.join(self.vault, "a.md"), "w", encoding="utf-8") as fh:
            fh.write(note("derived", "claim: the price is 100 [evidence: a.md]"))
        with open(os.path.join(self.vault, "b.md"), "w", encoding="utf-8") as fh:
            fh.write(note("casual", "claim: the price is 200 [evidence: b.md]"))
        self.auth_mod = surv._load_sibling("bm_vault_authority")
        self.triage_mod = surv._load_sibling("bm_vault_triage")

    def test_triage_calls_it_a_real_contradiction(self):
        _, scoped, contradictions, _ = self.triage_mod.scan(self.vault)
        self.assertEqual(scoped, [])
        self.assertEqual(len(contradictions), 1)

    def test_derived_beats_casual_under_the_default_table(self):
        _, _, contradictions, _ = self.triage_mod.scan(self.vault)
        a, b = contradictions[0]
        winner, loser, reason = surv.resolve(
            self.vault, a, b, "value", self.auth_mod, overrides=[])
        self.assertEqual(winner["path"], "a.md")
        self.assertEqual(loser["path"], "b.md")
        self.assertIn("derived", reason)
        self.assertIn("casual", reason)

    def test_cli_reports_the_same_winner_exit_1(self):
        store = os.path.join(self.vault, "..", "unused_overrides.jsonl")
        rc = surv.cmd_resolve_conflict(self.vault, "value", store)
        self.assertEqual(rc, 1)


class ReorderingATableCopyFlipsTheWinner(unittest.TestCase):
    """The drift test: bm_vault_lint.py's sibling loader and this module's
    own loader, pointed at the SAME mutated copy of bm_vault_authority.py,
    must see the identical mutated vocabulary. That is what "one structure"
    means: neither module keeps its own private copy of LEVELS."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        for name in ("bm_vault_authority.py", "bm_vault_survivorship.py",
                     "bm_vault_lint.py", "bm_vault_ids.py", "bm_vault_temporal.py",
                     "bm_vault_lifecycle.py"):
            shutil.copy(os.path.join(HERE, name), os.path.join(self.tmp, name))

    def _mutate_levels(self):
        auth_path = os.path.join(self.tmp, "bm_vault_authority.py")
        with open(auth_path, encoding="utf-8") as fh:
            text = fh.read()
        original = 'LEVELS = ("casual", "derived", "source_of_record")'
        mutated = 'LEVELS = ("derived", "casual", "source_of_record")'
        self.assertIn(original, text, "fixture assumption broke: LEVELS literal moved")
        with open(auth_path, "w", encoding="utf-8") as fh:
            fh.write(text.replace(original, mutated, 1))

    def test_linter_and_resolver_read_the_same_mutated_structure(self):
        self._mutate_levels()
        lint_mod = load_from("bm_vault_lint", self.tmp)
        surv_mod = load_from("bm_vault_survivorship", self.tmp)

        lint_auth = lint_mod._load_sibling("bm_vault_authority")
        surv_auth = surv_mod._load_sibling("bm_vault_authority")

        mutated_levels = ("derived", "casual", "source_of_record")
        self.assertEqual(lint_auth.LEVELS, mutated_levels)
        self.assertEqual(surv_auth.LEVELS, mutated_levels)
        self.assertEqual(lint_auth.LEVELS, surv_auth.LEVELS)

    def test_the_mutation_actually_flips_which_side_wins(self):
        vault = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, vault, ignore_errors=True)
        with open(os.path.join(vault, "a.md"), "w", encoding="utf-8") as fh:
            fh.write(note("derived", "claim: the total is 5 [evidence: a.md]"))
        with open(os.path.join(vault, "b.md"), "w", encoding="utf-8") as fh:
            fh.write(note("casual", "claim: the total is 9 [evidence: b.md]"))

        real_auth = surv._load_sibling("bm_vault_authority")
        triage_mod = surv._load_sibling("bm_vault_triage")
        _, _, contradictions, _ = triage_mod.scan(vault)
        a, b = contradictions[0]

        before_winner, _, _ = surv.resolve(vault, a, b, "value", real_auth, overrides=[])
        self.assertEqual(before_winner["path"], "a.md", "derived should win before mutation")

        self._mutate_levels()
        surv_mod = load_from("bm_vault_survivorship", self.tmp)
        mutated_auth = surv_mod._load_sibling("bm_vault_authority")
        after_winner, _, _ = surv_mod.resolve(vault, a, b, "value", mutated_auth, overrides=[])
        self.assertEqual(after_winner["path"], "b.md",
                          "casual now outranks derived in the mutated table, "
                          "so the winner must flip")


class AnOverrideRecordsItsHistory(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.store = os.path.join(self.tmp, "overrides.jsonl")

    def test_dry_run_writes_nothing(self):
        rc = surv.cmd_override("value", "b.md", "khalil", None, False, self.store)
        self.assertEqual(rc, 0)
        self.assertFalse(os.path.exists(self.store))

    def test_missing_by_is_refused(self):
        rc = surv.cmd_override("value", "b.md", None, None, True, self.store)
        self.assertEqual(rc, 2)
        self.assertFalse(os.path.exists(self.store))

    def test_apply_appends_and_never_overwrites(self):
        surv.cmd_override("value", "a.md", "khalil", None, True, self.store)
        surv.cmd_override("value", "b.md", "khalil", None, True, self.store)
        records = surv.load_overrides(self.store)
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["winner"], "a.md")
        self.assertEqual(records[1]["winner"], "b.md")

    def test_the_most_recent_override_is_active_and_outranks_the_table(self):
        surv.cmd_override("value", "a.md", "khalil", None, True, self.store)
        surv.cmd_override("value", "b.md", "khalil", None, True, self.store)
        records = surv.load_overrides(self.store)
        active = surv.active_override("value", None, records)
        self.assertEqual(active["winner"], "b.md")

        auth_mod = surv._load_sibling("bm_vault_authority")
        a = {"path": "a.md", "text": "x", "subject": "s"}
        b = {"path": "b.md", "text": "y", "subject": "s"}
        winner, loser, reason = surv.resolve("/unused", a, b, "value", auth_mod, records)
        self.assertEqual(winner["path"], "b.md")
        self.assertIn("override", reason)


class PerAttributeOrderIsStillTheOneVocabulary(unittest.TestCase):
    def test_an_attribute_can_name_its_own_order(self):
        auth_mod = surv._load_sibling("bm_vault_authority")
        surv.PER_ATTRIBUTE_ORDER["a_test_attribute"] = ("casual", "derived", "source_of_record")
        try:
            order = surv.order_for("a_test_attribute", auth_mod)
        finally:
            del surv.PER_ATTRIBUTE_ORDER["a_test_attribute"]
        self.assertEqual(order, ("casual", "derived", "source_of_record"))

    def test_naming_an_unranked_value_is_a_finding_not_a_guess(self):
        auth_mod = surv._load_sibling("bm_vault_authority")
        surv.PER_ATTRIBUTE_ORDER["a_bad_attribute"] = ("casual", "invented_level")
        try:
            with self.assertRaises(ValueError):
                surv.order_for("a_bad_attribute", auth_mod)
        finally:
            del surv.PER_ATTRIBUTE_ORDER["a_bad_attribute"]


if __name__ == "__main__":
    unittest.main()
