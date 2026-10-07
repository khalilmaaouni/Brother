"""D1.5 tests: the recording bridge in scripts/loop_bridge.py.

These live BESIDE the module under test, in scripts/, because done_check
runs exactly this dotted path:

    python3 -B -m unittest scripts.test_loop_bridge_d15

They assert what D1.5's specification says, not what the code happens to
do: per-field usage totals with their coverage counters, the content-class
defaults and their sources, the outside-lane refusal decided BEFORE any
claim lock is taken, the NO-DATA refusal of a recording request that
carries no store path, the release-time fingerprint control, and the one
promise this module has always made in its own docstring: run_node NEVER
RAISES, even when a caller hands it a parts mapping whose verify or repair
is not the object it expected.

No fixture here reads a live repository document, spawns a process or
touches a real claim store: every input is built in the test body, and the
node runner is replaced for the recording-path tests.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import scripts.loop_bridge as loop_bridge


LANE_ROUTER_READY = loop_bridge.lane_router is not None

_PASSING_RECORD = {"id": "U1", "worker_status": "ok", "verdict": "PASS",
                   "reason": None, "repair": None, "scope": None,
                   "integrable": True}


class _FakeVerify:
    def verify(self, unit, cwd=None):
        return dict(_PASSING_RECORD)

    def is_pass(self, verdict):
        return isinstance(verdict, dict) and verdict.get("verdict") == "PASS"


class _FakeRepair:
    def repair(self, unit, verdict, worker, cwd=None, max_attempts=3):
        return {"outcome": "not-needed", "attempts": [], "reason": None,
                "final_verdict": dict(_PASSING_RECORD)}


def _parts():
    """A parts mapping that can verify and repair, for the paths that need it."""
    return {"verify": _FakeVerify(), "repair": _FakeRepair()}


def _fake_worker():
    class _Worker:
        def run(self, unit, cwd=None):
            return {"status": "ok", "worker_claim": "d15", "usage": None}
    return _Worker()


def _lane(draft):
    """A real lane_router.Lane; only ever called when that import worked."""
    return loop_bridge.lane_router.Lane(
        draft, None, loop_bridge.lane_router.VERIFY, True, "d15 test lane")


class _FakeClaimStore:
    """The claim store seam, in memory, so no test touches a real store."""

    def __init__(self, fingerprint=None):
        self.fingerprint = fingerprint
        self.calls = []
        self.records = []

    def acquire(self, path, unit_id, owner, **kwargs):
        self.calls.append(("acquire", path, unit_id, owner, kwargs))
        attempt = len(self.records) + 1
        recorded = kwargs.get("ready_set_fingerprint")
        if self.fingerprint is not None:
            recorded = self.fingerprint
        claim = {"unit_id": unit_id, "owner": owner, "attempt": attempt,
                 "attempt_id": "attempt-%d" % attempt}
        claim.update(kwargs)
        claim["ready_set_fingerprint"] = recorded
        self.records.append(claim)
        return dict(claim), ""

    def release(self, path, unit_id, owner, state="done", **kwargs):
        self.calls.append(("release", state))
        return {"state": state}, ""

    def head(self, path, unit_id):
        if not self.records:
            return None, "NO-DATA"
        return dict(self.records[-1]), ""

    def history(self, path, unit_id):
        return tuple(dict(record) for record in self.records), ""


class TestD15AccumulateUsage(unittest.TestCase):
    def test_empty_records_give_an_empty_report(self):
        self.assertEqual(loop_bridge.accumulate_usage([]), {})

    def test_hostile_records_refused_never_raise(self):
        for bad in (None, 42, "text", object(), {"tokens_in": 1}, 3.5, b"x"):
            self.assertEqual(loop_bridge.accumulate_usage(bad), {})

    def test_sum_covers_every_child(self):
        out = loop_bridge.accumulate_usage([{"tokens_in": 100}] * 3)
        self.assertEqual(out["tokens_in"], 300.0)
        self.assertEqual(out["tokens_in_covered"], 3)
        self.assertEqual(out["tokens_in_of"], 3)

    def test_none_usage_counts_toward_of_only(self):
        out = loop_bridge.accumulate_usage(
            [{"tokens_in": 10}, None, {"tokens_in": 20}])
        self.assertEqual(out["tokens_in"], 30.0)
        self.assertEqual(out["tokens_in_covered"], 2)
        self.assertEqual(out["tokens_in_of"], 3)

    def test_zero_covered_field_absent_not_zero(self):
        out = loop_bridge.accumulate_usage([None, {}, {"tokens_in": "many"}])
        self.assertNotIn("tokens_in", out)
        self.assertNotIn("tokens_in_covered", out)
        self.assertNotIn("tokens_in_of", out)

    def test_bool_is_never_summed(self):
        out = loop_bridge.accumulate_usage(
            [{"tokens_in": True}, {"tokens_in": 5}])
        self.assertEqual(out["tokens_in"], 5.0)
        self.assertEqual(out["tokens_in_covered"], 1)
        self.assertEqual(out["tokens_in_of"], 2)

    def test_nan_is_never_summed_into_a_total(self):
        out = loop_bridge.accumulate_usage(
            [{"tokens_cached": float("nan")}])
        self.assertNotIn("tokens_cached", out)

    def test_a_fractional_mean_is_summed_not_rounded(self):
        out = loop_bridge.accumulate_usage(
            [{"tokens_out": 2.5}, {"tokens_out": 1.5}])
        self.assertEqual(out["tokens_out"], 4.0)

    def test_every_field_is_counted_separately(self):
        out = loop_bridge.accumulate_usage([{
            "tokens_in": 1, "tokens_out": 2,
            "tokens_cached": 3, "tokens_cache_write": 4}])
        self.assertEqual(out["tokens_in"], 1.0)
        self.assertEqual(out["tokens_out"], 2.0)
        self.assertEqual(out["tokens_cached"], 3.0)
        self.assertEqual(out["tokens_cache_write"], 4.0)


@unittest.skipUnless(LANE_ROUTER_READY, "lane_router could not be imported")
class TestD15DispatchRecordingKwargs(unittest.TestCase):
    def test_outside_lane_on_private_content_refused_before_acquire(self):
        kwargs, refusal = loop_bridge.dispatch_recording_kwargs(
            {"id": "U1", "content_class": "private"}, {}, _lane("deepseek"),
            "owner-a")
        self.assertEqual(kwargs, {})
        self.assertTrue(refusal)
        self.assertIn("outside", refusal)

    def test_outside_lane_on_public_content_allowed(self):
        kwargs, refusal = loop_bridge.dispatch_recording_kwargs(
            {"id": "U1", "content_class": "public"}, {}, _lane("deepseek"),
            "owner-a")
        self.assertEqual(refusal, "")
        self.assertEqual(kwargs["content_class"], "public")
        self.assertEqual(kwargs["advised_lane"], "deepseek")

    def test_missing_content_class_records_undeclared_default(self):
        kwargs, refusal = loop_bridge.dispatch_recording_kwargs(
            {"id": "U1"}, {}, _lane("claude"), "owner-a")
        self.assertEqual(refusal, "")
        self.assertEqual(kwargs["content_class"], "private")
        self.assertEqual(kwargs["content_class_source"], "undeclared-default")

    def test_invalid_content_class_records_invalid_default(self):
        kwargs, refusal = loop_bridge.dispatch_recording_kwargs(
            {"id": "U1", "content_class": "secret"}, {}, _lane("claude"),
            "owner-a")
        self.assertEqual(refusal, "")
        self.assertEqual(kwargs["content_class"], "private")
        self.assertEqual(kwargs["content_class_source"], "invalid-default")

    def test_unhashable_content_class_is_invalid_never_a_crash(self):
        kwargs, refusal = loop_bridge.dispatch_recording_kwargs(
            {"id": "U1", "content_class": ["private"]}, {}, _lane("claude"),
            "owner-a")
        self.assertEqual(refusal, "")
        self.assertEqual(kwargs["content_class"], "private")
        self.assertEqual(kwargs["content_class_source"], "invalid-default")

    def test_fingerprint_is_64_lowercase_hex(self):
        kwargs, refusal = loop_bridge.dispatch_recording_kwargs(
            {"id": "U1"}, {}, _lane("claude"), "owner-a")
        self.assertEqual(refusal, "")
        fingerprint = kwargs["ready_set_fingerprint"]
        self.assertEqual(len(fingerprint), 64)
        self.assertEqual(fingerprint, fingerprint.lower())
        int(fingerprint, 16)

    def test_empty_owner_refused_by_name(self):
        kwargs, refusal = loop_bridge.dispatch_recording_kwargs(
            {"id": "U1"}, {}, _lane("claude"), "")
        self.assertEqual(kwargs, {})
        self.assertTrue(refusal)
        self.assertIn("owner", refusal)

    def test_node_that_is_not_a_mapping_refused_by_name(self):
        for bad in (None, 42, "x", ["U1"], b"n"):
            kwargs, refusal = loop_bridge.dispatch_recording_kwargs(
                bad, {}, _lane("claude"), "owner-a")
            self.assertEqual(kwargs, {})
            self.assertTrue(refusal)
            self.assertIn("not a mapping", refusal)

    def test_dependency_parents_that_is_not_a_list_refused_by_name(self):
        for bad in (42, "U0", {"a": 1}, {1, 2}, (x for x in ["U0"])):
            kwargs, refusal = loop_bridge.dispatch_recording_kwargs(
                {"id": "U1", "dependency_parents": bad}, {}, _lane("claude"),
                "owner-a")
            self.assertEqual(kwargs, {})
            self.assertTrue(refusal)
            self.assertIn("dependency_parents", refusal)

    def test_dependency_parents_are_copied_not_aliased(self):
        parents = ["U0"]
        kwargs, refusal = loop_bridge.dispatch_recording_kwargs(
            {"id": "U1", "dependency_parents": parents}, {}, _lane("claude"),
            "owner-a")
        self.assertEqual(refusal, "")
        self.assertEqual(kwargs["dependency_parents"], ["U0"])
        self.assertIsNot(kwargs["dependency_parents"], parents)

    def test_hostile_plan_and_lane_never_raise(self):
        for bad_plan in (None, 42, "x", ["U1"], b"p"):
            kwargs, refusal = loop_bridge.dispatch_recording_kwargs(
                {"id": "U1"}, bad_plan, object(), "owner-a")
            self.assertIsInstance(kwargs, dict)
            self.assertIsInstance(refusal, str)


class TestD15RepairChildren(unittest.TestCase):
    def test_missing_path_refused_by_name(self):
        ids, problem = loop_bridge._record_repair_children(
            None, "U1", "owner-a", None, [], {})
        self.assertEqual(ids, [])
        self.assertIn("no claim store path", problem)

    def test_wrong_type_path_refused_by_name(self):
        ids, problem = loop_bridge._record_repair_children(
            42, "U1", "owner-a", None, [], {})
        self.assertEqual(ids, [])
        self.assertIn("no claim store path", problem)

    def test_empty_unit_id_refused_by_name(self):
        ids, problem = loop_bridge._record_repair_children(
            "/tmp/d15-store.json", "", "owner-a", None, [], {})
        self.assertEqual(ids, [])
        self.assertIn("unit id", problem)

    def test_empty_owner_refused_by_name(self):
        ids, problem = loop_bridge._record_repair_children(
            "/tmp/d15-store.json", "U1", "", None, [], {})
        self.assertEqual(ids, [])
        self.assertIn("owner", problem)

    def test_non_list_children_refused_by_name(self):
        for bad in (None, "nope", 42, {"a": 1}, (x for x in [1])):
            ids, problem = loop_bridge._record_repair_children(
                "/tmp/d15-store.json", "U1", "owner-a", None, bad, {})
            self.assertEqual(ids, [])
            self.assertIn("repair children must be a list", problem)

    def test_absent_claim_store_refused_by_name(self):
        saved = loop_bridge.claim_store
        loop_bridge.claim_store = None
        try:
            ids, problem = loop_bridge._record_repair_children(
                "/tmp/d15-store.json", "U1", "owner-a", None, [{}], {})
        finally:
            loop_bridge.claim_store = saved
        self.assertEqual(ids, [])
        self.assertIn("claim store is unavailable", problem)

    def test_every_child_is_recorded_in_order(self):
        store = _FakeClaimStore()
        saved = loop_bridge.claim_store
        loop_bridge.claim_store = store
        try:
            ids, problem = loop_bridge._record_repair_children(
                "/tmp/d15-store.json", "U1", "owner-a", "parent-1",
                [{"state": "failed"}, {"state": "done"}], {})
        finally:
            loop_bridge.claim_store = saved
        self.assertEqual(problem, "")
        self.assertEqual(ids, ["attempt-1", "attempt-2"])
        states = [call[1] for call in store.calls if call[0] == "release"]
        self.assertEqual(states, ["failed", "done"])


class TestD15RunNodeRefusals(unittest.TestCase):
    def test_missing_verify_part_refused_by_name(self):
        result = loop_bridge.run_node(
            {"id": "U1"}, {"repair": _FakeRepair()}, _fake_worker())
        self.assertEqual(result["verdict"], "NO-DATA")
        self.assertIn("verify part", result["reason"])

    def test_missing_repair_part_refused_by_name(self):
        result = loop_bridge.run_node(
            {"id": "U1"}, {"verify": _FakeVerify()}, _fake_worker())
        self.assertEqual(result["verdict"], "NO-DATA")
        self.assertIn("repair part", result["reason"])

    def test_parts_that_is_not_a_mapping_refused_by_name(self):
        for bad in (None, 42, "verify", ["verify"], b"parts"):
            result = loop_bridge.run_node({"id": "U1"}, bad, _fake_worker())
            self.assertEqual(result["verdict"], "NO-DATA")
            self.assertIn("parts mapping", result["reason"])

    def test_node_that_is_not_a_mapping_refused_by_name(self):
        for bad in (None, 42, "U1", ["U1"], b"U1"):
            result = loop_bridge.run_node(bad, _parts(), _fake_worker())
            self.assertEqual(result["verdict"], "NO-DATA")
            self.assertIn("not a mapping", result["reason"])

    def test_node_without_a_string_id_refused_by_name(self):
        for bad in ({}, {"id": None}, {"id": 7}, {"id": ""}, {"id": b"U1"}):
            result = loop_bridge.run_node(bad, _parts(), _fake_worker())
            self.assertEqual(result["verdict"], "NO-DATA")
            self.assertIn("string id", result["reason"])

    def test_run_node_never_raises_for_a_part_that_cannot_verify(self):
        """The finding class: a parts mapping that is not a verifier/repair."""
        result = loop_bridge.run_node(
            {"id": "U1", "base_revision": "deadbeef"},
            {"verify": object(), "repair": object()}, _fake_worker())
        self.assertIsInstance(result, dict)
        self.assertEqual(result["verdict"], "NO-DATA")
        self.assertTrue(result["reason"])

    def test_run_node_never_raises_for_a_worker_that_cannot_run(self):
        result = loop_bridge.run_node(
            {"id": "U1", "base_revision": "deadbeef"}, _parts(), object())
        self.assertIsInstance(result, dict)
        self.assertEqual(result["verdict"], "NO-DATA")
        self.assertTrue(result["reason"])

    def test_missing_store_path_refuses_no_data_never_silent(self):
        calls = []

        class _CountingParts:
            pass

        result = loop_bridge.run_node(
            {"id": "U1"}, _parts(), _fake_worker(),
            store_path=None, owner="owner-a")
        self.assertEqual(result["verdict"], "NO-DATA")
        self.assertIn("no claim store path", result["reason"])
        self.assertEqual(calls, [])
        self.assertNotIn("attempt_id", result)

    def test_recording_disabled_is_never_none(self):
        self.assertIsNot(loop_bridge.RECORDING_DISABLED, None)


@unittest.skipUnless(LANE_ROUTER_READY, "lane_router could not be imported")
class TestD15RecordedPath(unittest.TestCase):
    def setUp(self):
        self._impl = loop_bridge._run_node_impl
        self._store = loop_bridge.claim_store
        loop_bridge._run_node_impl = lambda *a, **k: dict(_PASSING_RECORD)

    def tearDown(self):
        loop_bridge._run_node_impl = self._impl
        loop_bridge.claim_store = self._store

    def _install(self, fingerprint=None):
        store = _FakeClaimStore(fingerprint=fingerprint)
        loop_bridge.claim_store = store
        return store

    def test_recording_disabled_calls_no_store_and_adds_no_d1_key(self):
        store = self._install()
        result = loop_bridge.run_node({"id": "U1"}, _parts(), _fake_worker())
        self.assertEqual(result["verdict"], "PASS")
        self.assertEqual(store.calls, [])
        self.assertNotIn("attempt_id", result)
        self.assertNotIn("ready_set_fingerprint", result)

    def test_missing_store_path_refuses_before_any_store_call(self):
        store = self._install()
        result = loop_bridge.run_node(
            {"id": "U1"}, _parts(), _fake_worker(),
            store_path=None, owner="owner-a")
        self.assertEqual(result["verdict"], "NO-DATA")
        self.assertIn("no claim store path", result["reason"])
        self.assertEqual(store.calls, [])
        self.assertNotIn("attempt_id", result)

    def test_acquire_records_fingerprint_and_content_class(self):
        store = self._install()
        result = loop_bridge.run_node(
            {"id": "U1", "content_class": "public"}, _parts(), _fake_worker(),
            store_path="/tmp/d15-store.json", owner="owner-a")
        acquires = [call for call in store.calls if call[0] == "acquire"]
        self.assertEqual(len(acquires), 1)
        recorded = acquires[0][4]
        self.assertEqual(recorded["content_class"], "public")
        self.assertEqual(recorded["content_class_source"], "declared")
        fingerprint = recorded["ready_set_fingerprint"]
        self.assertEqual(len(fingerprint), 64)
        self.assertEqual(result["attempt_id"], "attempt-1")

    def test_done_release_without_fingerprint_refused_no_data(self):
        store = self._install(fingerprint="")
        result = loop_bridge.run_node(
            {"id": "U1", "content_class": "public"}, _parts(), _fake_worker(),
            store_path="/tmp/d15-store.json", owner="owner-a")
        self.assertEqual(result["verdict"], "NO-DATA")
        self.assertIn("fingerprint", result["reason"])
        for call in store.calls:
            if call[0] == "release":
                self.assertNotEqual(call[1], "done")

    def test_executed_outside_lane_on_restricted_content_refused_at_release(self):
        store = self._install()
        record = dict(_PASSING_RECORD)
        record["executed_lane"] = "deepseek"
        loop_bridge._run_node_impl = lambda *a, **k: dict(record)
        result = loop_bridge.run_node(
            {"id": "U1", "content_class": "private"}, _parts(),
            _fake_worker(), store_path="/tmp/d15-store.json", owner="owner-a")
        self.assertEqual(result["verdict"], "NO-DATA")
        self.assertIn("executed lane", result["reason"])
        for call in store.calls:
            if call[0] == "release":
                self.assertNotEqual(call[1], "done")

    def test_advised_outside_lane_on_private_content_refused_before_acquire(self):
        store = self._install()
        result = loop_bridge.run_node(
            {"id": "U1", "content_class": "private"}, _parts(),
            _fake_worker(), store_path="/tmp/d15-store.json", owner="owner-a",
            lane=_lane("deepseek"))
        self.assertEqual(result["verdict"], "NO-DATA")
        self.assertIn("outside", result["reason"])
        self.assertEqual(store.calls, [])

    def test_store_calls_that_raise_are_refused_not_escaped(self):
        class _ExplodingStore:
            def acquire(self, *args, **kwargs):
                raise RuntimeError("store is corrupt")

        loop_bridge.claim_store = _ExplodingStore()
        result = loop_bridge.run_node(
            {"id": "U1", "content_class": "public"}, _parts(), _fake_worker(),
            store_path="/tmp/d15-store.json", owner="owner-a")
        self.assertEqual(result["verdict"], "NO-DATA")
        self.assertTrue(result["reason"])


if __name__ == "__main__":
    unittest.main()
