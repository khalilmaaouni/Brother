#!/usr/bin/env python3
"""host_hook_trace.py: the witness on the hook boundary a host crosses (HP1.a, spec docs/plan/specs/HP1.md section 5.1).

host_live_proof.py writes a two line `python3` shim that execs this file with the original arguments and puts it first
on the host's PATH, so every `python3 ...` the host, its hook guard or the model starts arrives here. witness_call decides
from argv alone whether the call is a shipped hook fire: the script, resolved to an absolute path, EQUALS (never ends
with) a path in HP1_HOOK_SCRIPTS, and its trailing arguments equal the mode a shipped command carries (HP1_HOOK_CALLS).
A call that is not one (python3 -c, the hook_guard.py wrapper itself, any other script) is exec'd straight through with
its stdin untouched and unread. A hook fire runs as a child (the real interpreter by absolute path, stdin re-fed byte for
byte, stdout and stderr re-emitted byte for byte), one row is appended to HP1_TRACE with a single os.write, and the
child's exit code is returned unchanged; a child killed at its definition's timeout is exit code -9, verdict no_data.

FAILURE DIRECTION: recording fails OPEN. A row that cannot be written is a warning on stderr and the hook's own bytes and
exit code still pass through, because a witness that blocks a hook changes what it measures. A missing or corrupt
allowlist matches nothing, so no row arrives and the verifier reads NO-DATA, never a pass.

usage: host_hook_trace.py <script> [args...]   (reached through the shim; with no arguments it prints this and exits 2)
Python 3.9 and 3.13, standard library only.
"""
import datetime
import hashlib
import json
import os
import signal
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import host_live_proof as proof  # noqa: E402  the one VERDICT_RULES, probe_of, parent_chain and write_row

USAGE = b"host_hook_trace: usage: host_hook_trace.py <script> [args...] (reached through the python3 shim of host_live_proof.py)\n"
KILLED = -9   # the exit code a child killed at its timeout reports (SIGKILL)


def _emit(fd, data):
    """Write all of data to fd; a reader that went away ends the write, never the witness."""
    while data:
        try:
            data = data[os.write(fd, data):]
        except OSError:
            return


def _read_all(fd):
    chunks = []
    while True:
        try:
            chunk = os.read(fd, 65536)
        except OSError:
            break
        if not chunk:
            break
        chunks.append(chunk)
    return b"".join(chunks)


def _file_sha256(path):
    try:
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except (OSError, ValueError):
        return "no_data"


def _real_python(env):
    """The interpreter the shim named (HP1_REAL_PYTHON) when it is an absolute executable, else this one: either way a
    real interpreter by absolute path, never `python3` through PATH (which is the shim)."""
    named = env.get(proof.PYTHON_ENV, "")
    if os.path.isabs(named) and os.path.isfile(named) and os.access(named, os.X_OK):
        return named
    return sys.executable


def _decide(argv, env):
    """(script, definitions): the absolute script this call names and the shipped hook definitions it fires, from argv
    and the allowlist alone, before any stdin is read. An empty list means the call is not a shipped hook fire."""
    first = argv[0]
    if not first or first.startswith("-"):
        return "", []   # interpreter options, -c and -m: never a shipped hook command
    try:
        full = os.path.realpath(os.path.join(os.getcwd(), first))
    except OSError:
        return "", []
    allowed = [p for p in env.get(proof.SCRIPTS_ENV, "").split(os.pathsep) if p]
    hit = next((p for p in allowed if p == full), None)   # the full resolved path; a basename or suffix is never a match
    if hit is None:
        return full, []
    try:
        calls = json.loads(env.get(proof.CALLS_ENV) or "[]")
    except (ValueError, RecursionError):
        return full, []
    if not isinstance(calls, list):
        return full, []
    return hit, [c for c in calls if isinstance(c, dict) and c.get("script") == hit and c.get("args") == argv[1:]]


def _pass_through(real, argv, env):
    """exec the call unchanged: the same process, the same stdin, never read here."""
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)   # what subprocess restores for a child, so the call starts as it would have
    signal.signal(signal.SIGXFSZ, signal.SIG_DFL)
    try:
        os.execve(real, [real] + argv, env)
    except OSError as exc:
        _emit(2, ("host_hook_trace: cannot run %s: %s\n" % (real, exc)).encode("utf-8", "replace"))
        return 127


def _timeout_of(defs):
    values = [d.get("timeout") for d in defs]
    values = [v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0]
    return max(values) if values else None


def _plugin_root(script, env):
    roots = [r for r in env.get(proof.ROOTS_ENV, "").split(os.pathsep) if r and script.startswith(r + os.sep)]
    return max(roots, key=len) if roots else "no_data"


def _record(trace_path, env, argv, script, script_sha, defs, raw_in, out, err, code, timed_out, real):
    """None when the row was appended, else the warning line for stderr. Recording fails open: whatever goes wrong here,
    the hook's own answer still reaches the host, so every failure becomes the warning (spec 5.1)."""
    try:
        payload = proof.payload_of(raw_in)
        named = payload.get("hook_event_name") if payload else None
        events = sorted(set(str(d.get("event")) for d in defs))
        event = named if isinstance(named, str) and named else events[0] if len(events) == 1 else "no_data"
        if payload is None:
            verdict, source = "no_data", "stdin_not_object"
        elif timed_out:
            verdict, source = "no_data", "timeout"
        else:
            verdict, source = proof.parse_verdict(out, code)
        try:
            chain, chain_error = proof.parent_chain(os.getpid()), ""
        except (proof.ChainUnreadable, ValueError) as exc:
            chain, chain_error = [], str(exc)
        row = {
            "schema": proof.TRACE_SCHEMA,
            "host": env.get(proof.HOST_ENV) or "no_data",
            "run_id": env.get(proof.RUN_ENV) or "no_data",
            "event": event,
            "probe": proof.probe_of(raw_in, event=event),
            "hook_command": list(argv),
            "script": script,
            "script_sha256": script_sha,
            "plugin_root": _plugin_root(script, env),
            "exit_code": code,
            "verdict": verdict,
            "verdict_source": source,
            "stderr_len": len(err),
            "stderr_sha256": hashlib.sha256(err).hexdigest(),
            "parent_chain": chain,
            "parent_chain_error": chain_error,
            "host_roots": [r for r in env.get(proof.HOST_ROOTS_ENV, "").split(os.pathsep) if r],
            "shim_dir": env.get(proof.SHIM_ENV, ""),
            "recorded_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "real_python": real,
            "witness_sha256": _file_sha256(os.path.abspath(__file__)),
            "witness_argv": list(sys.argv),
            "cwd": os.getcwd(),
        }
        if not trace_path:
            raise proof.HP1Refused("no trace path: %s is not set" % proof.TRACE_ENV)
        proof.write_row(trace_path, row, raw_in, out)
        return None
    except Exception as exc:  # fail open, never a changed verdict: the reason is printed, the hook's bytes still pass
        return ("host_hook_trace: WARNING: no row recorded (%s: %s); the hook's own answer passed through unchanged\n"
                % (type(exc).__name__, exc)).encode("utf-8", "replace")


def witness_call(argv, stdin_fd, trace_path, env):
    """The whole of the witness's work for one `python3 <argv>` call. Returns the exit code the host must see.

    argv: the arguments after `python3`; stdin_fd: the call's stdin; trace_path: the trace to append to (HP1_TRACE);
    env: the call's environment. A call that is not a shipped hook fire is exec'd with stdin_fd untouched and unread."""
    if not isinstance(argv, list) or not all(isinstance(a, str) for a in argv):
        raise proof.HP1Refused("witness_call: argv must be a list of strings")
    if isinstance(stdin_fd, bool) or not isinstance(stdin_fd, int) or stdin_fd < 0:
        raise proof.HP1Refused("witness_call: stdin_fd must be a file descriptor number")
    if not isinstance(trace_path, str):
        raise proof.HP1Refused("witness_call: trace_path must be a string")
    if not isinstance(env, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in env.items()):
        raise proof.HP1Refused("witness_call: env must map strings to strings")
    if not argv:
        _emit(2, USAGE)
        return 2
    real = _real_python(env)
    script, defs = _decide(argv, env)
    if not defs:   # not a shipped hook fire: exec'd straight through, nothing recorded
        return _pass_through(real, argv, env)
    raw_in = _read_all(stdin_fd)
    script_sha = _file_sha256(script)   # the bytes about to run, read now: never the committed blob
    timed_out = False
    try:
        done = subprocess.run([real] + argv, input=raw_in, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
                              timeout=_timeout_of(defs))
        code, out, err = done.returncode, done.stdout, done.stderr
    except subprocess.TimeoutExpired as exc:
        code, out, err, timed_out = KILLED, exc.stdout or b"", exc.stderr or b"", True
    except OSError as exc:
        code, out, err = 127, b"", ("host_hook_trace: cannot run %s: %s\n" % (real, exc)).encode("utf-8", "replace")
    warning = _record(trace_path, env, argv, script, script_sha, defs, raw_in, out, err, code, timed_out, real)
    _emit(1, out)
    _emit(2, err)
    if warning:
        _emit(2, warning)
    return code   # the child's own exit code, unchanged


def _exit_like(code):
    """Leave the way the child left: a child killed by a signal kills the witness by the same signal."""
    if code < 0:
        try:
            signal.signal(-code, signal.SIG_DFL)
        except (OSError, ValueError, RuntimeError):
            pass
        os.kill(os.getpid(), -code)
        code = 128 - code   # a signal whose default is to ignore: the shell's convention instead
    sys.exit(code)


def main():
    env = dict(os.environ)
    return witness_call(sys.argv[1:], 0, env.get(proof.TRACE_ENV, ""), env)


if __name__ == "__main__":
    _exit_like(main())
