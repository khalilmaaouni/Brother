"""D1.2 ready-set fingerprint tests.

Every hostile input class named by the red team is fired here as executed
code: an unhashable key, a mapping whose own get raises, a get attribute
that is not callable, a sequence whose iteration raises, a bool where a
number belongs, NaN, bytes that are not utf-8, and a record altered after
it was parsed. Each one must be denied with the module's own restrictive
value (a fingerprint), never a raw interpreter exception, and never
accepted as a real plan.
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import graph_loop


HEX = set("0123456789abcdef")


class _Unhashable(object):
    """A value that cannot be hashed, the way a list used as a key cannot."""
    __hash__ = None


class _HostileGet(dict):
    """A mapping whose own get raises the way an unhashable key does."""

    def get(self, key, default=None):
        raise TypeError("unhashable key")


class _NotCallableGet(dict):
    """A mapping whose get attribute is not callable."""
    get = None


class _BrokenIter(list):
    """A sequence whose own iteration raises."""

    def __iter__(self):
        raise RuntimeError("torn sequence")


class TestD12PlanFingerprint(unittest.TestCase):
    def assert_hex64(self, value):
        self.assertIsInstance(value, str)
        self.assertEqual(len(value), 64)
        self.assertTrue(all(c in HEX for c in value), value)

    def assert_no_data_plan(self, plan):
        self.assertIsInstance(plan, dict)
        self.assertEqual(plan["batch"], [])
        self.assertEqual(plan["capacity"], 0)
        self.assertTrue(any("NO-DATA" in note for note in plan["notes"]), plan)

    def _plan(self):
        doc = {"rows": [{"id": "U1", "status": "TODO", "owns": ["a.py"],
                         "depends_on": []}]}
        return graph_loop.plan(doc, slots=2)

    def test_fingerprint_is_64_hex_and_stable(self):
        plan = self._plan()
        first = graph_loop.plan_fingerprint(plan)
        self.assert_hex64(first)
        self.assertEqual(first, graph_loop.plan_fingerprint(plan))

    def test_fingerprint_moves_with_batch_membership(self):
        one = self._plan()
        two = graph_loop.plan({"rows": [
            {"id": "U1", "status": "TODO", "owns": ["a.py"], "depends_on": []},
            {"id": "U2", "status": "TODO", "owns": ["b.py"], "depends_on": []},
        ]}, slots=2)
        self.assertNotEqual(graph_loop.plan_fingerprint(one),
                            graph_loop.plan_fingerprint(two))

    def test_fingerprint_never_empty_on_empty_plan(self):
        value = graph_loop.plan_fingerprint({})
        self.assert_hex64(value)
        self.assertNotEqual(value, "")

    def test_bool_capacity_is_denied_as_zero(self):
        self.assertEqual(graph_loop.plan_fingerprint({"capacity": True}),
                         graph_loop.plan_fingerprint({"capacity": 0}))

    def test_non_string_node_id_denied_as_missing(self):
        self.assertEqual(
            graph_loop.plan_fingerprint({"batch": [{"id": ["a", "b"]}]}),
            graph_loop.plan_fingerprint({"batch": [{}]}))

    def test_unhashable_key_mapping_is_denied_not_crashed(self):
        hostile = _HostileGet({"batch": [{"id": "U1"}], "capacity": 4})
        self.assertEqual(graph_loop.plan_fingerprint(hostile),
                         graph_loop.plan_fingerprint({}))

    def test_mapping_get_not_callable_is_denied(self):
        hostile = _NotCallableGet({"batch": [{"id": "U1"}], "capacity": 4})
        self.assertEqual(graph_loop.plan_fingerprint(hostile),
                         graph_loop.plan_fingerprint({}))

    def test_unreadable_sequence_is_denied_as_empty_shape(self):
        plan = {"batch": _BrokenIter([{"id": "U1"}]), "capacity": 1}
        self.assertEqual(graph_loop.plan_fingerprint(plan),
                         graph_loop.plan_fingerprint({"capacity": 1}))

    def test_unhashable_component_denied_not_crashed(self):
        value = graph_loop.plan_fingerprint({
            "batch": [_Unhashable()],
            "deferred": [[_Unhashable(), _Unhashable()]],
            "unknown_deps": [(_Unhashable(), "U2")],
            "capacity": _Unhashable(),
        })
        self.assert_hex64(value)
        self.assertNotEqual(value, graph_loop.plan_fingerprint({}))

    def test_plan_altered_after_it_was_parsed_is_reread(self):
        plan = {"batch": [{"id": "U1"}], "capacity": 1}
        first = graph_loop.plan_fingerprint(plan)
        plan["batch"].append({"id": "U2"})
        self.assertNotEqual(first, graph_loop.plan_fingerprint(plan))

    def test_hostile_input_refused_not_crash(self):
        hostile = [
            None,
            7,
            [],
            "a string",
            b"\xff\xfe not utf-8",
            {"batch": "not a list"},
            {"deferred": None, "blocked": 7, "unknown_deps": {"x": 1}},
            {"batch": [None, 3, {"id": object()}], "capacity": float("nan")},
            {"batch": [{"id": None}, []], "unknown_deps": [[None, None]]},
            {"batch": [set([1, 2])], "capacity": False},
            {"batch": [{"id": "U1"}], "capacity": 2 ** 70},
            {"batch": [{"id": "U" * 5000}], "capacity": 1},
        ]
        for item in hostile:
            self.assert_hex64(graph_loop.plan_fingerprint(item))

    # Hostile input to plan(): every finding the red team named.
    def test_plan_none_refused(self):
        self.assert_no_data_plan(graph_loop.plan(None))

    def test_plan_bytes_not_utf8_refused(self):
        self.assert_no_data_plan(graph_loop.plan(b"\xff\xfe"))

    def test_plan_generator_rows_refused(self):
        self.assert_no_data_plan(graph_loop.plan({"rows": (r for r in [])}))

    def test_plan_str_rows_refused(self):
        self.assert_no_data_plan(graph_loop.plan({"rows": "not a list"}))

    def test_plan_wrong_type_doc_refused(self):
        self.assert_no_data_plan(graph_loop.plan("not a doc"))

    def test_plan_unhashable_id_refused(self):
        self.assert_no_data_plan(graph_loop.plan(
            {"rows": [{"id": [], "status": "TODO", "owns": [], "depends_on": []}]}))

    def test_nodes_none_refused(self):
        self.assertEqual(graph_loop.nodes(None), [])


if __name__ == "__main__":
    unittest.main()
