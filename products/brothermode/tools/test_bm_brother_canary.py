#!/usr/bin/env python3
"""Tests for tools/bm_brother_canary.py, the disk-backed BrotherMode
liveness canary.

Every state-level test passes an explicit fake env={"HOME": tmpdir}
(never the real process HOME) into the module's own functions, the same
technique tools/bm_session_cap.py's own tests use for its cap-file
override. The two CLI-path tests swap sys.stdin/sys.stdout for StringIO
and call bm_brother_canary.main(argv) in process, the same technique
tools/test_bm_session_cap.py uses for its own hook entrypoint, patching
os.environ so HOME is redirected there too.

Run: python3 tools/test_bm_brother_canary.py      (unittest output, exit 0 or 1)
"""
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
import unittest.mock
from datetime import datetime, timedelta, timezone

# E100: one sandbox for every temp tree this process makes, removed at exit.
import os as _e100_os, sys as _e100_sys  # noqa: E402
_e100_sys.path.append(_e100_os.path.join(
    _e100_os.path.dirname(_e100_os.path.abspath(__file__)), '../../../scripts'))
try:  # noqa: E402
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    # A packager (scripts/export_public.py, make_benchmark_bundle.py) can
    # copy this test without scripts/tmp_sandbox.py beside it. Say so
    # rather than dying: the sandbox is hygiene, not the subject.
    _e100_sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % _e100_os.path.basename(__file__))

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _iso(dt):
    return dt.astimezone(timezone.utc).isoformat()


def _write_consented_config(home):
    """A ~/.brotherme/config.json reading as consented, the same shape
    tools/test_bm_sessionstart.py's own _write_consented_config writes.
    cmd_postskill and cmd_sessionstart both gate on scripts/setup.py's
    is_consented(), so any CLI-level test needs this fixture or every
    call returns 0 having done nothing."""
    d = os.path.join(home, ".brotherme")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "config.json"), "w", encoding="utf-8") as fh:
        json.dump({"setup_complete": True}, fh)


class CanaryCase(unittest.TestCase):

    def setUp(self):
        self.canary = _load(os.path.join(HERE, "bm_brother_canary.py"),
                            "bm_brother_canary")
        self.tmp = tempfile.TemporaryDirectory()
        self.env = {"HOME": self.tmp.name}
        self.addCleanup(self.tmp.cleanup)

    def state_path(self):
        return self.canary._state_path(self.env)


class AtomicWriteReadTests(CanaryCase):

    def test_round_trips(self):
        data = {"active": True, "last_seen": "x"}
        ok = self.canary._atomic_write(self.state_path(), data)
        self.assertTrue(ok)
        self.assertEqual(self.canary.read_state(self.env), data)

    def test_read_state_missing_file(self):
        self.assertIsNone(self.canary.read_state(self.env))

    def test_read_state_corrupt_json(self):
        path = self.state_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        self.assertIsNone(self.canary.read_state(self.env))

    def test_read_state_truncated_json(self):
        path = self.state_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write('{"active": true, "last_')
        self.assertIsNone(self.canary.read_state(self.env))

    def test_read_state_non_dict_json(self):
        path = self.state_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write('["not", "a", "dict"]')
        self.assertIsNone(self.canary.read_state(self.env))

    def test_write_mode_is_0600(self):
        self.canary._atomic_write(self.state_path(), {"a": 1})
        mode = os.stat(self.state_path()).st_mode & 0o777
        self.assertEqual(mode, 0o600)


class ClassifyPriorStateTests(CanaryCase):

    def test_none_state_is_none(self):
        self.assertIsNone(self.canary.classify_prior_state(None, self.env))

    def test_missing_last_seen_is_none(self):
        self.assertIsNone(
            self.canary.classify_prior_state({}, self.env))

    def test_malformed_last_seen_is_none(self):
        self.assertIsNone(self.canary.classify_prior_state(
            {"last_seen": "not-a-timestamp"}, self.env))

    def test_naive_last_seen_is_none(self):
        self.assertIsNone(self.canary.classify_prior_state(
            {"last_seen": "2026-01-01T00:00:00"}, self.env))

    def test_future_last_seen_is_none(self):
        future = datetime.now(timezone.utc) + timedelta(days=365)
        self.assertIsNone(self.canary.classify_prior_state(
            {"last_seen": _iso(future)}, self.env))

    def test_age_just_under_24h_matching_version_is_resume(self):
        recent = datetime.now(timezone.utc) - timedelta(hours=23, minutes=59)
        state = {"last_seen": _iso(recent), "plugin_version": "9.9.9"}
        with unittest.mock.patch.object(
                self.canary, "_installed_version", return_value="9.9.9"):
            self.assertEqual(
                self.canary.classify_prior_state(state, self.env), "resume")

    def test_age_just_over_24h_is_stale(self):
        old = datetime.now(timezone.utc) - timedelta(hours=24, minutes=1)
        state = {"last_seen": _iso(old), "plugin_version": "9.9.9"}
        with unittest.mock.patch.object(
                self.canary, "_installed_version", return_value="9.9.9"):
            self.assertEqual(
                self.canary.classify_prior_state(state, self.env), "stale")

    def test_version_mismatch_recent_age_is_stale(self):
        recent = datetime.now(timezone.utc) - timedelta(minutes=5)
        state = {"last_seen": _iso(recent), "plugin_version": "1.0.0"}
        with unittest.mock.patch.object(
                self.canary, "_installed_version", return_value="2.0.0"):
            self.assertEqual(
                self.canary.classify_prior_state(state, self.env), "stale")

    def test_unknown_installed_version_falls_back_to_age(self):
        recent = datetime.now(timezone.utc) - timedelta(minutes=5)
        state = {"last_seen": _iso(recent), "plugin_version": "1.0.0"}
        with unittest.mock.patch.object(
                self.canary, "_installed_version", return_value=None):
            self.assertEqual(
                self.canary.classify_prior_state(state, self.env), "resume")

    def test_unknown_recorded_version_falls_back_to_age(self):
        recent = datetime.now(timezone.utc) - timedelta(minutes=5)
        state = {"last_seen": _iso(recent)}  # no plugin_version recorded
        with unittest.mock.patch.object(
                self.canary, "_installed_version", return_value="9.9.9"):
            self.assertEqual(
                self.canary.classify_prior_state(state, self.env), "resume")


class StampActiveTests(CanaryCase):

    def test_first_stamp_is_first(self):
        state, is_first = self.canary.stamp_active(
            "brother:brother", "sess-1", "/proj", env=self.env)
        self.assertTrue(is_first)
        self.assertEqual(state["session_id"], "sess-1")
        self.assertEqual(state["activated_at"], state["last_seen"])
        self.assertEqual(state["activation_count"], 1)

    def test_same_session_id_preserves_activated_at(self):
        first, _ = self.canary.stamp_active(
            "brother:brother", "sess-1", "/proj", env=self.env)
        second, is_first = self.canary.stamp_active(
            "brother:brother", "sess-1", "/proj", env=self.env)
        self.assertFalse(is_first)
        self.assertEqual(second["activated_at"], first["activated_at"])
        # last_seen is still bumped (both are real timestamps; equality
        # here would only fail if the clock ran backwards).
        self.assertGreaterEqual(second["last_seen"], first["last_seen"])
        # A second stamp inside the SAME session never bumps the count.
        self.assertEqual(second["activation_count"], 1)

    def test_different_session_id_resets_activated_at(self):
        first, _ = self.canary.stamp_active(
            "brother:brother", "sess-1", "/proj", env=self.env)
        second, is_first = self.canary.stamp_active(
            "brother:brother", "sess-2", "/proj", env=self.env)
        self.assertTrue(is_first)
        self.assertEqual(second["session_id"], "sess-2")
        self.assertNotEqual(second["activated_at"], first["activated_at"])
        # A genuinely new session carries the count forward and adds one.
        self.assertEqual(second["activation_count"], 2)

    def test_count_is_lifetime_not_reset_by_a_gap(self):
        first, _ = self.canary.stamp_active(
            "brother:brother", "sess-1", "/proj", env=self.env)
        self.assertEqual(first["activation_count"], 1)
        # Simulate a stale, long-abandoned record: same schema, ancient
        # last_seen. The count must still carry forward undiminished, this
        # is a lifetime count with no gap tracking, never a streak.
        state = self.canary.read_state(self.env)
        state["last_seen"] = "2020-01-01T00:00:00+00:00"
        self.canary._atomic_write(self.canary._state_path(self.env), state)
        second, is_first = self.canary.stamp_active(
            "brother:brother", "sess-2", "/proj", env=self.env)
        self.assertTrue(is_first)
        self.assertEqual(second["activation_count"], 2)

    def test_missing_activation_count_in_prior_state_reads_as_zero(self):
        # An older state file written before this field existed: no
        # invented history, count picks up from 0.
        self.canary._atomic_write(
            self.canary._state_path(self.env),
            {"session_id": "sess-old", "last_seen": self.canary._now_utc()})
        state, is_first = self.canary.stamp_active(
            "brother:brother", "sess-new", "/proj", env=self.env)
        self.assertTrue(is_first)
        self.assertEqual(state["activation_count"], 1)


class InstalledVersionTests(CanaryCase):

    def test_no_version_segment_is_none(self):
        self.canary.__file__ = (
            "/fake/repo/products/brothermode/tools/bm_brother_canary.py")
        self.assertIsNone(self.canary._installed_version())

    def test_version_segment_is_returned(self):
        self.canary.__file__ = (
            "/fake/home/.claude/plugins/cache/brother/brother/1.2.3/"
            "runtime/hooks/brothermode/tools/bm_brother_canary.py")
        self.assertEqual(self.canary._installed_version(), "1.2.3")


class FormatAgeTests(CanaryCase):

    def test_minutes(self):
        self.assertEqual(
            self.canary._format_age(timedelta(minutes=45)), "45m ago")

    def test_hours(self):
        self.assertEqual(
            self.canary._format_age(timedelta(hours=3, minutes=10)), "3h ago")

    def test_days(self):
        self.assertEqual(
            self.canary._format_age(timedelta(days=2, hours=5)), "2d ago")

    def test_under_a_minute(self):
        self.assertEqual(
            self.canary._format_age(timedelta(seconds=30)),
            "under a minute ago")


class CliCase(CanaryCase):
    """CLI-level tests: swap stdin/stdout for StringIO and call
    bm_brother_canary.main(argv) directly, the same in-process technique
    tools/test_bm_session_cap.py uses for its own hook entrypoint."""

    def setUp(self):
        super(CliCase, self).setUp()
        # cmd_postskill/cmd_sessionstart both gate on real-environment
        # consent (scripts/setup.py reads os.environ directly, not the
        # env= dict this module's own state functions take), so the CLI
        # path needs a consented ~/.brotherme/config.json under the same
        # HOME this case patches into os.environ.
        _write_consented_config(self.tmp.name)

    def run_cli(self, argv, envelope):
        out = io.StringIO()
        real_in, real_out = sys.stdin, sys.stdout
        sys.stdin, sys.stdout = io.StringIO(json.dumps(envelope)), out
        try:
            with unittest.mock.patch.dict(os.environ, self.env):
                code = self.canary.main(argv)
        finally:
            sys.stdin, sys.stdout = real_in, real_out
        return code, out.getvalue()


class PostSkillCliTests(CliCase):

    def envelope(self, skill="brothermode:start", session_id="sess-1",
                tool_name="Skill"):
        return {"tool_name": tool_name,
                "tool_input": {"skill": skill},
                "session_id": session_id,
                "cwd": "/proj"}

    def test_first_call_prints_banner(self):
        code, out = self.run_cli(["postskill"], self.envelope())
        self.assertEqual(code, 0)
        self.assertIn("This canary recorded BrotherMode session 1", out)
        self.assertIn("not a guarantee of current activity", out)

    def test_activation_count_carries_across_distinct_sessions(self):
        self.run_cli(["postskill"], self.envelope(session_id="sess-1"))
        code, out = self.run_cli(
            ["postskill"], self.envelope(session_id="sess-2"))
        self.assertEqual(code, 0)
        self.assertIn("This canary recorded BrotherMode session 2", out)

    def test_second_call_same_session_does_not_reprint(self):
        self.run_cli(["postskill"], self.envelope())
        code, out = self.run_cli(["postskill"], self.envelope())
        self.assertEqual(code, 0)
        self.assertEqual(out, "")

    def test_non_skill_tool_name_is_silent_and_writes_nothing(self):
        code, out = self.run_cli(
            ["postskill"], self.envelope(tool_name="Bash"))
        self.assertEqual(code, 0)
        self.assertEqual(out, "")
        self.assertIsNone(self.canary.read_state(self.env))

    def test_non_brother_skill_is_silent_and_writes_nothing(self):
        code, out = self.run_cli(
            ["postskill"], self.envelope(skill="some-other-plugin:thing"))
        self.assertEqual(code, 0)
        self.assertEqual(out, "")
        self.assertIsNone(self.canary.read_state(self.env))

    def test_no_consent_is_silent_and_writes_nothing(self):
        os.remove(os.path.join(self.tmp.name, ".brotherme", "config.json"))
        code, out = self.run_cli(["postskill"], self.envelope())
        self.assertEqual(code, 0)
        self.assertEqual(out, "")
        self.assertIsNone(self.canary.read_state(self.env))


class SessionStartCliTests(CliCase):

    def write_state(self, last_seen, plugin_version="9.9.9",
                    session_id="sess-1", activation_count=7):
        state = {"active": True, "activated_at": last_seen,
                 "last_seen": last_seen, "session_id": session_id,
                 "cwd": "/proj", "plugin_version": plugin_version,
                 "skill": "brothermode:start"}
        if activation_count is not None:
            state["activation_count"] = activation_count
        self.canary._atomic_write(self.canary._state_path(self.env), state)

    def test_no_state_file_prints_nothing(self):
        code, out = self.run_cli(["sessionstart"], {})
        self.assertEqual(code, 0)
        self.assertEqual(out, "")

    def test_stale_state_prints_stale_line(self):
        old = datetime.now(timezone.utc) - timedelta(days=2)
        self.write_state(_iso(old))
        code, out = self.run_cli(["sessionstart"], {})
        self.assertEqual(code, 0)
        self.assertIn("Old BrotherMode state found", out)
        self.assertIn("run /brother to reactivate", out)

    def test_resume_state_prints_resume_line_with_age(self):
        recent = datetime.now(timezone.utc) - timedelta(hours=2)
        with unittest.mock.patch.object(
                self.canary, "_installed_version", return_value="9.9.9"):
            self.write_state(_iso(recent))
            code, out = self.run_cli(["sessionstart"], {})
        self.assertEqual(code, 0)
        self.assertIn("BrotherMode was last confirmed active 2h ago", out)
        self.assertIn("this canary has recorded 7 sessions lifetime", out)
        self.assertIn("please recheck before relying on it", out)

    def test_resume_line_falls_back_without_invented_count(self):
        # An older state file written before activation_count existed:
        # no invented number, the original wording stands.
        recent = datetime.now(timezone.utc) - timedelta(hours=2)
        with unittest.mock.patch.object(
                self.canary, "_installed_version", return_value="9.9.9"):
            self.write_state(_iso(recent), activation_count=None)
            code, out = self.run_cli(["sessionstart"], {})
        self.assertEqual(code, 0)
        self.assertIn("BrotherMode was last confirmed active 2h ago, "
                       "please recheck before relying on it", out)
        self.assertNotIn("lifetime", out)

    def test_no_consent_is_silent_even_with_stale_state(self):
        old = datetime.now(timezone.utc) - timedelta(days=2)
        self.write_state(_iso(old))
        os.remove(os.path.join(self.tmp.name, ".brotherme", "config.json"))
        code, out = self.run_cli(["sessionstart"], {})
        self.assertEqual(code, 0)
        self.assertEqual(out, "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
