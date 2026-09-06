#!/usr/bin/env python3
"""E76: per-repository hook scoping, the shared reader every BrotherMode
hook calls at entry (directive P1, section 12; README.md's "no
per-repository opt-out yet" line, closed by this file).

.brother/config at the repository root is the only configuration surface
this adds. Three states, no others:

  absent (the common case): default behaviour, every hook runs exactly as
    before.
  present with an explicit "hooks: off" line (case-insensitive, blank
    lines and #-comments ignored): every hook EXCEPT the write guards
    (see WRITE_GUARD_HOOKS below) exits 0 at once, without doing its own
    work, and the FIRST hook to observe this in a given session prints
    one notice to stderr; every hook after that in the same session
    stays silent, so the transcript is not spammed.
  present but unreadable, undecodable, or without that exact line
    (garbage): default (active) behaviour, plus one stderr diagnostic
    line naming the problem, so a typo in the file is visible rather than
    silently ignored. It never blocks the hook.

No configuration system beyond those two lines: no sections, no other
keys, no per-hook granularity.

E50 (2026-09-04) adds the other half, the one the INSTALL decides rather
than the repository: SCOPED INSTALLATION. Both products' installers write
one marker file beside the Claude settings file they edited,
<claude config dir>/brother-hook-scope, holding the line

  scope: repositories

and from then on a repository with no .brother/config at all is INACTIVE:
every hook returns at entry, silently, having read no file of its own and
written none. Opting a repository in is the presence of that same
.brother/config (the documented content is the line "hooks: on", which is
self describing beside "hooks: off"; presence is what the gate reads).

So the three states above become four, and which one an absent config
means now depends on the marker, not on this file:

  marker absent (a clone, a developer tree, this repository's own tests):
    unchanged, absent config means ACTIVE, exactly as E76 shipped it.
  marker present, config absent: INACTIVE, silent, no notice file, no
    stderr line. A repository nobody opted in costs nothing at all, which
    is the whole point of the row, so it does not even get a sentence.
  marker present, config present: the E76 states above decide it, so
    "hooks: off" still turns a repository off even on a scoped machine.
  marker present but unreadable: treated as absent (ACTIVE) plus one
    stderr diagnostic, the same fail-open direction the config takes,
    because a broken marker must never be the reason a hook stops working.

Deliberately independent of bm_store.py: bm_store.resolve_root() answers
"where is this project's BrotherMode store", which a marker directory or
BROTHERMODE_ROOT can point away from the repository's own git root. This
gate is about the REPOSITORY (a git property), not the store, so it walks
to the nearest .git itself rather than borrowing a resolver built for a
different question.

Pure stdlib, no subprocess, no network: every caller loads this file the
same load-by-path way it loads its other siblings (bm_learning.py from
bm_telemetry.py is the shape this follows), so the SECURITY.md no-network
claim covers this file with no new exemption.

No em or en dashes anywhere in this file, its comments, or its output.
"""

import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# C3: the config directory is resolved by brother_paths, the one seam
# that knows which coding client is running (docs/codex/HOOKS-MAPPING.md).
# Loaded from beside this file because tools/ is not a package.
sys.path.insert(0, HERE)
import brother_paths  # noqa: E402

MODNAME = "bm_repo_scope"
CONFIG_RELPATH = os.path.join(".brother", "config")
OFF_LINE = "hooks: off"
ON_LINE = "hooks: on"
# E50: the install-side marker. Product neutral on purpose, one file for the
# whole machine: the sibling product's tools/sbe_repo_scope.py reads this same
# name at this same path, so a person who scoped their machine scoped it
# once rather than once per product.
SCOPE_MARKER_NAME = "brother-hook-scope"
SCOPE_LINE = "scope: repositories"
SCOPE_MARKER_TEXT = (
    "# Written by a Brother installer (E50). While this file carries the\n"
    "# line below, hooks from both products run ONLY in a repository that\n"
    "# carries its own .brother/config file. Delete this file to go back to\n"
    "# hooks in every repository on the machine.\n"
    "%s\n" % SCOPE_LINE)
NOTICE_DIRNAME = os.path.join(".brothermode", "repo-scope-notice")
NOTICE = (
    "BrotherMode: hooks are off for this repository (%s says '%s'); doing "
    "nothing. Remove that line to turn hooks back on.\n"
    % (CONFIG_RELPATH, OFF_LINE))

#: The hooks a repository's own file may NEVER switch off (security review
#: 2026-09-04, Major). Every other hook here reports, records or advises,
#: and a repository saying "not in my tree, thanks" to those is the whole
#: point of this file. These three DECIDE WHETHER A WRITE HAPPENS, and
#: .brother/config is content that arrives with the repository: a clone can
#: carry that line, so the tree being inspected was able to switch off the
#: guard standing between it and the person's disk, with one stderr line as
#: the only trace. The names are documentation for a reader; the mechanism
#: is the write_guard argument below, which each of these three passes.
WRITE_GUARD_HOOKS = ("bm_fence_hook.py",
                     "sbe_authority_hook.py",
                     "sbe_bash_write_guard.py")

GUARD_NOTICE = (
    "BrotherMode: %s says '%s', which does not switch off the write guards; "
    "a repository cannot turn off the check standing between it and your "
    "disk. Every other hook is off.\n"
    % (CONFIG_RELPATH, OFF_LINE))


def find_repo_root(start=None):
    """The nearest ancestor of start (default: cwd) that carries a .git
    entry (a directory in a normal clone, a file in a worktree), or None
    when no ancestor does. Pure os.path, no subprocess: the one question
    this file answers must not itself depend on git being invocable."""
    try:
        d = os.path.realpath(start or os.getcwd())
    except OSError:  # sbe: allow-silent unstattable cwd reads as no repo root; hooks_off degrades to active
        return None
    while True:
        if os.path.exists(os.path.join(d, ".git")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def _read_config(root):
    """(off, diagnostic). diagnostic is None unless the file exists but
    could not be opened or decoded as UTF-8 text, in which case off is
    False (default, active) and diagnostic names the problem. Never
    raises: an unreadable config is an explicit failure path, not a
    block."""
    if not root:
        return False, None
    path = os.path.join(root, CONFIG_RELPATH)
    if not os.path.exists(path):
        return False, None
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except (OSError, UnicodeDecodeError) as e:
        return False, ("bm_repo_scope: %s could not be read (%s: %s); "
                        "hooks stay active." % (path, type(e).__name__, e))
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.lower() == OFF_LINE:
            return True, None
    return False, None


def scope_marker_path():
    """Where an installer leaves the E50 marker: beside the Claude settings
    file it edited. CLAUDE_CONFIG_DIR is Claude Code's own override for that
    directory and the installers resolve the same place through the settings
    path they were given, so the two agree on a real machine and a test can
    move both with one environment variable."""
    return os.path.join(brother_paths.config_dir(), SCOPE_MARKER_NAME)


def installation_is_scoped():
    """(scoped, diagnostic). scoped is True only when the marker exists, is
    readable as UTF-8 text, and carries the SCOPE_LINE (case insensitive,
    blank lines and #-comments ignored). Anything else is False, which is
    the pre-E50 behaviour; an unreadable marker also returns a diagnostic
    naming the problem, because a scoping decision that silently stopped
    applying is worse than a noisy one. Never raises."""
    path = scope_marker_path()
    if not os.path.exists(path):
        return False, None
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except (OSError, UnicodeDecodeError) as e:
        return False, ("%s: %s could not be read (%s: %s); hooks run in "
                       "every repository until it is fixed."
                       % (MODNAME, path, type(e).__name__, e))
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.lower() == SCOPE_LINE:
            return True, None
    return False, None


def repository_is_opted_in(root):
    """True when this repository carries the opt-in file at all. Presence is
    the marker, so this asks os.path.exists and reads nothing: a repository
    nobody opted in must cost one stat, not one open."""
    if not root:
        return False
    return os.path.exists(os.path.join(root, CONFIG_RELPATH))


def _session_slot(session_id):
    sid = (session_id or "").strip() or "no-session-id"
    return hashlib.sha256(
        ("bm-repo-scope-notice-v1|" + sid).encode("utf-8")).hexdigest()[:32]


def _notice_pending(root, session_id):
    """True the first time this (root, session_id) pair asks; False on
    every call after, in this or any later process (a hook is a fresh
    process per invocation, so this has to persist to disk to mean
    anything). O_CREAT|O_EXCL makes the check-and-set atomic across
    concurrent hooks, the same technique bm_fence_hook.py's ensure_token
    uses for its own per-session marker.

    Fail-open toward PRINTING: a directory or marker this cannot create
    still returns True, because a missed notice hides an active scoping
    decision from the person it affects, and a duplicate notice merely
    repeats a true line."""
    if not root:
        return True
    try:
        d = os.path.join(root, NOTICE_DIRNAME)
        os.makedirs(d, exist_ok=True)
        marker = os.path.join(d, _session_slot(session_id))
        fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        return True
    except FileExistsError:
        return False
    except OSError:
        return True


def _extract(payload):
    """(cwd, session_id) best-effort out of a hook payload, which callers
    hand in as a dict already parsed, a raw JSON string straight off
    stdin, or None. Never raises."""
    if isinstance(payload, dict):
        d = payload
    elif isinstance(payload, str) and payload.strip():
        try:
            d = json.loads(payload)
        except ValueError:
            d = {}
        if not isinstance(d, dict):
            d = {}
    else:
        d = {}
    cwd = d.get("cwd") if isinstance(d.get("cwd"), str) else None
    session_id = d.get("session_id") if isinstance(d.get("session_id"), str) else None
    return cwd, session_id


def hooks_off(payload=None, cwd=None, write_guard=False):
    """True when the calling hook should stop now, having already printed
    the one-time notice (or the garbage-file diagnostic) to stderr. Never
    raises: any internal failure degrades to False (active), because a
    scoping check must never be the reason a real hook stops working.

    write_guard=True is passed by the three hooks in WRITE_GUARD_HOOKS and
    means "this caller decides whether a write happens". For those the
    answer is always False: the repository-supplied opt-out is honoured for
    every reporting hook and refused for the guards, with one notice per
    session so the person who wrote the line can see which half took."""
    try:
        p_cwd, session_id = _extract(payload)
        root = find_repo_root(cwd or p_cwd)
        scoped, scope_diagnostic = installation_is_scoped()
        if scope_diagnostic:
            sys.stderr.write(scope_diagnostic + "\n")
        if scoped and not repository_is_opted_in(root):
            # E50, the whole row in one branch: an install that scoped
            # itself runs nothing in a repository nobody opted in. Silent
            # on purpose. A notice here would print in every unrelated
            # repository on the machine, and the per-session marker that
            # keeps a notice from repeating would WRITE into a repository
            # this row promises to leave untouched.
            return True
        off, diagnostic = _read_config(root)
        if diagnostic:
            sys.stderr.write(diagnostic + "\n")
        if off and write_guard:
            # Its own marker slot, so this notice and the ordinary one above
            # do not consume each other: a session must be able to see both
            # "hooks are off" and "except the guards".
            if _notice_pending(root, "write-guard|" + (session_id or "")):
                sys.stderr.write(GUARD_NOTICE)
            return False
        if off and _notice_pending(root, session_id):
            sys.stderr.write(NOTICE)
        return off
    except Exception as e:
        sys.stderr.write("bm_repo_scope: %s: %s; hooks stay active.\n"
                         % (type(e).__name__, e))
        return False
