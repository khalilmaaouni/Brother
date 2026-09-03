"""A structured registry of who owns what, and a diff postcondition at close
that compares what ACTUALLY changed against what was declared.

The defect this exists for, in one sentence: the write fence is a PreToolUse
hook that fails open and cannot govern Bash, because shell cannot be parsed
reliably, so a writer who edits through the shell walks past the only control
standing in front of the keyboard.

The fix is not a better parser. `sbe task open` records what a writer declared
it owns, and `sbe task close` reads the git diff and refuses to close a task
whose tree changed outside that declaration. The shell is never parsed; the
diff is simply read. This is the AFTER-THE-FACT layer of a two-layer model:
`tools/sbe_fence_hook.py` stays the advisory pre-write control and is not
touched by this module, and the overlap rule is IMPORTED from it rather than
re-typed, so the fence a hook refuses over and the fence this registry refuses
over cannot drift into two different fences.

A task's identity is the pair (`change`, `id`), not `id` alone. Every derived
plan starts fresh at "T01" (`tools/sbe_plan.py`), so two different dossiers
routinely produce a task called "T01", and a registry that keyed a task by
`id` alone could not tell them apart: opening "T01" in dossier B refused
because "T01" was open in unrelated dossier A. `--change` (default the empty,
unscoped string, unchanged behavior for a bare `sbe task open`) records which
change a task belongs to, and every lookup that used to compare `id` alone
now compares the pair, EXCEPT owned-path overlap: two writers over the same
file collide regardless of which change either declares, because the file
does not care which dossier asked for it. A bare id, given with no `--change`,
still resolves unambiguously in the overwhelmingly common case of exactly one
match; when it resolves to more than one open (or, for `sbe work`, recorded)
task across different changes, that is genuine ambiguity, refused by
`AmbiguousTaskId` naming every colliding (change, id) pair rather than
guessing which one a bare id meant.

WHAT THIS DOES NOT DO, stated here because a control that oversells itself is
worse than none:

  - It cannot see an actor who never registers. Reviewer separation and the
    ownership scan order roles INSIDE the registry; a writer who never runs
    `sbe task open` is invisible to both, and only the fence hook (advisory,
    fail-open) stands in front of that writer.
  - Concurrent CALLERS who never go through `cmd_open`/`cmd_close` are out of
    scope: a caller that reads and rewrites `.sbe/tasks.json` by hand, past
    this module entirely, is not serialized by anything here, the same way a
    writer who never runs `sbe task open` is invisible to the ownership scan
    two bullets up. Both entry points this module ships now hold
    `_registry_lock` for the whole read-modify-write, the same `fcntl.flock`
    sidecar pattern `tools/sbe_telemetry.py` and `brothersbe.decisions` use,
    so two concurrent `sbe task open` (or `close`) calls can no longer both
    read the same tasks list and have the second write silently erase the
    first's addition. `save_registry` itself is still the same atomic write
    (temp file, then rename) it always was; the lock lives around it, in the
    caller, because the read has to be inside the same critical section as
    the write for a lost update to be impossible, and `save_registry` alone
    never saw the read.
  - `expiry` is informational only. Nothing deletes a task on a clock; a stale
    open task is a line a human reads in `sbe task list`, never a record the
    tool silently drops.
  - The registry file `.sbe/tasks.json` and its lock sidecar
    `.sbe/tasks.json.lock` are exempted, by exact name, from the changed-path
    comparison: opening a task writes both, so counting either would make an
    ordinary single-writer flow unable to close clean without --force, which
    is this control's own kill criterion.
  - `cmd_open` refuses, rather than silently starting a fresh empty registry,
    when `.sbe/tasks.json` is absent but `.sbe/tasks.json.lock` exists: that
    sidecar is written only from inside an `open`/`close` read-modify-write,
    so its presence with no registry file means one existed and was deleted
    rather than closed. It never repairs the missing file itself; a human
    restores it from a backup or accepts the loss and removes the sidecar
    too. One narrow gap this leaves: a call that fails validation INSIDE the
    lock before ever reaching `save_registry` (today, only a `--worktree`
    paired with a `--lease-id` that does not exist; `TestReviewerSeparation`
    used to be a second one, moved ahead of the lock above specifically to
    close it) still creates the lock sidecar as a side effect, so a
    genuinely first-ever project can, after such a failed attempt, see one
    corrected retry refused by this same check even though nothing was ever
    actually lost. Not closed here; see `docs/KNOWN-LIMITS.md`.
  - A pre-migration (schema 1.0) record carried no `change` at all, and there
    is nothing left to read that would say what dossier produced it: the
    migration (below) gives it the empty, unscoped change rather than a
    guessed one. Two records that both migrated to the empty change still
    collide by id alone, exactly as every record did before this pair
    existed; this is a stated fact about old data, never a promise that
    migrated records are disambiguated. The FLIP SIDE of the same fact, not
    stated by round 1: a migrated OPEN record's empty change also no longer
    collides with a DIFFERENT, non-empty change stamped onto a fresh open of
    the SAME id (identity is the pair now, and `''` != `'design/dossier-a'`).
    Before this pair existed, one global OPEN-by-id-alone check would have
    refused that reopen outright; now it succeeds, leaving TWO OPEN records
    for one id, and a bare `sbe task close <id>` on it becomes ambiguous.
    `--change ''` still addresses the legacy record on its own. See
    `docs/KNOWN-LIMITS.md` and `TestSchemaMigration` in `tools/test_sbe_tasks.py`.
  - `sbe work start`'s branch name already carries the change
    (`sbe/<change>/<taskId>`, `src/brothersbe/work.py`), so two dossiers'
    same-named task never collide on the branch. Its WORKTREE directory does
    not: `<repo>-sbe-<taskId>` is keyed by task id alone, so starting the
    same task id from two different dossiers AT THE SAME TIME still collides
    at that path even though this registry now permits both records open.
    That is a filesystem-layout gap, not a registry identity gap, and it is
    not closed here; see `docs/KNOWN-LIMITS.md`.

Python floor is 3.9: no match statements, no `X | Y` annotations. Standard
library only. Maturity: INTERNAL-EVAL, exercised on this repository's fixtures
and on no other estate.
"""
import argparse
import calendar
import contextlib
import io
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import time

try:
    import fcntl
except ImportError:          # every non-POSIX platform (Windows: msvcrt below)
    fcntl = None
try:
    import msvcrt
except ImportError:          # every POSIX platform (fcntl above covers it)
    msvcrt = None            # with neither, `_registry_lock` refuses to write
                             # unlocked rather than reopen the race the lock
                             # exists to close

TOOLS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tools")
if os.path.abspath(TOOLS) not in sys.path:
    sys.path.insert(0, os.path.abspath(TOOLS))
# KEPT INLINE ON PURPOSE, do not fold into `._toolspath`: this module is
# loaded standalone by `tools/sbe_authority_hook.py` via
# `spec_from_file_location`, with no package parent, so a package-relative
# import here kills the hook's registry read and the guard fails open.
# Proven the hard way: the 2026-08-05 fold-in attempt turned
# `tools/test_sbe_authority_hook.py` red on exactly that failure.
# The ONE overlap rule this project has, imported from the module that owns it.
# A private near-copy here is how the hook and the registry stop refusing over
# the same fence, and that failure would be silent.
from sbe_fence_hook import paths_overlap  # noqa: E402

#: Registry schema versions this module knows how to read. An unknown version
#: is refused rather than parsed hopefully, and NEVER silently reset: a
#: registry this module cannot read still records somebody's open fences.
#: "1.0" predates the `change` field: a task's identity was `id` alone.
#: "1.1" is the current shape, identity (change, taskId); `migrate_registry`
#: (below) is the ONLY place a "1.0" registry is turned into "1.1", and it
#: runs only on a write path (`cmd_open`, `cmd_close`), never merely a read.
KNOWN_REGISTRY_VERSIONS = ("1.0", "1.1")
REGISTRY_VERSION = "1.1"

#: The registry file, repo-relative, POSIX spelling. One file, no service.
REGISTRY_REL = ".sbe/tasks.json"

#: The lock sidecar's own name, a SIBLING of the registry file, never a
#: second key inside it: the registry format stays byte-compatible, so a
#: reader of `.sbe/tasks.json` that predates this lock still parses every
#: byte the same way. `changed_paths` exempts it by exact name for the same
#: reason it exempts `REGISTRY_REL` itself; see the module docstring.
REGISTRY_LOCK_REL = REGISTRY_REL + ".lock"

#: Where receipts live unless the caller says otherwise. A reviewer may not own
#: any path under this, and a reviewer whose diff touches it cannot close even
#: with --force: a reviewer who writes the receipts it reviews is the defect
#: reviewer separation exists to stop.
DEFAULT_EVIDENCE_DIR = ".sbe/evidence"


def evidence_dir(root):
    """`root` joined with `DEFAULT_EVIDENCE_DIR` using the platform's native
    separator, never the constant's own slash. `DEFAULT_EVIDENCE_DIR` stays a
    plain relative POSIX-style string because other code may still read it
    as a key; a caller that needs an actual filesystem path calls this
    instead of joining the constant directly with `os.path.join`, so the
    slash cannot reach a comparison, a JSON field or a printed message as a
    mixed separator on a platform where os.path.join does not split on it."""
    return os.path.join(root, *DEFAULT_EVIDENCE_DIR.split("/"))


ROLES = ("writer", "reviewer")
STATUSES = ("open", "closed", "abandoned")

#: The exact field set of a schema-1.0 record, frozen history: it predates
#: `change` and is kept ONLY so a 1.0 registry can still be READ (never
#: written again in this shape) long enough for `migrate_registry` to lift
#: it to 1.1. Nothing new is ever added here.
RECORD_FIELDS_1_0 = ("id", "agent", "role", "worktree", "ownedPaths", "readOnlyPaths",
                     "baseCommit", "expiry", "status", "verifyCommand", "evidenceId",
                     "openedAt", "closedAt")

#: Every field a CURRENT (1.1) task record carries. Exactly these; "abandoned"
#: is a legal status value in the schema, and nothing in this wave writes it
#: yet. `change` is the first half of a task's identity, the pair
#: (change, id): the empty string means unscoped, the same collide-by-id-alone
#: behavior a bare `sbe task open` always had.
RECORD_FIELDS = RECORD_FIELDS_1_0 + ("change",)
LEASE_FIELDS = ("leaseId", "change", "taskId", "worktree", "controller",
                "acquiredAt", "renewedAt", "expiresAt", "releasedAt", "release")
LEASE_RELEASE_FIELDS = ("kind", "actor", "reason", "at")
LEASE_DEFAULT_TTL = 1800
LEASE_MAX_TTL = 86400

#: The defect this closes: `tools/sbe_fence_hook.py` fails open over
#: `.sbe/tasks.json` for two reasons, and this is the fix for the second one.
#: (The first, that the hook never read this file's shape at all, is fixed in
#: that file.) A task record carried `agent` (a free-text label, never
#: compared against the harness's own identity) and no session at all, so the
#: hook could not tell the rightful holder of a task from any other session,
#: including one calling itself INTRUDER.
#:
#: `session` is DELIBERATELY NOT in RECORD_FIELDS: it is optional, read with
#: `record.get("session")`, never required. Every registry written before this
#: build has no such key, and `load_registry`'s required-field check must keep
#: reading those files exactly as it always has; making this field required
#: would refuse every pre-existing 1.1 registry outright over a key that
#: simply postdates it, which is the "silently reset" that function exists to
#: never do (see its own docstring). No schema version bump either: 1.1's
#: shape already tolerates unknown extra keys record by record (only MISSING
#: required keys are refused), so this field arrives the same way `forced`
#: already does on `cmd_close`, an additive key nothing has to migrate for.
#:
#: The two environment variables are checked in this order: CLAUDE_SESSION_ID
#: is the harness's own session id, the SAME value `tools/sbe_fence_hook.py`'s
#: PreToolUse payload carries as `session_id` for every later edit in this
#: session (and the one `tools/sbe_gatelock.py::session_id` already reads for
#: the identical attribution purpose, so this is not a new convention).
#: SBE_SESSION_ID is an explicit override for a manual run or a test, named
#: separately from the fence hook's own `BROTHERSBE_FENCE_SESSION` override
#: because that variable overrides the READER's identity at decision time and
#: this one supplies the WRITER's identity at open time; conflating the two
#: names would make a test that sets one accidentally change the other's
#: answer. Neither is invented when absent: an invented id would authorize
#: nobody and would make a stale placeholder look like a real claim.
SESSION_ID_ENV_VARS = ("CLAUDE_SESSION_ID", "SBE_SESSION_ID")

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_LEASE_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_ISO_RE = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")


class RegistryUnusable(Exception):
    """The registry cannot support a decision at all: not a git repository, a
    file that does not parse, or a schema version this module does not know.
    Raised rather than returning an empty registry, because an empty registry
    and an unreadable one look identical downstream and only one of them means
    nobody holds a fence."""


class DiffUnavailable(Exception):
    """The changed-path set could not be computed, so nothing was compared.
    NO-DATA, never a pass: a base commit that no longer resolves says nothing
    about scope having been kept."""


class AmbiguousTaskId(RegistryUnusable):
    """A bare task id, given with no `--change`, matches more than one record
    across different changes for the lookup being made (more than one OPEN
    record for `_find_open`; more than one distinct change's record at all
    for `sbe work`'s own `_find_record`). Identity is the (change, taskId)
    pair, and returning whichever match happened to be first or last would
    reopen exactly the T01-in-two-dossiers collision this pair exists to
    close, so this is raised instead, naming every colliding pair, and the
    caller decides from there (most callers ask for `--change` to disambiguate).

    Subclasses `RegistryUnusable` on purpose, as a safety net only: every call
    site this module and `work.py` control catches this specifically for its
    own correctly-prefixed refusal message, but `RegistryUnusable` is already
    caught at the top of both `main()` functions, so a call site that forgets
    to catch this still exits with a clear reason instead of an uncaught
    traceback. It is not, conceptually, "the registry cannot support a
    decision at all": the registry is fine, the caller just owes `--change`.
    """
    def __init__(self, task_id, matches):
        self.task_id = task_id
        self.matches = matches
        pairs = ", ".join("(change=%r, id=%r)" % (m.get("change") or "", task_id)
                          for m in matches)
        super(AmbiguousTaskId, self).__init__(
            "task id %r is not unique without --change: %d matching record(s): %s. Pass "
            "--change to say which." % (task_id, len(matches), pairs))


def _iso(epoch):
    """ISO 8601 in UTC, to the second, with an explicit Z, the same spelling
    the evidence receipts use."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def _iso_epoch(value):
    if not isinstance(value, str) or not _ISO_RE.match(value):
        raise ValueError("not a UTC ISO-8601 timestamp")
    return calendar.timegm(time.strptime(value, "%Y-%m-%dT%H:%M:%SZ"))


def _session_identity():
    """This process's session identity from `SESSION_ID_ENV_VARS`, checked in
    that order, or "" when neither is set. "" is never stored as a record's
    `session` value; see `cmd_open`, which omits the key entirely rather than
    writing an empty string that could later be confused with a real, if
    oddly blank, declared identity."""
    for var in SESSION_ID_ENV_VARS:
        value = os.environ.get(var, "").strip()
        if value:
            return value
    return ""


def controller_leases(data):
    """The optional top-level lease collection. Absence is the legacy empty set."""
    return data.get("controllerLeases", [])


def lease_state(lease, now=None):
    if lease.get("releasedAt") is not None:
        return "released"
    epoch = time.time() if now is None else now
    return "stale" if _iso_epoch(lease["expiresAt"]) <= epoch else "live"


def _validate_lease(lease, path, index):
    label = "%s: controllerLeases[%d]" % (path, index)
    if not isinstance(lease, dict):
        raise RegistryUnusable("%s is not a lease record object" % label)
    missing = [field for field in LEASE_FIELDS if field not in lease]
    if missing:
        raise RegistryUnusable("%s is missing field(s) %s" % (label, ", ".join(missing)))
    if not isinstance(lease["leaseId"], str) or not _LEASE_ID_RE.match(lease["leaseId"]):
        raise RegistryUnusable("%s leaseId must be 32 lowercase hexadecimal characters"
                               % label)
    for field in ("change", "taskId", "controller"):
        if not isinstance(lease[field], str) or not lease[field].strip():
            raise RegistryUnusable("%s %s must be a non-empty string" % (label, field))
    worktree = lease["worktree"]
    if (not isinstance(worktree, str) or not os.path.isabs(worktree)
            or os.path.realpath(worktree) != worktree):
        raise RegistryUnusable("%s worktree must be an absolute canonical path" % label)
    for field in ("acquiredAt", "renewedAt", "expiresAt"):
        try:
            _iso_epoch(lease[field])
        except (ValueError, OverflowError):
            raise RegistryUnusable("%s %s must be a UTC ISO-8601 timestamp" % (label, field))
    released_at = lease["releasedAt"]
    release = lease["release"]
    if (released_at is None) != (release is None):
        raise RegistryUnusable("%s releasedAt and release must both be null or both set"
                               % label)
    if release is not None:
        try:
            _iso_epoch(released_at)
        except (ValueError, OverflowError):
            raise RegistryUnusable("%s releasedAt must be a UTC ISO-8601 timestamp" % label)
        if not isinstance(release, dict):
            raise RegistryUnusable("%s release must be an object" % label)
        missing = [field for field in LEASE_RELEASE_FIELDS if field not in release]
        if missing:
            raise RegistryUnusable("%s release is missing field(s) %s"
                                   % (label, ", ".join(missing)))
        if release["kind"] not in ("released", "broken"):
            raise RegistryUnusable("%s release.kind must be released or broken" % label)
        for field in ("actor", "reason"):
            if not isinstance(release[field], str) or not release[field].strip():
                raise RegistryUnusable("%s release.%s must be a non-empty string"
                                       % (label, field))
        if release["at"] != released_at:
            raise RegistryUnusable("%s release.at must equal releasedAt" % label)


def _normalize_change(value):
    """A `--change` value, stripped exactly the way `cmd_open` strips it
    before storing (`(args.change or "").strip()`), or `value` unchanged
    when it is not a string (`None`, meaning the flag was never given at
    all, must stay `None`: that is what tells `_find_open` / `_find_record`
    to search every change rather than one specific one).

    Every READ site that looks a task up by `--change` (`cmd_close` here,
    and `cmd_check` / `cmd_finish` / `cmd_remove` in `work.py`) must run the
    given value through this before comparing, or a padded value that opened
    fine (stored stripped, by `cmd_open`) becomes permanently unreachable: a
    raw, un-stripped read compares `'  pad  '` against the stored `'pad'`,
    never matches, and refuses with a message that misleadingly reads as 'no
    such open task' for a task that IS open.
    """
    return value.strip() if isinstance(value, str) else value


def _git(args, cwd):
    out = subprocess.run(["git"] + list(args), cwd=cwd, capture_output=True, text=True)
    return out.returncode, out.stdout, out.stderr


def repo_root_of(cwd):
    """The top level of the repository the command runs in, which is where the
    registry lives. A directory git cannot answer for is refused, never guessed
    at: a registry written outside any repository would govern nothing."""
    code, out, err = _git(["rev-parse", "--show-toplevel"], cwd)
    if code != 0 or not out.strip():
        raise RegistryUnusable("%s is not inside a git repository (%s); the registry "
                               "lives at the repository root and there is none here"
                               % (cwd, err.strip() or "no toplevel"))
    return out.strip()


def registry_path(root):
    return os.path.join(root, ".sbe", "tasks.json")


def load_registry(root):
    """The registry as a dict, or a fresh empty one when no file exists yet.

    A file that EXISTS and cannot be read is refused with the reason and is
    never reset: a corrupt registry still records somebody's open fences, and
    replacing it with a clean empty one would close every fence in it without
    anybody deciding to."""
    path = registry_path(root)
    if not os.path.exists(path):
        return {"schemaVersion": REGISTRY_VERSION, "tasks": []}
    try:
        with io.open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (ValueError, OSError) as exc:
        raise RegistryUnusable("%s does not parse (%s). Refusing, and NOT resetting it: "
                               "a corrupt registry still records somebody's open fences"
                               % (path, exc))
    if not isinstance(data, dict):
        raise RegistryUnusable("%s is not a JSON object; refusing rather than resetting"
                               % path)
    version = data.get("schemaVersion")
    if version not in KNOWN_REGISTRY_VERSIONS:
        raise RegistryUnusable("%s carries schema version %r and this tool reads %s; "
                               "refusing rather than parsing hopefully or resetting"
                               % (path, version, ", ".join(KNOWN_REGISTRY_VERSIONS)))
    tasks = data.get("tasks")
    if not isinstance(tasks, list) or not all(isinstance(t, dict) for t in tasks):
        raise RegistryUnusable("%s: 'tasks' is not a list of records; refusing rather "
                               "than resetting" % path)
    # Version-aware: a schema-1.0 record never carried `change` at all, and
    # requiring it here would refuse every pre-migration registry outright,
    # which is exactly the "silently reset" this function exists to never do.
    # `migrate_registry` is the ONLY place a 1.0 record gains `change`.
    required = RECORD_FIELDS if version == REGISTRY_VERSION else RECORD_FIELDS_1_0
    for record in tasks:
        missing = [f for f in required if f not in record]
        if missing:
            raise RegistryUnusable("%s: task %r is missing field(s) %s; refusing rather "
                                   "than guessing what they held"
                                   % (path, record.get("id", "(no id)"), ", ".join(missing)))
    leases = data.get("controllerLeases", [])
    if not isinstance(leases, list):
        raise RegistryUnusable("%s: 'controllerLeases' is not a list of records; refusing "
                               "rather than resetting" % path)
    seen = set()
    for index, lease in enumerate(leases):
        _validate_lease(lease, path, index)
        if lease["leaseId"] in seen:
            raise RegistryUnusable("%s: duplicate controller lease id %s"
                                   % (path, lease["leaseId"]))
        seen.add(lease["leaseId"])
    return data


def migrate_registry(data):
    """`data`, upgraded to `REGISTRY_VERSION` ("1.1") if it is not already
    there. Returns a NEW dict; the one passed in is never mutated. Already-1.1
    data is returned unchanged (no-op, not an error).

    A schema-1.0 record carried no `change`, and nothing else it holds says
    what dossier produced it (see the module docstring's list of what this
    control does not do): this does NOT guess one. Every migrated record
    adopts the empty, unscoped change "", the same collide-by-id-alone
    behavior it always had, stated honestly rather than invented. The
    consequence for `_find_open` specifically: a migrated OPEN record's
    change="" no longer collides with a DIFFERENT, non-empty change stamped
    onto a fresh `sbe task open` / `sbe work start` for the SAME id, so an
    operator's first change-scoped start after upgrading can leave TWO OPEN
    records for one id (see the module docstring's own bullet on this).

    This is the ONLY function that turns a 1.0 registry into the 1.1 shape,
    and it is called ONLY from a write path (`cmd_open`, `cmd_close`), right
    before the read-modify-write those commands are already inside: the
    on-disk file changes shape on the FIRST WRITE after this build lands,
    never merely by being read. `list`, `fence` and `check` read a 1.0
    registry as-is (`record.get("change")` handles its absence) and never
    call this, so they never persist a migrated copy themselves.

    A registry this function cannot migrate is refused BY NAME, never
    silently rewritten: an unknown predecessor version (defensive; `open`/
    `close` only ever reach this after `load_registry` has already refused
    any version outside `KNOWN_REGISTRY_VERSIONS`, but a caller that built
    `data` some other way, e.g. a test, gets the same refusal), or a 1.0
    record that already carries a `change` key of a type this migration was
    never told how to interpret (a hand-edit, most likely): that record is
    named and the whole migration is refused, not coerced.
    """
    version = data.get("schemaVersion")
    if version == REGISTRY_VERSION:
        return data
    if version != "1.0":
        raise RegistryUnusable("cannot migrate registry schema version %r to %s; the only "
                               "known predecessor is 1.0" % (version, REGISTRY_VERSION))
    migrated = []
    for record in data.get("tasks") or []:
        existing = record.get("change")
        if "change" in record and not isinstance(existing, str):
            raise RegistryUnusable(
                "task %r in a registry declared schemaVersion 1.0 already carries a "
                "non-string 'change' field (%r); refusing to migrate rather than "
                "guessing what it means" % (record.get("id", "(no id)"), existing))
        new_record = dict(record)
        new_record.setdefault("change", "")
        migrated.append(new_record)
    result = dict(data)
    result["schemaVersion"] = REGISTRY_VERSION
    result["tasks"] = migrated
    return result


def save_registry(root, data):
    """Atomic rewrite: write a temp file in the same directory, then rename.
    The file is never half-written.

    This function alone still says nothing about two concurrent WRITERS: it
    only ever sees the `data` dict its caller already built in memory, so a
    caller that read the registry, mutated its own copy, and called this
    with a stale copy would still silently erase whatever a second writer
    added in between. That is why the lock lives one level up, in
    `cmd_open`/`cmd_close`, wrapped around the read AND this write as one
    critical section (`_registry_lock`, below): a lock only inside this
    function would still let two callers each build a version of `data`
    that never saw the other's task and race to write it, unprotected.
    """
    directory = os.path.join(root, ".sbe")
    if not os.path.isdir(directory):
        os.makedirs(directory)
    fd, tmp = tempfile.mkstemp(prefix="tasks.", suffix=".tmp", dir=directory)
    try:
        with io.open(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(data, indent=2, sort_keys=True) + "\n")
        os.replace(tmp, registry_path(root))
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


#: How long `_registry_lock` waits for the exclusive lock before giving up.
#: Bounded rather than infinite so a stuck holder cannot wedge every later
#: writer forever.
_LOCK_TIMEOUT_S = 30.0


@contextlib.contextmanager
def _registry_lock(root):
    """Exclusive advisory lock serializing every read-modify-write of ONE
    task registry, held from the registry's read through its rewrite so two
    concurrent `sbe task open` (or `close`) calls can no longer both read the
    same tasks list, both mutate their own in-memory copy, and have the
    second `save_registry` call silently overwrite the first's addition with
    a JSON blob that never saw it.

    This mirrors `tools/sbe_telemetry.py`'s `_writer_lock` and
    `brothersbe.decisions._store_lock`: an `fcntl.flock` taken on a sidecar
    file beside the thing being protected, spun for up to `_LOCK_TIMEOUT_S`
    before giving up, so the codebase has one locking style rather than a
    second one invented here. It is not the SAME function, because the two
    other callers want a timeout or a missing `fcntl` to yield an UNLOCKED
    pass so a telemetry row or a decision package is never dropped outright;
    this lock exists so two writers can no longer both win the same
    read-modify-write, and proceeding unlocked would reopen exactly that
    race, so a timeout or a missing `fcntl` here raises `RegistryUnusable`
    instead of yielding: nothing is written rather than written unprotected
    against a concurrent writer.
    """
    if fcntl is None and msvcrt is None:
        raise RegistryUnusable(
            "this platform has neither fcntl nor msvcrt, so the task registry cannot "
            "be locked for a safe read-modify-write; nothing was recorded rather than "
            "writing unprotected against a concurrent writer")
    directory = os.path.join(root, ".sbe")
    try:
        os.makedirs(directory, exist_ok=True)
    except OSError as exc:
        raise RegistryUnusable("%s cannot be created (%s); nothing was recorded"
                               % (directory, exc))
    lock_path = os.path.join(root, REGISTRY_LOCK_REL)
    try:
        fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT, 0o644)
    except OSError as exc:
        raise RegistryUnusable(
            "the lock sidecar %s could not be opened (%s); nothing was recorded rather "
            "than writing unprotected against a concurrent writer" % (lock_path, exc))
    try:
        held = False
        deadline = time.time() + _LOCK_TIMEOUT_S
        while True:
            try:
                if fcntl is not None:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                else:
                    # Same byte-range shape as decisions.py's store lock and
                    # tools/sbe_telemetry.py's writer lock (pad to one byte,
                    # anchor at offset 0, lock [0, 1)): msvcrt regions anchor
                    # at the CURRENT position, so every opener must target the
                    # same range or two writers never contend. Without this
                    # branch the whole task registry, and `sbe work` and
                    # `sbe handover` with it, refused to run on Windows at all,
                    # while decisions.py three files away already had the fix.
                    if os.fstat(fd).st_size < 1:
                        os.write(fd, b"0")
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                held = True
                break
            except OSError:
                if time.time() >= deadline:
                    break
                time.sleep(0.02)
        if not held:
            raise RegistryUnusable(
                "the task registry at %s stayed locked by another writer past %.0fs; "
                "nothing was recorded rather than writing unprotected against it"
                % (registry_path(root), _LOCK_TIMEOUT_S))
        yield
    finally:
        # No explicit `flock(fd, LOCK_UN)`: POSIX releases every lock held
        # through a descriptor the instant that descriptor is closed (`man 2
        # flock`), this fd is never reused past this point, and an explicit
        # unlock call here would need its own failure handling for no
        # behavioral gain. One syscall does the whole job.
        os.close(fd)


def _ttl_seconds(value):
    try:
        ttl = int(value)
    except (TypeError, ValueError):
        raise RegistryUnusable("lease TTL must be an integer number of seconds")
    if ttl < 1 or ttl > LEASE_MAX_TTL:
        raise RegistryUnusable("lease TTL must be between 1 and %d seconds"
                               % LEASE_MAX_TTL)
    return ttl


def _lease_by_id(data, lease_id):
    for lease in controller_leases(data):
        if lease["leaseId"] == lease_id:
            return lease
    return None


def acquire_controller_lease(root, change, task_id, worktree, controller,
                             ttl_seconds=LEASE_DEFAULT_TTL):
    ttl = _ttl_seconds(ttl_seconds)
    change = _normalize_change(change) or ""
    worktree = os.path.realpath(os.path.abspath(worktree))
    if not isinstance(controller, str) or not controller.strip():
        raise RegistryUnusable("lease controller must be a non-empty identity")
    controller = controller.strip()
    with _registry_lock(root):
        data = migrate_registry(load_registry(root))
        leases = data.setdefault("controllerLeases", [])
        for lease in leases:
            if lease.get("releasedAt") is not None:
                continue
            same_task = ((lease.get("change") or "") == change
                         and lease.get("taskId") == task_id)
            same_worktree = lease.get("worktree") == worktree
            if (same_task and same_worktree
                    and lease.get("controller") == controller):
                return lease, True
            if same_task or same_worktree:
                raise RegistryUnusable(
                    "controller lease collision with %s (%s): unreleased %s lease for "
                    "(change=%r, taskId=%r), worktree %s, controller %s"
                    % (lease["leaseId"], lease_state(lease), lease_state(lease),
                       lease["change"], lease["taskId"], lease["worktree"],
                       lease["controller"]))
        now = time.time()
        lease = {
            "leaseId": secrets.token_hex(16),
            "change": change,
            "taskId": task_id,
            "worktree": worktree,
            "controller": controller,
            "acquiredAt": _iso(now),
            "renewedAt": _iso(now),
            "expiresAt": _iso(now + ttl),
            "releasedAt": None,
            "release": None,
        }
        leases.append(lease)
        save_registry(root, data)
        return lease, False


def renew_controller_lease(root, lease_id, controller,
                           ttl_seconds=LEASE_DEFAULT_TTL):
    ttl = _ttl_seconds(ttl_seconds)
    with _registry_lock(root):
        data = migrate_registry(load_registry(root))
        lease = _lease_by_id(data, lease_id)
        if lease is None:
            raise RegistryUnusable("no controller lease %s" % lease_id)
        state = lease_state(lease)
        if state != "live":
            raise RegistryUnusable("controller lease %s is %s and cannot be renewed"
                                   % (lease_id, state))
        if lease["controller"] != controller:
            raise RegistryUnusable("controller lease %s belongs to %s, not %s"
                                   % (lease_id, lease["controller"], controller))
        now = time.time()
        lease["renewedAt"] = _iso(now)
        lease["expiresAt"] = _iso(now + ttl)
        save_registry(root, data)
        return lease


def release_controller_lease(root, lease_id, controller, reason):
    if not isinstance(reason, str) or not reason.strip():
        raise RegistryUnusable("lease release requires a non-empty reason")
    with _registry_lock(root):
        data = migrate_registry(load_registry(root))
        lease = _lease_by_id(data, lease_id)
        if lease is None:
            raise RegistryUnusable("no controller lease %s" % lease_id)
        state = lease_state(lease)
        if state != "live":
            raise RegistryUnusable("controller lease %s is %s and cannot be released"
                                   % (lease_id, state))
        if lease["controller"] != controller:
            raise RegistryUnusable("controller lease %s belongs to %s, not %s"
                                   % (lease_id, lease["controller"], controller))
        at = _iso(time.time())
        lease["releasedAt"] = at
        lease["release"] = {"kind": "released", "actor": controller,
                            "reason": reason.strip(), "at": at}
        save_registry(root, data)
        return lease


def break_controller_lease(root, lease_id, who, why):
    if not isinstance(who, str) or not who.strip():
        raise RegistryUnusable("lease break requires --who naming the human actor")
    if not isinstance(why, str) or not why.strip():
        raise RegistryUnusable("lease break requires --why with a reason")
    with _registry_lock(root):
        data = migrate_registry(load_registry(root))
        lease = _lease_by_id(data, lease_id)
        if lease is None:
            raise RegistryUnusable("no controller lease %s" % lease_id)
        if lease["releasedAt"] is not None:
            raise RegistryUnusable("controller lease %s is already released" % lease_id)
        at = _iso(time.time())
        lease["releasedAt"] = at
        lease["release"] = {"kind": "broken", "actor": who.strip(),
                            "reason": why.strip(), "at": at}
        save_registry(root, data)
        return lease


def validate_controller_lease(data, lease_id, change, task_id, worktree):
    """Return the matching live lease, or refuse. None permits only no match."""
    change = _normalize_change(change) or ""
    worktree = os.path.realpath(os.path.abspath(worktree))
    matching = [lease for lease in controller_leases(data)
                if lease.get("releasedAt") is None
                and (((lease.get("change") or "") == change
                      and lease.get("taskId") == task_id)
                     or lease.get("worktree") == worktree)]
    if lease_id is None:
        if matching:
            lease = matching[0]
            raise RegistryUnusable(
                "unleased start is blocked by %s controller lease %s for task/worktree"
                % (lease_state(lease), lease["leaseId"]))
        return None
    lease = _lease_by_id(data, lease_id)
    if lease is None:
        raise RegistryUnusable("no controller lease %s" % lease_id)
    if lease.get("releasedAt") is not None:
        raise RegistryUnusable("controller lease %s is released" % lease_id)
    state = lease_state(lease)
    if state != "live":
        raise RegistryUnusable("controller lease %s is %s; a human must break it"
                               % (lease_id, state))
    if ((lease.get("change") or "") != change or lease.get("taskId") != task_id
            or lease.get("worktree") != worktree):
        raise RegistryUnusable(
            "controller lease %s does not match (change=%r, taskId=%r, worktree=%s)"
            % (lease_id, change, task_id, worktree))
    return lease


def open_tasks(data):
    return [t for t in data["tasks"] if t.get("status") == "open"]


def claims_overlap(a, b, root):
    """True when two declared claims cover any common path, by the fence hook's
    own `paths_overlap` in both directions, because either claim may be the
    directory or glob that swallows the other."""
    return paths_overlap(a, b, root) or paths_overlap(b, a, root)


def _porcelain_paths(tree):
    """Changed paths from `git status --porcelain -uall`, a rename counting
    both sides. -uall lists untracked FILES rather than collapsing them to a
    directory entry, because a collapsed `dir/` would compare as a claim over
    the whole directory that nobody typed."""
    code, out, err = _git(["status", "--porcelain=v1", "-uall"], tree)
    if code != 0:
        raise DiffUnavailable("git status failed in %s: %s" % (tree, err.strip()))
    paths = []
    for line in out.splitlines():
        if len(line) < 4:
            continue
        rest = line[3:]
        for side in rest.split(" -> "):
            side = side.strip().strip('"')  # porcelain quotes unusual names
            if side:
                paths.append(side)
    return paths


def changed_paths(tree, base):
    """The union of `git diff --name-only <base>...HEAD` and the porcelain
    status: committed AND uncommitted edits count, and a rename counts both
    sides (--no-renames splits it into its delete and its add on purpose).
    The registry file and its lock sidecar are exempted by exact name; see
    the module docstring for why. A base that does not resolve raises,
    never passes."""
    code, _out, err = _git(["rev-parse", "--verify", "--quiet", base + "^{commit}"], tree)
    if code != 0:
        raise DiffUnavailable("base commit %s does not resolve in %s (%s); nothing was "
                              "compared" % (base, tree, err.strip() or "unknown ref"))
    code, out, err = _git(["diff", "--name-only", "--no-renames", "%s...HEAD" % base], tree)
    if code != 0:
        raise DiffUnavailable("git diff failed in %s: %s" % (tree, err.strip()))
    seen, ordered = set(), []
    for p in [f.strip().strip('"') for f in out.splitlines()] + _porcelain_paths(tree):
        if p and p not in (REGISTRY_REL, REGISTRY_LOCK_REL) and p not in seen:
            seen.add(p)
            ordered.append(p)
    return ordered


def postcondition(root, task, evidence_dir=DEFAULT_EVIDENCE_DIR):
    """The diff-against-declaration verdict for one task, as a dict.

    Every changed path outside the task's owned paths is a violation, listed by
    name. A changed path inside ownership prints as the evidence of scope kept.
    A declared read-only path that changed is a violation too, flagged as such:
    read-only means read. For a reviewer, any changed path under the evidence
    directory is a receipt violation, the one class `--force` may not waive.
    """
    tree = task.get("worktree") or root
    if task.get("worktree") and not os.path.isdir(tree):
        raise DiffUnavailable("declared worktree %s does not exist; nothing was compared"
                              % tree)
    changed = changed_paths(tree, task["baseCommit"])
    owned = task.get("ownedPaths") or []
    read_only = task.get("readOnlyPaths") or []
    in_scope, violations = [], []
    for p in changed:
        if any(paths_overlap(p, claim, tree) for claim in owned):
            in_scope.append(p)
        else:
            violations.append(p)
    read_only_writes = [p for p in violations
                        if any(paths_overlap(p, claim, tree) for claim in read_only)]
    receipt_violations = []
    if task.get("role") == "reviewer":
        receipt_violations = [p for p in changed if paths_overlap(p, evidence_dir, tree)]
    verdict = "FAIL" if (violations or receipt_violations) else "PASS"
    return {"verdict": verdict, "tree": tree, "changed": changed, "inScope": in_scope,
            "violations": violations, "readOnlyWrites": read_only_writes,
            "receiptViolations": receipt_violations}


def _find_open(data, task_id, change=None):
    """The single OPEN record for `task_id`, or None when there is none.

    Identity is the (change, taskId) pair. When `change` is given, only an
    OPEN record whose own `change` equals it can match (never ambiguous: the
    caller already said which one it means). When `change` is omitted and
    MORE than one OPEN record shares this `task_id` across different changes,
    the bare id cannot say which one is meant, and this raises
    `AmbiguousTaskId` naming every one, rather than silently returning
    whichever happens to be first in the list, the exact
    T01-open-in-two-dossiers collision this pair exists to close.
    """
    matches = [t for t in data["tasks"]
              if t.get("id") == task_id and t.get("status") == "open"]
    if change is not None:
        for t in matches:
            if (t.get("change") or "") == change:
                return t
        return None
    if len(matches) > 1:
        raise AmbiguousTaskId(task_id, matches)
    return matches[0] if matches else None


def cmd_open(args, exit_ok, exit_failed, exit_usage):
    root = repo_root_of(os.path.abspath(args.cwd))
    if not _SLUG_RE.match(args.id or ""):
        sys.stderr.write("sbe task open: %r is not a usable task id (letters, digits, "
                         "dot, dash, underscore)\n" % args.id)
        return exit_usage
    if args.role not in ROLES:
        sys.stderr.write("sbe task open: role must be one of %s, not %r\n"
                         % ("/".join(ROLES), args.role))
        return exit_usage
    if not _SHA_RE.match(args.base or ""):
        sys.stderr.write("sbe task open: --base must be a FULL 40-character commit sha, "
                         "not %r. A branch name means something different tomorrow, and a "
                         "postcondition against a moving base compares against nothing.\n"
                         % args.base)
        return exit_usage
    owns = list(args.owns or [])
    change = (args.change or "").strip()
    # Moved ahead of the lock, unchanged otherwise: this check is a pure
    # function of `owns` and `args.evidence_dir`, needs nothing `data` would
    # carry, and sitting here (rather than after the lock creates its lock
    # sidecar below) keeps a reviewer's bad `--owns` from ever manufacturing
    # the "a registry existed here before" signal the next check reads.
    if args.role == "reviewer":
        for mine in owns:
            if claims_overlap(mine, args.evidence_dir, root):
                sys.stderr.write(
                    "sbe task open: refused. A reviewer task may not own %r: it overlaps "
                    "the evidence store %s it would review. A reviewer who writes the "
                    "receipts it reviews is the defect reviewer separation exists to "
                    "stop. This separates roles inside the registry only; it cannot stop "
                    "an actor who never registers.\n" % (mine, args.evidence_dir))
                return exit_usage
    registry_file = registry_path(root)
    lock_file = os.path.join(root, REGISTRY_LOCK_REL)
    # Sampled BEFORE `.sbe/` is touched at all for this call: entering
    # `_registry_lock` below always creates `lock_file` as a side effect of
    # acquiring it (its `os.open(..., O_CREAT)` runs before the flock is even
    # attempted), so checking `lock_file` AFTER that point would always be
    # true and prove nothing. This is the one place left to ask "did a
    # registry exist here before". The signal is the lock sidecar the module
    # already writes, never an invented marker: this module creates it only
    # from inside an `open`/`close` read-modify-write, so a project that has
    # never once run `sbe task open` has neither file, `maybe_lost` stays
    # False, and the refusal below cannot fire (see
    # TestDeletedRegistry.test_a_genuinely_first_ever_open_in_a_fresh_project_still_works
    # in tools/test_sbe_tasks.py).
    maybe_lost = not os.path.exists(registry_file) and os.path.exists(lock_file)
    # From here down is the read-modify-write: the registry is read, migrated
    # to the current schema if it still carries the last one, checked against
    # every other open task, and (on success) rewritten, all under one lock,
    # so a second `sbe task open` racing this one cannot read the same tasks
    # list before this write lands and silently overwrite it.
    with _registry_lock(root):
        # Re-verified now that the lock is actually held, so a SIBLING `sbe
        # task open` racing this one on a project that has never held a
        # registry cannot be mistaken for a lost one: if that sibling was
        # simply mid-write when `maybe_lost` was sampled above, waiting for
        # the lock (just done, by entering this block) and re-checking now
        # sees the registry it finished writing, not an absent one, and this
        # never fires. Only a registry still missing after the wait, on a
        # path whose lock sidecar predates this call, means the file itself
        # was actually lost.
        if maybe_lost and not os.path.exists(registry_file):
            sys.stderr.write(
                "sbe task open: refused. %s is absent but %s (its lock sidecar) already "
                "exists. This module creates that sidecar only from inside an open/close "
                "read-modify-write, never on its own, so its presence with no registry "
                "file means a registry existed here before and its close and ownership "
                "history is gone, most likely because the file was deleted rather than "
                "closed through `sbe task close`. Refusing rather than silently starting "
                "a fresh, empty registry over that loss: restore %s from a backup or "
                "another clone if the history matters, or remove %s too, once the loss "
                "is accepted, so a genuinely first-ever registry can open clean.\n"
                % (registry_file, lock_file, registry_file, lock_file))
            return exit_usage
        data = load_registry(root)
        data = migrate_registry(data)
        if args.worktree:
            validate_controller_lease(
                data, getattr(args, "lease_id", None), change, args.id,
                os.path.realpath(os.path.abspath(args.worktree)))
        if _find_open(data, args.id, change=change):
            sys.stderr.write(
                "sbe task open: refused. Task (change=%r, id=%r) already exists and is "
                "open; close it or pick another id.\n" % (change, args.id))
            return exit_usage
        for other in open_tasks(data):
            for mine in owns:
                for theirs in other.get("ownedPaths") or []:
                    if claims_overlap(mine, theirs, root):
                        sys.stderr.write(
                            "sbe task open: refused. Owned path %r overlaps %r, owned by open "
                            "task %r (agent %s). One writer per file; queue behind it or "
                            "narrow the claim.\n"
                            % (mine, theirs, other["id"], other.get("agent", "(unnamed)")))
                        return exit_usage
        record = {
            "id": args.id,
            "change": change,
            "agent": args.agent,
            "role": args.role,
            "worktree": os.path.abspath(args.worktree) if args.worktree else None,
            "ownedPaths": owns,
            "readOnlyPaths": list(args.read_only or []),
            "baseCommit": args.base,
            "expiry": args.expiry,
            "status": "open",
            "verifyCommand": args.verify,
            "evidenceId": args.evidence_id,
            "openedAt": _iso(time.time()),
            "closedAt": None,
        }
        # Optional and additive on purpose; see SESSION_ID_ENV_VARS above. A
        # call with neither env var set records no session at all rather than
        # a guessed or empty one: `tools/sbe_fence_hook.py` treats that as a
        # claim it cannot tell the holder of, not as a claim held by nobody.
        session = _session_identity()
        if session:
            record["session"] = session
        data["tasks"].append(record)
        save_registry(root, data)
    # UNCHANGED from before this pair existed, on purpose: docs/guides/
    # 00-sandbox.md and tools/test_sbe_sandbox.py (both outside this
    # change's fence) pin this exact line byte-for-byte. `sbe task list`,
    # below, is where the change a task belongs to becomes visible instead.
    sys.stdout.write("sbe task open: %s is open. %s (%s) owns %d path(s): %s. Base %s. "
                     "Close runs the diff postcondition against exactly this "
                     "declaration.\n"
                     % (args.id, args.agent, args.role, len(owns),
                        ", ".join(owns) or "(none)", args.base[:12]))
    return exit_ok


def _note_decision_package_absence(root, task):
    """ONE informational line for an ordinary (non-forced) close: does the
    `.sbe/decisions/` store this change would use hold at least one package
    already? Never moves this command's exit code, the same structural
    guarantee `_record_forced_close` below states for itself and for the
    identical reason: a bookkeeping read must never turn a clean close into
    a failure, and every exception of any class is caught here and printed
    rather than raised past this point.

    THE DEFECT THIS CLOSES: before this function existed, an ordinary close
    with zero decision packages on record printed nothing about it at all,
    the silent half of the P4 complaint that "the decision record stays
    empty". A repository with no decision on file for a change is common and
    often correct (most changes need no human decision), so this is never a
    blocker, only a one-line NO-DATA naming the absence, printed beside the
    close's own PASS line rather than left unsaid.

    `decisions` is imported LAZILY, inside the function, the same reason
    `_record_forced_close` does: an older installation that ships no
    `brothersbe.decisions` still opens, closes and fences tasks.
    """
    try:
        from . import decisions as decisions_mod
    except ImportError:
        return
    try:
        top = decisions_mod.repo_top_of(root)["top"]
        dossier = str((task or {}).get("change") or "").strip() or None
        location = decisions_mod.package_location(top, dossier)
        listing = decisions_mod.list_packages(location["dir"])
    except Exception as exc:
        sys.stdout.write("sbe task close: could not read the decision store: %s: %s\n"
                         % (type(exc).__name__, exc))
        return
    if listing["packages"]:
        return
    sys.stdout.write(
        "sbe task close: NO-DATA: no decision package is recorded in %s (%s). This is not "
        "a failure and does not reopen the task; record one with `sbe record` if a human "
        "decision was actually taken for this change.\n" % (location["dir"], location["reason"]))


def _record_forced_close(root, task):
    """Write ONE decision package for a forced close, and say where it landed.

    THIS FUNCTION CANNOT MOVE THIS COMMAND'S EXIT CODE, and that is structural
    rather than a promise. It returns nothing at all, so there is no value for
    the caller to fold into a code; the caller invokes it as a bare statement,
    AFTER `save_registry` has already committed the close, so the close itself
    is durable before any of this runs; and every failure raised inside it, of
    any class, is caught here, printed with its exception class named, and stops
    here. A forced close still exits on the close's own result with a broken
    decisions directory. The failure is never swallowed either: the sentence
    printed on the way out says exactly what was not recorded, because a
    bookkeeping failure nobody was told about is worse than the missing package.

    `decisions` is imported LAZILY, inside the function, the way `cli.py`
    imports its modules, so an older installation that ships no
    `brothersbe.decisions` still opens, closes and fences tasks.
    """
    try:
        from . import decisions as decisions_mod
    except ImportError as exc:
        sys.stdout.write("sbe task close: no decision package was written: this installation "
                         "carries no brothersbe.decisions (%s). The close above stands and "
                         "this command's exit code is unchanged.\n" % exc)
        return
    try:
        path = decisions_mod.record_forced_close(root, task)
    except Exception as exc:
        sys.stdout.write("sbe task close: no decision package was written: %s: %s. The close "
                         "above stands and this command's exit code is unchanged.\n"
                         % (type(exc).__name__, exc))
        return
    sys.stdout.write("sbe task close: decision package written: %s\n" % path)


def cmd_close(args, exit_ok, exit_failed, exit_usage):
    root = repo_root_of(os.path.abspath(args.cwd))
    # The whole read-modify-write, including the postcondition read of the
    # tree, runs under one lock for the same reason `cmd_open` does: a second
    # `sbe task close` (or `open`) racing this one must not be able to read
    # the registry before this write lands and silently overwrite it. Only
    # `_record_forced_close`, below, runs after this block ends: it writes to
    # a DIFFERENT store under its own lock, and it must run after this close
    # is durable, not merely after this lock is taken.
    with _registry_lock(root):
        data = load_registry(root)
        data = migrate_registry(data)
        change = _normalize_change(getattr(args, "change", None))
        try:
            task = _find_open(data, args.id, change=change)
        except AmbiguousTaskId as exc:
            sys.stderr.write("sbe task close: %s\n" % exc)
            return exit_usage
        if task is None:
            sys.stderr.write(
                "sbe task close: no OPEN task with id %r%s in %s\n"
                % (args.id, (" in change %r" % change) if change else "",
                   registry_path(root)))
            return exit_usage
        try:
            result = postcondition(root, task, evidence_dir=args.evidence_dir)
        except DiffUnavailable as exc:
            result = None
            sys.stdout.write("sbe task close %s: NO-DATA. %s. NO-DATA is not a pass; the "
                             "task stays open.\n" % (args.id, exc))
            if not args.force:
                return exit_failed
        violations, verdict = None, None
        if result is not None:
            # A changed path outside THIS task's own ownedPaths that falls
            # under ANOTHER task's OPEN ownedPaths claim is not this task's
            # violation: two honest writers on disjoint claims in one
            # repository is the
            # ordinary two-lane case (PROTO-01), not a defect, so whichever
            # closes second must not be forced over the other's declared
            # scope. Reuse the exact matcher `postcondition` already used to
            # decide in-scope (`paths_overlap`), so "peer-owned" means
            # precisely what "in-scope" means, judged against someone else's
            # claim instead of this task's own. A path owned by nobody stays
            # a violation exactly as before.
            others = [t for t in open_tasks(data)
                     if not (t.get("id") == task.get("id")
                             and (t.get("change") or "") == (task.get("change") or ""))]
            tree = result["tree"]
            peer_owned, violations = [], []
            for p in result["violations"]:
                owner = next((t for t in others
                             if any(paths_overlap(p, claim, tree)
                                    for claim in t.get("ownedPaths") or [])),
                            None)
                if owner is not None:
                    peer_owned.append((p, owner["id"]))
                else:
                    violations.append(p)
            verdict = "FAIL" if (violations or result["receiptViolations"]) else "PASS"
            for p in result["inScope"]:
                sys.stdout.write("  IN-SCOPE   %s\n" % p)
            for p, owner_id in peer_owned:
                sys.stdout.write("  PEER-OWNED %s (owned by open task %s)\n" % (p, owner_id))
            for p in violations:
                flag = " (declared READ-ONLY, was written)" if p in result["readOnlyWrites"] \
                    else ""
                sys.stdout.write("  VIOLATION  %s%s\n" % (p, flag))
            for p in result["receiptViolations"]:
                sys.stdout.write("  RECEIPT-VIOLATION %s (a reviewer wrote under the "
                                 "evidence store)\n" % p)
            if result["receiptViolations"]:
                sys.stdout.write(
                    "sbe task close %s: FAIL. A reviewer task's diff touches the evidence "
                    "store, and --force may NOT waive this class: a reviewer who writes "
                    "receipts is not reviewing them. The task stays open.\n" % args.id)
                return exit_failed
            if verdict == "PASS":
                task["status"] = "closed"
                task["closedAt"] = _iso(time.time())
                if args.evidence_id:
                    task["evidenceId"] = args.evidence_id
                save_registry(root, data)
                if peer_owned:
                    sys.stdout.write(
                        "sbe task close %s: PASS. %d changed path(s), %d inside the "
                        "declaration and %d peer-owned. Closed clean.\n"
                        % (args.id, len(result["changed"]), len(result["inScope"]),
                           len(peer_owned)))
                else:
                    sys.stdout.write("sbe task close %s: PASS. %d changed path(s), all inside "
                                    "the declaration. Closed clean.\n"
                                     % (args.id, len(result["changed"])))
                _note_decision_package_absence(root, task)
                return exit_ok
            if not args.force:
                sys.stdout.write(
                    "sbe task close %s: FAIL. %d changed path(s) outside the declaration, "
                    "listed above by name. The shell was never parsed; the diff was read. "
                    "The task stays open. Close with --force --who --why to record a "
                    "disposition, never to make this clean.\n"
                    % (args.id, len(violations)))
                return exit_failed
        # --force from here: a recorded human decision, marked FORCED, never clean.
        if not (args.who or "").strip() or not (args.why or "").strip():
            sys.stderr.write("sbe task close: --force requires --who and --why. A forced "
                             "close with no author and no reason is an off switch, not a "
                             "decision.\n")
            return exit_usage
        task["status"] = "closed"
        task["closedAt"] = _iso(time.time())
        if args.evidence_id:
            task["evidenceId"] = args.evidence_id
        task["forced"] = {
            "who": args.who,
            "why": args.why,
            "at": _iso(time.time()),
            "verdict": verdict if result is not None else "NO-DATA",
            "violations": violations if result is not None else [],
        }
        save_registry(root, data)
        sys.stdout.write("sbe task close %s: FORCED by %s (%s). The record carries the "
                         "disposition and the violation list; this close is never read as "
                         "clean.\n" % (args.id, args.who, args.why))
    # A bare statement, after the registry is saved AND this lock released: a
    # forced close is one of the four moments that write a decision package,
    # and writing one may not move what this close decided.
    _record_forced_close(root, task)
    return exit_ok


def cmd_list(args, exit_ok, exit_failed, exit_usage):
    root = repo_root_of(os.path.abspath(args.cwd))
    data = load_registry(root)
    live = open_tasks(data)
    if args.json:
        sys.stdout.write(json.dumps({"schemaVersion": data["schemaVersion"],
                                     "openTasks": live}, indent=2, sort_keys=True) + "\n")
        return exit_ok
    if not live:
        sys.stdout.write("sbe task list: no open tasks in %s\n" % registry_path(root))
        return exit_ok
    # UNCHANGED column layout, on purpose: docs/book/07-the-task-registry.md
    # (outside this change's fence) pins this line byte-for-byte against live
    # output. `sbe task list --json`, above, already carries `change` for
    # every task (it dumps the raw records), so the pair is still readable
    # here without moving text a doc-truth suite this change cannot touch
    # already pinned.
    for t in live:
        sys.stdout.write("%-20s %-10s %-8s base %s  owns: %s%s\n"
                         % (t["id"], t.get("agent", ""), t.get("role", ""),
                            (t.get("baseCommit") or "")[:12],
                            ", ".join(t.get("ownedPaths") or []) or "(none)",
                            ("  expiry " + t["expiry"]) if t.get("expiry") else ""))
    sys.stdout.write("%d open task(s). Expiry is informational: nothing here deletes on "
                     "a clock.\n" % len(live))
    return exit_ok


def _fence_field(value):
    """A record value made safe for a pipe-delimited fence line. A '|' inside a
    field would end the field early for every reader of the line."""
    return str(value).replace("|", "/")


def cmd_fence(args, exit_ok, exit_failed, exit_usage):
    """The markdown fence view, GENERATED from the JSON registry, one direction
    only. Printed to stdout for a human to paste into a STATE.md style
    registry; nothing in this wave reads markdown fences back into the
    registry, and the hand-written fence flow keeps working untouched."""
    root = repo_root_of(os.path.abspath(args.cwd))
    data = load_registry(root)
    lines = 0
    for t in open_tasks(data):
        owned = t.get("ownedPaths") or []
        if not owned:
            sys.stderr.write("sbe task fence: task %r owns no paths, so it renders no "
                             "fence line; a fence with no file scope fences nothing the "
                             "hook can compare against\n" % t["id"])
            continue
        sys.stdout.write(
            "- agent: %s (sole writer, task %s, role %s) | files: %s | base %s | "
            "check: %s |\n"
            % (_fence_field(t.get("agent", "(unnamed)")), _fence_field(t["id"]),
               _fence_field(t.get("role", "")),
               ", ".join(_fence_field(p) for p in owned),
               _fence_field((t.get("baseCommit") or "")[:12]),
               _fence_field(t.get("verifyCommand") or "(none)")))
        lines += 1
    if lines == 0:
        sys.stderr.write("sbe task fence: no open task renders a fence line\n")
    return exit_ok


def cmd_check(args, exit_ok, exit_failed, exit_usage):
    """The overlap scan across all open tasks, so a collision injected into the
    JSON by hand is caught the same way `open` would have caught it."""
    root = repo_root_of(os.path.abspath(args.cwd))
    data = load_registry(root)
    live = open_tasks(data)
    collisions = []
    for i, a in enumerate(live):
        for b in live[i + 1:]:
            for pa in a.get("ownedPaths") or []:
                for pb in b.get("ownedPaths") or []:
                    if claims_overlap(pa, pb, root):
                        collisions.append((a["id"], pa, b["id"], pb))
    for a_id, pa, b_id, pb in collisions:
        sys.stdout.write("COLLISION: task %s owns %r and task %s owns %r; two open "
                         "writers overlap\n" % (a_id, pa, b_id, pb))
    if collisions:
        sys.stdout.write("sbe task check: FAIL, %d collision(s) among %d open task(s).\n"
                         % (len(collisions), len(live)))
        return exit_failed
    sys.stdout.write("sbe task check: no owned-path overlap among %d open task(s). This "
                     "scans the registry; it says nothing about writers who never "
                     "registered.\n" % len(live))
    return exit_ok


def _parser():
    parser = argparse.ArgumentParser(
        prog="sbe task",
        description="The write-scope registry: declare ownership at open, and close only "
                    "when the git diff stayed inside the declaration. After-the-fact by "
                    "design: the shell is never parsed, the diff is read.")
    sub = parser.add_subparsers(dest="sub")

    op = sub.add_parser("open", help="declare a task and the paths it owns")
    op.add_argument("--id", required=True, help="caller-supplied slug, unique among open "
                                                "tasks")
    op.add_argument("--agent", required=True, help="who is doing the work")
    op.add_argument("--role", required=True, help="writer or reviewer")
    op.add_argument("--base", required=True, help="FULL 40-char sha the diff will be "
                                                  "read from at close")
    op.add_argument("--verify", required=True, help="the command that will prove the "
                                                    "work, recorded with the task")
    op.add_argument("--change", default=None,
                    help="the change (dossier) this task belongs to; identity is the "
                         "(change, taskId) pair. Omitted means the empty, unscoped change: "
                         "collides by id alone, the same as before this pair existed")
    op.add_argument("--owns", action="append", default=[],
                    help="a repo-relative path this task owns; repeatable")
    op.add_argument("--read-only", action="append", default=[], dest="read_only",
                    help="a repo-relative path this task reads but may not write; "
                         "repeatable")
    op.add_argument("--worktree", default=None,
                    help="absolute path of the task's own worktree; omitted means the "
                         "shared tree")
    op.add_argument("--expiry", default=None,
                    help="ISO date, informational only; nothing deletes on a clock")
    op.add_argument("--evidence-id", default=None, dest="evidence_id")
    op.add_argument("--evidence-dir", default=DEFAULT_EVIDENCE_DIR, dest="evidence_dir",
                    help="where receipts live, for the reviewer-separation refusal "
                         "(default %s)" % DEFAULT_EVIDENCE_DIR)
    op.add_argument("--cwd", default=".")

    cl = sub.add_parser("close", help="run the diff postcondition and close on PASS")
    cl.add_argument("id")
    cl.add_argument("--change", default=None,
                    help="disambiguate when this id is open in more than one change; "
                         "required only when it is (AmbiguousTaskId names every pair)")
    cl.add_argument("--force", action="store_true",
                    help="close anyway, recording who and why; the close is marked "
                         "FORCED and is never read as clean")
    cl.add_argument("--who", default=None, help="required with --force")
    cl.add_argument("--why", default=None, help="required with --force")
    cl.add_argument("--evidence-id", default=None, dest="evidence_id",
                    help="attach the receipt id this close is backed by")
    cl.add_argument("--evidence-dir", default=DEFAULT_EVIDENCE_DIR, dest="evidence_dir")
    cl.add_argument("--cwd", default=".")

    ls = sub.add_parser("list", help="print open tasks")
    ls.add_argument("--json", action="store_true")
    ls.add_argument("--cwd", default=".")

    fe = sub.add_parser("fence", help="render the markdown fence view from the registry")
    fe.add_argument("--cwd", default=".")

    ck = sub.add_parser("check", help="re-run the overlap scan across all open tasks")
    ck.add_argument("--cwd", default=".")
    return parser


def main(rest, exit_ok=0, exit_failed=1, exit_usage=2):
    """The `sbe task` surface. Exit codes come from the caller so this module
    never disagrees with the CLI's documented table."""
    parser = _parser()
    if not rest:
        parser.print_help(sys.stderr)
        return exit_usage
    try:
        args = parser.parse_args(list(rest))
    except SystemExit as exc:
        # argparse already answered: -h/--help printed the usage the caller
        # asked for and raised 0, and help is not an error. Anything nonzero
        # is a refused flag or argument. Folding both into exit_usage made
        # every explicit help request on this surface exit 2.
        return exit_ok if exc.code in (0, None) else exit_usage
    handlers = {"open": cmd_open, "close": cmd_close, "list": cmd_list,
                "fence": cmd_fence, "check": cmd_check}
    handler = handlers.get(getattr(args, "sub", None))
    if handler is None:
        parser.print_help(sys.stderr)
        return exit_usage
    try:
        return handler(args, exit_ok, exit_failed, exit_usage)
    except RegistryUnusable as exc:
        sys.stderr.write("sbe task: %s\n" % exc)
        return exit_usage
