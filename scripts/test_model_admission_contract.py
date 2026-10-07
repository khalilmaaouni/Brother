#!/usr/bin/env python3
"""No network calls: shared admission contracts for all model transports."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts/loop"))
PLUGIN = (ROOT / "plugin" / "runtime" / "brother" / "core" / "dispatch_semaphore.py").is_file()
if PLUGIN:   # the pool this suite tests is not shipped in the public export (finding 32); there it skips, never errors
    from plugin.runtime.brother.core import dispatch_semaphore as slots
    import model_call


@unittest.skipUnless(PLUGIN, "the plugin runtime (the shared admission pool) is not shipped in this tree")
class AdmissionContract(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = self.temp.name
        self.env = patch.dict(os.environ, {"BROTHER_OR_STATE_ROOT": self.root})
        self.env.start()
        self.addCleanup(self.env.stop)

    def policy(self, value):
        Path(self.root, "dispatch-policy.json").write_text(json.dumps(value))

    def test_widest_caller_cannot_expand_shared_pool(self):
        self.policy({"max_concurrent_calls": 2})
        held = [slots.acquire_slot(self.root, n, "owner", timeout_seconds=.01)
                for n in (2, 99)]
        try:
            with self.assertRaises(slots.NoSlotAvailable):
                slots.acquire_slot(self.root, 999, "overflow", timeout_seconds=.01)
        finally:
            for handle in held:
                slots.release_slot(handle)

    def test_default_is_bounded_and_caller_may_narrow(self):
        self.assertEqual(slots.effective_slots(self.root, 99), 4)
        self.assertEqual(slots.effective_slots(self.root, 2), 2)

    def test_malformed_policy_refuses(self):
        for policy in ([], {}, {"max_concurrent_calls": True},
                       {"max_concurrent_calls": 0}, {"max_concurrent_calls": 1.5}):
            with self.subTest(policy=policy):
                self.policy(policy)
                with self.assertRaises(ValueError):
                    slots.acquire_slot(self.root, 99, "owner", timeout_seconds=.01)

    def test_invalid_wait_values_refuse_instead_of_waiting_forever(self):
        for value in (True, float("nan"), float("inf"), -1, "1"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    slots.acquire_slot(self.root, 1, "owner", timeout_seconds=value)

    def call(self, transport, runner):
        reg = {"fake": {"transport": transport, "id": "fake"}}
        with patch.object(model_call.R, "assert_may_send"), \
             patch.object(model_call, "_argv", return_value=(["fake"], "", None)), \
             patch.object(model_call, "_claude_result", return_value=("ok", "")), \
             patch.object(model_call, "_log_bridge"):
            return model_call.call_one("fake", "fixture", "build", "public",
                                       timeout=.01, reg=reg, runner=runner)

    def test_all_transports_wait_before_touching_the_wire(self):
        self.policy({"max_concurrent_calls": 1})
        held = slots.acquire_slot(self.root, 1, "busy", timeout_seconds=.01)
        calls = []
        try:
            for transport in ("bridge", "claude", "codex"):
                with self.subTest(transport=transport):
                    result = self.call(transport, lambda *args: calls.append(args))
                    self.assertFalse(result.ok)
                    self.assertIn("admission refused", result.detail)
        finally:
            slots.release_slot(held)
        self.assertEqual(calls, [])

    def test_failure_releases_slot_for_next_transport(self):
        self.policy({"max_concurrent_calls": 1})
        def fail(*args):
            raise OSError("fixture transport unavailable")
        self.assertFalse(self.call("claude", fail).ok)
        result = self.call("codex", lambda *args: {
            "returncode": 0, "stdout": "ok", "stderr": ""})
        self.assertTrue(result.ok)


if __name__ == "__main__":
    unittest.main()
