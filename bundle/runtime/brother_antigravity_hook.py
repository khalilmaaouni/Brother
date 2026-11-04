#!/usr/bin/env python3
"""Brother Antigravity hook adapter: translate Antigravity protojson
payloads to the Brother fence and assurance contract.

WHY THIS EXISTS
  Antigravity uses protojson (camelCase JSON) over stdin/stdout with
  lifecycle events (PreToolUse, PostToolUse, PreInvocation, PostInvocation,
  Stop) and tool names (run_command, write_to_file, replace_file_content,
  multi_replace_file_content). This adapter receives Antigravity's stdin,
  maps the tool and arguments to Brother's internal fence and audit
  formats, and returns Antigravity's expected JSON decision contract.

EDGE-CASE LAW (FOUNDER RULE)
  An unknown, corrupt or unreadable input BLOCKS; it never reads as the
  safe case. If stdin cannot be read, if JSON parsing fails, or if a
  lifecycle event is unrecognized or malformed, the hook returns:
    {"decision": "deny", "reason": "..."}
  A fence that silently allows unparseable or corrupt payloads is a
  compromised fence.

HOST DATA GUARD: OFF BY DEFAULT, OPT IN WITH BROTHER_ANTIGRAVITY_GUARD_HOST_DATA
  The owner ruled in chat on 2026-09-30 (about 05:40 JST), in two
  messages: "Antigravity can access private data" and "I allow full access
  to antigravity". So a tool call that reads, writes or runs a command
  under Antigravity's own data folder (~/.gemini/antigravity-ide) is
  ALLOWED by default. The earlier guard (FX-49) still exists and denies
  those calls only when the environment variable
  BROTHER_ANTIGRAVITY_GUARD_HOST_DATA is set to "1".
    unset, empty or "0"   guard off (the owner's ruling)
    "1"                   guard on
    any other value       guard on, with a warning on stderr: an unknown
                          setting is unknown input, and under the edge
                          case law above it BLOCKS rather than reading as
                          the permissive case
  Every other deny is unchanged. Corrupt input blocks whatever the setting
  (OP1.c, docs/plan/specs/OP1.md 5.2 (e)): a known tool whose args is
  missing or not an object, and a run_command whose CommandLine is missing,
  empty or not a string, are denied before the guard is consulted. With the
  guard on, host_data_hit walks nested dicts and lists and expands ~ and
  $VAR before comparing a path (OP1.c (f)); with it off nothing changes.

Python 3.8+, standard library only.
"""

from __future__ import annotations

import json
import os
import shlex
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# Antigravity tool name -> canonical Brother tool name
TOOL_MAP = {
    "run_command": "Bash",
    "write_to_file": "Write",
    "replace_file_content": "Edit",
    "multi_replace_file_content": "MultiEdit",
    "view_file": "Read",
    "list_dir": "ListDir",
    "grep_search": "Grep",
}

KNOWN_TOOLS = frozenset(TOOL_MAP.keys())


def _out(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def _warn(msg):
    sys.stderr.write(f"brother_antigravity_hook: {msg}\n")
    sys.stderr.flush()


MAX_STDIN_CHARS = 1_000_000

# The fields each event's contract declares (Antigravity hooks.md, sections
# 3 to 5). Only the event's own fields are required, never the common ones,
# so a contract addition cannot turn every event into a block.
_EVENT_SHAPE = {
    "pre_invocation": {"invocationNum": int},
    "post_invocation": {"invocationNum": int},
    "stop": {"terminationReason": str, "fullyIdle": bool},
}
_MODE_ALIASES = {"PreInvocation": "pre_invocation", "PostInvocation": "post_invocation", "Stop": "stop"}


def _shape_error(mode, data):
    for field, kind in _EVENT_SHAPE.get(_MODE_ALIASES.get(mode, mode), {}).items():
        value = data.get(field)
        if isinstance(value, bool) and kind is not bool:
            return "%s must be %s, got bool" % (field, kind.__name__)
        if not isinstance(value, kind):
            return "%s must be %s, got %s" % (field, kind.__name__, type(value).__name__)
        if kind is str and not value.strip():
            return "%s is empty" % field
    return None


def read_payload(mode=None):
    """Read and parse JSON from stdin.
    Returns (data_dict, error_message_or_None).
    """
    try:
        # Bounded: a hook runs on every event, so an unbounded read lets one
        # oversized payload take memory in proportion (proven 2026-09-20:
        # a 20 MB payload grew the process by about 40 MB).
        raw = sys.stdin.read(MAX_STDIN_CHARS + 1)
        if len(raw) > MAX_STDIN_CHARS:
            err = "stdin payload exceeds %d characters" % MAX_STDIN_CHARS
            _warn(err)
            return None, err
    except Exception as exc:
        err = f"stdin read failed: {type(exc).__name__}: {exc}"
        _warn(err)
        return None, err

    if not raw or not raw.strip():
        err = "stdin payload is empty (no data received)"
        _warn(err)
        return None, err

    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            err = f"payload is not a JSON object (got {type(data).__name__})"
            _warn(err)
            return None, err
        shape = _shape_error(mode, data)
        if shape:
            # Valid JSON is not a valid payload: {"garbage": 1} used to be
            # waved through as a clean event. A wrong shape is corrupt input.
            err = "malformed payload for %s: %s" % (mode, shape)
            _warn(err)
            return None, err
        return data, None
    except ValueError as exc:
        err = f"corrupt JSON payload on stdin: {exc}"
        _warn(err)
        return None, err


HOST_DATA_ROOT_SUFFIX = (".gemini", "antigravity-ide")
HOST_DATA_GUARD_ENV = "BROTHER_ANTIGRAVITY_GUARD_HOST_DATA"


def _host_data_guard_on():
    """True when the host data guard is opted in (see the module docstring).

    Unset, empty or "0" is off, per the owner's ruling of 2026-09-30.
    "1" is on. Any other value is on and warns: unknown input blocks.
    """
    raw = os.environ.get(HOST_DATA_GUARD_ENV, "").strip()
    if raw in ("", "0"):
        return False
    if raw != "1":
        _warn("%s has an unknown value; the host data guard stays on" % HOST_DATA_GUARD_ENV)
    return True


def _host_data_root():
    """Return the resolved Antigravity host data root, or None.

    The canonical root is inferred from the host documentation location
    recorded in the description of handle_post_invocation
    (~/.gemini/antigravity-ide/builtin/skills/agy-customizations/docs/hooks.md).
    Host-owner confirmation is a pre-landing gate; until then any failure
    to establish a usable absolute home returns None and callers deny.
    """
    home = os.path.expanduser("~")
    if not home or home == "~" or not os.path.isabs(home):
        try:
            import pwd
            home = pwd.getpwuid(os.getuid()).pw_dir
        except (ImportError, KeyError):
            return None
    if not home or not os.path.isabs(home):
        return None
    return os.path.realpath(os.path.join(home, *HOST_DATA_ROOT_SUFFIX))


def _is_host_data_path(path, host_root):
    """Return True when path resolves inside host_root (or equals it).

    Refusal rules:
      - non-string path: False, not a path candidate; caller filters
      - empty or whitespace-only string: False, preserve existing decision
      - host_root None or non-string: True, fail closed; root unresolvable
      - OSError or ValueError while resolving: True, fail closed
    """
    if not isinstance(path, str):
        return False
    stripped = path.strip()
    if not stripped:
        return False
    if not host_root or not isinstance(host_root, str):
        return True
    try:
        # ~ then $VAR (OP1.c (f)): a variable carrying path is compared as the
        # path it names, never as its literal spelling.
        resolved_path = os.path.realpath(os.path.expandvars(os.path.expanduser(stripped)))
        resolved_root = os.path.realpath(os.path.expanduser(host_root))
    except (OSError, ValueError):
        return True
    if resolved_path == resolved_root:
        return True
    if resolved_path.startswith(resolved_root + os.sep):
        return True
    return _same_folder_under_another_spelling(resolved_path, resolved_root)


def _same_folder_under_another_spelling(resolved_path, resolved_root):
    """True when the path reaches host_root under a spelling a string comparison
    misses. A case insensitive or normalising filesystem opens the same folder
    as .GEMINI or .gemini, so identities are compared, never spellings: every
    existing ancestor of the path is asked whether it IS the root. When the
    root does not exist there is no identity to compare, and the spellings are
    compared case folded, which can only deny more. An identity that cannot be
    read is True, fail closed."""
    if not os.path.exists(resolved_root):
        folded_path, folded_root = resolved_path.casefold(), resolved_root.casefold()
        return folded_path == folded_root or folded_path.startswith(folded_root + os.sep)
    probe = resolved_path
    while True:
        if os.path.exists(probe):
            try:
                if os.path.samefile(probe, resolved_root):
                    return True
            except (OSError, ValueError):
                return True
        parent = os.path.dirname(probe)
        if parent == probe:
            return False
        probe = parent


def _command_targets_host_data(command, host_root):
    """Return True when a shell token in command resolves under host_root.

    Refusal rules:
      - command missing (None): False, preserve existing decision
      - command present but non-string: True, malformed, deny
      - command empty or whitespace-only: False, no candidate path
      - host_root None or non-string: True, fail closed
      - shlex.split ValueError on a nonempty command: True, deny
    """
    if command is None:
        return False
    if not isinstance(command, str):
        return True
    if not command.strip():
        return False
    if not host_root or not isinstance(host_root, str):
        return True
    try:
        tokens = shlex.split(command)
    except ValueError:
        return True
    if not tokens:
        return False
    return host_data_hit(tokens, host_root)


def host_data_hit(tool_args, host_root):
    """True when any string reachable through nested dicts and lists in
    tool_args, after ~ and $VAR expansion, resolves under host_root (OP1.c
    (f)). A non container tool_args is False here: the caller already denied
    it under (e). An explicit stack, never recursion, so a deeply nested
    payload cannot exhaust the interpreter and read as allowed."""
    if isinstance(tool_args, dict):
        stack = list(tool_args.values())
    elif isinstance(tool_args, list):
        stack = list(tool_args)
    else:
        return False
    while stack:
        value = stack.pop()
        if isinstance(value, dict):
            stack.extend(value.values())
        elif isinstance(value, list):
            stack.extend(value)
        elif _is_host_data_path(value, host_root):
            return True
    return False


def handle_pre_tool(payload, error=None):
    """Handle Antigravity PreToolUse event.
    Input: toolCall: {name, args}, stepIdx, conversationId, workspacePaths
    Output: {"decision": "allow" | "deny" | "ask", "reason": "..."}
    Returns the decision dict as well as writing it to stdout.
    """
    if error is not None:
        # Founder edge-case law: corrupt or unreadable input BLOCKS.
        decision = {
            "decision": "deny",
            "reason": f"Brother Antigravity PreToolUse blocked: {error}",
        }
        _out(decision)
        return decision

    if not isinstance(payload, dict):
        # Edge-case law: a payload that is not an object is corrupt input and
        # blocks. None, bytes, str, int, float, list and generator all arrive
        # here from any caller that bypassed read_payload's shape check.
        decision = {
            "decision": "deny",
            "reason": "Brother Antigravity PreToolUse blocked: payload is not an object",
        }
        _out(decision)
        return decision

    tool_call = payload.get("toolCall")
    if not isinstance(tool_call, dict):
        decision = {
            "decision": "deny",
            "reason": "Brother Antigravity PreToolUse blocked: missing or invalid 'toolCall' object",
        }
        _out(decision)
        return decision

    tool_name = tool_call.get("name")
    if not tool_name or not isinstance(tool_name, str):
        decision = {
            "decision": "deny",
            "reason": "Brother Antigravity PreToolUse blocked: missing or non-string tool name",
        }
        _out(decision)
        return decision

    if type(tool_name).__hash__ is None:
        # a str subclass with no hash cannot be tested against the known tool
        # set: corrupt input blocks here rather than raising through the hook
        decision = {
            "decision": "deny",
            "reason": "Brother Antigravity PreToolUse blocked: unhashable tool name",
        }
        _out(decision)
        return decision

    if tool_name not in KNOWN_TOOLS:
        decision = {
            "decision": "deny",
            "reason": f"Brother Antigravity PreToolUse blocked: unrecognised tool '{tool_name}'",
        }
        _out(decision)
        return decision

    tool_args = tool_call.get("args")
    if not isinstance(tool_args, dict):
        # OP1.c (e): corrupt input blocks whatever the host data setting
        # says. The owner's full access ruling covers well formed calls.
        decision = {
            "decision": "deny",
            "reason": "Brother Antigravity PreToolUse blocked: 'args' is missing or not an object",
        }
        _out(decision)
        return decision

    command = tool_args.get("CommandLine")
    if tool_name == "run_command" and (not isinstance(command, str) or not command.strip()):
        decision = {
            "decision": "deny",
            "reason": "Brother Antigravity PreToolUse blocked: CommandLine is missing, empty or not a string",
        }
        _out(decision)
        return decision

    if not _host_data_guard_on():
        # Owner ruling 2026-09-30: full access to the host data folder.
        decision = {"decision": "allow"}
        _out(decision)
        return decision

    host_root = _host_data_root()
    if tool_name == "run_command":
        if _command_targets_host_data(command, host_root):
            decision = {
                "decision": "deny",
                "reason": "Brother Antigravity PreToolUse blocked: command targets the host data directory",
            }
            _out(decision)
            return decision
    elif host_data_hit(tool_args, host_root):
        decision = {
            "decision": "deny",
            "reason": "Brother Antigravity PreToolUse blocked: argument targets the host data directory",
        }
        _out(decision)
        return decision

    # In clean state with valid payload and recognized tool, allow execution.
    decision = {"decision": "allow"}
    _out(decision)
    return decision


def handle_post_tool(payload, error=None):
    """Handle Antigravity PostToolUse event.
    Input: stepIdx, error, conversationId
    Output: {}
    """
    if error is not None:
        _warn(f"PostToolUse received corrupt payload: {error}")
    _out({})


def handle_pre_invocation(payload, error=None):
    """Handle Antigravity PreInvocation event.
    Input: invocationNum, conversationId, workspacePaths
    Output: {"injectSteps": [...]}
    """
    if error is not None:
        # KNOWN CONTRACT LIMIT, stated rather than hidden: PreInvocation's
        # only output is injectSteps (hooks.md section 3), so this event has
        # NO blocking verb. The corrupt input is reported here and the same
        # invocation is blocked at PostInvocation and Stop, which do have one.
        _warn(f"PreInvocation received corrupt payload and cannot block by contract: {error}")
    _out({"injectSteps": []})


def handle_post_invocation(payload, error=None):
    """Handle Antigravity PostInvocation event.
    Input: same as PreInvocation (invocationNum, conversationId, workspacePaths).
    Output: {"injectSteps": [...], "terminationBehavior": "force_continue" | "terminate" | ""}
    Real contract confirmed against
    ~/.gemini/antigravity-ide/builtin/skills/agy-customizations/docs/hooks.md
    section 4, "PostInvocation Contract", not assumed.
    """
    if error is not None:
        # Edge-case law: corrupt or unreadable input BLOCKS. "terminate" is
        # PostInvocation's real blocking verb, per the same contract this
        # handler reads from; "" (the earlier version of this fix) means
        # "default behavior", i.e. silently pass, the opposite of blocking.
        _warn(f"PostInvocation received corrupt payload: {error}")
        _out({
            "injectSteps": [],
            "terminationBehavior": "terminate",
        })
        return
    _out({"injectSteps": [], "terminationBehavior": ""})


def handle_stop(payload, error=None):
    """Handle Antigravity Stop event.
    Input: terminationReason, fullyIdle
    Output: {"decision": "allow" | "continue", "reason": "..."}
    """
    if error is not None:
        # If stop payload is corrupt, refuse exit until clean state is reached.
        _out({
            "decision": "continue",
            "reason": f"Brother Antigravity Stop blocked: corrupt termination payload ({error})",
        })
        return

    _out({"decision": "allow"})


def main():
    # No default mode. A hook launched without its event name used to be
    # treated as pre_tool and could answer "allow" (proven 2026-09-20); an
    # unstated event is an unknown event, and an unknown event blocks below.
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        payload, error = read_payload(mode)
    except Exception as exc:
        # A payload the reader cannot survive (a nesting deep enough to exhaust
        # the parser, for one) is corrupt input, and corrupt input blocks: the
        # handlers below turn this error into the deny or block their event
        # owes. Before this the hook died with no decision at all, and a hook
        # that crashes guards nothing (docs/how-to/install-antigravity.md).
        payload = None
        error = "payload could not be read: %s" % type(exc).__name__
        _warn(error)

    try:
        if mode in ("pre_tool", "PreToolUse"):
            handle_pre_tool(payload, error)
        elif mode in ("post_tool", "PostToolUse"):
            handle_post_tool(payload, error)
        elif mode in ("pre_invocation", "PreInvocation"):
            handle_pre_invocation(payload, error)
        elif mode in ("post_invocation", "PostInvocation"):
            handle_post_invocation(payload, error)
        elif mode in ("stop", "Stop"):
            handle_stop(payload, error)
        else:
            # Unknown event mode BLOCKS per founder edge-case law
            _out({
                "decision": "deny",
                "reason": f"Brother Antigravity blocked: unknown hook mode '{mode}'",
            })
    except Exception as exc:
        _warn(f"unhandled exception in {mode}: {type(exc).__name__}: {exc}")
        if mode in ("post_tool", "PostToolUse"):
            _out({})
        elif mode in ("post_invocation", "PostInvocation"):
            # Edge-case law: an unhandled exception BLOCKS, never silently
            # continues. terminate is PostInvocation's real blocking verb.
            _out({
                "injectSteps": [],
                "terminationBehavior": "terminate",
            })
        elif mode in ("stop", "Stop"):
            # each event answers in ITS OWN schema: Stop's blocking verb is
            # "continue"; "deny" belongs to PreToolUse and is not a Stop answer
            _out({
                "decision": "continue",
                "reason": f"Brother Antigravity Stop blocked on unhandled exception: {type(exc).__name__}",
            })
        elif mode in ("pre_invocation", "PreInvocation"):
            # no blocking verb exists for this event (hooks.md section 3)
            _out({"injectSteps": []})
        else:
            _out({
                "decision": "deny",
                "reason": f"Brother Antigravity hook blocked on unhandled exception: {type(exc).__name__}",
            })


if __name__ == "__main__":
    main()
