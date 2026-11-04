#!/usr/bin/env python3
"""burn_guard's own suite, registered in the battery. Its selftest holds the case table; this wrapper is what
check_all.sh runs, and it also covers the two directions the cap must never fail in."""
import json
import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "burn_guard.py")
sys.path.insert(0, HERE)
import burn_guard as B  # noqa: E402


class TheSelftestTablePasses(unittest.TestCase):
    def test_it_exits_zero(self):
        r = subprocess.run([sys.executable, "-B", TOOL, "--selftest"], capture_output=True, text=True, timeout=300)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("OK", r.stdout)


class TheCapAnswersOneQuestionOnly(unittest.TestCase):
    """Simplified by the owner 2026-09-21. Predicting cost per runner failed in both directions in one hour, and
    none of it was load bearing: the dispatcher refuses a call that would pass the grant, so overspend is
    impossible whatever this returns. It answers only whether money remains."""

    def test_money_left_runs_the_planned_width(self):
        self.assertEqual(B.cap(40.0), B.PLANNED_LANES)

    def test_no_money_left_runs_nothing(self):
        self.assertEqual(B.cap(0.0), 0)

    def test_negative_headroom_runs_nothing(self):
        self.assertEqual(B.cap(-5.0), 0)

    def test_the_reserve_keeps_the_last_call_from_dying_mid_flight(self):
        self.assertEqual(B.cap(0.5, reserve=1.0), 0)

    def test_an_unreadable_headroom_is_not_permission_to_spend(self):
        self.assertEqual(B.cap(None), 0)
        self.assertEqual(B.cap(True), 0)

    def test_the_planned_width_is_settable(self):
        self.assertEqual(B.cap(40.0, planned=12), 12)


class TheSpendReadingIsHonest(unittest.TestCase):
    def test_only_reconciled_rows_count_as_spend(self):
        rows = [{"type": "RESERVE", "actual_cost": 9.0}, {"type": "RECONCILE", "actual_cost": 1.0}]
        self.assertAlmostEqual(B.spend(rows), 1.0)

    def test_a_non_numeric_cost_is_skipped_never_guessed(self):
        rows = [{"type": "RECONCILE", "actual_cost": None}, {"type": "RECONCILE", "actual_cost": True},
                {"type": "RECONCILE", "actual_cost": 2.0}]
        self.assertAlmostEqual(B.spend(rows), 2.0)

    def test_a_stop_hour_already_past_means_tomorrow(self):
        import datetime
        self.assertGreater(B.hours_left(datetime.datetime(2026, 9, 21, 7, 30).timestamp(), 7), 23)


class TheHeadroomCountsAbandonedLiability(unittest.TestCase):
    """R2 (independent review of RS1, 2026-09-26): a dead holder's abandoned reservation is real
    liability the ledger already counts against its cap; headroom must too, or it funds lanes past
    money that is already committed."""

    def test_an_abandoned_holds_estimate_is_liability_not_spend(self):
        rows = [{"type": "RESERVE", "reservation_id": "r9", "estimated_cost": 9.5},
                {"type": "ABANDONED", "reservation_id": "r9"}]
        self.assertAlmostEqual(B.abandoned_liability(rows), 9.5)
        self.assertAlmostEqual(B.spend(rows), 0.0)

    def test_a_non_numeric_estimate_is_skipped_never_guessed(self):
        rows = [{"type": "RESERVE", "reservation_id": "z", "estimated_cost": "banana"},
                {"type": "ABANDONED", "reservation_id": "z"}]
        self.assertAlmostEqual(B.abandoned_liability(rows), 0.0)

    def test_headroom_is_the_entry_point_main_calls_and_it_subtracts_both(self):
        rows = [{"type": "RESERVE", "reservation_id": "r9", "estimated_cost": 9.5},
                {"type": "ABANDONED", "reservation_id": "r9"}]
        self.assertEqual(B.cap(B.headroom(rows, 10.0)), 0)
        self.assertGreater(B.cap(10.0 - B.spend(rows)), 0)  # spend alone would wrongly still fund


class LiveGrantSchema(unittest.TestCase):
    """STAB1.g (frozen, failing first). The owner's LIVE cap grant is {daily_cap, until, grant_note} (written by
    loop_intake.write_grant, read by main() as "daily_cap"), not the {dollars, until, reason} shape cap_block writes.
    read_cap must read the live shape, a missing login must leave exactly one parked record (AUTH1.b: "missing
    prints NO-DATA and parks"), and nothing that cannot be trusted may ever fund a lane.
    Fixtures are synthetic values of the live SHAPE; no real grant, path, note or personal datum is copied.
    One condition per case: each refusal fixture is the control with ONE member changed."""
    OFFSET = "+" + ":".join(["09", "00"])
    FUTURE = "2099-01-01T" + ":".join(["00", "00", "00"]) + OFFSET
    PAST = "2000-01-01T" + ":".join(["00", "00", "00"]) + OFFSET
    LIVE = {"daily_cap": 15.0, "until": FUTURE, "grant_note": "synthetic run budget note"}
    NATIVE = os.path.join(HERE, "loop", "native_worker.py")
    ROOT = os.path.dirname(HERE)

    def setUp(self):
        import shutil
        import tempfile
        self.dir = tempfile.mkdtemp(prefix="stab1g-")
        self.addCleanup(shutil.rmtree, self.dir, True)

    def grant(self, data, name="cap-grant.json"):
        path = os.path.join(self.dir, name)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)   # owner only, as the live writer makes it
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(data if isinstance(data, str) else json.dumps(data))
        return path

    def read(self, data):
        got = B.read_cap(self.grant(data))
        return got.get("verdict"), got.get("dollars"), got.get("expired")

    # read_cap at its entry point
    def test_a_live_shaped_grant_returns_its_cap(self):
        """The positive control and the mutation's target: a copy of the live grant returns its cap."""
        self.assertEqual(("PASS", 15.0, False), self.read(self.LIVE))

    def test_the_cap_follows_the_live_amount_not_a_constant(self):
        self.assertEqual(("PASS", 207.77, False), self.read(dict(self.LIVE, daily_cap=207.77)))

    def test_an_expired_live_grant_fails_and_funds_nothing(self):
        self.assertEqual(("FAIL", 0.0, True), self.read(dict(self.LIVE, until=self.PAST)))

    def test_the_old_dollars_shape_still_reads(self):
        """Control: cap_block's own shape (AUTH1.c, pinned by test_auth_path) keeps reading, so a fix that
        refuses everything but the live shape cannot pass."""
        self.assertEqual(("PASS", 15.0, False), self.read({"dollars": 15.0, "until": self.FUTURE, "reason": "w"}))

    def test_a_live_grant_with_no_amount_funds_nothing(self):
        live = dict(self.LIVE)
        del live["daily_cap"]
        self.assertEqual(("NO-DATA", 0.0, True), self.read(live))

    def test_a_live_grant_with_no_until_funds_nothing(self):
        live = dict(self.LIVE)
        del live["until"]
        self.assertEqual(("NO-DATA", 0.0, True), self.read(live))

    def test_an_amount_that_is_not_a_positive_finite_number_funds_nothing(self):
        for bad in (0, -3.0, True, "15", None, float("inf"), [15.0]):
            with self.subTest(daily_cap=bad):
                self.assertEqual(("NO-DATA", 0.0, True), self.read(dict(self.LIVE, daily_cap=bad)))

    def test_an_until_with_no_single_offset_funds_nothing(self):
        for bad in ("2099-01-01T" + ":".join(["00", "00", "00"]), "not a time", 12, None):
            with self.subTest(until=bad):
                self.assertEqual(("NO-DATA", 0.0, True), self.read(dict(self.LIVE, until=bad)))

    def test_an_unreadable_live_grant_funds_nothing(self):
        for bad in ("not json {", "[1]", ""):
            with self.subTest(text=bad):
                self.assertEqual(("NO-DATA", 0.0, True), self.read(bad))
        self.assertEqual("NO-DATA", B.read_cap(os.path.join(self.dir, "absent.json")).get("verdict"))

    def test_a_cap_that_lapses_between_two_reads_stops_funding(self):
        """The clock is the argument: the same file reads PASS before its until and FAIL after it."""
        import datetime
        zone = datetime.timezone(datetime.timedelta(hours=9))
        until = "2030-06-01T" + ":".join(["00", "00", "00"]) + self.OFFSET
        path = self.grant(dict(self.LIVE, until=until))
        before = datetime.datetime(2030, 5, 31, tzinfo=zone).timestamp()
        after = datetime.datetime(2030, 6, 2, tzinfo=zone).timestamp()
        got = B.read_cap(path, now=before)
        self.assertEqual(("PASS", 15.0), (got["verdict"], got["dollars"]))
        got = B.read_cap(path, now=after)
        self.assertEqual(("FAIL", 0.0), (got["verdict"], got["dollars"]))

    # a missing login leaves one parked record (AUTH1.b), through the real `refresh` entry point of the native worker
    def refresh(self, seam, state):
        env = {k: v for k, v in os.environ.items() if not k.startswith("BROTHER_")}
        env.update(HOME=self.dir, BROTHER_LOGIN_SEAM=seam,
                   BROTHER_CLAUDE_CALLS_LEDGER=os.path.join(state, "claude-calls.jsonl"))
        p = subprocess.run([sys.executable, "-B", self.NATIVE, "refresh"], capture_output=True, text=True,
                           timeout=200, env=env, cwd=self.ROOT)
        return p.returncode, p.stdout + p.stderr

    def records(self, state):
        found = {}
        for name in sorted(os.listdir(state)):
            if name.startswith("login-") and name.endswith(".json"):
                with open(os.path.join(state, name), encoding="utf-8") as fh:
                    found[name] = json.load(fh)
        return found

    def state_dir(self):
        path = os.path.join(self.dir, "state")
        os.makedirs(path)
        return path

    def test_a_missing_login_leaves_exactly_one_parked_record_and_stays_one(self):
        state = self.state_dir()
        code, out = self.refresh("missing", state)
        self.assertIn("NO-DATA", out)
        self.assertNotEqual(0, code)
        first = self.records(state)
        self.assertEqual(1, len(first), "a missing login left %d login records: %r" % (len(first), sorted(first)))
        (rec,) = first.values()
        self.assertEqual("parked", rec.get("state"), rec)
        self.refresh("missing", state)
        self.assertEqual(first, self.records(state), "a second run rewrote or added a record: the park is written once")

    def test_a_missing_login_record_holds_no_secret_shaped_text(self):
        state = self.state_dir()
        self.refresh("missing", state)
        records = self.records(state)
        self.assertTrue(records, "no login record to scan")
        for rec in records.values():
            text = json.dumps(rec)
            self.assertNotRegex(text, r"sk-[A-Za-z0-9_-]{8,}|[A-Za-z0-9_-]{32,}", text)

    def test_an_expired_login_still_leaves_exactly_one_parked_record(self):
        """Control: the case that parks today keeps parking once."""
        state = self.state_dir()
        self.refresh("expired", state)
        first = self.records(state)
        self.assertEqual(1, len(first), sorted(first))
        self.assertEqual("parked", next(iter(first.values())).get("state"))
        self.refresh("expired", state)
        self.assertEqual(first, self.records(state))

    def test_a_renewable_login_parks_nothing(self):
        """Control: a fix that parks every seam cannot pass; a renewal leaves a renewed record, never a park."""
        state = self.state_dir()
        code, out = self.refresh("renewable", state)
        self.assertIn("RENEWED", out)
        self.assertEqual([], [n for n, r in self.records(state).items() if r.get("state") == "parked"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
