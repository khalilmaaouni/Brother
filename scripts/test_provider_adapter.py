#!/usr/bin/env python3
"""provider_adapter, pinned: one neutral core, three thin adapters, and a
Cortex adapter that answers NO-DATA for everything until somebody measures
it. No network, no real provider binary."""

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import provider_adapter as PA  # noqa: E402

CAPABILITIES = ("invocation", "worker_call", "hook_events", "tool_events",
                "sandbox_grants", "auth_discovery", "capability_discovery",
                "paths", "install", "upgrade", "rollback", "uninstall", "resume")


class Registry(unittest.TestCase):
    def test_three_adapters_registered(self):
        self.assertEqual(sorted(PA.ADAPTERS), ["claude", "codex", "cortex"])

    def test_every_adapter_answers_every_capability(self):
        for name, cls in PA.ADAPTERS.items():
            adapter = cls(env={})
            for cap in CAPABILITIES:
                self.assertTrue(callable(getattr(adapter, cap, None)), (name, cap))


class CortexIsNoData(unittest.TestCase):
    def test_every_capability_is_a_refusal(self):
        adapter = PA.CortexAdapter(env={})
        for cap in CAPABILITIES:
            self.assertIsInstance(getattr(adapter, cap)(), PA.Refusal, cap)

    def test_a_fake_binary_does_not_lift_the_refusals(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = os.path.join(tmp, "cortex")
            with open(fake, "w") as fh:
                fh.write("#!/bin/sh\necho cortex 0.0.1\n")
            os.chmod(fake, 0o755)
            adapter = PA.CortexAdapter(env={"BROTHER_CORTEX_BIN": fake})
            for cap in CAPABILITIES:
                self.assertIsInstance(getattr(adapter, cap)(), PA.Refusal, cap)


class Describe(unittest.TestCase):
    def test_describe_prints_valid_json_for_all_three(self):
        for provider in ("claude", "codex", "cortex", "all"):
            proc = subprocess.run([sys.executable, os.path.join(HERE, "provider_adapter.py"),
                                   "describe", "--provider", provider],
                                  capture_output=True, text=True, env=dict(os.environ))
            self.assertEqual(proc.returncode, 0, proc.stderr)
            json.loads(proc.stdout)


class NoProviderBranchingInTheCore(unittest.TestCase):
    """The core never branches on a provider name outside the adapter classes
    and the registry: every line naming "claude", "codex" or "cortex" as a
    string literal sits inside a class body or the ADAPTERS registry."""

    def test_string_literals_only_inside_classes_or_registry(self):
        path = os.path.join(HERE, "provider_adapter.py")
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        in_class = False
        in_registry = False
        offenders = []
        for n, line in enumerate(lines, 1):
            if re.match(r"^class \w+", line):
                in_class = True
            elif re.match(r"^(def |[A-Z_]+ = )", line) and not line.startswith("ADAPTERS"):
                in_class = False
            if line.startswith("ADAPTERS"):
                in_registry = True
            elif in_registry and re.match(r"^\S", line):
                in_registry = False
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'''"):
                continue
            if re.search(r"[\"'](claude|codex|cortex)[\"']", line) and not (in_class or in_registry):
                offenders.append((n, line.strip()))
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
