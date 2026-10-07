"""M1.4 selftest, mutation probe, installed copy and key components proof."""
import json
import os
import sys
import tempfile
import unittest

# The folder's own import form (as test_bm_repair_d16 beside this file): tools/test_all.py runs each suite as a
# script from this folder, where the repository root package does not exist. The hub rows still import the same file.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import bm_clock_guard as guard_module  # noqa: E402
from bm_clock_guard import (  # noqa: E402
    decide,
    selftest,
    selftest_mutation_probe,
    to_minutes,
    verify_installed,
    verify_key_components,
)


_PLUGIN_ROOT = os.path.dirname(_HERE)
_REPO_ROOT = os.path.dirname(os.path.dirname(_PLUGIN_ROOT))
_HOOKS_JSON = os.path.join(_PLUGIN_ROOT, "hooks", "hooks.json")
_KEY_COMPONENTS = os.path.join(_REPO_ROOT, "docs", "plan", "KEY-COMPONENTS.json")


def _write_json(path, data):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle)


class TestGuardSelftest(unittest.TestCase):
    def test_selftest_green(self):
        ok, message = selftest()
        self.assertTrue(ok, message)
        self.assertIsInstance(message, str)
        self.assertTrue(message)

    def test_selftest_mutation_probe_green(self):
        ok, message = selftest_mutation_probe()
        self.assertTrue(ok, message)

    def test_selftest_probe_red(self):
        original = guard_module.decide

        def broken_decide(text, minutes):
            return ("ALLOW", "OK")

        guard_module.decide = broken_decide
        try:
            ok, message = selftest_mutation_probe()
        finally:
            guard_module.decide = original
        self.assertFalse(ok, message)

    def test_selftest_probe_uses_shifted_evidence(self):
        base = to_minutes(9, 41)
        self.assertEqual(decide("meet at 09:41", [base]), ("ALLOW", "OK"))
        self.assertEqual(decide("meet at 09:41", [base + 30]), ("BLOCK", "MISMATCH"))


class TestVerifyInstalled(unittest.TestCase):
    def _make_full_fixture(self, folder, stop_command="python3 bm_clock_guard.py"):
        tools = os.path.join(folder, "tools")
        hooks = os.path.join(folder, "hooks")
        os.makedirs(tools, exist_ok=True)
        os.makedirs(hooks, exist_ok=True)
        with open(os.path.join(tools, "bm_clock_guard.py"), "w", encoding="utf-8") as handle:
            handle.write("value = 1\n")
        with open(os.path.join(tools, "test_bm_clock_guard.py"), "w", encoding="utf-8") as handle:
            handle.write("value = 1\n")
        hooks_data = {
            "hooks": {
                "PreToolUse": [
                    {"hooks": [{"type": "command", "command": "python3 bm_clock_guard.py"}]}
                ],
                "Stop": [
                    {"hooks": [{"type": "command", "command": stop_command}]}
                ],
            }
        }
        _write_json(os.path.join(hooks, "hooks.json"), hooks_data)

    @unittest.skipUnless(os.path.isfile(_HOOKS_JSON), "hooks.json not present")
    def test_verify_installed_real_tree(self):
        ok, message = verify_installed(_PLUGIN_ROOT)
        self.assertTrue(ok, message)

    def test_verify_installed_missing_files(self):
        with tempfile.TemporaryDirectory() as folder:
            ok, message = verify_installed(folder)
            self.assertFalse(ok)
            self.assertTrue(message)

    def test_verify_installed_full_fixture(self):
        with tempfile.TemporaryDirectory() as folder:
            self._make_full_fixture(folder)
            ok, message = verify_installed(folder)
            self.assertTrue(ok, message)

    def test_verify_installed_hooks_missing_guard_in_stop(self):
        with tempfile.TemporaryDirectory() as folder:
            self._make_full_fixture(folder, stop_command="python3 other_tool.py")
            ok, message = verify_installed(folder)
            self.assertFalse(ok)
            self.assertIn("Stop", message)

    def test_verify_installed_guard_does_not_compile(self):
        with tempfile.TemporaryDirectory() as folder:
            self._make_full_fixture(folder)
            with open(
                os.path.join(folder, "tools", "bm_clock_guard.py"), "w", encoding="utf-8"
            ) as handle:
                handle.write("def broken(:\n")
            ok, message = verify_installed(folder)
            self.assertFalse(ok)
            self.assertIn("compile", message)

    def test_verify_installed_test_file_missing(self):
        with tempfile.TemporaryDirectory() as folder:
            self._make_full_fixture(folder)
            os.remove(os.path.join(folder, "tools", "test_bm_clock_guard.py"))
            ok, message = verify_installed(folder)
            self.assertFalse(ok)

    def test_verify_installed_hostile(self):
        for bad in (None, 4, [], True, 3.5, b"path"):
            ok, message = verify_installed(bad)
            self.assertFalse(ok)
            self.assertTrue(message)


class TestVerifyKeyComponents(unittest.TestCase):
    def _write_plan(self, folder, payload):
        plan = os.path.join(folder, "docs", "plan")
        os.makedirs(plan, exist_ok=True)
        path = os.path.join(plan, "KEY-COMPONENTS.json")
        if isinstance(payload, str):
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(payload)
        else:
            _write_json(path, payload)
        return path

    def _entry(self, **overrides):
        entry = {
            "id": "clock-guard",
            "name": "clock-guard",
            "tool": "products/brothermode/tools/bm_clock_guard.py",
            "guard": "products/brothermode/tools/test_bm_clock_guard.py",
            "hooks": ["PreToolUse", "Stop"],
        }
        entry.update(overrides)
        return entry

    @unittest.skipUnless(os.path.isfile(_KEY_COMPONENTS), "key components not present")
    def test_verify_key_components(self):
        ok, message = verify_key_components(_REPO_ROOT)
        self.assertTrue(ok, message)

    def test_verify_key_components_missing(self):
        with tempfile.TemporaryDirectory() as folder:
            ok, message = verify_key_components(folder)
            self.assertFalse(ok)
            self.assertTrue(message)

    def test_verify_key_components_missing_entry(self):
        with tempfile.TemporaryDirectory() as folder:
            self._write_plan(folder, {"components": [{"id": "something-else"}]})
            ok, message = verify_key_components(folder)
            self.assertFalse(ok)

    def test_verify_key_components_wrong_tool(self):
        with tempfile.TemporaryDirectory() as folder:
            self._write_plan(folder, {"components": [self._entry(tool="tools/bm_clock_guard.py")]})
            ok, message = verify_key_components(folder)
            self.assertFalse(ok)
            self.assertIn("tool", message)

    def test_verify_key_components_right_entry(self):
        with tempfile.TemporaryDirectory() as folder:
            self._write_plan(folder, {"components": [self._entry()]})
            self.assertEqual(verify_key_components(folder), (True, "clock-guard entry verified"))

    def test_verify_key_components_wrong_guard(self):
        with tempfile.TemporaryDirectory() as folder:
            self._write_plan(folder, {"components": [self._entry(guard="wrong/path.py")]})
            ok, message = verify_key_components(folder)
            self.assertFalse(ok)
            self.assertIn("guard", message)

    def test_verify_key_components_wrong_hooks(self):
        with tempfile.TemporaryDirectory() as folder:
            self._write_plan(folder, {"components": [self._entry(hooks=["PreToolUse"])]})
            ok, message = verify_key_components(folder)
            self.assertFalse(ok)
            self.assertIn("hooks", message)

    def test_verify_key_components_full_fixture(self):
        with tempfile.TemporaryDirectory() as folder:
            self._write_plan(folder, {"components": [self._entry()]})
            ok, message = verify_key_components(folder)
            self.assertTrue(ok, message)

    def test_verify_key_components_malformed(self):
        with tempfile.TemporaryDirectory() as folder:
            self._write_plan(folder, "not json {{{")
            ok, message = verify_key_components(folder)
            self.assertFalse(ok)

    def test_verify_key_components_hostile(self):
        for bad in (None, 4, [], True, 3.5, b"path"):
            ok, message = verify_key_components(bad)
            self.assertFalse(ok)
            self.assertTrue(message)


if __name__ == "__main__":
    unittest.main()
