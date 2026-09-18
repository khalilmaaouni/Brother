"""test_battery_exception_audit.py: drives scripts/battery_exception_audit.py
with fixture expectations files and an injected `now`, never the real clock
(rule 5: a test that depends on today's date passes or fails by calendar
rather than by code; this estate has a recorded failure of exactly that
shape). Every date-sensitive test writes its own fixture and pins `now`
explicitly instead of reasoning about live BATTERY-EXPECTATIONS.json dates.
"""
import contextlib
import datetime
import io
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import battery_exception_audit as bea  # noqa: E402


def _write_expectations(tmpdir, checks):
    path = os.path.join(tmpdir, "expectations.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"checks": checks}, fh)
    return path


def _entry(cls="known_no_data", reason="test fixture", review_by="2099-01-01",
           removal_condition="fix the thing"):
    return {
        "class": cls,
        "reason": reason,
        "recorded": "2026-01-01",
        "review_by": review_by,
        "removal_condition": removal_condition,
    }


class AuditDatesTests(unittest.TestCase):
    """The four review_by outcomes, each with a fixture built to force it."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmp.name
        self.addCleanup(self._tmp.cleanup)
        # No stale-check surprises in these tests: point check_all at a file
        # that registers every fixture name, or leave the cross-reference
        # to its own dedicated tests below.
        self.check_all = os.path.join(self.tmpdir, "check_all.sh")
        with open(self.check_all, "w", encoding="utf-8") as fh:
            fh.write('run_check "a" "true"\nrun_check "b" "true"\n'
                     'run_check "c" "true"\n')

    def test_expired_blocks(self):
        path = _write_expectations(self.tmpdir, {
            "a": _entry(review_by="2020-01-02"),
        })
        result = bea.audit(path, now="2026-01-01", check_all_path=self.check_all)
        self.assertEqual(len(result.expired), 1)
        self.assertEqual(result.expired[0]["name"], "a")
        self.assertEqual(result.expired[0]["review_by"], "2020-01-02")
        self.assertEqual(result.expired[0]["reason"], "test fixture")
        self.assertEqual(result.expired[0]["removal_condition"], "fix the thing")
        self.assertFalse(result.ok)
        self.assertEqual(result.missing_review_by, [])
        self.assertEqual(result.unparseable, [])

    def test_missing_review_by_blocks_and_is_reported_separately(self):
        entry = _entry()
        del entry["review_by"]
        path = _write_expectations(self.tmpdir, {"a": entry})
        result = bea.audit(path, now="2026-01-01", check_all_path=self.check_all)
        self.assertEqual(len(result.missing_review_by), 1)
        self.assertEqual(result.missing_review_by[0]["name"], "a")
        self.assertFalse(result.ok)
        # missing is its own bucket, never folded into expired or unparseable
        self.assertEqual(result.expired, [])
        self.assertEqual(result.unparseable, [])

    def test_unparseable_review_by_blocks_and_is_reported_separately(self):
        path = _write_expectations(self.tmpdir, {
            "a": _entry(review_by="next Tuesday"),
        })
        result = bea.audit(path, now="2026-01-01", check_all_path=self.check_all)
        self.assertEqual(len(result.unparseable), 1)
        self.assertEqual(result.unparseable[0]["name"], "a")
        self.assertEqual(result.unparseable[0]["review_by"], "next Tuesday")
        self.assertFalse(result.ok)
        self.assertEqual(result.expired, [])
        self.assertEqual(result.missing_review_by, [])

    def test_due_soon_warns_and_does_not_fail(self):
        path = _write_expectations(self.tmpdir, {
            "a": _entry(review_by="2026-01-03"),  # 2 days after now below
        })
        result = bea.audit(path, now="2026-01-01", check_all_path=self.check_all)
        self.assertEqual(len(result.due_soon), 1)
        self.assertEqual(result.due_soon[0]["days_until"], 2)
        self.assertEqual(result.expired, [])
        self.assertTrue(result.ok)

    def test_far_future_date_is_not_flagged_at_all(self):
        path = _write_expectations(self.tmpdir, {
            "a": _entry(review_by="2030-01-01"),
        })
        result = bea.audit(path, now="2026-01-01", check_all_path=self.check_all)
        self.assertEqual(result.due_soon, [])
        self.assertEqual(result.expired, [])
        self.assertTrue(result.ok)

    def test_far_past_date_is_expired(self):
        path = _write_expectations(self.tmpdir, {
            "a": _entry(review_by="2019-06-01"),
        })
        result = bea.audit(path, now="2026-01-01", check_all_path=self.check_all)
        self.assertEqual(len(result.expired), 1)
        self.assertFalse(result.ok)

    def test_review_by_exactly_today_is_due_soon_not_expired(self):
        # Mirrors battery_verdict._expired()'s own strict less-than: the
        # review is due today, it has not yet passed today.
        path = _write_expectations(self.tmpdir, {
            "a": _entry(review_by="2026-01-01"),
        })
        result = bea.audit(path, now="2026-01-01", check_all_path=self.check_all)
        self.assertEqual(result.expired, [])
        self.assertEqual(len(result.due_soon), 1)
        self.assertEqual(result.due_soon[0]["days_until"], 0)
        self.assertTrue(result.ok)

    def test_window_days_is_configurable(self):
        path = _write_expectations(self.tmpdir, {
            "a": _entry(review_by="2026-01-10"),  # 9 days out
        })
        near = bea.audit(path, now="2026-01-01", check_all_path=self.check_all,
                          window_days=10)
        far = bea.audit(path, now="2026-01-01", check_all_path=self.check_all,
                         window_days=3)
        self.assertEqual(len(near.due_soon), 1)
        self.assertEqual(far.due_soon, [])


class PositiveCountGuardTests(unittest.TestCase):
    """The bad state named in the module docstring: zero exceptions examined
    must never be indistinguishable from a healthy run that examined some
    and found them all current. A mutant that iterated nothing (wrong key,
    empty dict, a loop that never ran) must fail this test."""

    def test_zero_declared_exceptions_is_not_ok(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _write_expectations(tmpdir, {})
            check_all = os.path.join(tmpdir, "check_all.sh")
            with open(check_all, "w", encoding="utf-8") as fh:
                fh.write("")
            result = bea.audit(path, now="2026-01-01", check_all_path=check_all)
            self.assertEqual(result.examined, 0)
            self.assertFalse(result.ok)
            self.assertEqual(result.expired, [])
            self.assertEqual(result.missing_review_by, [])
            self.assertEqual(result.unparseable, [])

    def test_five_current_exceptions_examined_is_ok(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            checks = {"c%d" % i: _entry(review_by="2099-01-01")
                      for i in range(5)}
            path = _write_expectations(tmpdir, checks)
            check_all = os.path.join(tmpdir, "check_all.sh")
            with open(check_all, "w", encoding="utf-8") as fh:
                fh.write("\n".join(
                    'run_check "%s" "true"' % name for name in checks))
            result = bea.audit(path, now="2026-01-01", check_all_path=check_all)
            self.assertEqual(result.examined, 5)
            self.assertTrue(result.ok)


class UnknownInputRaisesTests(unittest.TestCase):

    def test_missing_file_raises(self):
        with self.assertRaises(bea.AuditError):
            bea.audit("/nonexistent/path/does-not-exist.json", now="2026-01-01")

    def test_malformed_json_raises(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "bad.json")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("{not json")
            with self.assertRaises(bea.AuditError):
                bea.audit(path, now="2026-01-01")

    def test_non_object_root_raises(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "list.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump([1, 2, 3], fh)
            with self.assertRaises(bea.AuditError):
                bea.audit(path, now="2026-01-01")

    def test_missing_checks_key_raises(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "no_checks.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"critical": {}}, fh)
            with self.assertRaises(bea.AuditError):
                bea.audit(path, now="2026-01-01")

    def test_entry_not_object_raises(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "bad_entry.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"checks": {"a": "not an object"}}, fh)
            with self.assertRaises(bea.AuditError):
                bea.audit(path, now="2026-01-01")

    def test_unknown_class_raises(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _write_expectations(tmpdir, {"a": _entry(cls="made_up_class")})
            with self.assertRaises(bea.AuditError):
                bea.audit(path, now="2026-01-01")


class StaleCheckTests(unittest.TestCase):

    def test_check_no_longer_in_battery_is_stale(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _write_expectations(tmpdir, {
                "still-here": _entry(review_by="2099-01-01"),
                "long-gone": _entry(review_by="2099-01-01"),
            })
            check_all = os.path.join(tmpdir, "check_all.sh")
            with open(check_all, "w", encoding="utf-8") as fh:
                fh.write('run_check "still-here" "true"\n')
            result = bea.audit(path, now="2026-01-01", check_all_path=check_all)
            self.assertEqual(result.stale, ["long-gone"])
            # a stale declaration is dead weight, not itself a review_by
            # failure, so it does not by itself flip ok
            self.assertTrue(result.ok)

    def test_unreadable_check_all_reports_stale_as_none_not_empty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _write_expectations(tmpdir, {
                "a": _entry(review_by="2099-01-01"),
            })
            result = bea.audit(
                path, now="2026-01-01",
                check_all_path=os.path.join(tmpdir, "does-not-exist.sh"))
            self.assertIsNone(result.stale)


class DuplicateKeyTests(unittest.TestCase):

    def test_duplicate_json_key_collapses_to_last_before_this_module_sees_it(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "dup.json")
            first = json.dumps(_entry(review_by="2020-01-01"))
            second = json.dumps(_entry(review_by="2099-01-01"))
            with open(path, "w", encoding="utf-8") as fh:
                fh.write('{"checks": {"a": %s, "a": %s}}' % (first, second))
            check_all = os.path.join(tmpdir, "check_all.sh")
            with open(check_all, "w", encoding="utf-8") as fh:
                fh.write('run_check "a" "true"\n')
            result = bea.audit(path, now="2026-01-01", check_all_path=check_all)
            # json.loads already kept only the last occurrence: one entry,
            # the non-expired one, examined once.
            self.assertEqual(result.examined, 1)
            self.assertEqual(result.expired, [])


class MainCliTests(unittest.TestCase):

    def _run_main(self, argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = bea.main(argv)
        return code, buf.getvalue()

    def test_main_exits_zero_when_ok(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _write_expectations(tmpdir, {"a": _entry(review_by="2099-01-01")})
            check_all = os.path.join(tmpdir, "check_all.sh")
            with open(check_all, "w", encoding="utf-8") as fh:
                fh.write('run_check "a" "true"\n')
            code, out = self._run_main([
                "--expectations", path, "--check-all", check_all,
                "--now", "2026-01-01",
            ])
            self.assertEqual(code, 0)
            self.assertIn("OK:", out)

    def test_main_exits_nonzero_when_expired(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _write_expectations(tmpdir, {"a": _entry(review_by="2020-01-01")})
            check_all = os.path.join(tmpdir, "check_all.sh")
            with open(check_all, "w", encoding="utf-8") as fh:
                fh.write('run_check "a" "true"\n')
            code, out = self._run_main([
                "--expectations", path, "--check-all", check_all,
                "--now", "2026-01-01",
            ])
            self.assertEqual(code, 1)
            self.assertIn("EXPIRED a", out)
            self.assertIn("FAIL:", out)

    def test_main_exits_nonzero_when_missing_review_by(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            entry = _entry()
            del entry["review_by"]
            path = _write_expectations(tmpdir, {"a": entry})
            check_all = os.path.join(tmpdir, "check_all.sh")
            with open(check_all, "w", encoding="utf-8") as fh:
                fh.write('run_check "a" "true"\n')
            code, out = self._run_main([
                "--expectations", path, "--check-all", check_all,
                "--now", "2026-01-01",
            ])
            self.assertEqual(code, 1)
            self.assertIn("MISSING-REVIEW-BY a", out)

    def test_main_exits_nonzero_when_unparseable(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _write_expectations(tmpdir, {
                "a": _entry(review_by="not-a-date")})
            check_all = os.path.join(tmpdir, "check_all.sh")
            with open(check_all, "w", encoding="utf-8") as fh:
                fh.write('run_check "a" "true"\n')
            code, out = self._run_main([
                "--expectations", path, "--check-all", check_all,
                "--now", "2026-01-01",
            ])
            self.assertEqual(code, 1)
            self.assertIn("UNPARSEABLE a", out)

    def test_main_exits_two_on_missing_file(self):
        code, out = self._run_main([
            "--expectations", "/nonexistent/nope.json", "--now", "2026-01-01",
        ])
        self.assertEqual(code, 2)
        self.assertIn("NO-DATA", out)


if __name__ == "__main__":
    unittest.main()
