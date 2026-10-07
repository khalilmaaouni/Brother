#!/usr/bin/env python3
"""Antigravity lifecycle hook block schemas (unit L1.3).

Single entry point for the L1.3 hook validators. Running this file executes
its unittest suite:

    python3 scripts/test_l1_hooks.py

Fail-closed contract: unlisted event names, corrupt or truncated JSON,
oversize input over 256 KB, and a PreToolUse payload missing toolCall all
return BLOCK with a fixed reason. PreInvocation output may carry only
injectSteps with ephemeralMessage and must never claim a block decision.
Missing, unknown or corrupt input is never the safe case.
"""

import json
import os
import unittest

VALID_EVENTS = frozenset(
    (
        "PreToolUse",
        "PostToolUse",
        "PreInvocation",
        "PostInvocation",
        "Stop",
    )
)

MAX_PAYLOAD_BYTES = 256 * 1024

BLOCK_BAD_TYPE = "BLOCK: invalid input type"
BLOCK_UNLISTED = "BLOCK: unlisted event"
BLOCK_OVERSIZE = "BLOCK: oversize input"
BLOCK_CORRUPT = "BLOCK: corrupt JSON"
BLOCK_NOT_OBJECT = "BLOCK: payload is not a JSON object"
BLOCK_NO_TOOL_CALL = "BLOCK: missing toolCall"
BLOCK_PREINV_DECISION = "BLOCK: PreInvocation must not claim a decision"
BLOCK_PREINV_STEPS = "BLOCK: invalid injectSteps"
BLOCK_CRASH = "BLOCK: internal failure"


def _byte_len(text):
    try:
        return len(text.encode("utf-8", "strict"))
    except (UnicodeError, AttributeError, TypeError):
        return None


def _parse_hook_payload(event, payload):
    """Return (data, None) or (None, refusal). Never raises on hostile input."""
    if not isinstance(event, str) or not isinstance(payload, str):
        return (None, BLOCK_BAD_TYPE)
    if event not in VALID_EVENTS:
        return (None, BLOCK_UNLISTED)
    size = _byte_len(payload)
    if size is None or size > MAX_PAYLOAD_BYTES:
        return (None, BLOCK_OVERSIZE)
    try:
        data = json.loads(payload)
    except (ValueError, TypeError, RecursionError):
        return (None, BLOCK_CORRUPT)
    if not isinstance(data, dict):
        return (None, BLOCK_NOT_OBJECT)
    return (data, None)


def validate_hook_input(event, payload):
    """Return (True, "") when valid else (False, BLOCK reason)."""
    try:
        data, refusal = _parse_hook_payload(event, payload)
        if refusal is not None:
            return (False, refusal)
        if event == "PreToolUse":
            tool_call = data.get("toolCall")
            if not isinstance(tool_call, dict):
                return (False, BLOCK_NO_TOOL_CALL)
            if not isinstance(tool_call.get("name"), str):
                return (False, BLOCK_NO_TOOL_CALL)
            if "args" not in tool_call:
                return (False, BLOCK_NO_TOOL_CALL)
        return (True, "")
    except Exception:
        return (False, BLOCK_CRASH)


def validate_hook_output(event, payload):
    """Return (True, "") when valid else (False, BLOCK reason)."""
    try:
        data, refusal = _parse_hook_payload(event, payload)
        if refusal is not None:
            return (False, refusal)
        if event == "PreInvocation":
            if "decision" in data:
                return (False, BLOCK_PREINV_DECISION)
            steps = data.get("injectSteps")
            if not isinstance(steps, list):
                return (False, BLOCK_PREINV_STEPS)
            for step in steps:
                if not isinstance(step, dict):
                    return (False, BLOCK_PREINV_STEPS)
                if not isinstance(step.get("ephemeralMessage"), str):
                    return (False, BLOCK_PREINV_STEPS)
        return (True, "")
    except Exception:
        return (False, BLOCK_CRASH)


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPEC_PATH = os.path.join(
    REPO_ROOT, "docs", "architecture", "ANTIGRAVITY-ADAPTER-SPEC.md"
)


def _pre_tool_use_payload():
    return json.dumps(
        {
            "toolCall": {"name": "run_command", "args": {"CommandLine": "ls"}},
            "stepIdx": 12,
            "conversationId": "conversation-1",
            "workspacePaths": ["/tmp/workspace"],
        }
    )


class HookInputTests(unittest.TestCase):
    def test_valid_pre_tool_use_parses(self):
        ok, reason = validate_hook_input("PreToolUse", _pre_tool_use_payload())
        self.assertTrue(ok, reason)
        self.assertEqual(reason, "")

    def test_unlisted_event_blocks(self):
        ok, reason = validate_hook_input("NotAnEvent", "{}")
        self.assertFalse(ok, "unlisted event must never be allowed")
        self.assertEqual(reason, BLOCK_UNLISTED)

    def test_corrupt_blocks(self):
        ok, reason = validate_hook_input("PreToolUse", '{"toolCall": {"name": ')
        self.assertFalse(ok)
        self.assertEqual(reason, BLOCK_CORRUPT)
        self.assertNotIn("toolCall", reason)

    def test_empty_stdin_blocks(self):
        ok, reason = validate_hook_input("PreToolUse", "")
        self.assertFalse(ok)
        self.assertEqual(reason, BLOCK_CORRUPT)

    def test_oversize_blocks(self):
        blob = "a" * (300 * 1024)
        payload = json.dumps({"toolCall": {"name": "x", "args": {"blob": blob}}})
        self.assertGreater(len(payload.encode("utf-8")), MAX_PAYLOAD_BYTES)
        ok, reason = validate_hook_input("PreToolUse", payload)
        self.assertFalse(ok)
        self.assertEqual(reason, BLOCK_OVERSIZE)

    def test_missing_toolcall_blocks(self):
        payload = json.dumps(
            {"stepIdx": 1, "conversationId": "c", "workspacePaths": []}
        )
        ok, reason = validate_hook_input("PreToolUse", payload)
        self.assertFalse(ok)
        self.assertEqual(reason, BLOCK_NO_TOOL_CALL)

    def test_missing_args_blocks(self):
        payload = json.dumps({"toolCall": {"name": "run_command"}})
        ok, reason = validate_hook_input("PreToolUse", payload)
        self.assertFalse(ok)
        self.assertEqual(reason, BLOCK_NO_TOOL_CALL)

    def test_non_object_payloads_block(self):
        for payload in ("null", "[]", "42", '"text"', "NaN", "true"):
            ok, reason = validate_hook_input("PreToolUse", payload)
            self.assertFalse(ok, payload)
            self.assertEqual(reason, BLOCK_NOT_OBJECT)

    def test_events_are_independent_over_many_runs(self):
        for _ in range(100):
            ok, _unused = validate_hook_input("PostToolUse", "{}")
            self.assertTrue(ok)
            blocked, _why = validate_hook_input("NotAnEvent", "{}")
            self.assertFalse(blocked)

    def test_hostile_inputs_refused(self):
        hostile = [
            None,
            42,
            True,
            3.5,
            float("nan"),
            [],
            {},
            b"{}",
            bytearray(b"{}"),
        ]
        for value in hostile:
            ok, reason = validate_hook_input(value, "{}")
            self.assertFalse(ok, "event %r must be refused" % (value,))
            self.assertTrue(reason.startswith("BLOCK"), reason)
            ok2, reason2 = validate_hook_input("PreToolUse", value)
            self.assertFalse(ok2, "payload %r must be refused" % (value,))
            self.assertTrue(reason2.startswith("BLOCK"), reason2)


class HookOutputTests(unittest.TestCase):
    def test_preinvocation_no_block(self):
        good = json.dumps({"injectSteps": [{"ephemeralMessage": "hello"}]})
        ok, reason = validate_hook_output("PreInvocation", good)
        self.assertTrue(ok, reason)
        bad = json.dumps({"decision": "deny", "reason": "no"})
        blocked, why = validate_hook_output("PreInvocation", bad)
        self.assertFalse(blocked)
        self.assertEqual(why, BLOCK_PREINV_DECISION)

    def test_preinvocation_requires_ephemeral_message(self):
        bad = json.dumps({"injectSteps": [{}]})
        ok, why = validate_hook_output("PreInvocation", bad)
        self.assertFalse(ok)
        self.assertEqual(why, BLOCK_PREINV_STEPS)

    def test_preinvocation_continue_is_still_a_decision(self):
        blocked, why = validate_hook_output(
            "PreInvocation", json.dumps({"decision": "continue"})
        )
        self.assertFalse(blocked)
        self.assertEqual(why, BLOCK_PREINV_DECISION)

    def test_output_unlisted_event_blocks(self):
        ok, why = validate_hook_output("Nope", "{}")
        self.assertFalse(ok)
        self.assertEqual(why, BLOCK_UNLISTED)

    def test_output_hostile_inputs_refused(self):
        hostile = [None, 7, True, float("nan"), b"{}", {}, []]
        for value in hostile:
            ok, _why = validate_hook_output(value, "{}")
            self.assertFalse(ok, "event %r must be refused" % (value,))
            ok2, _why2 = validate_hook_output("PreInvocation", value)
            self.assertFalse(ok2, "payload %r must be refused" % (value,))


class SpecHookSectionTests(unittest.TestCase):
    @unittest.skipUnless(os.path.isfile(SPEC_PATH), "spec doc is not present")
    def test_spec_states_hook_block_defaults(self):
        with open(SPEC_PATH, "rb") as handle:
            text = handle.read().decode("utf-8", errors="replace")
        for event in ("PreToolUse", "PostToolUse", "PreInvocation", "Stop"):
            self.assertIn(event, text)
        self.assertIn("256 KB", text)
        self.assertIn("injectSteps", text)
        self.assertIn("BLOCK", text)


if __name__ == "__main__":
    unittest.main()
