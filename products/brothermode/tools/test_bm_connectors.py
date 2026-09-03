#!/usr/bin/env python3
"""Tests for bm_connectors: catalog integrity and probe verdict calibration.

Three families. Catalog integrity proves every record carries every required
field and an https doc url, that the two known-bad repos (the deprecated
Snowflake-Labs server, the archived Azure repo) appear in traps and NEVER in
any print-add wiring output. Verdict calibration proves the probe mapping in
BOTH directions with the subprocess runner mocked: exit 0 maps to LIVE,
nonzero to FAIL, a missing binary to NO-DATA, and the Bitbucket probe always
carries its separate MCP NO-DATA line so transport-alive can never read as
MCP-enabled. The print-add family proves that code path never invokes a
subprocess at all, by making every subprocess entry point raise.

A fourth family, added 2026-08-30 alongside tools/bm_mock_mcp.py: mock
conformance. Every profile's mock must list the catalog's own tool names
verbatim (a removed name on either side is drift, and is proven caught
below), a tools/call must round-trip, and the two injected misbehaviors
(an ACL leak, a stale delete) must each be CAUGHT, in both directions
(present when the flag is passed, absent when it is not). A fifth family
proves the Azurite probe added beside the az CLI check maps its three
outcomes (no port, an Azurite response, a non-Azurite response) correctly
without ever touching a real network host: the opener is injected, exactly
like the subprocess runner above.

Run: python3 tools/test_bm_connectors.py      (unittest output, exit 0 or 1)
"""
import io
import json
import os
import subprocess
import sys
import unittest
import urllib.error
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bm_connectors as bc  # noqa: E402


class FakeProc(object):
    def __init__(self, code, out=b""):
        self.returncode = code
        self.stdout = out


def runner_returning(code):
    def runner(argv, **kw):
        return FakeProc(code, b"probe output")
    return runner


class CatalogIntegrity(unittest.TestCase):
    def test_every_record_carries_every_required_field(self):
        for c in bc.CATALOG:
            for field in bc.REQUIRED_FIELDS:
                self.assertIn(field, c, "%s missing %s" % (c.get("id"), field))

    def test_every_doc_url_is_https(self):
        for c in bc.CATALOG:
            self.assertTrue(c["doc"].startswith("https://"),
                            "%s doc url not https: %s" % (c["id"], c["doc"]))

    def test_ids_unique_and_add_lines_cover_catalog(self):
        ids = [c["id"] for c in bc.CATALOG]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(set(ids), set(bc.ADD_LINES))

    def test_deprecated_repos_present_in_traps(self):
        self.assertTrue(any("Snowflake-Labs/mcp" in t
                            for t in bc.BY_ID["snowflake"]["traps"]))
        self.assertTrue(any("azure-mcp-server" in t and "ARCHIVED" in t
                            for t in bc.BY_ID["azure"]["traps"]))

    def test_deprecated_repos_absent_from_wiring_output(self):
        for cid in bc.BY_ID:
            out = io.StringIO()
            self.assertEqual(bc.print_add(cid, out=out), 0)
            wiring = [l for l in out.getvalue().splitlines()
                      if not l.startswith("#")]
            joined = "\n".join(wiring)
            self.assertNotIn("Snowflake-Labs", joined)
            self.assertNotIn("snowflake-labs-mcp", joined)
            self.assertNotIn("Azure/azure-mcp-server", joined)

    def test_single_sourced_flags_survive_into_output(self):
        out = io.StringIO()
        bc.print_add("teams", out=out)
        self.assertIn("single-sourced", out.getvalue())


class VerdictCalibration(unittest.TestCase):
    def test_exit_zero_maps_to_live(self):
        for cid in ("github", "azure"):
            with mock.patch.object(bc.shutil, "which", return_value="/bin/x"):
                verdicts = bc.probe(cid, runner=runner_returning(0))
            self.assertEqual(verdicts[0][0], bc.LIVE, cid)

    def test_nonzero_maps_to_fail(self):
        for cid in ("github", "azure"):
            with mock.patch.object(bc.shutil, "which", return_value="/bin/x"):
                verdicts = bc.probe(cid, runner=runner_returning(1))
            self.assertEqual(verdicts[0][0], bc.FAIL, cid)

    def test_missing_binary_maps_to_no_data(self):
        for cid in ("github", "azure", "bitbucket"):
            with mock.patch.object(bc.shutil, "which", return_value=None):
                verdicts = bc.probe(cid, runner=runner_returning(0))
            self.assertEqual(verdicts[0][0], bc.NO_DATA, cid)

    def test_github_needs_both_probes_green(self):
        calls = []

        def runner(argv, **kw):
            calls.append(argv)
            return FakeProc(0 if argv[:3] == ["gh", "auth", "status"] else 1)
        with mock.patch.object(bc.shutil, "which", return_value="/bin/gh"):
            verdicts = bc.probe("github", runner=runner)
        self.assertEqual(verdicts[0][0], bc.FAIL)
        self.assertEqual(len(calls), 2)

    def test_bitbucket_transport_live_still_carries_mcp_no_data(self):
        with mock.patch.object(bc.shutil, "which", return_value="/bin/ssh"):
            verdicts = bc.probe("bitbucket", runner=runner_returning(0))
        self.assertEqual(verdicts[0][0], bc.LIVE)
        self.assertEqual(verdicts[1][0], bc.NO_DATA)
        self.assertIn("Admin Hub", verdicts[1][1])

    def test_founder_only_connectors_are_always_no_data(self):
        def exploding_runner(argv, **kw):
            raise AssertionError("founder-only probe ran a subprocess: %r" % argv)
        for cid in ("snowflake", "databricks", "teams"):
            verdicts = bc.probe(cid, runner=exploding_runner)
            self.assertEqual(verdicts[0][0], bc.NO_DATA, cid)
            self.assertIn("founder", verdicts[0][1].lower(), cid)

    def test_check_exit_codes(self):
        import contextlib
        sink = io.StringIO()
        with contextlib.redirect_stdout(sink):
            with mock.patch.object(bc, "probe",
                                   return_value=[(bc.FAIL, "x: broke")]):
                self.assertEqual(bc.cmd_check("github"), 1)
            with mock.patch.object(bc, "probe",
                                   return_value=[(bc.NO_DATA, "x: cannot run")]):
                self.assertEqual(bc.cmd_check("github"), 0)
            self.assertEqual(bc.cmd_check("no-such-connector"), 2)


class PrintAddNeverExecutes(unittest.TestCase):
    def test_print_add_invokes_no_subprocess(self):
        def explode(*a, **kw):
            raise AssertionError("print-add invoked a subprocess")
        with mock.patch.object(subprocess, "run", explode), \
                mock.patch.object(subprocess, "Popen", explode), \
                mock.patch.object(subprocess, "call", explode), \
                mock.patch.object(subprocess, "check_output", explode):
            for cid in bc.BY_ID:
                out = io.StringIO()
                self.assertEqual(bc.print_add(cid, out=out), 0)
                self.assertIn("restart", out.getvalue())

    def test_print_add_unknown_connector_is_no_data_exit_2(self):
        self.assertEqual(bc.print_add("no-such-connector", out=io.StringIO()), 2)

    def test_no_credential_values_in_any_output(self):
        for cid in bc.BY_ID:
            out = io.StringIO()
            bc.print_add(cid, out=out)
            for marker in ("sk-", "AKIA", "ghp_", "Bearer ", "PRIVATE KEY",
                           "password="):
                self.assertNotIn(marker, out.getvalue(), cid)


MOCK_PROFILES = ("snowflake", "databricks", "github", "bitbucket", "teams")


class MockConformance(unittest.TestCase):
    """Integration-level: spawns the real tools/bm_mock_mcp.py subprocess,
    exactly as cmd_conformance does. No network, no port: stdio only."""

    def test_well_behaved_mock_is_conformant_for_every_profile(self):
        for cid in MOCK_PROFILES:
            out = io.StringIO()
            rc = bc.cmd_conformance(cid, "mock", None, out=out)
            self.assertEqual(rc, 0, out.getvalue())
            self.assertIn("LIVE", out.getvalue())

    def test_acl_misbehavior_is_caught(self):
        for cid in MOCK_PROFILES:
            out = io.StringIO()
            rc = bc.cmd_conformance(cid, "mock", "acl", out=out)
            self.assertEqual(rc, 1, out.getvalue())
            self.assertIn("acl-leak", out.getvalue())

    def test_stale_delete_misbehavior_is_caught(self):
        for cid in MOCK_PROFILES:
            out = io.StringIO()
            rc = bc.cmd_conformance(cid, "mock", "stale-delete", out=out)
            self.assertEqual(rc, 1, out.getvalue())
            self.assertIn("stale-delete", out.getvalue())

    def test_tools_list_drift_is_caught_when_catalog_loses_a_tool(self):
        # Calibration in the other direction from a mock that drops a tool:
        # here the CATALOG side the conformance check reads is missing one
        # name the mock still serves (mutated back after the assertion), so
        # the exact-match check has something to catch on either side.
        original = list(bc.BY_ID["snowflake"]["tools"])
        bc.BY_ID["snowflake"]["tools"] = original[1:]
        try:
            out = io.StringIO()
            rc = bc.cmd_conformance("snowflake", "mock", None, out=out)
            self.assertEqual(rc, 1, out.getvalue())
            self.assertIn("tools/list drift", out.getvalue())
        finally:
            bc.BY_ID["snowflake"]["tools"] = original

    def test_via_live_is_no_data_and_names_open_test_path(self):
        for cid in MOCK_PROFILES + ("azure",):
            out = io.StringIO()
            rc = bc.cmd_conformance(cid, "live", None, out=out)
            self.assertEqual(rc, 2)
            self.assertIn("NO-DATA", out.getvalue())
            self.assertIn(bc.BY_ID[cid]["open_test_path"][:20], out.getvalue())

    def test_unknown_connector_is_exit_2(self):
        self.assertEqual(bc.cmd_conformance("no-such-connector", "mock", None,
                                            out=io.StringIO(), err=io.StringIO()), 2)

    def test_mock_never_imports_a_network_module(self):
        # stdio only, on purpose: the docstring promises it, this proves it.
        with io.open(bc.MOCK_SCRIPT, encoding="utf-8") as fh:
            src = fh.read()
        for banned in ("socket", "urllib", "http.server", "asyncio"):
            self.assertNotIn("import %s" % banned, src)

    def test_every_mock_profile_serves_its_catalog_tools_verbatim(self):
        sys.path.insert(0, HERE)
        import bm_mock_mcp as mm  # noqa: E402
        for cid in MOCK_PROFILES:
            profile = bc.CATALOG_ID_TO_PROFILE[cid]
            self.assertEqual(sorted(mm.tool_names_for(profile)),
                              sorted(bc.BY_ID[cid]["tools"]))


class AzuriteProbeCalibration(unittest.TestCase):
    def test_no_port_is_no_data_naming_npx_azurite(self):
        def exploding(url, timeout=None):
            raise urllib.error.URLError("connection refused")
        verdict, line = bc.probe_azurite(opener=exploding)
        self.assertEqual(verdict, bc.NO_DATA)
        self.assertIn("npx azurite", line)

    def test_azurite_server_header_is_live(self):
        class Resp(object):
            headers = {"Server": "Azurite-Blob/3.30.0"}
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
        def opener(url, timeout=None):
            return Resp()
        verdict, line = bc.probe_azurite(opener=opener)
        self.assertEqual(verdict, bc.LIVE)

    def test_azurite_identifies_itself_even_on_an_auth_error(self):
        # An unsigned GET against real Azurite returns 403, not 200; the
        # probe must still read the Server header off the HTTPError.
        exc = urllib.error.HTTPError("http://x", 403, "Forbidden",
                                      {"Server": "Azurite-Blob/3.30.0"}, None)
        def opener(url, timeout=None):
            raise exc
        verdict, line = bc.probe_azurite(opener=opener)
        self.assertEqual(verdict, bc.LIVE)

    def test_non_azurite_service_on_the_port_is_fail(self):
        exc = urllib.error.HTTPError("http://x", 403, "Forbidden",
                                      {"Server": "nginx"}, None)
        def opener(url, timeout=None):
            raise exc
        verdict, line = bc.probe_azurite(opener=opener)
        self.assertEqual(verdict, bc.FAIL)

    def test_azure_probe_appends_azurite_result_without_disturbing_index_zero(self):
        with mock.patch.object(bc.shutil, "which", return_value="/bin/az"):
            with mock.patch.object(bc, "probe_azurite",
                                   return_value=(bc.NO_DATA, "azurite: stub")):
                verdicts = bc.probe("azure", runner=runner_returning(0))
        self.assertEqual(verdicts[0][0], bc.LIVE)
        self.assertEqual(verdicts[1], (bc.NO_DATA, "azurite: stub"))


class OpenTestPathField(unittest.TestCase):
    def test_every_connector_carries_a_non_empty_open_test_path(self):
        for c in bc.CATALOG:
            self.assertTrue(c["open_test_path"].strip(), c["id"])


class CanaryClasses(unittest.TestCase):
    """VB3-14: four canary classes on top of the one-shot conformance check
    above. Driven backwards in both directions: a well-behaved mock must
    PASS all four for every wired profile, and each class's OWN forced
    misbehavior (tools/bm_mock_mcp.py's --misbehave) must FAIL it by name
    while leaving the other three classes PASSing, proving a canary that
    always passes regardless of the fixture would be caught here."""

    def test_well_behaved_mock_passes_every_class_for_every_profile(self):
        for cid in MOCK_PROFILES:
            out = io.StringIO()
            rc = bc.cmd_canary(cid, out=out)
            self.assertEqual(rc, 0, out.getvalue())
            for klass in bc.CANARY_CLASSES:
                self.assertIn("PASS    %s %s:" % (cid, klass), out.getvalue(),
                             out.getvalue())

    def test_each_forced_state_fails_only_its_own_class(self):
        for klass, forced in bc.CANARY_FORCED_MISBEHAVE.items():
            for cid in MOCK_PROFILES:
                out = io.StringIO()
                rc = bc.cmd_canary(cid, misbehave=forced, out=out)
                self.assertEqual(rc, 1, out.getvalue())
                text = out.getvalue()
                self.assertIn("FAIL    %s %s: forced state %s caught"
                             % (cid, klass, forced), text)
                for other in bc.CANARY_CLASSES:
                    if other == klass:
                        continue
                    self.assertIn("PASS    %s %s:" % (cid, other), text,
                                 "%s's forced state %s leaked into %s: %s"
                                 % (klass, forced, other, text))

    def test_unwired_connector_is_no_data_naming_its_setup_step(self):
        out = io.StringIO()
        rc = bc.cmd_canary("azure", out=out)
        self.assertEqual(rc, 2)
        self.assertIn("NO-DATA", out.getvalue())
        self.assertIn("CATALOG_ID_TO_PROFILE", out.getvalue())
        self.assertIn("PROFILE_TO_CATALOG_ID", out.getvalue())

    def test_via_live_is_no_data_naming_open_test_path(self):
        for cid in MOCK_PROFILES:
            out = io.StringIO()
            rc = bc.cmd_canary(cid, via="live", out=out)
            self.assertEqual(rc, 2)
            self.assertIn("NO-DATA", out.getvalue())
            self.assertIn(bc.BY_ID[cid]["open_test_path"][:20], out.getvalue())

    def test_unknown_connector_is_exit_2(self):
        self.assertEqual(bc.cmd_canary("no-such-connector",
                                       out=io.StringIO(), err=io.StringIO()), 2)

    def test_all_flag_covers_every_catalog_connector_worst_code_wins(self):
        out = io.StringIO()
        rc = bc.cmd_canary_all(out=out)
        text = out.getvalue()
        self.assertEqual(rc, 2, text)  # azure's NO-DATA is the worst code present
        for cid in MOCK_PROFILES:
            self.assertIn(cid, text)
        self.assertIn("azure", text)

    def test_identity_pairing_matches_the_mock_fixture(self):
        # The expected pairing is duplicated rather than imported (avoids a
        # circular import with bm_mock_mcp, which already imports BY_ID
        # from this module); this proves the two copies never drift apart.
        sys.path.insert(0, HERE)
        import bm_mock_mcp as mm  # noqa: E402
        self.assertEqual(
            mm.IDENTITY_MAP[bc.IDENTITY_SOURCE_PRINCIPAL],
            bc.IDENTITY_EXPECTED_VAULT_PRINCIPAL)
        # And the wrong-mapping fixture must actually differ, or the
        # identity-mismatch misbehavior would silently stop being wrong.
        self.assertNotEqual(
            mm.WRONG_IDENTITY_MAP[bc.IDENTITY_SOURCE_PRINCIPAL],
            bc.IDENTITY_EXPECTED_VAULT_PRINCIPAL)

    def test_no_network_in_canary_path_either(self):
        # Same guarantee as MockConformance.test_mock_never_imports_a_network_module,
        # re-asserted here because the canary path is a second caller of the
        # same subprocess-spawning mock and must inherit the same posture.
        with io.open(bc.MOCK_SCRIPT, encoding="utf-8") as fh:
            src = fh.read()
        for banned in ("socket", "urllib", "http.server", "asyncio"):
            self.assertNotIn("import %s" % banned, src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
