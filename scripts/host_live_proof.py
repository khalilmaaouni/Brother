#!/usr/bin/env python3
"""host_live_proof.py: the per host live proof driver (HP1.a, spec docs/plan/specs/HP1.md sections 5.1 and 5.2).

WHY THIS EXISTS. Brother ships one plugin for Claude Code, Codex and Antigravity, and until HP1 every "real" row was
written by the harness that also started the hook, so a row could not say which process started it. This driver builds a
throwaway home, installs the one plugin there, writes a `python3` shim that runs the witness (host_hook_trace.py) and puts
it FIRST on the host's PATH, so the host itself fires the shipped hooks and the witness records each fire byte for byte.
It edits no shipped hook file (REQ-HP1-NO-EDIT): the Codex trust hashes, which cover the hook definitions, stay valid.

The one table VERDICT_RULES and the one labeller probe_of live here; the witness and the verifier (HP1.b) call the same
functions, so a row cannot be relabelled to satisfy a check.

REQ-HP1-SESSION: run_host with start False builds and returns the session plan and starts no process. Only the owner
command `HP1_LIVE=1 python3 scripts/host_live_proof.py --run <host>` starts a host session; without HP1_LIVE=1 it is
refused, so no loop test or gate ever spends a model call.

usage (repo root):
  python3 scripts/host_live_proof.py --plan <host> [--home DIR] [--trace FILE]       the session plan as JSON, no process
  python3 scripts/host_live_proof.py --print-owner-commands <host> [--home DIR]      the owner's exact commands
  HP1_LIVE=1 python3 scripts/host_live_proof.py --run <host> [--home DIR] [--trace FILE] [--wait S]
  python3 scripts/host_live_proof.py --collect <host> --home DIR [--trace FILE] [--evidence FILE]
Exit: 0 done, 2 refused, 3 NO-DATA (a required probe row did not arrive, or on Claude Code the host's stream and the
witness rows do not agree). Claude Code (HP1.e, host_live_claude.py): the binary comes from resolve_claude and never
from PATH alone, the argv from claude_argv (--plugin-dir the candidate bundle, never --bare), the raw stream is stored
beside the trace and crosscheck is applied to it.

THE COLLECTOR (HP1.f, spec section 10): `--collect` turns the witness's trace rows (hp1.trace.v1) of ONE run into the
hp1.v1 evidence rows host_live_verify.py reads, appended to docs/plan/evidence/HP1-host-live.jsonl through write_row.
It reads only this run's rows (bound by run id, the throwaway home's name), re-hashes every raw file against its sha256
name before copying it, derives every field from a recorded fact (the trace row, the session record `--run` wrote, the
envelopes and stream beside the trace, the installed hook file, the candidate's git objects) and never invents one: a
field it cannot derive makes the row NO-DATA and the row is not written; a raw file that does not hash to its name is
RED and nothing of the run is written. It ends with the verifier's own line under the 1.1.0 scope (Claude Code and
Codex decide, Antigravity reported NO-DATA, experimental). Exit: 0 collected and GREEN, 1 RED, 3 NO-DATA.
Python 3.9 and 3.13, standard library only.
"""
import argparse
import base64
import datetime
import glob
import hashlib
import json
import os
import pwd
import re
import shlex
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import host_live_claude as claude  # noqa: E402  HP1.e: the one Claude Code binary rule, argv, stream parser and crosscheck

WITNESS = os.path.join(HERE, "host_hook_trace.py")
HOSTS = ("codex", "antigravity", "claude")
PROBES = ("door", "verb", "guarded_write")
EXTRA_PROBES = {"antigravity": ("post_invocation",)}   # the Antigravity signed in proof needs a PostInvocation row (5.3)
TRACE_SCHEMA = "hp1.trace.v1"

#: The environment the driver hands the host and the witness reads. HP1_LIVE is the owner's switch and never reaches a host.
LIVE_ENV = "HP1_LIVE"
TRACE_ENV = "HP1_TRACE"
HOST_ENV = "HP1_HOST"
RUN_ENV = "HP1_RUN_ID"
SCRIPTS_ENV = "HP1_HOOK_SCRIPTS"    # colon separated absolute script paths, from every hook file the installers read
CALLS_ENV = "HP1_HOOK_CALLS"        # JSON [{script, args, event, timeout}]: the mode each shipped command carries
ROOTS_ENV = "HP1_PLUGIN_ROOTS"      # colon separated plugin roots the scripts resolve under
HOST_ROOTS_ENV = "HP1_HOST_ROOTS"   # colon separated install directories of the host binary
PYTHON_ENV = "HP1_REAL_PYTHON"      # the absolute interpreter the shim names
SHIM_ENV = "HP1_SHIM_DIR"

#: The hook files the installers read, relative to the candidate checkout, and the plugin root each expands against.
SHIPPED_HOOKS = ("bundle/.antigravity-plugin/hooks.json", "bundle/hooks/hooks.json")
SHIPPED_ROOTS = ("bundle/.antigravity-plugin", "bundle")
PRODUCT_HOOKS = os.path.join("products", "*", "hooks", "hooks.json")
PLUGIN_ROOT_CITATION = "${CLAUDE_PLUGIN_ROOT}"
GUARD_NAME = "hook_guard.py"         # the Claude Code wrapper: it execs the inner `python3 <script>` through PATH
MATCHER_FLAG = "--matcher="
PYTHON_NAMES = ("python3",)          # what a shipped command and the guard start through PATH

#: The probes, in order. The labels are never assigned from this order: probe_of reads them from the host's own payload.
DOOR_SENTENCE = "Use Brother: show status"
VERB_SENTENCE = "brother status"
GUARDED_WRITE_NAME = "hp1-guarded-write.txt"
GUARDED_WRITE_SENTENCE = "Write the single word hp1 into the file %s"
PROMPT_EVENTS = ("UserPromptSubmit", "SessionStart")
INVOCATION_PROBES = {1: "door", 2: "verb"}   # Antigravity PreInvocation carries no prompt, only the host's counter

#: VERDICT_RULES (5.1): (verdict_source, verdict), the order parse_verdict tries them. No rule holding is no_data:
#: unparseable output is never an allow. JSON rules hold only on exit 0, where the hook protocol reads stdout.
VERDICT_RULES = (
    ("exit_2", "deny"),            # exit code 2: the Claude Code hook protocol, which the Codex hooks share
    ("json_deny", "deny"),         # JSON decision (or hookSpecificOutput.permissionDecision) deny or block
    ("json_allow", "allow"),       # ... allow
    ("json_ask", "ask"),           # ... ask
    ("json_terminate", "deny"),    # Antigravity PostInvocation terminationBehavior terminate
    ("json_inject", "allow"),      # Antigravity injectSteps with terminationBehavior absent, "" or force_continue
    ("silent_exit_0", "allow"),    # exit 0 and stdout exactly empty
)
DECISION_SOURCES = {"deny": "json_deny", "block": "json_deny", "allow": "json_allow", "ask": "json_ask"}
NO_DATA = ("no_data", "no_rule")

#: The collector (HP1.f). SESSION_SCHEMA is the record `--run` writes beside the trace so `--collect` derives the host
#: binary, its version and the paths from facts recorded at run time, never re-resolved later.
EVIDENCE_SCHEMA = "hp1.v1"
SESSION_SCHEMA = "hp1.session.v1"
EVIDENCE_REL = os.path.join("docs", "plan", "evidence", "HP1-host-live.jsonl")
SESSION_SUFFIX = "-session.json"
LOGIN_SUFFIX = "-login-status.json"
SHIM_SUFFIX = "-python3.shim"
STREAM_SUFFIX = "-claude-stream.jsonl"
VERSION_TIMEOUT_S = 60
#: What the witness recorded (host_hook_trace._record) and the evidence row carries verbatim. probe is never copied: the
#: one labeller re-derives it from the raw stdin.
TRACE_KEYS = ("host", "run_id", "event", "hook_command", "script", "script_sha256", "plugin_root", "exit_code",
              "verdict", "verdict_source", "stderr_len", "stderr_sha256", "parent_chain", "parent_chain_error",
              "host_roots", "shim_dir", "recorded_at", "real_python", "witness_sha256", "witness_argv", "cwd")
SESSION_KEYS = ("schema", "host", "run_id", "home", "source", "host_bin", "host_bin_realpath", "host_version",
                "host_roots", "shim_dir", "shim_path", "real_python", "plugin_root", "hooks_path", "target",
                "workspace", "trace_path", "exists_before", "plugin_tree_sha256", "tool_sha256", "recorded_at")
MANIFEST_SUFFIX = ".sha256"   # <evidence>.sha256: write_manifest's digest of the evidence file, checked by the verifier
INSTALL_KEYS = ("plugin_install", "plugin_install_sha256")   # Codex: the measured install and its install_digest
CANDIDATE_KEYS = ("plugin_tree_sha256", "tool_sha256")   # read at run time from the candidate installed, re-read at collect
HOST_ENVELOPES = {"codex": ("signed_in", "model_turn"), "claude": ("model_turn",), "antigravity": ()}

RAW_CAP = 2000000          # bytes kept per raw file; a longer payload is recorded by hash and length, truncated true
CHAIN_LIMIT_MAX = 64
WAIT_MAX_S = 86400
POLL_S = 0.5
PROC_TIMEOUT_S = 10        # estimate: lsof over every process took 0.4 s on 2026-10-03
INSTALL_TIMEOUT_S = 600
LSOF = "/usr/sbin/lsof"
PGREP = "/usr/bin/pgrep"


class HP1Refused(ValueError):
    """A refusal this module states: hostile, unknown, corrupt or missing input, or an unsafe home. Never a pass."""


class ChainUnreadable(LookupError):
    """The process table could not be read: the parent chain is NO-DATA, never guessed."""


def _need_str(value, name):
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise HP1Refused("%s must be a non empty string, got %s" % (name, type(value).__name__))
    return value


def _need_int(value, name, low, high):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise HP1Refused("%s must be an integer from %d to %d, got %s" % (name, low, high, type(value).__name__))
    return value


def _need_host(host):
    _need_str(host, "host")
    if host not in HOSTS:
        raise HP1Refused("unknown host %r: one of %s" % (host[:40], ", ".join(HOSTS)))
    return host


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _sha256_path(path):
    with open(path, "rb") as fh:
        return _sha256(fh.read())


def _repo_root():
    return os.path.dirname(HERE)


def payload_of(raw_in):
    """The hook payload as a dict when raw_in is a non empty JSON object, else None (empty, not UTF-8, not JSON, not an
    object, or an empty object). Refuses anything that is not bytes."""
    if not isinstance(raw_in, bytes):
        raise HP1Refused("raw_in must be bytes, got %s" % type(raw_in).__name__)
    try:
        doc = json.loads(raw_in.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        return None
    return doc if isinstance(doc, dict) and doc else None


def parse_verdict(stdout, exit_code):
    """(verdict, verdict_source) for one hook answer, by VERDICT_RULES; ("no_data", "no_rule") when no rule holds."""
    if not isinstance(stdout, bytes):
        raise HP1Refused("stdout must be bytes, got %s" % type(stdout).__name__)
    if isinstance(exit_code, bool) or not isinstance(exit_code, int):
        raise HP1Refused("exit_code must be an integer, got %s" % type(exit_code).__name__)
    source = _verdict_source(stdout, exit_code)
    verdict = dict(VERDICT_RULES).get(source)
    return (verdict, source) if verdict else NO_DATA


def _verdict_source(stdout, exit_code):
    if exit_code == 2:
        return "exit_2"
    if exit_code != 0:
        return None
    if stdout == b"":
        return "silent_exit_0"
    doc = payload_of(stdout)
    if doc is None:
        return None
    words = []
    if "decision" in doc:
        words.append(doc["decision"])
    specific = doc.get("hookSpecificOutput")
    if isinstance(specific, dict) and "permissionDecision" in specific:
        words.append(specific["permissionDecision"])
    if words:
        sources = [DECISION_SOURCES.get(w) if isinstance(w, str) else None for w in words]
        return sources[0] if None not in sources and len(set(sources)) == 1 else None   # two answers that disagree are corrupt
    if "injectSteps" in doc:
        if not isinstance(doc["injectSteps"], list):
            return None
        behaviour = doc.get("terminationBehavior", "")
        if behaviour == "terminate":
            return "json_terminate"
        if behaviour in ("", "force_continue"):
            return "json_inject"
    return None


def probe_of(raw_in, *, event=None):
    """The probe label the HOST's own payload supports: guarded_write, door, verb, post_invocation or other.

    event: the hook event when the payload names none (Antigravity payloads carry no event field; the witness passes the
    event of the shipped definition the call fired). A payload's own hook_event_name always wins. Never reads call order."""
    payload = payload_of(raw_in)
    if event is not None and not isinstance(event, str):
        raise HP1Refused("event must be a string or None, got %s" % type(event).__name__)
    if payload is None:
        return "other"
    named = payload.get("hook_event_name")
    ev = named if isinstance(named, str) and named else event
    if ev == "PreToolUse":
        call = payload.get("tool_input") if "tool_input" in payload else payload.get("toolCall")
        return "guarded_write" if _names_target(call) else "other"
    if ev in PROMPT_EVENTS:
        prompt = payload.get("prompt")
        text = prompt.strip() if isinstance(prompt, str) else None
        return "door" if text == DOOR_SENTENCE else "verb" if text == VERB_SENTENCE else "other"
    if ev == "PreInvocation":
        num = payload.get("invocationNum")
        return INVOCATION_PROBES.get(num, "other") if isinstance(num, int) and not isinstance(num, bool) else "other"
    if ev == "PostInvocation":
        return "post_invocation"
    return "other"


def _names_target(node):
    """True when some string inside a tool call names the guarded write target file."""
    stack = [node]
    while stack:
        item = stack.pop()
        if isinstance(item, str) and GUARDED_WRITE_NAME in item:
            return True
        if isinstance(item, dict):
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    return False


def _read_hooks(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError, RecursionError) as exc:
        raise HP1Refused("hook file %s is missing or corrupt: %s" % (path, exc))


def _event_maps(doc, path):
    """The {event: [blocks]} maps of one hook file: Claude Code and product files nest them under "hooks", the
    Antigravity file under the plugin's own name."""
    if not isinstance(doc, dict):
        raise HP1Refused("hook file %s is not a JSON object" % path)
    if isinstance(doc.get("hooks"), dict):
        return [doc["hooks"]]
    maps = [v for v in doc.values() if isinstance(v, dict)]
    if not maps:
        raise HP1Refused("hook file %s names no hook events" % path)
    return maps


def _python_call(command, root, base, path):
    """(absolute script, args) the witness sees for one shipped command, or None when the command starts no python3
    through PATH. A Claude Code command is the script AFTER the hook_guard.py prefix: the guard execs it through PATH."""
    try:
        words = [w.replace(PLUGIN_ROOT_CITATION, root) for w in shlex.split(command)]
    except ValueError as exc:
        raise HP1Refused("hook file %s holds a command that does not parse: %s" % (path, exc))
    if len(words) < 2 or os.path.basename(words[0]) not in PYTHON_NAMES:
        return None
    script, rest = words[1], words[2:]
    if os.path.basename(script) == GUARD_NAME:
        inner = rest[2:]   # after <product> <event>
        if inner and inner[0].startswith(MATCHER_FLAG):
            inner = inner[1:]
        if len(inner) < 2 or os.path.basename(inner[0]) not in PYTHON_NAMES:
            return None
        script, rest = inner[1], inner[2:]
    return os.path.realpath(script if os.path.isabs(script) else os.path.join(base, script)), rest


def _timeout(hook):
    value = hook.get("timeout", hook.get("timeoutSec"))
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value < 86400:
        return None
    return float(value)


def _hook_calls(hooks_file, plugin_root):
    """[{script, args, event, timeout}] for every python3 command one hook file defines. ${CLAUDE_PLUGIN_ROOT} expands to
    plugin_root, a relative script to the hook file's own directory (the working directory the host gives the command)."""
    doc = _read_hooks(hooks_file)
    base = os.path.dirname(os.path.abspath(hooks_file))
    root = os.path.abspath(plugin_root)
    calls = []
    for events in _event_maps(doc, hooks_file):
        for event, blocks in events.items():
            if not isinstance(blocks, list):
                raise HP1Refused("hook file %s: event %s is not a list" % (hooks_file, event))
            for block in blocks:
                hooks = [block] if isinstance(block, dict) and "command" in block else (
                    block.get("hooks") if isinstance(block, dict) else None)
                if not isinstance(hooks, list):
                    raise HP1Refused("hook file %s: a block of %s holds no hook list" % (hooks_file, event))
                for hook in hooks:
                    if not isinstance(hook, dict) or not isinstance(hook.get("command"), str):
                        raise HP1Refused("hook file %s: a hook of %s has no command string" % (hooks_file, event))
                    found = _python_call(hook["command"], root, base, hooks_file)
                    if found:
                        calls.append({"script": found[0], "args": found[1], "event": event, "timeout": _timeout(hook)})
    return calls


def hook_scripts(hooks_files, plugin_root):
    """The ABSOLUTE resolved script paths the commands of these hook files run (a basename is never a match). A missing
    or corrupt hook file refuses: an allowlist built from a guess would record the wrong thing or nothing."""
    if not isinstance(hooks_files, list) or not hooks_files:
        raise HP1Refused("hooks_files must be a non empty list of paths")
    for path in hooks_files:
        _need_str(path, "hooks_files entry")
    _need_str(plugin_root, "plugin_root")
    out = []
    for path in hooks_files:
        for call in _hook_calls(path, plugin_root):
            if call["script"] not in out:
                out.append(call["script"])
    return out


def _allowlist(src):
    """(calls, plugin_roots) from every hook file the installers read under the candidate checkout src."""
    pairs = [(os.path.join(src, rel), os.path.join(src, root)) for rel, root in zip(SHIPPED_HOOKS, SHIPPED_ROOTS)]
    pairs += [(path, os.path.dirname(os.path.dirname(path))) for path in sorted(glob.glob(os.path.join(src, PRODUCT_HOOKS)))]
    calls, roots = [], []
    for path, root in pairs:
        calls += _hook_calls(path, root)
        roots.append(os.path.realpath(root))
    return calls, roots


def _witness_env(base, calls, roots, trace_path, host, run_id, real_python, shim_dir, host_roots):
    """The host's environment: base, the witness first on PATH, and what the witness reads. HP1_LIVE never passes."""
    env = dict(base)
    env.pop(LIVE_ENV, None)
    env["PATH"] = shim_dir + os.pathsep + base.get("PATH", os.defpath)
    scripts = []
    for call in calls:
        if call["script"] not in scripts:
            scripts.append(call["script"])
    env.update({TRACE_ENV: trace_path, HOST_ENV: host, RUN_ENV: run_id, SCRIPTS_ENV: os.pathsep.join(scripts),
                CALLS_ENV: json.dumps(calls, sort_keys=True), ROOTS_ENV: os.pathsep.join(roots),
                HOST_ROOTS_ENV: os.pathsep.join(host_roots), PYTHON_ENV: real_python, SHIM_ENV: shim_dir})
    return env


def _proc_table():
    """{pid: {ppid, comm, exe, argv}} for the processes this account can see. Linux reads /proc; macOS asks lsof (pid,
    parent, command, executable) and pgrep (the command line), both with a fixed argv, since ps is setuid and refused
    inside a sandbox. Unreadable: ChainUnreadable, never an empty guess."""
    if os.path.isfile("/proc/self/stat"):
        return _proc_table_linux()
    try:
        listed = subprocess.run([LSOF, "-nP", "-w", "-R", "+c", "0", "-d", "txt", "-F", "pRcn"],
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=PROC_TIMEOUT_S).stdout
        lines = subprocess.run([PGREP, "-a", "-l", "-f", "."],   # -a: by default pgrep hides its own ancestors
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=PROC_TIMEOUT_S).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise ChainUnreadable("the process table is unreadable: %s" % exc)
    table, cur = {}, None
    for line in listed.decode("utf-8", "replace").splitlines():
        tag, value = line[:1], line[1:]
        if tag == "p":
            cur = table.setdefault(int(value), {"ppid": 0, "comm": "", "exe": "", "argv": ""}) if value.isdigit() else None
        elif cur is not None and tag == "R" and value.isdigit():
            cur["ppid"] = int(value)
        elif cur is not None and tag == "c":
            cur["comm"] = value
        elif cur is not None and tag == "n" and not cur["exe"]:
            cur["exe"] = value   # the first txt entry is the executable itself
    for line in lines.decode("utf-8", "replace").splitlines():
        head, _, rest = line.partition(" ")
        if head.isdigit() and int(head) in table:
            table[int(head)]["argv"] = rest
    if not table:
        raise ChainUnreadable("lsof listed no process")
    return table


def _proc_table_linux():
    table = {}
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            with open("/proc/%s/stat" % name, encoding="utf-8", errors="replace") as fh:
                stat = fh.read()
            with open("/proc/%s/cmdline" % name, "rb") as fh:
                argv = fh.read().rstrip(b"\x00").replace(b"\x00", b" ").decode("utf-8", "replace")
        except OSError:
            continue
        try:
            exe = os.readlink("/proc/%s/exe" % name)
        except OSError:
            exe = ""
        head, _, tail = stat.rpartition(")")
        fields = tail.split()
        if len(fields) > 1 and fields[1].isdigit():
            table[int(name)] = {"ppid": int(fields[1]), "comm": head.partition("(")[2], "exe": exe, "argv": argv}
    if not table:
        raise ChainUnreadable("/proc listed no process")
    return table


def parent_chain(pid, limit=8):
    """[{pid, ppid, comm, exe, argv}] for pid and each ancestor, up to limit entries (REQ-HP1-ANCESTOR). exe is the
    resolved executable; argv is the command line as the process table prints it (what the shell rule of 5.3 reads)."""
    _need_int(pid, "pid", 1, 2 ** 31 - 1)
    _need_int(limit, "limit", 1, CHAIN_LIMIT_MAX)
    table = _proc_table()
    chain, cur = [], pid
    while cur > 0 and len(chain) < limit and cur in table:
        info = table[cur]
        exe = os.path.realpath(info["exe"]) if info["exe"] else ""
        chain.append({"pid": cur, "ppid": info["ppid"], "comm": info["comm"], "exe": exe, "argv": info["argv"]})
        if info["ppid"] == cur or any(link["pid"] == info["ppid"] for link in chain):
            break
        cur = info["ppid"]
    return chain   # the witness first, then each ancestor up to the limit


def write_shim(shim_dir, real_python):
    """Write the two line `python3` stand in into shim_dir (created when missing, so a PATH whose first entry is the shim
    directory never names a missing folder) and return its path. The bytes depend only on real_python and this checkout."""
    _need_str(shim_dir, "shim_dir")
    _need_str(real_python, "real_python")
    if not os.path.isabs(shim_dir) or not os.path.isabs(real_python):
        raise HP1Refused("shim_dir and real_python must be absolute paths")
    if not (os.path.isfile(real_python) and os.access(real_python, os.X_OK)):
        raise HP1Refused("real_python %s is not an executable file" % real_python)
    body = "#!/bin/sh\nexec %s -B %s \"$@\"\n" % (shlex.quote(real_python), shlex.quote(WITNESS))
    os.makedirs(shim_dir, exist_ok=True)
    path = os.path.join(shim_dir, "python3")
    fd, tmp = tempfile.mkstemp(prefix=".python3-", dir=shim_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(body)
        os.chmod(tmp, 0o755)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return path


def write_manifest(evidence_path):
    """Rewrite <evidence>.sha256 with the sha256 of the evidence file as it now stands, and return it. Every row names
    its raw files, envelopes, shim copy and stream by sha256, so this one digest binds the whole evidence folder: a row
    or file edited by hand after collection no longer matches, and the verifier reads NO-DATA. It proves the folder is
    what the collector wrote, not who ran the collector: a folder forged whole, manifest recomputed, is owner machine
    trust (spec section 13, question 4). An unreadable evidence file or unwritable manifest is refused."""
    try:
        with open(_need_str(evidence_path, "evidence_path"), "rb") as fh:
            digest = _sha256(fh.read())
        _write_bytes(evidence_path + MANIFEST_SUFFIX, (digest + "\n").encode("ascii"))
    except OSError as exc:
        raise HP1Refused("write_manifest: %s" % exc)
    return digest


def write_row(evidence_path, row, raw_in, raw_out):
    """Write raw_in and raw_out beside evidence_path, each named by its sha256, THEN append the row as one line with a
    single os.write on an O_APPEND descriptor: a crash leaves raw files with no row, never a row with no bytes. A raw
    file keeps at most RAW_CAP bytes; the row carries the full lengths and truncated. The folder must already exist."""
    _need_str(evidence_path, "evidence_path")
    if not isinstance(row, dict) or not all(isinstance(k, str) for k in row):
        raise HP1Refused("row must be a dict with string keys")
    if not isinstance(raw_in, bytes) or not isinstance(raw_out, bytes):
        raise HP1Refused("raw_in and raw_out must be bytes")
    folder = os.path.dirname(os.path.abspath(evidence_path))
    if not os.path.isdir(folder):
        raise HP1Refused("the evidence folder %s does not exist" % folder)
    full = dict(row)
    for name, data in (("raw_in", raw_in), ("raw_out", raw_out)):
        kept = data[:RAW_CAP]
        digest = _sha256(kept)
        _write_bytes(os.path.join(folder, digest + ".raw"), kept)
        full.update({name + "_path": digest + ".raw", name + "_sha256": digest, name + "_len": len(data)})
    full["truncated"] = len(raw_in) > RAW_CAP or len(raw_out) > RAW_CAP
    try:
        line = (json.dumps(full, sort_keys=True, ensure_ascii=True, allow_nan=False) + "\n").encode("ascii")
    except (TypeError, ValueError) as exc:
        raise HP1Refused("the row is not JSON: %s" % exc)
    fd = os.open(evidence_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        wrote = os.write(fd, line)   # ONE write: two concurrent writers append two whole lines, never interleaved
    finally:
        os.close(fd)
    if wrote != len(line):
        raise OSError("short write to %s: %d of %d bytes" % (evidence_path, wrote, len(line)))


def _write_bytes(path, data):
    fd, tmp = tempfile.mkstemp(prefix=".raw-", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _real_homes():
    homes = {os.path.realpath(os.path.expanduser("~"))}
    try:
        homes.add(os.path.realpath(pwd.getpwuid(os.getuid()).pw_dir))
    except KeyError:
        pass
    return homes


def _scratch_root():
    return os.path.realpath(os.path.join(os.path.expanduser("~"), ".claude", "brother-scratch"))


def _check_home(home):
    """The resolved throwaway home, refused when it resolves to a real home (HOME, the account's own home, or a real
    ~/.codex) or lies outside ~/.claude/brother-scratch (REQ-HP1-THROWAWAY)."""
    _need_str(home, "home")
    full = os.path.realpath(os.path.expanduser(home))
    reals = _real_homes()
    if full in reals or full in {os.path.join(r, ".codex") for r in reals}:
        raise HP1Refused("refused: %s resolves to the real home %s; a live proof runs only in a throwaway home" % (home, full))
    scratch = _scratch_root()
    if not full.startswith(scratch + os.sep):
        raise HP1Refused("refused: the throwaway home %s must lie under %s" % (full, scratch))
    return full


def _check_source(source):
    _need_str(source, "source")
    src = os.path.realpath(os.path.expanduser(source))
    missing = [rel for rel in SHIPPED_HOOKS if not os.path.isfile(os.path.join(src, rel))]
    if missing:
        raise HP1Refused("source %s is not a candidate checkout: missing %s" % (source, ", ".join(missing)))
    return src


def _shipped_hashes(src):
    try:
        return dict((rel, _sha256_path(os.path.join(src, rel))) for rel in SHIPPED_HOOKS)
    except OSError as exc:
        raise HP1Refused("a shipped hook file is unreadable: %s" % exc)


def _layout(full):
    return {"home": full, "workspace": os.path.join(full, "workspace"), "outside": os.path.join(full, "outside"),
            "target": os.path.join(full, "outside", GUARDED_WRITE_NAME), "shim_dir": os.path.join(full, "shim"),
            "codex_home": os.path.join(full, ".codex"), "trace": os.path.join(full, "trace", "trace.jsonl")}


def prepare(host, home, source):
    """Build the fresh throwaway home under ~/.claude/brother-scratch and install the one plugin from the candidate
    checkout source: Antigravity by linking bundle/.antigravity-plugin into <workspace>/.agents/plugins/brother, Claude
    Code by --plugin-dir (nothing to install), Codex through brother_install.py and codex_hooks_install.py --trust, then
    --check. Never the real ~/.codex; a home resolving to the real home is refused. Both shipped hook files are hashed
    before and after, and a change refuses (REQ-HP1-NO-EDIT). Returns the paths and hashes as strings."""
    _need_host(host)
    full = _check_home(home)
    src = _check_source(source)
    if os.path.lexists(full):
        raise HP1Refused("refused: %s already exists; a throwaway home is always fresh" % full)
    before = _shipped_hashes(src)
    lay = _layout(full)
    os.makedirs(full, mode=0o700)
    os.makedirs(lay["workspace"])
    # THE WORKSPACE IS A GIT REPOSITORY (2026-10-04): a user runs a host inside a repository, and Codex refuses any other
    # directory ("Not inside a trusted directory and --skip-git-repo-check was not specified"), so every probe of the
    # first real Codex run exited 1 and recorded no row. The probe stays realistic: the check is met, never skipped.
    try:
        # the init never inherits the caller's git world (review 2026-10-04): no template hooks copied in, no global or
        # system config, and no GIT_DIR or other location variable that would re-initialise some other repository
        import tmp_sandbox
        genv = {k: v for k, v in os.environ.items() if k not in tmp_sandbox.GIT_LOCATION_VARS}
        genv.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
        made = subprocess.run(["git", "init", "-q", "--template=", lay["workspace"]], env=genv, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, timeout=PROC_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError) as exc:
        raise HP1Refused("refused: the workspace %s could not be made a git repository (%s)" % (lay["workspace"], exc))
    if made.returncode != 0:
        raise HP1Refused("refused: git init in %s exited %d: %s" % (
            lay["workspace"], made.returncode, made.stdout.decode("utf-8", "replace").strip()[-200:]))
    os.makedirs(lay["outside"])
    real_python = sys.executable
    out = {"host": host, "home": full, "source": src, "workspace": lay["workspace"], "target": lay["target"],
           "shim_dir": lay["shim_dir"], "shim_path": write_shim(lay["shim_dir"], real_python), "real_python": real_python}
    if host == "antigravity":
        plugins = os.path.join(lay["workspace"], ".agents", "plugins")
        os.makedirs(plugins)
        link = os.path.join(plugins, "brother")
        os.symlink(os.path.join(src, "bundle", ".antigravity-plugin"), link)
        out.update({"plugin_root": link, "hooks_path": os.path.join(link, "hooks.json")})
    elif host == "claude":
        out.update({"plugin_root": os.path.join(src, "bundle"), "hooks_path": os.path.join(src, "bundle", "hooks", "hooks.json")})
    else:
        out.update(_install_codex(lay, src))
    after = _shipped_hashes(src)
    out["shipped_hooks_before"] = json.dumps(before, sort_keys=True)
    out["shipped_hooks_after"] = json.dumps(after, sort_keys=True)
    if after != before:
        raise HP1Refused("REQ-HP1-NO-EDIT: a shipped hook file changed during prepare: %s" % ", ".join(
            rel for rel in SHIPPED_HOOKS if before[rel] != after[rel]))
    return out


def _install_codex(lay, src):
    """The Codex install into the throwaway CODEX_HOME. The credential handoff is codex_battery.setup_home: a symlink to
    the real auth.json that only Codex opens; this driver never reads or prints it, and run_host removes it at the end."""
    import codex_battery   # imported here only: its import writes its own evidence folder, and only the owner run gets here
    codex_home = lay["codex_home"]
    codex_battery.setup_home(codex_home)
    env = dict(os.environ, HOME=lay["home"], CODEX_HOME=codex_home)
    env.pop(LIVE_ENV, None)
    install = os.path.join(src, "scripts", "brother_install.py")
    wire = os.path.join(src, "scripts", "codex_hooks_install.py")
    steps = ([sys.executable, "-B", install, "install", "--codex-home", codex_home, "--marketplace", src, "--ref", "HEAD", "--json"],
             [sys.executable, "-B", wire, "--codex-home", codex_home, "--trust", "--cwd", lay["workspace"]])
    for argv in steps:
        done = subprocess.run(argv, env=env, cwd=src, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=INSTALL_TIMEOUT_S)
        if done.returncode != 0:
            raise HP1Refused("the Codex install step %s failed (exit %d): %s" % (
                os.path.basename(argv[2]), done.returncode, done.stdout.decode("utf-8", "replace")[-400:]))
    check = subprocess.run([sys.executable, "-B", wire, "--codex-home", codex_home, "--check"], env=env, cwd=src,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=INSTALL_TIMEOUT_S)
    last = (check.stdout.decode("utf-8", "replace").strip().splitlines() or ["no output"])[-1]
    if check.returncode != 0:
        raise HP1Refused("codex_hooks_install.py --check did not pass: %s" % last)
    installed = installed_plugin_root(codex_home)
    return {"codex_home": codex_home, "plugin_root": os.path.join(src, "products"),
            "hooks_path": os.path.join(codex_home, "hooks.json"), "install_check": last,
            "auth_link": os.path.join(codex_home, "auth.json"), "plugin_install": installed,
            "plugin_install_sha256": installed_skills_digest(installed)}


def installed_plugin_root(codex_home):
    """The plugin folder Codex installed, measured after the install: the one version folder under
    <codex_home>/plugins/cache/brother/brother/ (1.1.0 in the 2026-10-04 run). None, two or an unreadable cache is
    refused: the verifier reads the door skill load against this exact path, never against a pattern."""
    cache = os.path.join(_need_str(codex_home, "codex_home"), "plugins", "cache", "brother", "brother")
    try:
        versions = sorted(name for name in os.listdir(cache) if os.path.isdir(os.path.join(cache, name)))
    except OSError as exc:
        raise HP1Refused("the Codex plugin install %s cannot be read: %s" % (cache, exc))
    if len(versions) != 1:
        raise HP1Refused("the Codex plugin install %s holds %d version folders, not one: %s" % (
            cache, len(versions), ", ".join(versions) or "none"))
    root = os.path.join(cache, versions[0])
    if os.path.realpath(root) != root:   # a link anywhere on the path could point the install into the workspace
        raise HP1Refused("the Codex plugin install %s passes through a symbolic link: it resolves to %s" % (
            root, os.path.realpath(root)))
    return root


def install_digest(root, shas):
    """sha256 binding an installed plugin root to the sha256 of each door skill under it ({name: sha256}): --run
    records it as plugin_install_sha256 from the installed bytes, the verifier recomputes it from the candidate's
    committed blobs, so a row naming another root, or a root whose skills are not the candidate's, never matches."""
    _need_str(root, "root")
    if not isinstance(shas, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in shas.items()):
        raise HP1Refused("install_digest: shas must map skill names to sha256 strings")
    return _sha256(json.dumps({"root": root, "skills": shas}, sort_keys=True).encode("utf-8"))


def installed_skills_digest(root):
    """install_digest of the door skills as installed under root, read from disk right after the install. A door
    skill that is missing, unreadable or reached through a link is refused."""
    import host_live_verify as verifier   # imported here: the verifier imports this module at its top
    shas = {}
    for name in verifier.DOOR_SKILLS:
        path = os.path.join(_need_str(root, "root"), "skills", name, "SKILL.md")
        if os.path.realpath(path) != path:
            raise HP1Refused("the installed door skill %s passes through a symbolic link" % path)
        try:
            with open(path, "rb") as fh:
                shas[name] = _sha256(fh.read())
        except OSError as exc:
            raise HP1Refused("the installed door skill %s cannot be read: %s" % (path, exc))
    return install_digest(root, shas)


def _host_root(path):
    """The install directory of a host binary: the outermost .app bundle holding it, else the folder above its bin/."""
    full = os.path.realpath(path)
    parts = full.split(os.sep)
    for i, part in enumerate(parts):
        if part.endswith(".app"):
            return os.sep.join(parts[:i + 1])
    return os.path.dirname(os.path.dirname(full))


def _antigravity_bin():
    """(absolute binary, "") from the one Antigravity resolver, resolve_host in tests/e2e/antigravity/run_e2e.py, or
    (None, why). The resolver is loaded from its file; a missing or broken file is NO-DATA, never a guess."""
    import importlib.util
    path = os.path.join(_repo_root(), "tests", "e2e", "antigravity", "run_e2e.py")
    try:
        spec = importlib.util.spec_from_file_location("hp1_run_e2e", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except (OSError, ImportError, SyntaxError, AttributeError) as exc:
        return None, "the Antigravity resolver %s did not load: %s" % (path, exc)
    try:
        found = mod.resolve_host()[0]
    except mod.HostUnresolvedError as exc:
        return None, str(exc)
    return os.path.realpath(found), ""


def _host_bin(host):
    """(binary or None, host_roots, why)."""
    if host == "codex":
        import brother_paths   # the one Codex binary rule
        found = brother_paths.codex_bin()
        ok = os.path.isfile(found) and os.access(found, os.X_OK)
        return (found, [_host_root(found)], "") if ok else (None, [], "no Codex binary at %s" % found)
    if host == "antigravity":
        found, why = _antigravity_bin()
        return (found, [_host_root(found)], "") if found else (None, [], why)
    found = claude.resolve_claude(dict(os.environ))   # HP1.e: HP1_CLAUDE_BIN, CLAUDE_BIN, then the newest install; never PATH
    if found is None:
        return None, [], ("no Claude Code binary: resolve_claude (scripts/host_live_claude.py) found none under "
                          "~/Library/Application Support/Claude/claude-code or %s, and PATH is never read; set %s to the "
                          "binary's absolute path" % (claude.ROOTS_ENV, claude.BIN_ENVS[0]))
    return found, [_host_root(found)], ""


def _session_plan(host, full, src, trace_path, run_id):
    lay = _layout(full)
    calls, roots = _allowlist(src)
    host_bin, host_roots, why = _host_bin(host)
    env = _witness_env(os.environ, calls, roots, os.path.abspath(trace_path), host, run_id, sys.executable,
                       lay["shim_dir"], host_roots)
    env["HOME"] = full
    if host == "codex":
        env["CODEX_HOME"] = lay["codex_home"]
    shown = dict((k, env[k]) for k in sorted(env) if k.startswith("HP1_") or k in ("HOME", "PATH", "CODEX_HOME"))
    plans = []
    for probe, prompt in (("door", DOOR_SENTENCE), ("verb", VERB_SENTENCE),
                          ("guarded_write", GUARDED_WRITE_SENTENCE % lay["target"])):
        if host_bin is None:
            argv = None
        elif host == "codex":
            argv = [host_bin, "exec", "--json", "--ephemeral", "-C", lay["workspace"], prompt]
        elif host == "claude":
            try:   # --plugin-dir is the candidate bundle; claude_argv refuses --bare (it skips hooks) and a flag shaped prompt
                argv = claude.claude_argv(host_bin, os.path.join(src, "bundle"), prompt)
            except claude.ClaudeRefused as exc:
                raise HP1Refused(str(exc))
        else:
            argv = [host_bin, lay["workspace"]]   # the owner types the prompt in the agent panel
        plans.append({"host": host, "probe": probe, "prompt": prompt, "argv": argv, "cwd": lay["workspace"],
                      "env": env, "env_set": shown, "path": env["PATH"], "shim_dir": lay["shim_dir"],
                      "real_python": sys.executable, "run_id": run_id, "trace_path": os.path.abspath(trace_path),
                      "target": lay["target"], "host_bin": host_bin, "host_roots": host_roots, "no_data": why})
    return plans


def _owner_lines(host, plans):
    first = plans[0]
    run_id, target = first["run_id"], first["target"]
    sets = " ".join("%s=%s" % (k, shlex.quote(v)) for k, v in sorted(first["env_set"].items()))
    collect_line = "python3 scripts/host_live_proof.py --collect %s --home %s" % (host, shlex.quote(first["env_set"]["HOME"]))
    if host == "antigravity":
        lines = ["# HP1 Antigravity live proof, run %s: quit Antigravity, then start it from this terminal" % run_id,
                 "export ANTIGRAVITY_BIN=%s" % shlex.quote(first["host_bin"]) if first["host_bin"] else "NO-DATA: " + first["no_data"],
                 "env %s \"$ANTIGRAVITY_BIN\" %s" % (sets, shlex.quote(first["cwd"])),
                 "# in ONE fresh conversation of the agent panel, send each message below, in this order:"]
        lines += ["%s: %s" % (p["probe"], p["prompt"]) for p in plans]
        lines.append("# every message fires PreInvocation and PostInvocation; the write to %s is expected to be allowed "
                     "on Antigravity (owner ruling 2026-09-30)" % target)
        return lines + ["# once the rows have arrived, collect them into the evidence file:", collect_line]
    if host == "codex":
        login = "%s login status" % shlex.quote(first["host_bin"]) if first["host_bin"] else "NO-DATA: " + first["no_data"]
        return ["# HP1 Codex live proof, run %s: sign in once, then run the three probes non interactively" % run_id,
                "env CODEX_HOME=%s %s" % (shlex.quote(first["env_set"]["CODEX_HOME"]), login),
                "HP1_LIVE=1 python3 scripts/host_live_proof.py --run codex --home %s" % shlex.quote(first["env_set"]["HOME"]),
                collect_line]
    lines = ["# HP1 Claude Code live proof, run %s: sign in to Claude Code once, then run the three probes non interactively"
             % run_id,
             "export %s=%s" % (claude.BIN_ENVS[0], shlex.quote(first["host_bin"])) if first["host_bin"] else "NO-DATA: " + first["no_data"],
             "# each probe runs, with the witness first on PATH and never --bare:"]
    lines += ["%s: %s" % (p["probe"], " ".join(shlex.quote(a) for a in p["argv"]) if p["argv"] else "NO-DATA") for p in plans]
    lines.append("HP1_LIVE=1 python3 scripts/host_live_proof.py --run claude --home %s" % shlex.quote(first["env_set"]["HOME"]))
    return lines + [collect_line]


def owner_commands(host, home):
    """The exact lines the owner runs for host, for the throwaway home: the binary named explicitly
    (ANTIGRAVITY_BIN=<absolute path>), the witness first on PATH, and the probe sentences in order. Writes nothing and
    starts nothing; a step the loop cannot run is a NO-DATA line naming it."""
    _need_host(host)
    full = _check_home(home)
    return _owner_lines(host, _session_plan(host, full, _repo_root(), _layout(full)["trace"], os.path.basename(full)))


def wait_for_rows(trace_path, need, wait_s):
    """The whole rows of trace_path once at least need of them are there, or what is there after wait_s seconds (the
    caller reads too few as NO-DATA, never a fallback). A partial last line is still being written and is not read; a
    whole line that is not a JSON object is corrupt and refuses."""
    _need_str(trace_path, "trace_path")
    _need_int(need, "need", 1, 10 ** 6)
    _need_int(wait_s, "wait_s", 0, WAIT_MAX_S)
    deadline = time.monotonic() + wait_s
    while True:
        rows = _read_rows(trace_path)
        left = deadline - time.monotonic()
        if len(rows) >= need or left <= 0:
            return rows
        time.sleep(min(POLL_S, left))


def _read_rows(path):
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise HP1Refused("the trace %s is unreadable: %s" % (path, exc))
    rows = []
    for n, line in enumerate(data.split(b"\n")[:-1], 1):
        if not line.strip():
            continue
        row = payload_of(line)
        if row is None:
            raise HP1Refused("the trace %s line %d is not a JSON object" % (path, n))
        rows.append(row)
    return rows


def _clear(path):
    if os.path.lexists(path):
        os.unlink(path)


def _envelope(folder, name, argv, code, stdout, extra=None):
    body = {"argv": argv, "exit_code": code, "stdout_b64": base64.b64encode(stdout).decode("ascii")}
    body.update(extra or {})
    path = os.path.join(folder, name)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(body, fh, sort_keys=True)
    return path


STDERR_TAIL_BYTES = 2000


def _stderr_field(err, code):
    """The stderr kept in an envelope (review 2026-10-04): nothing on success; on a failure only a bounded tail, and only
    after the shared secret table (scripts/loop/secret_scan.py, loaded the way pre_push_gate loads it) finds no secret
    shape in it; a table that cannot load, or any match, withholds the tail and says why. Host stderr can name the
    signed in account or a failed request, and this envelope is evidence that is read and packaged later."""
    if code == 0 or not err:
        return {}
    tail = err[-STDERR_TAIL_BYTES:].decode("utf-8", "replace")
    try:
        import importlib.util
        path = os.path.join(HERE, "loop", "secret_scan.py")
        spec = importlib.util.spec_from_file_location("secret_scan", path)
        if spec is None or spec.loader is None:
            raise ImportError(path)
        scan = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(scan)
        hits = scan.count(tail)
    except Exception as exc:   # sbe: allow-silent an unloadable scan withholds the tail, it never stores it unscanned
        return {"stderr_withheld": "the secret scan could not load (%s)" % type(exc).__name__}
    if hits:
        return {"stderr_withheld": "%d secret shaped match(es) in the stderr tail" % hits}
    # an address names a person (the signed in account), which is private content: redacted, never stored (review r2)
    tail = re.sub(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "[email redacted]", tail)
    return {"stderr_tail_b64": base64.b64encode(tail.encode("utf-8")).decode("ascii")}


def _run_envelope(argv, plan, timeout):
    """(exit code, stdout, stderr) of one host command. stdin is closed (a host that reads "additional input from stdin"
    never waits on the owner's terminal) and stderr is KEPT: on 2026-10-04 every Codex probe exited 1 with an empty
    stdout and the reason, printed on stderr, was discarded, so the run recorded nothing a reader could act on."""
    try:
        done = subprocess.run(argv, env=plan["env"], cwd=plan["cwd"], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=timeout)
        return done.returncode, done.stdout, done.stderr
    except subprocess.TimeoutExpired as exc:
        return -9, exc.stdout or b"", exc.stderr or b""


def run_host(host, home, source, trace_path, start=False, wait_s=600):
    """The session for host. start False (REQ-HP1-SESSION): build and return the plan, one dict per probe (argv,
    environment, the PATH with the witness first) and start no process. start True runs only with HP1_LIVE=1 in the
    environment: prepare, then Codex runs `codex exec --json --ephemeral -C <workspace>` per probe and keeps each
    envelope beside the trace, Claude Code runs claude_argv per probe (the binary resolve_claude named, --plugin-dir the
    candidate bundle, never --bare) and stores the raw stream beside the trace, Antigravity prints the owner commands
    and waits for the rows. Returns this run's rows."""
    _need_host(host)
    full = _check_home(home)
    src = _check_source(source)
    _need_str(trace_path, "trace_path")
    if not isinstance(start, bool):
        raise HP1Refused("start must be True or False, got %s" % type(start).__name__)
    _need_int(wait_s, "wait_s", 1, WAIT_MAX_S)
    plans = _session_plan(host, full, src, trace_path, os.path.basename(full))
    if not start:
        return plans   # REQ-HP1-SESSION: the plan only, no process
    if os.environ.get(LIVE_ENV) != "1":
        raise HP1Refused("REFUSED: a %s session spends a model call; only the owner command `HP1_LIVE=1 python3 "
                         "scripts/host_live_proof.py --run %s` starts one (HP1_LIVE=1 is not set)" % (host, host))
    if plans[0]["host_bin"] is None:
        raise HP1Refused("NO-DATA: %s" % plans[0]["no_data"])
    prepared = prepare(host, home, source)
    folder = os.path.dirname(plans[0]["trace_path"])
    os.makedirs(folder, exist_ok=True)
    run_id, target = plans[0]["run_id"], plans[0]["target"]
    _clear(target)   # exists_before is false by construction
    write_session(folder, session_record(plans[0], prepared, host_version(plans[0]["host_bin"]),
                                         candidate_binding(prepared["source"])))   # what --collect reads
    deadline = time.monotonic() + wait_s
    if host == "antigravity":
        for line in _owner_lines(host, plans):
            print(line, flush=True)
    else:   # Codex and Claude Code: one non interactive session per probe, each kept as an envelope beside the trace
        outs = []
        try:
            if host == "codex":
                code, out, err = _run_envelope([plans[0]["host_bin"], "login", "status"], plans[0], 60)
                _envelope(folder, "%s-login-status.json" % run_id, [plans[0]["host_bin"], "login", "status"], code, out,
                          _stderr_field(err, code))
            for plan in plans:
                _clear(target)
                code, out, err = _run_envelope(plan["argv"], plan, wait_s)
                effect = {"path": target, "exists_before": False, "exists_after": os.path.lexists(target)}
                extra = _stderr_field(err, code)
                if plan["probe"] == "guarded_write":
                    extra["effect"] = effect
                _envelope(folder, "%s-%s.json" % (run_id, plan["probe"]), plan["argv"], code, out, extra)
                outs.append(out)
        finally:
            link = prepared.get("auth_link", "")
            if link and os.path.islink(link):
                os.unlink(link)
        if host == "claude":   # the host's own account of its hook fires, stored raw beside the trace (HP1.e)
            _write_bytes(claude.claude_stream_path(plans[0]["trace_path"]),
                         b"".join(o if o.endswith(b"\n") else o + b"\n" for o in outs if o))
    need = set(PROBES) | set(EXTRA_PROBES.get(host, ()))
    seen = 0
    while True:
        left = int(max(0, deadline - time.monotonic()))
        every = wait_for_rows(plans[0]["trace_path"], seen + 1, left)   # one more row of any run, or the deadline
        seen = len(every)
        rows = [r for r in every if r.get("run_id") == run_id]
        if need <= {r.get("probe") for r in rows} or left <= 0:
            return rows


def _fresh_home(host):
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return os.path.join(_scratch_root(), "hp1-%s-%s-%d" % (host, stamp, os.getpid()))


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    if not isinstance(argv, list) or not all(isinstance(a, str) for a in argv):
        raise HP1Refused("main: argv must be a list of strings")
    parser = argparse.ArgumentParser(prog="host_live_proof.py", description="HP1 live proof driver (one host at a time).")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--run", choices=HOSTS, help="start the host session (the owner's command; needs HP1_LIVE=1)")
    mode.add_argument("--plan", choices=HOSTS, help="print the session plan as JSON and start nothing")
    mode.add_argument("--print-owner-commands", choices=HOSTS, dest="owner", help="print the owner's exact commands")
    mode.add_argument("--collect", choices=HOSTS, help="collect a finished run's trace rows into the evidence file (needs --home)")
    parser.add_argument("--home", default=None, help="the throwaway home (default: a fresh folder under ~/.claude/brother-scratch)")
    parser.add_argument("--source", default=_repo_root(), help="the candidate checkout (default: this one)")
    parser.add_argument("--trace", default=None, help="the trace file the witness appends to (default: <home>/trace/trace.jsonl)")
    parser.add_argument("--evidence", default=None, help="the evidence file --collect appends to (default: <source>/%s)" % EVIDENCE_REL)
    parser.add_argument("--wait", type=int, default=600, help="seconds to wait for the rows (default 600)")
    args = parser.parse_args(argv)
    host = args.run or args.plan or args.owner or args.collect
    if args.collect and not args.home:
        parser.error("--collect needs --home, the finished run's throwaway home (its name is the run id)")
    home = args.home or _fresh_home(host)
    trace = args.trace or os.path.join(home, "trace", "trace.jsonl")
    try:
        if args.owner:
            print("\n".join(owner_commands(host, home)))
            return 0
        if args.plan:
            plans = run_host(host, home, args.source, trace, False)
            print(json.dumps([dict((k, v) for k, v in p.items() if k != "env") for p in plans], indent=1, sort_keys=True))
            return 0
        if args.collect:
            return collect_and_judge(host, home, args.source, trace, args.evidence or os.path.join(args.source, EVIDENCE_REL))
        rows = run_host(host, home, args.source, trace, True, args.wait)
    except HP1Refused as exc:
        print(str(exc), file=sys.stderr)
        return 2
    import host_live_verify as verifier   # imported here: the verifier imports this module at its top
    ruled = verifier.RULED_NO_DATA.get(host, ())
    missing = missing_probes(host, rows)
    if missing:
        print("NO-DATA: %s run %s recorded %d rows but no %s row%s; trace %s" % (
            host, os.path.basename(home), len(rows), ", ".join(missing), _stream_errors(host, trace), trace))
        return 3
    agreed = ""
    if host == "claude":   # the stored stream must agree with the witness rows, per event name (REQ-HP1-CLAUDE-CROSSCHECK)
        ok, why = claude_agrees(rows, home, args.source, trace)
        if not ok:
            print("NO-DATA: claude run %s recorded %d rows but the host's stream and the witness rows do not agree: %s; "
                  "trace %s" % (os.path.basename(home), len(rows), why, trace))
            return 3
        agreed = ", stream and witness %s" % why
    if ruled:
        agreed += ", %s NO-DATA by %s" % (" and ".join(ruled), verifier.RULED_NO_DATA_REF[host])
    print("RECORDED: %s run %s, %d rows in %s%s" % (host, os.path.basename(home), len(rows), trace, agreed))
    return 0


def missing_probes(host, rows):
    """The probes this host owes a row for that no row of rows is labelled with, sorted. A probe the verifier's
    RULED_NO_DATA names for the host (owner rulings hp1-codex-door 2026-10-04, hp1-claude-door 2026-10-05: door and
    verb on Codex and Claude Code) is owed as the verifier's legs instead, so it is never reported missing here."""
    import host_live_verify as verifier   # imported here: the verifier imports this module at its top
    owed = set(PROBES) | set(EXTRA_PROBES.get(_need_host(host), ()))
    return sorted(owed - {r.get("probe") for r in rows if isinstance(r, dict)} - set(verifier.RULED_NO_DATA.get(host, ())))


def _stream_errors(host, trace_path):
    """', the host's stream says: ...' naming the error results of a Claude Code stream (a session that was not signed
    in ran no turn, so no prompt or tool hook could fire), or '' (another host, no stream, no error result)."""
    if host != "claude":
        return ""
    try:
        with open(claude.claude_stream_path(trace_path), "rb") as fh:
            errors = claude.stream_errors(fh.read())
    except (OSError, claude.ClaudeRefused):
        return ""
    return ", the host's stream says: %s" % "; ".join(sorted(set(errors))) if errors else ""


def claude_agrees(rows, home, source, trace_path):
    """(ok, why): HP1.e's crosscheck of this run's witness rows against the stream stored beside the trace, re-parsed
    from its bytes. A stream that cannot be read is (False, "NO-DATA: ..."), never agreement."""
    if not isinstance(rows, list):
        raise HP1Refused("rows must be a list")
    try:
        events = claude.run_claude_session(os.path.realpath(os.path.expanduser(_need_str(home, "home"))),
                                           _check_source(source), _need_str(trace_path, "trace_path"), WAIT_MAX_S)
    except claude.ClaudeRefused as exc:
        return False, str(exc) if str(exc).startswith("NO-DATA") else "NO-DATA: %s" % exc
    return claude.crosscheck(rows, events)


# -- the collector: trace rows to hp1.v1 evidence (HP1.f) -------------------------------------------------------------

def _now_stamp():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _short(value):
    return repr(value)[:60]


def host_version(host_bin):
    """The verbatim first line of `<host_bin> --version`, or "no_data" when the binary is not an absolute executable
    file, fails, times out or prints nothing. A version the binary did not print is never invented: the collector
    refuses a no_data version, so the row is NO-DATA rather than a row naming a version nobody saw."""
    _need_str(host_bin, "host_bin")
    if not os.path.isabs(host_bin) or not (os.path.isfile(host_bin) and os.access(host_bin, os.X_OK)):
        return "no_data"
    try:
        done = subprocess.run([host_bin, "--version"], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, timeout=VERSION_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError):
        return "no_data"
    lines = [line.strip() for line in done.stdout.decode("utf-8", "replace").splitlines() if line.strip()]
    return lines[0] if done.returncode == 0 and lines else "no_data"


def candidate_binding(source):
    """{plugin_tree_sha256, tool_sha256} of the candidate checkout at source, the same facts host_live_verify's
    candidate_facts computes, read at run time so the row is bound to the code the throwaway install actually held.
    A candidate that cannot be read gives "no_data" for both (never invented), which the collector refuses."""
    _need_str(source, "source")
    import host_live_verify as verifier   # imported here: the verifier imports this module at its top
    try:
        facts = verifier.candidate_facts(source)
    except (verifier.VerifyNoData, verifier.VerifyRefused):
        return dict((key, "no_data") for key in CANDIDATE_KEYS)
    return dict((key, facts[key]) for key in CANDIDATE_KEYS)


def session_record(plan, prepared, version, binding):
    """The facts of one run that `--collect` reads back: the host binary and its version, the throwaway paths, the
    installed hook file, the guarded write target and the candidate binding (plugin_tree_sha256, tool_sha256 of the
    checkout installed), recorded at run time by `--run` and never re-resolved later (when PATH, the installed
    binaries, the environment or the checkout may differ). plan is one dict of run_host's plan, prepared the dict
    prepare returned, version what host_version printed, binding what candidate_binding read."""
    if not isinstance(plan, dict) or not isinstance(prepared, dict):
        raise HP1Refused("session_record: plan and prepared must be dicts")
    _need_str(version, "version")
    if not isinstance(binding, dict) or any(key not in binding for key in CANDIDATE_KEYS):
        raise HP1Refused("session_record: binding must carry %s" % ", ".join(CANDIDATE_KEYS))
    try:
        host_bin = plan["host_bin"]
        record = {"schema": SESSION_SCHEMA, "host": _need_host(plan["host"]), "run_id": _need_str(plan["run_id"], "run_id"),
                  "home": prepared["home"], "source": prepared["source"], "host_bin": host_bin,
                  "host_bin_realpath": os.path.realpath(host_bin) if isinstance(host_bin, str) and host_bin else "no_data",
                  "host_version": version, "host_roots": list(plan["host_roots"]), "shim_dir": plan["shim_dir"],
                  "shim_path": prepared["shim_path"], "real_python": plan["real_python"],
                  "plugin_root": prepared["plugin_root"], "hooks_path": prepared["hooks_path"], "target": plan["target"],
                  "workspace": prepared["workspace"], "trace_path": plan["trace_path"], "exists_before": False,
                  "plugin_tree_sha256": binding["plugin_tree_sha256"], "tool_sha256": binding["tool_sha256"],
                  "recorded_at": _now_stamp()}
    except (KeyError, TypeError) as exc:
        raise HP1Refused("session_record: the plan or the prepared dict lacks %s" % exc)
    for key in INSTALL_KEYS:   # Codex only: the measured install the door skill load is read against, and its digest
        if key in prepared:
            record[key] = prepared[key]
    return record


def write_session(folder, record):
    """Write record as <folder>/<run_id>-session.json (replaced whole, never appended) and return its path. A record
    that is not a complete session record, a missing folder or a write failure refuses: a run whose record cannot be
    written could never be collected, so it stops before the host session starts."""
    _need_str(folder, "folder")
    if not isinstance(record, dict) or record.get("schema") != SESSION_SCHEMA \
            or any(key not in record for key in SESSION_KEYS) or not isinstance(record["run_id"], str):
        raise HP1Refused("write_session: record must be a %s record carrying %s" % (SESSION_SCHEMA, ", ".join(SESSION_KEYS)))
    if not os.path.isdir(folder):
        raise HP1Refused("write_session: %s is not a folder" % folder)
    path = os.path.join(folder, record["run_id"] + SESSION_SUFFIX)
    try:
        _write_bytes(path, (json.dumps(record, sort_keys=True, allow_nan=False) + "\n").encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise HP1Refused("write_session: the record is not JSON: %s" % exc)
    except OSError as exc:
        raise HP1Refused("write_session: cannot write %s: %s" % (path, exc))
    return path


def _read_session(folder, run_id):
    """(record, "") for this run's session record beside the trace, else (None, why): missing, unreadable, not a
    session record, a key short, or naming another run."""
    path = os.path.join(folder, run_id + SESSION_SUFFIX)
    try:
        with open(path, "rb") as fh:
            doc = json.loads(fh.read().decode("utf-8"))
    except FileNotFoundError:
        return None, "no session record %s: `--run` writes it when the host session starts" % path
    except (OSError, UnicodeDecodeError, ValueError, RecursionError) as exc:
        return None, "the session record %s is unreadable: %s" % (path, exc)
    if not isinstance(doc, dict) or doc.get("schema") != SESSION_SCHEMA:
        return None, "the session record %s is not a %s record" % (path, SESSION_SCHEMA)
    missing = [key for key in SESSION_KEYS if key not in doc]
    if missing:
        return None, "the session record %s lacks %s" % (path, ", ".join(missing))
    if doc["run_id"] != run_id or doc.get("host") not in HOSTS:
        return None, "the session record %s names run %s on host %s" % (path, _short(doc["run_id"]), _short(doc.get("host")))
    return doc, ""


def _trace_raw(folder, row, name):
    """(bytes, "", False) for the witness's raw_in or raw_out file beside the trace, re-hashed against the sha256 the
    trace row names, which is also the file's name; (None, why, red) when missing or unreadable (NO-DATA) or when
    the bytes do not hash to that name or are not the length recorded (RED: altered after the witness wrote them)."""
    rel, digest, length = row.get(name + "_path"), row.get(name + "_sha256"), row.get(name + "_len")
    if not isinstance(rel, str) or not rel or os.path.basename(rel) != rel or not isinstance(digest, str):
        return None, "%s_path %s is not a file name beside the trace with a sha256" % (name, _short(rel)), True
    try:
        with open(os.path.join(folder, rel), "rb") as fh:
            data = fh.read()
    except FileNotFoundError:
        return None, "the %s file %s is missing beside the trace" % (name, rel), False
    except OSError as exc:
        return None, "the %s file %s is unreadable: %s" % (name, rel, exc), False
    if rel != digest + ".raw" or _sha256(data) != digest:
        return None, ("the %s file %s does not hash to its sha256 name: the raw bytes were altered after the witness "
                      "wrote them" % (name, rel)), True
    if isinstance(length, bool) or not isinstance(length, int) or length != len(data):
        return None, "the %s file %s holds %d bytes, not the %s the witness recorded" % (name, rel, len(data), _short(length)), True
    return data, "", False


def _envelope_doc(folder, name):
    """(doc, bytes, "") for the envelope `--run` wrote beside the trace, else (None, None, why)."""
    try:
        with open(os.path.join(folder, name), "rb") as fh:
            data = fh.read()
    except OSError as exc:
        return None, None, "no %s envelope beside the trace: %s" % (name, exc)
    try:
        doc = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        doc = None
    argv = doc.get("argv") if isinstance(doc, dict) else None
    if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv) \
            or isinstance(doc.get("exit_code"), bool) or not isinstance(doc.get("exit_code"), int) \
            or not isinstance(doc.get("stdout_b64"), str):
        return None, None, "the envelope %s is not {argv, exit_code, stdout_b64}" % name
    return doc, data, ""


def _claude_stream(session, trace_path, rows):
    """(stream bytes, "") when the host's stored stream re-parsed by HP1.e agrees with this run's witness rows, else
    (None, why): the stream absent or empty, or the counts per event name differing (REQ-HP1-CLAUDE-CROSSCHECK)."""
    try:
        events = claude.run_claude_session(session["home"], session["source"], trace_path, WAIT_MAX_S)
    except claude.ClaudeRefused as exc:
        return None, str(exc)
    ok, why = claude.crosscheck([dict(r) for r in rows], events)
    if not ok:
        return None, "the host's stream and the witness rows do not agree: %s" % why
    try:
        with open(claude.claude_stream_path(trace_path), "rb") as fh:
            return fh.read(), ""
    except OSError as exc:
        return None, "the stream is unreadable: %s" % exc


def _complete(row, host):
    """The first hp1.v1 field the row lacks, mistypes or reads no_data in (host_live_verify.REQUIRED_KEYS, less the
    raw_* keys and truncated, which write_row derives from the bytes it stores), then the per host envelopes, the
    guarded write effect and the Claude Code stream; '' when every field is derived. verdict alone may read no_data:
    a hook whose answer no rule parsed is a recorded fact, not a field the collector failed to derive."""
    import host_live_verify as verifier   # imported here: the verifier imports this module at its top
    for key, kind in verifier.REQUIRED_KEYS:
        if key.startswith("raw_") or key == "truncated":
            continue
        if key not in row:
            return "no %s could be derived" % key
        value = row[key]
        if not isinstance(value, kind) or (kind is int and isinstance(value, bool)):
            return "%s is a %s, not a %s" % (key, type(value).__name__, kind.__name__)
        if kind is str and key != "verdict" and (not value.strip() or value == "no_data"):
            return "%s reads %s: not derived from any recorded fact" % (key, _short(value))
    needed = list(HOST_ENVELOPES[host]) if row["probe"] in PROBES else []
    if row["probe"] == "guarded_write":
        needed.append("effect")
    if host == "claude":
        needed += ["stream_path", "stream_sha256"]
    for key in needed:
        if key not in row:
            return "no %s could be derived" % key
    return ""


def _evidence_row(row, session, facts, folder, stream, copies):
    """(evidence row, raw_in, raw_out, "", False) for one witness row of the session's run, every field read from a
    recorded fact; (None, None, None, why, red) when one cannot be (NO-DATA) or the bytes were altered (RED). Files to
    copy beside the evidence (envelopes, the shim, the stream) go into copies as {name: bytes}, written by collect
    only when the whole run is clean."""
    raw_in, why, red = _trace_raw(folder, row, "raw_in")
    if why:
        return None, None, None, why, red
    raw_out, why, red = _trace_raw(folder, row, "raw_out")
    if why:
        return None, None, None, why, red
    if row.get("truncated") is not False:
        return None, None, None, "the raw bytes are truncated at %d bytes, so the full payload is not on disk" % RAW_CAP, False
    out = {"schema": EVIDENCE_SCHEMA}
    for key in TRACE_KEYS:
        if key not in row:
            return None, None, None, "the witness row has no %s" % key, False
        out[key] = row[key]
    event = row["event"] if isinstance(row["event"], str) and row["event"] else None
    out["probe"] = probe_of(raw_in, event=event)   # the one labeller: the witness's own label is never copied
    host, run_id = session["host"], session["run_id"]
    expected = facts["hooks_expected_sha256"].get(host)
    if expected is None:
        return None, None, None, "the candidate holds no %s hook file to expect" % host, False
    try:
        hooks_sha = _sha256_path(session["hooks_path"])
    except (OSError, TypeError, ValueError) as exc:
        return None, None, None, "the installed hook file %s the host read is gone: %s" % (_short(session["hooks_path"]), exc), False
    shim_dir = row["shim_dir"]
    if not isinstance(shim_dir, str) or not shim_dir:
        return None, None, None, "the witness row names no shim_dir", False
    try:
        with open(os.path.join(shim_dir, "python3"), "rb") as fh:
            shim = fh.read()
    except OSError as exc:
        return None, None, None, "the shim the host ran is gone: %s" % exc, False
    shim_name = run_id + SHIM_SUFFIX
    copies[shim_name] = shim
    out.update({"host_bin_realpath": session["host_bin_realpath"], "host_version": session["host_version"],
                "hooks_path": session["hooks_path"], "hooks_sha256": hooks_sha, "hooks_expected_sha256": expected,
                "plugin_tree_sha256": facts["plugin_tree_sha256"], "tree_clean": facts["tree_clean"],
                "tool_sha256": dict(facts["tool_sha256"]), "shim_path": shim_name, "shim_sha256": _sha256(shim)})
    if out["probe"] in PROBES:
        for key in HOST_ENVELOPES[host]:
            name = run_id + (LOGIN_SUFFIX if key == "signed_in" else "-%s.json" % out["probe"])
            doc, data, why = _envelope_doc(folder, name)
            if why:
                return None, None, None, why, False
            copies[name] = data
            out[key] = {"argv": doc["argv"], "envelope_path": name, "envelope_sha256": _sha256(data)}
            if key == "model_turn" and out["probe"] == "guarded_write":
                if not isinstance(doc.get("effect"), dict):
                    return None, None, None, "the guarded_write envelope %s records no effect" % name, False
                out["effect"] = doc["effect"]
        if out["probe"] == "guarded_write" and host == "antigravity":   # no envelope: the owner drove the panel
            target = session["target"]
            if not isinstance(target, str) or not target:
                return None, None, None, "the session record names no guarded write target", False
            out["effect"] = {"path": target, "exists_before": session["exists_before"], "exists_after": os.path.lexists(target)}
    import host_live_verify as verifier   # imported here: the verifier imports this module at its top
    if out["event"] == "SessionStart" and "door" in verifier.RULED_NO_DATA.get(host, ()):
        # Owner rulings hp1-codex-door (2026-10-04) and hp1-claude-door (2026-10-05): no door row exists on this host,
        # so the SessionStart row it owes
        # instead carries the door run's transcript, hash bound. Absent or malformed, nothing is attached and the
        # verifier reads NO-DATA: the collector never decides whether the door answered.
        for key in INSTALL_KEYS:   # the install --run measured and its digest; absent, the verifier reads NO-DATA
            if key in session:
                out[key] = session[key]
        name = run_id + "-door.json"
        doc, data, why = _envelope_doc(folder, name)
        if not why:
            copies[name] = data
            out["door_turn"] = {"argv": doc["argv"], "envelope_path": name, "envelope_sha256": _sha256(data)}
    if host == "claude":
        stream_name = run_id + STREAM_SUFFIX
        copies[stream_name] = stream
        out.update({"stream_path": stream_name, "stream_sha256": _sha256(stream)})
    why = _complete(out, host)
    if why:
        return None, None, None, why, False
    return out, raw_in, raw_out, "", False


def collect(trace_path, run_id, evidence_path, root, host=None):
    """Append the hp1.v1 evidence rows of run run_id to evidence_path, built from the witness's trace at trace_path,
    the session record and envelopes beside it, the installed hook file and the candidate checkout at root. Reads
    only this run's rows (another run's are skipped and counted, never written), re-hashes every raw file against its
    sha256 name, labels probes with probe_of, and writes through write_row. Returns (code, line): 0 every row of the
    run written; 1 RED (a raw file that does not hash to its name, a trace line that is not a witness row, a row of
    another host than the session's: nothing of the run is written); 3 NO-DATA (no session record, no row of this
    run, the candidate unreadable, the candidate's plugin_tree_sha256 or tool_sha256 no longer what the session record
    read at run time, the Claude Code stream absent or disagreeing, or a row with a field that cannot be derived: that
    row is not written, the complete ones are). host, when given, must be the session's.

    INTEGRITY, NOT AUTHENTICITY: the sha256 named raw files, the session record and the hashes prove the bytes were not
    altered after they were written; they do not prove who wrote them. A process under the same account can write trace
    rows, matching raw files and a session.json. What makes a row trustworthy is that the owner's run produced it (the
    HP1_LIVE seam of `--run` and the owner's own hand), never this collector (spec section 13, question 4)."""
    _need_str(trace_path, "trace_path")
    _need_str(run_id, "run_id")
    _need_str(evidence_path, "evidence_path")
    _need_str(root, "root")
    if host is not None:
        _need_host(host)
    import host_live_verify as verifier   # imported here: the verifier imports this module at its top
    folder = os.path.dirname(os.path.abspath(trace_path))
    session, why = _read_session(folder, run_id)
    if why:
        return 3, "NO-DATA: run %s: %s" % (run_id, why)
    if host is not None and session["host"] != host:
        return 1, "RED: run %s was a %s session, not %s" % (run_id, session["host"], host)
    try:
        every = _read_rows(trace_path)
    except HP1Refused as exc:
        return 1, "RED: %s" % exc
    mine, skipped = [], 0
    for n, row in enumerate(every, 1):
        if row.get("run_id") != run_id:
            skipped += 1   # another run: never collected under this run id
            continue
        if row.get("schema") != TRACE_SCHEMA:
            return 1, "RED: trace line %d of run %s is not a %s witness row" % (n, run_id, TRACE_SCHEMA)
        if row.get("host") != session["host"]:
            return 1, "RED: trace line %d of run %s names host %s, the session was %s" % (n, run_id, _short(row.get("host")), session["host"])
        mine.append((n, row))
    if not mine:
        return 3, "NO-DATA: no row of run %s in %s (%d row(s) of other runs skipped)" % (run_id, trace_path, skipped)
    try:
        facts = verifier.candidate_facts(root)
    except (verifier.VerifyNoData, verifier.VerifyRefused) as exc:
        return 3, "NO-DATA: run %s: the candidate at %s cannot be read: %s" % (run_id, root, exc)
    for key in CANDIDATE_KEYS:   # the run exercised the checkout it installed, not whatever the checkout holds now
        if session[key] != facts[key]:
            return 3, ("NO-DATA: run %s was recorded at %s %s and the candidate at %s now reads %s: the rows would bind "
                       "to code the run never exercised; nothing is written" % (
                           run_id, key, _short(session[key]), root, _short(facts[key])))
    stream = None
    if session["host"] == "claude":
        stream, why = _claude_stream(session, trace_path, [row for _n, row in mine])
        if why:
            return 3, "NO-DATA: run %s: %s" % (run_id, why)
    built, no_data, copies = [], [], {}
    for n, row in mine:
        out, raw_in, raw_out, why, red = _evidence_row(row, session, facts, folder, stream, copies)
        if red:
            return 1, "RED: run %s trace line %d: %s; nothing of the run is written" % (run_id, n, why)
        if why:
            no_data.append("line %d: %s" % (n, why))
            continue
        built.append((out, raw_in, raw_out))
    if not built:
        return 3, "NO-DATA: run %s: no row could be completed: %s" % (run_id, "; ".join(no_data))
    try:
        os.makedirs(os.path.dirname(os.path.abspath(evidence_path)), exist_ok=True)
        for name in sorted(copies):
            _write_bytes(os.path.join(os.path.dirname(os.path.abspath(evidence_path)), name), copies[name])
        for out, raw_in, raw_out in built:
            write_row(evidence_path, out, raw_in, raw_out)
        write_manifest(evidence_path)
    except (OSError, HP1Refused) as exc:
        return 3, "NO-DATA: run %s: the evidence could not be written to %s: %s" % (run_id, evidence_path, exc)
    probes = sorted(set(out["probe"] for out, _i, _o in built))
    line = "COLLECTED: %s run %s: %d row(s) appended to %s (probes %s)" % (
        session["host"], run_id, len(built), evidence_path, ", ".join(probes))
    if skipped:
        line += "; skipped %d row(s) of other runs" % skipped
    if no_data:
        return 3, "NO-DATA: %s; %d row(s) not written: %s" % (line, len(no_data), "; ".join(no_data))
    return 0, line


def collect_and_judge(host, home, source, trace_path, evidence_path):
    """The owner's `--collect`: collect the finished run in the throwaway home (its name is the run id), print the
    collector's line, then, when every row was written, the verifier's line under the 1.1.0 scope (Claude Code and
    Codex decide; Antigravity is reported NO-DATA, experimental and unverified, and never decides). Returns the exit
    code: the collector's when not 0, else the verifier's (0 GREEN, 1 RED, 3 NO-DATA)."""
    _need_host(host)
    full = _check_home(home)
    _need_str(source, "source")
    _need_str(trace_path, "trace_path")
    _need_str(evidence_path, "evidence_path")
    import host_live_verify as verifier   # imported here: the verifier imports this module at its top
    code, line = collect(trace_path, os.path.basename(full), evidence_path, source, host)
    print(line, flush=True)
    if code != 0:
        return code
    return verifier.verify(evidence_path, source, int(time.time()), verifier.DECIDING_HOSTS_1_1_0)[0]


if __name__ == "__main__":
    sys.exit(main())
