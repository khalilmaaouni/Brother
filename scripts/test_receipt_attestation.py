#!/usr/bin/env python3
"""Unit tests for receipt_attestation.py.

Run with: python3 scripts/test_receipt_attestation.py -v
"""
import hashlib
import hmac
import json
import os
import tempfile
import unittest

import receipt_attestation as ra


class BaseTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        self.keys = os.path.join(self.root, "keys")
        os.makedirs(self.keys)
        self.receipt = os.path.join(self.root, "receipt.bin")
        with open(self.receipt, "wb") as fh:
            fh.write(b"the receipt content\n")

    def write_key(self, identity, data=b"secret-key-bytes"):
        with open(os.path.join(self.keys, identity + ".key"), "wb") as fh:
            fh.write(data)

    def receipt_sha256(self):
        with open(self.receipt, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()


class RoundTripTest(BaseTest):
    def test_round_trip_pass(self):
        self.write_key("alice")
        record = ra.build_attestation(
            self.receipt, "bob", "alice", self.keys,
            timestamp="2024-01-01T00:00:00Z",
        )
        self.assertIsInstance(record, dict)
        for field in ra.REQUIRED_FIELDS:
            self.assertIn(field, record)
            self.assertIsInstance(record[field], str)

        verdict, reason = ra.verify_attestation(record, self.receipt, self.keys)
        self.assertEqual(verdict, ra.PASS, reason)

    def test_json_round_trip_pass(self):
        # A record serialized to JSON and re-parsed still verifies: proves
        # the signed bytes are reconstructed the same way on both sides.
        self.write_key("alice")
        record = ra.build_attestation(
            self.receipt, "bob", "alice", self.keys,
            timestamp="2024-01-01T00:00:00Z",
        )
        reparsed = json.loads(json.dumps(record))
        verdict, reason = ra.verify_attestation(reparsed, self.receipt, self.keys)
        self.assertEqual(verdict, ra.PASS, reason)


class TamperingTest(BaseTest):
    def test_modified_receipt_fails(self):
        self.write_key("alice")
        record = ra.build_attestation(
            self.receipt, "bob", "alice", self.keys,
            timestamp="2024-01-01T00:00:00Z",
        )
        # Mutate the receipt bytes after attestation.
        with open(self.receipt, "ab") as fh:
            fh.write(b"tampered\n")

        verdict, reason = ra.verify_attestation(record, self.receipt, self.keys)
        self.assertEqual(verdict, ra.FAIL)
        lowered = reason.lower()
        self.assertTrue(
            "tamper" in lowered or "content hash" in lowered,
            "reason should mention tampering or content hash, got: %r" % reason,
        )


class SelfAttestationTest(BaseTest):
    def test_attest_refuses_self_attestation(self):
        self.write_key("alice")
        with self.assertRaises(ra.AttestationError):
            ra.build_attestation(self.receipt, "alice", "alice", self.keys)

    def test_verify_rejects_handcrafted_self_attestation(self):
        # Bypass build_attestation entirely: hand-construct a record whose
        # producing and attesting identities are identical, with a bogus
        # signature, proving verify refuses it and does not merely rely
        # on the builder never having produced one.
        self.write_key("alice")
        record = {
            "receipt_sha256": self.receipt_sha256(),
            "producing_identity": "alice",
            "attesting_identity": "alice",
            "attested_at": "2024-01-01T00:00:00Z",
            "hmac_sha256": "0" * 64,
        }
        verdict, reason = ra.verify_attestation(record, self.receipt, self.keys)
        self.assertEqual(verdict, ra.FAIL, reason)

    def test_verify_rejects_self_attestation_even_with_a_valid_signature(self):
        # The record above is also refused for its bad signature, which
        # would still read as FAIL if the self-attestation guard itself
        # were ever deleted. This one signs the self-attesting fields for
        # real with alice's own key, so ONLY the self-attestation guard
        # stands between this record and a false PASS.
        self.write_key("alice")
        with open(os.path.join(self.keys, "alice.key"), "rb") as fh:
            key = fh.read()
        signed = {
            "receipt_sha256": self.receipt_sha256(),
            "producing_identity": "alice",
            "attesting_identity": "alice",
            "attested_at": "2024-01-01T00:00:00Z",
        }
        record = dict(signed)
        record["hmac_sha256"] = hmac.new(
            key, ra.receipt_attest.canonical_json_bytes(signed), hashlib.sha256
        ).hexdigest()

        verdict, reason = ra.verify_attestation(record, self.receipt, self.keys)
        self.assertEqual(verdict, ra.FAIL, reason)


class MissingDataTest(BaseTest):
    def test_missing_required_field_is_no_data(self):
        record = {
            "receipt_sha256": self.receipt_sha256(),
            "producing_identity": "bob",
            "attesting_identity": "alice",
            # "attested_at" intentionally omitted
            "hmac_sha256": "0" * 64,
        }
        verdict, _ = ra.verify_attestation(record, self.receipt, self.keys)
        self.assertEqual(verdict, ra.NO_DATA)

    def test_unreadable_receipt_is_no_data(self):
        self.write_key("alice")
        record = {
            "receipt_sha256": "0" * 64,
            "producing_identity": "bob",
            "attesting_identity": "alice",
            "attested_at": "2024-01-01T00:00:00Z",
            "hmac_sha256": "0" * 64,
        }
        missing = os.path.join(self.root, "does-not-exist.bin")
        verdict, _ = ra.verify_attestation(record, missing, self.keys)
        self.assertEqual(verdict, ra.NO_DATA)

    def test_missing_attesting_key_is_no_data(self):
        self.write_key("alice")
        record = ra.build_attestation(
            self.receipt, "bob", "alice", self.keys,
            timestamp="2024-01-01T00:00:00Z",
        )
        os.remove(os.path.join(self.keys, "alice.key"))
        verdict, _ = ra.verify_attestation(record, self.receipt, self.keys)
        self.assertEqual(verdict, ra.NO_DATA)

    def test_non_dict_attestation_is_no_data(self):
        verdict, _ = ra.verify_attestation("not a dict", self.receipt, self.keys)
        self.assertEqual(verdict, ra.NO_DATA)


class SignatureTest(BaseTest):
    def test_forged_signature_fails(self):
        self.write_key("alice")
        record = ra.build_attestation(
            self.receipt, "bob", "alice", self.keys,
            timestamp="2024-01-01T00:00:00Z",
        )
        original = record["hmac_sha256"]
        record["hmac_sha256"] = ("0" if original[0] != "0" else "1") + original[1:]
        self.assertNotEqual(record["hmac_sha256"], original)

        verdict, reason = ra.verify_attestation(record, self.receipt, self.keys)
        self.assertEqual(verdict, ra.FAIL, reason)


class KeyIsolationTest(BaseTest):
    def test_signature_from_other_key_does_not_verify(self):
        self.write_key("alice", b"alice-only-secret")
        self.write_key("mallory", b"mallory-only-secret")

        record = ra.build_attestation(
            self.receipt, "bob", "alice", self.keys,
            timestamp="2024-01-01T00:00:00Z",
        )
        # The signature was produced with alice's key. Relabel the record
        # as mallory's: verify looks up mallory.key and recomputes the
        # HMAC over the (now different) signed fields, so the recorded
        # signature can no longer match.
        record["attesting_identity"] = "mallory"

        verdict, reason = ra.verify_attestation(record, self.receipt, self.keys)
        self.assertEqual(verdict, ra.FAIL, reason)


class IdentityValidationTest(BaseTest):
    """attesting_identity from an untrusted record feeds a file path
    (<keys_dir>/<identity>.key); a path-traversal identity must be refused
    before any file is opened, not resolved."""

    def test_build_refuses_path_traversal_identity(self):
        self.write_key("alice")
        with self.assertRaises(ra.AttestationError):
            ra.build_attestation(
                self.receipt, "bob", "../../etc/passwd", self.keys
            )

    def test_verify_refuses_path_traversal_identity(self):
        outside = os.path.join(self.root, "outside.key")
        with open(outside, "wb") as fh:
            fh.write(b"whatever-bytes-happen-to-live-here")
        record = {
            "receipt_sha256": self.receipt_sha256(),
            "producing_identity": "bob",
            "attesting_identity": "../outside",
            "attested_at": "2024-01-01T00:00:00Z",
            "hmac_sha256": "0" * 64,
        }
        verdict, reason = ra.verify_attestation(record, self.receipt, self.keys)
        self.assertEqual(verdict, ra.NO_DATA, reason)

    def test_build_refuses_empty_identity(self):
        self.write_key("alice")
        with self.assertRaises(ra.AttestationError):
            ra.build_attestation(self.receipt, "", "alice", self.keys)


if __name__ == "__main__":
    unittest.main()
