#!/usr/bin/env python3
"""Tests for scripts/assurance_graph_wiring.py (unit DOM-10.01).

The headline case (test_removing_each_required_edge_fails) IS the row:
build a complete graph, assert PASS, then remove one edge of each
required type in turn and assert FAIL naming that edge type, restoring
the complete graph between attempts so each removal is independent.

stdlib only, temp dirs, no network.
"""
import json
import os
import tempfile
import unittest

import assurance_graph_wiring as agw


def complete_nodes():
    # Every node here is wired by complete_edges() below. The brief's
    # broader prose (intent, decision, files, integration, review) names
    # lifecycle stages the record MAY carry, but none of the ten required
    # edge types names a hop through them, so leaving them out keeps every
    # node reachable and avoids inventing edge types the row never asked
    # for; test_extra_unwired_lifecycle_node_still_fails covers the case
    # where one is present without being wired in.
    return [
        {"id": "n-req", "type": "requirement"},
        {"id": "n-impl", "type": "work_unit"},
        {"id": "n-claim", "type": "claim"},
        {"id": "n-check", "type": "check"},
        {"id": "n-env", "type": "execution_environment"},
        {"id": "n-evidence", "type": "evidence"},
        {"id": "n-receipt", "type": "receipt"},
        {"id": "n-accept", "type": "acceptance"},
        {"id": "n-release", "type": "release"},
        {"id": "n-outcome", "type": "observed_outcome"},
        {"id": "n-lesson", "type": "recorded_lesson"},
    ]


def complete_edges():
    return [
        {"from": "n-req", "to": "n-impl",
         "type": "requirement_to_implementation"},
        {"from": "n-impl", "to": "n-claim",
         "type": "implementation_to_claim"},
        {"from": "n-claim", "to": "n-check",
         "type": "claim_to_deciding_check"},
        {"from": "n-check", "to": "n-env",
         "type": "check_to_execution_environment"},
        {"from": "n-check", "to": "n-evidence",
         "type": "check_to_evidence"},
        {"from": "n-evidence", "to": "n-receipt",
         "type": "evidence_to_receipt"},
        {"from": "n-receipt", "to": "n-accept",
         "type": "receipt_to_acceptance"},
        {"from": "n-accept", "to": "n-release",
         "type": "acceptance_to_release"},
        {"from": "n-release", "to": "n-outcome",
         "type": "release_to_observed_outcome"},
        {"from": "n-outcome", "to": "n-lesson",
         "type": "failed_outcome_to_recorded_lesson"},
    ]


def complete_graph():
    return {"nodes": complete_nodes(), "edges": complete_edges()}


class WriteHelper(unittest.TestCase):
    def write(self, obj):
        fd, path = tempfile.mkstemp(dir=self.tmp, suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            if isinstance(obj, str):
                fh.write(obj)
            else:
                json.dump(obj, fh)
        return path

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = self._tmpdir.name

    def tearDown(self):
        self._tmpdir.cleanup()


class TestCompleteGraph(WriteHelper):
    def test_complete_graph_passes(self):
        self.assertEqual(agw.check(complete_graph()), [])

    def test_main_reports_pass(self):
        path = self.write(complete_graph())
        self.assertEqual(agw.main([path]), 0)


class TestRemovingEachRequiredEdgeFails(WriteHelper):
    """The row itself: one required edge removed at a time, complete
    graph restored between attempts (each test method gets a fresh
    complete_edges() list, never a mutated shared one)."""

    def test_removing_each_required_edge_fails(self):
        for etype in agw.REQUIRED_EDGE_TYPES:
            with self.subTest(edge_type=etype):
                edges = [e for e in complete_edges() if e["type"] != etype]
                graph = {"nodes": complete_nodes(), "edges": edges}
                problems = agw.check(graph)
                self.assertTrue(problems, "expected FAIL removing %s" % etype)
                self.assertTrue(
                    any(etype in p for p in problems),
                    "removing %s did not name it: %r" % (etype, problems))

    def test_main_exit_code_on_missing_edge(self):
        edges = [e for e in complete_edges()
                 if e["type"] != "check_to_evidence"]
        path = self.write({"nodes": complete_nodes(), "edges": edges})
        self.assertEqual(agw.main([path]), 1)


class TestDanglingEdges(WriteHelper):
    def test_from_names_no_node(self):
        graph = {"nodes": [{"id": "a"}],
                 "edges": [{"from": "ghost", "to": "a",
                            "type": "requirement_to_implementation"}]}
        problems = agw.check(graph)
        self.assertTrue(any("names no node" in p for p in problems))

    def test_to_names_no_node(self):
        graph = {"nodes": [{"id": "a"}],
                 "edges": [{"from": "a", "to": "ghost",
                            "type": "requirement_to_implementation"}]}
        problems = agw.check(graph)
        self.assertTrue(any("names no node" in p for p in problems))


class TestEdgeCases(WriteHelper):
    """The contingency list named in the brief, one test each."""

    def test_cycle_is_fail(self):
        graph = complete_graph()
        graph["edges"].append(
            {"from": "n-claim", "to": "n-req",
             "type": "requirement_to_implementation"})
        problems = agw.check(graph)
        self.assertTrue(any("cycle" in p for p in problems), problems)

    def test_self_loop_is_fail(self):
        # All ten required edges stay present; only a self-loop is added,
        # so this isolates the cycle check from the required-edge check.
        graph = complete_graph()
        graph["edges"].append(
            {"from": "n-req", "to": "n-req",
             "type": "requirement_to_implementation"})
        problems = agw.check(graph)
        self.assertTrue(any("cycle" in p for p in problems), problems)

    def test_duplicate_edge_is_fail(self):
        graph = complete_graph()
        graph["edges"].append(dict(complete_edges()[0]))  # exact repeat
        problems = agw.check(graph)
        self.assertTrue(any("duplicate" in p for p in problems), problems)

    def test_unrecognised_edge_type_is_fail(self):
        graph = complete_graph()
        graph["edges"].append(
            {"from": "n-req", "to": "n-impl", "type": "teleports_to"})
        problems = agw.check(graph)
        self.assertTrue(
            any("unrecognised type" in p for p in problems), problems)

    def test_node_unreachable_to_terminal_is_fail(self):
        # A lane node hangs off n-check but never rejoins the shared
        # path to a terminal: exactly the "forks away" failure.
        graph = complete_graph()
        graph["nodes"].append({"id": "n-orphan", "type": "work_unit"})
        graph["edges"].append(
            {"from": "n-check", "to": "n-orphan",
             "type": "check_to_execution_environment"})
        problems = agw.check(graph)
        self.assertTrue(
            any("n-orphan" in p and "no path to a terminal" in p
                for p in problems),
            problems)

    def test_start_node_with_no_incoming_edge_is_fine(self):
        # n-req has zero incoming edges (it is the pipeline's entry
        # point) and the complete graph still passes: an unreached
        # source is not an orphan, only an un-reaching sink is.
        graph = complete_graph()
        incoming = {e["to"] for e in graph["edges"]}
        self.assertNotIn("n-req", incoming)
        self.assertEqual(agw.check(graph), [])

    def test_extra_unwired_lifecycle_node_still_fails(self):
        # A lifecycle stage the brief's prose names (decision) but that
        # no required edge type routes through: present in the record
        # with no wiring at all is exactly the fork-away-from-the-shared-
        # route failure, not a free pass because it's "just extra".
        graph = complete_graph()
        graph["nodes"].append({"id": "n-decision", "type": "decision"})
        problems = agw.check(graph)
        self.assertTrue(
            any("n-decision" in p and "no path to a terminal" in p
                for p in problems),
            problems)

    def test_valid_json_but_not_a_graph_is_nodata(self):
        path = self.write({"not": "a graph"})
        self.assertEqual(agw.main([path]), 2)

    def test_json_array_is_nodata(self):
        path = self.write("[1, 2, 3]")
        self.assertEqual(agw.main([path]), 2)

    def test_empty_graph_is_nodata(self):
        path = self.write({"nodes": [], "edges": []})
        self.assertEqual(agw.main([path]), 2)

    def test_nodes_with_zero_edges_is_fail_not_nodata(self):
        path = self.write({"nodes": complete_nodes(), "edges": []})
        self.assertEqual(agw.main([path]), 1)

    def test_unreadable_file_is_nodata(self):
        self.assertEqual(agw.main([os.path.join(self.tmp, "missing.json")]),
                          2)

    def test_malformed_json_is_nodata(self):
        path = self.write("{not json")
        self.assertEqual(agw.main([path]), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
