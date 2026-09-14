#!/usr/bin/env python3
"""U6: bm_mcp_server.py as a genuinely OPTIONAL bundled component.

Codex review (docs/plan/1.0.17/WBS-70-CODEX-U3-U9-REVIEW-2026-09-13.txt,
U6 section): the product server and products/brothermode/mcp.json exist;
bundle/mcp.json and a bundled server did not. Shipping requires generator
support in scripts/bundle_runtime.py (bundle_runtime.generate_mcp /
check_mcp), the generated server and manifest entries, installer
validation, and package tests proving (1) dependency loading works when
the sibling tools/ mirror is present, and (2) the server degrades to a
clear "unavailable" report, never a crash or a silent partial success,
when that sibling is missing.

This file proves both ends against the REAL generated server process
over stdio (never by calling its functions in-process): the closure
under test is the whole "does the installed layout actually work"
question, which an in-process call cannot exercise.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import bundle_runtime as BR  # noqa: E402

try:
    import tmp_sandbox
    tmp_sandbox.install()
except ImportError:
    pass

PRODUCT_MCP_SERVER = os.path.join(REPO, "products", "brothermode", "mcp",
                                  "bm_mcp_server.py")
PRODUCT_BM_STORE = os.path.join(REPO, "products", "brothermode", "tools",
                                "bm_store.py")


def _drive(server_path, tool, arguments, cwd=None):
    """One real stdio JSON-RPC session against `server_path`: initialize,
    notifications/initialized, tools/list, then one tools/call. Returns
    (returncode, is_error, text, combined_output). Never raises on a bad
    child process: a crash must show up as an assertion failure in the
    caller, not as a test error here."""
    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                    "clientInfo": {"name": "test_bundle_mcp", "version": "1"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": tool, "arguments": arguments}},
    ]
    p = subprocess.run(
        [sys.executable, server_path],
        input="".join(json.dumps(m) + "\n" for m in msgs),
        cwd=cwd, capture_output=True, text=True, timeout=30)
    tools_listed = None
    is_error = None
    text = None
    for line in p.stdout.splitlines():
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if obj.get("id") == 2:
            tools_listed = [t["name"] for t in
                            (obj.get("result") or {}).get("tools", [])]
        if obj.get("id") == 3:
            result = obj.get("result") or {}
            is_error = result.get("isError")
            text = "".join(c.get("text", "")
                          for c in result.get("content", []))
    return p.returncode, tools_listed, is_error, text, p.stdout + p.stderr


class GenerateMcpMirrorsTheOptionalServer(unittest.TestCase):
    """generate_mcp() / check_mcp(): the product ships a server, so it must
    be mirrored next to the already-mirrored tools/, and bundle/mcp.json
    must point at the mirrored copy, not at the dev-checkout template's
    own ${PLUGIN_ROOT}/mcp/ path."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bundle-mcp-")
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.products_dir = os.path.join(self.tmp, "products")
        self.runtime_dir = os.path.join(self.tmp, "bundle", "runtime")
        product_mcp_dir = os.path.join(self.products_dir, "brothermode", "mcp")
        os.makedirs(product_mcp_dir)
        shutil.copy2(PRODUCT_MCP_SERVER,
                    os.path.join(product_mcp_dir, "bm_mcp_server.py"))

    def test_absent_source_mirrors_nothing_and_reports_no_drift(self):
        empty_products = os.path.join(self.tmp, "no-mcp-products")
        os.makedirs(os.path.join(empty_products, "brothermode", "tools"))
        changed = BR.generate_mcp(products_dir=empty_products,
                                  runtime_dir=self.runtime_dir)
        self.assertEqual(changed, [],
                         "a product with no mcp/bm_mcp_server.py must "
                         "mirror nothing: the component is optional")
        ok, problems = BR.check_mcp(products_dir=empty_products,
                                    runtime_dir=self.runtime_dir)
        self.assertTrue(ok, problems)
        self.assertEqual(problems, [])

    def test_present_source_is_mirrored_next_to_tools_and_manifest_json_written(self):
        changed = BR.generate_mcp(products_dir=self.products_dir,
                                  runtime_dir=self.runtime_dir)
        self.assertIn("runtime/hooks/brothermode/mcp/bm_mcp_server.py", changed)
        self.assertIn("mcp.json", changed)
        mirrored = os.path.join(self.runtime_dir, "hooks", "brothermode",
                                "mcp", "bm_mcp_server.py")
        self.assertTrue(os.path.isfile(mirrored))
        with open(PRODUCT_MCP_SERVER, "rb") as fh:
            source_bytes = fh.read()
        with open(mirrored, "rb") as fh:
            self.assertEqual(source_bytes, fh.read(),
                             "mirrored copy must be byte-identical to its "
                             "products/ source")
        bundle_dir = os.path.dirname(self.runtime_dir)
        mcp_json_path = os.path.join(bundle_dir, "mcp.json")
        with open(mcp_json_path, encoding="utf-8") as fh:
            doc = json.load(fh)
        entry = doc["mcpServers"]["brothermode"]
        # The whole point of the U6 fix: cwd must name the NESTED checkout
        # (runtime/hooks/brothermode), the settled U3 contract, not
        # ${PLUGIN_ROOT}/mcp where the dev-checkout template points and
        # where nothing is ever mirrored.
        self.assertEqual(entry["cwd"],
                         "${PLUGIN_ROOT}/runtime/hooks/brothermode")
        self.assertEqual(entry["args"], ["mcp/bm_mcp_server.py"])

    def test_second_generation_on_unchanged_source_writes_nothing(self):
        BR.generate_mcp(products_dir=self.products_dir,
                        runtime_dir=self.runtime_dir)
        changed = BR.generate_mcp(products_dir=self.products_dir,
                                  runtime_dir=self.runtime_dir)
        self.assertEqual(changed, [])

    def test_check_mcp_catches_a_stale_mirror(self):
        BR.generate_mcp(products_dir=self.products_dir,
                        runtime_dir=self.runtime_dir)
        mirrored = os.path.join(self.runtime_dir, "hooks", "brothermode",
                                "mcp", "bm_mcp_server.py")
        with open(mirrored, "a", encoding="utf-8") as fh:
            fh.write("\n# drift\n")
        ok, problems = BR.check_mcp(products_dir=self.products_dir,
                                    runtime_dir=self.runtime_dir)
        self.assertFalse(ok)
        self.assertTrue(any("does not match its products/ source" in p
                            for p in problems), problems)

    def test_check_mcp_catches_a_missing_mcp_json(self):
        BR.generate_mcp(products_dir=self.products_dir,
                        runtime_dir=self.runtime_dir)
        bundle_dir = os.path.dirname(self.runtime_dir)
        os.unlink(os.path.join(bundle_dir, "mcp.json"))
        ok, problems = BR.check_mcp(products_dir=self.products_dir,
                                    runtime_dir=self.runtime_dir)
        self.assertFalse(ok)
        self.assertTrue(any("mcp.json: missing" in p for p in problems),
                        problems)


class TheGeneratedServerWorksWithTheRealLayout(unittest.TestCase):
    """Drives the ACTUAL committed bundle output (bundle/runtime/hooks/
    brothermode/mcp/bm_mcp_server.py, bundle/runtime/hooks/brothermode/
    tools/bm_store.py) over real stdio, proving the __file__-relative
    sibling lookup this server already uses (importlib.util.
    spec_from_file_location against ../tools/bm_store.py, never a bare
    sys.path insert) resolves correctly in the installed layout the
    generator produces, from a cwd that is not this repository at all."""

    def setUp(self):
        self.server = os.path.join(REPO, "bundle", "runtime", "hooks",
                                   "brothermode", "mcp", "bm_mcp_server.py")
        if not os.path.isfile(self.server):
            self.skipTest("bundle/runtime/hooks/brothermode/mcp/"
                          "bm_mcp_server.py not generated in this tree; "
                          "run scripts/bundle_runtime.py first")
        self.tmp = tempfile.mkdtemp(prefix="bundle-mcp-project-")
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))

    def test_tools_list_and_bm_status_answer_from_an_unrelated_cwd(self):
        # A throwaway project, no .brothermode marker yet: bm_status must
        # still resolve the root (via .git) and report "no store exists",
        # never crash, from a server process launched with cwd set to
        # something that is NOT this repository checkout.
        subprocess.run(["git", "init", "-q", self.tmp], check=True)
        outside_cwd = tempfile.mkdtemp(prefix="bundle-mcp-outside-cwd-")
        self.addCleanup(lambda: shutil.rmtree(outside_cwd, ignore_errors=True))
        returncode, tools_listed, is_error, text, everything = _drive(
            self.server, "bm_status", {"project_root": self.tmp},
            cwd=outside_cwd)
        self.assertEqual(returncode, 0, everything)
        self.assertIsNotNone(tools_listed, everything)
        self.assertEqual(sorted(tools_listed),
                         ["bm_active_work", "bm_decisions", "bm_fences",
                          "bm_status"])
        self.assertFalse(is_error, text)
        self.assertIn("no store exists", text)
        self.assertNotIn("Traceback", everything)


class TheGeneratedServerDegradesCleanlyWhenTheLayoutDiverges(unittest.TestCase):
    """The heart of U6's done-check: an installed layout that diverges
    (the sibling tools/ mirror missing, or bm_store.py itself missing
    from it) must make this server report itself UNAVAILABLE through the
    protocol, isError: true with a clear reason, exactly the way other
    modules in this repo report NO-DATA honestly rather than fabricating
    success. It must never crash and never silently answer as though the
    dependency loaded. Proven by copying the server ALONE into an
    isolated directory, with no ../tools sibling at all, and driving it
    exactly like the working case above; its own source is never
    modified for this test."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bundle-mcp-isolated-")
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))

    def _copy_server_alone(self):
        mcp_dir = os.path.join(self.tmp, "mcp")
        os.makedirs(mcp_dir)
        dst = os.path.join(mcp_dir, "bm_mcp_server.py")
        shutil.copy2(PRODUCT_MCP_SERVER, dst)
        # No tools/ sibling created at all: the layout this server needs
        # (mcp/ and tools/ as siblings, per the Codex U6 finding) does not
        # exist here on purpose.
        return dst

    def test_initialize_and_tools_list_still_answer_with_no_sibling_tools_dir(self):
        server = self._copy_server_alone()
        returncode, tools_listed, is_error, text, everything = _drive(
            server, "bm_status", {"project_root": self.tmp})
        self.assertEqual(returncode, 0, everything)
        self.assertIsNotNone(tools_listed, everything)
        self.assertEqual(sorted(tools_listed),
                         ["bm_active_work", "bm_decisions", "bm_fences",
                          "bm_status"],
                         "tools/list must still answer with the full "
                         "schema; a missing dependency is reported per "
                         "call, never by silently shrinking the tool list")

    def test_tools_call_reports_unavailable_not_a_crash_not_silent_success(self):
        server = self._copy_server_alone()
        returncode, _tools_listed, is_error, text, everything = _drive(
            server, "bm_status", {"project_root": self.tmp})
        self.assertEqual(returncode, 0, everything)
        self.assertTrue(is_error, "a missing tools/bm_store.py sibling "
                        "must be reported as isError: true, not answered "
                        "as though the store loaded: %s" % text)
        self.assertIn("could not be loaded", text)
        self.assertNotIn("Traceback", everything,
                         "a missing sibling must degrade through the "
                         "protocol, never crash the process")

    def test_every_tool_degrades_the_same_way_none_half_registered(self):
        server = self._copy_server_alone()
        for tool, args in (
                ("bm_status", {"project_root": self.tmp}),
                ("bm_active_work", {"project_root": self.tmp}),
                ("bm_fences", {"project_root": self.tmp}),
                ("bm_decisions", {"project_root": self.tmp})):
            returncode, _tools_listed, is_error, text, everything = _drive(
                server, tool, args)
            self.assertEqual(returncode, 0, everything)
            self.assertTrue(is_error, "%s: %s" % (tool, text))
            self.assertIn("could not be loaded", text)

    def test_syntactically_broken_sibling_also_degrades_not_crashes(self):
        # Layout DIVERGES a second way: tools/ exists but bm_store.py
        # itself is corrupt/unreadable as Python, the case a bare
        # sys.path insert would surface as an ImportError deep in a
        # caller's stack rather than as a clean tool-level refusal.
        mcp_dir = os.path.join(self.tmp, "mcp")
        os.makedirs(mcp_dir)
        server = os.path.join(mcp_dir, "bm_mcp_server.py")
        shutil.copy2(PRODUCT_MCP_SERVER, server)
        tools_dir = os.path.join(self.tmp, "tools")
        os.makedirs(tools_dir)
        with open(os.path.join(tools_dir, "bm_store.py"), "w",
                 encoding="utf-8") as fh:
            fh.write("this is not valid python (((\n")
        returncode, tools_listed, is_error, text, everything = _drive(
            server, "bm_status", {"project_root": self.tmp})
        self.assertEqual(returncode, 0, everything)
        self.assertIsNotNone(tools_listed, everything)
        self.assertTrue(is_error, text)
        self.assertIn("could not be loaded", text)
        self.assertNotIn("Traceback", everything)


if __name__ == "__main__":
    unittest.main()
