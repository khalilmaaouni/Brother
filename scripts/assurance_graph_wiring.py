#!/usr/bin/env python3
"""assurance_graph_wiring: validate DOM-10.01's assurance graph record --
the whole-lifecycle graph (intent, requirement, decision, work unit, files,
claim, check, evidence, integration, review, acceptance, release, observed
outcome, recorded lesson), never scripts/dependency_graph_check.py's
narrower WBS-00.03 release-dependency skeleton and never
scripts/assurance_graph.py's per-claim failure-mode/control/check VIEW.
Those two already exist; this is the third, broader thing: the row's own
words are "a deliberately removed edge turns the check RED", checked here
by requiring ten named edge types to each be present, addressable, and on
a path to a real terminal.

THE DECIDING PROPERTY (the row's own words): a deliberately removed edge
turns the check RED. Mutation proof: take a complete graph, delete one
edge of a required type, watch validate() report FAIL naming that exact
edge type as missing; put it back, watch PASS return. See
test_assurance_graph_wiring.py's test_removing_each_required_edge_fails,
which does this once per required type, and _selftest() below, which does
it live at import/run time as this module's own mutation proof.

GRAPH SHAPE. nodes: a list of {"id": str, "type": str}. edges: a list of
{"from": node id, "to": node id, "type": edge type}. Both from/to must
name a real node (machine addressable, per the brief): an edge naming a
node that does not exist is a FAIL, never silently skipped, mirroring
dependency_graph_check.py's own "names no node" rule.

EDGE TYPE VOCABULARY IS CLOSED, deliberately, unlike node "type" (which
this validator never restricts, since node vocabulary was open-ended in
the brief and nothing depends on checking it). Every edge's "type" must be
one of the ten REQUIRED_EDGE_TYPES below -- there is no unrestricted extra
edge type in this record's contract, so anything else is the "edge of an
unrecognised type" case the brief names, and it is a FAIL, not a silent
pass-through: a typo'd edge type would otherwise never satisfy its
required slot and the validator would misreport which one is missing.

TERMINAL SET, and why it is a set, not one node like
dependency_graph_check.py's single TERMINAL_ID. This graph has three
legitimate sinks, not one: the normal completion path ends at an
observed_outcome node (release -> observed_outcome); the failure path
continues one edge further to a recorded_lesson node
(failed_outcome_to_recorded_lesson, where the failed observed_outcome IS
the failed_outcome); and check_to_execution_environment branches off the
main claim -> ... -> release chain into a descriptive fact about WHERE a
check ran, not a further step in the causal chain -- an execution
environment node is not itself a lane that should lead anywhere else, the
same way recorded_lesson is not. Treating only observed_outcome as
terminal would wrongly fail every graph that records which environment a
check ran in, or that records a lesson from a failure, both of which are
exactly the information this graph exists to carry. So: a node reaches
"the terminal" if it can reach ANY node whose type is in
TERMINAL_NODE_TYPES = {"observed_outcome", "recorded_lesson",
"execution_environment"}.

REACHABILITY IS ONE DIRECTION ONLY: forward, to a terminal, exactly like
dependency_graph_check.py's own invariant ("no lane may fork away from the
shared route"). Backward reachability from a "start" is NOT enforced: a
node with zero incoming edges (an intent node nobody points to, an entry
point) is normal, not a defect -- it is the source, not an orphan. Only a
node that CANNOT REACH a terminal (an out-degree-0 node partway through
the pipeline, or a lane that dead-ends before release) is a defect, since
that is the "present in the record while routing around the shared path"
failure this row forbids. See test_start_node_with_no_incoming_edge_is_fine
for the positive case this asymmetry protects.

EMPTY VS ZERO-EDGE, why drawn where it is (matches the brief's own line
verbatim). No nodes at all is NO-DATA: there is nothing to say a PASS or
FAIL about, exactly the same reasoning dependency_graph_check.py already
uses for a record that fails to parse. Nodes present but zero edges is a
REAL negative answer, not a missing one -- with no edges, every node
(other than a lone terminal-typed node) trivially fails to reach a
terminal and every required edge type is trivially absent, so it falls
out of the ordinary FAIL path with no special case, and is reported that
way (FAIL, naming all ten missing required edge types), never NO-DATA.

Exit codes, matching this repo's other checkers: 0 PASS, 1 FAIL,
2 NO-DATA.

Python 3, standard library only. No network.
"""
import json
import sys

NODATA = "NO-DATA"

#: The ten edges the row requires. Order matters only for stable output.
REQUIRED_EDGE_TYPES = (
    "requirement_to_implementation",
    "implementation_to_claim",
    "claim_to_deciding_check",
    "check_to_execution_environment",
    "check_to_evidence",
    "evidence_to_receipt",
    "receipt_to_acceptance",
    "acceptance_to_release",
    "release_to_observed_outcome",
    "failed_outcome_to_recorded_lesson",
)

#: Closed vocabulary: see the "EDGE TYPE VOCABULARY IS CLOSED" note above.
RECOGNIZED_EDGE_TYPES = frozenset(REQUIRED_EDGE_TYPES)

#: A node of any of these types ends the graph legitimately. See
#: "TERMINAL SET" above for why there are three, not one.
TERMINAL_NODE_TYPES = frozenset(
    {"observed_outcome", "recorded_lesson", "execution_environment"})


def load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _is_graph_shaped(record):
    """True only if `record` is even the right shape to run check() on.
    Anything else (not a dict, no 'nodes' list, or an empty 'nodes' list)
    is the "valid JSON but not a graph" / "empty graph" case: NO-DATA,
    never FAIL, because there is nothing here to say PASS or FAIL about."""
    if not isinstance(record, dict):
        return False
    nodes = record.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        return False
    return True


def check(record):
    """Returns a list of problem strings; empty means PASS. Caller must
    have already confirmed _is_graph_shaped(record) -- this never returns
    the NO-DATA case itself, matching dependency_graph_check.check()'s
    same division of labor with its own main()."""
    problems = []
    nodes = record["nodes"]
    edges = record.get("edges")
    if edges is None:
        edges = []  # missing 'edges' key: a real answer of zero edges.
    if not isinstance(edges, list):
        problems.append("edges: must be an array")
        return problems

    node_ids = set()
    node_type = {}
    for n in nodes:
        if not isinstance(n, dict) or not isinstance(n.get("id"), str):
            problems.append("nodes: every entry needs a string 'id'")
            continue
        node_ids.add(n["id"])
        node_type[n["id"]] = n.get("type")

    if problems:
        return problems

    adjacency = {nid: [] for nid in node_ids}
    seen_triples = set()
    edges_by_type = {t: [] for t in REQUIRED_EDGE_TYPES}
    for e in edges:
        if not isinstance(e, dict):
            problems.append("edges: every entry must be an object")
            continue
        src, dst, etype = e.get("from"), e.get("to"), e.get("type")
        if src not in node_ids:
            problems.append("edges: 'from' %r names no node" % (src,))
            continue
        if dst not in node_ids:
            problems.append("edges: 'to' %r names no node" % (dst,))
            continue
        if etype not in RECOGNIZED_EDGE_TYPES:
            problems.append(
                "edges: %r -> %r has unrecognised type %r"
                % (src, dst, etype))
            continue
        triple = (src, dst, etype)
        if triple in seen_triples:
            problems.append(
                "edges: duplicate edge %r -> %r of type %r"
                % (src, dst, etype))
            continue
        seen_triples.add(triple)
        adjacency[src].append(dst)
        edges_by_type[etype].append((src, dst))

    if problems:
        return problems

    # Every required edge type must appear at least once, addressably.
    # THE DECIDING PROPERTY lives here: delete the last edge of a type
    # and this loop is what turns red.
    for etype in REQUIRED_EDGE_TYPES:
        if not edges_by_type[etype]:
            problems.append(
                "wiring: no edge of required type %r" % (etype,))

    if problems:
        return problems

    # Cycle check (also catches a self-loop: a node is its own neighbour
    # while still GRAY). A pipeline with a cycle cannot have run in any
    # order, so this is structural, not a style preference.
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {nid: WHITE for nid in node_ids}

    def visit(nid):
        color[nid] = GRAY
        for nxt in adjacency[nid]:
            if color[nxt] == GRAY:
                problems.append(
                    "edges: cycle detected through %r -> %r" % (nid, nxt))
                return
            if color[nxt] == WHITE:
                visit(nxt)
        color[nid] = BLACK

    for nid in node_ids:
        if color[nid] == WHITE:
            visit(nid)

    if problems:
        return problems

    # Forward reachability to a terminal. See "REACHABILITY IS ONE
    # DIRECTION ONLY" above: no start-side check, a terminal-typed node
    # trivially reaches itself.
    def reaches_terminal(nid, seen):
        if node_type.get(nid) in TERMINAL_NODE_TYPES:
            return True
        if nid in seen:
            return False
        seen.add(nid)
        return any(reaches_terminal(nxt, seen) for nxt in adjacency[nid])

    for nid in sorted(node_ids):
        if not reaches_terminal(nid, set()):
            problems.append(
                "invariant: node %r has no path to a terminal node "
                "(%s) -- it forks away from the shared route"
                % (nid, " or ".join(sorted(TERMINAL_NODE_TYPES))))

    return problems


def _selftest():
    """Live mutation proof, run by --selftest: build a complete minimal
    graph, confirm PASS, then remove one required edge and confirm FAIL
    names it, for every required edge type in turn."""
    def base_nodes():
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

    def base_edges():
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

    complete = {"nodes": base_nodes(), "edges": base_edges()}
    assert check(complete) == [], check(complete)

    for etype in REQUIRED_EDGE_TYPES:
        mutated_edges = [e for e in base_edges() if e["type"] != etype]
        mutated = {"nodes": base_nodes(), "edges": mutated_edges}
        problems = check(mutated)
        assert any(etype in p for p in problems), (etype, problems)

    print("OK: mutation proof over %d required edge type(s)"
          % len(REQUIRED_EDGE_TYPES))
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv == ["--selftest"]:
        return _selftest()
    if len(argv) != 1:
        print("usage: assurance_graph_wiring.py <record.json> | --selftest")
        return 2

    try:
        record = load(argv[0])
    except (OSError, ValueError) as exc:
        print("assurance_graph_wiring: %s: %s" % (NODATA, exc))
        return 2

    if not _is_graph_shaped(record):
        print("assurance_graph_wiring: %s: not a graph record "
              "(missing or empty 'nodes')" % NODATA)
        return 2

    problems = check(record)
    if problems:
        for p in problems:
            print("assurance_graph_wiring: FAIL: %s" % p)
        print("assurance_graph_wiring: FAIL %d problem(s) in %s"
              % (len(problems), argv[0]))
        return 1
    print("assurance_graph_wiring: PASS %s" % argv[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
