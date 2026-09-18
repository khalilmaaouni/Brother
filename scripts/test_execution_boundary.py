#!/usr/bin/env python3
"""Tests for DOM-40.01, scripts/execution_boundary.py.

Every capability is driven against a pinned expected value (never just "it
returned something"), and every composed module gets its own delegation
test: a real refusal from that module's own decide()/request_credential()/
cancel_process_tree() must surface through ExecutionBoundary unchanged, so
a boundary that stopped calling through goes red, not green. Run directly:
python3 scripts/test_execution_boundary.py -v
"""
import os
import subprocess
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import credential_broker
import execution_boundary
import filesystem_enforcement
import network_policy
import process_containment
from execution_boundary import (
    ADVISORY,
    ENFORCED,
    SUPPORTED,
    UNSUPPORTED,
    CapabilityUnsupported,
    ExecutionBoundary,
)


class DefaultCapabilityReportTest(unittest.TestCase):
    """The pinned expected set the WORKER-CONTRACT's own rule 5 requires:
    a test that cannot fail is not evidence, so this asserts the exact
    dict, not just its keys."""

    def test_default_providers_report_pinned_mechanisms(self):
        boundary = ExecutionBoundary()
        self.assertEqual(
            boundary.capability_report(),
            {
                "filesystem": ADVISORY,
                "network": ADVISORY,
                "credential": ENFORCED,
                "process": ENFORCED,
            },
        )

    def test_capabilities_tuple_has_exactly_four_entries(self):
        # Named explicitly: docs/plan/ORCH-1020-WBS.json's deciding_property
        # names a fifth word, "resources", with no module behind it yet.
        # This assertion is what would catch a five-word tuple invented to
        # look complete rather than built.
        self.assertEqual(
            execution_boundary.CAPABILITIES,
            ("filesystem", "network", "credential", "process"),
        )


class UnsupportedCapabilityTest(unittest.TestCase):
    """A provider that is None says so, loudly, rather than pretending."""

    def test_none_provider_reports_unsupported(self):
        boundary = ExecutionBoundary(filesystem=None)
        self.assertEqual(boundary.capability_report()["filesystem"], UNSUPPORTED)

    def test_none_provider_raises_on_use_instead_of_defaulting(self):
        boundary = ExecutionBoundary(network=None)
        with self.assertRaises(CapabilityUnsupported) as ctx:
            boundary.network_decide("example.com", {"mode": "unrestricted"})
        self.assertEqual(ctx.exception.capability, "network")

    def test_provider_missing_required_method_raises_named(self):
        class _NoMethods(object):
            pass

        boundary = ExecutionBoundary(credential=_NoMethods())
        with self.assertRaises(CapabilityUnsupported) as ctx:
            boundary.request_credential({}, "x", "scope", "purpose", 0)
        self.assertEqual(ctx.exception.capability, "credential")

    def test_unknown_capability_name_raises_value_error(self):
        boundary = ExecutionBoundary()
        with self.assertRaises(ValueError):
            boundary._require("not-a-real-capability", "decide")


class CustomProviderReportsSupportedTest(unittest.TestCase):
    """A stand-in that is not one of the four built-in modules is neither
    claimed advisory nor enforced: it is reported SUPPORTED, the honest,
    narrower fact, and it is still genuinely called through."""

    def test_stand_in_provider_reports_supported_and_is_actually_called(self):
        calls = []

        class _StandIn(object):
            def decide(self, path, roots, action="write"):
                calls.append((path, roots, action))
                return "stand-in-result"

        boundary = ExecutionBoundary(filesystem=_StandIn())
        self.assertEqual(boundary.capability_report()["filesystem"], SUPPORTED)
        result = boundary.filesystem_decide("/tmp/x", {"write": ["/tmp"]})
        self.assertEqual(result, "stand-in-result")
        self.assertEqual(calls, [("/tmp/x", {"write": ["/tmp"]}, "write")])


class FilesystemDelegationTest(unittest.TestCase):
    """DOM-40.02: a real refusal from filesystem_enforcement.decide() must
    surface through the boundary unchanged."""

    def test_empty_roots_refused_through_boundary_matches_direct_call(self):
        boundary = ExecutionBoundary()
        direct = filesystem_enforcement.decide("/tmp/anything", {})
        via_boundary = boundary.filesystem_decide("/tmp/anything", {})
        self.assertFalse(via_boundary.allowed)
        self.assertEqual(via_boundary.rule, "empty-roots-declaration")
        self.assertEqual(via_boundary.rule, direct.rule)

    def test_declared_write_root_allowed_through_boundary(self):
        boundary = ExecutionBoundary()
        verdict = boundary.filesystem_decide(
            "/tmp/some/new/file.txt", {"write": ["/tmp"]})
        self.assertTrue(verdict.allowed)
        self.assertEqual(verdict.rule, "write-root")


class NetworkDelegationTest(unittest.TestCase):
    """DOM-40.03: a real refusal from network_policy.decide() must surface
    through the boundary unchanged."""

    def test_denied_mode_refused_through_boundary_matches_direct_call(self):
        boundary = ExecutionBoundary()
        config = {"mode": "denied"}
        direct = network_policy.decide("example.com", config)
        via_boundary = boundary.network_decide("example.com", config)
        self.assertFalse(via_boundary.allowed)
        self.assertEqual(via_boundary.rule, "mode-denied")
        self.assertEqual(via_boundary.rule, direct.rule)

    def test_unrestricted_high_autonomy_without_ack_refused(self):
        boundary = ExecutionBoundary()
        verdict = boundary.network_decide(
            "example.com", {"mode": "unrestricted"}, high_autonomy=True)
        self.assertFalse(verdict.allowed)
        self.assertEqual(
            verdict.rule, "unrestricted-requires-ack-for-high-autonomy")

    def test_undeclared_autonomy_through_the_boundary_is_treated_as_high(self):
        """Added by the orchestrator after its own mutation SURVIVED: the
        boundary's network_decide default could be flipped to False with
        every test green, silently reopening the hole network_policy closed
        (an undeclared caller running unrestricted with no acknowledgement)."""
        boundary = ExecutionBoundary()
        verdict = boundary.network_decide("example.com", {"mode": "unrestricted"})
        self.assertFalse(verdict.allowed)
        self.assertEqual(
            verdict.rule, "unrestricted-requires-ack-for-high-autonomy")


class CredentialDelegationTest(unittest.TestCase):
    """DOM-40.04: a real refusal from credential_broker.request_credential()
    must surface through the boundary unchanged, and a real grant's value
    must reach the caller through the same object the module itself
    returns."""

    def test_unknown_credential_refused_through_boundary(self):
        boundary = ExecutionBoundary()
        verdict, reason, grant = boundary.request_credential(
            {}, "does-not-exist", "read", "a test", 0)
        self.assertEqual(verdict, credential_broker.REFUSED)
        self.assertEqual(reason, credential_broker.REASON_UNKNOWN_CREDENTIAL)
        self.assertIsNone(grant)

    def test_granted_credential_value_reaches_caller(self):
        boundary = ExecutionBoundary()
        store = {
            "api-key": {
                "value": "the-real-secret",
                "scopes": ["read"],
                "expires_at": None,
            }
        }
        verdict, reason, grant = boundary.request_credential(
            store, "api-key", "read", "a test", 0)
        self.assertEqual(verdict, credential_broker.GRANTED)
        self.assertIsNone(reason)
        self.assertEqual(grant.value, "the-real-secret")
        # The secret is never in repr()/str(): the module's own redaction,
        # proven still intact when reached through the boundary.
        self.assertNotIn("the-real-secret", repr(grant))


class ProcessDelegationTest(unittest.TestCase):
    """DOM-40.05: spawn_contained() and cancel_process_tree() must be the
    REAL functions, verified by a real OS effect (a real process actually
    dies), not a mock standing in for one."""

    def test_spawned_process_is_actually_terminated(self):
        boundary = ExecutionBoundary()
        proc = boundary.spawn_contained(
            [sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            # give it a moment to actually start before cancelling it
            deadline = time.monotonic() + 5.0
            while proc.poll() is not None and time.monotonic() < deadline:
                time.sleep(0.05)
            result = boundary.cancel_process_tree(
                proc.pid, grace=1.0, kill_grace=0.5)
            self.assertTrue(result.verified)
            self.assertTrue(result.all_dead)
            self.assertEqual(result.alive, [])
        finally:
            try:
                proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()

    def test_spawn_contained_is_its_own_process_group_leader(self):
        boundary = ExecutionBoundary()
        proc = boundary.spawn_contained(
            [sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            self.assertEqual(os.getpgid(proc.pid), proc.pid)
            result = process_containment.cancel_process_tree(
                proc.pid, grace=1.0, kill_grace=0.5)
            self.assertTrue(result.all_dead)
        finally:
            try:
                proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()


if __name__ == "__main__":
    unittest.main()
