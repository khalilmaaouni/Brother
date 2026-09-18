#!/usr/bin/env python3
"""Tests for limit_watch.py and restart_schedule.py. Fixtures are built
from REAL transcript records measured on this machine 2026-08-30
(`grep -rl '"isApiErrorMessage":true' ~/.claude/projects`), redacted of
session/request ids and local paths, one fixture jsonl per class plus
normal and unreadable. No em or en dashes."""

import datetime
import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import limit_watch
import restart_schedule

# E100: one sandbox for every temp tree this process makes, removed at exit.
import os as _e100_os, sys as _e100_sys  # noqa: E402
_e100_sys.path.append(_e100_os.path.join(
    _e100_os.path.dirname(_e100_os.path.abspath(__file__)), '.'))
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


def _fake_schedule(resets_at, margin=120):
    return {"scheduled": resets_at is not None}


class TestArmPerRunSlot(unittest.TestCase):
    """LIMIT monitor fix, 2026-09-18: arm() with no explicit flag_path must
    write the caller's own run_id slot, never one shared legacy flag, and
    must refuse rather than guess when no run id is known anywhere."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.restart_dir = os.path.join(self.tmp, "brother-restart")
        self._old_env = os.environ.pop(limit_watch.RUN_ID_ENV_VAR, None)
        self.addCleanup(self._restore_env)

    def _restore_env(self):
        if self._old_env is None:
            os.environ.pop(limit_watch.RUN_ID_ENV_VAR, None)
        else:
            os.environ[limit_watch.RUN_ID_ENV_VAR] = self._old_env

    def test_missing_run_id_refuses_and_writes_nothing(self):
        """No run_id argument and no BROTHER_RUN_ID: arm() refuses. The
        bad state a green check would also pass here is a silent write to
        DEFAULT_RESTART_DIR/armed.flag (the old shared slot); this test
        fails if that ever happens again by checking the whole tree stays
        empty, not just that armed=False."""
        result = limit_watch.classify(SEVEN_DAY_RECORD)
        out = limit_watch.arm("/some/run/dir", result, restart_dir=self.restart_dir,
                              schedule_fn=_fake_schedule)
        self.assertFalse(out["armed"])
        self.assertIn("refused", out["reason"])
        self.assertIn("run id is missing", out["reason"])
        self.assertFalse(os.path.exists(self.restart_dir),
                         "arm() must not create anything when it refuses")

    def test_two_run_ids_get_two_slots_neither_clobbers_the_other(self):
        result = limit_watch.classify(SEVEN_DAY_RECORD)
        future = int(time.time()) + 3600
        out_a = limit_watch.arm("/run/a", result, run_id="orch-1020",
                                restart_dir=self.restart_dir, until=future,
                                schedule_fn=_fake_schedule)
        out_b = limit_watch.arm("/run/b", result, run_id="cut-1020",
                                restart_dir=self.restart_dir, until=future,
                                schedule_fn=_fake_schedule)
        self.assertTrue(out_a["armed"])
        self.assertTrue(out_b["armed"])
        slot_a = os.path.join(self.restart_dir, "armed.d", "orch-1020.flag")
        slot_b = os.path.join(self.restart_dir, "armed.d", "cut-1020.flag")
        self.assertTrue(os.path.isfile(slot_a))
        self.assertTrue(os.path.isfile(slot_b))
        with open(slot_a) as f:
            self.assertIn("/run/a", f.read())
        with open(slot_b) as f:
            self.assertIn("/run/b", f.read())
        # the legacy shared name never appears anywhere under restart_dir
        for root, _dirs, names in os.walk(self.restart_dir):
            self.assertNotIn("armed.flag", names)

    def test_same_run_id_armed_twice_is_idempotent(self):
        result = limit_watch.classify(SEVEN_DAY_RECORD)
        future = int(time.time()) + 3600
        first = limit_watch.arm("/run/a", result, run_id="orch-1020",
                                restart_dir=self.restart_dir, until=future,
                                schedule_fn=_fake_schedule)
        second = limit_watch.arm("/run/a", result, run_id="orch-1020",
                                 restart_dir=self.restart_dir, until=future,
                                 schedule_fn=_fake_schedule)
        self.assertTrue(first["armed"])
        self.assertTrue(second["armed"])
        self.assertEqual(first["flag_path"], second["flag_path"])
        # second is a live re-arm of the same slot: DEFECT 2's overwrite
        # guard treats that as "already armed by the run itself" rather
        # than rewriting, and exactly one flag/until pair exists either way.
        self.assertEqual(second["reason"], "already armed by the run itself")
        slots = sorted(os.listdir(os.path.join(self.restart_dir, "armed.d")))
        self.assertEqual(slots, ["orch-1020.flag", "orch-1020.until"])

    def test_armed_d_directory_is_created_when_missing(self):
        self.assertFalse(os.path.exists(self.restart_dir))
        result = limit_watch.classify(SEVEN_DAY_RECORD)
        out = limit_watch.arm("/run/a", result, run_id="orch-1020",
                              restart_dir=self.restart_dir,
                              until=int(time.time()) + 3600,
                              schedule_fn=_fake_schedule)
        self.assertTrue(out["armed"])
        self.assertTrue(os.path.isdir(os.path.join(self.restart_dir, "armed.d")))

    def test_run_id_env_var_is_used_when_no_explicit_run_id(self):
        os.environ[limit_watch.RUN_ID_ENV_VAR] = "env-run"
        result = limit_watch.classify(SEVEN_DAY_RECORD)
        out = limit_watch.arm("/run/env", result, restart_dir=self.restart_dir,
                              until=int(time.time()) + 3600,
                              schedule_fn=_fake_schedule)
        self.assertTrue(out["armed"])
        self.assertTrue(os.path.isfile(
            os.path.join(self.restart_dir, "armed.d", "env-run.flag")))

    def test_explicit_flag_path_bypasses_run_id_resolution(self):
        """Existing callers (limit_drill.py, tests) that pass flag_path
        explicitly must keep working exactly as before, run id or not."""
        flag_path = os.path.join(self.tmp, "explicit.flag")
        result = limit_watch.classify(SEVEN_DAY_RECORD)
        out = limit_watch.arm("/run/x", result, flag_path=flag_path,
                              schedule_fn=_fake_schedule)
        self.assertTrue(out["armed"])
        self.assertTrue(os.path.isfile(flag_path))

    def test_invalid_run_id_refuses_without_writing(self):
        result = limit_watch.classify(SEVEN_DAY_RECORD)
        out = limit_watch.arm("/run/a", result, run_id="has space",
                              restart_dir=self.restart_dir,
                              schedule_fn=_fake_schedule)
        self.assertFalse(out["armed"])
        self.assertIn("whitespace", out["reason"])
        self.assertFalse(os.path.exists(self.restart_dir))

    def test_run_id_with_a_path_separator_refuses_without_writing(self):
        """Added by the orchestrator after its own mutation SURVIVED: the
        separator guard in _invalid_run_id was deletable with every test
        green, so '../escape' would have written outside armed.d."""
        result = limit_watch.classify(SEVEN_DAY_RECORD)
        out = limit_watch.arm("/run/a", result, run_id="../escape",
                              restart_dir=self.restart_dir,
                              schedule_fn=_fake_schedule)
        self.assertFalse(out["armed"])
        self.assertIn("path separator", out["reason"])
        self.assertFalse(os.path.exists(self.restart_dir))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "escape.flag")))


def _write_plan(path, hard_stop_iso, drain_start_iso=None):
    """A minimal plan JSON in run_window.py's own {"window": {...}} shape."""
    window = {"drain_start": drain_start_iso or hard_stop_iso,
              "hard_stop": hard_stop_iso}
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"window": window}, f)


def _restart_sh_would_keep_armed(flag_path, until_path, now):
    """Simulates restart.sh's own disarm rule in Python (the shell script
    is read only, never edited): "$NOW" -ge "$(cat "$U" 2>/dev/null ||
    echo 0)" disarms; this returns True only when restart.sh would leave
    the slot armed. Mirrors restart.sh line for line, not a redesign."""
    if not os.path.isfile(flag_path):
        return False
    try:
        with open(until_path, encoding="utf-8") as f:
            until_epoch = int(f.read().strip())
    except (OSError, ValueError):
        until_epoch = 0  # restart.sh's own `cat ... || echo 0` fallback
    return now < until_epoch


class TestArmExpiry(unittest.TestCase):
    """DEFECT 1 (2026-09-18): restart.sh disarms any armed.d/<run_id>.flag
    whose sibling <run_id>.until is missing or past, and nothing ever wrote
    that sibling, so a resident monitor's arm was always dead by the next
    poll. DEFECT 2: a second arm of an already-live slot must not clobber a
    hand-written resume prompt."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.restart_dir = os.path.join(self.tmp, "brother-restart")
        self._old_run_id = os.environ.pop(limit_watch.RUN_ID_ENV_VAR, None)
        self._old_run_plan = os.environ.pop(limit_watch.RUN_PLAN_ENV_VAR, None)
        self.addCleanup(self._restore_env)

    def _restore_env(self):
        for var, old in ((limit_watch.RUN_ID_ENV_VAR, self._old_run_id),
                         (limit_watch.RUN_PLAN_ENV_VAR, self._old_run_plan)):
            if old is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = old

    def _slot(self, run_id="orch-1020"):
        return (os.path.join(self.restart_dir, "armed.d", run_id + ".flag"),
                os.path.join(self.restart_dir, "armed.d", run_id + ".until"))

    def test_until_written_from_plan_hard_stop(self):
        now = datetime.datetime.now().astimezone()
        hard_stop = now + datetime.timedelta(hours=1)
        plan_path = os.path.join(self.tmp, "plan.json")
        _write_plan(plan_path, hard_stop.isoformat())

        result = limit_watch.classify(SEVEN_DAY_RECORD)
        out = limit_watch.arm("/run/a", result, run_id="orch-1020",
                              restart_dir=self.restart_dir, run_plan=plan_path,
                              schedule_fn=_fake_schedule)
        self.assertTrue(out["armed"])
        flag_path, until_path = self._slot()
        self.assertTrue(os.path.isfile(until_path))
        with open(until_path) as f:
            written = int(f.read().strip())
        self.assertEqual(written, int(hard_stop.timestamp()))
        self.assertEqual(out["expiry"], written)

    def test_explicit_until_wins_over_plan(self):
        now = datetime.datetime.now().astimezone()
        plan_path = os.path.join(self.tmp, "plan.json")
        _write_plan(plan_path, (now + datetime.timedelta(hours=1)).isoformat())
        explicit = int(now.timestamp()) + 7200

        result = limit_watch.classify(SEVEN_DAY_RECORD)
        out = limit_watch.arm("/run/a", result, run_id="orch-1020",
                              restart_dir=self.restart_dir, run_plan=plan_path,
                              until=explicit, schedule_fn=_fake_schedule)
        self.assertTrue(out["armed"])
        self.assertEqual(out["expiry"], explicit)

    def test_no_plan_and_no_until_refuses_and_writes_nothing(self):
        result = limit_watch.classify(SEVEN_DAY_RECORD)
        out = limit_watch.arm("/run/a", result, run_id="orch-1020",
                              restart_dir=self.restart_dir,
                              schedule_fn=_fake_schedule)
        self.assertFalse(out["armed"])
        self.assertIn("refused", out["reason"])
        self.assertIn("no run expiry known", out["reason"])
        self.assertFalse(os.path.exists(self.restart_dir),
                         "a refused arm must write nothing, not even armed.d")

    def test_past_hard_stop_refuses(self):
        now = datetime.datetime.now().astimezone()
        plan_path = os.path.join(self.tmp, "plan.json")
        _write_plan(plan_path, (now - datetime.timedelta(minutes=5)).isoformat(),
                    drain_start_iso=(now - datetime.timedelta(hours=1)).isoformat())

        result = limit_watch.classify(SEVEN_DAY_RECORD)
        out = limit_watch.arm("/run/a", result, run_id="orch-1020",
                              restart_dir=self.restart_dir, run_plan=plan_path,
                              schedule_fn=_fake_schedule)
        self.assertFalse(out["armed"])
        self.assertIn("already passed", out["reason"])
        self.assertFalse(os.path.exists(self.restart_dir))

    def test_explicit_past_until_also_refuses(self):
        """Not named in the plan-only defect report, but the same invariant
        (never write a .until that is already expired): checked here so
        the explicit-until path cannot regress it silently."""
        past = int(datetime.datetime.now().astimezone().timestamp()) - 60
        result = limit_watch.classify(SEVEN_DAY_RECORD)
        out = limit_watch.arm("/run/a", result, run_id="orch-1020",
                              restart_dir=self.restart_dir, until=past,
                              schedule_fn=_fake_schedule)
        self.assertFalse(out["armed"])
        self.assertIn("not in the future", out["reason"])
        self.assertFalse(os.path.exists(self.restart_dir))

    def test_existing_live_slot_is_untouched_byte_for_byte(self):
        flag_path, until_path = self._slot()
        os.makedirs(os.path.dirname(flag_path))
        with open(flag_path, "w") as f:
            f.write("a hand-written resume prompt, richer than the default\n")
        future = int(datetime.datetime.now().astimezone().timestamp()) + 3600
        with open(until_path, "w") as f:
            f.write("%d\n" % future)
        with open(flag_path, "rb") as f:
            flag_before = f.read()
        with open(until_path, "rb") as f:
            until_before = f.read()

        result = limit_watch.classify(SEVEN_DAY_RECORD)
        out = limit_watch.arm("/run/DIFFERENT", result, run_id="orch-1020",
                              restart_dir=self.restart_dir,
                              until=future + 1000,  # a fresh, valid expiry offered anyway
                              schedule_fn=_fake_schedule)
        self.assertTrue(out["armed"])
        self.assertEqual(out["reason"], "already armed by the run itself")
        with open(flag_path, "rb") as f:
            self.assertEqual(f.read(), flag_before)
        with open(until_path, "rb") as f:
            self.assertEqual(f.read(), until_before)

    def test_expired_slot_is_replaced(self):
        flag_path, until_path = self._slot()
        os.makedirs(os.path.dirname(flag_path))
        with open(flag_path, "w") as f:
            f.write("a stale resume prompt from a slot nobody disarmed\n")
        past = int(datetime.datetime.now().astimezone().timestamp()) - 60
        with open(until_path, "w") as f:
            f.write("%d\n" % past)

        future = int(datetime.datetime.now().astimezone().timestamp()) + 3600
        result = limit_watch.classify(SEVEN_DAY_RECORD)
        out = limit_watch.arm("/run/fresh", result, run_id="orch-1020",
                              restart_dir=self.restart_dir, until=future,
                              schedule_fn=_fake_schedule)
        self.assertTrue(out["armed"])
        self.assertNotIn("already armed", out.get("reason", ""))
        with open(flag_path) as f:
            self.assertIn("/run/fresh", f.read())
        with open(until_path) as f:
            self.assertEqual(int(f.read().strip()), future)

    def test_missing_until_on_an_existing_flag_is_also_replaced(self):
        """A flag with no sibling .until at all is exactly restart.sh's own
        "missing" case: not live, so not "already armed"."""
        flag_path, until_path = self._slot()
        os.makedirs(os.path.dirname(flag_path))
        with open(flag_path, "w") as f:
            f.write("an old flag from before this fix existed\n")
        self.assertFalse(os.path.exists(until_path))

        future = int(datetime.datetime.now().astimezone().timestamp()) + 3600
        result = limit_watch.classify(SEVEN_DAY_RECORD)
        out = limit_watch.arm("/run/fresh", result, run_id="orch-1020",
                              restart_dir=self.restart_dir, until=future,
                              schedule_fn=_fake_schedule)
        self.assertTrue(out["armed"])
        self.assertNotEqual(out.get("reason"), "already armed by the run itself")
        with open(flag_path) as f:
            self.assertIn("/run/fresh", f.read())
        self.assertTrue(os.path.isfile(until_path))

    def test_written_pair_is_accepted_by_restart_shs_own_rule(self):
        """Simulates restart.sh's exact disarm arithmetic in Python against
        the real files arm() wrote, rather than trusting arm()'s own
        report of success."""
        future = int(datetime.datetime.now().astimezone().timestamp()) + 3600
        result = limit_watch.classify(SEVEN_DAY_RECORD)
        out = limit_watch.arm("/run/a", result, run_id="orch-1020",
                              restart_dir=self.restart_dir, until=future,
                              schedule_fn=_fake_schedule)
        self.assertTrue(out["armed"])
        flag_path, until_path = self._slot()
        now = int(datetime.datetime.now().timestamp())
        self.assertTrue(_restart_sh_would_keep_armed(flag_path, until_path, now))

    def test_a_missing_until_would_be_disarmed_by_restart_sh(self):
        """Control for the simulator itself: an old-style flag with no
        .until reads as NOT kept armed, matching restart.sh's own
        "missing .until" branch. Proves the simulator can go red."""
        flag_path, until_path = self._slot()
        os.makedirs(os.path.dirname(flag_path))
        with open(flag_path, "w") as f:
            f.write("orphaned flag, no sibling .until\n")
        now = int(datetime.datetime.now().timestamp())
        self.assertFalse(_restart_sh_would_keep_armed(flag_path, until_path, now))


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
