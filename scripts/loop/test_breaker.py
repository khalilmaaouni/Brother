#!/usr/bin/env python3
"""Tests for scripts/loop/breaker.py.

Run: python3 -B scripts/loop/test_breaker.py
"""
import datetime as _dt
import fcntl
import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import breaker  # noqa: E402


def _rmtree(path):
    try:
        names = os.listdir(path)
    except OSError:
        return
    for name in names:
        p = os.path.join(path, name)
        if os.path.isdir(p) and not os.path.islink(p):
            _rmtree(p)
        else:
            try:
                os.remove(p)
            except OSError:
                pass  # sbe: allow-silent test temp tree cleanup; a leftover file changes no test verdict
    try:
        os.rmdir(path)
    except OSError:
        pass  # sbe: allow-silent test temp tree cleanup; a leftover directory changes no test verdict


_ENV_KEYS = ("BROTHER_OR_STATE_ROOT", "BROTHER_BREAKER", "USER")


class _Rooted(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="breaker-test-")
        self._saved = dict((k, os.environ.get(k)) for k in _ENV_KEYS)
        os.environ["BROTHER_OR_STATE_ROOT"] = self.root
        os.environ["BROTHER_BREAKER"] = "on"
        os.environ["USER"] = "tester"
        breaker._MODE_WARNED[:] = []

    def tearDown(self):
        for k in _ENV_KEYS:
            v = self._saved.get(k)
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        breaker._MODE_WARNED[:] = []
        _rmtree(self.root)

    def read_state(self):
        with open(os.path.join(self.root, "breakers.json")) as f:
            return json.load(f)

    def read_events(self):
        path = os.path.join(self.root, "breaker-events.jsonl")
        if not os.path.isfile(path):
            return []
        with open(path) as f:
            return [json.loads(line) for line in f if line.strip()]


class BreakerStateTest(_Rooted):
    def test_missing_file_is_created_closed(self):
        state, reason = breaker.admit(["bridge:tester:m"], "c1")
        self.assertEqual(state, "CLOSED")
        self.assertIsNone(reason)
        self.assertTrue(os.path.isfile(os.path.join(self.root, "breakers.json")))

    def test_two_limit_do_not_trip(self):
        k = breaker.key("claude", "default", "opus55", "LIMIT")
        breaker.record(k, "LIMIT", "c1")
        breaker.record(k, "LIMIT", "c2")
        state, _ = breaker.admit([k], "c3")
        self.assertEqual(state, "CLOSED")

    def test_three_limit_trip(self):
        k = breaker.key("claude", "default", "opus55", "LIMIT")
        for i in range(3):
            breaker.record(k, "LIMIT", "c-%d" % i)
        state, reason = breaker.admit([k], "c4")
        self.assertEqual(state, "OPEN")
        self.assertIsNotNone(reason)
        self.assertIn("capacity:", reason)

    def test_three_malformed_trip(self):
        k = breaker.key("bridge", "tester", "m", "MALFORMED")
        for i in range(3):
            breaker.record(k, "MALFORMED", "c-%d" % i)
        state, _ = breaker.admit([k], "c4")
        self.assertEqual(state, "OPEN")

    def test_same_call_id_counted_once(self):
        k = breaker.key("bridge", "tester", "m", "LIMIT")
        breaker.record(k, "LIMIT", "same")
        breaker.record(k, "LIMIT", "same")
        rec = self.read_state()["keys"][k]
        self.assertEqual(rec["streak"], 1)

    def test_limit_on_one_model_leaves_sibling_closed(self):
        k_opus = breaker.key("claude", "default", "opus55", "LIMIT")
        k_sonnet = breaker.key("claude", "default", "sonnet", "LIMIT")
        self.assertEqual(k_opus, "claude:default:opus55")
        self.assertEqual(k_sonnet, "claude:default:sonnet")
        self.assertNotEqual(k_opus, k_sonnet)
        for i in range(3):
            breaker.record(k_opus, "LIMIT", "c-%d" % i)
        state, _ = breaker.admit([k_sonnet], "c-last")
        self.assertEqual(state, "CLOSED")

    def test_auth_opens_the_account(self):
        auth_key = breaker.key("bridge", "tester", "m1", "AUTH")
        self.assertEqual(auth_key, "bridge:tester")
        for i in range(3):
            breaker.record(auth_key, "AUTH", "c-%d" % i)
        model_key = breaker.key("bridge", "tester", "m2", "LIMIT")
        state, _ = breaker.admit([model_key, auth_key], "c-last")
        self.assertEqual(state, "OPEN")

    def test_shadow_trip_leaves_seat_closed(self):
        seat = breaker.key("bridge", "tester", "m", "LIMIT", False)
        shadow = breaker.key("bridge", "tester", "m", "LIMIT", True)
        self.assertEqual(seat, "bridge:tester:m")
        self.assertEqual(shadow, "bridge:tester:m:shadow")
        self.assertNotEqual(shadow, seat)
        for i in range(3):
            breaker.record(shadow, "LIMIT", "c-%d" % i)
        state, _ = breaker.admit([seat], "c-last")
        self.assertEqual(state, "CLOSED")

    def test_keys_for_returns_pair(self):
        pair = breaker.keys_for("claude", "default", "opus55")
        self.assertEqual(pair, ["claude:default:opus55", "claude:default"])

    def test_corrupt_file_quarantined(self):
        with open(os.path.join(self.root, "breakers.json"), "w") as f:
            f.write("not json")
        state, _ = breaker.admit(["bridge:tester:m"], "c1")
        self.assertEqual(state, "CLOSED")
        names = os.listdir(self.root)
        self.assertTrue(any(n.startswith("breakers.corrupt-") for n in names))
        self.assertTrue(os.path.isfile(os.path.join(self.root, "breakers.json")))
        events = self.read_events()
        self.assertTrue(any(r.get("event") == "quarantine" for r in events))

    def test_truncated_file_quarantined(self):
        with open(os.path.join(self.root, "breakers.json"), "w") as f:
            f.write('{"schema": "brother-breaker-v1", "keys": {')
        state, _ = breaker.admit(["bridge:tester:m"], "c1")
        self.assertEqual(state, "CLOSED")
        names = os.listdir(self.root)
        self.assertTrue(any(n.startswith("breakers.corrupt-") for n in names))

    def test_wrong_schema_quarantined(self):
        with open(os.path.join(self.root, "breakers.json"), "w") as f:
            json.dump({"schema": "other", "keys": {}}, f)
        state, _ = breaker.admit(["bridge:tester:m"], "c1")
        self.assertEqual(state, "CLOSED")
        names = os.listdir(self.root)
        self.assertTrue(any(n.startswith("breakers.corrupt-") for n in names))

    def test_unreadable_is_nodata(self):
        path = os.path.join(self.root, "breakers.json")
        os.makedirs(path)
        with self.assertRaises(breaker.BreakerNoData):
            breaker.admit(["bridge:tester:m"], "c1")

    @unittest.skipIf(hasattr(os, "getuid") and os.getuid() == 0,
                     "root reads a mode 000 file")
    def test_unreadable_mode_000_is_nodata(self):
        path = os.path.join(self.root, "breakers.json")
        with open(path, "w") as f:
            f.write("{}")
        os.chmod(path, 0)
        try:
            with self.assertRaises(breaker.BreakerNoData):
                breaker.admit(["bridge:tester:m"], "c1")
        finally:
            os.chmod(path, 0o600)

    def test_half_open_closes_on_answered(self):
        k = breaker.key("bridge", "tester", "m", "LIMIT")
        t0 = 1000000.0
        for i in range(3):
            breaker.record(k, "LIMIT", "c-%d" % i, now=t0)
        s1, _ = breaker.admit([k], "probe", now=t0 + 4000.0)
        self.assertEqual(s1, "HALF_OPEN")
        breaker.record(k, "ANSWERED", "probe", now=t0 + 4001.0)
        s2, _ = breaker.admit([k], "after", now=t0 + 4002.0)
        self.assertEqual(s2, "CLOSED")

    def test_half_open_reopens_at_double_wait(self):
        k = breaker.key("bridge", "tester", "m", "LIMIT")
        t0 = 1000000.0
        for i in range(3):
            breaker.record(k, "LIMIT", "c-%d" % i, now=t0)
        rec = self.read_state()["keys"][k]
        self.assertEqual(rec["state"], "open")
        self.assertAlmostEqual(rec["wait_s"], 300.0, delta=1.0)
        s1, _ = breaker.admit([k], "probe", now=t0 + 301.0)
        self.assertEqual(s1, "HALF_OPEN")
        breaker.record(k, "LIMIT", "probe-again", now=t0 + 302.0)
        rec2 = self.read_state()["keys"][k]
        self.assertEqual(rec2["state"], "open")
        self.assertAlmostEqual(rec2["wait_s"], 600.0, delta=1.0)

    def test_one_half_open_claim(self):
        k = breaker.key("bridge", "tester", "m", "LIMIT")
        t0 = 1000000.0
        for i in range(3):
            breaker.record(k, "LIMIT", "c-%d" % i, now=t0)
        s1, _ = breaker.admit([k], "first", now=t0 + 4000.0)
        s2, reason = breaker.admit([k], "second", now=t0 + 4000.0)
        self.assertEqual(s1, "HALF_OPEN")
        self.assertEqual(s2, "OPEN")
        self.assertIn("capacity:", reason)

    def test_dead_claim_released(self):
        k = breaker.key("bridge", "tester", "m", "LIMIT")
        t0 = 1000000.0
        for i in range(3):
            breaker.record(k, "LIMIT", "c-%d" % i, now=t0)
        breaker.admit([k], "first", now=t0 + 4000.0, ttl_s=10.0)
        s2, _ = breaker.admit([k], "second", now=t0 + 4020.0)
        self.assertEqual(s2, "HALF_OPEN")

    def test_same_claimant_re_admitted(self):
        k = breaker.key("bridge", "tester", "m", "LIMIT")
        t0 = 1000000.0
        for i in range(3):
            breaker.record(k, "LIMIT", "c-%d" % i, now=t0)
        s1, _ = breaker.admit([k], "first", now=t0 + 4000.0)
        s2, _ = breaker.admit([k], "first", now=t0 + 4001.0)
        self.assertEqual(s1, "HALF_OPEN")
        self.assertEqual(s2, "HALF_OPEN")

    def test_stale_streak_restarts(self):
        k = breaker.key("bridge", "tester", "m", "LIMIT")
        t0 = 1000000.0
        breaker.record(k, "LIMIT", "c1", now=t0)
        breaker.record(k, "LIMIT", "c2", now=t0 + 700.0)
        self.assertEqual(self.read_state()["keys"][k]["streak"], 1)

    def test_parsed_reset_wins(self):
        k = breaker.key("bridge", "tester", "m", "LIMIT")
        t0 = 1000000.0
        future = t0 + 1800.0
        stamp = _dt.datetime.fromtimestamp(
            future, tz=_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        target = breaker.parse_reset(stamp, now=t0)
        self.assertIsNotNone(target)
        self.assertAlmostEqual(target, future, delta=2.0)
        for i in range(3):
            breaker.record(k, "LIMIT", "c-%d" % i, detail="reset " + stamp, now=t0)
        rec = self.read_state()["keys"][k]
        self.assertAlmostEqual(rec["until"], target, delta=2.0)

    def test_absurd_reset_ignored(self):
        t0 = time.time()
        self.assertIsNone(breaker.parse_reset("2099-01-01T00:00:00+00:00", now=t0))
        self.assertIsNone(breaker.parse_reset("no offset here", now=t0))

    def test_unknown_status_refused(self):
        with self.assertRaises(ValueError):
            breaker.record("bridge:tester:m", "BOGUS", "c1")

    def test_answer_on_closed_key_is_noop(self):
        k = breaker.key("bridge", "tester", "m", "LIMIT")
        breaker.record(k, "ANSWERED", "c1")
        rec = self.read_state()["keys"][k]
        self.assertEqual(rec["streak"], 0)
        self.assertEqual(rec["state"], "closed")

    def test_open_keys_lists_open(self):
        k = breaker.key("bridge", "tester", "m", "LIMIT")
        for i in range(3):
            breaker.record(k, "LIMIT", "c-%d" % i)
        self.assertIn(k, breaker.open_keys())

    def test_open_keys_at_time(self):
        k = breaker.key("bridge", "tester", "m", "LIMIT")
        t0 = 1000000.0
        for i in range(3):
            breaker.record(k, "LIMIT", "c-%d" % i, now=t0)
        self.assertIn(k, breaker.open_keys(now=t0 + 1.0))

    def test_unknown_mode_is_off_and_said(self):
        breaker._MODE_WARNED[:] = []
        saved = os.environ.get("BROTHER_BREAKER")
        os.environ["BROTHER_BREAKER"] = "yes"
        buf = io.StringIO()
        old_err = sys.stderr
        sys.stderr = buf
        try:
            m = breaker.mode()
        finally:
            sys.stderr = old_err
            if saved is None:
                os.environ.pop("BROTHER_BREAKER", None)
            else:
                os.environ["BROTHER_BREAKER"] = saved
        self.assertEqual(m, "off")
        self.assertIn("NO-DATA", buf.getvalue())

    def test_mode_on_and_off(self):
        self.assertEqual(breaker.mode({"BROTHER_BREAKER": "on"}), "on")
        self.assertEqual(breaker.mode({"BROTHER_BREAKER": "off"}), "off")
        self.assertEqual(breaker.mode({"BROTHER_BREAKER": "  ON  "}), "on")
        self.assertEqual(breaker.mode({"BROTHER_BREAKER": ""}), "off")
        self.assertEqual(breaker.mode({}), "off")

    def test_unrecognised_body_is_malformed(self):
        self.assertEqual(breaker.classify_bridge(1, "", "some weird failure"),
                         "MALFORMED")
        self.assertEqual(breaker.classify_bridge(1, "", "boom"), "MALFORMED")

    def test_classify_bridge_shapes(self):
        self.assertEqual(breaker.classify_bridge(0, "an answer", ""), "ANSWERED")
        self.assertEqual(breaker.classify_bridge(0, "", ""), "EMPTY")
        self.assertEqual(breaker.classify_bridge(1, "", "HTTP 429 from OpenRouter"), "LIMIT")
        self.assertEqual(breaker.classify_bridge(1, "", "HTTP 503"), "OVERLOAD")
        self.assertEqual(breaker.classify_bridge(1, "", "HTTP 401"), "AUTH")
        self.assertEqual(breaker.classify_bridge(1, "", "NO-DATA: no valid key"), "AUTH")
        self.assertEqual(breaker.classify_bridge(1, "", "NO-DATA: empty answer from x"), "EMPTY")
        self.assertEqual(breaker.classify_bridge(1, "", "no answer from x within 5"),
                         "TRANSPORT_DOWN")

    def test_classify_cli_shapes(self):
        self.assertEqual(breaker.classify_cli("claude", 0, "body", "", {}), "ANSWERED")
        self.assertEqual(breaker.classify_cli("claude", 0, "", "", {}), "EMPTY")
        self.assertEqual(breaker.classify_cli("claude", 0, "body", "", {"is_error": True}),
                         "MALFORMED")
        self.assertEqual(
            breaker.classify_cli("claude", 0, "body", "", {"stop_reason": "refusal"}),
            "PROVIDER_REFUSED")
        self.assertEqual(breaker.classify_cli("codex", 0, "body", "", None), "ANSWERED")
        self.assertEqual(breaker.classify_cli("codex", 1, "body", "boom", None), "MALFORMED")

    def test_two_threads_400_records_exact(self):
        problems = []

        def worker(tag):
            try:
                for i in range(200):
                    breaker.record(
                        breaker.key("bridge", "tester", "thread-%d-%d" % (tag, i), "LIMIT"),
                        "LIMIT", "c-%d-%d" % (tag, i))
            except Exception as exc:
                problems.append(exc)

        threads = [threading.Thread(target=worker, args=(0,)),
                   threading.Thread(target=worker, args=(1,))]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        self.assertEqual(problems, [])
        rec = self.read_state()
        self.assertEqual(len(rec["keys"]), 400)

    def test_lock_held_is_nodata(self):
        lock = os.path.join(self.root, "breakers.lock")
        fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        saved = breaker.LOCK_WAIT_S
        breaker.LOCK_WAIT_S = 0.2
        try:
            with self.assertRaises(breaker.BreakerNoData):
                breaker.admit(["bridge:tester:m"], "c1")
        finally:
            breaker.LOCK_WAIT_S = saved
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def test_summary_off_reads_off(self):
        lines, nodata = breaker.summary(0.0, {"BROTHER_BREAKER": "off"})
        self.assertEqual(lines, ["BREAKER off"])
        self.assertIsNone(nodata)

    def test_summary_closed_when_no_open_keys(self):
        lines, nodata = breaker.summary(0.0)
        self.assertIn("BREAKER closed", lines)
        self.assertIsNone(nodata)

    def test_summary_open_key(self):
        k = breaker.key("bridge", "tester", "m", "LIMIT")
        for i in range(3):
            breaker.record(k, "LIMIT", "c-%d" % i)
        lines, _ = breaker.summary(0.0)
        self.assertTrue(any(line.startswith("BREAKER " + k + " open until ")
                            for line in lines))

    def test_main_selftest(self):
        buf = io.StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            rc = breaker.main(["--selftest"])
        finally:
            sys.stdout = old
        self.assertEqual(rc, 0)
        self.assertIn("selftest:", buf.getvalue())

    def test_main_summary_verb(self):
        buf = io.StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            rc = breaker.main(["summary"])
        finally:
            sys.stdout = old
        self.assertEqual(rc, 0)
        self.assertIn("BREAKER", buf.getvalue())

    def test_main_usage_empty(self):
        buf = io.StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            rc = breaker.main([])
        finally:
            sys.stdout = old
        self.assertEqual(rc, 2)
        self.assertIn("usage", buf.getvalue())

    def test_selftest_body_ok(self):
        self.assertEqual(breaker.selftest(), 0)


class HostileArgs(_Rooted):
    def assert_refuses(self, fn, *args, **kwargs):
        try:
            fn(*args, **kwargs)
        except ValueError:
            return
        except Exception as exc:
            self.fail("expected ValueError, got %s: %s" % (type(exc).__name__, exc))
        self.fail("expected ValueError, call returned normally")

    def test_state_root_refuses_bad_env(self):
        for v in (True, "x", [], 1, b"x", object()):
            self.assert_refuses(breaker.state_root, v)

    def test_state_root_default(self):
        default = os.path.expanduser("~/.claude/brother-or-dispatch-state")
        self.assertEqual(breaker.state_root({}), default)
        self.assertEqual(breaker.state_root({"BROTHER_OR_STATE_ROOT": ""}), default)
        self.assertEqual(breaker.state_root({"BROTHER_OR_STATE_ROOT": "/tmp/x"}), "/tmp/x")

    def test_state_root_refuses_bad_value(self):
        for v in (True, 1, [], b"x", object()):
            self.assert_refuses(breaker.state_root, {"BROTHER_OR_STATE_ROOT": v})

    def test_mode_refuses_bad_env(self):
        for v in (True, "x", [], 1, b"x", object()):
            self.assert_refuses(breaker.mode, v)

    def test_mode_refuses_bad_value(self):
        for v in (True, 1, [], b"on", object()):
            self.assert_refuses(breaker.mode, {"BROTHER_BREAKER": v})

    def test_account_of_refuses_bad_transport(self):
        for v in (None, True, "", "nope", b"bridge", [], {}, 1, object()):
            self.assert_refuses(breaker.account_of, v, [])

    def test_account_of_refuses_bad_argv(self):
        for v in (None, True, "argv", b"argv", 1, {}, object(), float("nan")):
            self.assert_refuses(breaker.account_of, "bridge", v)

    def test_account_of_refuses_non_string_member(self):
        for v in (None, True, 1, b"x", [], {}, object()):
            self.assert_refuses(breaker.account_of, "bridge", [v])

    def test_account_of_refuses_bad_env(self):
        self.assert_refuses(breaker.account_of, "bridge", [], True)
        self.assert_refuses(breaker.account_of, "bridge", [], "x")

    def test_account_of_defaults(self):
        self.assertEqual(breaker.account_of("bridge", [], {}), "default")
        self.assertEqual(breaker.account_of("claude", [], {}), "default")
        self.assertEqual(breaker.account_of("codex", [], {}), "default")
        self.assertEqual(breaker.account_of("bridge", ["--account", "alice"], {}), "alice")
        self.assertEqual(breaker.account_of("bridge", ["--account=bob"], {}), "bob")
        self.assertEqual(breaker.account_of("bridge", [], {"USER": "carol"}), "carol")

    def test_account_of_refuses_bad_account_value(self):
        for v in ("bad name", "under_score!", "x" * 65):
            self.assert_refuses(breaker.account_of, "bridge", ["--account", v])

    def test_key_refuses_bad_transport(self):
        for v in (None, True, "", "nope", b"bridge", [], {}, 1, object()):
            self.assert_refuses(breaker.key, v, "u", "m", "LIMIT")

    def test_key_refuses_bad_account(self):
        for v in (None, True, "", "bad name", b"u", [], {}, 1, object()):
            self.assert_refuses(breaker.key, "bridge", v, "m", "LIMIT")

    def test_key_refuses_bad_model(self):
        for v in (None, True, "", b"m", [], {}, 1, object()):
            self.assert_refuses(breaker.key, "bridge", "u", v, "LIMIT")

    def test_key_refuses_shadow_model_name(self):
        self.assert_refuses(breaker.key, "bridge", "u", "shadow", "LIMIT")

    def test_key_refuses_bad_status(self):
        for v in (None, True, "", "BOGUS", "ANSWERED", b"LIMIT", [], {}, 1):
            self.assert_refuses(breaker.key, "bridge", "u", "m", v)

    def test_key_refuses_bad_shadow(self):
        for v in (None, "yes", 1, [], {}, b"x"):
            self.assert_refuses(breaker.key, "bridge", "u", "m", "LIMIT", v)

    def test_keys_for_refuses(self):
        self.assert_refuses(breaker.keys_for, None, "u", "m")
        self.assert_refuses(breaker.keys_for, "bridge", None, "m")
        self.assert_refuses(breaker.keys_for, "bridge", "u", None)

    def test_family_refuses_bad_row(self):
        for v in (None, True, "", b"x", [], 1, object()):
            self.assert_refuses(breaker.family, v)

    def test_family_of_row(self):
        self.assertEqual(breaker.family({"transport": "claude"}), "anthropic")
        self.assertEqual(breaker.family({"transport": "codex"}), "openai")
        self.assertEqual(breaker.family({"transport": "bridge", "id": "deepseek/x"}),
                         "deepseek")
        self.assertIsNone(breaker.family({"transport": "bridge"}))
        self.assertIsNone(breaker.family({"transport": "bridge", "id": "noslash"}))
        self.assertIsNone(breaker.family({}))
        self.assertIsNone(breaker.family({"transport": "nope"}))

    def test_parse_reset_refuses_bad_text(self):
        for v in (None, True, 1, b"x", [], {}, object()):
            self.assert_refuses(breaker.parse_reset, v)

    def test_parse_reset_refuses_bad_now(self):
        for v in (True, "x", b"1", [], {}, object()):
            self.assert_refuses(breaker.parse_reset, "2026-01-01T00:00:00+00:00", v)

    def test_parse_reset_reads_iso(self):
        t0 = 1000000.0
        val = breaker.parse_reset("1970-01-13T00:00:00+00:00", now=t0)
        self.assertIsNotNone(val)
        self.assertGreater(val, t0)

    def test_classify_bridge_refuses_bad_returncode(self):
        for v in (None, True, "0", b"0", 1.5, [], {}, object(), float("nan")):
            self.assert_refuses(breaker.classify_bridge, v, "", "")

    def test_classify_bridge_refuses_bad_stdout(self):
        for v in (None, True, 1, b"out", [], {}, object()):
            self.assert_refuses(breaker.classify_bridge, 0, v, "")

    def test_classify_bridge_refuses_bad_stderr(self):
        for v in (None, True, 1, b"err", [], {}, object()):
            self.assert_refuses(breaker.classify_bridge, 0, "", v)

    def test_classify_cli_refuses_bad_transport(self):
        for v in (None, True, "", "nope", b"claude", "bridge", [], {}):
            self.assert_refuses(breaker.classify_cli, v, 0, "", "", None)

    def test_classify_cli_refuses_bad_returncode(self):
        for v in (None, True, "0", b"0", 1.5, [], {}):
            self.assert_refuses(breaker.classify_cli, "claude", v, "", "", None)

    def test_classify_cli_refuses_bad_stdout(self):
        for v in (None, True, 1, b"out", [], {}):
            self.assert_refuses(breaker.classify_cli, "claude", 0, v, "", None)

    def test_classify_cli_refuses_bad_stderr(self):
        for v in (None, True, 1, b"err", [], {}):
            self.assert_refuses(breaker.classify_cli, "claude", 0, "", v, None)

    def test_classify_cli_refuses_bad_claude_doc(self):
        for v in (True, "doc", 1, b"doc", [], object()):
            self.assert_refuses(breaker.classify_cli, "claude", 0, "", "", v)

    def test_admit_refuses_bad_key_list(self):
        for v in (None, True, "k", b"k", 1, {}, object(), float("nan")):
            self.assert_refuses(breaker.admit, v, "c1")

    def test_admit_refuses_bad_key_member(self):
        for v in (None, True, 1, b"k", [], {}, object()):
            self.assert_refuses(breaker.admit, [v], "c1")

    def test_admit_refuses_bad_call_id(self):
        for v in (None, True, "", b"c", 1, [], {}, object()):
            self.assert_refuses(breaker.admit, ["bridge:tester:m"], v)

    def test_admit_refuses_bad_ttl(self):
        for v in (None, True, "360", b"360", [], {}, object(), float("nan")):
            self.assert_refuses(breaker.admit, ["bridge:tester:m"], "c1", v)

    def test_admit_refuses_bad_now(self):
        for v in (True, "x", b"1", [], {}, object()):
            self.assert_refuses(breaker.admit, ["bridge:tester:m"], "c1", 360.0, v)

    def test_record_refuses_bad_key(self):
        for v in (None, True, "", b"k", 1, [], {}):
            self.assert_refuses(breaker.record, v, "LIMIT", "c1")

    def test_record_refuses_bad_status(self):
        for v in (None, True, "", "BOGUS", b"LIMIT", 1, [], {}):
            self.assert_refuses(breaker.record, "bridge:tester:m", v, "c1")

    def test_record_refuses_bad_call_id(self):
        for v in (None, True, "", b"c", 1, [], {}):
            self.assert_refuses(breaker.record, "bridge:tester:m", "LIMIT", v)

    def test_record_refuses_bad_detail(self):
        for v in (None, True, b"d", 1, [], {}):
            self.assert_refuses(breaker.record, "bridge:tester:m", "LIMIT", "c1", v)

    def test_record_refuses_bad_now(self):
        for v in (True, "x", b"1", [], {}, object()):
            self.assert_refuses(breaker.record, "bridge:tester:m", "LIMIT", "c1", "", v)

    def test_open_keys_refuses_bad_now(self):
        for v in (True, "x", b"1", [], {}, object()):
            self.assert_refuses(breaker.open_keys, now=v)

    def test_summary_refuses_bad_run_start(self):
        for v in (None, True, "0", b"0", [], {}, object(), float("nan")):
            self.assert_refuses(breaker.summary, v)

    def test_summary_refuses_bad_env(self):
        for v in (True, "x", [], 1, b"x", object()):
            self.assert_refuses(breaker.summary, 0.0, v)

    def test_main_refuses_bad_argv(self):
        for v in (True, 0, 1, 1.5, "x", b"x", {}, object()):
            self.assert_refuses(breaker.main, v)

    def test_main_refuses_non_string_member(self):
        for v in (None, True, 1, b"x", [], {}, object()):
            self.assert_refuses(breaker.main, [v])

    def test_main_none_and_empty(self):
        buf = io.StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            self.assertEqual(breaker.main(None), 2)
            self.assertEqual(breaker.main([]), 2)
        finally:
            sys.stdout = old
        self.assertEqual(buf.getvalue().count("usage"), 2)


class ALostEventIsSaidOutLoud(unittest.TestCase):
    """2026-09-30: _event swallowed every OSError, so a trip or close vanished from the audit log."""

    def test_an_unwritable_event_log_names_the_event_on_stderr(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "breaker-events.jsonl"))  # a directory: only the append open fails
            err = io.StringIO()
            saved, sys.stderr = sys.stderr, err
            try:
                breaker._event(d, {"event": "trip", "key": "k"})
            finally:
                sys.stderr = saved
            self.assertIn("NOT recorded", err.getvalue())
            self.assertIn("trip", err.getvalue())

    def test_a_written_but_unsynced_event_is_said_on_stderr(self):
        """Isolates the fsync guard: the append succeeds, only os.fsync refuses."""
        real_fsync = os.fsync

        def refuse(fd):
            raise OSError("fsync refused (fixture)")
        with tempfile.TemporaryDirectory() as d:
            err = io.StringIO()
            saved, sys.stderr = sys.stderr, err
            os.fsync = refuse
            try:
                breaker._event(d, {"event": "close", "key": "k"})
            finally:
                os.fsync = real_fsync
                sys.stderr = saved
            self.assertIn("not synced", err.getvalue())
            self.assertIn("close", err.getvalue())
            with open(os.path.join(d, "breaker-events.jsonl")) as f:
                self.assertIn('"close"', f.read(), "the row itself was still appended")


class TheAlarmScanNeverReadsQuietlyAsNoAlarm(_Rooted):
    """2026-09-30: summary() dropped a quarantined file it could not stat, and read an unlistable root as no alarm."""

    def test_a_quarantined_file_whose_age_cannot_be_read_is_still_an_alarm(self):
        breaker.admit(["bridge:tester:m"], "c1")
        with open(os.path.join(self.root, "breakers.corrupt-20260101T000000Z.json"), "w") as f:
            f.write("{")
        real = os.path.getmtime

        def unreadable_age(p):
            if "breakers.corrupt-" in os.path.basename(p):
                raise OSError("stat refused (fixture)")
            return real(p)
        os.path.getmtime = unreadable_age
        try:
            # run_start an hour ahead: a READABLE age would never alarm, so only this guard can produce the line
            lines, nodata = breaker.summary(time.time() + 3600.0)
        finally:
            os.path.getmtime = real
        self.assertTrue(any(l.startswith("BREAKER ALARM") and "age could not be read" in l for l in lines), lines)

    def test_an_alarm_scan_that_cannot_list_the_root_is_nodata_never_no_alarm(self):
        breaker.admit(["bridge:tester:m"], "c1")
        real = os.listdir
        root = os.path.realpath(self.root)

        def refuse(p):
            if os.path.realpath(p) == root:
                raise OSError("listdir refused (fixture)")
            return real(p)
        os.listdir = refuse
        try:
            lines, nodata = breaker.summary(0.0)
        finally:
            os.listdir = real
        self.assertTrue(any(l.startswith("BREAKER NO-DATA") and "could not list" in l for l in lines), lines)


if __name__ == "__main__":
    unittest.main()
