"""ORCH-02: orchestrator routing metadata survives the whole spine.

WHAT THIS PROVES. A unit carries routing metadata (task_class, worker_profile,
review_profile, risk_class, evidence_obligation, the two retry counts,
leaf_worker_only) that decides which model runs the work, who reviews it, and
whether a NO-DATA blocks a merge. This run's advisory pass found and this
session verified five real places that dropped it: work_record._row_from_unit
(the Work row is built without them), graph_loop.nodes (rebuilds every node
from a fixed key list that never held them), loop_bridge.run_node (builds the
worker's brief from another fixed key list, and used to overwrite risk_class
and attempt with "normal" and 1 unconditionally), model_worker.build_prompt
(reads the brief defensively but never rendered these fields even when they
arrived), and integrate.integrate_one (the evidence dict and the returned
result are both fixed shapes). This test drives all five real functions, in
the real order production code calls them, with a distinct sentinel per
field, and asserts the exact sentinel survives at every stage.

THE BAD STATE A GREEN RUN WOULD ALSO PASS: a test where each stage is
stubbed with a hand-built dict shaped like that stage's real output. The
sentinels would flow through the test's OWN fixtures and never through
work_record.create, graph_loop.nodes, loop_bridge.run_node,
model_worker.build_prompt or integrate.integrate_one, so the five real drop
points named above would never get a chance to drop anything, and the test
would prove only that a dict can hold whatever you put in it. Defended
against by calling the five real functions directly (never reimplementing
their logic here) and by asserting on values only those real functions can
produce and a hand-built fixture could not: work_record.independence_for's
own "unverified" default (unreachable without running check_units and
_row_from_unit for real), graph_loop.nodes' own "declared" flag (computed
from `owns is not None`, not copied from any sentinel), loop_bridge.run_node's
own record shape (worker_status/scope/integrable, populated only inside
run_node's real body, never by a caller), and integrate.integrate_one's own
git merge actually advancing the repository's HEAD with the done_check
actually run as a real subprocess against the merged tree.

Python 3.9, standard library only. Runs real git subprocesses in temp
directories; no network.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import work_record  # noqa: E402
import graph_loop  # noqa: E402
import loop_bridge  # noqa: E402
import model_worker  # noqa: E402
import integrate  # noqa: E402
import worktree_lane  # noqa: E402

try:
    import tmp_sandbox
    tmp_sandbox.install()
except ImportError:
    # A packager can copy this test without scripts/tmp_sandbox.py beside
    # it. Say so rather than dying: the sandbox is hygiene, not the
    # subject of this test.
    sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % os.path.basename(__file__))


# Every sentinel is distinct and unmistakable: no default, fallback or
# other real value in the five modules under test could ever equal one of
# these by accident.
S_TASK_CLASS = "SENTINEL-task-class-7f3a"
S_WORKER_PROFILE = "SENTINEL-worker-profile-9c1d"
S_REVIEW_PROFILE = "SENTINEL-review-profile-2b6e"
S_RISK_CLASS = "SENTINEL-risk-class-e04f"
S_EVIDENCE_OBLIGATION = "SENTINEL-evidence-obligation-a83c"
S_MAX_OUTER_ATTEMPTS = 733019
S_MAX_REPAIR_ATTEMPTS = 911037
S_ATTEMPT = 42042
S_LEAF_WORKER_ONLY = True

ROUTING_METADATA_FIELDS = (
    "task_class", "worker_profile", "review_profile", "risk_class",
    "evidence_obligation", "max_outer_attempts", "max_repair_attempts",
    "leaf_worker_only",
)

SENTINEL_VALUES = {
    "task_class": S_TASK_CLASS,
    "worker_profile": S_WORKER_PROFILE,
    "review_profile": S_REVIEW_PROFILE,
    "risk_class": S_RISK_CLASS,
    "evidence_obligation": S_EVIDENCE_OBLIGATION,
    "max_outer_attempts": S_MAX_OUTER_ATTEMPTS,
    "max_repair_attempts": S_MAX_REPAIR_ATTEMPTS,
    "leaf_worker_only": S_LEAF_WORKER_ONLY,
}


def _git(args, cwd):
    proc = subprocess.run(["git"] + list(args), cwd=cwd,
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError("git %s failed in %s: %s"
                          % (" ".join(args), cwd, proc.stderr))
    return proc.stdout.strip()


def _canonical_repo():
    """A real one-commit git repository, the same shape test_integrate.py's
    own canon() builds, kept local rather than imported: importing another
    test module's private helper would couple two test files that do not
    otherwise know about each other."""
    d = tempfile.mkdtemp(prefix="orch-spine-canon-")
    _git(["init", "-q", "-b", "main"], d)
    _git(["config", "user.email", "a@b.c"], d)
    _git(["config", "user.name", "t"], d)
    with open(os.path.join(d, "seed.txt"), "w", encoding="utf-8") as fh:
        fh.write("seed\n")
    _git(["add", "-A"], d)
    _git(["commit", "-q", "-m", "R0"], d)
    return d


class FakeVerify(object):
    """Always PASS. This unit tests metadata carrying, not the verify
    module's own behaviour, which has its own test file."""

    def verify(self, unit, cwd=None):
        return {"verdict": "PASS", "reason": "fake, real behaviour lives in "
                                             "test_loop_bridge.py"}

    def is_pass(self, result):
        return result.get("verdict") == "PASS"


class FakeRepair(object):
    """Never expected to run: FakeVerify always reports PASS. Raising here
    (rather than quietly succeeding) turns a silent regression into a loud
    one if some future change ever sends this test's unit to repair."""

    def repair(self, unit, verdict, worker, cwd=None, max_attempts=3):
        raise AssertionError(
            "repair.repair() was called; FakeVerify always reports PASS, so "
            "this path should be unreachable in this test")


class BriefCapturingWorker(object):
    """Stands in for a spawned model process: this unit does not test
    spawning a real model (test_model_worker.py and test_loop_bridge.py
    already do). It records the exact dict loop_bridge.run_node() built and
    handed to it, unmodified, so model_worker.build_prompt() below runs
    against the real production brief rather than a hand-built one."""

    def __init__(self):
        self.briefs = []

    def run(self, unit, cwd=None):
        self.briefs.append(dict(unit))
        return {"worker_claim": "sentinel worker ran", "artifacts": [],
                "status": "returned", "cost": {"tokens": 0, "minutes": 0}}


def _parts():
    return {"spawn": None, "verify": FakeVerify(), "repair": FakeRepair()}


class RoutingMetadataSurvivesTheWholeSpine(unittest.TestCase):
    """The unit this WBS entry exists for: inject a sentinel per field at
    work_record, and prove the exact value reaches the model's prompt and
    the integration record, through the real functions of every stage in
    between."""

    def setUp(self):
        self.repo = _canonical_repo()
        self.addCleanup(shutil.rmtree, self.repo, ignore_errors=True)

    def test_sentinels_survive_work_record_through_integration(self):
        # STAGE 1: work_record.create(), the FIRST drop point. A sentinel
        # per routing field, on a unit that also satisfies the ordinary
        # contract (id, done_check, a declared, in-tree write scope).
        units = [{
            "id": "SPINE-1",
            "done_check": "test -f spine_marker.txt",
            "owns": ["spine_marker.txt"],
            "task_class": S_TASK_CLASS,
            "worker_profile": S_WORKER_PROFILE,
            "review_profile": S_REVIEW_PROFILE,
            "risk_class": S_RISK_CLASS,
            "evidence_obligation": S_EVIDENCE_OBLIGATION,
            "max_outer_attempts": S_MAX_OUTER_ATTEMPTS,
            "max_repair_attempts": S_MAX_REPAIR_ATTEMPTS,
            "leaf_worker_only": S_LEAF_WORKER_ONLY,
        }]
        record, problems = work_record.create(
            "prove routing metadata survives the whole spine", units)
        self.assertEqual(problems, [], "the unit should satisfy the "
                         "ordinary work_record contract with no problems")
        self.assertIsNotNone(record)
        self.assertEqual(len(record["rows"]), 1)
        row = record["rows"][0]
        # Real-function-only fact: independence_for() is computed, not
        # copied, and "unverified" is what it returns for an unset
        # oracle_source. A hand-built fixture would have no reason to
        # produce this exact string unless it re-derived the same rule.
        self.assertEqual(row["independence"], "unverified")
        for field in ROUTING_METADATA_FIELDS:
            self.assertIn(field, row, "work_record dropped %r at row "
                          "creation, the first of the five known drop "
                          "points" % field)
            self.assertEqual(row[field], SENTINEL_VALUES[field],
                             "work_record altered %r instead of carrying "
                             "it through unchanged" % field)

        # STAGE 2: graph_loop.nodes(), the SECOND drop point. Rows and
        # features feed the same scheduler, so a Work document's own
        # `rows` list is handed to nodes() exactly as brother_run.py's
        # graph-loop callers already do.
        nodes = graph_loop.nodes({"rows": record["rows"], "features": []})
        self.assertEqual(len(nodes), 1)
        node = nodes[0]
        # Real-function-only fact: 'declared' is computed from `owns is
        # not None`, never copied from a sentinel.
        self.assertTrue(node["declared"])
        for field in ROUTING_METADATA_FIELDS:
            self.assertIn(field, node, "graph_loop.nodes() dropped %r "
                          "rebuilding the node from its fixed key list, "
                          "the second of the five known drop points"
                          % field)
            self.assertEqual(node[field], SENTINEL_VALUES[field],
                             "graph_loop.nodes() altered %r" % field)

        # A real dispatch loop stamps a node with the attempt it is
        # actually on before a repeat dispatch (loop_bridge.run_node and
        # context_capsule.py both already read node.get("attempt"); this
        # is the one field in this test that is not part of the
        # orchestrator-task-v1 schema work_record validates, since it
        # names a live retry count rather than a declared unit property).
        node["attempt"] = S_ATTEMPT

        # STAGE 3: loop_bridge.run_node(), the THIRD drop point, and the
        # one this unit most exists for: it used to overwrite risk_class
        # and attempt with "normal" and 1 unconditionally. cwd=None here
        # on purpose: run_node's git-scope audit is exercised for real by
        # test_loop_bridge.py, and this test's subject is metadata
        # carrying, not scope auditing.
        worker = BriefCapturingWorker()
        result = loop_bridge.run_node(node, _parts(), worker, cwd=None)
        # Real-function-only fact: this exact shape (worker_status, scope,
        # integrable) is populated inside run_node's own body; no caller
        # builds it.
        self.assertIn("worker_status", result)
        self.assertIn("scope", result)
        self.assertEqual(result["verdict"], "PASS")
        self.assertEqual(len(worker.briefs), 1,
                         "the fake worker should have been dispatched "
                         "exactly once")
        brief = worker.briefs[0]

        # THE ASSERTION THIS UNIT MOST EXISTS FOR.
        self.assertEqual(brief["risk_class"], S_RISK_CLASS,
                         "loop_bridge.run_node overwrote risk_class with "
                         "its own hardcoded 'normal' instead of carrying "
                         "the node's real value")
        self.assertEqual(brief["attempt"], S_ATTEMPT,
                         "loop_bridge.run_node overwrote attempt with its "
                         "own hardcoded 1 instead of carrying the node's "
                         "real value")
        for field in ("task_class", "worker_profile", "review_profile",
                     "evidence_obligation", "max_outer_attempts",
                     "max_repair_attempts", "leaf_worker_only"):
            self.assertIn(field, brief, "loop_bridge.run_node dropped %r "
                          "building the worker's brief from its own fixed "
                          "key list" % field)
            self.assertEqual(brief[field], SENTINEL_VALUES[field],
                             "loop_bridge.run_node altered %r" % field)

        # STAGE 4: model_worker.build_prompt(), the FOURTH drop point.
        # Receiving a field on the brief and expressing it in the text the
        # model actually reads are two different facts; this brief just
        # proved the first, so this checks the second, separately.
        prompt = model_worker.build_prompt(brief)
        self.assertIn(S_TASK_CLASS, prompt)
        self.assertIn(S_RISK_CLASS, prompt)
        self.assertIn(S_WORKER_PROFILE, prompt)
        self.assertIn(S_REVIEW_PROFILE, prompt)
        self.assertIn(S_EVIDENCE_OBLIGATION, prompt)
        self.assertIn(str(S_MAX_OUTER_ATTEMPTS), prompt)
        self.assertIn(str(S_MAX_REPAIR_ATTEMPTS), prompt)
        self.assertIn(str(S_ATTEMPT), prompt,
                     "the real attempt count never reached the rendered "
                     "prompt text")
        self.assertIn("leaf-worker-only", prompt.lower(),
                     "leaf_worker_only=True never reached the rendered "
                     "prompt text")

        # STAGE 5: integrate.integrate_one(), the FIFTH drop point. The
        # real production caller (loop_bridge.py's rolling_run) hands
        # integrate_one the graph_loop NODE, not the worker's brief (the
        # brief is model_worker's contract; the node is integrate's), so
        # this stage is driven from `node`, matching that real wiring.
        lanes = worktree_lane.Lanes(self.repo, ["SPINE-1"])
        self.assertTrue(lanes.isolated, lanes.why())
        lane_path = lanes.path_for("SPINE-1")
        with open(os.path.join(lane_path, "spine_marker.txt"), "w",
                 encoding="utf-8") as fh:
            fh.write("proof\n")
        _git(["add", "-A"], lane_path)
        _git(["commit", "-q", "-m", "SPINE-1"], lane_path)
        branch = worktree_lane.branch_for("SPINE-1")

        before_head = _git(["rev-parse", "HEAD"], self.repo)
        outcome = integrate.integrate_one(self.repo, branch, node)
        # Real-function-only fact: canonical's HEAD actually moved and the
        # done_check actually ran as a subprocess against the merged tree
        # (exit_code 0 because spine_marker.txt genuinely exists there
        # now), neither of which a hand-built fixture could produce.
        self.assertEqual(outcome["verdict"], integrate.INTEGRATED,
                         outcome.get("reason"))
        after_head = _git(["rev-parse", "HEAD"], self.repo)
        self.assertNotEqual(before_head, after_head,
                           "integrate_one reported INTEGRATED but "
                           "canonical's HEAD never moved")
        self.assertEqual(outcome["evidence"]["exit_code"], 0)
        self.assertTrue(
            os.path.isfile(os.path.join(self.repo, "spine_marker.txt")))

        for field in ROUTING_METADATA_FIELDS:
            self.assertIn(field, outcome["evidence"],
                          "integrate.integrate_one's evidence dict dropped "
                          "%r, the fourth of the five known drop points"
                          % field)
            self.assertEqual(outcome["evidence"][field],
                             SENTINEL_VALUES[field])
            self.assertIn(field, outcome,
                          "integrate.integrate_one's own result dict "
                          "dropped %r, the fifth of the five known drop "
                          "points" % field)
            self.assertEqual(outcome[field], SENTINEL_VALUES[field])


class ANodeWithoutTheNewFieldsIsUnchanged(unittest.TestCase):
    """Backward compatibility: a node, row, brief or unit that never
    carried any of this metadata must behave exactly as it did before
    ORCH-02, at every one of the five stages."""

    def setUp(self):
        self.repo = _canonical_repo()
        self.addCleanup(shutil.rmtree, self.repo, ignore_errors=True)

    def test_work_record_row_gains_no_keys_when_none_were_given(self):
        units = [{"id": "PLAIN-1", "done_check": "true", "owns": ["x"]}]
        record, problems = work_record.create("plain unit", units)
        self.assertEqual(problems, [])
        row = record["rows"][0]
        for field in ROUTING_METADATA_FIELDS:
            self.assertNotIn(field, row)

    def test_graph_loop_node_gains_no_keys_when_the_row_had_none(self):
        nodes = graph_loop.nodes({"rows": [{"id": "PLAIN-1",
                                            "done_check": "true",
                                            "owns": ["x"]}],
                                  "features": []})
        node = nodes[0]
        for field in ROUTING_METADATA_FIELDS:
            self.assertNotIn(field, node)

    def test_run_node_keeps_its_old_fallback_when_the_node_has_none(self):
        node = {"id": "PLAIN-1", "done_check": "true", "owns": ["x"]}
        worker = BriefCapturingWorker()
        loop_bridge.run_node(node, _parts(), worker, cwd=None)
        brief = worker.briefs[0]
        self.assertEqual(brief["risk_class"], "normal")
        self.assertEqual(brief["attempt"], 1)
        for field in ("task_class", "worker_profile", "review_profile",
                     "evidence_obligation", "max_outer_attempts",
                     "max_repair_attempts", "leaf_worker_only"):
            self.assertNotIn(field, brief)

    def test_build_prompt_renders_no_new_lines_when_the_brief_has_none(self):
        prompt = model_worker.build_prompt({"id": "PLAIN-1",
                                            "objective": "plain",
                                            "done_check": "true"})
        for label in ("Task class:", "Risk class:", "Worker profile:",
                     "Review profile:", "Evidence obligation:",
                     "Max outer attempts", "Max repair attempts",
                     "leaf-worker-only"):
            self.assertNotIn(label, prompt)

    def test_integrate_evidence_and_result_gain_no_keys_when_the_unit_has_none(self):
        lanes = worktree_lane.Lanes(self.repo, ["PLAIN-1"])
        self.assertTrue(lanes.isolated, lanes.why())
        lane_path = lanes.path_for("PLAIN-1")
        with open(os.path.join(lane_path, "plain_marker.txt"), "w",
                 encoding="utf-8") as fh:
            fh.write("proof\n")
        _git(["add", "-A"], lane_path)
        _git(["commit", "-q", "-m", "PLAIN-1"], lane_path)
        branch = worktree_lane.branch_for("PLAIN-1")
        unit = {"id": "PLAIN-1", "done_check": "test -f plain_marker.txt"}
        outcome = integrate.integrate_one(self.repo, branch, unit)
        self.assertEqual(outcome["verdict"], integrate.INTEGRATED,
                         outcome.get("reason"))
        for field in ROUTING_METADATA_FIELDS:
            self.assertNotIn(field, outcome["evidence"])
            self.assertNotIn(field, outcome)


if __name__ == "__main__":
    unittest.main()
