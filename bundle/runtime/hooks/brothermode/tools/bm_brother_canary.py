#!/usr/bin/env python3
"""tools/bm_brother_canary.py: a disk-backed canary for BrotherMode
liveness, so the NEXT session (after a crash, a quit, running out of
tokens, or a computer restart) can honestly report whether BrotherMode was
previously active, when that was last confirmed, and how to reactivate.

WHAT THIS PROVES, AND WHAT IT DOES NOT
  One fact only: "BrotherMode state was last confirmed active at time T."
  It never proves BrotherMode is active RIGHT NOW. A session that crashed
  one second after the last confirmation looks, on disk, identical to a
  session still running; the lines this file prints say "last confirmed",
  never "is active", on purpose.

THE DETERMINISTIC HOOK, NEVER THE MODEL
  Liveness is computed here, by a hook the model does not control, from a
  timestamp on disk. The model must never be the one asserting BrotherMode
  is still active; that would be a self-report, and a self-report is
  exactly what this file exists to replace.

WHEN IT WRITES, AND THE GAP THAT IS LEFT OPEN ON PURPOSE
  Only when the Skill tool actually fires with a recognizable
  brother*-prefixed skill name (brother:, brothermode:, brothersbe:). A
  failed or interrupted skill call, or any routing path that bypasses the
  Skill tool entirely (a raw slash command taken some other way, a skill
  invoked by a different mechanism), leaves no mark here. That is a
  known, accepted gap, not a bug to silently paper over: the alternative
  would be guessing liveness from something softer than an actual observed
  tool call, which is the self-report this file is built to avoid.

Two entry points, both hooks, both degrade to allow (exit 0 always,
matching tools/bm_session_cap.py and tools/bm_sessionstart.py: a broken
canary must never break the Skill call it observes or the session it
starts):

  postskill    PostToolUse hook (matcher: Skill). Stamps state active on a
               recognized brother*-prefixed skill call and prints one
               activation banner on the FIRST stamp for a session.
  sessionstart SessionStart hook. Reads the last stamp and prints one line:
               resume (recently confirmed, matching version), stale (too
               old, or a known version mismatch), or nothing at all.

State file: ~/.claude/bm_brother_canary.json, mode 0600, written
atomically (tempfile.mkstemp beside the target, os.replace). Every read
and every write in this file degrades quietly on failure: a missing file,
corrupt JSON, a JSON value that is not a dict, or a write that fails
partway all resolve to "no state read" or "no state changed", never an
exception escaping to the caller.

activation_count is a LIFETIME count of distinct sessions this canary has
ever recorded on this machine: a real number counted from this file's own
history, never invented, never pulled from a telemetry system. It is not
a streak: there is no gap tracking, so a session a year after the last
one still just adds one. An older state file with no activation_count
yet reads as 0 rather than inventing a history, and every line that
would print it falls back to the count-free wording instead when it is
genuinely unknown.

Python 3.9, standard library only. No network, no subprocess.
No em or en dashes anywhere in this file, its comments, or its output.
"""

import json
import os
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

#: How stale is too stale to call it a resume. 24 hours: long enough to
#: survive a night, short enough that a state file from last week does not
#: read as "still going".
STALE_AGE = timedelta(hours=24)

#: The skill-name prefixes that count as a BrotherMode activation. Any
#: other Skill call (a third-party skill, an unrelated plugin) leaves no
#: mark here.
_SKILL_PREFIXES = ("brother:", "brothermode:", "brothersbe:")

#: A plugin-cache install path looks like
#: .../brother/brother/<version>/runtime/hooks/brothermode/tools/
#: bm_brother_canary.py; this matches the version segment. Never a
#: hardcoded literal: a literal drifts the first release it ships
#: unchanged in.
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+")


def _say(text):
    """Write to stdout AND FLUSH. Copied from tools/bm_sessionstart.py's
    _say(): the flush is load-bearing so a banner line lands in the order
    this process produced it rather than after everything else once a
    pipe's buffer happens to fill."""
    sys.stdout.write(text)
    sys.stdout.flush()


def _warn(message):
    sys.stderr.write(message.rstrip("\n") + "\n")


def _state_path(env=None):
    """Where the canary's state lives. Expands "~" against env["HOME"]
    rather than calling os.path.expanduser directly, the same technique
    tools/bm_session_cap.py's cap_file_path() uses, so a test's fake HOME
    is never silently ignored by a stdlib call that reads the real
    process environment instead of the env dict passed in here."""
    env = os.environ if env is None else env
    home = env.get("HOME") or os.path.expanduser("~")
    return os.path.join(home, ".claude", "bm_brother_canary.json")


def _installed_version(env=None):
    """The plugin version this file is running from, or None when it
    cannot be told (a repo dev checkout under products/, a stripped
    install). Derived by introspecting this file's OWN resolved path for
    a path segment that looks like a version, never a hardcoded literal.
    `env` is accepted for signature symmetry with the rest of this module
    even though the real filesystem path is what matters here, not the
    environment."""
    try:
        parts = Path(__file__).resolve().parts
    except (OSError, RuntimeError, ValueError):
        return None
    for part in parts:
        if _VERSION_RE.match(part):
            return part
    return None


def _now_utc():
    """The current instant, UTC, ISO 8601 with an explicit offset."""
    return datetime.now(timezone.utc).isoformat()


def _parse_ts(raw):
    """An aware UTC datetime, or None on ANY malformed, naive, missing,
    unparsable, or future-dated input. Never guesses: a bad timestamp is a
    NO-DATA case for the caller, not an exception and not a best effort."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        dt = datetime.fromisoformat(raw.strip())
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
        return None  # naive timestamp: cannot be trusted as UTC
    dt = dt.astimezone(timezone.utc)
    if dt > datetime.now(timezone.utc):
        return None  # clock skew: a future timestamp is not evidence
    return dt


def read_state(env=None):
    """The state dict, or None on ANY failure to produce one: missing
    file, corrupt or truncated JSON, or a JSON value that parses but is
    not a dict. No exception escapes this function."""
    path = _state_path(env)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def _atomic_write(path, data):
    """Write `data` (a JSON-serializable dict) to `path` atomically:
    tempfile.mkstemp beside the target (guarantees os.replace stays on one
    filesystem), chmod 0600, os.replace. NEVER raises: any failure at any
    point, including before the temp file exists, resolves to "no state
    changed" and returns False, because a broken write must not crash the
    hook that called it. The temp file is removed on any path that does
    not end in a successful replace."""
    directory = os.path.dirname(path) or "."
    tmp_path = None
    try:
        os.makedirs(directory, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
        except BaseException:
            broken = tmp_path
            tmp_path = None
            try:
                os.remove(broken)
            except OSError:
                pass
            return False
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, path)
        tmp_path = None
        return True
    except Exception:
        return False
    finally:
        if tmp_path is not None:
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def stamp_active(skill_name, session_id, cwd, env=None):
    """Record BrotherMode as active right now, under `session_id`.

    Returns (state_dict, is_first_stamp_for_this_session_id). A state file
    already on disk for the SAME session_id keeps its original
    activated_at (this call only bumps last_seen and the other fields);
    anything else (no prior state, or a different session_id) starts a
    fresh activated_at at this call's timestamp. The write itself
    degrades quietly through _atomic_write: a write failure never raises
    out of this function, so a broken disk resolves to "state not
    persisted", never a crashed hook.

    activation_count is a LIFETIME count of distinct sessions this canary
    has ever recorded on this machine, carried forward from the prior
    state file and incremented by exactly one on each genuinely new
    session_id. It is a real, counted number, never a guess: a state file
    with no activation_count yet (an older schema, or none at all) reads
    as 0 rather than inventing a history. It is NOT a streak: there is no
    gap tracking, so a session a year after the last one still just adds
    one."""
    env = os.environ if env is None else env
    existing = read_state(env)
    now = _now_utc()
    prior_count = 0
    if isinstance(existing, dict):
        raw_count = existing.get("activation_count")
        if isinstance(raw_count, int) and not isinstance(raw_count, bool):
            prior_count = raw_count
    if (isinstance(existing, dict)
            and isinstance(existing.get("session_id"), str)
            and existing.get("session_id") == session_id):
        activated_at = existing.get("activated_at") or now
        is_first = False
        activation_count = prior_count
    else:
        activated_at = now
        is_first = True
        activation_count = prior_count + 1
    state = {
        "active": True,
        "activated_at": activated_at,
        "last_seen": now,
        "session_id": session_id,
        "cwd": cwd,
        "plugin_version": _installed_version(env),
        "skill": skill_name,
        "activation_count": activation_count,
    }
    _atomic_write(_state_path(env), state)
    return state, is_first


def classify_prior_state(state, env=None):
    """None (say nothing), "resume", or "stale".

    None covers: no state at all, or a last_seen that _parse_ts refuses
    (missing, malformed, or future). "stale" covers: age at least 24
    hours, OR a known version mismatch (both the installed version and
    the state's plugin_version are known strings and they differ). If the
    installed version cannot be determined, a mismatch is never claimed;
    classification rests on age alone. Everything else is "resume"."""
    if not isinstance(state, dict):
        return None
    last_seen = _parse_ts(state.get("last_seen"))
    if last_seen is None:
        return None
    age = datetime.now(timezone.utc) - last_seen
    installed = _installed_version(env)
    recorded = state.get("plugin_version")
    version_mismatch = (
        isinstance(installed, str) and installed
        and isinstance(recorded, str) and recorded
        and installed != recorded)
    if age >= STALE_AGE or version_mismatch:
        return "stale"
    return "resume"


def _format_age(delta):
    """A short, whole-unit age string: "45m ago", "3h ago", "2d ago". No
    fractions, no seconds granularity."""
    seconds = max(0, int(delta.total_seconds()))
    days = seconds // 86400
    if days >= 1:
        return "%dd ago" % days
    hours = seconds // 3600
    if hours >= 1:
        return "%dh ago" % hours
    minutes = seconds // 60
    if minutes >= 1:
        return "%dm ago" % minutes
    return "under a minute ago"


# ---------------------------------------------------------------------------
# Consent gate (mirrors tools/vault_recall_hook.py's own _consented()
# exactly: same technique, same schema, same fail-CLOSED-on-any-error
# direction. A second, independent copy on purpose, matching how every
# write-capable entry point in this project duplicates rather than
# imports one shared _consented(): each one owns its own gate rather
# than trusting a shared import to still be gating tomorrow. Both
# postskill (writes the state file) and sessionstart (reads it) are
# gated, because both are wired as their own top-level hooks.json entry
# rather than something bm_sessionstart.py calls on a stranger's behalf,
# so neither can lean on that script's own consent probe running first.
# ---------------------------------------------------------------------------
_bm_setup_cache = []


def _load_bm_setup():
    try:
        import importlib.util
        here = os.path.dirname(os.path.abspath(__file__))
        root = os.path.dirname(here)
        spec = importlib.util.spec_from_file_location(
            "bm_setup_for_brother_canary",
            os.path.join(root, "scripts", "setup.py"))
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:  # sbe: allow-silent optional consent module load; _consented() fails closed on None
        return None


def _consented():
    """True only when scripts/setup.py's own is_consented() says so. Fails
    CLOSED (not consented) on any load error, missing config, or a corrupt
    one."""
    if not _bm_setup_cache:
        _bm_setup_cache.append(_load_bm_setup())
    mod = _bm_setup_cache[0]
    if mod is None:
        return False
    try:
        cfg, _err = mod.read_config()
        return bool(mod.is_consented(cfg))
    except Exception:
        return False


def _read_payload(env=None):
    """The hook JSON payload from stdin, tolerant of empty or unparsable
    input: returns the parsed dict, or None for anything else (empty
    stdin, invalid JSON, a JSON value that is not an object). Callers
    treat None as a no-op, never as an error to raise."""
    try:
        raw = sys.stdin.read()
    except (OSError, ValueError):
        return None
    if not raw or not raw.strip():
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def cmd_postskill(argv, env=None):
    """PostToolUse (matcher: Skill). Stamps state active on a recognized
    brother*-prefixed skill call; prints the activation banner once per
    session, on the first stamp only. Always returns 0."""
    # The gate, before anything else: no consent means no read of stdin
    # and no write of the state file.
    if not _consented():
        return 0
    env = os.environ if env is None else env
    payload = _read_payload(env)
    if payload is None:
        return 0
    if payload.get("tool_name") != "Skill":
        return 0
    tool_input = payload.get("tool_input")
    skill = tool_input.get("skill") if isinstance(tool_input, dict) else None
    if not (isinstance(skill, str) and skill.startswith(_SKILL_PREFIXES)):
        return 0
    session_id = payload.get("session_id")
    if not (isinstance(session_id, str) and session_id.strip()):
        # tools/bm_bash_audit.py confirms session_id rides in the hook
        # envelope on PreToolUse/PostToolUse; this branch only covers a
        # payload shape that omits it. The estate names no environment
        # variable for a session id outside the envelope itself, so this
        # is a plain, honest placeholder, never invented to look like a
        # real session id.
        session_id = (env.get("CLAUDE_SESSION_ID")
                      or "unknown-session")
    cwd = payload.get("cwd")
    if not (isinstance(cwd, str) and cwd.strip()):
        cwd = env.get("PWD") or os.getcwd()
    state, is_first = stamp_active(skill, session_id, cwd, env=env)
    if is_first:
        _say("\U0001F43E This canary recorded BrotherMode session %d, a "
             "lifetime count, not a guarantee of current activity.\n"
             % state["activation_count"])
    return 0


def cmd_sessionstart(argv, env=None):
    """SessionStart. Reads the last stamp and prints at most one line.
    Always returns 0."""
    # The gate, before anything else: no consent means no read of the
    # state file at all.
    if not _consented():
        return 0
    env = os.environ if env is None else env
    _read_payload(env)  # tolerated and discarded; classification needs none of it
    state = read_state(env)
    verdict = classify_prior_state(state, env)
    if verdict == "resume":
        last_seen = _parse_ts(state.get("last_seen"))
        age = datetime.now(timezone.utc) - last_seen
        count = state.get("activation_count")
        if isinstance(count, int) and not isinstance(count, bool):
            _say("\U0001F43E BrotherMode was last confirmed active %s, "
                 "this canary has recorded %d sessions lifetime, please "
                 "recheck before relying on it.\n"
                 % (_format_age(age), count))
        else:
            # An older state file with no activation_count yet: no
            # invented number, fall back to the original wording.
            _say("\U0001F43E BrotherMode was last confirmed active %s, "
                 "please recheck before relying on it.\n"
                 % _format_age(age))
    elif verdict == "stale":
        _say("\U0001F43E Old BrotherMode state found, not a guarantee, run "
             "/brother to reactivate.\n")
    return 0


_COMMANDS = {
    "postskill": cmd_postskill,
    "sessionstart": cmd_sessionstart,
}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in _COMMANDS:
        return _COMMANDS[argv[0]](argv[1:])
    _warn("bm_brother_canary: usage: bm_brother_canary.py "
         "postskill|sessionstart (invoked as a PostToolUse/SessionStart "
         "hook with a JSON payload on stdin)")
    return 0


if __name__ == "__main__":
    try:
        code = main()
    except Exception:
        # MUST always exit 0: a broken canary must never break the Skill
        # call it observes or the session it starts.
        code = 0
    sys.exit(code)
