#!/usr/bin/env python3
"""Tests for ORCH-07, scripts/orchestrators/semantic.py: the semantic-role
orchestrator adapter whose live invocation has not been measured (unlike
its sibling, scripts/orchestrators/execution.py, driven against the
app-bundled Codex binary). Every attempt in this suite is driven against a
FAKE subprocess runner or a stub environment mapping, never a real outside
model: fast, offline, deterministic.

THE BAD STATE THIS SUITE IS BUILT TO CATCH: a fake runner that always
returns a valid-looking, schema-passing action or a well-formed
decomposition proposal no matter what SemanticAdapter actually sent it.
Such a runner would make every "does invoke()/propose_decomposition()
return ok" test pass while the adapter's own configuration check, deadline
enforcement, task-id check and child validation did nothing at all; the
adapter would be a pass-through, not a gate. Two defenses, both used
below, mirroring test_orchestrator_codex.py's own approach for its sibling:

  (a) _fake_runner RECORDS every call it receives (argv, kwargs), and
      several tests assert on that recorded data
      (test_run_attempt_reads_the_configured_command,
      test_retry_uses_two_distinct_budgets), proving the adapter actually
      threaded a real, distinct invocation through to the runner rather
      than the test only checking a canned return value.
  (b) Several tests configure the runner to return GARBAGE (prose,
      malformed JSON, an out-of-vocabulary action, a mismatched task_id,
      a decomposition proposal missing a required child field) and assert
      the adapter refuses it. A suite with only the agreeable runner would
      never exercise the refusal path at all.

This suite does not re-prove base.OrchestratorAdapter's own generic
retry-once-never-identically and classification machinery in isolation:
that is already test_orchestrator_codex.py's ClassificationTests,
RetryTests and CheckRetryAllowedTests, exercised there against a bare
StubAdapter, and this unit's contract says not to duplicate that suite.
What this suite proves instead is that SemanticAdapter wires into that
shared machinery correctly, AND the one thing only this role needs: the
configuration-is-mandatory rule and decomposition proposal validation.
"""
import json
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from orchestrators import base  # noqa: E402
from orchestrators import semantic  # noqa: E402


#: One clean, schema-valid NOOP envelope, the one shape of document this
#: whole module is willing to call ok for the standard action path.
VALID_ACTION = {
    "schema_version": "orchestrator-event-v1",
    "run_id": "run-1",
    "orchestrator": "semantic-adapter-test",
    "instance": "inst-1",
    "epoch": 0,
    "at": "2026-09-18T00:00:00Z",
    "action": "NOOP",
    "reason": "no ready work this tick",
    "observed_revision": "deadbeef",
    "observed_journal_seq": 0,
}

#: A task-scoped action, used for the task_id-mismatch tests.
TASK_SCOPED_ACTION = {
    "schema_version": "orchestrator-event-v1",
    "run_id": "run-1",
    "orchestrator": "semantic-adapter-test",
    "instance": "inst-1",
    "epoch": 0,
    "at": "2026-09-18T00:00:00Z",
    "action": "PARK",
    "task_id": "ORCH-99",
    "reason": "waiting on a dependency",
    "observed_revision": "deadbeef",
    "observed_journal_seq": 0,
    "observed_task_state": "READY",
    "observed_attempt": 1,
}


class _FakeCompleted(object):
    def __init__(self, stdout):
        self.stdout = stdout


def _fake_runner(responses):
    """A subprocess.run-shaped stand-in. Pops one entry per call: an
    Exception instance is raised, anything else is wrapped as stdout on a
    fake CompletedProcess. Records every call it received so a test can
    assert on the exact argv and prompt SemanticAdapter built, with no
    process spawned and no real outside model reached."""
    calls = []

    def runner(argv, **kwargs):
        calls.append({"argv": argv, "kwargs": kwargs})
        if not responses:
            raise AssertionError(
                "fake runner called %d time(s), more than the test "
                "configured responses for" % (len(calls),))
        item = responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return _FakeCompleted(item)

    runner.calls = calls
    return runner


class ConfigurationTests(unittest.TestCase):
    """SEMANTIC_ORCHESTRATOR_CMD absent, empty, or whitespace: health()
    must report UNAVAILABLE and name why, and invoke() must raise rather
    than return anything, because this estate never falls back to the
    execution role's model for a semantic decision."""

    def test_semantic_cmd_is_none_when_variable_absent(self):
        self.assertIsNone(semantic.semantic_cmd({}))

    def test_semantic_cmd_is_none_when_variable_empty(self):
        self.assertIsNone(
            semantic.semantic_cmd({semantic.SEMANTIC_ORCHESTRATOR_CMD: ""}))

    def test_semantic_cmd_is_none_when_variable_whitespace_only(self):
        self.assertIsNone(semantic.semantic_cmd(
            {semantic.SEMANTIC_ORCHESTRATOR_CMD: "   \t  "}))

    def test_semantic_cmd_splits_a_configured_command(self):
        cmd = semantic.semantic_cmd(
            {semantic.SEMANTIC_ORCHESTRATOR_CMD: "/bin/echo --json"})
        self.assertEqual(cmd, ["/bin/echo", "--json"])

    def test_health_is_unavailable_and_never_healthy_when_not_configured(self):
        adapter = semantic.SemanticAdapter(env={})
        self.assertEqual(adapter.health(), "UNAVAILABLE")
        self.assertNotEqual(adapter.health(), "HEALTHY")
        self.assertIn("not configured", adapter.health_reason())

    def test_health_names_a_reason_for_a_nonexistent_configured_path(self):
        adapter = semantic.SemanticAdapter(
            cmd=["/no/such/semantic/binary"])
        self.assertEqual(adapter.health(), "UNAVAILABLE")
        self.assertIn("does not resolve", adapter.health_reason())

    def test_health_is_starting_for_a_resolvable_command(self):
        adapter = semantic.SemanticAdapter(cmd=["/bin/echo"])
        self.assertEqual(adapter.health(), "STARTING")
        self.assertNotEqual(adapter.health(), "HEALTHY")

    def test_invoke_raises_when_not_configured(self):
        adapter = semantic.SemanticAdapter(env={})
        with self.assertRaises(base.AdapterRefused):
            adapter.invoke({"unit_id": "ORCH-99"}, deadline_s=5, now=0)

    def test_propose_decomposition_raises_when_not_configured(self):
        adapter = semantic.SemanticAdapter(env={})
        with self.assertRaises(base.AdapterRefused):
            adapter.propose_decomposition(
                {"unit_id": "ORCH-99"}, deadline_s=5, now=0)

    def test_invoke_raises_on_nonpositive_deadline_even_if_configured(self):
        adapter = semantic.SemanticAdapter(cmd=["/bin/echo"])
        with self.assertRaises(base.AdapterRefused):
            adapter.invoke({"unit_id": "X"}, deadline_s=0, now=0)
        with self.assertRaises(base.AdapterRefused):
            adapter.invoke({"unit_id": "X"}, deadline_s=-3, now=0)

    def test_propose_decomposition_raises_on_nonpositive_deadline(self):
        adapter = semantic.SemanticAdapter(cmd=["/bin/echo"])
        with self.assertRaises(base.AdapterRefused):
            adapter.propose_decomposition({"unit_id": "X"}, deadline_s=0, now=0)


class LaunchFailureTests(unittest.TestCase):
    """A command that IS configured but cannot be launched (bad path, not
    executable) is classified, never crashed and never mistaken for 'not
    configured'."""

    def test_run_attempt_missing_binary_is_tool_unavailable_not_a_raise(self):
        runner = _fake_runner([OSError("no such file or directory")])
        adapter = semantic.SemanticAdapter(
            cmd=["/no/such/semantic/binary"], runner=runner)
        result = adapter._run_attempt(
            {"unit_id": "X"}, budget_s=5, attempt_number=1, prior_note=None)
        self.assertFalse(result["timed_out"])
        self.assertEqual(result["failure_class"], "tool_unavailable")

    def test_invoke_end_to_end_reports_tool_unavailable(self):
        runner = _fake_runner(
            [OSError("no such file or directory"),
             OSError("no such file or directory")])
        adapter = semantic.SemanticAdapter(
            cmd=["/no/such/semantic/binary"], runner=runner)
        result = adapter.invoke({"unit_id": "X"}, deadline_s=5, now=0)
        self.assertFalse(result.ok)
        self.assertEqual(result.failure_class, "tool_unavailable")


class InvokeWiringTests(unittest.TestCase):
    """SemanticAdapter's own _run_attempt/_classify wired through the
    inherited invoke(): a valid record passes, garbage of several shapes
    is refused, empty is never ok, the deadline is enforced, and a retry
    genuinely differs from the first attempt."""

    def test_stub_saw_what_the_adapter_actually_constructed(self):
        runner = _fake_runner([json.dumps(VALID_ACTION) + "\n"])
        adapter = semantic.SemanticAdapter(
            cmd=["/bin/fake-semantic"], runner=runner)
        capsule = {"unit_id": "ORCH-07", "objective": "prove the seam"}
        result = adapter.invoke(capsule, deadline_s=5, now=0)
        self.assertTrue(result.ok)
        self.assertEqual(len(runner.calls), 1)
        argv, kwargs = runner.calls[0]["argv"], runner.calls[0]["kwargs"]
        self.assertEqual(argv, ["/bin/fake-semantic"])
        self.assertIn("ORCH-07", kwargs["input"])
        self.assertEqual(kwargs["timeout"], 5.0)

    def test_prose_is_refused(self):
        runner = _fake_runner(
            ["I recommend dispatching ORCH-08 next.\n", "still prose\n"])
        adapter = semantic.SemanticAdapter(
            cmd=["/bin/fake-semantic"], runner=runner)
        result = adapter.invoke({"unit_id": "X"}, deadline_s=5, now=0)
        self.assertFalse(result.ok)
        self.assertEqual(result.failure_class, "worker_crash")

    def test_malformed_json_is_refused(self):
        runner = _fake_runner(
            ['{"action": "DISPATCH", "task_id": ', "still broken"])
        adapter = semantic.SemanticAdapter(
            cmd=["/bin/fake-semantic"], runner=runner)
        result = adapter.invoke({"unit_id": "X"}, deadline_s=5, now=0)
        self.assertFalse(result.ok)
        self.assertEqual(result.failure_class, "worker_crash")

    def test_action_outside_vocabulary_is_refused(self):
        bad = dict(VALID_ACTION, action="MERGE-CANONICAL")
        runner = _fake_runner([json.dumps(bad) + "\n", json.dumps(bad) + "\n"])
        adapter = semantic.SemanticAdapter(
            cmd=["/bin/fake-semantic"], runner=runner)
        result = adapter.invoke({"unit_id": "X"}, deadline_s=5, now=0)
        self.assertFalse(result.ok)
        self.assertEqual(result.failure_class, "worker_crash")

    def test_empty_answer_is_never_ok(self):
        runner = _fake_runner(["", ""])
        adapter = semantic.SemanticAdapter(
            cmd=["/bin/fake-semantic"], runner=runner)
        result = adapter.invoke({"unit_id": "X"}, deadline_s=5, now=0)
        self.assertFalse(result.ok)
        self.assertEqual(result.failure_class, "empty")

    def test_deadline_is_enforced_via_timeout(self):
        runner = _fake_runner(
            [subprocess.TimeoutExpired(cmd=["fake"], timeout=5)])
        adapter = semantic.SemanticAdapter(
            cmd=["/bin/fake-semantic"], runner=runner)
        result = adapter._run_attempt(
            {"unit_id": "X"}, budget_s=5, attempt_number=1, prior_note=None)
        self.assertTrue(result["timed_out"])
        self.assertEqual(runner.calls[0]["kwargs"]["timeout"], 5)

    def test_retry_uses_two_distinct_budgets_and_recovers(self):
        runner = _fake_runner(
            ["", json.dumps(VALID_ACTION) + "\n"])
        adapter = semantic.SemanticAdapter(
            cmd=["/bin/fake-semantic"], runner=runner)
        result = adapter.invoke({"unit_id": "X"}, deadline_s=10, now=0)
        self.assertTrue(result.ok)
        self.assertEqual(len(runner.calls), 2)
        first_timeout = runner.calls[0]["kwargs"]["timeout"]
        second_timeout = runner.calls[1]["kwargs"]["timeout"]
        self.assertNotEqual(first_timeout, second_timeout)
        # "never identically" also means the second prompt is not a byte
        # for byte repeat of the first: it must carry a note of what
        # happened last time.
        self.assertNotEqual(
            runner.calls[0]["kwargs"]["input"], runner.calls[1]["kwargs"]["input"])
        self.assertIn("prior_note", runner.calls[1]["kwargs"]["input"])

    def test_two_failures_stop_at_two_attempts_never_a_third(self):
        runner = _fake_runner(["not json at all", "still not json"])
        adapter = semantic.SemanticAdapter(
            cmd=["/bin/fake-semantic"], runner=runner)
        result = adapter.invoke({"unit_id": "X"}, deadline_s=10, now=0)
        self.assertFalse(result.ok)
        self.assertEqual(len(runner.calls), 2)


class TaskIdMismatchTests(unittest.TestCase):
    """The edge this role adds on top of the shared contract: an answer
    that validates cleanly but names a DIFFERENT task than the one this
    call actually asked about is refused, never trusted."""

    def test_mismatched_task_id_is_refused_as_missing_evidence(self):
        mismatched = dict(TASK_SCOPED_ACTION, task_id="ORCH-01")
        runner = _fake_runner(
            [json.dumps(mismatched) + "\n", json.dumps(mismatched) + "\n"])
        adapter = semantic.SemanticAdapter(
            cmd=["/bin/fake-semantic"], runner=runner)
        result = adapter.invoke({"unit_id": "ORCH-07"}, deadline_s=5, now=0)
        self.assertFalse(result.ok)
        self.assertEqual(result.failure_class, "missing_evidence")

    def test_matching_task_id_passes(self):
        matched = dict(TASK_SCOPED_ACTION, task_id="ORCH-07")
        runner = _fake_runner([json.dumps(matched) + "\n"])
        adapter = semantic.SemanticAdapter(
            cmd=["/bin/fake-semantic"], runner=runner)
        result = adapter.invoke({"unit_id": "ORCH-07"}, deadline_s=5, now=0)
        self.assertTrue(result.ok)

    def test_no_task_action_skips_the_task_id_check(self):
        # NOOP carries no task_id at all; the mismatch check must not
        # spuriously fire on the no-task branch.
        runner = _fake_runner([json.dumps(VALID_ACTION) + "\n"])
        adapter = semantic.SemanticAdapter(
            cmd=["/bin/fake-semantic"], runner=runner)
        result = adapter.invoke({"unit_id": "ORCH-07"}, deadline_s=5, now=0)
        self.assertTrue(result.ok)


class DecompositionProposalTests(unittest.TestCase):
    """propose_decomposition(): a proposal missing a required field on
    any child is refused; a complete proposal is accepted; the deadline
    and empty-answer rules apply here too; and the two extra edges named
    by the worker brief (zero children, a single child identical to the
    parent) are refused."""

    def _adapter(self, stdout_items):
        runner = _fake_runner(stdout_items)
        return semantic.SemanticAdapter(
            cmd=["/bin/fake-semantic"], runner=runner), runner

    def test_complete_proposal_is_accepted(self):
        proposal_doc = {"children": [
            {"title": "Split A", "owns": ["scripts/a.py"],
             "done_check": "python3 scripts/test_a.py -v"},
            {"title": "Split B", "owns": ["scripts/b.py"],
             "done_check": "python3 scripts/test_b.py -v"},
        ]}
        adapter, runner = self._adapter([json.dumps(proposal_doc) + "\n"])
        result = adapter.propose_decomposition(
            {"unit_id": "ORCH-XX", "owns": ["scripts/xx.py"],
             "done_check": "python3 scripts/test_xx.py -v"},
            deadline_s=5, now=0)
        self.assertTrue(result.ok)
        self.assertEqual(len(result.children), 2)
        self.assertIn("ORCH-XX", runner.calls[0]["kwargs"]["input"])
        self.assertIn("children", runner.calls[0]["kwargs"]["input"])

    def test_child_missing_done_check_is_refused(self):
        proposal_doc = {"children": [
            {"title": "Split A", "owns": ["scripts/a.py"]},
        ]}
        adapter, _ = self._adapter([json.dumps(proposal_doc) + "\n"])
        result = adapter.propose_decomposition(
            {"unit_id": "ORCH-XX", "owns": ["scripts/xx.py"],
             "done_check": "python3 scripts/test_xx.py -v"},
            deadline_s=5, now=0)
        self.assertFalse(result.ok)
        self.assertIn("done_check", result.reason)

    def test_child_missing_owns_is_refused(self):
        proposal_doc = {"children": [
            {"title": "Split A", "done_check": "python3 scripts/test_a.py -v"},
        ]}
        adapter, _ = self._adapter([json.dumps(proposal_doc) + "\n"])
        result = adapter.propose_decomposition(
            {"unit_id": "ORCH-XX"}, deadline_s=5, now=0)
        self.assertFalse(result.ok)
        self.assertIn("owns", result.reason)

    def test_child_missing_title_is_refused(self):
        proposal_doc = {"children": [
            {"owns": ["scripts/a.py"], "done_check": "python3 x.py -v"},
        ]}
        adapter, _ = self._adapter([json.dumps(proposal_doc) + "\n"])
        result = adapter.propose_decomposition(
            {"unit_id": "ORCH-XX"}, deadline_s=5, now=0)
        self.assertFalse(result.ok)
        self.assertIn("title", result.reason)

    def test_zero_children_is_refused(self):
        adapter, _ = self._adapter([json.dumps({"children": []}) + "\n"])
        result = adapter.propose_decomposition(
            {"unit_id": "ORCH-XX"}, deadline_s=5, now=0)
        self.assertFalse(result.ok)
        self.assertIn("zero children", result.reason)

    def test_single_child_identical_to_parent_is_refused(self):
        parent_owns = ["scripts/xx.py"]
        parent_done_check = "python3 scripts/test_xx.py -v"
        proposal_doc = {"children": [
            {"title": "Relabel", "owns": list(parent_owns),
             "done_check": parent_done_check},
        ]}
        adapter, _ = self._adapter([json.dumps(proposal_doc) + "\n"])
        result = adapter.propose_decomposition(
            {"unit_id": "ORCH-XX", "owns": parent_owns,
             "done_check": parent_done_check},
            deadline_s=5, now=0)
        self.assertFalse(result.ok)
        self.assertIn("not a decomposition", result.reason)

    def test_single_child_with_different_scope_is_accepted(self):
        proposal_doc = {"children": [
            {"title": "Narrower", "owns": ["scripts/only_part.py"],
             "done_check": "python3 scripts/test_only_part.py -v"},
        ]}
        adapter, _ = self._adapter([json.dumps(proposal_doc) + "\n"])
        result = adapter.propose_decomposition(
            {"unit_id": "ORCH-XX", "owns": ["scripts/xx.py"],
             "done_check": "python3 scripts/test_xx.py -v"},
            deadline_s=5, now=0)
        self.assertTrue(result.ok)

    def test_prose_answer_is_refused(self):
        adapter, _ = self._adapter(["I think this should become two units.\n"])
        result = adapter.propose_decomposition(
            {"unit_id": "ORCH-XX"}, deadline_s=5, now=0)
        self.assertFalse(result.ok)
        self.assertIn("not valid JSON", result.reason)

    def test_empty_answer_is_refused(self):
        adapter, _ = self._adapter([""])
        result = adapter.propose_decomposition(
            {"unit_id": "ORCH-XX"}, deadline_s=5, now=0)
        self.assertFalse(result.ok)
        self.assertIn("empty", result.reason)

    def test_timeout_is_refused(self):
        runner = _fake_runner(
            [subprocess.TimeoutExpired(cmd=["fake"], timeout=5)])
        adapter = semantic.SemanticAdapter(
            cmd=["/bin/fake-semantic"], runner=runner)
        result = adapter.propose_decomposition(
            {"unit_id": "ORCH-XX"}, deadline_s=5, now=0)
        self.assertFalse(result.ok)
        self.assertIn("timed out", result.reason)

    def test_launch_failure_is_refused_not_raised(self):
        runner = _fake_runner([OSError("permission denied")])
        adapter = semantic.SemanticAdapter(
            cmd=["/no/such/semantic/binary"], runner=runner)
        result = adapter.propose_decomposition(
            {"unit_id": "ORCH-XX"}, deadline_s=5, now=0)
        self.assertFalse(result.ok)
        self.assertIn("tool_unavailable", result.reason)

    def test_decomposition_proposal_construction_rejects_inconsistent_state(self):
        with self.assertRaises(base.AdapterRefused):
            semantic.DecompositionProposal(True, None, None, "raw")
        with self.assertRaises(base.AdapterRefused):
            semantic.DecompositionProposal(False, [{"title": "x"}], None, "raw")
        with self.assertRaises(base.AdapterRefused):
            semantic.DecompositionProposal(False, None, None, "raw")


class NoFullAccessBypassTests(unittest.TestCase):
    """The adapter never constructs an invocation granting canonical
    write access, or bypassing sandboxing: reused directly from
    base.assert_safe_invocation(), never a second copy of that list."""

    def test_build_argv_refuses_a_push_command(self):
        adapter = semantic.SemanticAdapter(cmd=["git", "push", "origin", "main"])
        with self.assertRaises(base.AdapterRefused):
            adapter.build_argv()

    def test_build_argv_refuses_danger_full_access(self):
        adapter = semantic.SemanticAdapter(
            cmd=["/bin/fake-semantic", "-s", "danger-full-access"])
        with self.assertRaises(base.AdapterRefused):
            adapter.build_argv()

    def test_build_argv_allows_a_clean_configured_command(self):
        adapter = semantic.SemanticAdapter(cmd=["/bin/fake-semantic", "--json"])
        argv = adapter.build_argv()
        self.assertEqual(argv, ["/bin/fake-semantic", "--json"])

    def test_invoke_raises_before_any_call_on_an_unsafe_configured_command(self):
        runner = _fake_runner([])
        adapter = semantic.SemanticAdapter(
            cmd=["git", "merge", "main"], runner=runner)
        with self.assertRaises(base.AdapterRefused):
            adapter.invoke({"unit_id": "X"}, deadline_s=5, now=0)
        self.assertEqual(runner.calls, [])


if __name__ == "__main__":
    unittest.main()
