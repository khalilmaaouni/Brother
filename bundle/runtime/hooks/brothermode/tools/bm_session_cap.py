#!/usr/bin/env python3
"""tools/bm_session_cap.py: the machine-wide cap on concurrent sessions.

A PreToolUse hook. It refuses ONE thing: starting another Claude Code
session when too many are already running. Everything else passes through
untouched.

WHY THIS EXISTS
  On 2026-08-08 this project's own caps were prose. SKILL.md said three
  fences per shared tree and nine agents; the machine ran fifteen sessions
  at once and forty across ten hours, spending roughly 6.4 million output
  tokens and 1.69 billion cache-read tokens while the founder was away from
  the screen. Nothing counted. Nothing could have refused.

  The lesson is narrower than "add limits". The limits already existed. The
  contract signed that morning carried a token ceiling of 3,000,000 and
  tools/bm_controller.py compares spend against it correctly, but
  autonomy_spend held zero rows all night, so the comparison read 0 against
  3,000,000 forever. A control that depends on a meter fails exactly when
  the meter is what broke. So this file measures the one thing that cannot
  be under-reported by a bookkeeping gap: how many session transcripts are
  being written to right now.

WHAT IT GATES, AND WHY ONLY THAT
  A session spawn, and nothing else. Concretely: a Bash command that runs
  `claude -p` or `claude --print`. That is the surface the 2026-08-08 relay
  used (tools/bm_continue.py's printable_command prints exactly this shape)
  and it is the one that multiplies, because each spawned session can spawn
  more. Subagents dispatched inside one session are NOT gated here: they
  die with their parent, they are the sanctioned delegation path, and a cap
  that fought normal delegation would be switched off inside a day. That is
  a deliberate scope decision, not an oversight; it is stated in
  docs/KNOWN-LIMITS.md terms right here so nobody reads this file as
  broader protection than it is.

WHAT "LIVE" MEANS, HONESTLY
  A session is counted as live when its transcript file under the Claude
  Code projects directory has been written to inside RECENCY_SECONDS. That
  is a PROXY, not a fact: a session idling longer than the window reads as
  finished, and a crashed session reads as finished only after the window
  passes. It was chosen over inspecting the process table because it needs
  no subprocess, costs one directory walk, and cannot be fooled by a
  session whose process name differs across install methods. A proxy that
  runs on every launch beats a truth that is too expensive to consult.

FAIL OPEN ON ITS OWN FAILURE, NEVER ON ITS OWN VERDICT
  If the counter cannot read the projects directory, it warns on stderr and
  ALLOWS: a brake that bricks a founder's machine when it breaks is worse
  than the fleet it was meant to stop, and the same reasoning governs
  tools/bm_fence_hook.py. It does NOT fail open when the count succeeds and
  says the cap is reached. That case is the entire point of the file.

Python 3.9, standard library only. No network, no subprocess.
"""

import json
import os
import re
import sys
import time

#: The cap itself. Four concurrent sessions: enough for a founder's own
#: session plus a relay plus one lane of parallel work, small enough that a
#: self-replicating chain meets it within minutes rather than hours. A
#: THRESHOLD MEASURED ON ONE ESTATE (this machine, 2026-08-09), not a law:
#: raise it deliberately with the environment variable below.
CAP_DEFAULT = 4

#: Environment overrides. The projects root is overridable for tests and
#: for installs that relocate it; nothing else reads it.
CAP_ENV = "BROTHERMODE_SESSION_CAP"
PROJECTS_ENV = "BROTHERMODE_PROJECTS_ROOT"

#: FILE OVERRIDE (2026-08-31, finding E2.1). An uncoached stranger hit the
#: cap and the refusal told them to set BROTHERMODE_SESSION_CAP=N "in the
#: environment of the session doing the launching". That is only true of
#: the process that starts `claude` in the first place (a person at a
#: terminal, before any session exists); it is NOT reachable from inside a
#: session already running, because this hook is a fresh subprocess the
#: harness spawns with the environment the harness itself was given, and
#: nothing a session does afterward (export, a Bash tool call) reaches it.
#: A file is the one thing a session CAN change that a later, separate
#: invocation of this same hook will actually re-read from disk. CAP_ENV
#: still wins when set, unchanged, because it remains the real lever for
#: whoever controls the launch; the file is the lever for whoever is
#: already inside one.
CAP_FILE_ENV = "BROTHERMODE_SESSION_CAP_FILE"
CAP_FILE_DEFAULT = os.path.join("~", ".claude", "bm_session_cap.override")

#: How recently a transcript must have been written for its session to
#: count as live. Ten minutes: longer than any single turn observed on
#: 2026-08-08 (the slowest hour held 1,431 turns across thirteen sessions),
#: short enough that yesterday's forty dead sessions do not lock the
#: founder out of their own machine this morning.
RECENCY_SECONDS = 600

#: The spawn shapes this refuses. Anchored on a word boundary rather than
#: on the start of the line, because every real launch the relay prints is
#: wrapped: `nohup claude -p '...' > log 2>&1 &`, often after a `cd`.
#: Widened 2026-08-09 after the adversarial review of PR 36 executed two
#: bypasses against the narrow version: `/usr/local/bin/claude -p` (a path
#: prefix) and `claude --dangerously-skip-permissions -p` (a flag between
#: the binary and -p) both passed a matcher that demanded exact adjacency.
#: The widened shape allows an optional slash-terminated path before the
#: word claude and any run of flag-shaped tokens between it and -p/--print,
#: while a separator is still required in front, so prose words that merely
#: END in claude (notes-about-claude, myclaude) stay unmatched.
#: The re-verification then executed one more bypass against the widened
#: shape: `claude --permission-mode acceptEdits -p`, our own shipped flag
#: reordered, because a flag whose VALUE rides as the next token broke the
#: flag run. Each flag may now be followed by one bare value token (a token
#: not starting with a dash). Prose stays safe because a bare word after
#: claude only reads as a value when a dash-led flag came first: `echo
#: claude is nice -p` has no flag in front of `is` and stays unmatched.
#: DISCLOSED RESIDUALS, per that review and left open deliberately: a
#: quoted binary ('claude' -p), a backslash escape, and env -i relaunches
#: all still pass a textual matcher; no regex closes that class, and the
#: real backstops are the width bound in bm_continue and the depth carried
#: in the environment.
_SPAWN = re.compile(
    r"(?:^|[;&|]|\s)(?:[\w./-]*/)?claude\s+"
    r"(?:--?[\w-]+(?:=\S*)?(?:\s+(?![-'\"])[^\s;&|]+)?\s+)*(?:-p|--print)\b")


def deny_payload(reason):
    """The deny object, in the exact shape the PreToolUse contract
    requires and tools/bm_fence_hook.py already emits. Kept identical to
    that file on purpose: two refusal shapes in one product is one shape
    the harness silently ignores."""
    return {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }}


def _warn(message):
    sys.stderr.write(message.rstrip("\n") + "\n")


def projects_root(env=None):
    """Where Claude Code keeps its session transcripts."""
    env = os.environ if env is None else env
    override = env.get(PROJECTS_ENV)
    if override:
        return override
    return os.path.expanduser(os.path.join("~", ".claude", "projects"))


def cap_file_path(env=None):
    """Where the file escape lives. Overridable for tests; the real
    default is a dotfile under the same ~/.claude tree the projects
    directory lives under, so a session can write it with a plain Bash
    redirect without needing a new directory.

    Expands "~" against env["HOME"] rather than calling
    os.path.expanduser directly, because that stdlib call reads the real
    process environment and ignores any env dict passed in here; every
    other lookup in this module honours the env parameter, and this one
    must too or a test's fake HOME is silently ignored."""
    env = os.environ if env is None else env
    override = env.get(CAP_FILE_ENV)
    if override:
        return override
    home = env.get("HOME") or os.path.expanduser("~")
    return os.path.join(home, ".claude", "bm_session_cap.override")


def cap(env=None):
    """The cap in force. CAP_ENV wins when set: it is the escape for
    whoever controls how the `claude` process itself was launched. Failing
    that, the override FILE wins when it holds a number: it is the escape
    for whoever is already inside a running session and can only change
    state on disk, not the environment of a process the harness already
    started. An unreadable value from either source returns the default
    rather than raising: a typo must not be able to deny every launch on
    the machine, nor to allow every one."""
    env = os.environ if env is None else env
    raw = env.get(CAP_ENV)
    source = CAP_ENV
    if raw is None:
        path = cap_file_path(env)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                raw = fh.read()
        except OSError:
            raw = None
        source = path
    if raw is None:
        return CAP_DEFAULT
    try:
        return max(0, int(str(raw).strip()))
    except (TypeError, ValueError):
        _warn("bm_session_cap: %s held %r, which is not a number of "
              "sessions, so the default of %d is in force."
              % (source, raw, CAP_DEFAULT))
        return CAP_DEFAULT


def count_live_sessions(root, now=None, recency=RECENCY_SECONDS,
                        exclude_session=None):
    """How many session transcripts were written to inside the window, or
    None when the directory cannot be read at all. None is the signal to
    FAIL OPEN; a zero count is a real answer and means the machine is
    idle. Those two must never collapse into one value, which is why this
    returns None rather than 0 on failure.

    `exclude_session` skips that session's own transcript. Added 2026-08-09
    after the adversarial review measured the launcher counting against
    itself: the effective headroom was cap minus one, and a cap of 1
    deadlocked a lone session. The hook envelope carries session_id, so the
    counter can honestly count OTHERS."""
    now = time.time() if now is None else now
    cutoff = now - recency
    own = ("%s.jsonl" % exclude_session) if exclude_session else None
    try:
        projects = os.listdir(root)
    except OSError as exc:
        _warn("bm_session_cap: the session directory could not be read "
              "(%s), so no session count is claimed and this launch is "
              "allowed." % exc)
        return None
    live = 0
    for project in projects:
        pdir = os.path.join(root, project)
        try:
            names = os.listdir(pdir)
        except OSError:
            # ONE unreadable project directory is not a failure to count:
            # it is one directory missing from an otherwise real number.
            # Refusing the whole count here would fail open on a permission
            # quirk in a single folder, which is how a cap quietly stops
            # being a cap.
            _warn("bm_session_cap: skipped an unreadable project directory "
                  "(%s); the count below excludes it." % project)
            continue
        for name in names:
            if not name.endswith(".jsonl"):
                continue
            if own is not None and name == own:
                continue
            try:
                if os.path.getmtime(os.path.join(pdir, name)) >= cutoff:
                    live += 1
            except OSError:
                # Same posture as the unreadable-directory skip above: one
                # unreadable session file is excluded from the count, not a
                # reason to fail open on the whole cap.
                _warn("bm_session_cap: skipped an unreadable session file "
                      "(%s); the count below excludes it." % name)
                continue
    return live


def is_session_spawn(envelope):
    """True when this tool call would start another Claude Code session.
    Only Bash can: every other tool is out of scope by construction, and
    saying so here keeps the gate narrow in one readable place."""
    if not isinstance(envelope, dict):
        return False
    if envelope.get("tool_name") != "Bash":
        return False
    tool_input = envelope.get("tool_input")
    if not isinstance(tool_input, dict):
        return False
    command = tool_input.get("command")
    if not isinstance(command, str):
        return False
    return bool(_SPAWN.search(command))


def decide(envelope, projects_root=None, env=None, now=None):
    """The whole decision: a deny payload, or None to allow. Pure apart
    from the filesystem read, so the cap's behaviour can be proven without
    a hook harness, a real session, or a real `claude` binary."""
    env = os.environ if env is None else env
    if not is_session_spawn(envelope):
        return None
    root = (projects_root if projects_root is not None
            else globals()["projects_root"](env))
    # The launcher's own transcript does not count against it: the cap
    # bounds how many OTHER sessions are live when a new one would start.
    live = count_live_sessions(root, now=now,
                               exclude_session=envelope.get("session_id"))
    if live is None:
        return None
    limit = cap(env)
    if live < limit:
        return None
    return deny_payload(
        "BrotherMode session cap: %d Claude sessions have been active in "
        "the last %d minutes and the cap is %d, so this launch is refused. "
        "On 2026-08-08 an unattended chain reached fifteen at once and "
        "spent millions of tokens with nobody watching, which is why this "
        "refuses rather than warns. Close a session and try again, or "
        "raise the cap from right here by writing a number to %s (for "
        "example: echo 10 > %s), which this hook re-reads on the next "
        "launch attempt. Setting %s=N only works if it is in place before "
        "`claude` itself starts, so it is not something a session already "
        "running can do to itself; the file is. Do not work around this by "
        "starting the session another way."
        % (live, RECENCY_SECONDS // 60, limit,
           cap_file_path(env), cap_file_path(env), CAP_ENV))


def _load_bm_repo_scope():
    """Load bm_repo_scope.py by path, same load-by-path shape every hook
    in this directory uses to load a sibling module (bm_fence_hook.py's
    _load_store_module is the template). E76 per-repository hook scoping,
    checked right before the cap's own decision, once the envelope is
    already parsed."""
    try:
        import importlib.util
        here = os.path.dirname(os.path.abspath(__file__))
        spec = importlib.util.spec_from_file_location(
            "bm_repo_scope_for_session_cap", os.path.join(here, "bm_repo_scope.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:  # sbe: allow-silent optional gate module load; hooks_off degrades to active when this returns None
        return None


def main(argv=None):
    """The hook invocation: one JSON envelope on stdin, one JSON object on
    stdout when refusing, nothing at all when allowing. Exit 0 either way,
    because a non-zero exit reads as a BROKEN HOOK to the harness rather
    than as a refusal, and a refusal nobody honours is theatre."""
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        raw = sys.stdin.read()
    except (OSError, ValueError) as exc:
        _warn("bm_session_cap: stdin could not be read (%s); allowing."
              % exc)
        return 0
    try:
        envelope = json.loads(raw) if raw.strip() else {}
    except ValueError as exc:
        _warn("bm_session_cap: the hook envelope was not JSON (%s); "
              "allowing, because a cap that guesses at a malformed "
              "envelope refuses the wrong calls." % exc)
        return 0
    _rs = _load_bm_repo_scope()
    if _rs is not None and _rs.hooks_off(payload=envelope):
        return 0
    decision = decide(envelope)
    if decision is not None:
        sys.stdout.write(json.dumps(decision))
    return 0


if __name__ == "__main__":
    sys.exit(main())
