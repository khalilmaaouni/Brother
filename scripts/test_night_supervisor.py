#!/usr/bin/env python3
"""Tests for ORCH-09, scripts/night_supervisor.py: the durable night
supervisor. Every scenario runs offline against a temp directory: no real
process is ever spawned (spawn is always a fake, recording its calls), no
real sleep drives any timing decision (every clock is the injected `now`
float), and the one place a real cross-process lock is exercised
(concurrent-start) shrinks its own timeout first so the wait stays under a
tenth of a second.

THE BAD STATE EACH GROUP IS BUILT TO CATCH, named up front because a test
that cannot fail is not evidence (worker contract, rule 5):
  - liveness: a supervisor that trusts only one signal restarts a healthy
    but momentarily slow process, or leaves a wedged one running forever.
  - restart/epoch: a supervisor that hands a restarted process the epoch
    it remembers instead of a fresh one recreates the exact stale-epoch
    wakeup this build was warned about.
  - lease boundary: a supervisor that "helpfully" takes over a scope some
    other live instance legitimately holds causes the split-brain this
    whole authority module exists to prevent.
  - crash loop: a supervisor with no bound against a process that dies on
    startup turns one bad deploy into a machine-wide resource fire.
  - state durability: a supervisor that keeps any decision-relevant fact
    only in memory loses that fact the instant it is itself restarted,
    which is exactly the failure category this unit exists to close.
"""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import claim_store  # noqa: E402
import orchestrator_authority as auth  # noqa: E402
import night_supervisor as ns  # noqa: E402

NOW = 1_800_000_000.0
FAR_FUTURE_HARD_STOP = datetime.fromtimestamp(
    NOW + 1_000_000.0, tz=timezone.utc).isoformat()


def _iso(epoch_s):
    return datetime.fromtimestamp(epoch_s, tz=timezone.utc).isoformat()


class FakeAdapter(object):
    """A liveness adapter driven entirely by the test: no file, no real
    pid check. alive and heartbeat_age are plain values or None."""

    def __init__(self, alive=True, heartbeat_age=0.0):
        self.alive = alive
        self.heartbeat_age = heartbeat_age
        self.process_alive_calls = []
        self.heartbeat_calls = []

    def process_alive(self, pid):
        self.process_alive_calls.append(pid)
        return self.alive

    def heartbeat_age_s(self, now):
        self.heartbeat_calls.append(now)
        return self.heartbeat_age


class RecordingSpawn(object):
    """Records every call; hands out sequential pids starting at 1000
    unless configured to raise."""

    def __init__(self, raise_exc=None):
        self.calls = []
        self.raise_exc = raise_exc
        self._next_pid = 1000

    def __call__(self, scope_cfg, lease):
        self.calls.append((dict(scope_cfg), lease))
        if self.raise_exc is not None:
            raise self.raise_exc
        pid = self._next_pid
        self._next_pid += 1
        return pid


def _never_called(scope_cfg, lease):
    raise AssertionError(
        "spawn() must never be called for a live orchestrator (rule 2)")


def _scope_cfg(scope="primary", orchestrator="execution", instance="primary-a",
               ttl_seconds=300, heartbeat_stale_s=60, max_restarts=3,
               backoff_base_s=10, backoff_cap_s=None, startup_grace_s=None):
    cfg = {"scope": scope, "orchestrator": orchestrator, "instance": instance,
           "ttl_seconds": ttl_seconds, "heartbeat_stale_s": heartbeat_stale_s,
           "max_restarts": max_restarts, "backoff_base_s": backoff_base_s}
    if backoff_cap_s is not None:
        cfg["backoff_cap_s"] = backoff_cap_s
    if startup_grace_s is not None:
        cfg["startup_grace_s"] = startup_grace_s
    return cfg


class Fixture(object):
    """One tmpdir with a manifest and the paths it names, so every test
    reads the same shape read_manifest() actually validates against."""

    def __init__(self, tmpdir, scopes, hard_stop=FAR_FUTURE_HARD_STOP,
                 graceful_stop_lead_s=None):
        self.tmpdir = tmpdir
        self.manifest_path = os.path.join(tmpdir, "manifest.json")
        self.authority_store = os.path.join(tmpdir, "authority.json")
        self.state_path = os.path.join(tmpdir, "state.json")
        self.journal_path = os.path.join(tmpdir, "journal.jsonl")
        manifest = {
            "run_id": "run-test", "authority_store": self.authority_store,
            "state_path": self.state_path, "journal_path": self.journal_path,
            "hard_stop": hard_stop, "scopes": scopes,
        }
        if graceful_stop_lead_s is not None:
            manifest["graceful_stop_lead_s"] = graceful_stop_lead_s
        with open(self.manifest_path, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh)

    def seed_state(self, state):
        ns._write_state(self.state_path, state)


class ManifestTests(unittest.TestCase):

    def test_missing_manifest_refuses(self):
        with self.assertRaises(ns.SupervisorRefused):
            ns.read_manifest("/nonexistent/does-not-exist.json")

    def test_corrupt_json_refuses(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "bad.json")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("{not json")
            with self.assertRaises(ns.SupervisorRefused):
                ns.read_manifest(path)

    def test_non_object_json_refuses(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "list.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump([1, 2, 3], fh)
            with self.assertRaises(ns.SupervisorRefused):
                ns.read_manifest(path)

    def test_missing_top_level_key_refuses(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "manifest.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"run_id": "x"}, fh)
            with self.assertRaises(ns.SupervisorRefused):
                ns.read_manifest(path)

    def test_scope_missing_required_key_refuses(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "manifest.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({
                    "run_id": "x", "authority_store": "a", "state_path": "s",
                    "journal_path": "j", "hard_stop": FAR_FUTURE_HARD_STOP,
                    "scopes": [{"scope": "primary"}],
                }, fh)
            with self.assertRaises(ns.SupervisorRefused):
                ns.read_manifest(path)

    def test_defaults_applied_for_optional_fields(self):
        with tempfile.TemporaryDirectory() as d:
            fx = Fixture(d, [_scope_cfg()])
            manifest = ns.read_manifest(fx.manifest_path)
            self.assertEqual(manifest["graceful_stop_lead_s"],
                              ns.DEFAULT_GRACEFUL_STOP_LEAD_S)
            self.assertEqual(manifest["scopes"][0]["startup_grace_s"],
                              ns.DEFAULT_STARTUP_GRACE_S)
            self.assertEqual(manifest["scopes"][0]["backoff_cap_s"],
                              ns.DEFAULT_BACKOFF_CAP_S)

    def test_bad_hard_stop_refuses(self):
        with tempfile.TemporaryDirectory() as d:
            fx = Fixture(d, [_scope_cfg()], hard_stop="not-a-timestamp")
            with self.assertRaises(ns.SupervisorRefused):
                ns.supervise(fx.manifest_path,
                              adapters={"primary": FakeAdapter()}, now=NOW,
                              spawn=RecordingSpawn())


class LivenessClassificationTests(unittest.TestCase):
    """Rule 1: heartbeat age AND process existence, never one alone, with
    process existence winning whenever it gives a definite answer."""

    def test_no_pid_ever_recorded_is_dead(self):
        status, _ = ns.classify_liveness(None, None, None, 60, False)
        self.assertEqual(status, "DEAD")

    def test_process_gone_is_dead_even_with_a_fresh_looking_heartbeat(self):
        # THE DISAGREEMENT CASE: heartbeat says fresh, process says gone.
        # Process existence wins: nothing can fake a process that is not
        # there, while a fresh stamp could come from clock skew or a
        # second stray writer.
        status, reason = ns.classify_liveness(111, False, 1.0, 60, False)
        self.assertEqual(status, "DEAD")
        self.assertIn("does not exist", reason)

    def test_process_alive_heartbeat_fresh_is_alive(self):
        status, _ = ns.classify_liveness(111, True, 5.0, 60, False)
        self.assertEqual(status, "ALIVE")

    def test_process_alive_heartbeat_stale_is_dead_wedged(self):
        status, reason = ns.classify_liveness(111, True, 999.0, 60, False)
        self.assertEqual(status, "DEAD")
        self.assertIn("wedged", reason)

    def test_process_unknown_heartbeat_fresh_is_alive(self):
        status, _ = ns.classify_liveness(111, None, 5.0, 60, False)
        self.assertEqual(status, "ALIVE")

    def test_process_unknown_heartbeat_stale_is_no_data_never_dead(self):
        status, _ = ns.classify_liveness(111, None, 999.0, 60, False)
        self.assertEqual(status, "NO-DATA")

    def test_process_unknown_no_heartbeat_is_no_data(self):
        status, _ = ns.classify_liveness(111, None, None, 60, False)
        self.assertEqual(status, "NO-DATA")

    def test_startup_grace_covers_missing_heartbeat(self):
        status, _ = ns.classify_liveness(111, True, None, 60, True)
        self.assertEqual(status, "ALIVE")

    def test_missing_heartbeat_past_grace_is_dead(self):
        status, _ = ns.classify_liveness(111, True, None, 60, False)
        self.assertEqual(status, "DEAD")

    def test_heartbeat_from_the_future_is_clock_skew_treated_as_alive(self):
        status, reason = ns.classify_liveness(111, True, -30.0, 60, False)
        self.assertEqual(status, "ALIVE")
        self.assertIn("clock skew", reason)


class BackoffTests(unittest.TestCase):

    def test_exponential_and_capped(self):
        self.assertEqual(ns.next_backoff_s(0, 10, 1000), 10)
        self.assertEqual(ns.next_backoff_s(1, 10, 1000), 20)
        self.assertEqual(ns.next_backoff_s(2, 10, 1000), 40)
        self.assertEqual(ns.next_backoff_s(10, 10, 1000), 1000)


class SuperviseLivenessTests(unittest.TestCase):
    """Rules 1 and 2: dead is restarted, live is never touched."""

    def test_dead_orchestrator_is_restarted(self):
        with tempfile.TemporaryDirectory() as d:
            fx = Fixture(d, [_scope_cfg()])
            spawn = RecordingSpawn()
            report = ns.supervise(
                fx.manifest_path, adapters={"primary": FakeAdapter()},
                now=NOW, spawn=spawn)
            self.assertEqual(len(report.restarted), 1)
            self.assertEqual(report.restarted[0]["scope"], "primary")
            self.assertEqual(len(spawn.calls), 1)
            lease = spawn.calls[0][1]
            self.assertEqual(lease.instance, "primary-a")
            self.assertEqual(lease.epoch, 1)

    def test_live_orchestrator_is_never_restarted(self):
        with tempfile.TemporaryDirectory() as d:
            fx = Fixture(d, [_scope_cfg()])
            fx.seed_state({"primary": {
                "instance": "primary-a", "epoch": 1, "pid": 111,
                "started_at": NOW - 5000, "restart_count": 0,
                "backoff_until": None, "down": False, "down_reason": None}})
            adapter = FakeAdapter(alive=True, heartbeat_age=5.0)
            report = ns.supervise(
                fx.manifest_path, adapters={"primary": adapter}, now=NOW,
                spawn=_never_called)
            self.assertEqual(report.alive[0]["scope"], "primary")
            self.assertEqual(report.restarted, [])


class CrashLoopTests(unittest.TestCase):

    def test_crash_loop_is_bounded_then_left_down(self):
        with tempfile.TemporaryDirectory() as d:
            fx = Fixture(d, [_scope_cfg(max_restarts=2, backoff_base_s=0)])
            adapter = FakeAdapter(alive=False)
            spawn = RecordingSpawn()
            t = NOW
            # Two restarts are allowed (max_restarts=2); a third finds the
            # scope already marked down and spawn is not called again.
            for _ in range(2):
                report = ns.supervise(
                    fx.manifest_path, adapters={"primary": adapter}, now=t,
                    spawn=spawn)
                self.assertEqual(len(report.restarted), 1)
                t += 1
            report = ns.supervise(
                fx.manifest_path, adapters={"primary": adapter}, now=t,
                spawn=spawn)
            self.assertEqual(report.restarted, [])
            self.assertEqual(len(report.down), 1)
            self.assertIn("crash-loop", report.down[0]["reason"])
            self.assertEqual(len(spawn.calls), 2, "no restart past the bound")
            # And it stays down: another tick still refuses to restart it.
            report2 = ns.supervise(
                fx.manifest_path, adapters={"primary": adapter}, now=t + 1,
                spawn=spawn)
            self.assertEqual(len(spawn.calls), 2)
            self.assertEqual(len(report2.down), 1)

    def test_backoff_defers_a_restart_before_the_bound(self):
        with tempfile.TemporaryDirectory() as d:
            fx = Fixture(d, [_scope_cfg(max_restarts=5, backoff_base_s=100)])
            adapter = FakeAdapter(alive=False)
            spawn = RecordingSpawn()
            report1 = ns.supervise(
                fx.manifest_path, adapters={"primary": adapter}, now=NOW,
                spawn=spawn)
            self.assertEqual(len(report1.restarted), 1)
            # Immediately after: still within the 100s backoff window.
            report2 = ns.supervise(
                fx.manifest_path, adapters={"primary": adapter}, now=NOW + 1,
                spawn=spawn)
            self.assertEqual(report2.restarted, [])
            self.assertEqual(len(report2.deferred), 1)
            self.assertEqual(len(spawn.calls), 1)
            # Past the backoff window: restarts again.
            report3 = ns.supervise(
                fx.manifest_path, adapters={"primary": adapter}, now=NOW + 200,
                spawn=spawn)
            self.assertEqual(len(report3.restarted), 1)
            self.assertEqual(len(spawn.calls), 2)


class HardStopTests(unittest.TestCase):

    def test_alive_near_hard_stop_requests_graceful_stop_not_a_restart(self):
        with tempfile.TemporaryDirectory() as d:
            hard_stop = _iso(NOW + 100)
            fx = Fixture(d, [_scope_cfg()], hard_stop=hard_stop,
                         graceful_stop_lead_s=900)
            fx.seed_state({"primary": {
                "instance": "primary-a", "epoch": 1, "pid": 111,
                "started_at": NOW - 5000, "restart_count": 0,
                "backoff_until": None, "down": False, "down_reason": None}})
            adapter = FakeAdapter(alive=True, heartbeat_age=5.0)
            report = ns.supervise(
                fx.manifest_path, adapters={"primary": adapter}, now=NOW,
                spawn=_never_called)
            self.assertEqual(report.graceful_stop[0]["scope"], "primary")
            self.assertEqual(report.alive, [])

    def test_dead_near_hard_stop_is_not_restarted(self):
        with tempfile.TemporaryDirectory() as d:
            hard_stop = _iso(NOW + 100)
            fx = Fixture(d, [_scope_cfg()], hard_stop=hard_stop,
                         graceful_stop_lead_s=900)
            report = ns.supervise(
                fx.manifest_path, adapters={"primary": FakeAdapter(alive=False)},
                now=NOW, spawn=_never_called)
            self.assertEqual(report.restarted, [])
            self.assertEqual(len(report.deferred), 1)
            self.assertIn("hard stop", report.deferred[0]["reason"])

    def test_hard_stop_already_passed_at_startup_behaves_like_near(self):
        with tempfile.TemporaryDirectory() as d:
            hard_stop = _iso(NOW - 3600)  # already in the past
            fx = Fixture(d, [_scope_cfg()], hard_stop=hard_stop,
                         graceful_stop_lead_s=900)
            report = ns.supervise(
                fx.manifest_path, adapters={"primary": FakeAdapter(alive=False)},
                now=NOW, spawn=_never_called)
            self.assertEqual(report.restarted, [])
            self.assertEqual(len(report.deferred), 1)


class UnreadableStateTests(unittest.TestCase):

    def test_corrupt_state_file_refuses(self):
        with tempfile.TemporaryDirectory() as d:
            fx = Fixture(d, [_scope_cfg()])
            with open(fx.state_path, "w", encoding="utf-8") as fh:
                fh.write("{not json")
            with self.assertRaises(ns.SupervisorRefused):
                ns.supervise(fx.manifest_path, adapters={"primary": FakeAdapter()},
                              now=NOW, spawn=RecordingSpawn())

    def test_corrupt_authority_store_propagates_unreadable_never_free(self):
        with tempfile.TemporaryDirectory() as d:
            fx = Fixture(d, [_scope_cfg()])
            with open(fx.authority_store, "w", encoding="utf-8") as fh:
                fh.write("{not json")
            with self.assertRaises(auth.AuthorityUnreadable):
                ns.supervise(fx.manifest_path, adapters={"primary": FakeAdapter()},
                              now=NOW, spawn=RecordingSpawn())

    def test_missing_adapter_for_a_configured_scope_refuses(self):
        with tempfile.TemporaryDirectory() as d:
            fx = Fixture(d, [_scope_cfg()])
            with self.assertRaises(ns.SupervisorRefused):
                ns.supervise(fx.manifest_path, adapters={}, now=NOW,
                              spawn=RecordingSpawn())


class LiveLeaseBoundaryTests(unittest.TestCase):
    """Rule 6: never bypass a live lease, direct raise, and the sibling
    "own lease still live, process confirmed dead" recovery path."""

    def test_attempt_restart_refuses_a_foreign_live_lease(self):
        with tempfile.TemporaryDirectory() as d:
            store = os.path.join(d, "authority.json")
            auth.acquire(store, "run-test", "primary", "someone-else",
                         "someone-else-instance", 300, now=NOW)
            with self.assertRaises(ns.SupervisorRefused):
                ns.attempt_restart(
                    store, "run-test", "primary", "primary-a", "execution",
                    300, "test", NOW + 1, RecordingSpawn(),
                    _scope_cfg())

    def test_supervise_refuses_one_scope_but_still_restarts_the_other(self):
        # Independence: a foreign-lease conflict on one scope must not
        # block judgement of a sibling scope in the same tick.
        with tempfile.TemporaryDirectory() as d:
            fx = Fixture(d, [_scope_cfg(scope="primary", instance="primary-a"),
                             _scope_cfg(scope="secondary", instance="secondary-a")])
            auth.acquire(fx.authority_store, "run-test", "primary",
                         "someone-else", "foreign-instance", 300, now=NOW)
            adapters = {"primary": FakeAdapter(alive=False),
                        "secondary": FakeAdapter(alive=False)}
            report = ns.supervise(fx.manifest_path, adapters=adapters, now=NOW,
                                   spawn=RecordingSpawn())
            self.assertEqual(len(report.refused), 1)
            self.assertEqual(report.refused[0]["scope"], "primary")
            self.assertEqual(len(report.restarted), 1)
            self.assertEqual(report.restarted[0]["scope"], "secondary")

    def test_own_live_lease_with_dead_process_is_released_then_retaken(self):
        # "a lease held by an instance with no process": our own instance's
        # lease has not hit its TTL yet, but the process is confirmed
        # dead. This must restart immediately rather than waiting out the
        # remaining TTL, and must not raise SupervisorRefused (it is our
        # own lease, not a bypass of someone else's).
        with tempfile.TemporaryDirectory() as d:
            fx = Fixture(d, [_scope_cfg(ttl_seconds=300)])
            lease = auth.acquire(fx.authority_store, "run-test", "primary",
                                  "execution", "primary-a", 300, now=NOW)
            fx.seed_state({"primary": {
                "instance": "primary-a", "epoch": lease.epoch, "pid": 111,
                "started_at": NOW - 10, "restart_count": 0,
                "backoff_until": None, "down": False, "down_reason": None}})
            spawn = RecordingSpawn()
            report = ns.supervise(
                fx.manifest_path, adapters={"primary": FakeAdapter(alive=False)},
                now=NOW + 1, spawn=spawn)
            self.assertEqual(report.refused, [])
            self.assertEqual(len(report.restarted), 1)
            self.assertEqual(report.restarted[0]["epoch"], lease.epoch + 1)

    def test_alive_process_with_no_lease_takes_no_authority_action(self):
        # "a process with no lease": liveness says ALIVE, so the
        # supervisor must not touch the authority store at all.
        with tempfile.TemporaryDirectory() as d:
            fx = Fixture(d, [_scope_cfg()])
            fx.seed_state({"primary": {
                "instance": "primary-a", "epoch": 1, "pid": 111,
                "started_at": NOW - 5000, "restart_count": 0,
                "backoff_until": None, "down": False, "down_reason": None}})
            report = ns.supervise(
                fx.manifest_path,
                adapters={"primary": FakeAdapter(alive=True, heartbeat_age=1.0)},
                now=NOW, spawn=_never_called)
            self.assertEqual(report.alive[0]["scope"], "primary")
            self.assertIsNone(auth.current(fx.authority_store, "run-test",
                                            "primary", now=NOW))


class StaleEpochTests(unittest.TestCase):
    """THE ONE THAT MATTERS: a restart under the SAME instance identity
    must mint a genuinely new epoch, and the old epoch must be refused by
    the authority module afterwards."""

    def test_restart_under_same_instance_id_refuses_the_old_epoch(self):
        with tempfile.TemporaryDirectory() as d:
            fx = Fixture(d, [_scope_cfg(instance="primary-a", ttl_seconds=60,
                                        backoff_base_s=0)])
            spawn = RecordingSpawn()
            report1 = ns.supervise(
                fx.manifest_path, adapters={"primary": FakeAdapter(alive=False)},
                now=NOW, spawn=spawn)
            first = report1.restarted[0]
            self.assertEqual(first["instance"], "primary-a")
            first_epoch = first["epoch"]
            self.assertTrue(auth.check(fx.authority_store, "run-test", "primary",
                                        "primary-a", first_epoch, now=NOW))

            # The lease expires (or is released, standing in for a stall),
            # and the SAME instance identity is restarted a second time.
            report2 = ns.supervise(
                fx.manifest_path, adapters={"primary": FakeAdapter(alive=False)},
                now=NOW + 61, spawn=spawn)
            second = report2.restarted[0]
            self.assertEqual(second["instance"], "primary-a")
            second_epoch = second["epoch"]
            self.assertNotEqual(first_epoch, second_epoch)

            # THE ASSERTION THAT MATTERS: a caller still holding the OLD
            # epoch (the process that "woke up" believing it still holds
            # the lane) is refused, even though the instance name matches.
            self.assertFalse(
                auth.check(fx.authority_store, "run-test", "primary",
                           "primary-a", first_epoch, now=NOW + 61))
            with self.assertRaises(auth.AuthorityRefused):
                auth.renew(fx.authority_store, "run-test", "primary",
                           "primary-a", first_epoch, 60, now=NOW + 61)
            # The new epoch is the one actually live.
            self.assertTrue(
                auth.check(fx.authority_store, "run-test", "primary",
                           "primary-a", second_epoch, now=NOW + 61))


class SpawnFailureTests(unittest.TestCase):

    def test_spawn_failure_releases_the_lease_and_counts_toward_the_bound(self):
        with tempfile.TemporaryDirectory() as d:
            fx = Fixture(d, [_scope_cfg(max_restarts=1, backoff_base_s=0)])
            spawn = RecordingSpawn(raise_exc=OSError("launch failed"))
            report = ns.supervise(
                fx.manifest_path, adapters={"primary": FakeAdapter(alive=False)},
                now=NOW, spawn=spawn)
            self.assertEqual(report.restarted, [])
            self.assertEqual(len(report.deferred), 1)
            self.assertIsNone(auth.current(fx.authority_store, "run-test",
                                            "primary", now=NOW))
            # That attempt already counted: the bound (max_restarts=1) is
            # now exhausted.
            report2 = ns.supervise(
                fx.manifest_path, adapters={"primary": FakeAdapter(alive=False)},
                now=NOW + 1, spawn=spawn)
            self.assertEqual(len(report2.down), 1)


class JournalTests(unittest.TestCase):

    def test_every_action_is_journalled_and_countable(self):
        with tempfile.TemporaryDirectory() as d:
            fx = Fixture(d, [_scope_cfg()])
            ns.supervise(fx.manifest_path, adapters={"primary": FakeAdapter(alive=False)},
                         now=NOW, spawn=RecordingSpawn())
            self.assertEqual(ns.count_events(fx.journal_path), 1)
            self.assertEqual(ns.count_events(fx.journal_path, action="restarted"), 1)
            self.assertEqual(ns.count_events(fx.journal_path, action="nope"), 0)

    def test_count_events_on_a_journal_that_does_not_exist_yet_is_zero(self):
        self.assertEqual(ns.count_events("/nonexistent/journal.jsonl"), 0)

    def test_malformed_journal_line_is_skipped_not_fatal(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "j.jsonl")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write('{"action": "restarted"}\n')
                fh.write("not json at all\n")
                fh.write('{"action": "restarted"}\n')
            self.assertEqual(ns.count_events(path), 2)


class StateDurabilityTests(unittest.TestCase):
    """Rule 8: the supervisor's own state is on disk, not in memory. There
    is no long-lived Python object to discard here by construction (every
    supervise() call is independently driven from the files it is handed)
    so this proves the same thing the hard way: build state with one call,
    read it back with a second, unrelated call using none of the first
    call's Python objects, and confirm the second call reaches the exact
    same judgement the first one's outcome implies it should."""

    def test_state_survives_and_drives_the_next_independent_call(self):
        with tempfile.TemporaryDirectory() as d:
            fx = Fixture(d, [_scope_cfg()])
            spawn = RecordingSpawn()
            report1 = ns.supervise(
                fx.manifest_path, adapters={"primary": FakeAdapter(alive=False)},
                now=NOW, spawn=spawn)
            pid = report1.restarted[0]["pid"]
            epoch = report1.restarted[0]["epoch"]

            # Read the state back independently, as a fresh reader would.
            reloaded = ns._read_state(fx.state_path)
            self.assertEqual(reloaded["primary"]["pid"], pid)
            self.assertEqual(reloaded["primary"]["epoch"], epoch)
            del reloaded  # discard: nothing below may depend on this object

            # A second, wholly independent supervise() call: same paths,
            # new adapter and spawn objects, no shared Python state at all.
            # The process is now reported alive; this must reach ALIVE
            # purely by having re-read pid/epoch from disk.
            report2 = ns.supervise(
                fx.manifest_path,
                adapters={"primary": FakeAdapter(alive=True, heartbeat_age=1.0)},
                now=NOW + 5, spawn=RecordingSpawn())
            self.assertEqual(report2.alive[0]["scope"], "primary")
            self.assertEqual(report2.restarted, [])


class MultiScopeIndependenceTests(unittest.TestCase):

    def test_no_scopes_configured_is_a_quiet_no_op(self):
        with tempfile.TemporaryDirectory() as d:
            fx = Fixture(d, [])
            report = ns.supervise(fx.manifest_path, adapters={}, now=NOW,
                                   spawn=_never_called)
            self.assertEqual(report.as_dict(), {
                "restarted": [], "alive": [], "deferred": [], "down": [],
                "refused": [], "graceful_stop": [], "no_data": []})

    def test_both_dead_at_once_both_restart(self):
        with tempfile.TemporaryDirectory() as d:
            fx = Fixture(d, [_scope_cfg(scope="primary", instance="primary-a"),
                             _scope_cfg(scope="secondary", instance="secondary-a")])
            spawn = RecordingSpawn()
            report = ns.supervise(
                fx.manifest_path,
                adapters={"primary": FakeAdapter(alive=False),
                          "secondary": FakeAdapter(alive=False)},
                now=NOW, spawn=spawn)
            self.assertEqual(len(report.restarted), 2)
            self.assertEqual(len(spawn.calls), 2)

    def test_one_crash_looping_the_other_stays_healthy(self):
        with tempfile.TemporaryDirectory() as d:
            fx = Fixture(d, [_scope_cfg(scope="primary", instance="primary-a",
                                        max_restarts=0),
                             _scope_cfg(scope="secondary", instance="secondary-a")])
            fx.seed_state({"secondary": {
                "instance": "secondary-a", "epoch": 1, "pid": 222,
                "started_at": NOW - 5000, "restart_count": 0,
                "backoff_until": None, "down": False, "down_reason": None}})
            report = ns.supervise(
                fx.manifest_path,
                adapters={"primary": FakeAdapter(alive=False),
                          "secondary": FakeAdapter(alive=True, heartbeat_age=1.0)},
                now=NOW, spawn=_never_called)
            self.assertEqual(len(report.down), 1)
            self.assertEqual(report.down[0]["scope"], "primary")
            self.assertEqual(len(report.alive), 1)
            self.assertEqual(report.alive[0]["scope"], "secondary")


class ClockBudgetTests(unittest.TestCase):

    def test_zero_clock_budget_defers_every_scope(self):
        with tempfile.TemporaryDirectory() as d:
            fx = Fixture(d, [_scope_cfg()])
            report = ns.supervise(
                fx.manifest_path, adapters={"primary": FakeAdapter(alive=False)},
                now=NOW, spawn=_never_called, clock_budget=0)
            self.assertEqual(report.restarted, [])
            self.assertEqual(len(report.deferred), 1)
            self.assertIn("clock_budget", report.deferred[0]["reason"])


class ConcurrentStartTests(unittest.TestCase):

    def test_second_concurrent_supervisor_is_refused_not_silently_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            fx = Fixture(d, [_scope_cfg()])
            old_timeout = ns.STATE_LOCK_TIMEOUT_S
            ns.STATE_LOCK_TIMEOUT_S = 0.05
            try:
                # Another "instance" already holds the state lock: acquire
                # it directly, exactly as a concurrently running
                # supervise() call would via the same claim_store.Lock.
                lock = claim_store.Lock(fx.state_path,
                                         timeout=ns.STATE_LOCK_TIMEOUT_S)
                lock.__enter__()
                try:
                    with self.assertRaises(TimeoutError):
                        ns.supervise(
                            fx.manifest_path,
                            adapters={"primary": FakeAdapter(alive=False)},
                            now=NOW, spawn=RecordingSpawn())
                finally:
                    lock.__exit__(None, None, None)
            finally:
                ns.STATE_LOCK_TIMEOUT_S = old_timeout


class JudgementBoundaryTests(unittest.TestCase):
    """Direct proof for at least three of the eight things this module may
    never do (module docstring): edit source, choose a decomposition,
    merge, lower a risk class, mark a unit DONE, invent a verdict, bypass
    a live lease, execute a RED action."""

    def test_module_exposes_no_task_judgement_functions(self):
        forbidden_fragments = (
            "merge", "decompos", "mark_done", "lower_risk", "verdict",
            "execute_red", "edit_source", "choose_",
        )
        names = [n.lower() for n in dir(ns)]
        for fragment in forbidden_fragments:
            hits = [n for n in names if fragment in n]
            self.assertEqual(
                hits, [],
                "night_supervisor exposes a name suggesting task judgement: "
                "%r matched by fragment %r" % (hits, fragment))

    def test_module_never_imports_the_canonical_or_scheduling_engines(self):
        with open(ns.__file__, encoding="utf-8") as fh:
            source = fh.read()
        for forbidden in ("import integrate", "from integrate",
                          "import graph_loop", "from graph_loop"):
            self.assertNotIn(
                forbidden, source,
                "night_supervisor must never import the modules that "
                "establish canonical truth or decide what may run")

    def test_a_live_lease_held_by_someone_else_is_refused_not_bypassed(self):
        # Restated here as its own top-level assertion (also covered in
        # LiveLeaseBoundaryTests) because rule 6 is explicitly one of the
        # eight things this module may never do.
        with tempfile.TemporaryDirectory() as d:
            store = os.path.join(d, "authority.json")
            auth.acquire(store, "run-test", "primary", "someone-else",
                         "foreign", 300, now=NOW)
            with self.assertRaises(ns.SupervisorRefused):
                ns.attempt_restart(store, "run-test", "primary", "primary-a",
                                    "execution", 300, "test", NOW + 1,
                                    RecordingSpawn(), _scope_cfg())


class HeartbeatFileAdapterTests(unittest.TestCase):
    """The one production adapter this module ships."""

    def test_round_trip_fresh(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "heartbeat.json")
            ns.write_heartbeat_file(path, now=NOW)
            adapter = ns.FileHeartbeatAdapter(path)
            self.assertAlmostEqual(adapter.heartbeat_age_s(NOW + 5), 5.0)

    def test_missing_file_is_no_data_none(self):
        adapter = ns.FileHeartbeatAdapter("/nonexistent/heartbeat.json")
        self.assertIsNone(adapter.heartbeat_age_s(NOW))

    def test_corrupt_file_is_no_data_none(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "heartbeat.json")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("not json")
            adapter = ns.FileHeartbeatAdapter(path)
            self.assertIsNone(adapter.heartbeat_age_s(NOW))

    def test_process_alive_delegates_to_claim_store(self):
        adapter = ns.FileHeartbeatAdapter("/nonexistent/heartbeat.json")
        self.assertTrue(adapter.process_alive(os.getpid()))
        self.assertFalse(adapter.process_alive(_certainly_dead_pid()))


def _certainly_dead_pid():
    """A pid essentially guaranteed not to exist, for a real
    claim_store.pid_alive() check without spawning or killing anything."""
    candidate = 2_000_000_000
    while claim_store.pid_alive(candidate):
        candidate -= 1
    return candidate


if __name__ == "__main__":
    unittest.main()
