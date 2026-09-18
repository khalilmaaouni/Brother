#!/usr/bin/env python3
"""Tests for limit_monitor.py (LIMIT-01: resident usage-limit detection).
Fixtures mirror test_limit_watch.py's real measured record shapes, extended
with the sessionId/timestamp fields a real transcript record always carries
(measured on this machine: every record carries sessionId, uuid, timestamp).
Everything runs under a temp projects root and a temp state path; the real
~/.claude tree, the real launchd, and the real plist are never touched.
No em or en dashes."""

import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import limit_monitor
import limit_watch

# E100: one sandbox for every temp tree this process makes, removed at exit.
import os as _e100_os, sys as _e100_sys  # noqa: E402
_e100_sys.path.append(_e100_os.path.join(
    _e100_os.path.dirname(_e100_os.path.abspath(__file__)), '.'))
try:  # noqa: E402
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    _e100_sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % _e100_os.path.basename(__file__))


def _api_error_record(text, session_id, timestamp, quota_limits=None):
    """The measured shape a real rate_limit rejection record carries,
    extended with sessionId/timestamp (present on every real record;
    limit_watch.py's own fixtures omit them because classify() never reads
    them, but _episode_key() below does)."""
    return {
        "type": "assistant",
        "sessionId": session_id,
        "timestamp": timestamp,
        "message": {"role": "assistant",
                    "content": [{"type": "text", "text": text}]},
        "quotaLimits": quota_limits,
        "error": "rate_limit",
        "isApiErrorMessage": True,
        "apiErrorStatus": 429,
    }


def _plain_record(text, session_id="sess-normal", timestamp="2026-09-18T00:00:00Z"):
    return {
        "type": "assistant",
        "sessionId": session_id,
        "timestamp": timestamp,
        "message": {"role": "assistant",
                    "content": [{"type": "text", "text": text}]},
    }


def _seven_day_record(session_id, timestamp, resets_at):
    return _api_error_record(
        "You've hit your weekly limit · resets Aug 30 at 4am (Asia/Tokyo)",
        session_id, timestamp,
        quota_limits={
            "status": "rejected", "resetsAt": resets_at,
            "rateLimitType": "seven_day", "overageStatus": "rejected",
            "isUsingOverage": False,
        })


def _fake_schedule(resets_at, margin=120):
    """Never touches the real launchd or plist; mirrors test_limit_watch.py's
    own TestArm fake_schedule stub."""
    return {"scheduled": resets_at is not None, "fire_epoch": resets_at}


class LimitMonitorTestCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.projects_root = os.path.join(self.root, "projects")
        self.project_dir = os.path.join(
            self.projects_root, limit_monitor.BROTHER_PROJECT_PREFIX)
        os.makedirs(self.project_dir)
        self.state_path = os.path.join(self.root, "state", "state.json")
        self.flag_path = os.path.join(self.root, "flag", "armed.flag")

    def _write_transcript(self, records, name="session.jsonl"):
        path = os.path.join(self.project_dir, name)
        with open(path, "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")
        return path

    def _tick(self, run_dir=None):
        return limit_monitor.tick(
            projects_root=self.projects_root, state_path=self.state_path,
            run_dir=run_dir or self.root, flag_path=self.flag_path,
            schedule_fn=_fake_schedule)


class TestNormalAndDiscovery(LimitMonitorTestCase):
    def test_normal_record_arms_nothing(self):
        self._write_transcript([_plain_record("all good")])
        out = self._tick()
        self.assertEqual(out["class"], "NORMAL")
        self.assertFalse(out["armed"])

    def test_no_transcript_anywhere_is_no_data(self):
        out = self._tick()
        self.assertEqual(out["class"], "NO-DATA")
        self.assertFalse(out["armed"])
        self.assertIn("NO-DATA", out["reason"])

    def test_newest_known_transcript_picks_the_newer_file(self):
        self._write_transcript([_plain_record("old")], name="a.jsonl")
        import time
        time.sleep(0.02)
        self._write_transcript(
            [_seven_day_record("sess-1", "2026-09-18T07:54:00Z", 1788030000)],
            name="b.jsonl")
        out = self._tick()
        self.assertEqual(out["class"], "seven_day")


class TestEpisodeDedupe(LimitMonitorTestCase):
    def test_limit_record_arms_exactly_once(self):
        self._write_transcript(
            [_seven_day_record("sess-1", "2026-09-18T07:54:00Z", 1788030000)])
        out = self._tick()
        self.assertEqual(out["class"], "seven_day")
        self.assertTrue(out["armed"])
        with open(self.flag_path) as f:
            self.assertIn("brother_run.py --resume", f.read())

    def test_same_episode_second_tick_arms_nothing(self):
        self._write_transcript(
            [_seven_day_record("sess-1", "2026-09-18T07:54:00Z", 1788030000)])
        first = self._tick()
        self.assertTrue(first["armed"])
        second = self._tick()
        self.assertFalse(second["armed"])
        self.assertIn("already armed", second["reason"])

    def test_missed_ticks_on_same_episode_never_double_schedule(self):
        """Three ticks in a row on the same unchanged transcript: only the
        first arms, coalescing what a cadence process would see as three
        separate wakeups for one real episode."""
        self._write_transcript(
            [_seven_day_record("sess-1", "2026-09-18T07:54:00Z", 1788030000)])
        results = [self._tick() for _ in range(3)]
        self.assertEqual([r["armed"] for r in results], [True, False, False])

    def test_new_episode_with_different_reset_time_arms_again(self):
        self._write_transcript(
            [_seven_day_record("sess-1", "2026-09-18T07:54:00Z", 1788030000)])
        first = self._tick()
        self.assertTrue(first["armed"])

        # A later, genuinely different rejection: same session, a new
        # reset time. Overwriting bumps the file's mtime so it stays the
        # newest transcript.
        self._write_transcript(
            [_seven_day_record("sess-1", "2026-09-19T04:00:00Z", 1788120000)])
        second = self._tick()
        self.assertTrue(second["armed"])
        self.assertEqual(second["classification"]["resets_at"], 1788120000)

    def test_five_hour_episode_with_no_reset_time_still_dedupes_on_timestamp(self):
        """five_hour carries resets_at=None on every measured record
        (limit_watch.py's own documented contract), so the episode key
        must fall back to the record's own timestamp, not resets_at, or
        every five_hour rejection on one session would look identical."""
        five_hour = _api_error_record(
            "You've hit your session limit · resets 10:40am (Asia/Tokyo)",
            "sess-1", "2026-09-18T07:54:00Z", quota_limits=None)
        self._write_transcript([five_hour])
        first = self._tick()
        self.assertTrue(first["armed"])
        second = self._tick()
        self.assertFalse(second["armed"])

        later = _api_error_record(
            "You've hit your session limit · resets 3:00pm (Asia/Tokyo)",
            "sess-1", "2026-09-18T12:00:00Z", quota_limits=None)
        self._write_transcript([later])
        third = self._tick()
        self.assertTrue(third["armed"])


class TestStateFailureDirection(LimitMonitorTestCase):
    def test_corrupt_state_file_fails_open_and_arms_nothing(self):
        """A corrupt state file blocks the arm (never crashes, never
        guesses whether the episode was already handled): NO-DATA in the
        reason, armed False, and no exception raised getting there."""
        os.makedirs(os.path.dirname(self.state_path))
        with open(self.state_path, "w", encoding="utf-8") as f:
            f.write("{not valid json::")
        self._write_transcript(
            [_seven_day_record("sess-1", "2026-09-18T07:54:00Z", 1788030000)])
        out = self._tick()
        self.assertEqual(out["class"], "seven_day")
        self.assertFalse(out["armed"])
        self.assertIn("NO-DATA", out["reason"])
        self.assertFalse(os.path.exists(self.flag_path))

    def test_unreadable_transcript_is_no_data_not_a_crash(self):
        bogus = os.path.join(self.project_dir, "bogus.jsonl")
        with open(bogus, "w", encoding="utf-8") as f:
            f.write("not json at all\n")
        out = self._tick()
        self.assertEqual(out["class"], "NO-DATA")
        self.assertFalse(out["armed"])


class TestRunIdPassthrough(LimitMonitorTestCase):
    """tick() used to default flag_path to limit_watch.DEFAULT_FLAG_PATH
    (the single shared armed.flag, now removed). Fixed 2026-09-18: with no
    flag_path override, tick() must hand run_id straight to limit_watch.arm(),
    which writes the caller's own armed.d/<run_id>.flag slot and refuses
    (arms nothing) rather than reach for a shared flag when no run id is
    known anywhere, not even from the environment."""

    def setUp(self):
        super().setUp()
        self.restart_dir = os.path.join(self.root, "brother-restart")
        self._old_env = os.environ.pop(limit_watch.RUN_ID_ENV_VAR, None)
        self.addCleanup(self._restore_env)

    def _restore_env(self):
        if self._old_env is None:
            os.environ.pop(limit_watch.RUN_ID_ENV_VAR, None)
        else:
            os.environ[limit_watch.RUN_ID_ENV_VAR] = self._old_env

    def _tick_no_flag_path(self, run_id=None, until=None):
        # No flag_path passed: tick() leaves it None and hands run_id/until
        # straight to arm_fn, exactly the path this fix changed. arm_fn
        # here is the real limit_watch.arm with restart_dir pinned to a
        # tempdir, so the resolved per-run slot never touches the real
        # ~/.claude/brother-restart tree.
        return limit_monitor.tick(
            projects_root=self.projects_root, state_path=self.state_path,
            run_dir=self.root, run_id=run_id, until=until,
            schedule_fn=_fake_schedule,
            arm_fn=lambda run_dir, classification, **kw: limit_watch.arm(
                run_dir, classification, restart_dir=self.restart_dir, **kw))

    def test_no_run_id_anywhere_refuses_and_never_writes_a_shared_flag(self):
        self._write_transcript(
            [_seven_day_record("sess-1", "2026-09-18T07:54:00Z", 1788030000)])
        out = self._tick_no_flag_path()
        self.assertEqual(out["class"], "seven_day")
        self.assertFalse(out["armed"])
        self.assertIn("refused", out["arm_result"]["reason"])
        self.assertFalse(os.path.exists(self.restart_dir),
                         "no flag_path and no run id must write nothing")

    def test_run_id_argument_writes_that_runs_own_slot(self):
        self._write_transcript(
            [_seven_day_record("sess-1", "2026-09-18T07:54:00Z", 1788030000)])
        out = self._tick_no_flag_path(run_id="orch-1020",
                                      until=int(time.time()) + 3600)
        self.assertTrue(out["armed"])
        self.assertTrue(os.path.isfile(
            os.path.join(self.restart_dir, "armed.d", "orch-1020.flag")))
        self.assertTrue(os.path.isfile(
            os.path.join(self.restart_dir, "armed.d", "orch-1020.until")))

    def test_env_run_id_is_honored_when_no_argument_given(self):
        os.environ[limit_watch.RUN_ID_ENV_VAR] = "env-run"
        self._write_transcript(
            [_seven_day_record("sess-1", "2026-09-18T07:54:00Z", 1788030000)])
        out = self._tick_no_flag_path(until=int(time.time()) + 3600)
        self.assertTrue(out["armed"])
        self.assertTrue(os.path.isfile(
            os.path.join(self.restart_dir, "armed.d", "env-run.flag")))

    def test_run_id_with_no_expiry_source_refuses_via_tick(self):
        """A known run id but no until and no run_plan: arm() refuses
        (DEFECT 1's expiry requirement) and tick() must surface that as
        armed=False, not the pre-fix bug where any non-raising arm_fn call
        was reported as armed=True regardless of what it actually did."""
        self._write_transcript(
            [_seven_day_record("sess-1", "2026-09-18T07:54:00Z", 1788030000)])
        out = self._tick_no_flag_path(run_id="orch-1020")
        self.assertFalse(out["armed"])
        self.assertIn("no run expiry known", out["reason"])
        self.assertFalse(os.path.exists(self.restart_dir))


class TestPrintPlist(unittest.TestCase):
    def test_print_plist_contains_label_and_interval(self):
        text = limit_monitor.render_monitor_plist(
            script_path="/abs/path/scripts/limit_monitor.py")
        self.assertIn(
            "<key>Label</key><string>com.brother.limit-monitor</string>",
            text)
        self.assertIn("<key>StartInterval</key><integer>300</integer>", text)
        self.assertIn("/abs/path/scripts/limit_monitor.py", text)
        self.assertIn("--tick", text)

    def test_main_print_plist_flag(self):
        self.assertEqual(limit_monitor.main(["--print-plist"]), 0)


if __name__ == "__main__":
    unittest.main()
