#!/usr/bin/env python3
"""Calibration for tools/bm_vault_attribute_provenance.py, VB13-04.

The property under test: a value's source and verification state are
queryable per (note, attribute), a verification claim always names its
mechanism or its promoter, an unknown status is refused before anything is
written, a second write versions rather than clobbers the first, and the
by-status-and-age query used by a later census extension actually filters on
both axes. No em or en dashes anywhere in this file.
"""
import datetime
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bm_vault_attribute_provenance as ap   # noqa: E402

# E100: one sandbox for every temp tree this process makes, removed at exit.
import os as _e100_os, sys as _e100_sys  # noqa: E402
_e100_sys.path.append(_e100_os.path.join(
    _e100_os.path.dirname(_e100_os.path.abspath(__file__)), '../../../scripts'))
try:  # noqa: E402
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    # A packager (scripts/export_public.py, make_benchmark_bundle.py)
    # can copy this test without scripts/tmp_sandbox.py beside it. Say
    # so rather than dying: the sandbox is hygiene, not the subject.
    _e100_sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % _e100_os.path.basename(__file__))


def note_text():
    return "\n".join(["---", "type: reference", "status: open", "---", "", "# a note"]) + "\n"


class VaultFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-attr-prov-")
        self.vault = os.path.join(self.tmp, "vault")
        os.makedirs(self.vault)
        self.store = os.path.join(self.tmp, "store.json")
        with open(os.path.join(self.vault, "a.md"), "w", encoding="utf-8") as fh:
            fh.write(note_text())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _set(self, **kwargs):
        defaults = dict(vault=self.vault, store_path=self.store, note_ident="a.md",
                         attribute="owner", source="khalil", status="unverified")
        defaults.update(kwargs)
        return ap.set_record(**defaults)


class AnAttributeSetByAFixtureSourceCarriesQueryableProvenance(VaultFixture):
    def test_unverified_record_is_written_and_readable(self):
        ok, message, record = self._set(source="intake", status="unverified")
        self.assertTrue(ok, message)
        self.assertEqual(record["verification_status"], "unverified")
        self.assertEqual(record["source"], "intake")
        self.assertEqual(record["note"], "a.md")
        self.assertEqual(record["version"], 1)

        data = ap.load_store(self.store)
        got = ap.latest_record(data["records"], "a.md", "owner")
        self.assertIsNotNone(got)
        self.assertEqual(got["source"], "intake")
        self.assertEqual(got["verification_status"], "unverified")

    def test_unresolvable_note_is_no_data_and_writes_nothing(self):
        ok, message, record = self._set(note_ident="no-such-note.md")
        self.assertFalse(ok)
        self.assertTrue(message.startswith("NO-DATA"), message)
        self.assertIsNone(record)
        self.assertFalse(os.path.isfile(self.store))


class AMachineCheckedRecordNamesItsMechanism(VaultFixture):
    def test_machine_checked_without_checked_by_is_refused(self):
        ok, message, record = self._set(status="machine-checked", checked_by=None)
        self.assertFalse(ok)
        self.assertTrue(message.startswith("REFUSED"), message)
        self.assertIn("checked-by", message)
        self.assertIsNone(record)
        self.assertFalse(os.path.isfile(self.store), "a refusal must write nothing")

    def test_machine_checked_with_checked_by_is_recorded(self):
        ok, message, record = self._set(status="machine-checked",
                                         checked_by="bm_vault_staleness")
        self.assertTrue(ok, message)
        self.assertEqual(record["verification_status"], "machine-checked")
        self.assertEqual(record["checked_by"], "bm_vault_staleness")


class APromotionWithByFlipsToHumanVerified(VaultFixture):
    def test_human_verified_without_by_is_refused(self):
        ok, message, record = self._set(status="human-verified", by=None)
        self.assertFalse(ok)
        self.assertTrue(message.startswith("REFUSED"), message)
        self.assertIn("--by", message)
        self.assertIsNone(record)
        self.assertFalse(os.path.isfile(self.store))

    def test_human_verified_with_by_records_the_promoter(self):
        ok, message, record = self._set(status="human-verified", by="khalil")
        self.assertTrue(ok, message)
        self.assertEqual(record["verification_status"], "human-verified")
        self.assertEqual(record["promoted_by"], "khalil")


class AnUnknownStatusValueIsRefused(VaultFixture):
    def test_bogus_status_is_refused_before_any_write(self):
        ok, message, record = self._set(status="pretty-sure")
        self.assertFalse(ok)
        self.assertTrue(message.startswith("REFUSED"), message)
        self.assertIsNone(record)
        self.assertFalse(os.path.isfile(self.store))


class ASecondWriteVersionsRatherThanOverwrites(VaultFixture):
    def test_both_versions_survive_and_the_latest_wins_on_read(self):
        ok1, _m1, r1 = self._set(source="intake", status="unverified", at="2026-08-01")
        self.assertTrue(ok1)
        ok2, _m2, r2 = self._set(source="machine:stealth/ox-alpha",
                                  status="machine-checked", checked_by="bm_vault_staleness",
                                  at="2026-08-20")
        self.assertTrue(ok2)
        self.assertEqual(r1["version"], 1)
        self.assertEqual(r2["version"], 2)

        data = ap.load_store(self.store)
        rows = ap.history(data["records"], "a.md", "owner")
        self.assertEqual(len(rows), 2, "both versions must be present in history")
        self.assertEqual(rows[0]["version"], 1)
        self.assertEqual(rows[1]["version"], 2)

        latest = ap.latest_record(data["records"], "a.md", "owner")
        self.assertEqual(latest["version"], 2, "the latest write must win on read")
        self.assertEqual(latest["verification_status"], "machine-checked")


class TheByStatusAndAgeQuery(VaultFixture):
    def test_returns_machine_checked_records_past_a_fixture_age_and_no_data_on_empty(self):
        # An empty store: NO-DATA, never a silent empty pass.
        self.assertFalse(os.path.isfile(self.store))
        empty = ap.load_store(self.store)
        self.assertEqual(ap.by_status(empty["records"], "machine-checked", min_age_days=10), [])

        self._set(attribute="owner", status="machine-checked",
                   checked_by="bm_vault_staleness", at="2026-08-01")
        self._set(attribute="description", status="machine-checked",
                   checked_by="bm_vault_staleness", at="2026-08-29")
        self._set(attribute="status", status="unverified", source="intake", at="2026-08-01")

        data = ap.load_store(self.store)
        as_of = datetime.date(2026, 8, 30)
        old_enough = ap.by_status(data["records"], "machine-checked", min_age_days=10, as_of=as_of)
        self.assertEqual([r["attribute"] for r in old_enough], ["owner"],
                          "only the record at least 10 days old must return, "
                          "and only machine-checked records at all")

    def test_cli_by_status_reports_no_data_on_an_empty_store(self):
        class Args:
            store = self.store
            status = "machine-checked"
            min_age_days = 0
            as_of = None
            json = False
        self.assertEqual(ap.cmd_by_status(Args()), 2)


class ZeroSilentExcepts(VaultFixture):
    def test_a_corrupt_store_raises_a_named_error_rather_than_reading_as_empty(self):
        with open(self.store, "w", encoding="utf-8") as fh:
            fh.write("{not valid json")
        with self.assertRaises(RuntimeError):
            ap.load_store(self.store)

    def test_a_wrongly_shaped_store_raises_a_named_error(self):
        with open(self.store, "w", encoding="utf-8") as fh:
            json.dump({"nope": True}, fh)
        with self.assertRaises(RuntimeError):
            ap.load_store(self.store)


if __name__ == "__main__":
    unittest.main()
