"""D15-B tests: plan_with_scheduling integration.

These tests run without the D15-A scheduling policy module. They patch
scripts.graph_loop.rank_ready_units with a deterministic fake so the
integration logic of plan_with_scheduling is exercised directly.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import graph_loop


class FakeResult:
    def __init__(self, status, chosen_order=(), width=0, reason="", refused=()):
        self.status = status
        self.chosen_order = chosen_order
        self.width = width
        self.reason = reason
        self.refused = refused
        self.policy_hash = "a" * 64
        self.limits_hash = "b" * 64
        self.constraints_hash = "c" * 64


class GraphPlanTests(unittest.TestCase):
    def setUp(self):
        self._orig_rank = graph_loop.rank_ready_units
        self._orig_error = graph_loop.SchedulingError

    def tearDown(self):
        graph_loop.rank_ready_units = self._orig_rank
        graph_loop.SchedulingError = self._orig_error

    def _patch_rank(self, result):
        graph_loop.rank_ready_units = lambda *args, **kwargs: result

    def test_plan_batch_equals_chosen_prefix(self):
        ready = [{"id": "A", "title": "alpha"}, {"id": "B", "title": "beta"}]
        deferred = [(({"id": "C"}), "waiting")]
        blocked = [(({"id": "D"}), ("E",))]
        self._patch_rank(FakeResult(status="OK", chosen_order=("B", "A"), width=1))
        plan = graph_loop.plan_with_scheduling(
            ready, deferred, blocked, ["A", "B"], object(), object(), object()
        )
        self.assertEqual([n["id"] for n in plan["batch"]], ["B"])
        self.assertEqual(plan["deferred"], deferred)
        self.assertEqual(plan["blocked"], blocked)
        self.assertEqual(plan["scheduling"]["status"], "OK")
        self.assertEqual(plan["scheduling"]["chosen_order"], ("B", "A"))
        self.assertEqual(plan["scheduling"]["width"], 1)

    def test_plan_batch_empty_when_not_ok(self):
        ready = [{"id": "A"}, {"id": "B"}]
        self._patch_rank(FakeResult(status="BLOCKS", chosen_order=("A", "B"), width=2))
        plan = graph_loop.plan_with_scheduling(
            ready, [], [], ["A", "B"], object(), object(), object()
        )
        self.assertEqual(plan["batch"], [])
        self.assertEqual(plan["scheduling"]["status"], "BLOCKS")

    def test_missing_dependency_refuses(self):
        graph_loop.rank_ready_units = None
        plan = graph_loop.plan_with_scheduling(
            [], [], [], [], object(), object(), object()
        )
        self.assertEqual(plan["batch"], [])
        self.assertEqual(plan["scheduling"]["status"], "NO-DATA")
        self.assertIn("missing", plan["scheduling"]["reason"].lower())

    def test_scheduling_error_blocks(self):
        class FakeSchedulingError(Exception):
            def __init__(self, code):
                self.code = code
        graph_loop.SchedulingError = FakeSchedulingError

        def raise_error(*args, **kwargs):
            raise FakeSchedulingError("WIDTH_OVERRIDE")

        graph_loop.rank_ready_units = raise_error
        plan = graph_loop.plan_with_scheduling(
            [], [], [], [], object(), object(), object()
        )
        self.assertEqual(plan["batch"], [])
        self.assertEqual(plan["scheduling"]["status"], "BLOCKS")
        self.assertEqual(plan["scheduling"]["reason"], "WIDTH_OVERRIDE")

    def test_hostile_ready_none(self):
        plan = graph_loop.plan_with_scheduling(
            None, [], [], [], object(), object(), object()
        )
        self.assertEqual(plan["batch"], [])
        self.assertEqual(plan["scheduling"]["status"], "NO-DATA")

    def test_hostile_ready_item_not_mapping(self):
        plan = graph_loop.plan_with_scheduling(
            [None], [], [], [], object(), object(), object()
        )
        self.assertEqual(plan["batch"], [])
        self.assertEqual(plan["scheduling"]["status"], "NO-DATA")

    def test_hostile_ready_item_missing_id(self):
        plan = graph_loop.plan_with_scheduling(
            [{}], [], [], [], object(), object(), object()
        )
        self.assertEqual(plan["batch"], [])
        self.assertEqual(plan["scheduling"]["status"], "NO-DATA")

    def test_zero_width_ok_yields_empty_batch(self):
        ready = [{"id": "A"}, {"id": "B"}]
        self._patch_rank(FakeResult(status="OK", chosen_order=("A", "B"), width=0))
        plan = graph_loop.plan_with_scheduling(
            ready, [], [], [], object(), object(), object()
        )
        self.assertEqual(plan["batch"], [])
        self.assertEqual(plan["scheduling"]["status"], "OK")


if __name__ == "__main__":
    unittest.main()
