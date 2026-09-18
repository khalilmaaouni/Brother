#!/usr/bin/env python3
"""Tests for scripts/credential_broker.py (unit DOM-40.04).

Expected reason strings are pinned here as independent literals, not read
back from the module's own REASON_* constants: a test that compares a
constant against itself cannot catch the constant being wrong. Run:
python3 scripts/test_credential_broker.py -v
"""
import math
import unittest

import credential_broker as cb


def _store(**entries):
    return dict(entries)


def _entry(value="s3cr3t-test-value", scopes=("read",), expires_at=None):
    return {"value": value, "scopes": scopes, "expires_at": expires_at}


class GrantedCases(unittest.TestCase):
    def test_granted_minimal(self):
        store = _store(db=_entry())
        verdict, reason, grant = cb.request_credential(
            store, "db", "read", "run a report", now=1000)
        self.assertEqual(verdict, cb.GRANTED)
        self.assertIsNone(reason)
        self.assertEqual(grant.value, "s3cr3t-test-value")
        self.assertEqual(grant.name, "db")
        self.assertEqual(grant.scope, "read")
        self.assertEqual(grant.purpose, "run a report")

    def test_granted_never_expires(self):
        store = _store(db=_entry(expires_at=None))
        verdict, _, grant = cb.request_credential(
            store, "db", "read", "purpose", now=99999999)
        self.assertEqual(verdict, cb.GRANTED)
        self.assertIsNotNone(grant)

    def test_granted_just_before_expiry_boundary(self):
        store = _store(db=_entry(expires_at=1000))
        verdict, _, grant = cb.request_credential(
            store, "db", "read", "purpose", now=999)
        self.assertEqual(verdict, cb.GRANTED)
        self.assertIsNotNone(grant)

    def test_many_entries_grants_only_the_requested_one(self):
        store = _store(
            db=_entry(value="db-secret", scopes=("read",)),
            api=_entry(value="api-secret", scopes=("write",)),
        )
        verdict, _, grant = cb.request_credential(
            store, "api", "write", "call the api", now=0)
        self.assertEqual(verdict, cb.GRANTED)
        self.assertEqual(grant.value, "api-secret")
        self.assertEqual(grant.name, "api")
        # the sibling credential's value never leaks through the grant
        self.assertNotIn("db-secret", repr(grant))


class RefusalCases(unittest.TestCase):
    def test_unknown_credential_against_empty_store(self):
        verdict, reason, grant = cb.request_credential(
            {}, "db", "read", "purpose", now=0)
        self.assertEqual(verdict, cb.REFUSED)
        self.assertEqual(reason, "unknown credential: not present in the store")
        self.assertIsNone(grant)

    def test_unknown_credential_against_nonempty_store(self):
        store = _store(other=_entry())
        verdict, reason, grant = cb.request_credential(
            store, "db", "read", "purpose", now=0)
        self.assertEqual(verdict, cb.REFUSED)
        self.assertEqual(reason, "unknown credential: not present in the store")
        self.assertIsNone(grant)

    def test_no_purpose_empty_string(self):
        store = _store(db=_entry())
        verdict, reason, grant = cb.request_credential(
            store, "db", "read", "", now=0)
        self.assertEqual(verdict, cb.REFUSED)
        self.assertEqual(reason, "no purpose stated: purpose must be a non-empty string")
        self.assertIsNone(grant)

    def test_no_purpose_none(self):
        store = _store(db=_entry())
        verdict, reason, grant = cb.request_credential(
            store, "db", "read", None, now=0)
        self.assertEqual(verdict, cb.REFUSED)
        self.assertEqual(reason, "no purpose stated: purpose must be a non-empty string")
        self.assertIsNone(grant)

    def test_no_purpose_not_a_string(self):
        store = _store(db=_entry())
        verdict, reason, grant = cb.request_credential(
            store, "db", "read", 12345, now=0)
        self.assertEqual(verdict, cb.REFUSED)
        self.assertEqual(reason, "no purpose stated: purpose must be a non-empty string")
        self.assertIsNone(grant)

    def test_scope_wider_than_declared(self):
        store = _store(db=_entry(scopes=("read",)))
        verdict, reason, grant = cb.request_credential(
            store, "db", "admin", "purpose", now=0)
        self.assertEqual(verdict, cb.REFUSED)
        self.assertEqual(
            reason, "requested scope is not one of the credential's declared scopes")
        self.assertIsNone(grant)

    def test_expired_past(self):
        store = _store(db=_entry(expires_at=1000))
        verdict, reason, grant = cb.request_credential(
            store, "db", "read", "purpose", now=1001)
        self.assertEqual(verdict, cb.REFUSED)
        self.assertEqual(reason, "credential has expired (now is at or past expires_at)")
        self.assertIsNone(grant)

    def test_expired_at_exact_boundary(self):
        store = _store(db=_entry(expires_at=1000))
        verdict, reason, grant = cb.request_credential(
            store, "db", "read", "purpose", now=1000)
        self.assertEqual(verdict, cb.REFUSED)
        self.assertEqual(reason, "credential has expired (now is at or past expires_at)")
        self.assertIsNone(grant)

    def test_malformed_store_missing_key(self):
        store = _store(db={"value": "x", "scopes": ("read",)})  # no expires_at
        verdict, reason, grant = cb.request_credential(
            store, "db", "read", "purpose", now=0)
        self.assertEqual(verdict, cb.REFUSED)
        self.assertEqual(
            reason,
            "malformed store entry: missing or invalid value, scopes, or expires_at")
        self.assertIsNone(grant)

    def test_malformed_store_empty_value(self):
        store = _store(db=_entry(value=""))
        verdict, reason, grant = cb.request_credential(
            store, "db", "read", "purpose", now=0)
        self.assertEqual(verdict, cb.REFUSED)
        self.assertEqual(
            reason,
            "malformed store entry: missing or invalid value, scopes, or expires_at")

    def test_malformed_store_value_not_a_string(self):
        store = _store(db=_entry(value=b"bytes-not-str"))
        verdict, reason, grant = cb.request_credential(
            store, "db", "read", "purpose", now=0)
        self.assertEqual(verdict, cb.REFUSED)
        self.assertEqual(
            reason,
            "malformed store entry: missing or invalid value, scopes, or expires_at")

    def test_malformed_store_scopes_is_bare_string(self):
        # "read" is iterable but iterating it yields one-letter scopes;
        # a bare string must be refused, never silently treated as
        # a single declared scope.
        store = _store(db=_entry(scopes="read"))
        verdict, reason, grant = cb.request_credential(
            store, "db", "read", "purpose", now=0)
        self.assertEqual(verdict, cb.REFUSED)
        self.assertEqual(
            reason,
            "malformed store entry: missing or invalid value, scopes, or expires_at")

    def test_malformed_store_scopes_not_iterable(self):
        store = _store(db=_entry(scopes=42))
        verdict, reason, grant = cb.request_credential(
            store, "db", "read", "purpose", now=0)
        self.assertEqual(verdict, cb.REFUSED)
        self.assertEqual(
            reason,
            "malformed store entry: missing or invalid value, scopes, or expires_at")

    def test_malformed_store_scopes_contains_non_string(self):
        store = _store(db=_entry(scopes=("read", 7)))
        verdict, reason, grant = cb.request_credential(
            store, "db", "read", "purpose", now=0)
        self.assertEqual(verdict, cb.REFUSED)
        self.assertEqual(
            reason,
            "malformed store entry: missing or invalid value, scopes, or expires_at")

    def test_malformed_store_expires_at_not_a_number(self):
        store = _store(db=_entry(expires_at="never"))
        verdict, reason, grant = cb.request_credential(
            store, "db", "read", "purpose", now=0)
        self.assertEqual(verdict, cb.REFUSED)
        self.assertEqual(
            reason,
            "malformed store entry: missing or invalid value, scopes, or expires_at")

    def test_malformed_store_entry_not_a_dict(self):
        store = _store(db="not-a-dict-entry")
        verdict, reason, grant = cb.request_credential(
            store, "db", "read", "purpose", now=0)
        self.assertEqual(verdict, cb.REFUSED)
        self.assertEqual(
            reason,
            "malformed store entry: missing or invalid value, scopes, or expires_at")

    def test_malformed_request_store_not_a_dict(self):
        verdict, reason, grant = cb.request_credential(
            None, "db", "read", "purpose", now=0)
        self.assertEqual(verdict, cb.REFUSED)
        self.assertEqual(
            reason,
            "malformed request: store, name, scope, or now is not the expected shape")

    def test_malformed_request_name_empty(self):
        store = _store(db=_entry())
        verdict, reason, grant = cb.request_credential(
            store, "", "read", "purpose", now=0)
        self.assertEqual(verdict, cb.REFUSED)
        self.assertEqual(
            reason,
            "malformed request: store, name, scope, or now is not the expected shape")

    def test_malformed_request_scope_not_a_string(self):
        store = _store(db=_entry())
        verdict, reason, grant = cb.request_credential(
            store, "db", 7, "purpose", now=0)
        self.assertEqual(verdict, cb.REFUSED)
        self.assertEqual(
            reason,
            "malformed request: store, name, scope, or now is not the expected shape")

    def test_malformed_request_now_not_a_number(self):
        store = _store(db=_entry())
        verdict, reason, grant = cb.request_credential(
            store, "db", "read", "purpose", now="not-a-clock-reading")
        self.assertEqual(verdict, cb.REFUSED)
        self.assertEqual(
            reason,
            "malformed request: store, name, scope, or now is not the expected shape")

    def test_malformed_request_now_is_nan(self):
        store = _store(db=_entry())
        verdict, reason, grant = cb.request_credential(
            store, "db", "read", "purpose", now=float("nan"))
        self.assertEqual(verdict, cb.REFUSED)
        self.assertEqual(
            reason,
            "malformed request: store, name, scope, or now is not the expected shape")

    def test_malformed_request_now_is_infinite(self):
        store = _store(db=_entry(expires_at=1000))
        verdict, reason, grant = cb.request_credential(
            store, "db", "read", "purpose", now=float("inf"))
        self.assertEqual(verdict, cb.REFUSED)
        self.assertEqual(
            reason,
            "malformed request: store, name, scope, or now is not the expected shape")

    def test_malformed_request_now_is_a_bool(self):
        # bool is an int subclass in Python; a clock reading is never
        # meaningfully True or False, so it is refused as malformed.
        store = _store(db=_entry())
        verdict, reason, grant = cb.request_credential(
            store, "db", "read", "purpose", now=True)
        self.assertEqual(verdict, cb.REFUSED)
        self.assertEqual(
            reason,
            "malformed request: store, name, scope, or now is not the expected shape")


class ExceptionSafetyCases(unittest.TestCase):
    def test_unanticipated_exception_is_refused_not_raised(self):
        class ExplodingStore(dict):
            def __getitem__(self, key):
                raise RuntimeError("boom, and this text must never reach the reason")

        store = ExplodingStore(db=_entry())
        verdict, reason, grant = cb.request_credential(
            store, "db", "read", "purpose", now=0)
        self.assertEqual(verdict, cb.REFUSED)
        self.assertEqual(
            reason,
            "malformed request: store, name, scope, or now is not the expected shape")
        self.assertIsNone(grant)
        self.assertNotIn("boom", reason)

    def test_never_raises_across_a_sweep_of_garbage(self):
        garbage = [None, 0, 1.5, "x", [], {}, object(), math.nan, math.inf]
        for store in garbage:
            for name in garbage:
                try:
                    verdict, reason, grant = cb.request_credential(
                        store, name, "read", "purpose", now=0)
                except Exception as exc:  # pragma: no cover - the failure itself
                    self.fail(
                        "request_credential raised %r for store=%r name=%r" %
                        (exc, store, name))
                self.assertIn(verdict, (cb.GRANTED, cb.REFUSED))


class SecretNeverLeaksCases(unittest.TestCase):
    SECRET = "fixture-secret-only-appears-in-dot-value-abc123"

    def test_secret_absent_from_every_refusal_reason(self):
        store = _store(db=_entry(value=self.SECRET, scopes=("read",), expires_at=5))
        attempts = [
            (store, "db", "read", "", 0),          # no purpose
            (store, "db", "admin", "p", 0),         # wrong scope
            (store, "db", "read", "p", 5),          # expired at boundary
            (store, "missing", "read", "p", 0),     # unknown credential
            (store, "db", "read", "p", "bad-clock"),  # malformed request
        ]
        for args in attempts:
            verdict, reason, grant = cb.request_credential(*args)
            self.assertEqual(verdict, cb.REFUSED)
            self.assertIsNotNone(reason)
            self.assertNotIn(self.SECRET, reason)

    def test_secret_absent_from_grant_repr_and_str(self):
        store = _store(db=_entry(value=self.SECRET))
        verdict, _, grant = cb.request_credential(
            store, "db", "read", "purpose", now=0)
        self.assertEqual(verdict, cb.GRANTED)
        self.assertNotIn(self.SECRET, repr(grant))
        self.assertNotIn(self.SECRET, str(grant))
        self.assertIn("<redacted>", repr(grant))
        # the secret is still reachable, but only through the one
        # field a caller must read on purpose
        self.assertEqual(grant.value, self.SECRET)


if __name__ == "__main__":
    unittest.main()
