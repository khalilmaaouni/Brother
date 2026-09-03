#!/usr/bin/env python3
"""R25 end to end battery: drives the three limit classes named in R25's
row-level done-check through watch() then arm(), everything injected into
a tempdir (flag_path, schedule_fn, pack_fn). Never touches real
~/.claude, ~/Library, or the real Documents pack root.

Fixture record shapes are the same measured shapes test_limit_watch.py
carries (real transcript records measured on this machine 2026-08-30,
redacted of session/request ids and local paths). No em or en dashes.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import limit_watch


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


# A future resetsAt (year 2099-ish), well past any real "now" this battery
# could run at.
SEVEN_DAY_RECORD = _api_error_record(
    "You've hit your weekly limit · resets Aug 30 at 4am (Asia/Tokyo)",
    quota_limits={
        "status": "rejected", "resetsAt": 4083696000,
        "unifiedRateLimitFallbackAvailable": False,
        "rateLimitType": "seven_day", "overageStatus": "rejected",
        "overageDisabledReason": "org_level_disabled", "isUsingOverage": False,
    })

FIVE_HOUR_RECORD = _api_error_record(
    "You've hit your session limit · resets 10:40am (Asia/Tokyo)",
    quota_limits=None)  # measured: quotaLimits null on every session-limit
                        # record found, never an epoch (see test_limit_watch.py)

NORMAL_RECORD = _plain_record("Here is the file you asked for.")


def _write_jsonl(path, records):
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


class RecordingFn:
    """A callable that records every call's args/kwargs and either returns
    a fixed value or raises a fixed exception, standing in for schedule_fn
    or pack_fn."""

    def __init__(self, return_value=None, raises=None):
        self.calls = []
        self._return_value = return_value
        self._raises = raises

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self._raises is not None:
            raise self._raises
        return self._return_value


class R25EndToEndBase(unittest.TestCase):
    """Common tempdir plumbing: a fixture transcript directory and an
    armed.flag path, both under one per-test tempdir, cleaned in
    tearDown. Verdicts are asserted in the test body, before tearDown
    ever runs, so a cleanup wrinkle never masks a decided result."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="r25-e2e-")
        self.flag_path = os.path.join(self.tmp, "armed.flag")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_transcript(self, records):
        path = os.path.join(self.tmp, "session.jsonl")
        _write_jsonl(path, records)
        return path


class TestSevenDayLeg(R25EndToEndBase):
    """Leg 1: seven_day arms the flag, schedules the restart, AND emits
    the portable pack."""

    def test_seven_day_arms_flag_schedule_and_pack(self):
        transcript = self._write_transcript([SEVEN_DAY_RECORD])
        result = limit_watch.watch(transcript_path=transcript)
        self.assertEqual(result["class"], "seven_day")

        schedule_fn = RecordingFn(return_value={"scheduled": True})
        pack_fn = RecordingFn(return_value=("/fake/2026-08-30-portable-pack.zip", False))

        out = limit_watch.arm("/some/run/dir", result, flag_path=self.flag_path,
                              margin=120, schedule_fn=schedule_fn, pack_fn=pack_fn)

        self.assertTrue(out["armed"])
        self.assertTrue(os.path.exists(self.flag_path))
        with open(self.flag_path) as f:
            content = f.read()
        self.assertIn("brother_run.py --resume", content)
        self.assertIn("/some/run/dir", content)

        self.assertEqual(len(schedule_fn.calls), 1)
        args, kwargs = schedule_fn.calls[0]
        self.assertEqual(args[0], 4083696000)  # resets_at reached the scheduler

        self.assertEqual(len(pack_fn.calls), 1)  # the pack WAS called
        pack_args, _pack_kwargs = pack_fn.calls[0]
        self.assertEqual(pack_args[0], [limit_watch.REPO_ROOT])  # with the repo list
        self.assertEqual(out["pack"], "/fake/2026-08-30-portable-pack.zip")
        self.assertNotIn("pack_degraded", out)  # a clean pack is not flagged

    def test_seven_day_degraded_pack_says_so(self):
        transcript = self._write_transcript([SEVEN_DAY_RECORD])
        result = limit_watch.watch(transcript_path=transcript)

        schedule_fn = RecordingFn(return_value={"scheduled": True})
        pack_fn = RecordingFn(return_value=("/fake/pack.zip", True))  # had_no_data

        out = limit_watch.arm("/some/run/dir", result, flag_path=self.flag_path,
                              schedule_fn=schedule_fn, pack_fn=pack_fn)

        self.assertTrue(out["armed"])
        self.assertEqual(out["pack"], "/fake/pack.zip")
        self.assertIn("pack_degraded", out)  # a partial pack never reads as clean
        self.assertIn("NO-DATA", out["pack_degraded"])

    def test_seven_day_pack_failure_never_aborts_the_arm(self):
        transcript = self._write_transcript([SEVEN_DAY_RECORD])
        result = limit_watch.watch(transcript_path=transcript)

        schedule_fn = RecordingFn(return_value={"scheduled": True})
        pack_fn = RecordingFn(raises=OSError("disk full"))

        out = limit_watch.arm("/some/run/dir", result, flag_path=self.flag_path,
                              schedule_fn=schedule_fn, pack_fn=pack_fn)

        self.assertTrue(out["armed"])  # the flag/schedule still landed
        self.assertTrue(os.path.exists(self.flag_path))
        self.assertIn("pack", out)
        self.assertIn("disk full", out["pack"])  # failure named, never silent


class TestFiveHourLeg(R25EndToEndBase):
    """Leg 2: five_hour arms the flag and schedules, but never calls the
    pack. resets_at is None for this class on this machine (measured, per
    limit_watch's own module docstring and test_limit_watch.py): the
    scheduler is still called with whatever the classification promises,
    never a guessed value."""

    def test_five_hour_arms_without_pack(self):
        transcript = self._write_transcript([FIVE_HOUR_RECORD])
        result = limit_watch.watch(transcript_path=transcript)
        self.assertEqual(result["class"], "five_hour")
        self.assertIsNone(result["resets_at"])  # measured: no epoch for this class

        schedule_fn = RecordingFn(return_value={"scheduled": False,
                                                 "error": "NO-DATA: resets_at is null"})
        pack_fn = RecordingFn(return_value=("/fake/pack.zip", False))

        out = limit_watch.arm("/some/run/dir", result, flag_path=self.flag_path,
                              schedule_fn=schedule_fn, pack_fn=pack_fn)

        self.assertTrue(out["armed"])
        self.assertTrue(os.path.exists(self.flag_path))
        self.assertEqual(len(schedule_fn.calls), 1)
        args, _kwargs = schedule_fn.calls[0]
        self.assertIsNone(args[0])  # resets_at is None, exactly as classified

        self.assertEqual(pack_fn.calls, [])  # pack_fn NOT called for this class
        self.assertNotIn("pack", out)


class TestDrivenBackwards(R25EndToEndBase):
    """Leg 3: a limit that never fires leaves no pause artifacts, whether
    the transcript is NORMAL or unreadable (NO-DATA)."""

    def test_normal_leaves_no_artifacts(self):
        transcript = self._write_transcript([NORMAL_RECORD])
        result = limit_watch.watch(transcript_path=transcript)
        self.assertEqual(result["class"], "NORMAL")

        schedule_fn = RecordingFn(return_value={"scheduled": True})
        pack_fn = RecordingFn(return_value=("/fake/pack.zip", False))

        out = limit_watch.arm("/some/run/dir", result, flag_path=self.flag_path,
                              schedule_fn=schedule_fn, pack_fn=pack_fn)

        self.assertFalse(out["armed"])
        self.assertFalse(os.path.exists(self.flag_path))  # no flag file created
        self.assertEqual(schedule_fn.calls, [])  # scheduler never called
        self.assertEqual(pack_fn.calls, [])  # pack never called

    def test_no_data_leaves_no_artifacts(self):
        unreadable_path = os.path.join(self.tmp, "does-not-exist.jsonl")
        result = limit_watch.watch(transcript_path=unreadable_path)
        self.assertEqual(result["class"], "NO-DATA")

        schedule_fn = RecordingFn(return_value={"scheduled": True})
        pack_fn = RecordingFn(return_value=("/fake/pack.zip", False))

        out = limit_watch.arm("/some/run/dir", result, flag_path=self.flag_path,
                              schedule_fn=schedule_fn, pack_fn=pack_fn)

        self.assertFalse(out["armed"])
        self.assertFalse(os.path.exists(self.flag_path))
        self.assertEqual(schedule_fn.calls, [])
        self.assertEqual(pack_fn.calls, [])


if __name__ == "__main__":
    unittest.main()
