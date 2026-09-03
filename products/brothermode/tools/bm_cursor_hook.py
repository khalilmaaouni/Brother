#!/usr/bin/env python3
"""BrotherMode Cursor hook adapter: translate Cursor payloads to the
Claude Code contract the fence and audit hooks already understand.

WHY THIS EXISTS
  Cursor has its own hooks.json shape (version 1, lowerCamelCase events,
  flat permission objects) and its own tool names (Shell, Write, Delete).
  BrotherMode's fence and bash audit parse Claude Code's PreToolUse /
  PostToolUse contract (docs/HOOKS.md). This file is the seam between
  them: it accepts Cursor stdin, maps tools and events, calls the same
  decide() path bm_fence_hook.py uses (or the bash audit phases), and
  emits a Cursor-native decision object that Cursor also accepts in the
  nested Claude form (cursor.com/docs/reference/third-party-hooks).

HONEST LIMIT
  Payload shapes below were read from Cursor's own Hooks documentation
  on 2026-08-10 (https://cursor.com/docs/hooks). A LIVE canary that proves
  Cursor actually runs these hooks in Agent and Cloud Agent modes has
  NOT been measured in this tree. Until that canary lands, treat the
  fence under Cursor as ADVISORY: the adapter is real, the wiring is
  installable, enforcement is UNVERIFIED. docs/CURSOR-COMPAT.md states
  the same limit.

FAIL OPEN, LOUDLY
  Same law as bm_fence_hook.py: every unexpected failure allows the
  action and prints the reason to stderr. A broken adapter must never
  brick editing.

Python 3.9, standard library only. No network, no subprocess.
No em or en dashes anywhere in this file, its comments, or its output.
"""

from __future__ import annotations

import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# Cursor tool name -> Claude Code tool name the fence/audit already know.
TOOL_MAP = {
    "Shell": "Bash",
    "Bash": "Bash",
    "Write": "Write",
    "Edit": "Edit",
    "Delete": "Delete",
    "Read": "Read",
    "Grep": "Grep",
    "Task": "Task",
    "MultiEdit": "MultiEdit",
    "NotebookEdit": "NotebookEdit",
    "CreateDirectory": "CreateDirectory",
}

# Cursor events this adapter understands. Unknown events fail open.
EVENTS = frozenset((
    "preToolUse",
    "postToolUse",
    "beforeShellExecution",
    "afterShellExecution",
    "afterFileEdit",
    "sessionStart",
    "sessionEnd",
    "preCompact",
    "stop",
))

# Events that can refuse a write. afterFileEdit is detection only.
GATE_EVENTS = frozenset(("preToolUse", "beforeShellExecution"))


def _out(s):
    sys.stdout.write(s)
    sys.stdout.flush()


def _warn(s):
    sys.stderr.write(s if s.endswith("\n") else s + "\n")
    sys.stderr.flush()


def _load(name):
    """Import a sibling tools module by absolute path, or None."""
    try:
        import importlib.util
        path = os.path.join(HERE, name + ".py")
        spec = importlib.util.spec_from_file_location("bm_cursor_" + name, path)
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception as exc:
        _warn("bm_cursor_hook: failed to load %s: %s: %s"
              % (name, type(exc).__name__, exc))
        return None


def read_payload(stdin=None):
    """Parse JSON from stdin. Returns (payload_or_None, error_note)."""
    stream = stdin if stdin is not None else sys.stdin
    try:
        raw = stream.read()
    except Exception as exc:
        return None, "stdin read failed: %s: %s" % (type(exc).__name__, exc)
    if not raw or not str(raw).strip():
        return {}, None
    try:
        data = json.loads(raw)
    except ValueError as exc:
        return None, "payload is not JSON: %s" % exc
    if not isinstance(data, dict):
        return None, "payload is not a JSON object"
    return data, None


def event_name(payload, argv):
    """Resolve the Cursor event name from argv or the payload."""
    if argv:
        name = argv[0]
        if name in EVENTS:
            return name
    for key in ("hook_event_name", "hookEventName"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            # Accept Claude PascalCase via third-party mapping too.
            mapped = {
                "PreToolUse": "preToolUse",
                "PostToolUse": "postToolUse",
                "SessionStart": "sessionStart",
                "SessionEnd": "sessionEnd",
                "PreCompact": "preCompact",
                "Stop": "stop",
            }.get(value, value)
            return mapped
    return None


def map_tool_name(name):
    if not isinstance(name, str):
        return name
    return TOOL_MAP.get(name, name)


def cursor_to_claude_payload(payload, event):
    """Build a Claude-shaped PreToolUse/PostToolUse payload from Cursor input.

    Sources for field names: Cursor Hooks docs 2026-08-10. Missing fields
    stay missing; the fence fail-opens on incomplete identity rather than
    inventing one.
    """
    out = dict(payload)
    out["hook_event_name"] = {
        "preToolUse": "PreToolUse",
        "postToolUse": "PostToolUse",
        "beforeShellExecution": "PreToolUse",
        "afterShellExecution": "PostToolUse",
        "afterFileEdit": "PostToolUse",
        "sessionStart": "SessionStart",
        "sessionEnd": "SessionEnd",
        "preCompact": "PreCompact",
        "stop": "Stop",
    }.get(event, event)

    if event == "beforeShellExecution":
        command = payload.get("command")
        out["tool_name"] = "Bash"
        out["tool_input"] = {
            "command": command if isinstance(command, str) else "",
        }
        if isinstance(payload.get("cwd"), str):
            out["cwd"] = payload["cwd"]
            out["tool_input"]["working_directory"] = payload["cwd"]
    elif event == "afterShellExecution":
        command = payload.get("command")
        out["tool_name"] = "Bash"
        out["tool_input"] = {
            "command": command if isinstance(command, str) else "",
        }
        out["tool_response"] = {
            "stdout": payload.get("output", ""),
            "output": payload.get("output", ""),
        }
    elif event == "afterFileEdit":
        # Detection only: synthesize a Write so bash_audit/post style
        # observers can see the path. The fence is never asked here.
        path = payload.get("file_path")
        out["tool_name"] = "Write"
        out["tool_input"] = {
            "file_path": path if isinstance(path, str) else "",
            "path": path if isinstance(path, str) else "",
        }
    else:
        tool = map_tool_name(payload.get("tool_name"))
        if isinstance(tool, str):
            out["tool_name"] = tool
        tool_input = payload.get("tool_input")
        if isinstance(tool_input, dict):
            # Cursor Write uses path keys the fence already accepts
            # (file_path, path, filePath, target_file). Also accept
            # working_directory as cwd when present.
            ti = dict(tool_input)
            if "path" in ti and "file_path" not in ti:
                ti["file_path"] = ti["path"]
            if "target_file" in ti and "file_path" not in ti:
                ti["file_path"] = ti["target_file"]
            out["tool_input"] = ti
            wd = ti.get("working_directory")
            if isinstance(wd, str) and "cwd" not in out:
                out["cwd"] = wd
        elif event in ("preToolUse", "postToolUse") and isinstance(
                payload.get("command"), str):
            # Some Cursor paths put the shell command at the top level
            # even under preToolUse. Honour it.
            out["tool_name"] = "Bash"
            out["tool_input"] = {"command": payload["command"]}

    # Session identity: Cursor docs name conversation_id and session_id.
    # Prefer session_id when present; otherwise fall back so the fence
    # token slot is still stable within one conversation.
    if "session_id" not in out:
        for key in ("conversation_id", "generation_id"):
            if isinstance(payload.get(key), str) and payload[key]:
                out["session_id"] = payload[key]
                break

    roots = payload.get("workspace_roots")
    if isinstance(roots, list) and roots and "cwd" not in out:
        if isinstance(roots[0], str) and roots[0]:
            out["cwd"] = roots[0]

    return out


def claude_deny_to_cursor(decision):
    """Translate a Claude nested deny into Cursor's flat permission object.

    Cursor accepts BOTH shapes (third-party hooks doc). Emitting the flat
    form is the native path; keep the nested form too so a third-party
    Claude settings install keeps working if this adapter is somehow
    invoked under that path.
    """
    if not isinstance(decision, dict):
        return None
    nested = decision.get("hookSpecificOutput")
    reason = ""
    if isinstance(nested, dict):
        perm = nested.get("permissionDecision")
        reason = nested.get("permissionDecisionReason") or ""
        if perm == "deny":
            return {
                "permission": "deny",
                "user_message": reason,
                "agent_message": reason,
                "hookSpecificOutput": nested,
            }
        return None
    # Already flat.
    if decision.get("permission") == "deny":
        return decision
    return None


def allow_payload():
    return {"permission": "allow", "continue": True}


def run_fence(claude_payload):
    fence = _load("bm_fence_hook")
    if fence is None:
        return None, ["fence module unavailable; fail-open"]
    try:
        decision, notes = fence.decide(claude_payload)
    except Exception as exc:
        return None, ["fence decide raised %s: %s"
                      % (type(exc).__name__, exc)]
    return decision, list(notes or [])


def run_bash_audit(phase, claude_payload):
    audit = _load("bm_bash_audit")
    if audit is None:
        return ["bash audit unavailable; skip"]
    # bm_bash_audit exposes main() that reads stdin. Call decide-shaped
    # helpers when present; otherwise invoke main with a temp stdin.
    try:
        if phase == "pre" and hasattr(audit, "pre_main"):
            audit.pre_main(claude_payload)
        elif phase == "post" and hasattr(audit, "post_main"):
            audit.post_main(claude_payload)
        elif hasattr(audit, "main"):
            raw = json.dumps(claude_payload)
            old_in, old_out = sys.stdin, sys.stdout
            sys.stdin = io.StringIO(raw)
            # Audit must not write a permission decision to stdout.
            sys.stdout = io.StringIO()
            try:
                audit.main([phase])
            finally:
                sys.stdin = old_in
                sys.stdout = old_out
    except SystemExit:  # sbe: allow-silent audit.main() exits on its own when called as a library; that exit code is not this hook's
        pass
    except Exception as exc:
        return ["bash audit %s raised %s: %s"
                % (phase, type(exc).__name__, exc)]
    return []


def handle(payload, event):
    """Return (exit_code, stdout_object_or_None)."""
    if event not in EVENTS:
        _warn("bm_cursor_hook: unknown event %r; fail-open" % event)
        return 0, allow_payload()

    claude = cursor_to_claude_payload(payload, event)

    if event in ("preToolUse", "beforeShellExecution"):
        decision, notes = run_fence(claude)
        for note in notes:
            _warn("bm_cursor_hook: %s" % note)
        # Bash audit pre runs after the fence so a deny still short
        # circuits before snapshot work when ownership conflicts.
        cursor_deny = claude_deny_to_cursor(decision) if decision else None
        if cursor_deny is not None:
            return 0, cursor_deny
        if claude.get("tool_name") == "Bash":
            for note in run_bash_audit("pre", claude):
                _warn("bm_cursor_hook: %s" % note)
        return 0, allow_payload()

    if event in ("postToolUse", "afterShellExecution"):
        if claude.get("tool_name") == "Bash":
            for note in run_bash_audit("post", claude):
                _warn("bm_cursor_hook: %s" % note)
        return 0, {}

    if event == "afterFileEdit":
        # No refuse path after the write. Emit nothing Cursor needs to
        # parse; observers can extend this later.
        return 0, {}

    if event == "sessionStart":
        # Best effort: run the sessionstart script's python-side work by
        # pointing at bm_progress_check when present. Shell script stays
        # the Claude path; Cursor gets a quiet no-op that still proves
        # the adapter ran.
        return 0, {}

    if event in ("sessionEnd", "preCompact", "stop"):
        # Telemetry and autosave stay consent-gated and Claude-shaped.
        # Cursor wiring for those lands through install_cursor.py's
        # optional --with-telemetry flag, which points at the same
        # tools with translated payloads. Default adapter is silent.
        return 0, {}

    return 0, allow_payload()


def main(argv=None):
    argv = list(argv if argv is not None else sys.argv[1:])
    payload, err = read_payload()
    if err is not None:
        _warn("bm_cursor_hook: %s; fail-open" % err)
        _out(json.dumps(allow_payload()) + "\n")
        return 0
    event = event_name(payload or {}, argv)
    if event is None:
        _warn("bm_cursor_hook: no event name in argv or payload; fail-open")
        _out(json.dumps(allow_payload()) + "\n")
        return 0
    # Drop the event token so nested tools see a clean argv if needed.
    rest = argv[1:] if argv and argv[0] == event else argv
    try:
        code, body = handle(payload or {}, event)
    except Exception as exc:
        _warn("bm_cursor_hook: unexpected %s: %s; fail-open"
              % (type(exc).__name__, exc))
        _out(json.dumps(allow_payload()) + "\n")
        return 0
    if body is not None:
        _out(json.dumps(body) + "\n")
    return code


if __name__ == "__main__":
    sys.exit(main())
