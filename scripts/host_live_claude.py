#!/usr/bin/env python3
"""host_live_claude.py: Claude Code as the reference host of the live proof (HP1.e, spec docs/plan/specs/HP1.md).

Claude Code gets the same witness (host_hook_trace.py), the same row schema and the same verifier as the other hosts, plus
a second and independent source: the host's own stream-json account of the hooks it fired (--include-hook-events).
crosscheck requires the witness rows and that account to agree on how often each hook event fired.

This module starts no process: the owner's command runs `claude` (argv from claude_argv, the witness shim first on PATH)
and redirects its stdout to claude_stream_path(trace); run_claude_session reads that file. A stream that cannot be read, a
binary that cannot be found or proven to be a Claude Code install, and a stream line that lacks the fields the parser
reads are NO-DATA, never GREEN.

usage: import from scripts/host_live_proof.py callers and scripts/test_host_live_claude.py. Python 3.9, standard library only.
"""
import glob
import hashlib
import json
import os
import re
import shlex
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BUNDLE = os.path.join(os.path.dirname(HERE), "bundle")

BIN_ENVS = ("HP1_CLAUDE_BIN", "CLAUDE_BIN")      # tried in this order; PATH is never consulted
ROOTS_ENV = "HP1_HOST_ALLOWED_ROOTS"             # colon separated extra directories a binary may live under
INSTALL_PARTS = ("Library", "Application Support", "Claude", "claude-code")
#: The layouts under the install folder, each starting with the version folder: <version>/claude.app (the 2026-10-03
#: reading) and <version>/<build hash>/claude.app (2.1.284 and 2.1.286 on this machine, read 2026-10-04).
APP_GLOBS = (("*", "claude.app", "Contents", "MacOS"), ("*", "*", "claude.app", "Contents", "MacOS"))
BINARY_NAME = "claude"
STREAM_NAME = "claude-stream.jsonl"
PLUGIN_ROOT_CITATION = "${CLAUDE_PLUGIN_ROOT}"
GUARD_NAME = "hook_guard.py"
MATCHER_FLAG = "--matcher="
PYTHON_NAMES = ("python3",)
FORBIDDEN_FLAGS = ("--bare",)                    # it skips hooks, so a run with it would prove nothing
HOST_OK = "ok"
HOST_NO_DATA = "no_data"


class ClaudeRefused(ValueError):
    """Hostile, unknown, corrupt or missing input: a refusal this module states, never a pass."""


def _need_str(value, name):
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ClaudeRefused("%s must be a non empty string, got %s" % (name, type(value).__name__))
    return value


def _env_text(env, name):
    value = env.get(name)
    return value if isinstance(value, str) and value.strip() and "\x00" not in value else ""


def _inside(path, root):
    root = os.path.realpath(root)
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def _newest(patterns):
    """The path under the globs with the highest numeric version folder (the first folder after the install folder),
    or None. A folder that is not dotted numbers is skipped."""
    best, best_key = None, None
    for pattern in patterns:
        for path in glob.glob(pattern):
            folder = os.path.relpath(path, pattern.split("*")[0]).split(os.sep)[0]
            if not re.fullmatch(r"\d+(\.\d+)*", folder):
                continue
            key = tuple(int(p) for p in folder.split("."))
            if best_key is None or key > best_key:
                best, best_key = path, key
    return best


def resolve_claude(env):
    """The absolute real path of the Claude Code binary, or None (NO-DATA). Order: HP1_CLAUDE_BIN, CLAUDE_BIN, then the
    newest claude under ~/Library/Application Support/Claude/claude-code/ (APP_GLOBS: with or without the build hash
    folder between the version and claude.app). PATH is never read:
    a row must say which binary fired, never whichever came first. A variable that is set but unusable gives None; it never
    falls through to the next one. The real path must lie under the install folder or a folder of HP1_HOST_ALLOWED_ROOTS."""
    if not isinstance(env, dict):
        return None
    home = _env_text(env, "HOME")
    if not home or not os.path.isabs(home):
        return None
    install = os.path.join(home, *INSTALL_PARTS)
    named = [(n, _env_text(env, n)) for n in BIN_ENVS if n in env]
    if named:
        candidate = named[0][1]
        if not candidate or not os.path.isabs(candidate):
            return None
    else:
        candidate = _newest([os.path.join(install, *parts, BINARY_NAME) for parts in APP_GLOBS])
        if candidate is None:
            return None
    real = os.path.realpath(candidate)
    if not (os.path.isfile(real) and os.access(real, os.X_OK)):
        return None
    roots = [install] + [r for r in _env_text(env, ROOTS_ENV).split(os.pathsep) if r and os.path.isabs(r)]
    return real if any(_inside(real, r) for r in roots) else None


def claude_argv(claude_bin, plugin_dir, prompt):
    """The session argv. --bare is refused anywhere (it skips hooks); the prompt may not look like a flag."""
    _need_str(claude_bin, "claude_bin")
    _need_str(plugin_dir, "plugin_dir")
    _need_str(prompt, "prompt")
    if not os.path.isabs(claude_bin) or not os.path.isabs(plugin_dir):
        raise ClaudeRefused("claude_bin and plugin_dir must be absolute paths")
    argv = [claude_bin, "--print", "--output-format", "stream-json", "--verbose", "--include-hook-events", "--plugin-dir", plugin_dir, prompt]
    if prompt.startswith("-") or any(word in FORBIDDEN_FLAGS for word in argv):
        raise ClaudeRefused("refused: %s skips hooks, so the run would prove nothing" % ", ".join(FORBIDDEN_FLAGS))
    return argv


def _script_of(command, root, base):
    """The absolute resolved script a hook command runs (the one after the hook_guard.py prefix), or None."""
    try:
        words = [w.replace(PLUGIN_ROOT_CITATION, root) for w in shlex.split(command)]
    except ValueError:
        return None
    if len(words) < 2 or os.path.basename(words[0]) not in PYTHON_NAMES:
        return None
    script, rest = words[1], words[2:]
    if os.path.basename(script) == GUARD_NAME:
        inner = rest[2:]
        if inner and inner[0].startswith(MATCHER_FLAG):
            inner = inner[1:]
        if len(inner) < 2 or os.path.basename(inner[0]) not in PYTHON_NAMES:
            return None
        script = inner[1]
    return os.path.realpath(script if os.path.isabs(script) else os.path.join(base, script))


def _hook_events(plugin_root):
    """(path, {event: blocks}) of <plugin_root>/hooks/hooks.json. Missing, corrupt or naming no event refuses."""
    _need_str(plugin_root, "plugin_root")
    path = os.path.join(plugin_root, "hooks", "hooks.json")
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError, RecursionError) as exc:
        raise ClaudeRefused("hook file %s is missing or corrupt: %s" % (path, exc))
    events = doc.get("hooks") if isinstance(doc, dict) else None
    if not isinstance(events, dict) or not events:
        raise ClaudeRefused("hook file %s names no hook events" % path)
    return path, events


def shipped_counts(plugin_root):
    """{event: how many hooks <plugin_root>/hooks/hooks.json registers for it} for every event none of whose blocks
    carries a matcher: each fire of such an event runs every one of them, once. An event with a matcher (the tool
    events) fires a number that depends on the tools the turn used, so it is left out (None in parse_hook_events)."""
    out = {}
    for name, blocks in _hook_events(plugin_root)[1].items():
        blocks = blocks if isinstance(blocks, list) else []
        if not blocks or any(not isinstance(b, dict) or b.get("matcher") not in (None, "") or
                             not isinstance(b.get("hooks"), list) for b in blocks):
            continue
        out[name] = sum(len(b["hooks"]) for b in blocks)
    return out


def shipped_scripts(plugin_root):
    """The allowlist: absolute script paths the commands of <plugin_root>/hooks/hooks.json run. Missing or corrupt refuses."""
    path, events = _hook_events(plugin_root)
    root, base, out = os.path.abspath(plugin_root), os.path.dirname(path), []
    for blocks in events.values():
        for block in blocks if isinstance(blocks, list) else []:
            for hook in block.get("hooks", []) if isinstance(block, dict) and isinstance(block.get("hooks"), list) else []:
                command = hook.get("command") if isinstance(hook, dict) else None
                script = _script_of(command, root, base) if isinstance(command, str) else None
                if script and script not in out:
                    out.append(script)
    if not out:
        raise ClaudeRefused("hook file %s runs no python3 script" % path)
    return out


def _lines(stream):
    """(complete lines, truncated count): a last segment with no newline that is not a whole JSON value is cut off."""
    if not isinstance(stream, bytes):
        raise ClaudeRefused("stream must be bytes, got %s" % type(stream).__name__)
    parts = stream.split(b"\n")
    tail = parts.pop()
    lines = [p for p in parts if p.strip()]
    truncated = 0
    if tail.strip():
        try:
            json.loads(tail.decode("utf-8"))
            lines.append(tail)
        except (UnicodeDecodeError, ValueError, RecursionError):
            truncated = 1
    return lines, truncated


def stream_truncated(stream):
    """1 when the stream was cut off mid line (that last partial line is ignored, not parsed), else 0."""
    return _lines(stream)[1]


#: The hook line shape Claude Code 2.1.286 writes under --output-format stream-json --verbose --include-hook-events,
#: read from the real run hp1-claude-20261004T111227Z-14772 (fixture scripts/fixtures/hp1-real-2026-10-04/): one
#: {"type": "system", "subtype": "hook_started", "hook_id", "hook_name": "SessionStart:startup", "hook_event",
#: "uuid", "session_id"} line per hook, then a "hook_response" line with the same hook_id adding output, stdout, stderr,
#: exit_code and outcome. No line carries the hook's command, so such a line is counted once by its hook_id and tied to
#: the shim by its answer: the crosscheck needs, per session, as many fires of an event without a matcher as the
#: candidate's hooks.json registers (shipped_counts), and the stream's (event, stdout sha256, exit_code) answers equal to
#: the shim rows' (event, raw_out_sha256, exit_code). A foreign hook is a surplus or a different answer, a shipped hook
#: that never fired a deficit: RED. KNOWN LIMIT: a foreign hook that replaced a shipped one AND answered byte for byte
#: the same cannot be told apart, the stream naming no script. A line that carries a command is held to the allowlist.
EVENT_KEY = "hook_event"
#: Events the stream cannot report: in the same run SessionEnd fired twice per session through the shim, after the
#: session's result line, and the stream holds no SessionEnd line. The crosscheck leaves them out on both sides.
STREAM_UNREPORTED = ("SessionEnd",)


def parse_hook_events(stream, plugin_root=None):
    """The hook events the host's own stream reports for the shipped scripts. A line counts when its JSON subtype starts
    with `hook`. It needs a string hook_event and either a string hook_id (the 2.1.286 shape, no command) or a string
    command, which is then kept only when it resolves to a path in the shipped allowlist. A hook line without them gives
    {"host_reported": "no_data"}: never a guess. One event per hook_id."""
    root = BUNDLE if plugin_root is None else plugin_root
    allowed = set(shipped_scripts(root))
    counts = shipped_counts(root)
    base = os.path.join(os.path.abspath(root), "hooks")
    lines, _ = _lines(stream)
    events, seen = [], {}
    for line in lines:
        try:
            doc = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError):
            continue
        subtype = doc.get("subtype") if isinstance(doc, dict) else None
        if not isinstance(subtype, str) or not subtype.startswith("hook"):
            continue
        name = doc.get(EVENT_KEY, doc.get("hook_event_name"))
        command = doc.get("command")
        hook_id = doc.get("hook_id")
        has_command = isinstance(command, str) and bool(command)
        has_id = isinstance(hook_id, str) and bool(hook_id)
        if not (isinstance(name, str) and name and (has_command or has_id)):
            events.append({"host_reported": HOST_NO_DATA, "subtype": subtype, "event": None, "command": None, "script": None})
            continue
        script = None
        if has_command:
            script = _script_of(command, os.path.abspath(root), base)
            if script not in allowed:
                continue
        binding = {}
        if isinstance(doc.get("stdout"), str) and isinstance(doc.get("exit_code"), int) \
                and not isinstance(doc.get("exit_code"), bool):
            binding = {"stdout_sha256": hashlib.sha256(doc["stdout"].encode("utf-8")).hexdigest(),
                       "exit_code": doc["exit_code"]}
        if has_id and hook_id in seen:
            seen[hook_id].update(binding)   # the response line of a hook already counted by its started line
            continue
        event = {"host_reported": HOST_OK, "subtype": subtype, "event": name,
                 "command": command if has_command else None, "script": script,
                 "hook_id": hook_id if has_id else None, "shipped": counts.get(name),
                 "session_id": doc.get("session_id") if isinstance(doc.get("session_id"), str) else None}
        event.update(binding)
        if has_id:
            seen[hook_id] = event
        events.append(event)
    return events


def stream_errors(stream):
    """The `result` text of every result line the stream marks is_error true (2.1.286 wrote a not logged in result on
    all three sessions of the 2026-10-04 run): the reason a session ran no turn, named rather than guessed."""
    out = []
    for line in _lines(stream)[0]:
        try:
            doc = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError):
            continue
        if isinstance(doc, dict) and doc.get("type") == "result" and doc.get("is_error") is True:
            out.append(doc["result"] if isinstance(doc.get("result"), str) else "is_error with no result text")
    return out


def _count(items, key):
    counts = {}
    for item in items:
        counts[item[key]] = counts.get(item[key], 0) + 1
    return counts


def _judge(rows, events):
    if not rows and not events:
        return False, "NO-DATA: neither shim rows nor stream events"
    if events and not rows:
        return False, "RED: the stream reports hook events but the trace has no shim rows (the shim was not first on PATH)"
    if rows and not events:
        return False, "RED: the trace has shim rows but the stream reports no hook events"
    rows = [r for r in rows if r["event"] not in STREAM_UNREPORTED]
    events = [e for e in events if e["event"] not in STREAM_UNREPORTED]
    if not rows and not events:
        return False, "NO-DATA: only %s rows, which the stream cannot report" % ", ".join(STREAM_UNREPORTED)
    shim, host = _count(rows, "event"), _count(events, "event")
    diffs = ["%s: shim %d, stream %d" % (n, shim.get(n, 0), host.get(n, 0)) for n in sorted(set(shim) | set(host))
             if shim.get(n, 0) != host.get(n, 0)]
    if diffs:
        return False, "RED: counts differ (%s)" % "; ".join(diffs)
    # The shipped set: per session, an event without a matcher fires every hook the candidate registers for it, once.
    # A foreign hook is a surplus, a shipped hook that never fired a deficit, whatever the shim counted.
    per = {}
    for e in events:
        if e["shipped"] is not None:
            per[(e["session_id"], e["event"])] = per.get((e["session_id"], e["event"]), 0) + 1
    wrong = ["%s in session %s: stream %d, shipped %d" % (n, sid, c, next(e["shipped"] for e in events if e["event"] == n))
             for (sid, n), c in sorted(per.items()) if c != next(e["shipped"] for e in events if e["event"] == n)]
    if wrong:
        return False, "RED: the stream's hook fires are not the candidate's shipped set (%s)" % "; ".join(wrong)
    # The binding: a stream line with no command names no script, so its response (stdout, exit_code) must be the
    # answer some shim row recorded, one row each. A foreign hook that replaced a shipped one answers differently.
    bare = [e for e in events if e.get("command") is None]
    names = set(e["event"] for e in bare)
    if bare:
        mine = [r for r in rows if r["event"] in names]
        if any(not isinstance(r.get("raw_out_sha256"), str) or not _is_int(r.get("exit_code")) for r in mine):
            return False, "NO-DATA: a shim row lacks the raw_out_sha256 or exit_code the stream's answers are tied to"
        theirs = _count([{"k": (e["event"], e["stdout_sha256"], e["exit_code"])} for e in bare], "k")
        if _count([{"k": (r["event"], r["raw_out_sha256"], r["exit_code"])} for r in mine], "k") != theirs:
            return False, "RED: the stream's hook answers are not the answers the shim recorded (a hook that is not " \
                          "the shipped one answered in the stream)"
    return True, "agree: %d hook fires" % len(rows)


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def crosscheck(shim_rows, stream_events):
    """(ok, reason): per run_id, the count of shim rows per event name equals the count the stream reports. Hostile or
    no_data input is (False, "NO-DATA: ..."), a disagreement (False, "RED: ..."). Stream events with no run_id join the one
    run_id the rows carry; with several run_ids they cannot be attributed and that is NO-DATA."""
    if not isinstance(shim_rows, list) or not isinstance(stream_events, list):
        return False, "NO-DATA: shim_rows and stream_events must be lists"
    for row in shim_rows:
        if not isinstance(row, dict) or not isinstance(row.get("event"), str) or not isinstance(row.get("run_id"), str):
            return False, "NO-DATA: a shim row lacks a string event or run_id"
    for ev in stream_events:
        if not isinstance(ev, dict):
            return False, "NO-DATA: a stream event is not an object"
        if ev.get("host_reported") != HOST_OK or not isinstance(ev.get("event"), str):
            return False, "NO-DATA: host_reported is %r, the stream line did not carry the fields the parser reads" % (
                ev.get("host_reported"),)
        if "run_id" in ev and not isinstance(ev["run_id"], str):
            return False, "NO-DATA: a stream event has a run_id that is not a string"
        if "shipped" not in ev or not (ev["shipped"] is None or _is_int(ev["shipped"])):
            return False, "NO-DATA: a stream event does not say how many hooks the candidate ships for its event"
        if ev["shipped"] is not None and not isinstance(ev.get("session_id"), str):
            return False, "NO-DATA: a %s stream event names no session_id" % ev["event"]
        if ev.get("command") is None and not (isinstance(ev.get("stdout_sha256"), str) and _is_int(ev.get("exit_code"))):
            return False, "NO-DATA: a %s stream event carries neither a command nor a response (stdout, exit_code) " \
                          "to tie it to a shim row" % ev["event"]
    run_ids = sorted(set(r["run_id"] for r in shim_rows))
    unnamed = [e for e in stream_events if "run_id" not in e]
    if unnamed and len(run_ids) > 1:
        return False, "NO-DATA: stream events carry no run_id and the trace holds %d runs" % len(run_ids)
    for rid in sorted(set(run_ids) | set(e["run_id"] for e in stream_events if "run_id" in e)):
        mine = [r for r in shim_rows if r["run_id"] == rid]
        theirs = [e for e in stream_events if e.get("run_id", run_ids[0] if run_ids else rid) == rid]
        ok, why = _judge(mine, theirs)
        if not ok:
            return False, "run %s: %s" % (rid, why)
    if not run_ids:
        return _judge([], stream_events)
    return True, "agree for %d run(s)" % len(run_ids)


def claude_stream_path(trace_path):
    """Where the raw stream lives: beside the evidence file."""
    return os.path.join(os.path.dirname(os.path.abspath(_need_str(trace_path, "trace_path"))), STREAM_NAME)


def _read_trace(path):
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError as exc:
        raise ClaudeRefused("the trace %s is unreadable: %s" % (path, exc))
    rows = []
    for n, line in enumerate(data.split(b"\n")[:-1], 1):
        if line.strip():
            try:
                row = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, ValueError, RecursionError):
                row = None
            if not isinstance(row, dict):
                raise ClaudeRefused("the trace %s line %d is not a JSON object" % (path, n))
            rows.append(row)
    return rows


def run_claude_session(home, source, trace_path, timeout_s):
    """The stream events of one finished Claude Code session. This module starts no process: the owner's command ran
    claude_argv with the shim first on PATH and wrote the stream to claude_stream_path(trace_path); a missing or empty
    stream refuses (NO-DATA). Each event gets the run_id of the trace when the trace holds exactly one."""
    _need_str(home, "home")
    _need_str(source, "source")
    if isinstance(timeout_s, bool) or not isinstance(timeout_s, int) or not 0 < timeout_s <= 86400:
        raise ClaudeRefused("timeout_s must be an integer from 1 to 86400, got %s" % type(timeout_s).__name__)
    if not os.path.isdir(home):
        raise ClaudeRefused("home %s is not a folder" % home)
    stream_path = claude_stream_path(trace_path)
    try:
        with open(stream_path, "rb") as fh:
            stream = fh.read()
    except OSError as exc:
        raise ClaudeRefused("NO-DATA: the stream %s is unreadable: %s" % (stream_path, exc))
    if not stream.strip():
        raise ClaudeRefused("NO-DATA: the stream %s is empty" % stream_path)
    events = parse_hook_events(stream, os.path.join(os.path.realpath(source), "bundle"))
    run_ids = sorted(set(str(r.get("run_id")) for r in _read_trace(trace_path)))
    if len(run_ids) == 1:
        for ev in events:
            ev["run_id"] = run_ids[0]
    return events
