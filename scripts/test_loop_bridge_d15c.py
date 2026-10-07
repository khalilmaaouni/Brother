"""D15-C tests: the bridge refuses a plan whose scheduling contract is wrong.

This file sits BESIDE the module it tests (scripts/loop_bridge.py). It imports
no plugin package and no products package, because a scripts/ test runs on the
public export tree and that tree ships neither. Where the scheduling policy
module IS importable, one test also observes the accepted path; where it is not,
the same test asserts the refusal instead, because a missing validator must
refuse and never approve.

Run: python3 -B -m unittest scripts.test_loop_bridge_d15c
"""
import contextlib
import copy
import io
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import loop_bridge  # noqa: E402  (the sibling module under test)


POLICY_RAW = {
    "policy_id": "ready-priority-v1",
    "requested_width": 2,
    "priority_key": "priority_score",
    "tie_breaker": "starvation_age",
}

LIMITS_RAW = {
    "max_width": 4,
    "max_cpu_seconds": 3600,
    "max_wall_seconds": 3600,
    "max_memory_mb": 4096,
    "max_disk_mb": 8192,
    "max_network_bytes": 0,
    "max_tokens": 200000,
    "max_cost_micros": 5000000,
}

CONSTRAINTS_RAW = {
    "conflict_free_ids": ["D15", "D16"],
    "dependency_satisfied_ids": ["D15", "D16"],
    "founder_authorized_ids": ["D15", "D16"],
    "lease_held_ids": ["D15", "D16"],
    "starvation_guaranteed_ids": ["D16"],
    "canonical_base_known": True,
    "contention_known": True,
}

HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64


def contract_plan(batch_ids=("D15", "D16"), chosen=("D15", "D16"), width=2,
                  status="OK", hashes=(HASH_A, HASH_B, HASH_C), raw=True):
    """A plan shaped like the one plan_with_scheduling returns."""
    scheduling = {
        "status": status,
        "chosen_order": list(chosen),
        "width": width,
        "policy_hash": hashes[0],
        "limits_hash": hashes[1],
        "constraints_hash": hashes[2],
    }
    if raw:
        scheduling["policy_raw"] = copy.deepcopy(POLICY_RAW)
        scheduling["limits_raw"] = copy.deepcopy(LIMITS_RAW)
        scheduling["constraints_raw"] = copy.deepcopy(CONSTRAINTS_RAW)
    return {"batch": [{"id": unit_id} for unit_id in batch_ids],
            "deferred": [],
            "blocked": [],
            "scheduling": scheduling}


def _accepted_scheduling_plan():
    """A plan the bridge accepts, or None when no validator is importable here.

    The hashes come from the bridge's own recompute, so this plan is about the
    dispatch ORDER and not about the hash arithmetic (which the mismatch test
    covers). On a tree with no importable scheduling policy module no contract
    can be accepted at all, and the caller asserts that refusal instead of
    pretending it observed an accepted dispatch.
    """
    plan = contract_plan()
    ok, _why, triple = loop_bridge.recompute_scheduling_hashes(plan)
    if not ok or triple is None:
        return None
    (plan["scheduling"]["policy_hash"],
     plan["scheduling"]["limits_hash"],
     plan["scheduling"]["constraints_hash"]) = triple
    ok, _why = loop_bridge.validate_scheduling_contract(plan)
    if not ok:
        return None
    return plan


class _FakeClaimStore(object):
    """A claim store that records what was asked of it and grants nothing.

    Granting nothing is the refusal a live claim produces, and it keeps every
    assertion here about what was ASKED for. It reads and writes no file.
    """

    def __init__(self):
        self.acquires = []
        self.reconciled = []

    def reconcile(self, path, clock=None):
        self.reconciled.append(path)
        return [], ""

    def acquire(self, path, unit_id, owner, work_id="", ttl=None, clock=None,
                attempt=None, **rest):
        self.acquires.append(unit_id)
        return None, ("unit %s is claimed by somebody else until later"
                      % unit_id)


class _Harness(unittest.TestCase):
    """Runs main() with no roadmap, no claim store, no worker and no file."""

    def run_main(self, plan, record_validation=False):
        real_load = loop_bridge.graph_loop.load
        real_plan = loop_bridge.graph_loop.plan
        real_parts = loop_bridge.load_parts
        real_store = loop_bridge.claim_store
        real_validate = loop_bridge.validate_scheduling_contract
        store = _FakeClaimStore()
        validations = []

        def fake_load(path=None):
            return {"rows": [], "features": []}

        def fake_plan(doc, slots=None):
            return plan

        def fake_parts(tools):
            return {"spawn": "unused"}, ""

        def recording_validate(candidate):
            validations.append(candidate)
            return False, "NO-DATA: scheduling contract is absent"

        sink = io.StringIO()
        try:
            loop_bridge.graph_loop.load = fake_load
            loop_bridge.graph_loop.plan = fake_plan
            loop_bridge.load_parts = fake_parts
            loop_bridge.claim_store = store
            if record_validation:
                loop_bridge.validate_scheduling_contract = recording_validate
            with contextlib.redirect_stdout(sink):
                with contextlib.redirect_stderr(sink):
                    rc = loop_bridge.main([])
        finally:
            loop_bridge.graph_loop.load = real_load
            loop_bridge.graph_loop.plan = real_plan
            loop_bridge.load_parts = real_parts
            loop_bridge.claim_store = real_store
            loop_bridge.validate_scheduling_contract = real_validate
        return rc, store, validations


class DispatchControlTests(_Harness):

    def test_validate_absent_blocks(self):
        ok, why = loop_bridge.validate_scheduling_contract(
            {"batch": [{"id": "D15"}], "deferred": [], "blocked": []})
        self.assertFalse(ok)
        self.assertIn("NO-DATA", why)
        self.assertIn("absent", why)

    def test_validate_corrupt_blocks(self):
        for corrupt in (None, [], "OK", 3, 3.5, True, float("nan"),
                        ("status",)):
            ok, why = loop_bridge.validate_scheduling_contract(
                {"scheduling": corrupt})
            self.assertFalse(ok, corrupt)
            self.assertTrue(why, corrupt)
        ok, why = loop_bridge.validate_scheduling_contract("not a plan")
        self.assertFalse(ok)
        self.assertTrue(why)
        ok, why = loop_bridge.validate_scheduling_contract(
            contract_plan(status="BLOCKS"))
        self.assertFalse(ok)
        self.assertIn("BLOCKS", why)

    def test_validate_mismatch_blocks(self):
        plan = contract_plan(batch_ids=("D16", "D15"), chosen=("D15", "D16"))
        self.assertEqual([n["id"] for n in loop_bridge.dispatchable(plan)],
                         ["D16", "D15"])
        ok, why = loop_bridge.validate_scheduling_contract(plan)
        self.assertFalse(ok)
        self.assertIn("prefix", why)

    def test_validate_width_outside_blocks(self):
        for width in (3, -1, True, "2", 2.0, None, float("nan")):
            plan = contract_plan(width=width)
            ok, why = loop_bridge.validate_scheduling_contract(plan)
            self.assertFalse(ok, width)
            self.assertIn("width", why, width)

    def test_validate_duplicate_ids_blocks(self):
        plan = contract_plan(batch_ids=("D15", "D15"), chosen=("D15", "D15"))
        ok, why = loop_bridge.validate_scheduling_contract(plan)
        self.assertFalse(ok)
        self.assertIn("duplicate", why)

    def test_validate_hash_malformed_blocks(self):
        for bad in (None, "", "xyz", 0, True, float("nan"), "A" * 64,
                    "a" * 63, "a" * 65, "g" * 64, ["a" * 64]):
            plan = contract_plan()
            plan["scheduling"]["limits_hash"] = bad
            ok, why = loop_bridge.validate_scheduling_contract(plan)
            self.assertFalse(ok, bad)
            self.assertIn("limits_hash", why, bad)

    def test_validate_raw_hash_mismatch_blocks(self):
        plan = contract_plan()
        ok, why = loop_bridge.validate_scheduling_contract(plan)
        self.assertFalse(ok)
        self.assertTrue(why)
        plan = contract_plan(raw=False)
        ok, why = loop_bridge.validate_scheduling_contract(plan)
        self.assertFalse(ok)
        self.assertIn("SCHEDULING-RAW-MISSING", why)

    def test_machine_wide_refusal_surfaces_invalid_contract(self):
        plan = contract_plan(batch_ids=("D16", "D15"), chosen=("D15", "D16"))
        self.assertTrue(loop_bridge.dispatchable(plan))
        alert = loop_bridge.machine_wide_refusal(plan)
        self.assertTrue(alert)
        self.assertIn("SCHEDULING-CONTRACT", alert)
        self.assertIn("prefix", alert)

    def test_no_dispatch_when_invalid(self):
        plan = contract_plan()
        del plan["scheduling"]["chosen_order"]
        rc, store, _validations = self.run_main(plan)
        self.assertEqual(store.acquires, [])
        self.assertEqual(len(store.reconciled), 1)
        self.assertNotEqual(rc, 0)

    def test_main_calls_validate_before_dispatch(self):
        plan = contract_plan()
        rc, store, validations = self.run_main(plan, record_validation=True)
        self.assertEqual(len(validations), 1)
        self.assertEqual(store.acquires, [])
        self.assertNotEqual(rc, 0)

    def test_effective_dispatch_uses_chosen_order(self):
        chosen = ("D15", "D16")
        incumbent = contract_plan(batch_ids=("D16", "D15"), chosen=chosen)
        ok, why = loop_bridge.validate_scheduling_contract(incumbent)
        self.assertFalse(ok, "an incumbent-ordered batch must never pass")
        self.assertIn("prefix", why)
        accepted = _accepted_scheduling_plan()
        if accepted is None:
            return
        ok, why = loop_bridge.validate_scheduling_contract(accepted)
        self.assertTrue(ok, why)
        self.assertEqual(
            [n["id"] for n in loop_bridge.dispatchable(accepted)],
            list(chosen[:accepted["scheduling"]["width"]]))


class HostilePlanTests(unittest.TestCase):
    """A plan that is not a mapping must be refused, never crash a caller.

    Every function below is public, so a caller may hand it None, a bool, a
    string, a float, bytes or a set. Each of those must come back as this
    module's own refusal value; a raw AttributeError from a .get() on something
    that is not a plan is a crash the caller cannot classify.
    """

    HOSTILE = (None, 3, 3.5, float("nan"), True, "plan", ["plan"],
               ("plan",), b"plan", {"plan"}, float("inf"))

    def test_dispatchable_hostile_plan_refuses(self):
        for hostile in self.HOSTILE:
            self.assertEqual(loop_bridge.dispatchable(hostile), [], hostile)

    def test_dispatchable_hostile_batch_refuses(self):
        for batch in ("abc", {"D15": 1}, 3, 3.5, True, float("nan")):
            self.assertEqual(loop_bridge.dispatchable({"batch": batch}), [],
                             batch)

    def test_refused_hostile_plan_refuses(self):
        for hostile in self.HOSTILE:
            self.assertEqual(loop_bridge.refused(hostile), [], hostile)

    def test_refused_hostile_entries_are_skipped(self):
        plan = {"batch": [], "deferred": ["ab", ({"id": 3}, "why"), None],
                "blocked": [{"id": "D15"}, ({"id": "D15"}, "D16"),
                            ({"id": "D15"}, ("D16", 3))]}
        self.assertEqual(loop_bridge.refused(plan), [])

    def test_refused_wellformed_plan_unchanged(self):
        plan = {"deferred": [({"id": "D15"}, "no free slot")],
                "blocked": [({"id": "D16"}, ("D17", "D18"))]}
        self.assertEqual(
            loop_bridge.refused(plan),
            [("D15", "no free slot"), ("D16", "BLOCKED-BY D17, D18")])

    def test_machine_wide_refusal_hostile_plan_is_reported(self):
        for hostile in self.HOSTILE:
            alert = loop_bridge.machine_wide_refusal(hostile)
            self.assertTrue(alert, hostile)
            self.assertIn("SCHEDULING-CONTRACT", alert)

    def test_hostile_plan_values_are_refused(self):
        for hostile in self.HOSTILE:
            ok, why = loop_bridge.validate_scheduling_contract(hostile)
            self.assertFalse(ok, hostile)
            self.assertTrue(why, hostile)

    def test_hostile_contract_fields_are_refused(self):
        base = contract_plan()
        cases = []
        for field, value in (("status", None), ("status", 3),
                             ("status", ["OK"]),
                             ("chosen_order", "D15"),
                             ("chosen_order", ("D15",)),
                             ("chosen_order", [1]),
                             ("chosen_order", None),
                             ("chosen_order", [["D15"]]),
                             ("width", "2"), ("width", 2.0),
                             ("width", None), ("width", float("nan")),
                             ("width", -1)):
            plan = copy.deepcopy(base)
            plan["scheduling"][field] = value
            cases.append((field, value, plan))
        for batch in ("D15", {"id": "D15"}, None, [None], [[]], [{}],
                      [{"id": 3}], [{"id": ""}], [{"id": ["D15"]}]):
            plan = copy.deepcopy(base)
            plan["batch"] = batch
            cases.append(("batch", batch, plan))
        for field, value, plan in cases:
            ok, why = loop_bridge.validate_scheduling_contract(plan)
            self.assertFalse(ok, (field, value))
            self.assertTrue(why, (field, value))

    def test_hostile_raw_inputs_are_refused(self):
        for hostile in (None, 3, 3.5, float("nan"), True, "plan",
                        [{"id": "D15"}], b"plan", {"plan"}):
            ok, why, triple = loop_bridge.recompute_scheduling_hashes(hostile)
            self.assertFalse(ok, hostile)
            self.assertTrue(why, hostile)
            self.assertIsNone(triple, hostile)
        plan = contract_plan()
        plan["scheduling"]["policy_raw"] = ["not", "a", "mapping"]
        ok, why, triple = loop_bridge.recompute_scheduling_hashes(plan)
        self.assertFalse(ok)
        self.assertIn("SCHEDULING-RAW-MISSING", why)
        self.assertIsNone(triple)
        plan = contract_plan()
        broken = dict(LIMITS_RAW)
        broken["max_width"] = float("nan")
        plan["scheduling"]["limits_raw"] = broken
        ok, why, triple = loop_bridge.recompute_scheduling_hashes(plan)
        self.assertFalse(ok)
        self.assertTrue(why)
        self.assertIsNone(triple)
        plan = contract_plan()
        plan["scheduling"]["policy_raw"] = dict(POLICY_RAW,
                                                requested_width=True)
        ok, why, triple = loop_bridge.recompute_scheduling_hashes(plan)
        self.assertFalse(ok)
        self.assertTrue(why)
        self.assertIsNone(triple)
        plan = contract_plan()
        plan["scheduling"]["constraints_raw"] = dict(
            CONSTRAINTS_RAW, conflict_free_ids=[["D15"]])
        ok, why, triple = loop_bridge.recompute_scheduling_hashes(plan)
        self.assertFalse(ok)
        self.assertTrue(why)
        self.assertIsNone(triple)


if __name__ == "__main__":
    unittest.main()
