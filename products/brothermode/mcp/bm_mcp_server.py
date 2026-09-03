#!/usr/bin/env python3
"""bm_mcp_server.py: a read-only Model Context Protocol server for one
BrotherMode project's store.

HOW "NEVER WRITES" IS ENFORCED, STRUCTURALLY, NOT BY PROMISE (BLOCKER 1,
final-blockers spec 2026-07-26, VERIFIED BY ORCHESTRATOR): a single
bm_status call against a store whose header bytes had been corrupted MOVED
the founder's store.sqlite3 aside into a quarantine directory and reported
isError: false, because bm_store.ReadOnlyStore's own corruption handling
(the right call for a WRITER, catastrophic for a READER) ran directly
against the real file; even the healthy path created -shm/-wal sidecars
that were not there a moment before. The fix removes the founder's real
files from the blast radius entirely rather than trusting any code path
inside bm_store.py to behave: every tool below first copies
`.brothermode/` and `STATE.md` from the real project root into a fresh,
private temporary directory (`_snapshot_for_reading`), runs every
bm_store.py call against THAT COPY, and deletes the copy before returning.
Whatever bm_store.py's own corruption handling, schema checks, or sidecar
creation do, including quarantining a file it decides is unreadable, they
now do it to bytes that are not the founder's, inside a directory this
file made and erases on every call, success or failure alike (and, when a
removal genuinely fails, says so on standard error naming the directory it
could not remove, rather than leaving an unannounced copy of a store on
disk behind an `ignore_errors=True`: FINDING 3c, 2026-07-27).

COPY-FIRST IS NOT, BY ITSELF, CONTAINMENT (FINDING 3, HIGH, cross-project
disclosure, EXECUTED AND CONFIRMED 2026-07-27, not theorised). The copy
above closed a write hazard and opened a read one. Root resolution probed
for the `.brothermode` marker with `os.path.isdir`, which FOLLOWS a
directory symlink, and the copy used `shutil.copytree` at its default
`symlinks=False`, which DEREFERENCES. So with project-b/.brothermode
symlinked at project-a/.brothermode, a call naming ONLY project B returned
project A's records and fences with `isError: false`: bm_active_work
answered with A's active record and objective, bm_fences with A's claimed
path. `bm_store.ReadOnlyStore`'s own containment checks could never fire,
because by the time they ran they were pointed at the SNAPSHOT, whose
`.brothermode` was by then a real, already-dereferenced copy of A's. The
fix applies this project's own containment checks (`bm_store.
_refuse_if_symlink_escape` and `bm_store._refuse_if_hardlinked`, imported,
never reimplemented) to the root the CALLER named, before anything is
probed or copied, and refuses outright rather than copying carefully:
every marker probe here now goes through `os.lstat`, never
`os.path.isdir`, and every source inode this file copies must live
literally beneath the requested root.

A read-only sqlite URI (mode=ro or immutable=1) was considered and
rejected on purpose, not merely left undone: bm_store.py's own GATE A
(fix-round 6, 2026-07-26) already found and closed a real defect where a
project path containing a '%' character was silently misread as a
DIFFERENT file, because sqlite's URI filename grammar percent-decodes the
whole path, not only its query string. Reusing a URI here to solve a
different problem risks reopening that exact class; copying the file
first avoids URIs altogether, which is why bm_store.py's own read-only
connection helper does the same.

Every database access in this file still goes through exactly two entry
points from `tools/bm_store.py`: `bm_store.ReadOnlyStore` (which refuses to
create a store that does not already exist, and enforces `PRAGMA
query_only=ON` on its connection) and `bm_store.verify()` (which itself
only opens a `ReadOnlyStore`) -- now always against the private copy
`_snapshot_for_reading` builds, never against the path a caller named.
This file adds no new query logic beyond simple in-memory filtering of
what `ReadOnlyStore.dump()` already returns, and calls no `Store` (the
writable class) anywhere; the one piece of machinery this file owns and
bm_store.py does not is the copy-then-discard snapshot itself.

WHAT THIS SERVER ANSWERS, matching the four questions ratified for the
read-only project server: what work is active, what fences (claimed file
paths) are live, what decisions were recorded, and whether the store is
healthy. Four tools, one each: bm_status, bm_active_work, bm_fences,
bm_decisions.

HOW MUCH OF EACH ANSWER IS ACTUALLY LEGIBLE, stated here because two of the
four questions are now answered structurally rather than verbatim (LOOP 11
plus its fix round). Rows arrive from `ReadOnlyStore.dump()` already carrying
the central withholding policy, `bm_store.export_column`. A record name, a
tier and a CLAIMED PATH are scrub-only, so they read as themselves: bm_fences
still names the fenced file, which is the whole point of a fence primitive
(the fix round restored this after claims.path was briefly withheld, leaving
a second agent unable to see which file it must not touch). An objective, a
decision's TOPIC and a decision's TEXT are founder prose and are WITHHELD:
bm_decisions therefore reports that a decision exists, when, and under which
record, but not what it said. That is deliberate, and a client that needs the
words reads the store itself with `bm_store.py dump --raw` on the machine
that owns it.

Every founder-typed value this server returns -- including
every `bm_store.verify()` problem line, which is a raw invariant-checker
string with no redaction of its own -- also passes through
`bm_store._protect_text`, the exact function `tools/bm_store.py`'s own CLI
uses for its output (GATE 3, final-blockers spec 2026-07-26, VERIFIED BY
ORCHESTRATOR: a `verify()` problem naming a secret-shaped record reached a
client here completely unredacted while the CLI showed `[REDACTED]` for
the identical value, because `verify()` is an invariant checker, not a
rendering funnel, and this file used to print its problem strings
straight through with no funnel of its own).

project_root is REQUIRED and MUST be an absolute path (GATE 4, final-
blockers spec 2026-07-26, VERIFIED BY ORCHESTRATOR: a relative
project_root used to resolve against THIS SERVER PROCESS's own working
directory, which a caller neither sets nor sees, and the BROTHERMODE_ROOT
environment variable silently overrode an explicit argument outright, so a
call naming one project answered with a different project's record and no
sign anything had substituted). This server therefore never calls
`bm_store.resolve_root` (which checks BROTHERMODE_ROOT first): it walks up
from the given absolute path for a `.brothermode` marker, then a `.git`
directory, reusing `bm_store._walk_up` (a private but pure helper) rather
than reimplementing tree-walking a second time, and every tool names the
project root it actually resolved in its own first line of output, so a
substitution can never be silent.

HONESTY ABOUT SCOPE. This implements the subset of the Model Context
Protocol (2025-06-18) a read-only query tool over stdio actually needs:
the `initialize` handshake, the `notifications/initialized` notification,
`ping`, `tools/list`, and `tools/call`. It does NOT implement resources,
prompts, sampling, elicitation, roots, pagination cursors, or any
`listChanged` notification (the tool list here is fixed at process start
and never changes, so there is nothing to notify about). This is a
deliberate, minimal cut, not an accidental gap; `mcp/README.md` says so
again and states plainly what has and has not been verified end to end
with a real MCP client. It also does not implement a crash-safe snapshot:
copying `.brothermode/` while another process is actively writing to it
(WAL mode) is a best-effort "hot copy", not a transactionally guaranteed
one; a snapshot taken mid-write can itself read back as busy or corrupted
on the COPY, which this server reports honestly rather than retrying
silently, and which never touches the real store either way.

Standard library only: json, os, stat, sys, shutil, tempfile, contextlib,
importlib.util. No MCP SDK, no network library, no subprocess. The stdio
transport this implements is exactly what the spec defines it to be:
newline-delimited JSON-RPC 2.0 messages on stdin/stdout, UTF-8, one message
per line, nothing else ever written to stdout. Confirmed against
https://modelcontextprotocol.io/specification/2025-06-18/basic/transports
and the adjoining lifecycle and tools pages on 2026-07-26; this header
records that check rather than asking a reader to trust an unstated memory
of the spec.
"""

import contextlib
import importlib.util
import json
import os
import shutil
import stat
import sys
import tempfile

PROTOCOL_VERSION = "2025-06-18"
SERVER_NAME = "brothermode-project-readonly"
SERVER_VERSION = "0.1.0"  # this server's own version, independent of VERSION
# at the repository root: it was built today, has not been adversarially
# reviewed the way the rest of this project has, and a shared version
# number would overstate how proven it is.

_HERE = os.path.dirname(os.path.abspath(__file__))
_BM_STORE_PATH = os.path.normpath(os.path.join(_HERE, "..", "tools", "bm_store.py"))


def _load_bm_store():
    """Dynamic load by path, mirroring bm_store.py's own `_load_redact` /
    `_load_atomic_write` pattern (same file, search for either name) rather
    than inventing a new loading convention: this project's established way
    of pulling in a sibling tool module regardless of the caller's cwd or
    whether `tools/` is on sys.path."""
    try:
        spec = importlib.util.spec_from_file_location("bm_store_for_mcp", _BM_STORE_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod, None
    except Exception as e:
        return None, repr(e)


bm_store, _BM_STORE_LOAD_ERROR = _load_bm_store()


class ToolError(Exception):
    """A tool-level failure to report back as isError: true (bad input, no
    project found, store unreadable), distinct from a protocol-level
    JSON-RPC error (unknown method, unknown tool name, malformed request)."""


def _raw_write(stream, text):
    """The ONE place any stream in this module is actually written to,
    named and shaped the same way tools/bm_store.py's own lowest-level
    primitive is: a stray direct write (a bare print call, or writing
    straight to standard out or standard error by name) anywhere else in
    this file is now a defect a shared structural scan in tools/test_bm.py
    catches (GATE 3, final-blockers spec 2026-07-26), the same scan that
    already covers tools/bm_store.py. Writing through a generically named
    `stream` parameter here, rather than the literal stdout/stderr object
    by name, is the one shape that scan recognizes as the approved bypass,
    matching bm_store.py's own convention exactly so one scan works,
    unmodified, across both files."""
    stream.write(text)
    stream.flush()


def _log(message):
    """Every diagnostic line goes to standard error, never standard out:
    the protocol requires nothing but a valid MCP message ever reach
    standard out, and a stray direct write would corrupt the stream for
    every message after it. Routed through `_raw_write`, this module's own
    named primitive, exactly like `_send` below."""
    _raw_write(sys.stderr, "bm_mcp_server: %s\n" % message)


def _require_bm_store():
    if bm_store is None:
        raise ToolError(
            "tools/bm_store.py could not be loaded (%s); this server has "
            "nothing to query without it." % _BM_STORE_LOAD_ERROR)


def _path_kind(path):
    """What is literally AT path, deciding with os.lstat and never with
    os.path.isdir/isfile (FINDING 3): both of those follow a symlink, so
    they answer about the TARGET, and a marker that is really a link into
    another project reads back as an ordinary directory of this one.
    Returns one of "absent", "symlink", "dir", "file", "other"; "symlink"
    wins over every other answer, because that is the case the callers
    below exist to refuse."""
    try:
        st = os.lstat(path)
    except OSError:
        return "absent"
    if stat.S_ISLNK(st.st_mode):
        return "symlink"
    if stat.S_ISDIR(st.st_mode):
        return "dir"
    if stat.S_ISREG(st.st_mode):
        return "file"
    return "other"


def _uncontained_message(path, root, what):
    """The one refusal wording for a source path that does not live where
    it claims to. Names the offending path and the project root the caller
    asked about, and deliberately NOT what the path resolves to: that
    target is, in the very case this refusal exists for, another project's
    directory, and naming it in a client-visible response would hand back a
    smaller piece of exactly the cross-project information the refusal is
    denying."""
    return (
        "refusing to read %s: it is %s, so it does not live beneath the "
        "project root %s that this call named. This server answers about "
        "one project, the one the caller named, and copies only inodes "
        "that are literally inside it: following a link out of the "
        "requested root is how a call naming one project came back with "
        "another project's records and fences (FINDING 3, 2026-07-27). "
        "The path this resolves to is deliberately not named here. Inspect "
        "it yourself with `ls -l %s`, and if the link is intentional, "
        "point this call at the project that really owns the store."
        % (path, what, root, path))


def _refuse_symlink_escape(path, root):
    """bm_store._refuse_if_symlink_escape (imported, never reimplemented:
    a second copy of a containment check is how one file's guard gets
    escaped by the next file), translated into this server's own
    ToolError so it reaches a client as isError: true with a message that
    does not name the escape target."""
    try:
        bm_store._refuse_if_symlink_escape(path)
    except bm_store.OwnershipRefused:
        _log("containment refusal: %s does not resolve to itself" % path)
        raise ToolError(_uncontained_message(path, root, "a symlink"))


def _refuse_hardlink(path, root):
    """bm_store._refuse_if_hardlinked, same translation. A hardlink is
    invisible to the symlink check above (realpath still equals the path),
    yet the same bytes are reachable, and copyable, through another name."""
    try:
        bm_store._refuse_if_hardlinked(path)
    except bm_store.OwnershipRefused:
        _log("containment refusal: %s is reachable through more than one "
             "name" % path)
        raise ToolError(_uncontained_message(
            path, root, "reachable through more than one name (a hard link)"))


_SIDECAR_SUFFIXES = ("", "-wal", "-shm")


def _refuse_uncontained_sources(root):
    """Apply THIS PROJECT'S OWN containment checks to the root a CALLER
    named, before anything under it is probed, opened, or copied (FINDING
    3, HIGH, executed 2026-07-27).

    bm_store.ReadOnlyStore already runs these exact checks, and they could
    never fire here: this server hands ReadOnlyStore a SNAPSHOT root, and
    the snapshot's .brothermode is by then a real, already-dereferenced
    copy of whatever the link pointed at. Nothing is wrong with it by the
    time it is checked, because the escape happened during the copy. So
    the checks have to run against the path the caller actually named,
    which is what this function is for.

    Refusing is preferred over copying carefully (shutil.copytree with
    symlinks=True would preserve the link instead of dereferencing it) for
    two reasons. First, a preserved dangling link inside a snapshot is
    still a path this server would then open, and a link that happens to
    resolve to a readable file once the snapshot is on disk reopens the
    same hole from a different direction. Second, a refusal is legible: a
    founder who genuinely shares one store between two checkouts is told
    which path is a link and can name the owning project instead, whereas
    a careful copy silently answers about whatever the link resolved to."""
    _require_bm_store()
    store_dir_src = bm_store.store_dir(root)
    store_file = bm_store.store_path(root)
    _refuse_symlink_escape(store_dir_src, root)
    for suffix in _SIDECAR_SUFFIXES:
        _refuse_symlink_escape(store_file + suffix, root)
    # STATE.md is copied too, so it is a source inode like any other.
    _refuse_symlink_escape(os.path.join(root, "STATE.md"), root)
    for suffix in _SIDECAR_SUFFIXES:
        _refuse_hardlink(store_file + suffix, root)
    if _path_kind(store_dir_src) != "dir":
        return
    # Every OTHER entry beneath .brothermode as well, not only the store
    # file and its sidecars this server knows about by name: copytree
    # copies the whole directory, so any single symlinked entry anywhere
    # under it is an inode outside the requested root being copied into a
    # snapshot this server then reads. followlinks=False so the walk
    # itself never descends through one.
    for dirpath, dirnames, filenames in os.walk(store_dir_src, followlinks=False):
        for entry in list(dirnames) + list(filenames):
            _refuse_symlink_escape(os.path.join(dirpath, entry), root)


def _resolve_root(arguments):
    """Resolve a project root WITHOUT ever consulting BROTHERMODE_ROOT and
    WITHOUT ever joining a relative path to this SERVER PROCESS's own cwd
    (GATE 4, final-blockers spec 2026-07-26, VERIFIED BY ORCHESTRATOR: the
    old version did both, so a relative project_root silently resolved
    against wherever the MCP client happened to launch this process from,
    and an ambient BROTHERMODE_ROOT overrode an explicit argument outright;
    a call naming one project answered with a different project's record,
    with no sign anything had substituted). project_root is a required,
    ABSOLUTE argument on every tool here: a relative path is refused
    outright rather than silently anchored to an ambient directory the
    caller does not control. Walks up from the given path for the nearest
    `.brothermode` marker, then the nearest `.git`, reusing
    `bm_store._walk_up` (a private but pure helper, no state) rather than
    reimplementing tree-walking a second time; never `bm_store.resolve_root`
    itself, since that function checks BROTHERMODE_ROOT before anything
    else and this server must never let an environment variable a caller
    cannot see from here override an argument the caller explicitly gave."""
    _require_bm_store()
    project_root = arguments.get("project_root")
    if not project_root or not isinstance(project_root, str):
        raise ToolError("project_root (a non-empty string path) is required")
    expanded = os.path.expanduser(project_root)
    if not os.path.isabs(expanded):
        raise ToolError(
            "project_root must be an absolute path; got the relative path "
            "%r. Resolving a relative path against this server process's "
            "own working directory, which the caller neither sets nor "
            "sees, is exactly the ambiguity that once made a call naming "
            "one project return a different project's record (GATE 4, "
            "final-blockers spec 2026-07-26)." % project_root)
    start = os.path.realpath(expanded)
    chain = bm_store._walk_up(start)
    for d in chain:
        marker = os.path.join(d, ".brothermode")
        kind = _path_kind(marker)
        if kind == "symlink":
            # FINDING 3: os.path.isdir, which this loop used to call here,
            # FOLLOWS a directory symlink, so a .brothermode symlinked at
            # another project's .brothermode read as a perfectly ordinary
            # marker and this server answered a call naming THIS project
            # with THAT project's records. Refused outright, and refused
            # HERE, before the marker is ever handed to anything that
            # copies or opens it.
            _log("refusing project root %s: its .brothermode marker is a "
                 "symlink" % d)
            raise ToolError(_uncontained_message(marker, d, "a symlink"))
        if kind == "dir":
            return d, "marker"
    for d in chain:
        # .git is a directory in a normal clone and a FILE in a worktree;
        # os.path.exists covers both, matching bm_store.resolve_root's own
        # check exactly.
        if os.path.exists(os.path.join(d, ".git")):
            return d, "git"
    raise ToolError(
        "no BrotherMode project found at or above %r (checked for a "
        ".brothermode marker, then a .git directory, walking up from "
        "there). The BROTHERMODE_ROOT environment variable is deliberately "
        "never consulted by this server: project_root is a required "
        "argument precisely so a call is never silently answered about a "
        "different project than the one it named." % project_root)


@contextlib.contextmanager
def _snapshot_for_reading(root):
    """Yield a private, throwaway COPY of everything a read might need
    (`.brothermode/` and `STATE.md`) from `root`, so every tool below
    operates on the copy and never on the founder's real files (BLOCKER 1,
    see the module docstring for the full reproduction and reasoning). The
    copy is made with plain, read-only filesystem calls against `root`
    (`shutil.copytree`/`shutil.copy2`, never a write), and removal is
    attempted in the `finally` below, success or failure alike, so in the
    ordinary case nothing this context manager does leaves a trace next to
    `root` or inside the caller's own process temp directory once a tool
    call finishes. A removal that genuinely FAILS is reported, never
    swallowed: see `_remove_snapshot`.

    Every source inode copied here must live literally beneath `root`,
    which `_refuse_uncontained_sources` establishes before the temporary
    directory is even created (FINDING 3): copying first and checking the
    copy is precisely what let another project's store be dereferenced
    into a snapshot and read back as this project's."""
    _refuse_uncontained_sources(root)
    store_dir_src = bm_store.store_dir(root)
    state_src = os.path.join(root, "STATE.md")
    snapshot_root = tempfile.mkdtemp(prefix="bm_mcp_readonly_snapshot_")
    try:
        if _path_kind(store_dir_src) == "dir":
            # symlinks=True, so even if a link somehow reached this line it
            # would be copied AS a link rather than dereferenced into a copy
            # of its target. The refusal above is the real guard; this is
            # the second of two, because copytree's DEFAULT (symlinks=False,
            # dereference) is the exact default that caused FINDING 3.
            shutil.copytree(store_dir_src, bm_store.store_dir(snapshot_root),
                            symlinks=True)
        if _path_kind(state_src) == "file":
            shutil.copy2(state_src, os.path.join(snapshot_root, "STATE.md"))
        yield snapshot_root
    finally:
        _remove_snapshot(snapshot_root)


def _remove_snapshot(snapshot_root):
    """Delete the snapshot, and SAY SO when that fails (FINDING 3c). This
    used to be `shutil.rmtree(..., ignore_errors=True)` directly under a
    docstring promising that nothing this context manager does ever leaves
    a trace. A partially failed removal breaks that promise silently, and
    what it leaves behind is not a harmless temp directory: it is a
    complete copy of a project's store, sitting somewhere nobody is
    watching, that no later call will ever revisit. Reported through this
    module's own logging path, naming the directory, so an operator can
    remove it; not raised, because this runs in a `finally` and an
    exception here would replace whatever the tool was actually about to
    report with an unrelated failure."""
    try:
        shutil.rmtree(snapshot_root)
    except OSError as e:
        _log("FAILED to remove the temporary snapshot directory %s (%s). It "
             "holds a COPY OF A PROJECT'S STORE and it is still on disk. "
             "Remove it by hand." % (snapshot_root, e))


def _protect(text):
    """Redact then sanitize ONE line of founder-controlled text exactly
    the way tools/bm_store.py's own output funnel does (GATE 3, VERIFIED
    BY ORCHESTRATOR), by calling that exact function rather than
    reimplementing redact-then-sanitize a second time: a second copy is
    precisely how "a guard that covers one file keeps being escaped by the
    next file" happens. Applied per LINE, before it joins a multi-line
    `lines` list, never to an already-assembled multi-line document (which
    would escape the document's OWN newlines as if they were injected
    control characters, a regression tools/bm_store.py already hit once).

    LOOP 11 adds the second half of the central export policy,
    `bm_store.mask_absolute_paths`. Rows already arrive withheld or scrubbed
    from `dump()`, but `bm_store.verify()` problem lines are invariant-checker
    strings that name real files, and an MCP response leaves this machine.
    The caller's own project root, which the caller itself supplied, is
    assembled into the header OUTSIDE this function and is deliberately not
    masked: telling a caller which project it just asked about is not a
    disclosure."""
    return bm_store.mask_absolute_paths(bm_store._protect_text(text))


def _safe_protect(text):
    """`_protect`, for the one caller that must never itself become the
    reason a response cannot be sent: the catch-all in
    `handle_tools_call`, which is the last line between an unexpected
    exception and the protocol, and which may be running precisely because
    `bm_store` is the thing that failed. Falls back to the caller's own
    text, which is always server-authored here and carries no founder
    data, rather than raising a second exception out of the handler that
    exists to stop the first one."""
    try:
        return _protect(text)
    except Exception:
        return text


def _dump_store(root):
    """Open read-only, dump every table with default-deny redaction
    (raw=False, the only mode this server ever uses), close. Reused by
    every tool below except bm_status, which calls bm_store.verify()
    instead since it needs invariant checks, not row contents. `root` is
    always a snapshot directory from `_snapshot_for_reading`, never the
    project root a caller named."""
    store = bm_store.ReadOnlyStore(root)
    try:
        return store.dump(raw=False)
    finally:
        store.close()


def _dump_store_or_clean_error(snap, real_root):
    """`_dump_store(snap)`, translating a corrupt or unreadable SNAPSHOT
    into a clean ToolError that never names the snapshot's own path: that
    path lives inside a temporary directory this call is about to delete
    (`_snapshot_for_reading`'s `finally`), so printing it would name
    something that no longer exists a moment later and could be misread as
    something having happened to the founder's REAL store (BLOCKER 1).
    Mirrors the same two exceptions tool_bm_status already handles this
    way; factored out so bm_active_work, bm_fences, and bm_decisions do not
    each grow their own copy of this handling."""
    try:
        return _dump_store(snap)
    except bm_store.StoreCorrupt:
        raise ToolError(
            "store at %s could not be read as a database (it appears "
            "corrupted or truncated). Nothing on disk was changed: this "
            "check reads a private, temporary copy of the store, never "
            "the store itself, and that copy has already been discarded."
            % bm_store.store_path(real_root))
    except bm_store.OwnershipRefused as e:
        raise ToolError("could not open the store: %s: %s" % (e.reason, _protect(str(e))))


# ---------------------------------------------------------------------------
# The four tools.
# ---------------------------------------------------------------------------

def tool_bm_status(arguments):
    root, source = _resolve_root(arguments)
    lines = ["project root: %s (found via %s)" % (root, source)]
    real_store_file = bm_store.store_path(root)
    with _snapshot_for_reading(root) as snap:
        if not os.path.isfile(bm_store.store_path(snap)):
            lines.append("no store exists at %s yet" % real_store_file)
            lines.append("healthy: nothing to check (no store)")
        else:
            try:
                problems = bm_store.verify(snap)
            except bm_store.OwnershipRefused as e:
                lines.append("could not open the store: %s: %s" % (e.reason, _protect(str(e))))
            except bm_store.StoreCorrupt:
                # BLOCKER 1: never name the snapshot's own quarantine path
                # here. That path lives inside a temporary directory this
                # function is about to delete, so printing it would name
                # something that no longer exists a moment later and would
                # wrongly imply the founder's OWN store had been moved,
                # exactly the false report this whole fix exists to close.
                lines.append(
                    "store at %s could not be read as a database (it "
                    "appears corrupted or truncated). Nothing on disk was "
                    "changed: this check reads a private, temporary copy "
                    "of the store, never the store itself, and that copy "
                    "has already been discarded." % real_store_file)
            else:
                if not problems:
                    lines.append("healthy: yes (0 problems)")
                else:
                    lines.append("healthy: no (%d problem(s)):" % len(problems))
                    for p in problems:
                        lines.append(_protect("  - %s" % p))
    return "\n".join(lines)


_VALID_STATES = ("active", "parked", "complete", "adopted")


def tool_bm_active_work(arguments):
    root, source = _resolve_root(arguments)
    state = arguments.get("state", "active")
    if state != "all" and state not in _VALID_STATES:
        raise ToolError(
            "state must be one of %s or 'all', got %r"
            % (_VALID_STATES, state))
    header = "project root: %s (found via %s)" % (root, source)
    with _snapshot_for_reading(root) as snap:
        if not os.path.isfile(bm_store.store_path(snap)):
            return "%s\nno store exists at %s yet" % (header, bm_store.store_path(root))
        data = _dump_store_or_clean_error(snap, root)
    records = data["records"]
    if state != "all":
        records = [r for r in records if r["state"] == state]
    if not records:
        return "%s\nno records in state %r at %s" % (header, state, root)
    records = sorted(records, key=lambda r: (r["state"], r["name"] or "", r["lifecycle_uuid"]))
    lines = [header]
    for r in records:
        lines.append(_protect(
            "- %s (%s, v%s, %s, state=%s) tier=%s objective=%s"
            % (r["name"] or "(unnamed)", r["lifecycle_uuid"][:8], r["version"],
               r["lifetime"], r["state"], r["tier"] or "(none)",
               r["objective"] or "(none)")))
    return "\n".join(lines)


def tool_bm_fences(arguments):
    root, source = _resolve_root(arguments)
    header = "project root: %s (found via %s)" % (root, source)
    with _snapshot_for_reading(root) as snap:
        if not os.path.isfile(bm_store.store_path(snap)):
            return "%s\nno store exists at %s yet" % (header, bm_store.store_path(root))
        data = _dump_store_or_clean_error(snap, root)
    active_by_uuid = {r["lifecycle_uuid"]: r for r in data["records"] if r["state"] == "active"}
    fences = [c for c in data["claims"] if c["lifecycle_uuid"] in active_by_uuid]
    if not fences:
        return "%s\nno active fences at %s" % (header, root)
    fences = sorted(fences, key=lambda c: (c["path"] or "", c["lifecycle_uuid"]))
    lines = [header]
    for c in fences:
        r = active_by_uuid[c["lifecycle_uuid"]]
        lines.append(_protect(
            "- %s claimed by %s (%s)"
            % (c["path"], r["name"] or "(unnamed)", r["lifecycle_uuid"][:8])))
    return "\n".join(lines)


def tool_bm_decisions(arguments):
    root, source = _resolve_root(arguments)
    header = "project root: %s (found via %s)" % (root, source)
    limit = arguments.get("limit", 20)
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise ToolError("limit must be an integer, got %r" % (limit,))
    if limit <= 0:
        raise ToolError("limit must be a positive integer, got %d" % limit)
    record_name = arguments.get("record_name")
    with _snapshot_for_reading(root) as snap:
        if not os.path.isfile(bm_store.store_path(snap)):
            return "%s\nno store exists at %s yet" % (header, bm_store.store_path(root))
        data = _dump_store_or_clean_error(snap, root)
        matching_uuids = None
        if record_name:
            # SOFT 6 (final-blockers spec 2026-07-26, VERIFIED BY
            # ORCHESTRATOR): filtering must compare against the record's
            # REAL name, never the already-redacted one `data` above
            # carries. Comparing a caller's real query against a redacted
            # "[REDACTED]" name is a false negative the moment a record's
            # name happens to look secret-shaped: "no decisions recorded"
            # reads as fact, indistinguishable from a project that
            # genuinely recorded none. A second, RAW dump is opened and
            # closed here, against the same snapshot, purely to compute
            # which lifecycle_uuids match; only those identifiers (never
            # secret-shaped) leave this block, and the raw names are
            # discarded immediately, never reaching `lines` below.
            try:
                raw_store = bm_store.ReadOnlyStore(snap)
                try:
                    raw_records = raw_store.dump(raw=True)["records"]
                finally:
                    raw_store.close()
            except bm_store.StoreCorrupt:
                raise ToolError(
                    "store at %s could not be read as a database (it "
                    "appears corrupted or truncated). Nothing on disk was "
                    "changed: this check reads a private, temporary copy "
                    "of the store, never the store itself, and that copy "
                    "has already been discarded." % bm_store.store_path(root))
            except bm_store.OwnershipRefused as e:
                raise ToolError("could not open the store: %s: %s" % (e.reason, _protect(str(e))))
            matching_uuids = {
                r["lifecycle_uuid"] for r in raw_records if r["name"] == record_name
            }
    by_uuid = {r["lifecycle_uuid"]: r for r in data["records"]}
    decisions = data["decisions"]
    if matching_uuids is not None:
        decisions = [d for d in decisions if d["lifecycle_uuid"] in matching_uuids]
    decisions = sorted(decisions, key=lambda d: d["created_at"], reverse=True)[:limit]
    if not decisions:
        return "%s\nno decisions recorded at %s" % (header, root)
    lines = [header]
    for d in decisions:
        r = by_uuid.get(d["lifecycle_uuid"], {})
        lines.append(_protect(
            "- [%s] %s (%s) topic=%s: %s"
            % (d["created_at"], r.get("name") or "(unknown record)",
               d["lifecycle_uuid"][:8], d["topic"] or "(no topic)",
               d["text"] or "(empty)")))
    return "\n".join(lines)


TOOLS = [
    {
        "name": "bm_status",
        "description": (
            "Report whether the BrotherMode store at a project is healthy: "
            "whether a store exists yet, bm_store.py verify() problems "
            "(an empty list means healthy), and store corruption or "
            "ownership refusals if opening it fails. Read-only: opens the "
            "store via bm_store.ReadOnlyStore, never creates one."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_root": {
                    "type": "string",
                    "description": (
                        "An ABSOLUTE path at or inside the BrotherMode "
                        "project to check. A relative path is refused "
                        "outright, never resolved against this server "
                        "process's own working directory. Walks up from "
                        "this path for the nearest .brothermode marker, "
                        "then the nearest .git; the BROTHERMODE_ROOT "
                        "environment variable is never consulted (GATE 4, "
                        "final-blockers spec 2026-07-26): an explicit "
                        "project_root argument is always authoritative, so "
                        "a call naming one project can never be silently "
                        "answered about another."
                    ),
                }
            },
            "required": ["project_root"],
        },
    },
    {
        "name": "bm_active_work",
        "description": (
            "List records (fenced units of work) in a project's store, "
            "filtered by state. Every founder-typed field (name, "
            "objective, tier) passes through the same default-deny "
            "redaction bm_store.py dump uses; there is no raw/unredacted "
            "mode in this server."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_root": {
                    "type": "string",
                    "description": "Same resolution rule as bm_status.",
                },
                "state": {
                    "type": "string",
                    "enum": ["active", "parked", "complete", "adopted", "all"],
                    "description": "Which state to list. Defaults to active.",
                },
            },
            "required": ["project_root"],
        },
    },
    {
        "name": "bm_fences",
        "description": (
            "List the file paths currently fenced by active records (the "
            "one-writer-per-path boundary while a record is active). Paths "
            "pass through the same redaction as bm_store.py dump."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_root": {
                    "type": "string",
                    "description": "Same resolution rule as bm_status.",
                }
            },
            "required": ["project_root"],
        },
    },
    {
        "name": "bm_decisions",
        "description": (
            "List recorded decisions (topic and text), most recent first. "
            "Topic and text pass through the same redaction as bm_store.py "
            "dump."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_root": {
                    "type": "string",
                    "description": "Same resolution rule as bm_status.",
                },
                "record_name": {
                    "type": "string",
                    "description": (
                        "Optional: only decisions belonging to the record "
                        "with exactly this pre-redaction name."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of decisions to return. Defaults to 20.",
                },
            },
            "required": ["project_root"],
        },
    },
]

TOOL_HANDLERS = {
    "bm_status": tool_bm_status,
    "bm_active_work": tool_bm_active_work,
    "bm_fences": tool_bm_fences,
    "bm_decisions": tool_bm_decisions,
}


# ---------------------------------------------------------------------------
# JSON-RPC 2.0 over stdio: newline-delimited, UTF-8, one message per line,
# nothing else ever written to stdout (spec requirement, restated in the
# module docstring). json.dumps() without indent= never emits a literal
# embedded newline inside the line it produces (control characters inside
# string values are escaped, e.g. "\n"), which is what "messages MUST NOT
# contain embedded newlines" requires.
# ---------------------------------------------------------------------------

def _send(obj):
    _raw_write(sys.stdout, json.dumps(obj, sort_keys=True) + "\n")


def _result(id_, result):
    return {"jsonrpc": "2.0", "id": id_, "result": result}


def _error(id_, code, message, data=None):
    err = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": id_, "error": err}


def handle_initialize(msg):
    params = msg.get("params") or {}
    requested = params.get("protocolVersion")
    # This server supports exactly one protocol version. Per spec: if the
    # server supports the requested version, echo it back; otherwise
    # respond with a version it does support. There is only ever one
    # option here, so both branches send the same thing.
    version_to_send = PROTOCOL_VERSION
    if requested != PROTOCOL_VERSION:
        _log("client requested protocolVersion %r; this server only speaks "
             "%r and is answering with that" % (requested, PROTOCOL_VERSION))
    return _result(msg["id"], {
        "protocolVersion": version_to_send,
        "capabilities": {"tools": {}},
        "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        "instructions": (
            "Read-only BrotherMode project queries. Every tool takes a "
            "required, ABSOLUTE project_root path (a relative path is "
            "refused, and the BROTHERMODE_ROOT environment variable is "
            "never consulted, so a call can never be silently answered "
            "about a different project than the one it named). This "
            "server never writes, deletes, or transitions anything: every "
            "read runs against a private temporary copy of the store, "
            "made and discarded within the same call, so nothing this "
            "server does, including its own failure paths, ever touches "
            "the founder's real files."
        ),
    })


def handle_ping(msg):
    return _result(msg["id"], {})


def handle_tools_list(msg):
    return _result(msg["id"], {"tools": TOOLS})


def handle_tools_call(msg):
    params = msg.get("params") or {}
    name = params.get("name")
    arguments = params.get("arguments") or {}
    handler = TOOL_HANDLERS.get(name)
    if handler is None:
        return _error(msg["id"], -32602, "Unknown tool: %r" % (name,))
    try:
        text = handler(arguments)
    except ToolError as e:
        return _result(msg["id"], {
            "content": [{"type": "text", "text": str(e)}],
            "isError": True,
        })
    except Exception as e:  # a bug here must still answer the protocol, not crash the process
        # FINDING 3b: this used to return "internal error: %r" % (e,), the
        # one response in this file that reached a client without passing
        # through _protect. An exception repr is not server-authored text:
        # it can carry an absolute path, a row this server was mid-way
        # through rendering, or a founder-typed value that every other
        # response in this file is careful to redact first. The class name
        # is enough to tell a caller a bug happened here rather than a
        # refusal; the repr itself goes to this server's own log, on
        # standard error, and nowhere else.
        _log("tool %r raised an unexpected exception: %r" % (name, e))
        return _result(msg["id"], {
            "content": [{"type": "text", "text": _safe_protect(
                "internal error: this tool raised an unexpected %s. Its text "
                "is deliberately not repeated here, because an exception's "
                "own message can carry absolute paths and store contents "
                "that every other response from this server redacts first. "
                "The full detail is on this server's standard error."
                % type(e).__name__)}],
            "isError": True,
        })
    return _result(msg["id"], {
        "content": [{"type": "text", "text": text}],
        "isError": False,
    })


_METHODS = {
    "initialize": handle_initialize,
    "ping": handle_ping,
    "tools/list": handle_tools_list,
    "tools/call": handle_tools_call,
}


def _handle_line(line):
    """Parse and dispatch one JSON-RPC message. Returns the response dict
    to send, or None when nothing should be sent (a notification, or a
    known-but-response-free method)."""
    try:
        msg = json.loads(line)
    except ValueError as e:
        return _error(None, -32700, "Parse error: %s" % e)
    if not isinstance(msg, dict):
        return _error(None, -32600, "Invalid Request: top-level JSON must be an object")
    method = msg.get("method")
    has_id = "id" in msg
    if method == "notifications/initialized":
        return None  # notification: no response, nothing to initialize (stateless server)
    handler = _METHODS.get(method)
    if handler is None:
        if has_id:
            return _error(msg.get("id"), -32601, "Method not found: %r" % (method,))
        return None  # unknown notification: ignore, never respond to a notification
    if not has_id:
        # A request-shaped method arriving as a notification (no id): per
        # JSON-RPC, notifications never get a reply, even from a method
        # that would normally answer. Log it and move on.
        _log("received %r with no id (notification); no response is sent" % method)
        return None
    return handler(msg)


def main():
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        response = _handle_line(line)
        if response is not None:
            _send(response)
    return 0


if __name__ == "__main__":
    sys.exit(main())
