#!/usr/bin/env python3
"""Tests for limit_watch.py and restart_schedule.py. Fixtures are built
from REAL transcript records measured on this machine 2026-08-30
(`grep -rl '"isApiErrorMessage":true' ~/.claude/projects`), redacted of
session/request ids and local paths, one fixture jsonl per class plus
normal and unreadable. No em or en dashes."""

import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import limit_watch
import restart_schedule


def _api_error_record(text, quota_limits=None):
    """The measured shape shared by every real rate_limit rejection: an
    assistant record with isApiErrorMessage true, error rate_limit,
    apiErrorStatus 429."""
    return {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": text}],
        },
        "quotaLimits": quota_limits,
        "error": "rate_limit",
        "isApiErrorMessage": True,
        "apiErrorStatus": 429,
    }


def _plain_record(text):
    """An ordinary assistant record, no limit involved."""
    return {
        "type": "assistant",
        "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
    }


# Real fixtures, one per measured class.

FIVE_HOUR_RECORD = _api_error_record(
    "You've hit your session limit · resets 10:40am (Asia/Tokyo)",
    quota_limits=None)  # measured: quotaLimits null on every session-limit
                        # record found (44/44), never an epoch.

SEVEN_DAY_RECORD = _api_error_record(
    "You've hit your weekly limit · resets Aug 30 at 4am (Asia/Tokyo)",
    quota_limits={
        "status": "rejected", "resetsAt": 1788030000,
        "unifiedRateLimitFallbackAvailable": False,
        "rateLimitType": "seven_day", "overageStatus": "rejected",
        "overageDisabledReason": "org_level_disabled", "isUsingOverage": False,
    })

MONTHLY_SPEND_RECORD = _api_error_record(
    "You've hit your monthly spend limit · raise it at "
    "claude.ai/settings/usage?from=cc_cli_limit_message",
    quota_limits={
        # measured mislabel: the structured field says five_hour even
        # though the text is the monthly-spend rejection.
        "status": "rejected", "resetsAt": 1787347200,
        "unifiedRateLimitFallbackAvailable": False,
        "rateLimitType": "five_hour", "overageStatus": "rejected",
        "overageDisabledReason": "org_level_disabled_until", "isUsingOverage": False,
    })

FALLBACK_MODEL_RECORD = _api_error_record(
    "You've reached your Fable 5 limit. Run /usage-credits to continue "
    "or switch models with /model.",
    quota_limits=None)

NORMAL_RECORD = _plain_record("Here is the file you asked for.")


def _write_jsonl(records):
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    return path


class TestClassify(unittest.TestCase):
    """classify() against the measured record shapes directly."""

    def test_normal_record(self):
        out = limit_watch.classify(NORMAL_RECORD)
        self.assertEqual(out["class"], "NORMAL")
        self.assertIsNone(out["resets_at"])

    def test_five_hour_real_shape_has_no_epoch(self):
        """The genuine session-limit class measured null quotaLimits on
        every one of 44 records: resets_at is null, not guessed from the
        bare clock time in the text."""
        out = limit_watch.classify(FIVE_HOUR_RECORD)
        self.assertEqual(out["class"], "five_hour")
        self.assertIsNone(out["resets_at"])
        self.assertIn("session limit", out["raw_text_excerpt"])

    def test_seven_day_uses_structured_epoch(self):
        out = limit_watch.classify(SEVEN_DAY_RECORD)
        self.assertEqual(out["class"], "seven_day")
        self.assertEqual(out["resets_at"], 1788030000)

    def test_monthly_spend_mislabel_resolved_by_text(self):
        """quotaLimits.rateLimitType says five_hour; the text says monthly
        spend and wins. resets_at is null even though quotaLimits carried
        one, because raising the cap needs the founder's hand, not a
        timed restart."""
        out = limit_watch.classify(MONTHLY_SPEND_RECORD)
        self.assertEqual(out["class"], "monthly-spend")
        self.assertIsNone(out["resets_at"])
        self.assertIn("claude.ai/settings/usage", out["message_url"])

    def test_fallback_model_has_no_reset_anywhere(self):
        out = limit_watch.classify(FALLBACK_MODEL_RECORD)
        self.assertEqual(out["class"], "fallback-model")
        self.assertIsNone(out["resets_at"])
        self.assertIn("standing model cap", out["remedy"])

    def test_non_rate_limit_api_error_is_normal(self):
        rec = _api_error_record("API Error: 529 Overloaded.")
        rec["error"] = "overloaded"
        rec["apiErrorStatus"] = 529
        out = limit_watch.classify(rec)
        self.assertEqual(out["class"], "NORMAL")

    def test_non_dict_record_is_normal(self):
        out = limit_watch.classify(["not", "a", "dict"])
        self.assertEqual(out["class"], "NORMAL")


class TestWatchTranscript(unittest.TestCase):
    """watch() over real fixture files, proving the LAST record wins and
    an unreadable transcript is NO-DATA, never a guess."""

    def test_reads_the_last_record_not_the_first(self):
        path = _write_jsonl([NORMAL_RECORD, SEVEN_DAY_RECORD])
        try:
            out = limit_watch.watch(transcript_path=path)
            self.assertEqual(out["class"], "seven_day")
        finally:
            os.remove(path)

    def test_project_dir_picks_newest_jsonl(self):
        tmp = tempfile.mkdtemp()
        try:
            older = os.path.join(tmp, "a.jsonl")
            newer = os.path.join(tmp, "b.jsonl")
            with open(older, "w") as f:
                f.write(json.dumps(NORMAL_RECORD) + "\n")
            time.sleep(0.02)
            with open(newer, "w") as f:
                f.write(json.dumps(FIVE_HOUR_RECORD) + "\n")
            out = limit_watch.watch(project_dir=tmp)
            self.assertEqual(out["class"], "five_hour")
        finally:
            for n in os.listdir(tmp):
                os.remove(os.path.join(tmp, n))
            os.rmdir(tmp)

    def test_missing_transcript_is_no_data(self):
        out = limit_watch.watch(transcript_path="/nowhere/nope.jsonl")
        self.assertEqual(out["class"], "NO-DATA")
        self.assertIn("NO-DATA", out["error"])

    def test_no_path_and_no_project_dir_is_no_data(self):
        out = limit_watch.watch()
        self.assertEqual(out["class"], "NO-DATA")

    def test_empty_transcript_is_no_data(self):
        path = _write_jsonl([])
        try:
            out = limit_watch.watch(transcript_path=path)
            self.assertEqual(out["class"], "NO-DATA")
        finally:
            os.remove(path)


class TestArm(unittest.TestCase):
    """--arm writes the flag with the exact resume command and calls the
    scheduler, without ever touching the real flag path or plist."""

    def test_arm_writes_resume_command(self):
        tmp = tempfile.mkdtemp()
        flag_path = os.path.join(tmp, "armed.flag")
        calls = []

        def fake_schedule(resets_at, margin=120):
            calls.append((resets_at, margin))
            return {"scheduled": resets_at is not None}

        result = limit_watch.classify(SEVEN_DAY_RECORD)
        out = limit_watch.arm("/some/run/dir", result, flag_path=flag_path,
                              margin=300, schedule_fn=fake_schedule)
        self.assertTrue(out["armed"])
        with open(flag_path) as f:
            content = f.read()
        self.assertIn("brother_run.py --resume", content)
        self.assertIn("/some/run/dir", content)
        self.assertEqual(calls, [(1788030000, 300)])

    def test_arm_no_ops_on_normal(self):
        out = limit_watch.arm("/some/run/dir", {"class": "NORMAL"})
        self.assertFalse(out["armed"])

    def test_arm_no_ops_on_no_data(self):
        out = limit_watch.arm("/some/run/dir", {"class": "NO-DATA"})
        self.assertFalse(out["armed"])

    def test_arm_still_writes_flag_when_scheduler_refuses(self):
        """fallback-model and monthly-spend carry no resets_at: the flag
        still records the resume command (a human or a later limit may
        still trigger the restart), the scheduler just reports NO-DATA."""
        tmp = tempfile.mkdtemp()
        flag_path = os.path.join(tmp, "armed.flag")
        result = limit_watch.classify(FALLBACK_MODEL_RECORD)
        out = limit_watch.arm("/some/run/dir", result, flag_path=flag_path,
                              schedule_fn=restart_schedule.schedule)
        self.assertTrue(out["armed"])
        self.assertFalse(out["schedule"]["scheduled"])
        self.assertIn("NO-DATA", out["schedule"]["error"])
        self.assertTrue(os.path.exists(flag_path))


class TestRestartSchedule(unittest.TestCase):
    """restart_schedule.schedule() against a fake plist path and a
    no-op reload_fn: never the real LaunchAgent."""

    def test_refuses_null_resets_at(self):
        out = restart_schedule.schedule(None, plist_path="/tmp/does-not-matter",
                                        reload_fn=None)
        self.assertFalse(out["scheduled"])
        self.assertIn("NO-DATA", out["error"])

    def test_schedules_future_resets_at(self):
        tmp = tempfile.mkdtemp()
        plist_path = os.path.join(tmp, "fake.plist")
        now = 1000000000.0
        resets_at = now + 3600  # one hour out
        out = restart_schedule.schedule(
            resets_at, margin=120, plist_path=plist_path, now=now,
            reload_fn=None)
        self.assertTrue(out["scheduled"])
        self.assertEqual(out["fire_epoch"], resets_at + 120)
        with open(plist_path) as f:
            content = f.read()
        self.assertIn("<key>Label</key><string>com.brother.usage-restart</string>",
                      content)
        self.assertIn("StartCalendarInterval", content)
        expected = time.localtime(resets_at + 120)
        self.assertIn("<key>Hour</key><integer>%d</integer>" % expected.tm_hour,
                      content)
        self.assertIn("<key>Minute</key><integer>%d</integer>" % expected.tm_min,
                      content)

    def test_past_resets_at_schedules_margin_from_now(self):
        now = 1000000000.0
        resets_at = now - 500  # already passed
        out = restart_schedule.schedule(
            resets_at, margin=120, plist_path="/tmp/does-not-matter",
            now=now, reload_fn=None)
        self.assertTrue(out["scheduled"])
        self.assertEqual(out["fire_epoch"], now + 120)

    def test_reload_fn_invoked_only_when_scheduled(self):
        calls = []
        tmp = tempfile.mkdtemp()
        plist_path = os.path.join(tmp, "fake.plist")
        restart_schedule.schedule(
            2000000000.0, plist_path=plist_path,
            reload_fn=lambda p, label=None: calls.append(p))
        self.assertEqual(calls, [plist_path])

    def test_cli_requires_resets_at(self):
        self.assertEqual(restart_schedule.main([]), 2)


if __name__ == "__main__":
    unittest.main()
