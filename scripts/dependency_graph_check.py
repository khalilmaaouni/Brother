#!/usr/bin/env python3
"""dependency_graph_check: validate a WBS-00.03 release dependency graph
record (docs/plan/1.0.17/DEPENDENCY-GRAPH.json's own shape).

WHY THIS EXISTS. Roadmap WBS-00.03 requires a release dependency
skeleton and states one rule about it: "No vertical lane may fork its
own evidence or lifecycle to avoid a dependency." A GANTT row asserting
the skeleton exists is not that skeleton (WBS-VERIFIED-BREAKDOWN-
2026-09-13.md item 9). This script checks the real artifact: every edge
names real nodes, the graph has no cycles, and -- the roadmap's own
invariant -- every node except the terminal node has a path to it, so no
lane can be present in the record while routing around the shared
Assurance Graph -> release-gates path.

Exit contract, matching this repo's other checkers:
  0  PASS      the graph is well-formed and every node reaches the terminal
  1  FAIL      one or more problems, each printed on its own line
  2  NO-DATA   the file could not be read as JSON

Python 3, standard library only. No network.
"""
import json
import sys

NODATA = "NO-DATA"
TERMINAL_ID = "release-gates"


def load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def check(record):
    """Returns a list of problem strings; empty means PASS."""
    problems = []
    nodes = record.get("nodes")
    edges = record.get("edges")

    if not isinstance(nodes, list) or not nodes:
        problems.append("nodes: must be a non-empty array")
        return problems
    if not isinstance(edges, list):
        problems.append("edges: must be an array")
        return problems

    node_ids = set()
    for n in nodes:
        if not isinstance(n, dict) or not isinstance(n.get("id"), str):
            problems.append("nodes: every entry needs a string 'id'")
            continue
        node_ids.add(n["id"])

    adjacency = {nid: [] for nid in node_ids}
    for e in edges:
        if not isinstance(e, dict):
            problems.append("edges: every entry must be an object")
            continue
        src, dst = e.get("from"), e.get("to")
        if src not in node_ids:
            problems.append("edges: 'from' %r names no node" % (src,))
            continue
        if dst not in node_ids:
            problems.append("edges: 'to' %r names no node" % (dst,))
            continue
        adjacency[src].append(dst)

    if problems:
        return problems

    if TERMINAL_ID not in node_ids:
        problems.append("nodes: no node named %r (the graph's terminal)"
                        % TERMINAL_ID)
        return problems

    # Cycle check: a plain DFS with a recursion-stack set. A dependency
    # graph with a cycle cannot be built in any order, so this is a
    # structural requirement, not a style preference.
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {nid: WHITE for nid in node_ids}

    def visit(nid, stack):
        color[nid] = GRAY
        for nxt in adjacency[nid]:
            if color[nxt] == GRAY:
                problems.append("edges: cycle detected through %r -> %r"
                                % (nid, nxt))
                return
            if color[nxt] == WHITE:
                visit(nxt, stack + [nid])
        color[nid] = BLACK

    for nid in node_ids:
        if color[nid] == WHITE:
            visit(nid, [])

    if problems:
        return problems

    # THE ROADMAP'S OWN INVARIANT: no lane may fork away from the shared
    # route. Checked as reachability to the terminal from every other
    # node, over the (now confirmed acyclic) edge set.
    reaches_terminal = set()

    def can_reach(nid, seen):
        if nid == TERMINAL_ID:
            return True
        if nid in seen:
            return False
        seen.add(nid)
        for nxt in adjacency[nid]:
            if can_reach(nxt, seen):
                return True
        return False

    for nid in node_ids:
        if nid == TERMINAL_ID:
            continue
        if can_reach(nid, set()):
            reaches_terminal.add(nid)
        else:
            problems.append(
                "invariant: node %r has no path to %r -- it forks away "
                "from the shared dependency route" % (nid, TERMINAL_ID))

    return problems


def _selftest():
    good = {
        "nodes": [{"id": "a"}, {"id": "b"}, {"id": TERMINAL_ID}],
        "edges": [{"from": "a", "to": "b"}, {"from": "b", "to": TERMINAL_ID}],
    }
    assert check(good) == [], check(good)

    cyclic = {
        "nodes": [{"id": "a"}, {"id": "b"}, {"id": TERMINAL_ID}],
        "edges": [
            {"from": "a", "to": "b"}, {"from": "b", "to": "a"},
            {"from": "a", "to": TERMINAL_ID},
        ],
    }
    problems = check(cyclic)
    assert any("cycle" in p for p in problems), problems

    forked = {
        "nodes": [{"id": "a"}, {"id": "b"}, {"id": TERMINAL_ID}],
        "edges": [{"from": "a", "to": TERMINAL_ID}],
    }
    problems = check(forked)
    assert any("forks away" in p for p in problems), problems

    bad_edge = {
        "nodes": [{"id": "a"}],
        "edges": [{"from": "a", "to": "nowhere"}],
    }
    problems = check(bad_edge)
    assert any("names no node" in p for p in problems), problems

    print("OK over 4 case(s)")
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv == ["--selftest"]:
        return _selftest()
    if len(argv) != 1:
        print("usage: dependency_graph_check.py <record.json> | --selftest")
        return 2

    try:
        record = load(argv[0])
    except (OSError, ValueError) as exc:
        print("dependency_graph_check: %s: %s" % (NODATA, exc))
        return 2

    if not isinstance(record, dict):
        print("dependency_graph_check: %s: not a JSON object" % NODATA)
        return 2

    problems = check(record)
    if problems:
        for p in problems:
            print("dependency_graph_check: FAIL: %s" % p)
        print("dependency_graph_check: FAIL %d problem(s) in %s"
              % (len(problems), argv[0]))
        return 1
    print("dependency_graph_check: PASS %s" % argv[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
