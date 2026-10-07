#!/usr/bin/env python3
"""L1b.0 host contract capture for the Antigravity end to end gate.

The contract is read out of the real hook adapter: the tool map keys, the
known tool names, the stdin cap, the per event shape fields and types, each
handler's clean output object, and the list of modes main accepts. Host
facts that are readable right now are captured beside them: plugin file
hashes, hooks byte length, skills and rules counts.

A required real code fact that is missing or wrongly typed is reported by
check_unknowns. A host fact that could not be read is recorded as the
literal string no_data and is reported by check_host_facts. Neither ever
reads absence as the safe case. Corrupt or wrongly typed arguments are
refused with ValueError. Files are read as bytes.

Python 3.9+, standard library only.
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import math
import os
import re

NO_DATA = "no_data"

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
HOOK_PATH = os.path.join(ROOT, "scripts", "brother_antigravity_hook.py")
PLUGIN_DIR = os.path.join(ROOT, "bundle", ".antigravity-plugin")
LOG_PATH = os.path.join(ROOT, "docs", "architecture", "ANTIGRAVITY-E2E-TEST-LOG.md")

REQUIRED_REAL_CODE_FACTS = (
    "tool_map_keys",
    "known_tools",
    "max_stdin_chars",
    "event_shape",
    "handler_outputs",
    "main_modes",
)

HOST_FACT_KEYS = (
    "plugin_path_abs",
    "manifest_sha256",
    "mcp_config_sha256",
    "hooks_sha256",
    "hooks_byte_length",
    "skills_count",
    "rules_count",
    "host_name",
    "host_version",
    "load_argv",
    "resolved_cwd",
    "matcher_proof",
    "benign_pre_tool_stdin",
    "benign_pre_tool_stdout",
)

COUNT_FACT_KEYS = ("hooks_byte_length", "skills_count", "rules_count")
HASH_FACT_KEYS = ("manifest_sha256", "mcp_config_sha256", "hooks_sha256")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
MODE_CALL = re.compile(r"mode in \(([^)]*)\)")
MODE_NAME = re.compile(r'"([^"]+)"')


def _require_path(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("%s must be a non empty str, got %s" % (name, type(value).__name__))
    return value


def _reject_non_string_keys(value, where):
    if isinstance(value, dict):
        try:
            items = list(value.items())
        except TypeError as exc:
            raise ValueError("cannot inspect mapping at %s: %s" % (where, exc))
        for key, item in items:
            if not isinstance(key, str):
                raise ValueError("non string key at %s: %s" % (where, type(key).__name__))
            _reject_non_string_keys(item, where + "." + key)
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _reject_non_string_keys(item, "%s[%d]" % (where, index))
    elif value is None:
        return
    elif isinstance(value, (str, bool, int)):
        return
    elif isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            raise ValueError("non finite float at %s" % where)
        return
    else:
        raise ValueError("unsupported type at %s: %s" % (where, type(value).__name__))


def _as_json_facts(value):
    if not isinstance(value, dict) or not value:
        raise ValueError("facts must be a non empty dict, got %s" % type(value).__name__)
    _reject_non_string_keys(value, "facts")
    try:
        text = json.dumps(value, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("facts are not serialisable as JSON: %s" % exc)
    try:
        parsed = json.loads(text)
    except ValueError as exc:
        raise ValueError("facts are not serialisable as JSON: %s" % exc)
    if not isinstance(parsed, dict):
        raise ValueError("facts must round trip as a JSON object")
    return parsed


def load_hook_module(hook_path=HOOK_PATH):
    _require_path(hook_path, "hook_path")
    if os.path.isdir(hook_path) or not os.path.isfile(hook_path):
        raise ValueError("hook path is missing or is not a file: %s" % hook_path)
    spec = importlib.util.spec_from_file_location("brother_antigravity_contract_source", hook_path)
    if spec is None or spec.loader is None:
        raise ValueError("cannot load the hook module from %s" % hook_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def extract_main_modes(source_text):
    _require_path(source_text, "source_text")
    modes = set()
    for match in MODE_CALL.finditer(source_text):
        for name in MODE_NAME.finditer(match.group(1)):
            modes.add(name.group(1))
    if not modes:
        raise ValueError("no hook mode list found in the hook source")
    return sorted(modes)


def _call_handler(module, handler_name, payload):
    handler = getattr(module, handler_name, None)
    if handler is None:
        raise ValueError("hook module has no handler %s" % handler_name)
    out = io.StringIO()
    err = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        handler(payload, None)
    text = out.getvalue().strip()
    if not text:
        raise ValueError("handler %s produced no output" % handler_name)
    try:
        parsed = json.loads(text)
    except ValueError as exc:
        raise ValueError("handler %s output is not JSON: %s" % (handler_name, exc))
    if not isinstance(parsed, dict):
        raise ValueError("handler %s output is not a JSON object" % handler_name)
    return parsed


def _read_hook_source(hook_path):
    _require_path(hook_path, "hook_path")
    try:
        with open(hook_path, "rb") as handle:
            raw = handle.read()
    except OSError as exc:
        raise ValueError("hook file is unreadable: %s" % exc)
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("hook file is not utf-8 text: %s" % exc)


def collect_real_code_facts(hook_path=HOOK_PATH):
    module = load_hook_module(hook_path)
    tool_map = getattr(module, "TOOL_MAP", None)
    known_tools = getattr(module, "KNOWN_TOOLS", None)
    cap = getattr(module, "MAX_STDIN_CHARS", None)
    raw_shape = getattr(module, "_EVENT_SHAPE", None)

    if not isinstance(tool_map, dict) or not tool_map:
        raise ValueError("TOOL_MAP is missing or empty in %s" % hook_path)
    for name, canonical in tool_map.items():
        if not isinstance(name, str) or not name or not isinstance(canonical, str) or not canonical:
            raise ValueError("TOOL_MAP has a non string entry: %r" % (name,))
    if not isinstance(known_tools, (frozenset, set, tuple, list)) or not known_tools:
        raise ValueError("KNOWN_TOOLS is missing or empty in %s" % hook_path)
    for name in known_tools:
        if not isinstance(name, str) or not name:
            raise ValueError("KNOWN_TOOLS has a non string entry: %r" % (name,))
    if isinstance(cap, bool) or not isinstance(cap, int) or cap <= 0:
        raise ValueError("MAX_STDIN_CHARS must be a positive int, got %r" % (cap,))
    if not isinstance(raw_shape, dict) or not raw_shape:
        raise ValueError("_EVENT_SHAPE is missing or empty in %s" % hook_path)

    event_shape = {}
    for event, fields in raw_shape.items():
        if not isinstance(event, str) or not isinstance(fields, dict) or not fields:
            raise ValueError("event shape for %r is missing or empty" % (event,))
        event_shape[event] = {}
        for field, kind in fields.items():
            if not isinstance(field, str) or kind not in (int, str, bool):
                raise ValueError("event shape %s.%s has an unsupported type" % (event, field))
            event_shape[event][field] = kind.__name__

    handler_outputs = {
        "pre_tool": _call_handler(module, "handle_pre_tool",
                                  {"toolCall": {"name": "run_command", "args": {}}, "stepIdx": 0}),
        "post_tool": _call_handler(module, "handle_post_tool", {"stepIdx": 0, "error": ""}),
        "pre_invocation": _call_handler(module, "handle_pre_invocation", {"invocationNum": 1}),
        "post_invocation": _call_handler(module, "handle_post_invocation", {"invocationNum": 1}),
        "stop": _call_handler(module, "handle_stop",
                              {"terminationReason": "model_stop", "fullyIdle": True}),
    }

    source = _read_hook_source(hook_path)
    return {
        "tool_map_keys": sorted(tool_map.keys()),
        "known_tools": sorted(known_tools),
        "max_stdin_chars": cap,
        "event_shape": event_shape,
        "handler_outputs": handler_outputs,
        "main_modes": extract_main_modes(source),
    }


def _file_sha256(path):
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError:
        return NO_DATA
    return hashlib.sha256(data).hexdigest()


def _file_size(path):
    try:
        with open(path, "rb") as handle:
            return len(handle.read())
    except OSError:
        return NO_DATA


def _count_subdirs(path):
    if not os.path.isdir(path):
        return NO_DATA
    try:
        names = os.listdir(path)
    except OSError:
        return NO_DATA
    return len([name for name in names if os.path.isdir(os.path.join(path, name))])


def _count_files(path, suffix):
    if not os.path.isdir(path):
        return NO_DATA
    try:
        names = os.listdir(path)
    except OSError:
        return NO_DATA
    return len([name for name in names
                if name.endswith(suffix) and os.path.isfile(os.path.join(path, name))])


def host_facts(plugin_dir=PLUGIN_DIR):
    _require_path(plugin_dir, "plugin_dir")
    facts = {}
    facts["plugin_path_abs"] = os.path.abspath(plugin_dir) if os.path.isdir(plugin_dir) else NO_DATA
    facts["manifest_sha256"] = _file_sha256(os.path.join(plugin_dir, "plugin.json"))
    facts["mcp_config_sha256"] = _file_sha256(os.path.join(plugin_dir, "mcp_config.json"))
    facts["hooks_sha256"] = _file_sha256(os.path.join(plugin_dir, "hooks.json"))
    facts["hooks_byte_length"] = _file_size(os.path.join(plugin_dir, "hooks.json"))
    facts["skills_count"] = _count_subdirs(os.path.join(plugin_dir, "skills"))
    facts["rules_count"] = _count_files(os.path.join(plugin_dir, "rules"), ".md")
    for key in ("host_name", "host_version", "load_argv", "resolved_cwd", "matcher_proof",
                "benign_pre_tool_stdin", "benign_pre_tool_stdout"):
        facts[key] = NO_DATA
    return facts


def build_facts(hook_path=HOOK_PATH, plugin_dir=PLUGIN_DIR):
    facts = collect_real_code_facts(hook_path)
    facts.update(host_facts(plugin_dir))
    return facts


def capture_schema_section(log_path, facts):
    _require_path(log_path, "log_path")
    payload = _as_json_facts(facts)
    directory = os.path.dirname(os.path.abspath(log_path))
    if directory and not os.path.isdir(directory):
        try:
            os.makedirs(directory)
        except OSError as exc:
            raise ValueError("cannot create the log directory %s: %s" % (directory, exc))
    text = (
        "# Antigravity E2E test log\n\n"
        "## L1b.0 host contract schema\n\n"
        "Captured from the real hook adapter. A host fact that could not be read\n"
        "is recorded as no_data and reported by the gate, never as the safe case.\n\n"
        "```json\n"
        + json.dumps(payload, indent=2, sort_keys=True, allow_nan=False)
        + "\n```\n"
    )
    try:
        with open(log_path, "w", encoding="utf-8") as handle:
            handle.write(text)
    except OSError as exc:
        raise ValueError("cannot write the schema section to %s: %s" % (log_path, exc))


def _extract_schema_json(text):
    start = text.find("```json")
    if start < 0:
        return None, "schema"
    start += len("```json")
    end = text.find("```", start)
    if end < 0:
        return None, "schema"
    body = text[start:end].strip()
    if not body:
        return None, "schema"
    try:
        facts = json.loads(body)
    except ValueError:
        return None, "schema"
    if not isinstance(facts, dict) or not facts:
        return None, "schema"
    return facts, None


def _load_schema_facts(log_path):
    if not isinstance(log_path, str) or not log_path.strip():
        return None, "log_path"
    if os.path.isdir(log_path):
        return None, "log_path"
    try:
        with open(log_path, "rb") as handle:
            raw = handle.read()
    except OSError:
        return None, "log_path"
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None, "log_path"
    return _extract_schema_json(text)


def read_schema_section(log_path):
    facts, error = _load_schema_facts(log_path)
    if error == "log_path":
        raise ValueError("log path is missing, unreadable or not utf-8: %r" % (log_path,))
    if error == "schema":
        raise ValueError("schema json block is missing or corrupt in %r" % (log_path,))
    return facts


def check_unknowns(log_path):
    facts, error = _load_schema_facts(log_path)
    if error is not None:
        return [error]
    unknowns = []
    for key in REQUIRED_REAL_CODE_FACTS:
        if key not in facts:
            unknowns.append(key)
    if unknowns:
        return sorted(set(unknowns))
    for key in ("tool_map_keys", "known_tools", "main_modes"):
        value = facts[key]
        if not isinstance(value, list) or not value:
            unknowns.append(key)
        elif not all(isinstance(item, str) and item for item in value):
            unknowns.append(key)
    cap = facts["max_stdin_chars"]
    if isinstance(cap, bool) or not isinstance(cap, int) or cap <= 0:
        unknowns.append("max_stdin_chars")
    shape = facts["event_shape"]
    if not isinstance(shape, dict) or not shape:
        unknowns.append("event_shape")
    else:
        pre = shape.get("pre_invocation")
        post = shape.get("post_invocation")
        stop = shape.get("stop")
        if not isinstance(pre, dict) or pre.get("invocationNum") != "int":
            unknowns.append("event_shape")
        if not isinstance(post, dict) or post.get("invocationNum") != "int":
            unknowns.append("event_shape")
        if (not isinstance(stop, dict)
                or stop.get("terminationReason") != "str"
                or stop.get("fullyIdle") != "bool"):
            unknowns.append("event_shape")
    outputs = facts["handler_outputs"]
    if not isinstance(outputs, dict) or not outputs:
        unknowns.append("handler_outputs")
    return sorted(set(unknowns))


def check_host_facts(facts):
    if not isinstance(facts, dict):
        return list(HOST_FACT_KEYS)
    unknowns = []
    for key in HOST_FACT_KEYS:
        if key not in facts:
            unknowns.append(key)
            continue
        value = facts[key]
        if value is None or value == NO_DATA:
            unknowns.append(key)
        elif key in COUNT_FACT_KEYS:
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                unknowns.append(key)
        elif key in HASH_FACT_KEYS:
            if not isinstance(value, str) or HEX64.match(value) is None:
                unknowns.append(key)
        elif key == "plugin_path_abs":
            if not isinstance(value, str) or not os.path.isabs(value):
                unknowns.append(key)
        elif key == "load_argv":
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                unknowns.append(key)
        elif not isinstance(value, str):
            unknowns.append(key)
    return unknowns
