"""L1b.2 Plugin load gate.

Resolves the Antigravity host binary without inventing load verbs, then
inspects a sandbox copy of the Brother plugin and reports which parts of
the load succeeded. Partial loads yield ``ok False``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

__all__ = [
    "HostUnresolvedError",
    "resolve_host",
    "load_plugin",
    "fire_event",
    "capture_raw",
    "sha256_file",
    "main",
]


class HostUnresolvedError(Exception):
    """Raised when no Antigravity host binary can be resolved."""


_HOST_ENV = "ANTIGRAVITY_BIN"
# "antigravity-ide" is the command the installed app bundle ships
# (Contents/Resources/app/bin/antigravity-ide, read 2026-09-30).
_HOST_PATH_NAMES = ("antigravity", "agy", "antigravity-ide")

# The host's own CLI option table (Contents/Resources/app/out/cli.js) declares
# `version: {type: "boolean", alias: "v"}`; that is the verb used here, not an
# invented one. A bare `[host_bin]` opens the IDE window and prints nothing, so
# it can never capture the verbatim version REQ-VERSION-PIN requires.
_LOAD_ARGS = ("--version",)
_LOAD_TIMEOUT_S = 60


def resolve_host(*args, **kwargs):
    """Resolve the Antigravity host binary.

    Returns ``(host_bin, argv_prefix)``. The argv prefix is the verbatim
    argv to invoke the host with; no invented verbs are appended. Raises
    ``HostUnresolvedError`` when no host can be found in ``ANTIGRAVITY_BIN``
    or on PATH. Extra arguments are refused rather than causing a raw
    ``TypeError``.
    """
    if args or kwargs:
        raise HostUnresolvedError("resolve_host takes no arguments")
    raw = os.environ.get(_HOST_ENV)
    if isinstance(raw, str):
        raw = raw.strip()
    if raw:
        if not os.path.isfile(raw):
            raise HostUnresolvedError(
                "ANTIGRAVITY_BIN is not a file: %r" % raw
            )
        if not os.access(raw, os.X_OK):
            raise HostUnresolvedError(
                "ANTIGRAVITY_BIN is not executable: %r" % raw
            )
        return (raw, [raw])
    for name in _HOST_PATH_NAMES:
        found = shutil.which(name)
        if found:
            return (found, [found])
    raise HostUnresolvedError(
        "no Antigravity host binary resolved via ANTIGRAVITY_BIN or PATH"
    )


def _read_json_object(path):
    """Read a JSON object from ``path``.

    Returns ``(data, None)`` on success or ``(None, reason)`` on failure.
    A missing file, a directory, non-utf-8 bytes, corrupt JSON or a
    non-object root are all documented failures, not crashes.
    """
    if not isinstance(path, str):
        return None, "path must be a string, got %s" % type(path).__name__
    if not os.path.isfile(path):
        return None, "not a regular file: %s" % path
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError as exc:
        return None, "cannot read %s: %s" % (path, exc)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        return None, "not valid utf-8: %s" % exc
    try:
        data = json.loads(text)
    except ValueError as exc:
        return None, "corrupt JSON: %s" % exc
    if not isinstance(data, dict):
        return None, "JSON root is %s, expected object" % type(data).__name__
    return data, None


def _count_skills(skills_dir):
    if not isinstance(skills_dir, str) or not os.path.isdir(skills_dir):
        return 0
    count = 0
    for entry in os.listdir(skills_dir):
        full = os.path.join(skills_dir, entry)
        if os.path.isdir(full):
            if os.path.isfile(os.path.join(full, "SKILL.md")):
                count += 1
    return count


def _count_rules(rules_dir):
    if not isinstance(rules_dir, str) or not os.path.isdir(rules_dir):
        return 0
    count = 0
    for entry in os.listdir(rules_dir):
        full = os.path.join(rules_dir, entry)
        if os.path.isfile(full):
            count += 1
    return count


def _host_version_from(stdout):
    """The first non-empty line of the host's own output, verbatim, when it
    reads as a version (digits and dots first); otherwise ``no_data``."""
    try:
        text = stdout.decode("utf-8")
    except (AttributeError, UnicodeDecodeError):
        return "no_data"
    for line in text.splitlines():
        line = line.strip()
        if line:
            parts = line.split(".")
            if len(parts) >= 2 and parts[0].isdigit() and parts[1][:1].isdigit():
                return line
            return "no_data"
    return "no_data"


def load_plugin(host_bin, sandbox):
    """Run the host, inspect the plugin it is pointed at, report a verdict.

    Returns a dict with the fields named in the L1b data contract. The
    host is RUN (``load_argv``) and its exit code and verbatim version are
    recorded; file inspection alone is ``ok False`` (REQ-LOAD), and so is a
    host that exits nonzero or prints no version (REQ-VERSION-PIN). A
    partial load, a missing sandbox, a corrupt manifest or mcp config all
    yield ``ok False``. Hostile input (wrong type, ``None``, a bytes
    path where a str is expected) is refused through the ``ok False``
    path, never by raising.

    Known limit: the host's ``--version`` proves the host exists and names
    its version; it does not report whether the host accepted the manifest,
    skills and rules, which are still read from the package it is pointed
    at. Host-side acceptance is the signed-in installation run.
    """
    result = {
        "ok": False,
        "host_name": "no_data",
        "host_version": "no_data",
        "load_argv": [],
        "load_exit_code": -1,
        "load_stdout_sha256": "no_data",
        "manifest": "no_data",
        "mcp_config": "no_data",
        "skills_count": 0,
        "rules_count": 0,
        "reason": "",
    }

    if not isinstance(sandbox, str) or not sandbox:
        result["reason"] = "sandbox path missing or wrong type"
        return result
    if not os.path.isdir(sandbox):
        result["reason"] = "sandbox is not a directory: %s" % sandbox
        return result
    host_error = ""
    if not isinstance(host_bin, str) or not host_bin:
        host_error = "no_data: host_bin missing or wrong type, and file inspection alone is not a load"
    else:
        result["load_argv"] = [host_bin] + list(_LOAD_ARGS)
        result["host_name"] = os.path.basename(host_bin)
        try:
            proc = subprocess.run(result["load_argv"], stdin=subprocess.DEVNULL,
                                  capture_output=True, timeout=_LOAD_TIMEOUT_S)
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            host_error = "no_data: host did not run (%s)" % exc
        else:
            result["load_exit_code"] = proc.returncode
            result["load_stdout_sha256"] = hashlib.sha256(proc.stdout or b"").hexdigest()
            result["host_version"] = _host_version_from(proc.stdout or b"")

    manifest, manifest_error = _read_json_object(os.path.join(sandbox, "plugin.json"))
    if manifest_error is not None:
        result["manifest"] = "fail"
        result["reason"] = "manifest: %s" % manifest_error
    else:
        if not isinstance(manifest.get("name"), str) or not manifest["name"].strip():
            result["manifest"] = "fail"
            result["reason"] = "manifest: missing or empty 'name'"
        else:
            result["manifest"] = "ok"

    mcp, mcp_error = _read_json_object(os.path.join(sandbox, "mcp_config.json"))
    if mcp_error is not None:
        result["mcp_config"] = "fail"
        result["reason"] = (result["reason"] + "; " if result["reason"] else "") + "mcp_config: %s" % mcp_error
    else:
        mcp_servers = mcp.get("mcpServers")
        if not isinstance(mcp_servers, dict):
            result["mcp_config"] = "fail"
            result["reason"] = (result["reason"] + "; " if result["reason"] else "") + "mcp_config: mcpServers missing or not an object"
        elif mcp_servers:
            result["mcp_config"] = "fail"
            result["reason"] = (result["reason"] + "; " if result["reason"] else "") + "mcp_config: mcpServers is not empty"
        else:
            result["mcp_config"] = "ok"

    result["skills_count"] = _count_skills(os.path.join(sandbox, "skills"))
    result["rules_count"] = _count_rules(os.path.join(sandbox, "rules"))

    reasons = []
    if host_error:
        reasons.append(host_error)
    elif result["load_exit_code"] != 0:
        reasons.append("no_data: host exited %d" % result["load_exit_code"])
    if result["host_version"] == "no_data":
        reasons.append("no_data: host_version not captured")
    if result["manifest"] != "ok":
        reasons.append("manifest not ok")
    if result["mcp_config"] != "ok":
        reasons.append("mcp_config not ok")
    if result["skills_count"] < 2:
        reasons.append("skills_count < 2")
    if result["rules_count"] < 1:
        reasons.append("rules_count < 1")

    if reasons:
        result["ok"] = False
        result["reason"] = "; ".join(reasons)
    else:
        result["ok"] = True
        result["reason"] = ""
    return result


HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
ADAPTER_PATH = os.path.join(REPO_ROOT, "scripts", "brother_antigravity_hook.py")

_EVENT_MODES = {
    "PreToolUse": "pre_tool",
    "PostToolUse": "post_tool",
    "PreInvocation": "pre_invocation",
    "PostInvocation": "post_invocation",
    "Stop": "stop",
}

_EVENT_SEQUENCE = {
    "PreToolUse": 1,
    "PostToolUse": 2,
    "PreInvocation": 3,
    "PostInvocation": 4,
    "Stop": 5,
}

_VALID_ADAPTER_MODES = frozenset({
    "pre_tool",
    "post_tool",
    "pre_invocation",
    "post_invocation",
    "stop",
})


def sha256_file(path):
    """Return the hex sha256 of the file at ``path``.

    Missing files, directories and non-str paths are refused with
    ``ValueError`` rather than a raw ``OSError``.
    """
    if not isinstance(path, str) or not path:
        raise ValueError("sha256_file: path must be a non-empty string")
    if not os.path.isfile(path):
        raise ValueError("sha256_file: not a regular file: %s" % path)
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError as exc:
        raise ValueError("sha256_file: cannot read %s: %s" % (path, exc))
    return hashlib.sha256(data).hexdigest()


def capture_raw(run_dir, event_name, seq, data, suffix):
    """Write ``data`` bytes to ``run_dir/raw/<seq>-<event><suffix>``.

    Returns the absolute path. Hostile input (wrong type, missing run_dir,
    non-bytes data, bool where an int belongs) is refused with ``ValueError``.
    """
    if not isinstance(run_dir, str) or not run_dir:
        raise ValueError("capture_raw: run_dir must be a non-empty string")
    if not isinstance(event_name, str) or not event_name:
        raise ValueError("capture_raw: event_name must be a non-empty string")
    if isinstance(seq, bool) or not isinstance(seq, int):
        raise ValueError("capture_raw: seq must be an int")
    if not isinstance(data, bytes):
        raise ValueError("capture_raw: data must be bytes")
    if not isinstance(suffix, str) or not suffix:
        raise ValueError("capture_raw: suffix must be a non-empty string")
    raw_dir = os.path.join(run_dir, "raw")
    try:
        os.makedirs(raw_dir, exist_ok=True)
    except OSError as exc:
        raise ValueError("capture_raw: cannot create %s: %s" % (raw_dir, exc))
    filename = "%d-%s%s" % (seq, event_name, suffix)
    path = os.path.join(raw_dir, filename)
    try:
        with open(path, "wb") as handle:
            handle.write(data)
    except OSError as exc:
        raise ValueError("capture_raw: cannot write %s: %s" % (path, exc))
    return path


_TOOL_EVENTS = ("PreToolUse", "PostToolUse")


def _wired_command(hooks_path, event_name, payload):
    """``(command, timeout_s, None)`` for the ONE hook ``hooks.json`` wires to
    this event (and, for a tool event, whose matcher takes the payload's tool
    name), or ``(None, None, reason)``. Zero wired hooks is the matcher gap
    (the host would fire nothing); more than one is refused as ambiguous."""
    data, error = _read_json_object(hooks_path)
    if error is not None:
        return None, None, "hooks.json: %s" % error
    tool = None
    if event_name in _TOOL_EVENTS:
        call = payload.get("toolCall") if isinstance(payload, dict) else None
        tool = call.get("name") if isinstance(call, dict) else None
        if not isinstance(tool, str) or not tool:
            # A payload with no tool name still reaches the adapter, which
            # refuses it: the corrupt-input path must be exercised, not
            # skipped. Only a NAMED tool the matcher excludes is a gap.
            tool = None
    found = []
    for group in data.values():
        entries = group.get(event_name) if isinstance(group, dict) else None
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            hooks = [entry]
            if event_name in _TOOL_EVENTS:
                matcher = entry.get("matcher")
                if not isinstance(matcher, str):
                    continue
                if tool is not None:
                    try:
                        if not re.fullmatch(matcher, tool):
                            continue
                    except re.error:
                        return None, None, "hooks.json: matcher is not a valid pattern: %r" % matcher
                hooks = entry.get("hooks") if isinstance(entry.get("hooks"), list) else []
            for hook in hooks:
                if isinstance(hook, dict) and hook.get("type") == "command" \
                        and isinstance(hook.get("command"), str) and hook["command"].strip():
                    cap = hook.get("timeout")
                    cap = cap if isinstance(cap, (int, float)) and not isinstance(cap, bool) and cap > 0 else None
                    found.append((hook["command"], cap))
    if not found:
        return None, None, "hooks.json wires no command for %s%s" % (
            event_name, " and tool %r (matcher gap)" % tool if tool else "")
    if len(found) > 1:
        return None, None, "hooks.json wires %d commands for %s; refusing to pick one" % (len(found), event_name)
    return found[0][0], found[0][1], None


def fire_event(host_bin, sandbox, event_name, payload, timeout_s, run_dir=None):
    """Fire one Antigravity hook event through the package's own wiring.

    The command is the one ``<sandbox>/hooks.json`` wires to the event,
    run through ``/bin/sh -c`` from the directory holding that hooks.json,
    exactly as the host launches it (REQ-CWD); it is never rebuilt from
    ``host_bin`` and a repository adapter path. ``host_bin`` names the
    host the run is recorded against and is not executed here. Raw input,
    output and stderr bytes go under ``run_dir`` (default ``sandbox``).

    Returns a dict with the fields required by the L1b data contract for a
    fired event, plus ``hook_cwd_abs`` and ``hook_command``. Unknown,
    corrupt or missing input yields a refusal dict with ``outcome``
    ``no_data`` or ``fail``, never a silent pass.
    """
    def _refuse(reason, decision_kind="no_data"):
        return {
            "event_name": event_name if isinstance(event_name, str) else "no_data",
            "event_sequence": _EVENT_SEQUENCE.get(event_name, 0) if isinstance(event_name, str) else 0,
            "adapter_invoked": False,
            "adapter_exit_code": -1,
            "adapter_wall_ms": 0,
            "raw_in_path": "no_data",
            "raw_in_sha256": "no_data",
            "raw_out_path": "no_data",
            "raw_out_sha256": "no_data",
            "stderr_path": "no_data",
            "decision_verbatim": "",
            "decision_kind": decision_kind,
            "outcome": "no_data" if decision_kind == "no_data" else "fail",
            "reason": reason,
        }

    if not isinstance(host_bin, str) or not host_bin:
        return _refuse("host_bin missing or wrong type")
    if not isinstance(sandbox, str) or not sandbox:
        return _refuse("sandbox missing or wrong type")
    if not isinstance(event_name, str) or event_name not in _EVENT_MODES:
        return _refuse("unknown event_name: %r" % (event_name,))
    if not isinstance(payload, dict):
        return _refuse("payload must be a dict, got %s" % type(payload).__name__, "deny")
    if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)) or timeout_s <= 0:
        return _refuse("timeout_s must be a positive number", "deny")

    mode = _EVENT_MODES[event_name]
    if mode not in _VALID_ADAPTER_MODES:
        return _refuse("unknown adapter mode: %s" % mode, "deny")

    seq = _EVENT_SEQUENCE[event_name]
    if run_dir is None:
        run_dir = sandbox
    if not isinstance(run_dir, str) or not run_dir:
        return _refuse("run_dir must be a non-empty string")

    try:
        input_bytes = json.dumps(payload).encode("utf-8")
    except (TypeError, ValueError) as exc:
        return _refuse("cannot serialize payload: %s" % exc, "deny")

    hooks_path = os.path.join(sandbox, "hooks.json")
    command, cap, wiring_error = _wired_command(hooks_path, event_name, payload)
    if command is None:
        return _refuse(wiring_error)
    hook_cwd = os.path.dirname(os.path.abspath(hooks_path))
    if cap is not None:
        timeout_s = min(timeout_s, cap)

    cmd = ["/bin/sh", "-c", command]
    start = time.monotonic()
    timed_out = False
    exit_code = -1
    stdout_bytes = b""
    stderr_bytes = b""
    try:
        proc = subprocess.run(
            cmd,
            cwd=hook_cwd,
            input=input_bytes,
            capture_output=True,
            timeout=timeout_s,
        )
        stdout_bytes = proc.stdout or b""
        stderr_bytes = proc.stderr or b""
        exit_code = proc.returncode
    except subprocess.TimeoutExpired as exc:
        stdout_bytes = exc.stdout or b""
        stderr_bytes = exc.stderr or b""
        timed_out = True
        exit_code = -1
    except Exception as exc:
        return _refuse("adapter invocation failed: %s" % exc, "deny")
    wall_ms = int((time.monotonic() - start) * 1000)

    try:
        raw_in_path = capture_raw(run_dir, event_name, seq, input_bytes, ".in")
        raw_out_path = capture_raw(run_dir, event_name, seq, stdout_bytes, ".out")
        stderr_path = capture_raw(run_dir, event_name, seq, stderr_bytes, ".err")
    except ValueError as exc:
        return _refuse("raw capture failed: %s" % exc, "deny")

    try:
        raw_in_sha = sha256_file(raw_in_path)
        raw_out_sha = sha256_file(raw_out_path)
    except ValueError as exc:
        return _refuse("raw hash failed: %s" % exc, "deny")

    decision_verbatim = ""
    decision_kind = "no_data"
    adapter_invoked = False
    outcome = "fail"

    if timed_out:
        outcome = "fail"
    else:
        try:
            text = stdout_bytes.decode("utf-8")
            decision_verbatim = text.strip()
            parsed = json.loads(decision_verbatim)
            if not isinstance(parsed, dict):
                raise ValueError("adapter output is not a JSON object")
            reason = parsed.get("reason", "")
            if isinstance(reason, str) and "unknown hook mode" in reason:
                adapter_invoked = False
            else:
                adapter_invoked = True

            if event_name == "PreToolUse":
                d = parsed.get("decision")
                if d in ("allow", "deny", "ask"):
                    decision_kind = d
                else:
                    adapter_invoked = False
            elif event_name == "PostToolUse":
                if parsed == {}:
                    decision_kind = "no_data"
                else:
                    adapter_invoked = False
            elif event_name == "PreInvocation":
                if "injectSteps" in parsed:
                    decision_kind = "no_data"
                else:
                    adapter_invoked = False
            elif event_name == "PostInvocation":
                tb = parsed.get("terminationBehavior")
                if tb == "terminate":
                    decision_kind = "terminate"
                elif tb == "force_continue":
                    decision_kind = "continue"
                elif tb == "":
                    decision_kind = "no_data"
                else:
                    adapter_invoked = False
            elif event_name == "Stop":
                d = parsed.get("decision")
                if d in ("allow", "continue"):
                    decision_kind = d
                else:
                    adapter_invoked = False
            outcome = "pass" if adapter_invoked else "fail"
        except Exception:
            outcome = "fail"
            adapter_invoked = False

    return {
        "event_name": event_name,
        "event_sequence": seq,
        "adapter_invoked": adapter_invoked,
        "adapter_exit_code": exit_code,
        "adapter_wall_ms": wall_ms,
        "raw_in_path": raw_in_path,
        "raw_in_sha256": raw_in_sha,
        "raw_out_path": raw_out_path,
        "raw_out_sha256": raw_out_sha,
        "stderr_path": stderr_path,
        "decision_verbatim": decision_verbatim,
        "decision_kind": decision_kind,
        "outcome": outcome,
        "hook_cwd_abs": hook_cwd,
        "hook_command": command,
    }


# L1b.7 run and write log
# ---------------------------------------------------------------------

_TIMEOUT_CAPS = {
    "PreToolUse": 15.0,
    "PostToolUse": 15.0,
    "PreInvocation": 10.0,
    "PostInvocation": 10.0,
    "Stop": 30.0,
}

_EVENT_ORDER = ("PreToolUse", "PostToolUse", "PreInvocation", "PostInvocation", "Stop")


def _new_run_id():
    """Return a 32 character hex run id."""
    return os.urandom(16).hex()


def _iso8601_utc_now():
    """Return the current UTC time as an ISO8601 string."""
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _os_platform_name():
    """Return a lowercase platform name or no_data."""
    if sys.platform.startswith("linux"):
        return "linux"
    if sys.platform == "darwin":
        return "darwin"
    if sys.platform.startswith("win"):
        return "windows"
    return "no_data"


def _copytree_contents(src, dst):
    """Copy every entry of ``src`` into ``dst``.

    Directories are copied recursively, regular files keep their modes.
    Missing or unreadable entries raise OSError rather than being
    silently skipped.
    """
    for name in os.listdir(src):
        s = os.path.join(src, name)
        d = os.path.join(dst, name)
        if os.path.isdir(s):
            shutil.copytree(s, d)
        elif os.path.isfile(s):
            shutil.copy2(s, d)


def _payload_for(event_name):
    """Return a clean benign payload for the given event name."""
    if event_name == "PreToolUse":
        return {"toolCall": {"name": "run_command", "args": {"command": "ls -la"}}, "stepIdx": 0}
    if event_name == "PostToolUse":
        return {"stepIdx": 0, "error": ""}
    if event_name == "PreInvocation":
        return {"invocationNum": 1}
    if event_name == "PostInvocation":
        return {"invocationNum": 1}
    if event_name == "Stop":
        return {"terminationReason": "done", "fullyIdle": True}
    return {}


def _tool_name_for(payload, event_name):
    """Return the tool name from a payload, or the literal no_data."""
    if event_name in ("PreToolUse", "PostToolUse") and isinstance(payload, dict):
        tool_call = payload.get("toolCall")
        if isinstance(tool_call, dict):
            name = tool_call.get("name")
            if isinstance(name, str) and name:
                return name
    return "no_data"


def _tool_input_sha(payload, event_name):
    """Return the sha256 of the tool args JSON, or the literal no_data."""
    if event_name not in ("PreToolUse", "PostToolUse") or not isinstance(payload, dict):
        return "no_data"
    tool_call = payload.get("toolCall")
    if not isinstance(tool_call, dict):
        return "no_data"
    args = tool_call.get("args")
    if args is None:
        return "no_data"
    try:
        blob = json.dumps(args, sort_keys=True).encode("utf-8")
    except (TypeError, ValueError):
        return "no_data"
    return hashlib.sha256(blob).hexdigest()


def run_sandbox(source_plugin, host_bin=None, environment="sandbox", run_dir=None):
    """Run the full five event flow and return a run result dict.

    ``environment`` is ``sandbox`` (the plugin is copied into a scratch
    directory first) or ``real`` (the installed plugin is fired in place,
    as the host would, and nothing is written into it). Raw bytes go under
    ``run_dir``: default the sandbox copy, or a fresh scratch directory for
    ``real``. The result carries run_id, environment, started_at, load,
    events and ok. Every event entry carries the full L1b data contract
    fields including hook_cwd_abs, the directory the hook command actually
    ran in. Unknown, missing or corrupt input refuses with ok False and a
    reason, never a raw exception. Partial load stops the chain.
    """
    result = {
        "run_id": _new_run_id(),
        "environment": environment if environment in ("sandbox", "real") else "no_data",
        "started_at": _iso8601_utc_now(),
        "load": None,
        "events": [],
        "ok": False,
        "reason": "",
    }
    if environment not in ("sandbox", "real"):
        result["reason"] = "environment must be 'sandbox' or 'real', got %r" % (environment,)
        return result
    if run_dir is not None and (not isinstance(run_dir, str) or not run_dir):
        result["reason"] = "run_dir must be a non-empty string when provided"
        return result
    if not isinstance(source_plugin, str) or not source_plugin:
        result["reason"] = "source_plugin must be a non-empty string"
        return result
    if not os.path.isdir(source_plugin):
        result["reason"] = "source_plugin is not a directory: %s" % source_plugin
        return result
    if host_bin is None:
        try:
            host_bin, _argv_prefix = resolve_host()
        except HostUnresolvedError as exc:
            result["reason"] = "host unresolved: %s" % exc
            return result
    elif not isinstance(host_bin, str) or not host_bin:
        result["reason"] = "host_bin must be a non-empty string when provided"
        return result

    if environment == "real":
        sandbox = os.path.abspath(source_plugin)
    else:
        try:
            sandbox = tempfile.mkdtemp(prefix="brother-e2e-")
        except OSError as exc:
            result["reason"] = "cannot create sandbox: %s" % exc
            return result
        try:
            _copytree_contents(source_plugin, sandbox)
        except OSError as exc:
            result["reason"] = "cannot copy source_plugin into sandbox: %s" % exc
            return result
    owned_copy = sandbox if environment == "sandbox" and run_dir is not None else None
    try:
        return _run_events(result, source_plugin, host_bin, environment, sandbox, run_dir)
    finally:
        # The scratch copy this call made is removed once the raw bytes live elsewhere;
        # with the default run_dir they live inside it, so it is kept for the caller.
        if owned_copy is not None:
            shutil.rmtree(owned_copy, ignore_errors=True)


def _run_events(result, source_plugin, host_bin, environment, sandbox, run_dir):
    """The load and five events of run_sandbox, after the sandbox and run_dir are chosen."""
    if run_dir is None:
        if environment == "real":
            try:
                run_dir = tempfile.mkdtemp(prefix="brother-e2e-real-")
            except OSError as exc:
                result["reason"] = "cannot create run_dir: %s" % exc
                return result
        else:
            run_dir = sandbox

    try:
        load = load_plugin(host_bin, sandbox)
    except Exception as exc:
        result["reason"] = "load_plugin failed: %s" % exc
        return result
    result["load"] = load
    if not isinstance(load, dict) or not load.get("ok"):
        reason = load.get("reason", "") if isinstance(load, dict) else ""
        result["reason"] = "partial load: %s" % reason
        return result

    manifest_path = os.path.join(sandbox, "plugin.json")
    mcp_path = os.path.join(sandbox, "mcp_config.json")
    hooks_json = os.path.join(sandbox, "hooks.json")
    try:
        manifest_sha = sha256_file(manifest_path)
    except ValueError:
        manifest_sha = "no_data"
    try:
        mcp_sha = sha256_file(mcp_path)
    except ValueError:
        mcp_sha = "no_data"
    try:
        hooks_sha = sha256_file(hooks_json)
    except ValueError:
        hooks_sha = "no_data"

    if os.path.isfile(hooks_json):
        hook_cwd_abs = os.path.dirname(os.path.abspath(hooks_json))
    else:
        hook_cwd_abs = os.path.abspath(sandbox)

    host_name = os.path.basename(host_bin) if isinstance(host_bin, str) and host_bin else "no_data"
    host_version = "no_data"
    if isinstance(load, dict) and isinstance(load.get("host_version"), str):
        host_version = load["host_version"]

    common = {
        "run_id": result["run_id"],
        "environment": result["environment"],
        "started_at": result["started_at"],
        "host_name": host_name,
        "host_version": host_version,
        "os_platform": _os_platform_name(),
        "plugin_path_abs": os.path.abspath(source_plugin),
        "manifest_sha256": manifest_sha,
        "hooks_sha256": hooks_sha,
        "mcp_config_sha256": mcp_sha,
        "hook_cwd_abs": hook_cwd_abs,
    }

    events = []
    for event_name in _EVENT_ORDER:
        payload = _payload_for(event_name)
        timeout_s = _TIMEOUT_CAPS[event_name]
        try:
            entry = fire_event(host_bin, sandbox, event_name, payload, timeout_s, run_dir=run_dir)
        except Exception as exc:
            entry = {
                "event_name": event_name,
                "event_sequence": _EVENT_SEQUENCE.get(event_name, 0),
                "adapter_invoked": False,
                "adapter_exit_code": -1,
                "adapter_wall_ms": 0,
                "raw_in_path": "no_data",
                "raw_in_sha256": "no_data",
                "raw_out_path": "no_data",
                "raw_out_sha256": "no_data",
                "stderr_path": "no_data",
                "decision_verbatim": "",
                "decision_kind": "no_data",
                "outcome": "no_data",
                "reason": "fire_event raised: %s" % exc,
            }
        enriched = dict(common)
        if isinstance(entry, dict):
            enriched.update(entry)
        if not enriched.get("hook_cwd_abs"):
            enriched["hook_cwd_abs"] = hook_cwd_abs
        tool_name = _tool_name_for(payload, event_name)
        if not enriched.get("tool_name"):
            enriched["tool_name"] = tool_name
        if not enriched.get("tool_input_sha256"):
            enriched["tool_input_sha256"] = _tool_input_sha(payload, event_name)
        stderr_path = enriched.get("stderr_path")
        if isinstance(stderr_path, str) and os.path.isfile(stderr_path):
            try:
                enriched["stderr_sha256"] = sha256_file(stderr_path)
            except ValueError:
                enriched["stderr_sha256"] = "no_data"
        else:
            enriched["stderr_sha256"] = "no_data"
        events.append(enriched)

    result["events"] = events
    outcomes = [e.get("outcome") for e in events if isinstance(e, dict)]
    result["ok"] = len(outcomes) == 5 and all(o == "pass" for o in outcomes)
    if result["ok"]:
        result["reason"] = ""
    else:
        result["reason"] = "one or more events did not pass"
    return result


def write_log(run_result, log_path):
    """Write ``run_result`` event entries as JSON Lines to ``log_path``.

    Hostile input is refused with ``ValueError``: a non dict run result,
    a run_result without an ``events`` list, a non string or empty log
    path, and any non dict event entry. One event entry per line.

    A raw, output or stderr path that lies under the log's own directory
    is written RELATIVE to that directory, so the log and its raw bytes
    travel together and verify_log can recompute every hash on another
    checkout. A path elsewhere is written as it is.
    """
    if not isinstance(run_result, dict):
        raise ValueError("write_log: run_result must be a dict")
    events = run_result.get("events")
    if not isinstance(events, list):
        raise ValueError("write_log: run_result['events'] must be a list")
    if not isinstance(log_path, str) or not log_path:
        raise ValueError("write_log: log_path must be a non-empty string")

    log_dir = os.path.dirname(os.path.abspath(log_path))
    lines = []
    for index, entry in enumerate(events):
        if not isinstance(entry, dict):
            raise ValueError("write_log: event entry %d must be a dict" % index)
        entry = dict(entry)
        for key in ("raw_in_path", "raw_out_path", "stderr_path"):
            value = entry.get(key)
            if isinstance(value, str) and os.path.isabs(value):
                rel = os.path.relpath(value, log_dir)
                if not rel.startswith(os.pardir):
                    entry[key] = rel
        try:
            lines.append(json.dumps(entry, sort_keys=True))
        except (TypeError, ValueError) as exc:
            raise ValueError("write_log: cannot serialize event entry %d: %s" % (index, exc))

    directory = os.path.dirname(os.path.abspath(log_path))
    if directory and not os.path.isdir(directory):
        try:
            os.makedirs(directory, exist_ok=True)
        except OSError as exc:
            raise ValueError("write_log: cannot create %s: %s" % (directory, exc))
    try:
        with open(log_path, "w", encoding="utf-8") as handle:
            for line in lines:
                handle.write(line + "\n")
    except OSError as exc:
        raise ValueError("write_log: cannot write %s: %s" % (log_path, exc))


def main(argv=None):
    """Load gate entry point.

    Exit 0 on a full load. An unresolved host writes ``outcome no_data``
    and exits nonzero. No fallback to direct adapter invocation happens
    here.
    """
    if argv is None:
        args = list(sys.argv[1:])
    else:
        if not isinstance(argv, (list, tuple)):
            sys.stdout.write("outcome no_data\n")
            sys.stdout.write("reason argv must be a list or tuple of strings\n")
            return 2
        args = []
        for item in argv:
            if not isinstance(item, str):
                sys.stdout.write("outcome no_data\n")
                sys.stdout.write("reason argv items must be strings\n")
                return 2
            args.append(item)
    try:
        host_bin, _argv_prefix = resolve_host()
    except HostUnresolvedError as exc:
        sys.stdout.write("outcome no_data\n")
        sys.stdout.write("reason %s\n" % exc)
        return 2
    sandbox = args[0] if args else os.getcwd()
    result = load_plugin(host_bin, sandbox)
    sys.stdout.write(json.dumps(result, sort_keys=True) + "\n")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
