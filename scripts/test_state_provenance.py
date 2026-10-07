#!/usr/bin/env python3
"""Tests for state_provenance.py (EPIC M2.05). Every fixture/receipt here is
a synthetic, throwaway value: no real account, project, or app name is read
or referenced anywhere in this file."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import state_provenance as SP  # noqa: E402
import mobile_state_fixture as F  # noqa: E402
import contract_check as CC  # noqa: E402
from mobile_workflow import Refusal, digest, read_json  # noqa: E402


def _valid_fixture(**overrides):
    fixture = {
        "schema_version": "mobile-state-fixture-v1",
        "fixture_id": "test-journey-fresh-install",
        "account_data_ref": "fixtures/accounts/synthetic-001.json",
        "backend_dataset": {"id": "synthetic-dataset", "version": "1"},
        "app_storage": {"reset_mode": "clean"},
        "keychain_policy": {"mode": "clean"},
        "feature_flags": {"new_onboarding": True},
        "clock": {"mode": "real"},
        "randomness_seed": None,
        "locale": "en-US",
        "timezone": "Asia/Tokyo",
        "network_profile": {"mode": "online"},
        "permissions": {"location": "not-determined"},
        "location": {"mode": "disabled"},
        "notifications": {"mode": "disabled"},
        "external_services": [],
        "cleanup_policy": {"required": False},
    }
    fixture.update(overrides)
    return fixture


def _valid_apply_receipt(fixture_hash=None, **overrides):
    receipt = {
        "schema": "brother-mobile-state-reset-v1",
        "status": "PASS",
        "fixture_id": "test-journey-fresh-install",
        "platform": "ios",
        "device": "11111111-2222-3333-4444-555555555555",
        "bundle_id": "com.example.fixture-app",
        "mechanisms": {
            "app_storage": {"status": "PASS", "detail": "app_storage: clean"},
            "keychain": {"status": "PASS", "detail": "keychain: preserve"},
            "feature_flags": {"status": "PASS", "detail": "feature_flags: nothing to apply"},
            "fixture_api": {"status": "NO-DATA", "detail": "fixture_api: no endpoint"},
            "local_database": {"status": "NO-DATA", "detail": "local_database: no script"},
            "backend_namespace": {"status": "NO-DATA", "detail": "backend_namespace: no endpoint"},
        },
        "stages": [],
        "limits": ["Each mechanism's own status is the source of truth."],
    }
    if fixture_hash is not None:
        receipt["fixture_hash"] = fixture_hash
    receipt.update(overrides)
    return receipt


class StateProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="brother-state-provenance-tests-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    # -- helpers ------------------------------------------------------------

    def write_json(self, name, value):
        path = self.root / name
        path.write_text(json.dumps(value))
        return str(path)

    def apply_setup(self, fixture=None, receipt=None, stamp_hash=True):
        """Write a fixture + apply receipt to disk. By default the receipt
        carries the fixture's own real content hash, the way
        mobile_state_reset.apply_fixture actually stamps it inside its own
        lease -- set stamp_hash=False to get the shape of a receipt that
        never went through a real apply_fixture run at all."""
        fixture_path = self.write_json("fixture.json", fixture or _valid_fixture())
        real_hash = digest(fixture_path)
        if receipt is None:
            receipt = _valid_apply_receipt(fixture_hash=real_hash if stamp_hash else None)
        receipt_path = self.write_json("receipt.json", receipt)
        return fixture_path, real_hash, receipt_path

    # -- record_setup: success -----------------------------------------------

    def test_record_setup_success_binds_hash_and_mechanisms(self):
        fixture_path, real_hash, receipt_path = self.apply_setup()
        out = self.root / "provenance"
        record, path = SP.record_setup(fixture_path, F.DEFAULT_SCHEMA, receipt_path, out)
        self.assertEqual(record["schema"], SP.PROVENANCE_SCHEMA)
        self.assertEqual(record["fixture_id"], "test-journey-fresh-install")
        self.assertEqual(record["fixture_hash"]["sha256"], real_hash["sha256"])
        self.assertEqual(record["cleanup_policy"], {"required": False})
        self.assertEqual(record["setup"]["status"], "PASS")
        self.assertEqual(set(record["setup"]["mechanisms"]), set(_valid_apply_receipt()["mechanisms"]))
        self.assertIsNone(record["cleanup"])
        self.assertTrue(path.is_file())
        self.assertEqual(read_json(path), record)

    def test_record_setup_accepts_a_real_no_op_mechanism_status(self):
        # mobile_state_reset.py's apply_fixture (fix-pr719-hardening-2026-09-15)
        # reports a mechanism's own status as NO-OP for a correct no-op
        # (keychain preserve, an empty feature_flags map) -- a real, honest
        # receipt shape this module must accept, never refuse as malformed.
        fixture_path = self.write_json("fixture.json", _valid_fixture())
        receipt = _valid_apply_receipt(fixture_hash=digest(fixture_path))
        receipt["mechanisms"]["keychain"] = {"status": "NO-OP", "detail": "keychain preserve: no command run"}
        receipt_path = self.write_json("receipt.json", receipt)
        out = self.root / "provenance"
        record, _path = SP.record_setup(fixture_path, F.DEFAULT_SCHEMA, receipt_path, out)
        self.assertEqual(record["setup"]["mechanisms"]["keychain"]["status"], "NO-OP")

    # -- record_setup: hash mismatch / forgery (the named self-review target) --

    def test_record_setup_refuses_when_fixture_changed_since_apply(self):
        fixture_path, _real_hash, receipt_path = self.apply_setup()
        # The fixture file is edited after the receipt was written -- exactly
        # the swap window this module exists to close.
        Path(fixture_path).write_text(json.dumps(_valid_fixture(locale="fr-FR")))
        out = self.root / "provenance"
        with self.assertRaises(Refusal) as raised:
            SP.record_setup(fixture_path, F.DEFAULT_SCHEMA, receipt_path, out)
        self.assertIn("hash mismatch", str(raised.exception))
        self.assertFalse(out.exists())

    def test_record_setup_refuses_a_hand_typed_receipt_that_never_ran_apply_fixture(self):
        # The reviewer's own proof-of-concept: a fixture and a receipt are
        # both hand-typed straight to disk, apply_fixture is never called,
        # so the receipt carries no fixture_hash at all (the field only
        # exists because apply_fixture stamps it for real). Must be refused,
        # not silently accepted as a PASS provenance record.
        fixture_path, _real_hash, receipt_path = self.apply_setup(stamp_hash=False)
        out = self.root / "provenance"
        with self.assertRaises(Refusal) as raised:
            SP.record_setup(fixture_path, F.DEFAULT_SCHEMA, receipt_path, out)
        self.assertIn("fixture_hash", str(raised.exception))
        self.assertFalse(out.exists())

    def test_record_setup_refuses_when_fixture_missing_at_record_time(self):
        fixture_path, _real_hash, receipt_path = self.apply_setup()
        os.remove(fixture_path)
        out = self.root / "provenance"
        with self.assertRaises(CC.NoData):
            SP.record_setup(fixture_path, F.DEFAULT_SCHEMA, receipt_path, out)
        self.assertFalse(out.exists())

    # -- record_setup: invalid or mismatched fixture -------------------------

    def test_record_setup_refuses_invalid_fixture(self):
        bad = _valid_fixture(); bad["schema_version"] = "wrong"
        fixture_path, _real_hash, receipt_path = self.apply_setup(fixture=bad)
        out = self.root / "provenance"
        with self.assertRaises(Refusal):
            SP.record_setup(fixture_path, F.DEFAULT_SCHEMA, receipt_path, out)
        self.assertFalse(out.exists())

    def test_record_setup_refuses_fixture_id_mismatch(self):
        fixture_path = self.write_json("fixture.json", _valid_fixture())
        receipt = _valid_apply_receipt(fixture_hash=digest(fixture_path), fixture_id="a-different-journey")
        receipt_path = self.write_json("receipt.json", receipt)
        out = self.root / "provenance"
        with self.assertRaises(Refusal) as raised:
            SP.record_setup(fixture_path, F.DEFAULT_SCHEMA, receipt_path, out)
        self.assertIn("fixture_id", str(raised.exception))
        self.assertFalse(out.exists())

    def test_record_setup_refuses_wrong_receipt_schema(self):
        fixture_path = self.write_json("fixture.json", _valid_fixture())
        receipt = _valid_apply_receipt(fixture_hash=digest(fixture_path), schema="not-the-right-schema")
        receipt_path = self.write_json("receipt.json", receipt)
        out = self.root / "provenance"
        with self.assertRaises(Refusal):
            SP.record_setup(fixture_path, F.DEFAULT_SCHEMA, receipt_path, out)
        self.assertFalse(out.exists())

    # -- record_setup: incomplete apply evidence (the other named target) ---

    def test_record_setup_refuses_empty_mechanisms(self):
        fixture_path = self.write_json("fixture.json", _valid_fixture())
        receipt = _valid_apply_receipt(fixture_hash=digest(fixture_path), mechanisms={})
        receipt_path = self.write_json("receipt.json", receipt)
        out = self.root / "provenance"
        with self.assertRaises(Refusal) as raised:
            SP.record_setup(fixture_path, F.DEFAULT_SCHEMA, receipt_path, out)
        self.assertIn("mechanisms", str(raised.exception))
        self.assertFalse(out.exists())

    def test_record_setup_refuses_mechanism_missing_detail(self):
        fixture_path = self.write_json("fixture.json", _valid_fixture())
        receipt = _valid_apply_receipt(fixture_hash=digest(fixture_path))
        receipt["mechanisms"]["keychain"] = {"status": "PASS"}  # no detail
        receipt_path = self.write_json("receipt.json", receipt)
        out = self.root / "provenance"
        with self.assertRaises(Refusal):
            SP.record_setup(fixture_path, F.DEFAULT_SCHEMA, receipt_path, out)
        self.assertFalse(out.exists())

    def test_record_setup_refuses_missing_apply_receipt(self):
        fixture_path, _real_hash, receipt_path = self.apply_setup()
        os.remove(receipt_path)
        out = self.root / "provenance"
        with self.assertRaises(Refusal):
            SP.record_setup(fixture_path, F.DEFAULT_SCHEMA, receipt_path, out)
        self.assertFalse(out.exists())

    def test_record_setup_refuses_existing_output(self):
        fixture_path, _real_hash, receipt_path = self.apply_setup()
        out = self.root / "provenance"
        out.mkdir()
        with self.assertRaises(Refusal):
            SP.record_setup(fixture_path, F.DEFAULT_SCHEMA, receipt_path, out)

    # -- record_cleanup: success across every receipt shape ------------------

    # Matches _valid_apply_receipt()'s own platform/device/bundle_id, since
    # record_cleanup now correlates identity against the setup record.
    SETUP_PLATFORM = "ios"
    SETUP_DEVICE = "11111111-2222-3333-4444-555555555555"
    SETUP_BUNDLE_ID = "com.example.fixture-app"

    def _make_provenance(self, fixture=None):
        fixture_path, _real_hash, receipt_path = self.apply_setup(fixture=fixture)
        out = self.root / "provenance"
        _record, path = SP.record_setup(fixture_path, F.DEFAULT_SCHEMA, receipt_path, out)
        return path

    def test_record_cleanup_success_uninstall_shape(self):
        provenance_path = self._make_provenance()
        cleanup_receipt = self.write_json("cleanup.json", {
            "schema": "brother-mobile-state-uninstall-v1", "status": "PASS",
            "platform": self.SETUP_PLATFORM, "device": self.SETUP_DEVICE, "bundle_id": self.SETUP_BUNDLE_ID,
            "result": {"status": "PASS", "detail": "uninstall: removed com.example.fixture-app"},
            "stages": [],
        })
        out = self.root / "provenance-final.json"
        record, path = SP.record_cleanup(str(provenance_path), cleanup_receipt, out)
        self.assertEqual(record["cleanup"]["status"], "PASS")
        self.assertEqual(record["cleanup"]["schema"], "brother-mobile-state-uninstall-v1")
        self.assertEqual(record["cleanup"]["device"], self.SETUP_DEVICE)
        self.assertTrue(path.is_file())
        # The original setup-only record is untouched.
        self.assertIsNone(read_json(provenance_path)["cleanup"])

    def test_record_cleanup_success_capture_checkpoint_shape(self):
        provenance_path = self._make_provenance()
        checkpoint_file = self.root / "cp.tar"
        checkpoint_file.write_text("fake tar bytes")
        cleanup_receipt = self.write_json("cleanup.json", {
            "schema": "brother-mobile-state-capture-checkpoint-v1", "status": "PASS",
            "platform": self.SETUP_PLATFORM, "device": self.SETUP_DEVICE, "bundle_id": self.SETUP_BUNDLE_ID,
            "checkpoint": str(checkpoint_file), "digest": digest(str(checkpoint_file)), "stages": [],
        })
        out = self.root / "provenance-final.json"
        record, _path = SP.record_cleanup(str(provenance_path), cleanup_receipt, out)
        self.assertEqual(record["cleanup"]["status"], "PASS")

    def test_record_cleanup_success_reset_v1_shape(self):
        # A restore-checkpoint apply used as the cleanup mechanism.
        provenance_path = self._make_provenance()
        fixture_path = self.write_json("fixture2.json", _valid_fixture())
        cleanup_receipt_body = _valid_apply_receipt(fixture_hash=digest(fixture_path))
        cleanup_receipt = self.write_json("cleanup.json", cleanup_receipt_body)
        out = self.root / "provenance-final.json"
        record, _path = SP.record_cleanup(str(provenance_path), cleanup_receipt, out)
        self.assertEqual(record["cleanup"]["status"], "PASS")

    # -- record_cleanup: identity correlation (MAJOR 1, the named self-review target) --

    def test_record_cleanup_refuses_cross_platform_receipt(self):
        # The reviewer's own proof-of-concept: an Android uninstall receipt
        # for an unrelated app, bound onto an iOS setup record.
        provenance_path = self._make_provenance()
        cleanup_receipt = self.write_json("cleanup.json", {
            "schema": "brother-mobile-state-uninstall-v1", "status": "PASS",
            "platform": "android", "device": "some-android-emulator-id",
            "bundle_id": "com.unrelated.other-app",
            "result": {"status": "PASS", "detail": "uninstall: removed com.unrelated.other-app"},
        })
        out = self.root / "provenance-final.json"
        with self.assertRaises(Refusal) as raised:
            SP.record_cleanup(str(provenance_path), cleanup_receipt, out)
        self.assertIn("does not match", str(raised.exception))
        self.assertFalse(out.exists())

    def test_record_cleanup_refuses_bundle_id_mismatch_same_platform(self):
        provenance_path = self._make_provenance()
        cleanup_receipt = self.write_json("cleanup.json", {
            "schema": "brother-mobile-state-uninstall-v1", "status": "PASS",
            "platform": self.SETUP_PLATFORM, "device": self.SETUP_DEVICE,
            "bundle_id": "com.unrelated.other-app",
            "result": {"status": "PASS", "detail": "uninstall: removed com.unrelated.other-app"},
        })
        out = self.root / "provenance-final.json"
        with self.assertRaises(Refusal):
            SP.record_cleanup(str(provenance_path), cleanup_receipt, out)
        self.assertFalse(out.exists())

    # -- record_cleanup: cleanup_policy.method mismatch (MAJOR 2b) -----------

    def test_record_cleanup_refuses_method_mismatch(self):
        fixture = _valid_fixture(cleanup_policy={"required": True, "method": "restore-checkpoint"})
        provenance_path = self._make_provenance(fixture=fixture)
        # uninstall implies "delete-account" per CLEANUP_METHOD_BY_SCHEMA, not
        # the fixture's declared "restore-checkpoint".
        cleanup_receipt = self.write_json("cleanup.json", {
            "schema": "brother-mobile-state-uninstall-v1", "status": "PASS",
            "platform": self.SETUP_PLATFORM, "device": self.SETUP_DEVICE, "bundle_id": self.SETUP_BUNDLE_ID,
            "result": {"status": "PASS", "detail": "uninstall: removed app"},
        })
        out = self.root / "provenance-final.json"
        with self.assertRaises(Refusal) as raised:
            SP.record_cleanup(str(provenance_path), cleanup_receipt, out)
        self.assertIn("cleanup_policy.method", str(raised.exception))
        self.assertFalse(out.exists())

    # -- record_cleanup: incomplete cleanup (the named self-review target) --

    def test_record_cleanup_refuses_uninstall_receipt_with_no_result(self):
        provenance_path = self._make_provenance()
        cleanup_receipt = self.write_json("cleanup.json", {
            "schema": "brother-mobile-state-uninstall-v1", "status": "PASS",
        })
        out = self.root / "provenance-final.json"
        with self.assertRaises(Refusal) as raised:
            SP.record_cleanup(str(provenance_path), cleanup_receipt, out)
        self.assertIn("result", str(raised.exception))
        self.assertFalse(out.exists())

    def test_record_cleanup_refuses_capture_checkpoint_with_no_digest(self):
        provenance_path = self._make_provenance()
        cleanup_receipt = self.write_json("cleanup.json", {
            "schema": "brother-mobile-state-capture-checkpoint-v1", "status": "PASS",
            "checkpoint": "some/path.tar",
        })
        out = self.root / "provenance-final.json"
        with self.assertRaises(Refusal):
            SP.record_cleanup(str(provenance_path), cleanup_receipt, out)
        self.assertFalse(out.exists())

    def test_record_cleanup_refuses_reset_v1_receipt_with_empty_mechanisms(self):
        provenance_path = self._make_provenance()
        cleanup_receipt = self.write_json("cleanup.json", _valid_apply_receipt(mechanisms={}))
        out = self.root / "provenance-final.json"
        with self.assertRaises(Refusal):
            SP.record_cleanup(str(provenance_path), cleanup_receipt, out)
        self.assertFalse(out.exists())

    def test_record_cleanup_refuses_unrecognized_schema(self):
        provenance_path = self._make_provenance()
        cleanup_receipt = self.write_json("cleanup.json", {
            "schema": "brother-mobile-state-mystery-v1", "status": "PASS",
        })
        out = self.root / "provenance-final.json"
        with self.assertRaises(Refusal) as raised:
            SP.record_cleanup(str(provenance_path), cleanup_receipt, out)
        self.assertIn("not a recognized", str(raised.exception))
        self.assertFalse(out.exists())

    def test_record_cleanup_refuses_schema_outside_prefix(self):
        provenance_path = self._make_provenance()
        cleanup_receipt = self.write_json("cleanup.json", {
            "schema": "totally-unrelated-schema", "status": "PASS",
        })
        out = self.root / "provenance-final.json"
        with self.assertRaises(Refusal):
            SP.record_cleanup(str(provenance_path), cleanup_receipt, out)
        self.assertFalse(out.exists())

    def test_record_cleanup_refuses_missing_receipt_file(self):
        provenance_path = self._make_provenance()
        out = self.root / "provenance-final.json"
        with self.assertRaises(Refusal):
            SP.record_cleanup(str(provenance_path), str(self.root / "nope.json"), out)
        self.assertFalse(out.exists())

    # -- record_cleanup: double-binding and overwrite refusal ---------------

    def test_record_cleanup_refuses_when_already_bound(self):
        provenance_path = self._make_provenance()
        cleanup_receipt = self.write_json("cleanup.json", {
            "schema": "brother-mobile-state-uninstall-v1", "status": "PASS",
            "platform": self.SETUP_PLATFORM, "device": self.SETUP_DEVICE, "bundle_id": self.SETUP_BUNDLE_ID,
            "result": {"status": "PASS", "detail": "uninstall: removed app"},
        })
        first_out = self.root / "provenance-final.json"
        _record, path = SP.record_cleanup(str(provenance_path), cleanup_receipt, first_out)
        second_out = self.root / "provenance-final-2.json"
        with self.assertRaises(Refusal) as raised:
            SP.record_cleanup(str(path), cleanup_receipt, second_out)
        self.assertIn("already recorded", str(raised.exception))
        self.assertFalse(second_out.exists())

    def test_record_cleanup_refuses_existing_output(self):
        provenance_path = self._make_provenance()
        cleanup_receipt = self.write_json("cleanup.json", {
            "schema": "brother-mobile-state-uninstall-v1", "status": "PASS",
            "platform": self.SETUP_PLATFORM, "device": self.SETUP_DEVICE, "bundle_id": self.SETUP_BUNDLE_ID,
            "result": {"status": "PASS", "detail": "uninstall: removed app"},
        })
        out = self.root / "provenance-final.json"
        out.write_text("{}")
        with self.assertRaises(Refusal):
            SP.record_cleanup(str(provenance_path), cleanup_receipt, out)

    def test_record_cleanup_refuses_malformed_provenance_schema(self):
        bogus = self.write_json("bogus-provenance.json", {"schema": "not-provenance", "cleanup": None})
        cleanup_receipt = self.write_json("cleanup.json", {
            "schema": "brother-mobile-state-uninstall-v1", "status": "PASS",
            "result": {"status": "PASS", "detail": "uninstall: removed app"},
        })
        out = self.root / "provenance-final.json"
        with self.assertRaises(Refusal):
            SP.record_cleanup(bogus, cleanup_receipt, out)

    # -- check_complete: cleanup_policy.required with no cleanup (MAJOR 2a) --

    def test_check_complete_refuses_required_cleanup_missing(self):
        fixture = _valid_fixture(cleanup_policy={"required": True, "method": "delete-account"})
        provenance_path = self._make_provenance(fixture=fixture)
        with self.assertRaises(Refusal) as raised:
            SP.check_complete(read_json(provenance_path))
        self.assertIn("cleanup_policy.required", str(raised.exception))

    def test_check_complete_passes_when_cleanup_not_required(self):
        provenance_path = self._make_provenance()  # default fixture: cleanup_policy.required False
        record = SP.check_complete(read_json(provenance_path))
        self.assertIsNone(record["cleanup"])

    def test_check_complete_passes_when_required_cleanup_present(self):
        fixture = _valid_fixture(cleanup_policy={"required": True, "method": "delete-account"})
        provenance_path = self._make_provenance(fixture=fixture)
        cleanup_receipt = self.write_json("cleanup.json", {
            "schema": "brother-mobile-state-uninstall-v1", "status": "PASS",
            "platform": self.SETUP_PLATFORM, "device": self.SETUP_DEVICE, "bundle_id": self.SETUP_BUNDLE_ID,
            "result": {"status": "PASS", "detail": "uninstall: removed app"},
        })
        final_out = self.root / "provenance-final.json"
        SP.record_cleanup(str(provenance_path), cleanup_receipt, final_out)
        record = SP.check_complete(read_json(final_out))
        self.assertIsNotNone(record["cleanup"])

    # -- main(): CLI wiring ---------------------------------------------------

    def test_main_record_setup_and_record_cleanup_round_trip(self):
        fixture_path, _real_hash, receipt_path = self.apply_setup()
        out = str(self.root / "cli-provenance")
        code = SP.main(["record-setup", "--fixture", fixture_path,
                        "--apply-receipt", receipt_path, "--out", out])
        self.assertEqual(code, 0)
        provenance_path = Path(out) / "provenance.json"
        self.assertTrue(provenance_path.is_file())

        cleanup_receipt = self.write_json("cleanup.json", {
            "schema": "brother-mobile-state-uninstall-v1", "status": "PASS",
            "platform": self.SETUP_PLATFORM, "device": self.SETUP_DEVICE, "bundle_id": self.SETUP_BUNDLE_ID,
            "result": {"status": "PASS", "detail": "uninstall: removed app"},
        })
        final_out = str(self.root / "cli-provenance-final.json")
        code = SP.main(["record-cleanup", "--provenance", str(provenance_path),
                        "--cleanup-receipt", cleanup_receipt, "--out", final_out])
        self.assertEqual(code, 0)
        self.assertEqual(read_json(final_out)["cleanup"]["status"], "PASS")

        code = SP.main(["check-complete", "--provenance", final_out])
        self.assertEqual(code, 0)

    def test_main_record_setup_hash_mismatch_exits_fail(self):
        fixture_path, _real_hash, receipt_path = self.apply_setup()
        Path(fixture_path).write_text(json.dumps(_valid_fixture(locale="de-DE")))
        out = str(self.root / "cli-provenance")
        code = SP.main(["record-setup", "--fixture", fixture_path,
                        "--apply-receipt", receipt_path, "--out", out])
        self.assertEqual(code, 1)

    def test_main_record_setup_hand_typed_receipt_exits_fail(self):
        fixture_path, _real_hash, receipt_path = self.apply_setup(stamp_hash=False)
        out = str(self.root / "cli-provenance")
        code = SP.main(["record-setup", "--fixture", fixture_path,
                        "--apply-receipt", receipt_path, "--out", out])
        self.assertEqual(code, 1)

    def test_main_missing_fixture_exits_no_data(self):
        fixture_path, _real_hash, receipt_path = self.apply_setup()
        os.remove(fixture_path)
        out = str(self.root / "cli-provenance")
        code = SP.main(["record-setup", "--fixture", fixture_path,
                        "--apply-receipt", receipt_path, "--out", out])
        self.assertEqual(code, 2)

    def test_main_check_complete_exits_fail_when_required_cleanup_missing(self):
        fixture = _valid_fixture(cleanup_policy={"required": True, "method": "delete-account"})
        provenance_path = self._make_provenance(fixture=fixture)
        code = SP.main(["check-complete", "--provenance", str(provenance_path)])
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
