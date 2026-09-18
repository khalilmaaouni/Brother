#!/usr/bin/env python3
"""Tests for ORCH-06, scripts/orchestrators/base.py and execution.py: the
orchestrator adapter that turns an outside model's answer into either a
schema-valid action record or a named failure, never a best-effort
interpretation of prose.

Every attempt in this suite is driven against a STUB (StubAdapter,
_fake_runner), never a real outside model: fast, offline, deterministic,
matching the worker contract's own rule against calling a real model in a
test.

THE BAD STATE THIS SUITE IS BUILT TO CATCH: a stub whose _run_attempt
always returns a valid, schema-passing action record no matter what
invoke() actually sent it. Such a stub would make every "does invoke()
return ok" test pass while the adapter's own retry, deadline and
classification machinery did nothing at all; the adapter would be a pass
through, not a gate. Two defenses against it, both used below:

  (a) StubAdapter RECORDS every call it receives (self.calls: capsule,
      budget_s, attempt_number, prior_note), and several tests assert on
      that recorded data (test_stub_saw_what_the_adapter_actually_
      constructed, test_retry_differs_from_first_attempt_and_recovers),
      proving invoke() actually threaded distinct, real arguments through
      to each attempt rather than the test only checking a canned return
      value.
  (b) Several tests configure the stub to return GARBAGE (prose,
      truncated JSON, an out-of-vocabulary action, an empty string) and
      assert the adapter refuses it. A suite with only the agreeable stub
      would never exercise the refusal path at all.
"""
import json
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import orchestrator_invariants as inv  # noqa: E402
from orchestrators import base  # noqa: E402
from orchestrators import execution  # noqa: E402


#: One clean, schema-valid "no task" action envelope (NOOP), used
#: throughout as the one shape of document this whole module is willing
#: to call ok.
VALID_ACTION = {
    "schema_version": "orchestrator-event-v1",
    "run_id": "run-1",
    "orchestrator": "codex-adapter-test",
    "instance": "inst-1",
    "epoch": 0,
    "at": "2026-09-18T00:00:00Z",
    "action": "NOOP",
    "reason": "no ready work this tick",
    "observed_revision": "deadbeef",
    "observed_journal_seq": 0,
}


class StubAdapter(base.OrchestratorAdapter):
    """A test double for base.OrchestratorAdapter. Returns canned
    _run_attempt responses in order and records exactly what each call
    received. Exhausting the response list is a test bug, not a silent
    no-op: it raises loudly rather than returning a default 'success' so
    a test expecting N attempts that gets N+1 fails instead of passing by
    accident."""

    name = "stub"

    def __init__(self, responses, requested_model=None, health_value=None):
        super(StubAdapter, self).__init__(requested_model=requested_model)
        self._responses = list(responses)
        self.calls = []
        self._health_value = health_value

    def _run_attempt(self, capsule, *, budget_s, attempt_number, prior_note):
        self.calls.append({
            "capsule": capsule, "budget_s": budget_s,
            "attempt_number": attempt_number, "prior_note": prior_note,
        })
        if not self._responses:
            raise AssertionError(
                "StubAdapter._run_attempt called %d time(s), more than "
                "the test configured responses for" % len(self.calls))
        return self._responses.pop(0)

    def health(self):
        if self._health_value is not None:
            return self._health_value
        return super(StubAdapter, self).health()


class ClassificationTests(unittest.TestCase):
    """base.OrchestratorAdapter._classify(): one attempt in, one
    ActionResult out, no retry loop involved. The per-shape proofs the
    worker contract names by name."""

    def setUp(self):
        self.adapter = StubAdapter([])

    def test_valid_action_record_passes(self):
        result = self.adapter._classify({
            "raw": json.dumps(VALID_ACTION), "timed_out": False,
            "model_reported": None,
        })
        self.assertTrue(result.ok)
        self.assertEqual(result.action, VALID_ACTION)
        self.assertIsNone(result.failure_class)

    def test_prose_is_refused(self):
        result = self.adapter._classify({
            "raw": "I think we should dispatch ORCH-06 next.",
            "timed_out": False, "model_reported": None,
        })
        self.assertFalse(result.ok)
        self.assertIsNone(result.action)
        self.assertEqual(result.failure_class, "worker_crash")

    def test_malformed_json_is_refused(self):
        result = self.adapter._classify({
            "raw": '{"action": "DISPATCH", "task_id": ',
            "timed_out": False, "model_reported": None,
        })
        self.assertFalse(result.ok)
        self.assertEqual(result.failure_class, "worker_crash")

    def test_valid_json_that_is_not_an_object_is_refused(self):
        result = self.adapter._classify({
            "raw": json.dumps(["NOOP", "reason"]),
            "timed_out": False, "model_reported": None,
        })
        self.assertFalse(result.ok)
        self.assertEqual(result.failure_class, "worker_crash")

    def test_action_outside_vocabulary_is_refused(self):
        bad = dict(VALID_ACTION, action="MERGE-CANONICAL")
        result = self.adapter._classify({
            "raw": json.dumps(bad), "timed_out": False, "model_reported": None,
        })
        self.assertFalse(result.ok)
        self.assertEqual(result.failure_class, "worker_crash")

    def test_empty_answer_is_never_ok(self):
        # The estate's recorded SR-1 incident: an exit 0 with an empty
        # body read as success. timed_out False, raw empty, must refuse.
        result = self.adapter._classify({
            "raw": "", "timed_out": False, "model_reported": None,
        })
        self.assertFalse(result.ok)
        self.assertEqual(result.failure_class, "empty")

    def test_whitespace_only_answer_is_empty(self):
        result = self.adapter._classify({
            "raw": "   \n\t  ", "timed_out": False, "model_reported": None,
        })
        self.assertFalse(result.ok)
        self.assertEqual(result.failure_class, "empty")

    def test_timeout_is_classified_timeout(self):
        result = self.adapter._classify({
            "raw": "", "timed_out": True, "model_reported": None,
        })
        self.assertFalse(result.ok)
        self.assertEqual(result.failure_class, "timeout")

    def test_timeout_wins_even_with_a_nonempty_partial_answer(self):
        result = self.adapter._classify({
            "raw": json.dumps(VALID_ACTION), "timed_out": True,
            "model_reported": None,
        })
        self.assertFalse(result.ok)
        self.assertEqual(result.failure_class, "timeout")

    def test_model_mismatch_is_caught(self):
        adapter = StubAdapter([], requested_model="gpt-5-codex")
        result = adapter._classify({
            "raw": json.dumps(VALID_ACTION), "timed_out": False,
            "model_reported": "some-other-model",
        })
        self.assertFalse(result.ok)
        self.assertEqual(result.failure_class, "missing_evidence")
        self.assertEqual(result.model_reported, "some-other-model")

    def test_matching_model_is_not_a_failure(self):
        adapter = StubAdapter([], requested_model="gpt-5-codex")
        result = adapter._classify({
            "raw": json.dumps(VALID_ACTION), "timed_out": False,
            "model_reported": "gpt-5-codex",
        })
        self.assertTrue(result.ok)

    def test_no_requested_model_skips_the_identity_check(self):
        adapter = StubAdapter([], requested_model=None)
        result = adapter._classify({
            "raw": json.dumps(VALID_ACTION), "timed_out": False,
            "model_reported": "whatever-answered",
        })
        self.assertTrue(result.ok)

    def test_subclass_reported_failure_class_is_trusted(self):
        result = self.adapter._classify({
            "raw": "", "timed_out": False, "model_reported": None,
            "failure_class": "rate_limit",
        })
        self.assertFalse(result.ok)
        self.assertEqual(result.failure_class, "rate_limit")

    def test_unrecognised_failure_class_raises(self):
        # Requirement 4: an unrecognised failure raises rather than being
        # classified "unknown" by default.
        with self.assertRaises(ValueError):
            self.adapter._classify({
                "raw": "", "timed_out": False, "model_reported": None,
                "failure_class": "not_a_real_failure_class",
            })


class RetryTests(unittest.TestCase):
    """invoke(): the deadline, the retry-once-never-identically rule, and
    the never-retry-a-policy-refusal rule."""

    def test_retry_differs_from_first_attempt_and_recovers(self):
        adapter = StubAdapter([
            {"raw": "", "timed_out": False, "model_reported": None},
            {"raw": json.dumps(VALID_ACTION), "timed_out": False,
             "model_reported": None},
        ])
        result = adapter.invoke({"unit_id": "X"}, deadline_s=10, now=0)
        self.assertTrue(result.ok)
        self.assertEqual(len(adapter.calls), 2)
        first, second = adapter.calls
        self.assertNotEqual(first["budget_s"], second["budget_s"])
        self.assertIsNone(first["prior_note"])
        self.assertIsNotNone(second["prior_note"])
        self.assertIn("empty", second["prior_note"])

    def test_two_failures_stop_at_two_attempts_never_a_third(self):
        adapter = StubAdapter([
            {"raw": "not json at all", "timed_out": False,
             "model_reported": None},
            {"raw": "still not json", "timed_out": False,
             "model_reported": None},
        ])
        result = adapter.invoke({"unit_id": "X"}, deadline_s=10, now=0)
        self.assertFalse(result.ok)
        self.assertEqual(result.failure_class, "worker_crash")
        self.assertEqual(len(adapter.calls), 2)

    def test_policy_refusal_is_never_retried(self):
        adapter = StubAdapter([
            {"raw": "", "timed_out": False, "model_reported": None,
             "failure_class": "policy_refusal"},
        ])
        result = adapter.invoke({"unit_id": "X"}, deadline_s=10, now=0)
        self.assertFalse(result.ok)
        self.assertEqual(result.failure_class, "policy_refusal")
        self.assertEqual(len(adapter.calls), 1)

    def test_nonpositive_deadline_is_refused_before_any_attempt(self):
        adapter = StubAdapter([])
        with self.assertRaises(base.AdapterRefused):
            adapter.invoke({"unit_id": "X"}, deadline_s=0, now=0)
        self.assertEqual(adapter.calls, [])

    def test_stub_saw_what_the_adapter_actually_constructed(self):
        adapter = StubAdapter([
            {"raw": json.dumps(VALID_ACTION), "timed_out": False,
             "model_reported": None},
        ])
        capsule = {"unit_id": "ORCH-06", "objective": "prove the seam"}
        result = adapter.invoke(capsule, deadline_s=5, now=0)
        self.assertTrue(result.ok)
        self.assertEqual(adapter.calls[0]["capsule"], capsule)
        self.assertEqual(adapter.calls[0]["attempt_number"], 1)
        self.assertEqual(adapter.calls[0]["budget_s"], 5.0)
        self.assertEqual(adapter.last_invoked_at, 0.0)


class CheckRetryAllowedTests(unittest.TestCase):
    """base.check_retry_allowed(): the pure function invoke() relies on
    for the retry rule, driven directly so the third-attempt and
    identical-attempt refusals are proven without an adapter or a
    subprocess anywhere in between."""

    def test_first_and_distinct_second_attempt_allowed(self):
        prior = []
        base.check_retry_allowed(1, (10.0, None), prior)
        prior.append((10.0, None))
        base.check_retry_allowed(2, (20.0, "attempt 1 failed: empty"), prior)

    def test_third_attempt_is_refused_outright(self):
        prior = [(10.0, None), (20.0, "note")]
        with self.assertRaises(base.AdapterRefused):
            base.check_retry_allowed(3, (40.0, "note2"), prior)

    def test_identical_retry_is_refused(self):
        prior = [(10.0, None)]
        with self.assertRaises(base.AdapterRefused):
            base.check_retry_allowed(2, (10.0, None), prior)


class HealthTests(unittest.TestCase):
    def test_default_health_with_no_signal_is_unavailable_not_healthy(self):
        adapter = StubAdapter([])
        self.assertEqual(adapter.health(), "UNAVAILABLE")
        self.assertNotEqual(adapter.health(), "HEALTHY")
        self.assertIn("UNAVAILABLE", inv.HEALTH_STATES)

    def test_execution_adapter_health_unavailable_when_binary_missing(self):
        adapter = execution.ExecutionAdapter(
            codex_bin_path="/no/such/binary/codex-does-not-exist")
        self.assertEqual(adapter.health(), "UNAVAILABLE")


class NoFullAccessBypassTests(unittest.TestCase):
    def test_assert_safe_invocation_refuses_danger_full_access(self):
        with self.assertRaises(base.AdapterRefused):
            base.assert_safe_invocation(
                ["/bin/codex", "exec", "-s", "danger-full-access"])

    def test_assert_safe_invocation_refuses_merge_and_push(self):
        with self.assertRaises(base.AdapterRefused):
            base.assert_safe_invocation(["git", "merge", "main"])
        with self.assertRaises(base.AdapterRefused):
            base.assert_safe_invocation(["git", "push", "origin", "main"])

    def test_assert_safe_invocation_allows_a_clean_argv(self):
        base.assert_safe_invocation(
            ["/bin/codex", "exec", "-s", "workspace-write", "-C", "/tmp/x"])

    def test_execution_adapter_never_builds_a_full_access_or_merge_argv(self):
        adapter = execution.ExecutionAdapter(codex_bin_path="/bin/codex")
        argv = adapter.build_argv("/tmp/workdir")
        joined = " ".join(argv)
        self.assertNotIn("danger-full-access", joined)
        self.assertNotIn("merge", joined)
        self.assertNotIn("push", joined)
        self.assertEqual(argv[0], "/bin/codex")
        self.assertIn("-C", argv)
        self.assertEqual(argv[argv.index("-s") + 1], "workspace-write")


class _FakeCompleted(object):
    def __init__(self, stdout):
        self.stdout = stdout


def _fake_runner(responses):
    """A subprocess.run-shaped stand-in. Pops one entry per call: an
    Exception instance is raised, anything else is wrapped as stdout on a
    fake CompletedProcess. Records every call it received so a test can
    assert on the exact argv the adapter built, with no process spawned
    and no real outside model reached."""
    calls = []

    def runner(argv, **kwargs):
        calls.append({"argv": argv, "kwargs": kwargs})
        item = responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return _FakeCompleted(item)

    runner.calls = calls
    return runner


class ExecutionAdapterTests(unittest.TestCase):
    """scripts/orchestrators/execution.py, always through _fake_runner:
    never a real `codex exec` call in this suite."""

    def test_run_attempt_times_out_without_a_real_process(self):
        runner = _fake_runner(
            [subprocess.TimeoutExpired(cmd=["codex"], timeout=5)])
        adapter = execution.ExecutionAdapter(
            codex_bin_path="/bin/codex", runner=runner)
        result = adapter._run_attempt(
            {"unit_id": "X"}, budget_s=5, attempt_number=1, prior_note=None)
        self.assertTrue(result["timed_out"])
        self.assertEqual(runner.calls[0]["kwargs"]["timeout"], 5)

    def test_run_attempt_missing_binary_is_tool_unavailable(self):
        runner = _fake_runner([OSError("no such file or directory")])
        adapter = execution.ExecutionAdapter(
            codex_bin_path="/no/such/codex", runner=runner)
        result = adapter._run_attempt(
            {"unit_id": "X"}, budget_s=5, attempt_number=1, prior_note=None)
        self.assertFalse(result["timed_out"])
        self.assertEqual(result["failure_class"], "tool_unavailable")

    def test_run_attempt_reports_the_answering_model(self):
        stdout = json.dumps({"type": "result", "model": "codex-mini"}) + "\n"
        runner = _fake_runner([stdout])
        adapter = execution.ExecutionAdapter(
            codex_bin_path="/bin/codex", runner=runner)
        result = adapter._run_attempt(
            {"unit_id": "X"}, budget_s=5, attempt_number=1, prior_note=None)
        self.assertEqual(result["model_reported"], "codex-mini")

    def test_run_attempt_with_no_model_line_reports_none(self):
        runner = _fake_runner(["not jsonl at all\n"])
        adapter = execution.ExecutionAdapter(
            codex_bin_path="/bin/codex", runner=runner)
        result = adapter._run_attempt(
            {"unit_id": "X"}, budget_s=5, attempt_number=1, prior_note=None)
        self.assertIsNone(result["model_reported"])

    def test_invoke_end_to_end_with_a_fake_runner_never_a_real_model(self):
        stdout = json.dumps(VALID_ACTION) + "\n"
        runner = _fake_runner([stdout])
        adapter = execution.ExecutionAdapter(
            codex_bin_path="/bin/codex", runner=runner)
        result = adapter.invoke(
            {"unit_id": "ORCH-06", "workdir": "/tmp/workdir"},
            deadline_s=5, now=0)
        self.assertTrue(result.ok)
        self.assertEqual(result.action, VALID_ACTION)
        self.assertEqual(len(runner.calls), 1)
        argv = runner.calls[0]["argv"]
        self.assertEqual(argv[0], "/bin/codex")
        self.assertNotIn("danger-full-access", " ".join(argv))


if __name__ == "__main__":
    unittest.main()
