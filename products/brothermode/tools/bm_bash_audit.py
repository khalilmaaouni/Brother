#!/usr/bin/env python3
"""BrotherMode Bash-write DETECTION hook (Loop 6 design D-1): the honest
answer to the one gap docs/HOOKS.md has stated for months and never closed.

WHY THIS EXISTS
  tools/bm_fence_hook.py enforces one writer per file for Edit, Write,
  MultiEdit and NotebookEdit, and it says so itself: Bash is deliberately
  absent from WRITE_TOOLS, because no reliable parse of arbitrary shell
  exists and pretending to gate it would be a guarantee that file cannot
  keep. A shell redirect, `sed -i`, `tee`, or a script invoked through Bash
  can still cross a fence, invisibly, and until this file nothing noticed.

  Gating Bash is not on the table (it would need a PreToolUse payload that
  names the files a command will touch, which the harness does not provide,
  or an OS-level write mediator, which is outside "Python 3.9, standard
  library only"). What IS achievable without either of those: notice AFTER
  the fact. This file is a PreToolUse/PostToolUse PAIR, both wired to the
  same `Bash` matcher, that snapshots every fenced file before a Bash call
  and re-hashes it after. When a fenced file changed and the session that
  ran the Bash call is not that fence's owner, it raises a real alert row
  through the service layer and prints one plain sentence to stderr. That
  is detection, not prevention: the write already happened. Docs/HOOKS.md
  and docs/KNOWN-LIMITS.md say so in as many words, because a reader who
  believes this closes the Bash gap has been misled.

THE RULES THIS FILE OBEYS (same three bm_fence_hook.py states, plus one)
  1. FAIL OPEN, LOUDLY, IN THE DEFAULT MODE. Both entrypoints always return
     0, whatever went wrong, and by default nothing here ever blocks a Bash
     call. A hook that failed closed on its own bug would brick every shell
     command the founder runs. Every failure path prints its reason to
     stderr, same policy as bm_fence_hook.py's own stated one.
     ONE EXCEPTION, added for C-02 (2026-08-03) and opt-in only: when
     BM_FENCE_MODE=enforced AND the Bash call's cwd resolves to a
     BrotherMode project, the pre phase REFUSES a Bash command that matches
     an obvious destructive form aimed at BrotherMode's own enforcement
     state (the store file, the fence token directory). Outside a
     BrotherMode project this hook is installed at user-global scope, so
     that project check is load-bearing, not decoration: without it,
     enforced mode would refuse commands anywhere on the machine. See
     refusal_for() for exactly what that can and cannot catch, in its own
     words. The default mode is byte-for-byte unchanged.
  2. STDOUT IS THE DECISION CHANNEL AND NOTHING ELSE. A PreToolUse hook's
     stdout is the permission decision channel. This hook writes to it in
     exactly one situation, the enforced-mode refusal in rule 1, and it
     writes the deny object and nothing else; the post phase never writes to
     stdout at all. Every diagnostic, including the sentences naming a
     breach or a lost store, goes to stderr.
  3. CANONICAL PATHS AND IDENTITY, SHARED WITH THE FENCE HOOK, NOT
     REIMPLEMENTED. This file loads tools/bm_fence_hook.py by path (the same
     importlib-by-path technique bm_fence_hook.py itself uses for
     tools/bm_store.py) and calls its own `active_claims` and
     `session_label`, so "which paths are fenced" and "does this session own
     that fence" can never drift from what the PreToolUse fence hook already
     enforces for Edit/Write/MultiEdit/NotebookEdit. The store itself is
     never opened directly by sqlite3 here for the READ half either: it goes
     through tools/bm_store.py exactly the way bm_fence_hook.py resolves it
     (ReadOnlyStore, inside active_claims). Only the WRITE half (raising the
     alert) opens a writable Store, because ReadOnlyStore has no path to
     raise_alert by construction.
  4. CONSENT-GATED, MIRRORING tools/bm_autosave.py. Pre-consent, both
     entrypoints check first, before reading stdin, before touching the
     filesystem, and print one sentence naming `scripts/setup.py`. Nothing
     is written, not a snapshot file, not an alert row.

WHAT THIS ACTUALLY COVERS, STATED RATHER THAN IMPLIED
  A snapshot only exists for a claim path that is a REAL, EXISTING FILE at
  the moment the Bash call starts (a literal `os.path.isfile` check on the
  claim's stored path, root-relative, joined onto the project root). A
  claim on a directory or a glob-shaped path (bm_store.paths_overlap's `*`,
  `?`, `[` handling) is not expanded into the files it would cover: this
  file does not walk the tree to discover what a glob claim spans, so a new
  file created inside a claimed directory during the Bash call is invisible
  to it. That is a real, stated limitation, not a silent gap: see the "What
  this cannot see" section of docs/HOOKS.md.

Python 3.9, standard library only, cross-platform. No em or en dashes
anywhere in this file, its comments, or its output.
"""
import hashlib
import io
import json
import os
import re
import sys
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------------------
# Module loaders. Each by path, each independent of whatever any other
# tools/*.py file already loaded in this process, matching the rationale
# bm_fence_hook.py and bm_autosave.py both state for the same technique: a
# plain `import bm_store` would depend on whichever sys.path the caller
# happened to have, and this file is invoked by Claude Code with an
# arbitrary cwd. Every loader is deferred into a function and never raises:
# an unimportable module is a fail-OPEN condition, printed, not a crash in
# front of every Bash call.
# ---------------------------------------------------------------------------
_BS = None
_BS_ERROR = None
_FH = None
_FH_ERROR = None


def _load_store_module():
    global _BS, _BS_ERROR
    if _BS is not None or _BS_ERROR is not None:
        return _BS
    try:
        import importlib.util
        path = os.path.join(HERE, "bm_store.py")
        spec = importlib.util.spec_from_file_location("bm_store", path)
        if spec is None or spec.loader is None:
            _BS_ERROR = "no import spec for %s" % path
            return None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        sys.modules.setdefault("bm_store", mod)
        _BS = mod
        return _BS
    except Exception as e:
        _BS_ERROR = "%s: %s" % (type(e).__name__, e)
        return None


_RS = None
_RS_LOADED = False


def _load_repo_scope_module():
    """Loads tools/bm_repo_scope.py by path, same shape as
    _load_store_module above. E76 per-repository hook scoping, checked at
    the top of cmd_pre and cmd_post, before either does any real work."""
    global _RS, _RS_LOADED
    if _RS_LOADED:
        return _RS
    _RS_LOADED = True
    try:
        import importlib.util
        path = os.path.join(HERE, "bm_repo_scope.py")
        spec = importlib.util.spec_from_file_location("bm_repo_scope", path)
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _RS = mod
        return _RS
    except Exception:  # sbe: allow-silent optional gate module load; hooks_off degrades to active when this returns None
        return None


def _load_fence_hook_module():
    """Loads tools/bm_fence_hook.py by path. This file OWNS no logic for
    "which paths are fenced" or "who owns them": it reuses the already
    tested `active_claims` and `session_label` from the fence hook itself,
    the same way bm_autosave.py reuses tools/bm_store.py rather than
    restating its schema."""
    global _FH, _FH_ERROR
    if _FH is not None or _FH_ERROR is not None:
        return _FH
    try:
        import importlib.util
        path = os.path.join(HERE, "bm_fence_hook.py")
        spec = importlib.util.spec_from_file_location("bm_fence_hook", path)
        if spec is None or spec.loader is None:
            _FH_ERROR = "no import spec for %s" % path
            return None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        sys.modules.setdefault("bm_fence_hook", mod)
        _FH = mod
        return _FH
    except Exception as e:
        _FH_ERROR = "%s: %s" % (type(e).__name__, e)
        return None


# ---------------------------------------------------------------------------
# Consent gate (mirrors tools/bm_autosave.py's exactly: same technique, same
# schema, same fail-CLOSED-on-any-error direction, same env override). A
# second, independent copy on purpose, matching how bm_autosave.py itself
# duplicates rather than imports tools/bm_telemetry.py's own _consented:
# each write-capable entry point owns its own gate rather than trusting a
# shared import to still be gating tomorrow.
# ---------------------------------------------------------------------------
_bm_setup_cache = []


def _load_bm_setup():
    try:
        import importlib.util
        root = os.path.dirname(HERE)
        spec = importlib.util.spec_from_file_location(
            "bm_setup_for_bash_audit", os.path.join(root, "scripts", "setup.py"))
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:  # sbe: allow-silent optional consent module load; _consented() fails closed on None
        return None


def _get_bm_setup():
    if not _bm_setup_cache:
        _bm_setup_cache.append(_load_bm_setup())
    return _bm_setup_cache[0]


def _consented():
    """True only when scripts/setup.py's own is_consented() says so. Fails
    CLOSED (not consented) on any load error, missing config, or a corrupt
    one."""
    mod = _get_bm_setup()
    if mod is None:
        return False
    try:
        cfg, _err = mod.read_config()
        return bool(mod.is_consented(cfg))
    except Exception:
        return False


_CONSENT_REQUIRED_LINE = (
    "bm_bash_audit: setup is not complete yet; run: python3 scripts/setup.py")


# ---------------------------------------------------------------------------
# Output funnel. ONE, on purpose (rule 2 above): stderr only. There is no
# _out() here, unlike bm_fence_hook.py, because this file never has a
# decision to emit.
# ---------------------------------------------------------------------------

def _warn(s):
    try:
        sys.stderr.write(s if s.endswith("\n") else s + "\n")
        sys.stderr.flush()
    except Exception:  # sbe: allow-silent the warn sink itself failed; nothing left to report to
        pass


class _FailOpen(Exception):
    """Raised anywhere this file cannot proceed SAFELY. Always caught at the
    top of each entrypoint; always produces a printed reason and exit 0."""


def _read_stdin_json():
    try:
        raw = sys.stdin.read()
    except Exception as e:
        return None, "stdin could not be read (%s: %s)" % (type(e).__name__, e)
    if not raw or not raw.strip():
        return None, "stdin was empty"
    try:
        return json.loads(raw), None
    except Exception as e:
        return None, "stdin was not valid JSON (%s: %s)" % (type(e).__name__, e)


# ---------------------------------------------------------------------------
# C-02: the REFUSE half. Enforced mode only.
# ---------------------------------------------------------------------------

def _enforced(env=None):
    """True when BM_FENCE_MODE is 'enforced'.

    Read here rather than through bm_fence_hook.enforced_mode() because this
    check has to work even when that module cannot be imported, which is one
    of the states enforcement exists for. tools/test_bm_bash_audit.py asserts
    this function and fh.enforced_mode() agree on every value they are given,
    so the duplication cannot drift."""
    env = os.environ if env is None else env
    return (env.get("BM_FENCE_MODE", "") or "").strip().lower() == "enforced"


def deny_payload(reason):
    """The PreToolUse deny object, the same four-key shape
    tools/bm_fence_hook.py's own deny_payload builds, restated here for the
    same reason _enforced is: a refusal must not depend on an import that may
    itself be the thing that failed. A test asserts the two are identical."""
    return {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }}


_DESTRUCTIVE_FORMS = (
    (r"(^|[^<>&])>{1,2}([^&]|$)", "output redirection (> or >>)"),
    (r"\btee\b", "tee"),
    (r"\bsed\b[^|;]*\s-i", "sed -i"),
    (r"\bperl\b[^|;]*\s-i", "perl -i"),
    (r"\brm\b", "rm"),
    (r"\bunlink\b", "unlink"),
    (r"\bshred\b", "shred"),
    (r"\btruncate\b", "truncate"),
    (r"\bdd\b", "dd"),
    (r"\bmv\b", "mv"),
    (r"\bcp\b", "cp"),
    (r"\bln\b", "ln"),
    (r"\bchmod\b", "chmod"),
    (r"\bchown\b", "chown"),
    (r"\bpatch\b", "patch"),
    (r"\bgit\s+(checkout|restore|reset|clean|stash|apply|rm)\b",
     "a git command that rewrites files"),
    (r"\b(python3?|node|ruby|perl)\b[^|;]*\s-(c|e)\b",
     "an inline interpreter script"),
    (r"\bfind\b[^|;]*\s-delete\b", "find -delete"),
    (r"\bmkdir\b", "mkdir"),
)

_TREE_WIDE_FORMS = (
    (r"\bgit\s+clean\b[^|;]*\s-{1,2}[A-Za-z]*[xX]",
     "git clean with -x, which deletes ignored files"),
    (r"\brm\b[^|;]*\s-[A-Za-z]*[rR][A-Za-z]*\s+(\.|\./|\*|\./\*)(\s|;|$)",
     "rm -r aimed at the whole working directory"),
)

_REFUSE_REASONS = {
    "store-destruction": (
        "the command matched a shell form that deletes or overwrites files, "
        "and it named BrotherMode's own enforcement state, which is the "
        "record of who owns which file",
        "make the change with Edit or Write so the fence can check it, or, "
        "if this store really has to be rebuilt, re-run that one command "
        "with BM_FENCE_MODE=advisory and say in the session that you did"),
    "tree-wide-destruction": (
        "the command matched a shell form that empties or rewrites the whole "
        "working directory, which would take BrotherMode's own enforcement "
        "state with it",
        "name the paths the command should touch instead of the whole "
        "directory, or re-run that one command with BM_FENCE_MODE=advisory "
        "and say in the session that you did"),
}


def protected_names(bs):
    """The literal names of this project's own enforcement state on disk.
    Taken from bm_store's constants rather than retyped, so a rename there
    cannot leave this list matching a name that no longer exists."""
    store_dirname = bs.STORE_DIRNAME if bs is not None else ".brothermode"
    store_filename = bs.STORE_FILENAME if bs is not None else "store.sqlite3"
    return (store_dirname, store_filename)


def refusal_for(command_text, bs):
    """(code, labels, names) when this command text matches a destructive
    form aimed at BrotherMode's own enforcement state, else (None, [], []).

    WHAT THIS CAN AND CANNOT DO, STATED RATHER THAN IMPLIED. This is not a
    shell parser and cannot become one here, for the reason bm_fence_hook.py
    already gives for leaving Bash out of WRITE_TOOLS. It matches two things
    and only two:

      A. the command TEXT contains one of this project's own state names as
         a literal substring (bm_store.STORE_DIRNAME or STORE_FILENAME) AND
         it matches one of the destructive shell forms above.
      B. the command TEXT matches one of a very small set of forms that
         empty or rewrite the whole working directory without naming
         anything at all.

    It therefore catches every form the C-02 reproduction used (rm,
    redirection, sed -i, tee, an inline python3 -c, git checkout). It MISSES,
    by construction and not by oversight:
      - a name assembled at runtime ('d=.brother; rm -f ${d}mode/...'),
      - a name reached through a variable, an alias, a shell function, or a
        script FILE whose contents this hook never sees,
      - any program that removes the file without the name appearing in the
        command at all (an editor, a runtime reading the path from a config,
        a second process this command merely starts),
      - every destructive form not in the two lists above.
    And it OVER-refuses on purpose: a read-only command that merely mentions
    the directory next to any redirection ('ls .brothermode > /tmp/x') is
    refused too, and so is 'git clean -x' anywhere in the tree. Erring toward
    refusal is the deliberate choice, because the alternative inside a
    fail-closed mode is a false ALLOW on exactly the class of command this
    exists for."""
    if not isinstance(command_text, str) or not command_text.strip():
        return None, [], []
    names = [n for n in protected_names(bs) if n in command_text]
    if names:
        labels = [lab for pat, lab in _DESTRUCTIVE_FORMS
                  if re.search(pat, command_text)]
        if labels:
            return "store-destruction", labels, names
    labels = [lab for pat, lab in _TREE_WIDE_FORMS
              if re.search(pat, command_text)]
    if labels:
        return "tree-wide-destruction", labels, []
    return None, [], []


def _refuse(code, labels, names):
    """Emit the refusal: one operator sentence on stderr, one deny object on
    stdout. The deny REASON is LITERAL, drawn from _REFUSE_REASONS, and names
    no path and no payload content, matching the rule bm_fence_hook.py's
    _FAIL_REASONS already follows: that string is read by the model and lands
    in a transcript. The stderr line is allowed to be specific, and is, but
    even it never repeats the command text: it names only the FORM LABELS and
    the PROTECTED NAMES, both of which are this file's own constants."""
    summary, remedy = _REFUSE_REASONS[code]
    detail = " and ".join(labels) or "a destructive shell form"
    named = ", ".join(names)
    _warn("bm_bash_audit: REFUSING this Bash call. BM_FENCE_MODE=enforced, "
          "and the command matched %s%s. Nothing was run and nothing was "
          "changed. The command text is not repeated here."
          % (detail, (" while naming %s" % named) if named else ""))
    try:
        sys.stdout.write(json.dumps(deny_payload(
            "BrotherMode is in enforced mode and refused this shell command "
            "because %s. To fix it, %s. To go back to warning only, set "
            "BM_FENCE_MODE=advisory." % (summary, remedy))))
        sys.stdout.flush()
    except Exception as e:
        _warn("bm_bash_audit: the refusal above could not be written to "
              "stdout (%s: %s), so Claude Code will not see it; treat the "
              "line above as the only record." % (type(e).__name__, e))


# ---------------------------------------------------------------------------
# Snapshot storage. Lives beside the fence token directory, inside the same
# .brothermode container the store and the fence hook already use, so it
# inherits whatever gitignore and containment treatment that directory has.
# ---------------------------------------------------------------------------
SNAPSHOT_DIRNAME = "bash-audit"
_SLOT_DOMAIN = "bm-bash-audit-slot-v1|"

# A4 (loop6 refuter finding): a snapshot written at PreToolUse is normally
# removed once its matching PostToolUse comparison finishes (see
# _run_post). When that PostToolUse never runs at all -- the Bash call was
# denied before it started, the hook itself timed out, or the session was
# killed mid-call -- the snapshot is orphaned: nothing else ever cleans it
# up, and each one holds paths plus sha256 digests of every file that was
# fenced at that moment, unbounded, forever. 24 hours is the TTL: long
# enough that no snapshot from an in-progress session is ever mistaken for
# an orphan (a PostToolUse fires seconds after its PreToolUse, not hours
# later), short enough that a crashed session's leftovers do not linger.
SNAPSHOT_TTL_SECONDS = 24 * 60 * 60

ALERT_PROJECT_ID = "brothermode-bash-audit"
# The two static strings the ALERT_PROJECT_ID row is registered with (see
# _alert_project_id). Static on purpose: nothing founder-typed reaches this
# row, so no redaction decision rides on it. Project.REQUIRED is
# (project_id, name, created_at, updated_at); goal is optional and carried
# here so a founder who does meet this row in a listing can tell at a
# glance that it is bookkeeping, not work of theirs.
ALERT_PROJECT_NAME = "BrotherMode bash-audit hook"
ALERT_PROJECT_GOAL = (
    "bookkeeping only: the project the bash-audit hook files its "
    "fence-breach and fence-control-loss alerts under when this store "
    "holds no single founder project to attribute them to. Not a project "
    "anyone works on.")
ALERT_SEVERITY = "high"
ALERT_CATEGORY = "fence-breach"


def _sha256_hex(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def snapshot_dir(root, bs):
    store_dirname = bs.STORE_DIRNAME if bs is not None else ".brothermode"
    return os.path.join(root, store_dirname, SNAPSHOT_DIRNAME)


def snapshot_slot(session_id, tool_use_id):
    """The snapshot FILENAME's stem, keyed on both the session and the exact
    tool call: two Bash calls in the same session must never share a
    snapshot, or the second call's PostToolUse would compare against the
    wrong baseline (or none, if the first call already deleted it)."""
    return _sha256_hex(_SLOT_DOMAIN + session_id + "|" + (tool_use_id or ""))[:32]


def snapshot_path(root, bs, session_id, tool_use_id):
    return os.path.join(snapshot_dir(root, bs),
                        snapshot_slot(session_id, tool_use_id) + ".json")


def _sha256_file(path):
    """Chunked, so a large fenced file is never read whole into memory."""
    h = hashlib.sha256()
    with io.open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _write_json_atomic(bs, path, obj):
    """The one write primitive this file uses for the snapshot: a temp file
    beside the target, tightened to 0600, then os.replace, mirroring
    tools/bm_autosave.py's _write_text_atomic exactly (same shape, same
    reason: a reader must never see a half-written snapshot)."""
    tmp = "%s.tmp-%d" % (path, os.getpid())
    text = json.dumps(obj)
    with io.open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    if bs is not None:
        bs._chmod_best_effort(tmp, 0o600)
    os.replace(tmp, path)


def _ensure_snapshot_dir(root, bs):
    d = snapshot_dir(root, bs)
    if not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
        if bs is not None:
            bs._chmod_best_effort(d, 0o700)
        else:
            try:
                os.chmod(d, 0o700)
            except OSError:  # sbe: allow-silent best-effort dir tightening, no bs helper available on this path
                pass
    if bs is not None:
        bs._refuse_if_symlink_escape(d)
    return d


def _reap_stale_snapshots(root, bs):
    """A4 (loop6 refuter finding): best-effort age-based cleanup for
    orphaned snapshot files, run at the START of every pre phase (see
    _run_pre). Removes any *.json file under the snapshot directory whose
    own mtime is older than SNAPSHOT_TTL_SECONDS (24 hours). Never raises
    and never blocks the pre phase's own work: a single unreadable or
    unremovable entry, or a directory that cannot be listed at all, is
    skipped rather than fatal, because this is housekeeping, not part of
    the snapshot-and-compare contract this file exists for."""
    try:
        d = snapshot_dir(root, bs)
        if not os.path.isdir(d):
            return
        cutoff = time.time() - SNAPSHOT_TTL_SECONDS
        for name in os.listdir(d):
            if not name.endswith(".json"):
                continue
            p = os.path.join(d, name)
            try:
                if os.path.isfile(p) and os.stat(p).st_mtime < cutoff:
                    os.remove(p)
            except OSError:  # sbe: allow-silent one unreadable/unremovable snapshot entry, housekeeping only
                continue
    except Exception:  # sbe: allow-silent reap is best-effort cleanup, never blocks the pre phase per docstring
        pass


def _entry_for_claim(root, rel_path, row):
    """(path, size, mtime, sha256, owner metadata) for ONE claimed path, or
    None when that path is not a real, readable file right now (a directory
    or glob-shaped claim, or a claim on a path that does not exist yet).
    Never raises: a file this process cannot read is simply not snapshotted,
    the same best-effort posture tools/bm_autosave.py takes on its own
    per-path reads."""
    if not isinstance(rel_path, str) or not rel_path.strip():
        return None
    abs_path = os.path.join(root, rel_path.replace("/", os.sep))
    try:
        if not os.path.isfile(abs_path):
            return None
        st = os.stat(abs_path)
        sha = _sha256_file(abs_path)
    except OSError:  # sbe: allow-silent unreadable file is simply not snapshotted, per docstring
        return None
    return {
        "path": rel_path,
        "size": st.st_size,
        "mtime": st.st_mtime,
        "sha256": sha,
        "owner_session_id": row.get("session_id") or "",
        "record_name": row.get("name") or "",
        "lifecycle_uuid": row.get("lifecycle_uuid") or "",
    }


#: The first sixteen bytes of every SQLite database file. Read from the
#: on-disk format documentation and confirmed against this project's own
#: store; used only to notice that a file that WAS a database no longer is.
SQLITE_MAGIC = b"SQLite format 3\x00"

ALERT_CATEGORY_CONTROL = "fence-control-loss"

#: The control findings, keyed by code, each a LITERAL sentence. C-02: a
#: shell command that destroys the enforcement state used to produce no
#: output at all, because the store is not itself a claimed path and nothing
#: in this file looked at it.
CONTROL_FINDINGS = {
    "store-removed": (
        "the BrotherMode store file was present before this shell command "
        "and is gone after it"),
    "store-emptied": (
        "the BrotherMode store file held a non-empty database before this "
        "shell command and is zero bytes after it"),
    "store-overwritten": (
        "the BrotherMode store file no longer begins with the SQLite file "
        "header, so what is there now is not a database"),
    "fence-dir-removed": (
        "the fence directory that holds session ownership tokens was present "
        "before this shell command and is gone after it"),
    "fence-token-removed": (
        "one or more session ownership tokens that existed before this shell "
        "command are gone after it"),
}


def _control_state(root, bs, fh):
    """A deliberately COARSE picture of BrotherMode's own enforcement state.

    Coarse on purpose. The store legitimately CHANGES during a Bash call (any
    bm_store.py command run through a shell writes to it), so a content hash
    here would alarm on ordinary work and be trained away within a day. This
    records only enough to notice the three destructive outcomes: the store
    gone, emptied, or replaced by something that is not a database, plus the
    fence token directory or individual tokens disappearing. Never raises: an
    unreadable state is recorded as absent, not as a crash in front of a Bash
    call."""
    state = {"store_present": False, "store_size": 0, "store_header_ok": False,
             "fence_dir_present": False, "token_files": []}
    try:
        p = bs.store_path(root)
        if os.path.isfile(p):
            state["store_present"] = True
            state["store_size"] = os.stat(p).st_size
            with io.open(p, "rb") as f:
                state["store_header_ok"] = (
                    f.read(len(SQLITE_MAGIC)) == SQLITE_MAGIC)
    except OSError:  # sbe: allow-silent unreadable state recorded as absent, per docstring, not a crash before Bash
        pass
    try:
        d = fh.fence_dir(root)
        if os.path.isdir(d):
            state["fence_dir_present"] = True
            state["token_files"] = sorted(
                n for n in os.listdir(d) if n.endswith(fh.TOKEN_SUFFIX))
    except OSError:  # sbe: allow-silent unreadable fence dir recorded as absent, same posture as the store read above
        pass
    return state


def _control_findings(root, bs, fh, before):
    """Codes for every destructive change between the pre-phase state and
    now. Growth, ordinary mutation, and NEW token files are invisible here on
    purpose: this reports loss, not change."""
    if not isinstance(before, dict):
        return []
    now = _control_state(root, bs, fh)
    out = []
    if before.get("store_present"):
        if not now["store_present"]:
            out.append("store-removed")
        elif before.get("store_size", 0) > 0 and now["store_size"] == 0:
            out.append("store-emptied")
        elif before.get("store_header_ok") and not now["store_header_ok"]:
            out.append("store-overwritten")
    if before.get("fence_dir_present") and not now["fence_dir_present"]:
        out.append("fence-dir-removed")
    elif before.get("fence_dir_present"):
        gone = [t for t in (before.get("token_files") or [])
                if t not in (now["token_files"] or [])]
        if gone:
            out.append("fence-token-removed")
    return out


def _alert_project_id(bs, store, actor):
    """The project id this hook's alerts are attributed under, guaranteed to
    name a project row that EXISTS by the time it is returned.

    Store.raise_alert writes an attribution row, and
    AttributionEvent.project_id is a required non-empty field it takes from
    its caller. `bm_store.py verify` reports every attribution row whose
    project_id has no matching projects row ("attribution event %s
    references missing project %r"). This hook used to hand raise_alert a
    bare ALERT_PROJECT_ID that nothing ever registered, so the FIRST alert
    it ever raised made verify report a problem permanently, on a store
    that was otherwise healthy.

    A fence is NOT project-scoped: records and claims carry no project_id
    column at all, so there is no project a fence-breach alert intrinsically
    belongs to. Two cases, in this order:

    (1) The store holds exactly ONE project. That is the beginner model
        tools/bm_project.py documents by name (one project per folder), so
        that project is what this root is working on, and the alert is
        filed under it, where the founder's own pages already look.

    (2) Anything else (no project at all, or several, where picking one
        would be a guess). The hook's own ALERT_PROJECT_ID row is used, and
        REGISTERED FIRST so the attribution row points at a project that
        exists.

    Registering is deliberately confined to case (2): this row is not a
    founder project, and six call sites across bm_project.py,
    bm_statusline.py, bm_view.py and bm_lead.py branch on
    len(list_projects()). Adding a row to a store that holds exactly one
    project would flip that count and silently blank the status line,
    rename CANVAS.md, and make `bm_project start` refuse. Case (2) either
    leaves an already-plural count plural, or turns an empty store into a
    one-project store whose single project is this one.

    Idempotent by construction, which this hook needs because it runs on
    EVERY Bash call: the existence check comes first, so the row is written
    once and every later alert finds it and leaves it alone. Calling
    upsert_project unconditionally would instead overwrite the row and file
    a fresh project.upserted event on every breach."""
    projects = store.list_projects(raw=True)
    if len(projects) == 1:
        return projects[0]["project_id"]
    if store.get_project(ALERT_PROJECT_ID, raw=True) is None:
        now = bs.now_iso()
        store.upsert_project({
            "project_id": ALERT_PROJECT_ID,
            "name": ALERT_PROJECT_NAME,
            "goal": ALERT_PROJECT_GOAL,
            "created_at": now,
            "updated_at": now,
        }, actor)
    return ALERT_PROJECT_ID


def _raise_control_alert(bs, root, code, offending_session_id):
    """One alert row for one control finding. Same shape, severity and actor
    as _raise_breach_alert, different category, and a message built entirely
    from CONTROL_FINDINGS, so no founder text and no absolute path can reach
    it at all."""
    alert = {
        "alert_id": uuid.uuid4().hex,
        "severity": ALERT_SEVERITY,
        "category": ALERT_CATEGORY_CONTROL,
        "message": (
            "a Bash command run by session %s changed BrotherMode's own "
            "enforcement state: %s. Enforcement state is not a fenced file, "
            "so no hook refused this."
            % (offending_session_id, CONTROL_FINDINGS[code])),
        "why_it_matters": (
            "the fence decides who may write which file by reading this "
            "state, so a shell command that removes or empties it turns "
            "every later refusal into an allow, silently, and that is the "
            "defect this check exists for."),
        "recommended_action": (
            "restore the store and the fence directory (git, or an autosave "
            "snapshot through `%s recover`), then "
            "run `python3 scripts/doctor.py` and confirm the claims you "
            "expect are still there."
            % bs.invocation("bm-autosave", os.path.join(HERE, "bm_autosave.py"))),
        "requires_human": True,
        "created_at": bs.now_iso(),
        "resolved_at": None,
    }
    actor = {
        "actor_type": "hook",
        "actor_name": "bm_bash_audit",
        "session_id": offending_session_id,
    }
    store = bs.Store(root, create=False)
    try:
        project_id = _alert_project_id(bs, store, actor)
        store.raise_alert(alert, project_id, actor)
    finally:
        store.close()


def _remove_snapshot_best_effort(path):
    """Delete a snapshot file whose job is finished. Never raises: an
    unreadable filesystem here must not turn a successful comparison into a
    reported failure. Called ONLY after a comparison completes, per A3
    (see _run_post): never from an early-return or a _FailOpen path, or a
    retry would have nothing left to compare against."""
    try:
        os.remove(path)
    except OSError:  # sbe: allow-silent finished snapshot cleanup, per docstring never turns a pass into a failure
        pass


def _load_snapshot(path):
    """The saved snapshot, or raise _FailOpen naming why it could not be
    used. Both "missing" and "corrupt" land here, on purpose: a caller that
    cannot verify a baseline must fail open exactly the same way whichever
    of the two happened, per D-4(e)."""
    if not os.path.isfile(path):
        raise _FailOpen("no snapshot was recorded for this session and Bash "
                        "call (%s); nothing to compare against" % path)
    try:
        with io.open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        raise _FailOpen("the snapshot at %s could not be read (%s: %s)"
                        % (path, type(e).__name__, e))
    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        raise _FailOpen("the snapshot at %s is not shaped as expected" % path)
    return data


# ---------------------------------------------------------------------------
# The breach message. Every field that could carry founder-typed free text
# (a record's name is typed by whoever claimed it) is run through the SAME
# export-policy functions tools/bm_store.py's own export path uses
# (redact_text, then mask_absolute_paths), so a fake secret or an absolute
# path typed into a record's name cannot land in the alert unmasked. The
# claimed path itself is already root-relative by construction (bm_store
# canonicalizes every claim to a root-relative POSIX string before it is
# stored), so masking it is a defensive no-op in the ordinary case rather
# than a load-bearing one, and it is still run through the same funnel
# rather than assumed safe.
# ---------------------------------------------------------------------------

def _breach_message(bs, rel_path, entry, offending_session_id):
    safe_path = bs.mask_absolute_paths(bs.redact_text(rel_path))
    safe_name = bs.mask_absolute_paths(
        bs.redact_text(entry.get("record_name") or "(unnamed record)"))
    return (
        "a Bash command run by session %s changed %s, which is inside the "
        "active fence %r (lifecycle %s), and that session is not the "
        "fence's owner. The fence hook cannot see Bash writes; this is the "
        "after-the-fact detection for one."
        % (offending_session_id, safe_path, safe_name,
           entry.get("lifecycle_uuid") or "(unknown)"))


def _raise_breach_alert(bs, root, rel_path, entry, offending_session_id):
    """Open a writable Store, raise ONE alert row, close it. Never called
    pre-consent (both entrypoints gate before this is reachable) and never
    called for the fence's own owner (the caller filters that first)."""
    # C-02: SAY IT FIRST, then try to record it. This line used to be printed
    # only AFTER store.raise_alert returned, so a detection that could not be
    # recorded (a store that has just been deleted, a disk that is full) was
    # announced by nothing at all except a generic FAILING OPEN line further
    # up the stack. Detection must be audible even when recording fails.
    _warn("bm_bash_audit: DETECTED a Bash write across a fence: %s changed "
          "and the session that ran the command does not own that fence."
          % bs.mask_absolute_paths(bs.redact_text(rel_path)))
    message = _breach_message(bs, rel_path, entry, offending_session_id)
    alert = {
        "alert_id": uuid.uuid4().hex,
        "severity": ALERT_SEVERITY,
        "category": ALERT_CATEGORY,
        "message": message,
        "why_it_matters": (
            "one writer per file is BrotherMode's headline promise, and a "
            "Bash write bypasses the PreToolUse fence entirely, so this is "
            "the only place that promise can still be checked for a "
            "command run outside Edit, Write, MultiEdit or NotebookEdit."),
        "recommended_action": (
            "review the change to the path named above, confirm it was "
            "intended, and if the fence should move, adopt it deliberately "
            "with `bm_store.py adopt ... --adopt-from-live-session` rather "
            "than leaving it crossed silently."),
        "requires_human": True,
        "created_at": bs.now_iso(),
        "resolved_at": None,
    }
    actor = {
        "actor_type": "hook",
        "actor_name": "bm_bash_audit",
        "session_id": offending_session_id,
    }
    store = bs.Store(root, create=False)
    try:
        project_id = _alert_project_id(bs, store, actor)
        store.raise_alert(alert, project_id, actor)
    finally:
        store.close()
    safe_path = bs.mask_absolute_paths(bs.redact_text(rel_path))
    _warn("bm_bash_audit: a Bash write outside its fence changed %s; a "
         "high-severity fence-breach alert was raised and needs a human."
         % safe_path)


# ---------------------------------------------------------------------------
# PreToolUse: snapshot every fenced, currently-existing file.
# ---------------------------------------------------------------------------

def _run_pre(payload):
    if not isinstance(payload, dict):
        raise _FailOpen("hook payload was not a JSON object")
    tool_name = payload.get("tool_name")
    if not isinstance(tool_name, str) or tool_name != "Bash":
        return  # matcher already restricts this; defensive re-check only
    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not session_id.strip():
        raise _FailOpen("hook payload carried no session_id")
    session_id = session_id.strip()
    tool_use_id = payload.get("tool_use_id")
    tool_use_id = tool_use_id.strip() if isinstance(tool_use_id, str) else ""

    bs = _load_store_module()
    if bs is None:
        raise _FailOpen("bm_store.py could not be imported (%s)" % _BS_ERROR)
    fh = _load_fence_hook_module()
    if fh is None:
        raise _FailOpen("bm_fence_hook.py could not be imported (%s)" % _FH_ERROR)

    cwd = payload.get("cwd")
    cwd = cwd if isinstance(cwd, str) and cwd.strip() else None
    root, _source = bs.resolve_root(cwd, env_must_contain_start=True)
    if root is None:
        raise _FailOpen("no BrotherMode project root found from %s"
                        % (cwd or os.getcwd()))

    # A4: reap anything orphaned from an earlier call before doing this
    # call's own work. Best effort, never fatal to this pre phase.
    _reap_stale_snapshots(root, bs)

    if not os.path.isfile(bs.store_path(root)):
        raise _FailOpen("no store at %s; nothing to snapshot" % bs.store_path(root))

    try:
        rows = fh.active_claims(root)
    except Exception as e:
        raise _FailOpen("could not read active claims (%s: %s)"
                        % (type(e).__name__, e))
    if not rows:
        raise _FailOpen("the store holds no active claims; nothing is fenced")

    entries = []
    for row in rows:
        e = _entry_for_claim(root, row.get("path"), row)
        if e is not None:
            entries.append(e)
    if not entries:
        raise _FailOpen("no active claim resolved to an existing file")

    snapshot = {
        "schema": 2,
        "session_id": session_id,
        "tool_use_id": tool_use_id,
        "root": root,
        "created_at": bs.now_iso(),
        "entries": entries,
        # C-02: the enforcement state itself, which is not a claimed path and
        # so was never looked at before. schema goes 1 to 2 for this; a
        # schema-1 snapshot written by an older copy simply carries no
        # "control" key, and _control_findings returns nothing for it.
        "control": _control_state(root, bs, fh),
    }
    _ensure_snapshot_dir(root, bs)
    spath = snapshot_path(root, bs, session_id, tool_use_id)
    if bs is not None:
        bs._refuse_if_symlink_escape(spath)
    _write_json_atomic(bs, spath, snapshot)


def cmd_pre(argv):
    # C-02, the REFUSE half, and it runs FIRST, before the consent gate, on
    # purpose. It reads nothing but the payload the harness already handed
    # us, opens no file and writes nothing, so the pre-consent rule above
    # still holds, and an operator who set BM_FENCE_MODE=enforced asked to
    # be refused rather than to be refused only once setup happens to have
    # been run.
    #
    # SCOPED TO A PROJECT, and that scoping is the whole correction. An
    # earlier draft of this block refused BEFORE resolving a project root.
    # This hook installs at USER-GLOBAL scope, so that draft reached into
    # every directory on this machine, and its blanket except would have
    # refused EVERY Bash command anywhere bm_store.py failed to import.
    # That is the same defect already corrected once in bm_fence_hook.py.
    # Outside a BrotherMode project this block now does nothing at all.
    payload, err = _read_stdin_json()
    _rs = _load_repo_scope_module()
    if err is None and _rs is not None and _rs.hooks_off(payload=payload):
        return 0
    if err is None and _enforced() and isinstance(payload, dict):
        bs = _load_store_module()
        root = None
        if bs is not None:
            cwd = payload.get("cwd")
            cwd = cwd if isinstance(cwd, str) and cwd.strip() else None
            try:
                root, _source = bs.resolve_root(cwd, env_must_contain_start=True)
            except Exception:
                root = None
        if root is not None:
            try:
                tool_input = payload.get("tool_input")
                command_text = (tool_input or {}).get("command") \
                    if isinstance(tool_input, dict) else None
                code, labels, names = refusal_for(command_text, bs)
                if code is not None:
                    _refuse(code, labels, names)
                    return 0
            except Exception as e:
                # Inside a known project, enforced mode inverts the usual
                # judgement the way bm_fence_hook.py's blanket catch does:
                # somebody who asked for fail-closed would rather be stopped
                # by a bug here than have a store-destroying command waved
                # through unexamined. Outside a project we never get here.
                _refuse("store-destruction",
                        ["an internal error in the refusal check (%s)"
                         % type(e).__name__], [])
                return 0
    if not _consented():
        _warn(_CONSENT_REQUIRED_LINE)
        return 0
    try:
        if err is not None:
            raise _FailOpen(err)
        _run_pre(payload)
    except _FailOpen as e:
        _warn("bm_bash_audit: FAILING OPEN before the Bash call, nothing "
             "was snapshotted. Reason: %s" % e)
    except Exception as e:
        _warn("bm_bash_audit: FAILING OPEN before the Bash call after an "
             "unexpected error, nothing was snapshotted. Reason: %s: %s"
             % (type(e).__name__, e))
    return 0


# ---------------------------------------------------------------------------
# PostToolUse: re-hash the snapshot, raise an alert for every fenced path a
# non-owning session changed.
# ---------------------------------------------------------------------------

def _run_post(payload):
    if not isinstance(payload, dict):
        raise _FailOpen("hook payload was not a JSON object")
    tool_name = payload.get("tool_name")
    if not isinstance(tool_name, str) or tool_name != "Bash":
        return
    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not session_id.strip():
        raise _FailOpen("hook payload carried no session_id")
    session_id = session_id.strip()
    tool_use_id = payload.get("tool_use_id")
    tool_use_id = tool_use_id.strip() if isinstance(tool_use_id, str) else ""

    bs = _load_store_module()
    if bs is None:
        raise _FailOpen("bm_store.py could not be imported (%s)" % _BS_ERROR)
    fh = _load_fence_hook_module()
    if fh is None:
        raise _FailOpen("bm_fence_hook.py could not be imported (%s)" % _FH_ERROR)

    cwd = payload.get("cwd")
    cwd = cwd if isinstance(cwd, str) and cwd.strip() else None
    root, _source = bs.resolve_root(cwd, env_must_contain_start=True)
    if root is None:
        raise _FailOpen("no BrotherMode project root found from %s"
                        % (cwd or os.getcwd()))

    spath = snapshot_path(root, bs, session_id, tool_use_id)
    snapshot = _load_snapshot(spath)

    # C-02: the enforcement state is checked FIRST, before ownership is even
    # derived, because a command that destroyed the store is exactly the
    # command after which deriving anything from the store is pointless, and
    # because this half must be audible whatever else fails below.
    for code in _control_findings(root, bs, fh, snapshot.get("control")):
        _warn("bm_bash_audit: DETECTED a change to BrotherMode's own "
              "enforcement state during a Bash call: %s. Enforcement state "
              "is not a fenced file, so no hook refused this."
              % CONTROL_FINDINGS[code])
        if os.path.isfile(bs.store_path(root)):
            try:
                _raise_control_alert(bs, root, code, session_id)
                _warn("bm_bash_audit: a high-severity fence-control-loss "
                      "alert was raised and needs a human.")
            except Exception as e:
                _warn("bm_bash_audit: that finding could NOT be recorded as "
                      "an alert (%s: %s); the line above is the only record."
                      % (type(e).__name__, e))
        else:
            _warn("bm_bash_audit: that finding could NOT be recorded as an "
                  "alert, because the store that would hold it is the thing "
                  "that went missing; the line above is the only record. "
                  "Restore it from git or from an autosave snapshot, then "
                  "run `python3 scripts/doctor.py`.")

    try:
        my_label = fh.session_label(root, session_id)
    except Exception as e:
        raise _FailOpen("this session's fence label could not be derived "
                        "(%s: %s)" % (type(e).__name__, e))

    breaches = []
    for entry in snapshot.get("entries", []):
        rel_path = entry.get("path")
        if not isinstance(rel_path, str) or not rel_path.strip():
            continue
        owner = entry.get("owner_session_id") or ""
        if owner == my_label:
            continue  # the fence's own owner made this change
        abs_path = os.path.join(root, rel_path.replace("/", os.sep))
        changed = False
        try:
            if not os.path.isfile(abs_path):
                changed = True  # deleted: a content change too
            else:
                st = os.stat(abs_path)
                if st.st_size != entry.get("size"):
                    changed = True
                else:
                    changed = _sha256_file(abs_path) != entry.get("sha256")
        except OSError:  # sbe: allow-silent cannot verify; best effort, not counted as a breach
            continue
        if changed:
            breaches.append((rel_path, entry))

    if not breaches:
        # A3 fix (loop6 refuter finding): the snapshot is removed only once
        # the comparison it exists for has actually completed. This used to
        # be removed unconditionally right after _load_snapshot, BEFORE this
        # store-existence check below, so a post phase that could not reach
        # the store lost its baseline with no way to retry. Every early
        # return and every raised _FailOpen above and below this point must
        # leave the snapshot exactly where it was.
        _remove_snapshot_best_effort(spath)
        return

    if not os.path.isfile(bs.store_path(root)):
        raise _FailOpen("no store at %s; %d breach(es) found but could not "
                        "be recorded" % (bs.store_path(root), len(breaches)))
    for rel_path, entry in breaches:
        _raise_breach_alert(bs, root, rel_path, entry, session_id)
    # Every breach was recorded successfully: this snapshot's job is done.
    _remove_snapshot_best_effort(spath)


def cmd_post(argv):
    if not _consented():
        _warn(_CONSENT_REQUIRED_LINE)
        return 0
    payload, err = _read_stdin_json()
    _rs = _load_repo_scope_module()
    if err is None and _rs is not None and _rs.hooks_off(payload=payload):
        return 0
    try:
        if err is not None:
            raise _FailOpen(err)
        _run_post(payload)
    except _FailOpen as e:
        _warn("bm_bash_audit: FAILING OPEN after the Bash call, no fenced "
             "path could be checked. Reason: %s" % e)
    except Exception as e:
        _warn("bm_bash_audit: FAILING OPEN after the Bash call after an "
             "unexpected error, no fenced path could be checked. "
             "Reason: %s: %s" % (type(e).__name__, e))
    return 0


_COMMANDS = {
    "pre": cmd_pre,
    "post": cmd_post,
}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in _COMMANDS:
        return _COMMANDS[argv[0]](argv[1:])
    _warn("bm_bash_audit: usage: bm_bash_audit.py pre|post (invoked as a "
         "PreToolUse/PostToolUse hook with a JSON payload on stdin)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
