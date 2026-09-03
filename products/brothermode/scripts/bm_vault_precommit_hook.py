#!/usr/bin/env python3
"""bm_vault_precommit_hook.py: WARNS, at commit time, when another session holds
the vault writer lock. It never blocks.

This is the hook tools/bm_vault_lock.py's own docstring describes when it says
"the pre-commit hook only warns, never blocks, on an active foreign lock". That
file provides the lock and the visibility; this file is the thing that puts the
visibility in front of a person at the moment it matters, which is the instant
before they commit.

WHY IT WARNS RATHER THAN REFUSES, and this is a correction rather than a design
note. An earlier version of this file BLOCKED, exiting 1 on a live foreign lock,
and that was wrong for a reason this estate had already written down: a commit
time gate's blast radius must match the size of the act it guards. The danger
being guarded against is sweeping ANOTHER session's staged files into your
commit, which is what `git commit` with no pathspec does, because it commits the
whole index rather than what you had in mind. A session committing its own work
with an explicit pathspec is safe and was blocked anyway. A refusal here would
have had the same shape as the mistake it responds to, which is exactly the
finding that produced the advisory design in the first place.

So the real fix is an explicit pathspec on every commit, and this hook exists to
make the person reach for one. It says who is writing, how long they have been
writing, and what to do; then it gets out of the way.

WHAT IT DELIBERATELY DOES NOT DO
  1. It does not block. Every path through this file exits 0.
  2. It does not inspect the index, the message or the diff. One job.
  3. It does not invent a stricter rule than the lock it reads. An unreadable or
     structurally wrong lock file is treated as no lock, which is
     bm_vault_lock's own documented behaviour: a reader that disagreed with its
     lock would give the estate two answers about one file.
  4. It does not warn about a lock this session holds itself, or a stale one.

HOW IT IS WIRED (not done by this file, on purpose)
  A pre-commit hook must live at .git/hooks/pre-commit, and .git/hooks/ is never
  tracked by git: installing into it here would silently do nothing for every
  other clone, and would be this file writing into a repository nobody asked it
  to touch. Wiring it in is the caller's job, one line, run from the vault root:
    printf '#!/bin/sh\\nexec python3 %s\\n' "$PWD/scripts/bm_vault_precommit_hook.py" \\
      > .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit

EXIT CODES
  0   always. A warning hook that can fail a commit is a blocking hook with an
      inconsistent temper, which is worse than either.

Python 3.9, standard library only. No network, no subprocess. Reads one file.

No em or en dashes anywhere in this file, its comments, or its output.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LOCK_MODULE = os.path.join(os.path.dirname(HERE), "tools", "bm_vault_lock.py")


def _warn(text):
    sys.stderr.write(text + "\n")


def _session_label():
    """Who this process is. Matches bm_vault_lock's --session convention and
    tools/bm_vault_catalog.py's reader, so all three agree on identity."""
    return os.environ.get("CLAUDE_SESSION_ID") or ("pid-%d" % os.getpid())


def _load_lock_module(path=LOCK_MODULE):
    """Loaded BY PATH, the technique the sibling tools use. Returns None when the
    module is absent, because a hook that crashes on a missing sibling is a hook
    that breaks every commit in the repository."""
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("bm_vault_lock", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception as exc:
        # Spoken, never swallowed: with no lock module there is no warning to give,
        # and a reader must not mistake silence for "nobody is writing".
        _warn("bm_vault_precommit_hook: NO-LOCK, could not load bm_vault_lock (%s), "
              "so no concurrent writer was checked for. Not blocking." % exc)
        return None


def warning_for(vault, me=None, lock_module=None):
    """The warning text, or None when there is nothing to say. Pure enough for the
    suite to drive every branch: pass a stub module and a session label."""
    me = _session_label() if me is None else me
    lk = _load_lock_module() if lock_module is None else lock_module
    if lk is None:
        return None
    data = lk._read_lock(lk._lock_path(vault))
    if not data:
        return None
    holder = data.get("session", "unknown")
    if holder == me:
        return None
    try:
        age = (lk.datetime.now(lk.timezone.utc)
               - lk._parse_iso(data.get("acquired", ""))).total_seconds()
    except (ValueError, TypeError) as exc:
        _warn("bm_vault_precommit_hook: a vault writer lock exists but its timestamp "
              "could not be read (%s), so no warning was raised about it. Not "
              "blocking." % exc)
        return None
    if age > lk.STALE_SECONDS:
        return None
    note = data.get("note")
    lines = [
        "bm_vault_precommit_hook: %s is writing this vault (%d seconds)."
        % (holder, int(age)),
    ]
    if note:
        lines.append("  what they are doing: %s" % note)
    lines.append(
        "  A bare `git commit` commits the WHOLE INDEX, so it can sweep their staged "
        "files into your commit under your message.")
    lines.append(
        "  Commit with an explicit pathspec instead, for example: "
        "git commit -- path/to/your/file.md")
    lines.append("  Not blocking. This is a warning.")
    return "\n".join(lines)


def main(argv):
    if argv:
        _warn("bm_vault_precommit_hook: takes no arguments")
        return 0
    text = warning_for(os.getcwd())
    if text is not None:
        _warn(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
