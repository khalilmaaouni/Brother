#!/usr/bin/env python3
"""L5b.4 test_exempt: the exemption ledger refuses unknown, corrupt,
stale, unkeyed, duplicated, unsorted and data-path exemptions; a valid
in-date HMAC-keyed entry is the only thing that clears a hit."""
import contextlib
import datetime
import hashlib
import hmac
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.l5b_audit.score import (  # noqa: E402
    Exemption,
    apply_exemptions,
    load_exemptions,
    validate_exemption,
)


class FakeHit:
    def __init__(self, hit_id, rule_id=None, kind=None):
        self.hit_id = hit_id
        self.rule_id = rule_id
        self.kind = kind


TEST_KEY = "unit-test-approval-key-not-shipped"


def _canonical(hit_id, reason, approver, approved_at):
    return json.dumps(
        {"hit_id": hit_id, "reason": reason,
         "approver": approver, "approved_at": approved_at},
        separators=(",", ":"), ensure_ascii=False,
    )


def _token(hit_id, reason, approver, approved_at, key=TEST_KEY):
    body = _canonical(hit_id, reason, approver, approved_at)
    return hmac.new(key.encode("utf-8"), body.encode("utf-8"),
                    hashlib.sha256).hexdigest()


def _recent_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


@contextlib.contextmanager
def _env(name, value):
    old = os.environ.get(name)
    if value is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = value
    try:
        yield
    finally:
        if old is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = old


def _ex_to_dict(ex):
    return {
        "hit_id": ex.hit_id,
        "reason": ex.reason,
        "approver": ex.approver,
        "approved_at": ex.approved_at,
        "approval_token": ex.approval_token,
    }


class ExemptBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="l5b4-exempt-")
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        if not os.path.isdir(self.tmp):
            return
        for name in os.listdir(self.tmp):
            try:
                os.unlink(os.path.join(self.tmp, name))
            except OSError:
                pass
        try:
            os.rmdir(self.tmp)
        except OSError:
            pass

    def write_json(self, entries):
        path = os.path.join(self.tmp, "exemptions.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(entries, fh)
        return path

    def write_raw(self, raw_bytes):
        path = os.path.join(self.tmp, "exemptions.json")
        with open(path, "wb") as fh:
            fh.write(raw_bytes)
        return path

    def make_ex(self, hit_id="file.py:1:0:L5B-BARE-EXCEPT",
                reason="propagates to the caller, no swallow here",
                approver="role:on-call", approved_at=None,
                approval_token=None, key=TEST_KEY):
        if approved_at is None:
            approved_at = _recent_iso()
        if approval_token is None:
            approval_token = _token(hit_id, reason, approver, approved_at, key)
        return Exemption(hit_id=hit_id, reason=reason, approver=approver,
                         approved_at=approved_at, approval_token=approval_token)


class TestValidate(ExemptBase):
    def test_no_key_rejects_every_exemption(self):
        ex = self.make_ex()
        hit = FakeHit(ex.hit_id)
        with _env("L5B_APPROVAL_KEY", None):
            self.assertEqual(validate_exemption(ex, (hit,)), "NO_APPROVAL_KEY")

    def test_valid_exemption_passes(self):
        ex = self.make_ex()
        hit = FakeHit(ex.hit_id)
        with _env("L5B_APPROVAL_KEY", TEST_KEY):
            self.assertIsNone(validate_exemption(ex, (hit,)))

    def test_short_reason_rejected(self):
        ex = self.make_ex(reason="too short")
        hit = FakeHit(ex.hit_id)
        with _env("L5B_APPROVAL_KEY", TEST_KEY):
            self.assertEqual(validate_exemption(ex, (hit,)), "SHORT_REASON")

    def test_bad_token_rejected(self):
        ex = self.make_ex(approval_token="0" * 64)
        hit = FakeHit(ex.hit_id)
        with _env("L5B_APPROVAL_KEY", TEST_KEY):
            self.assertEqual(validate_exemption(ex, (hit,)), "BAD_TOKEN")

    def test_bad_time_rejected(self):
        ex = self.make_ex(approved_at="2026-09-24T10:00:00")
        hit = FakeHit(ex.hit_id)
        with _env("L5B_APPROVAL_KEY", TEST_KEY):
            self.assertEqual(validate_exemption(ex, (hit,)), "BAD_TIME")

    def test_stale_rejected(self):
        old = (datetime.datetime.now(datetime.timezone.utc)
               - datetime.timedelta(hours=48)).isoformat()
        ex = self.make_ex(approved_at=old)
        hit = FakeHit(ex.hit_id)
        with _env("L5B_APPROVAL_KEY", TEST_KEY):
            self.assertEqual(validate_exemption(ex, (hit,)), "STALE")

    def test_unknown_hit_rejected(self):
        ex = self.make_ex(hit_id="a.py:1:0:L5B-BARE-EXCEPT")
        with _env("L5B_APPROVAL_KEY", TEST_KEY):
            self.assertEqual(
                validate_exemption(ex, (FakeHit("other.py:1:0:X"),)),
                "UNKNOWN_HIT")

    def test_data_path_rejected(self):
        ex = self.make_ex()
        hit = FakeHit(ex.hit_id, rule_id="L5B-EXCEPT-PASS", kind="FILE_IO")
        with _env("L5B_APPROVAL_KEY", TEST_KEY):
            self.assertEqual(validate_exemption(ex, (hit,)), "DATA_PATH")

    def test_data_path_json_force_unwrap_rejected(self):
        ex = self.make_ex()
        hit = FakeHit(ex.hit_id, rule_id="L5B-FORCE-UNWRAP", kind="JSON")
        with _env("L5B_APPROVAL_KEY", TEST_KEY):
            self.assertEqual(validate_exemption(ex, (hit,)), "DATA_PATH")


class TestLoad(ExemptBase):
    def test_empty_ledger_returns_empty_tuple(self):
        path = self.write_json([])
        with _env("L5B_APPROVAL_KEY", TEST_KEY):
            self.assertEqual(load_exemptions(path, ()), ())

    def test_valid_entry_loads_clean(self):
        ex = self.make_ex()
        path = self.write_json([_ex_to_dict(ex)])
        hit = FakeHit(ex.hit_id)
        with _env("L5B_APPROVAL_KEY", TEST_KEY):
            result = load_exemptions(path, (hit,))
        self.assertEqual(len(result), 1)
        self.assertIsNone(result[0].invalid_reason)
        self.assertEqual(result[0].hit_id, ex.hit_id)

    def test_duplicate_rejected(self):
        ex1 = self.make_ex(hit_id="z.py:1:0:L5B-BARE-EXCEPT")
        ex2 = self.make_ex(hit_id="z.py:1:0:L5B-BARE-EXCEPT")
        path = self.write_json([_ex_to_dict(ex1), _ex_to_dict(ex2)])
        hits = (FakeHit(ex1.hit_id),)
        with _env("L5B_APPROVAL_KEY", TEST_KEY):
            result = load_exemptions(path, hits)
        self.assertEqual(len(result), 2)
        for entry in result:
            self.assertEqual(entry.invalid_reason, "DUPLICATE")

    def test_unsorted_rejected(self):
        ex_b = self.make_ex(hit_id="b.py:1:0:L5B-BARE-EXCEPT")
        ex_a = self.make_ex(hit_id="a.py:1:0:L5B-BARE-EXCEPT")
        path = self.write_json([_ex_to_dict(ex_b), _ex_to_dict(ex_a)])
        hits = (FakeHit(ex_a.hit_id), FakeHit(ex_b.hit_id))
        with _env("L5B_APPROVAL_KEY", TEST_KEY):
            result = load_exemptions(path, hits)
        self.assertEqual([e.invalid_reason for e in result], ["UNSORTED", "UNSORTED"])


class TestApply(ExemptBase):
    def test_valid_exemption_clears_its_hit(self):
        ex = self.make_ex(hit_id="a.py:1:0:L5B-BARE-EXCEPT")
        valid = Exemption(hit_id=ex.hit_id, reason=ex.reason, approver=ex.approver,
                          approved_at=ex.approved_at, approval_token=ex.approval_token,
                          invalid_reason=None)
        hit_a = FakeHit(ex.hit_id)
        hit_b = FakeHit("b.py:2:0:L5B-EXCEPT-PASS")
        cleared, remaining = apply_exemptions((hit_a, hit_b), (valid,))
        self.assertEqual(cleared, (hit_a,))
        self.assertEqual(remaining, (hit_b,))

    def test_invalid_exemption_clears_nothing(self):
        ex = self.make_ex(hit_id="a.py:1:0:L5B-BARE-EXCEPT")
        entry = Exemption(hit_id=ex.hit_id, reason=ex.reason, approver=ex.approver,
                          approved_at=ex.approved_at, approval_token=ex.approval_token,
                          invalid_reason="DATA_PATH")
        hit_a = FakeHit(ex.hit_id)
        cleared, remaining = apply_exemptions((hit_a,), (entry,))
        self.assertEqual(cleared, ())
        self.assertEqual(remaining, (hit_a,))


class TestHostile(ExemptBase):
    def test_load_refuses_wrong_path_type(self):
        for bad in (None, "", 123):
            with self.assertRaises(ValueError):
                load_exemptions(bad, ())

    def test_load_refuses_wrong_hits_type(self):
        for bad in ([], None, "not a tuple"):
            with self.assertRaises(ValueError):
                load_exemptions("/dev/null", bad)

    def test_load_refuses_missing_file(self):
        missing = os.path.join(self.tmp, "no-such.json")
        with self.assertRaises(ValueError):
            load_exemptions(missing, ())

    def test_load_refuses_non_utf8(self):
        path = self.write_raw(b"\xff\xfe\x00\x00")
        with self.assertRaises(ValueError):
            load_exemptions(path, ())

    def test_load_refuses_bad_json(self):
        path = self.write_raw(b"{not json")
        with self.assertRaises(ValueError):
            load_exemptions(path, ())

    def test_load_refuses_non_array(self):
        path = self.write_json({"not": "array"})
        with self.assertRaises(ValueError):
            load_exemptions(path, ())

    def test_load_refuses_non_object_entry(self):
        path = self.write_json(["not an object"])
        with self.assertRaises(ValueError):
            load_exemptions(path, ())

    def test_load_refuses_missing_field(self):
        path = self.write_json([{"hit_id": "x.py:1:0:L5B-BARE-EXCEPT"}])
        with self.assertRaises(ValueError):
            load_exemptions(path, ())

    def test_load_refuses_wrong_field_type(self):
        path = self.write_json([{
            "hit_id": "x.py:1:0:L5B-BARE-EXCEPT",
            "reason": None,
            "approver": "role",
            "approved_at": "2026-01-01T00:00:00+00:00",
            "approval_token": "deadbeef",
        }])
        with self.assertRaises(ValueError):
            load_exemptions(path, ())

    def test_validate_refuses_wrong_ex_type(self):
        for bad in (None, "nope", 1):
            with self.assertRaises(ValueError):
                validate_exemption(bad, ())

    def test_validate_refuses_wrong_hits_type(self):
        ex = self.make_ex()
        for bad in ([], None, "not a tuple"):
            with self.assertRaises(ValueError):
                validate_exemption(ex, bad)

    def test_validate_returns_bad_fields_for_non_string(self):
        ex = Exemption(hit_id=None, reason="a" * 30, approver="role",
                       approved_at=_recent_iso(), approval_token="0" * 64)
        with _env("L5B_APPROVAL_KEY", TEST_KEY):
            self.assertEqual(validate_exemption(ex, ()), "BAD_FIELDS")

    def test_apply_refuses_wrong_types(self):
        with self.assertRaises(ValueError):
            apply_exemptions([], ())
        with self.assertRaises(ValueError):
            apply_exemptions((), None)
        with self.assertRaises(ValueError):
            apply_exemptions((), [])
        with self.assertRaises(ValueError):
            apply_exemptions(("x",), ())

    def test_apply_refuses_non_exemption_entries(self):
        with self.assertRaises(ValueError):
            apply_exemptions((), ("not an exemption",))


if __name__ == "__main__":
    unittest.main()
