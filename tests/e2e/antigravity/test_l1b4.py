#!/usr/bin/env python3
"""L1b.4 fixtures and their single validated reader.

load_fixture is the only reader of a fixture file. Every caller (the E2E
harness, the log recorder, every checker) routes through it, so one
validation covers every caller. A fixture is a tool call payload for the
Antigravity hook boundary and must be a JSON object carrying a toolCall
object with a non-empty string name.

Missing, corrupt, non utf-8, duplicate key or wrong shape input is refused
with FixtureRefused (a ValueError); it is never read as the safe case.

A fixture whose filename or "kind" field marks it destructive, or that
carries a dry_run field anywhere, must carry dry_run true (the boolean, not
the string "true", not 1, not null), so a dropped guard can never let a
destructive payload be handed to the harness.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
HOOK_PATH = os.path.join(REPO_ROOT, "scripts", "brother_antigravity_hook.py")


def _load_hook():
    spec = importlib.util.spec_from_file_location("brother_antigravity_hook_l1b4", HOOK_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


HOOK = _load_hook()
KNOWN_TOOLS = HOOK.KNOWN_TOOLS

BENIGN_FIXTURE = os.path.join(HERE, "fixtures", "benign_tool_call.json")
ADVERSARIAL_FIXTURE = os.path.join(HERE, "fixtures", "adversarial_tool_call.json")
DESTRUCTIVE_FIXTURE = os.path.join(HERE, "fixtures", "destructive_known_tool.json")
ROOT_DESTRUCTIVE_FIXTURE = os.path.join(REPO_ROOT, "destructive_known_tool.json")


class FixtureRefused(ValueError):
    """A fixture was missing, corrupt or of the wrong shape."""


def _refuse(message):
    raise FixtureRefused(message)


def _read_bytes(path):
    """The single byte reader every fixture caller routes through."""
    if not isinstance(path, str):
        _refuse("fixture path must be a string, got %s" % type(path).__name__)
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except OSError as exc:
        _refuse("cannot read fixture %r: %s" % (path, exc))


def fixture_sha256(path):
    """Return the sha256 hex digest of the fixture bytes at path."""
    raw = _read_bytes(path)
    return hashlib.sha256(raw).hexdigest()


def _no_duplicate_keys(pairs):
    seen = {}
    for key, value in pairs:
        if key in seen:
            _refuse("fixture has duplicate JSON key %r" % (key,))
        seen[key] = value
    return seen


def _reject_json_constant(value):
    _refuse("invalid JSON constant: %s" % value)


def _iter_dry_run_fields(node, path):
    found = []
    if isinstance(node, dict):
        for key, value in node.items():
            child = "%s.%s" % (path, key) if path else str(key)
            if key == "dry_run":
                found.append((child, value))
            found.extend(_iter_dry_run_fields(value, child))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found.extend(_iter_dry_run_fields(value, "%s[%d]" % (path, index)))
    return found


def load_fixture(path):
    """Load one fixture payload as bytes, parse it, and refuse corrupt input.

    Refused with FixtureRefused: unreadable path, a path that is not a
    string, non utf-8 bytes, invalid JSON, duplicate JSON keys, a body that
    is not a JSON object, a toolCall that is not an object, a missing, empty
    or non-string toolCall name, non-object args, a destructive fixture
    without a guard, and any dry_run field whose value is not the boolean
    true.
    """
    raw = _read_bytes(path)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        _refuse("fixture is not valid utf-8: %s" % exc)
    try:
        data = json.loads(
            text,
            parse_constant=_reject_json_constant,
            object_pairs_hook=_no_duplicate_keys,
        )
    except ValueError as exc:
        _refuse("fixture is not valid JSON: %s" % exc)
    if not isinstance(data, dict):
        _refuse("fixture must be a JSON object")
    kind = data.get("kind")
    if kind is not None and not isinstance(kind, str):
        _refuse("fixture kind must be a string")
    tool_call = data.get("toolCall")
    if not isinstance(tool_call, dict):
        _refuse("fixture must carry a toolCall object")
    tool_name = tool_call.get("name")
    if not isinstance(tool_name, str) or not tool_name.strip():
        _refuse("fixture toolCall must carry a non-empty string name")
    if "args" in tool_call and not isinstance(tool_call["args"], dict):
        _refuse("fixture toolCall args must be an object when present")
    name = os.path.basename(path).lower()
    filename_marks_destructive = "destructive" in name
    kind_marks_destructive = isinstance(kind, str) and kind.lower() == "destructive"
    guard_fields = _iter_dry_run_fields(data, "")
    if filename_marks_destructive or kind_marks_destructive or guard_fields:
        for where, guarded in guard_fields:
            if guarded is not True:
                _refuse("fixture dry_run at %s must be boolean true, got %r" % (where, guarded))
        if not guard_fields:
            _refuse("destructive fixture must carry dry_run true")
    return data


class TestFixtures(unittest.TestCase):
    def _write(self, directory, name, text):
        path = os.path.join(directory, name)
        with open(path, "wb") as handle:
            handle.write(text.encode("utf-8"))
        return path

    def test_benign_fixture_shape(self):
        data = load_fixture(BENIGN_FIXTURE)
        self.assertEqual(data["toolCall"]["name"], "run_command")
        self.assertEqual(data["toolCall"]["args"]["CommandLine"], "ls -la")

    def test_benign_tool_is_known(self):
        data = load_fixture(BENIGN_FIXTURE)
        self.assertIn(data["toolCall"]["name"], KNOWN_TOOLS)

    def test_adversarial_name_unknown(self):
        data = load_fixture(ADVERSARIAL_FIXTURE)
        name = data["toolCall"]["name"]
        self.assertEqual(name, "dangerous_unknown_tool")
        self.assertNotIn(name, KNOWN_TOOLS)

    def test_destructive_guard_present(self):
        data = load_fixture(DESTRUCTIVE_FIXTURE)
        self.assertIs(data.get("dry_run"), True)
        self.assertEqual(data["toolCall"]["name"], "run_command")
        self.assertEqual(data["toolCall"]["args"]["CommandLine"], "rm -rf docs/")

    def test_destructive_tool_is_known(self):
        data = load_fixture(DESTRUCTIVE_FIXTURE)
        self.assertIn(data["toolCall"]["name"], KNOWN_TOOLS)

    def test_destructive_guard_is_boolean_true_not_truthy(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(
                tmp,
                "destructive_payload.json",
                '{"dry_run": 1, "toolCall": {"name": "run_command", "args": {}}}',
            )
            with self.assertRaises(FixtureRefused):
                load_fixture(path)

    def test_kind_destructive_refused_without_guard(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(
                tmp,
                "payload.json",
                '{"kind": "destructive", "toolCall": {"name": "run_command", "args": {}}}',
            )
            with self.assertRaises(FixtureRefused):
                load_fixture(path)

    def test_filename_destructive_refused_without_guard(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(
                tmp,
                "something_destructive.json",
                '{"toolCall": {"name": "run_command", "args": {}}}',
            )
            with self.assertRaises(FixtureRefused):
                load_fixture(path)

    def test_non_string_kind_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(
                tmp,
                "payload.json",
                '{"kind": 1, "toolCall": {"name": "run_command", "args": {}}}',
            )
            with self.assertRaises(FixtureRefused):
                load_fixture(path)

    def test_non_destructive_kind_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(
                tmp,
                "payload.json",
                '{"kind": "benign", "toolCall": {"name": "run_command", "args": {}}}',
            )
            data = load_fixture(path)
            self.assertEqual(data["kind"], "benign")

    def test_destructive_with_guard_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(
                tmp,
                "destructive_with_guard.json",
                '{"kind": "destructive", "dry_run": true, "toolCall": {"name": "run_command", "args": {}}}',
            )
            data = load_fixture(path)
            self.assertIs(data["dry_run"], True)

    def test_dry_run_must_be_boolean_true(self):
        for body in ('"true"', '"True"', '"yes"', "1", "0", "null", "[]", "{}", "7"):
            with tempfile.TemporaryDirectory() as tmp:
                path = self._write(
                    tmp,
                    "payload.json",
                    '{"dry_run": %s, "toolCall": {"name": "run_command", "args": {"CommandLine": "rm -rf docs/"}}, "stepIdx": 3}' % body,
                )
                with self.assertRaises(FixtureRefused):
                    load_fixture(path)

    def test_dry_run_false_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(
                tmp,
                "payload.json",
                '{"dry_run": false, "toolCall": {"name": "run_command", "args": {"CommandLine": "rm -rf docs/"}}, "stepIdx": 3}',
            )
            with self.assertRaises(FixtureRefused):
                load_fixture(path)

    def test_deep_dry_run_string_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(
                tmp,
                "payload.json",
                '{"toolCall": {"name": "run_command", "args": {"CommandLine": "rm -rf docs/", "dry_run": "true"}}, "stepIdx": 3}',
            )
            with self.assertRaises(FixtureRefused):
                load_fixture(path)

    def test_duplicate_keys_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(
                tmp,
                "payload.json",
                '{"toolCall": {"name": "run_command"}, "toolCall": {"name": "dangerous_unknown_tool"}}',
            )
            with self.assertRaises(FixtureRefused):
                load_fixture(path)

    def test_duplicate_keys_nested_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(
                tmp,
                "payload.json",
                '{"toolCall": {"name": "run_command", "name": "dangerous_unknown_tool", "args": {}}}',
            )
            with self.assertRaises(FixtureRefused):
                load_fixture(path)

    def test_true_guard_accepted_on_any_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(
                tmp,
                "payload.json",
                '{"dry_run": true, "toolCall": {"name": "run_command", "args": {"CommandLine": "ls -la"}}}',
            )
            data = load_fixture(path)
            self.assertIs(data["dry_run"], True)

    def test_invalid_json_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, "broken.json", "{ broken")
            with self.assertRaises(FixtureRefused):
                load_fixture(path)

    def test_missing_file_refused(self):
        missing = os.path.join(tempfile.gettempdir(), "no_such_fixture_l1b4.json")
        if os.path.exists(missing):
            os.remove(missing)
        with self.assertRaises(FixtureRefused):
            load_fixture(missing)

    def test_hostile_path_types_refused(self):
        for bad in (None, 1, 1.5, True, b"path", [], {}, float("nan")):
            with self.assertRaises(FixtureRefused):
                load_fixture(bad)
            with self.assertRaises(FixtureRefused):
                fixture_sha256(bad)

    def test_directory_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FixtureRefused):
                load_fixture(tmp)
            with self.assertRaises(FixtureRefused):
                fixture_sha256(tmp)

    def test_non_utf8_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "bad.json")
            with open(path, "wb") as handle:
                handle.write(b"\xff\xfe\x00\x01")
            with self.assertRaises(FixtureRefused):
                load_fixture(path)

    def test_nan_json_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, "nan.json", '{"value": NaN}')
            with self.assertRaises(FixtureRefused):
                load_fixture(path)

    def test_non_object_body_refused(self):
        for body in ("[]", "7", "null", '"run_command"', "1.5", "true"):
            with tempfile.TemporaryDirectory() as tmp:
                path = self._write(tmp, "payload.json", body)
                with self.assertRaises(FixtureRefused):
                    load_fixture(path)

    def test_toolcall_missing_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, "payload.json", '{"kind": "benign"}')
            with self.assertRaises(FixtureRefused):
                load_fixture(path)

    def test_toolcall_none_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, "payload.json", '{"kind": "benign", "toolCall": null}')
            with self.assertRaises(FixtureRefused):
                load_fixture(path)

    def test_toolcall_not_object_refused(self):
        for body in ('7', '[]', '"run_command"', 'true', '1.5'):
            with tempfile.TemporaryDirectory() as tmp:
                path = self._write(tmp, "payload.json", '{"toolCall": %s}' % body)
                with self.assertRaises(FixtureRefused):
                    load_fixture(path)

    def test_toolcall_name_missing_or_empty_refused(self):
        for body in ('{}', '{"args": {}}', '{"name": ""}', '{"name": "   "}',
                     '{"name": 7}', '{"name": null}', '{"name": true}'):
            with tempfile.TemporaryDirectory() as tmp:
                path = self._write(tmp, "payload.json", '{"toolCall": %s}' % body)
                with self.assertRaises(FixtureRefused):
                    load_fixture(path)

    def test_toolcall_args_not_object_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(
                tmp,
                "payload.json",
                '{"toolCall": {"name": "run_command", "args": "ls -la"}}',
            )
            with self.assertRaises(FixtureRefused):
                load_fixture(path)

    def test_each_load_is_independent(self):
        first = load_fixture(BENIGN_FIXTURE)
        first["toolCall"]["name"] = "mutated"
        second = load_fixture(BENIGN_FIXTURE)
        self.assertEqual(second["toolCall"]["name"], "run_command")

    def test_fixture_sha256_matches_bytes(self):
        with open(BENIGN_FIXTURE, "rb") as handle:
            expected = hashlib.sha256(handle.read()).hexdigest()
        digest = fixture_sha256(BENIGN_FIXTURE)
        self.assertEqual(digest, expected)
        self.assertEqual(len(digest), 64)

    @unittest.skipUnless(os.path.isfile(ROOT_DESTRUCTIVE_FIXTURE), "root destructive fixture is not present in this export")
    def test_root_destructive_fixture_is_refused_without_guard(self):
        with self.assertRaises(FixtureRefused):
            load_fixture(ROOT_DESTRUCTIVE_FIXTURE)


if __name__ == "__main__":
    unittest.main()
