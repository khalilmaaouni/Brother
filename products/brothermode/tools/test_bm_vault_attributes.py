#!/usr/bin/env python3
"""Calibration for tools/bm_vault_attributes.py, WBS row VB13-03.

Driven backwards, same discipline as test_bm_vault_contract.py: each behavior
is proved present under the real declared table, then a mutated copy of that
table is used to prove the finding actually depends on the table rather than
on something baked into the test.

No em or en dashes anywhere in this file.
"""
import copy
import datetime
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "bm_vault_attributes.py")
sys.path.insert(0, HERE)
import bm_vault_attributes as a  # noqa: E402


def _date(s):
    return datetime.date.fromisoformat(s)


class InheritanceIsResolvedNotFlat(unittest.TestCase):
    def test_child_inherits_the_parent_set(self):
        resolved = a.resolve_attributes("product")
        # "id" and "name" only ever appear on item; product never redeclares
        # them, so they must still resolve for product.
        self.assertIn("id", resolved)
        self.assertIn("name", resolved)
        self.assertEqual(resolved["id"]["source"], "item")
        self.assertFalse(resolved["id"]["override"])

    def test_grandchild_inherits_the_whole_chain(self):
        resolved = a.resolve_attributes("kit")
        self.assertIn("id", resolved)       # from item
        self.assertIn("sku", resolved)      # from product
        self.assertIn("component_count", resolved)  # kit's own

    def test_child_adds_its_own_attribute(self):
        resolved = a.resolve_attributes("product")
        self.assertIn("sku", resolved)
        self.assertEqual(resolved["sku"]["source"], "product")

    def test_redeclared_attribute_is_visible_as_an_override(self):
        resolved = a.resolve_attributes("product")
        self.assertTrue(resolved["status"]["override"], resolved["status"])
        self.assertEqual(resolved["status"]["overrides"], "item")
        self.assertEqual(resolved["status"]["source"], "product")
        # And the override actually changed the rule, not merely relabeled it.
        self.assertTrue(resolved["status"]["required"])

    def test_removing_the_override_declaration_stops_it_being_an_override(self):
        """Table-mutation half: drop product's own "status" redeclaration
        from a copy of the table, and prove the override marker disappears
        (status now resolves cleanly from item alone), proving the marker is
        computed from the table rather than hardcoded in resolve_attributes."""
        mutated = copy.deepcopy(a.CLASS_ATTRIBUTES)
        del mutated["product"]["attributes"]["status"]
        resolved = a.resolve_attributes("product", table=mutated)
        self.assertFalse(resolved["status"]["override"], resolved["status"])
        self.assertIsNone(resolved["status"]["overrides"])
        self.assertEqual(resolved["status"]["source"], "item")

    def test_unknown_class_is_out_of_scope_not_invented(self):
        self.assertIsNone(a.resolve_attributes("no-such-class"))

    def test_cyclical_parent_chain_raises(self):
        mutated = copy.deepcopy(a.CLASS_ATTRIBUTES)
        mutated["item"]["parent"] = "kit"  # item -> kit -> product -> item
        with self.assertRaises(ValueError):
            a.resolve_attributes("item", table=mutated)


class ChannelRequirednessRefusesForOneChannel(unittest.TestCase):
    def test_missing_channel_required_attribute_refuses_for_that_channel(self):
        fmap = {"id": "1", "name": "n", "status": "open", "sku": "SKU-1", "unit": "kg"}
        missing = a.missing_for_channel("product", fmap, "web")
        self.assertIn("pack_type", missing)

    def test_same_record_passes_for_the_other_channel(self):
        fmap = {"id": "1", "name": "n", "status": "open", "sku": "SKU-1", "unit": "kg"}
        missing = a.missing_for_channel("product", fmap, "wholesale")
        self.assertNotIn("pack_type", missing)

    def test_dropping_the_channel_override_falls_back_to_the_class_default(self):
        """Table-mutation half: with no per-channel entry at all, pack_type's
        own class-level required=False governs every channel, so "web" no
        longer demands it."""
        mutated = copy.deepcopy(a.CHANNEL_REQUIRED)
        del mutated["product"]["pack_type"]
        fmap = {"id": "1", "name": "n", "status": "open", "sku": "SKU-1", "unit": "kg"}
        missing = a.missing_for_channel("product", fmap, "web", channel_table=mutated)
        self.assertNotIn("pack_type", missing)

    def test_unknown_class_is_none_not_an_empty_pass(self):
        self.assertIsNone(a.missing_for_channel("no-such-class", {}, "web"))


class GovernedCodeLists(unittest.TestCase):
    def setUp(self):
        self.lists = {
            "unit": {
                "kg": [{"valid_from": None, "valid_to": None}],
                "lb": [{"valid_from": _date("2020-01-01"), "valid_to": _date("2024-12-31")}],
            },
            "enum:pack_type": {
                "can": [{"valid_from": None, "valid_to": None}],
            },
        }

    def test_unit_not_in_the_code_list_refuses(self):
        f = a.classify_code_value(self.lists, "unit", "gallon", _date("2026-08-30"), is_new=True)
        self.assertIsNotNone(f)
        self.assertEqual(f["kind"], "ERROR")

    def test_enum_value_outside_dated_list_refuses_on_new_record(self):
        f = a.classify_code_value(self.lists, "enum:pack_type", "jar", _date("2026-08-30"), is_new=True)
        self.assertIsNotNone(f)
        self.assertEqual(f["kind"], "ERROR")

    def test_same_violation_queues_on_a_legacy_record(self):
        f = a.classify_code_value(self.lists, "enum:pack_type", "jar", _date("2020-06-01"), is_new=False)
        self.assertIsNotNone(f)
        self.assertEqual(f["kind"], "QUEUE")

    def test_retired_value_resolves_for_dates_inside_its_interval(self):
        self.assertTrue(a.value_covers(self.lists, "unit", "lb", _date("2023-01-01")))

    def test_retired_value_refuses_after_its_interval(self):
        self.assertFalse(a.value_covers(self.lists, "unit", "lb", _date("2025-06-01")))

    def test_open_interval_value_covers_every_date(self):
        self.assertTrue(a.value_covers(self.lists, "unit", "kg", _date("1999-01-01")))
        self.assertTrue(a.value_covers(self.lists, "unit", "kg", _date("2099-01-01")))

    def test_adding_a_duplicate_open_interval_for_one_value_refuses(self):
        ok, _msg = a.add_value(self.lists, "unit", "kg", _date("2026-08-30"))
        self.assertFalse(ok)

    def test_adding_a_new_value_opens_an_interval(self):
        ok, _msg = a.add_value(self.lists, "unit", "gallon", _date("2026-08-30"))
        self.assertTrue(ok)
        self.assertTrue(a.value_covers(self.lists, "unit", "gallon", _date("2026-09-01")))
        # Declared, but the date is before its valid_from: known value, not
        # covered, which is False (not None: None means never declared).
        self.assertFalse(a.value_covers(self.lists, "unit", "gallon", _date("2026-08-01")))

    def test_retiring_closes_never_deletes(self):
        ok, _msg = a.retire_value(self.lists, "unit", "kg", _date("2026-08-30"))
        self.assertTrue(ok)
        self.assertIn("kg", self.lists["unit"])  # never deleted
        self.assertTrue(a.value_covers(self.lists, "unit", "kg", _date("2026-08-30")))
        self.assertFalse(a.value_covers(self.lists, "unit", "kg", _date("2026-09-01")))

    def test_retiring_a_value_with_no_open_interval_refuses(self):
        ok, _msg = a.retire_value(self.lists, "unit", "lb", _date("2026-08-30"))
        self.assertFalse(ok)

    def test_retiring_an_undeclared_value_refuses(self):
        ok, _msg = a.retire_value(self.lists, "unit", "no-such-value", _date("2026-08-30"))
        self.assertFalse(ok)

    def test_unknown_list_or_value_is_none_not_false(self):
        self.assertIsNone(a.value_covers(self.lists, "unit", "no-such-value", _date("2026-08-30")))
        self.assertIsNone(a.value_covers(self.lists, "no-such-list", "kg", _date("2026-08-30")))

    def test_check_code_values_skips_a_blank_attribute(self):
        fmap = {"id": "1", "name": "n", "status": "open", "sku": "SKU-1", "created": "2026-08-30"}
        findings = a.check_code_values("product", fmap, self.lists, is_new=True)
        self.assertEqual(findings, [])  # unit/pack_type both blank, not this check's job

    def test_check_code_values_flags_a_bad_unit_with_the_field_name(self):
        fmap = {"id": "1", "name": "n", "status": "open", "sku": "SKU-1",
                "unit": "gallon", "created": "2026-08-30"}
        findings = a.check_code_values("product", fmap, self.lists, is_new=True)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["field"], "unit")
        self.assertEqual(findings[0]["kind"], "ERROR")


class SaveAndLoadRoundTrip(unittest.TestCase):
    def test_save_then_load_preserves_intervals(self):
        with tempfile.TemporaryDirectory() as vault:
            lists = {"unit": {"kg": [{"valid_from": None, "valid_to": None}],
                               "lb": [{"valid_from": _date("2020-01-01"), "valid_to": _date("2024-12-31")}]}}
            a.save_code_lists(vault, lists)
            loaded, err = a.load_code_lists(vault)
            self.assertIsNone(err)
            self.assertEqual(loaded, lists)

    def test_absent_file_is_none_none(self):
        with tempfile.TemporaryDirectory() as vault:
            data, err = a.load_code_lists(vault)
            self.assertIsNone(data)
            self.assertIsNone(err)

    def test_malformed_file_is_an_error_not_none(self):
        with tempfile.TemporaryDirectory() as vault:
            os.makedirs(os.path.join(vault, "99-System"))
            with open(os.path.join(vault, "99-System", "code-lists.json"), "w") as fh:
                fh.write("not json")
            data, err = a.load_code_lists(vault)
            self.assertIsNone(data)
            self.assertIsNotNone(err)


class CLISmoke(unittest.TestCase):
    def _run(self, *args):
        return subprocess.run([sys.executable, TOOL] + list(args), capture_output=True, text=True)

    def test_classes_lists_the_override(self):
        r = self._run("classes", "--class", "product", "--json")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        payload = json.loads(r.stdout)
        self.assertTrue(payload["product"]["status"]["override"])

    def test_check_channel_refuses_then_passes(self):
        with tempfile.TemporaryDirectory() as vault:
            rel = "n.md"
            with open(os.path.join(vault, rel), "w") as fh:
                fh.write("---\nid: 1\ntype: entity\ncreated: 2026-08-30\n"
                         "name: n\nstatus: open\nsku: SKU-1\nunit: kg\n---\nbody\n")
            r_web = self._run("check-channel", "--class", "product", "--channel", "web",
                               rel, "--vault", vault)
            self.assertEqual(r_web.returncode, 1, r_web.stdout)
            r_wholesale = self._run("check-channel", "--class", "product", "--channel", "wholesale",
                                     rel, "--vault", vault)
            self.assertEqual(r_wholesale.returncode, 0, r_wholesale.stdout)

    def test_codes_check_no_data_on_empty_vault(self):
        with tempfile.TemporaryDirectory() as vault:
            r = self._run("codes", "check", "--all", "--vault", vault)
            self.assertEqual(r.returncode, 2)
            self.assertIn("NO-DATA", r.stdout)

    def test_codes_add_then_check_flags_a_bad_new_note(self):
        with tempfile.TemporaryDirectory() as vault:
            add = self._run("codes", "add-value", "--list", "unit", "--value", "kg",
                             "--valid-from", "2020-01-01", "--vault", vault)
            self.assertEqual(add.returncode, 0, add.stdout)
            with open(os.path.join(vault, "n.md"), "w") as fh:
                fh.write("---\nid: 1\ntype: entity\nclass: product\ncreated: 2026-08-30\n"
                         "name: n\nstatus: open\nsku: SKU-1\nunit: gallon\n---\nbody\n")
            r = self._run("codes", "check", "--all", "--vault", vault, "--json")
            self.assertEqual(r.returncode, 0, r.stdout)
            payload = json.loads(r.stdout)
            self.assertEqual(payload["error_count"], 1)

    def test_codes_add_duplicate_open_interval_refuses(self):
        with tempfile.TemporaryDirectory() as vault:
            first = self._run("codes", "add-value", "--list", "unit", "--value", "kg",
                               "--valid-from", "2020-01-01", "--vault", vault)
            self.assertEqual(first.returncode, 0, first.stdout)
            second = self._run("codes", "add-value", "--list", "unit", "--value", "kg",
                                "--valid-from", "2021-01-01", "--vault", vault)
            self.assertEqual(second.returncode, 1, second.stdout)
            self.assertIn("REFUSED", second.stdout)


if __name__ == "__main__":
    unittest.main()
