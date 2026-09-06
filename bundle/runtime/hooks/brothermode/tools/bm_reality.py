#!/usr/bin/env python3
"""The verified-reality record: the smallest honest account of what
actually happened to a release after it shipped.

WHY THIS EXISTS
  docs/NORTH-STAR-CHAIN.md's own chain ends in a stage named
  verified-reality, and until this tool existed nothing in this project
  ever wrote a row for it. Every earlier stage (passport, evidence-
  integrity, release-readiness) is about what BrotherMode believed BEFORE
  a release went out; this one is about what happened in the world AFTER
  it did, and nothing before this tool recorded that at all. A release
  could be accepted, reopened, rolled back, or blow up in production, and
  the chain had no place to say so. Once a release ships, BrotherMode was
  blind.

  H4, the second half of the same gap: a defect discovered in production
  is a fact about the past unless it also creates a fact about the
  future. Without a forced return edge, "we found a bug" is a sentence
  someone types in a chat and the loop never closes. `defect` is that
  return edge: it cannot be written without also appending a new item to
  docs/plan/QUEUE.json, so a defect ALWAYS creates new queued intent, and
  the reality row and the queue item point at each other by id, so either
  one can be found from the other. Verb order matters here: the queue
  item is written FIRST and the reality row SECOND, deliberately, so a
  queue write that fails (an unwritable path, a malformed existing file)
  never leaves behind a reality row that claims to have created intent it
  did not.

THE FOUR VERBS
  accept  a human accepted a release. The one verb that can start a
          release's own history; every other verb must point back at one.
  enter   something happened to an already-accepted release: it was
          reopened, rolled back, or hit an incident. Always names the
          accepted record it happened to.
  defect  an incident with a name: writes the queue item AND the reality
          row, in that order, so the return edge is never partial.
  show    pure read. Prints one release's whole recorded history, or
          every release's history when no --release is given.

INSERT ONLY, ALWAYS
  Every write here appends exactly one row to tools/bm_store.py's
  reality_records table (schema 21) and nothing here ever asks that store
  to update or delete one: a record of what actually happened must not be
  quietly rewritten by a later, more flattering judgement. See
  tools/bm_store.py's own _REALITY_DDL comment and
  Store.add_reality_record's docstring for the three refusals this tool's
  writes run into on purpose (an anonymous acceptance, a record that
  links back to nothing, a defect that creates no intent).

EFFECT CLASSES (tools/bm_effects.py REGISTRY)
  accept, enter, defect are ledger_write: each mutates the BrotherMode
  store, and `defect` additionally regenerates docs/plan/QUEUE.json (a
  project_write in its own right, folded into `defect`'s ledger_write
  declaration the same way bm_project.py's composite commands are).
  show is pure_read: it opens ONLY a read-only store handle and writes
  nothing, including under --help, proven by tools/test_bm_effects.py's
  own purity suite running it in a sandbox.

Python 3.9, standard library only. No network, no subprocess.
No em or en dashes anywhere in this file or its output.

Usage:
  python3 tools/bm_reality.py accept --release ID --accountable NAME
          [--passport SHA256] [--at ISO8601] [--detail TEXT] [--root DIR]
  python3 tools/bm_reality.py enter --kind reopened|rolled-back|incident
          --release-record ID [--at ISO8601] [--detail TEXT] [--root DIR]
  python3 tools/bm_reality.py defect --release-record ID --title TEXT
          [--files PATH ...] [--at ISO8601] [--queue PATH] [--root DIR]
  python3 tools/bm_reality.py show [--release ID] [--root DIR]
"""
import datetime
import json
import os
import shlex
import sys
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))

#: The four verbs this tool accepts, in the order the module docstring
#: describes them.
_VERBS = ("accept", "enter", "defect", "show")

#: The three kinds `enter` may write. 'accepted' is reachable only through
#: `accept`, and 'defect' only through `defect`: both carry obligations
#: (accountable/release_id for one, the queue write for the other) that
#: `enter`'s own flag set cannot satisfy, so this tool never offers them
#: as an --kind choice here.
_ENTER_KINDS = ("reopened", "rolled-back", "incident")

#: Default path to the north-star queue, relative to the project root,
#: matching tools/bm_idle.py's own default exactly (the file `defect`
#: appends new intent to).
_DEFAULT_QUEUE_REL = os.path.join("docs", "plan", "QUEUE.json")


# ---------------------------------------------------------------------------
# Root resolution. Honors --root when given; otherwise borrows bm_store's
# resolve_root() purely for that lookup. Mirrors tools/bm_idle.py's
# resolve_project_root() exactly, since that file is this tool's structural
# template (its own CHAIN_STAGES already names 'verified-reality' and
# 'intent', the two stages this tool writes to).
# ---------------------------------------------------------------------------

def _load_bm_store():
    """Load bm_store.py by PATH: this tool is invoked with an arbitrary
    cwd, and a plain `import bm_store` would depend on whichever sys.path
    the caller happened to have. Never raises: an unimportable module
    degrades to an explicit reason string, not a crash."""
    try:
        import importlib.util
        path = os.path.join(HERE, "bm_store.py")
        spec = importlib.util.spec_from_file_location(
            "bm_store_for_reality", path)
        if spec is None or spec.loader is None:
            return None, "could not build an import spec for bm_store.py"
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod, None
    except Exception as exc:
        return None, "%s: %s" % (type(exc).__name__, exc)


def resolve_project_root(explicit_root):
    """Return (root, None) or (None, reason). `explicit_root` (--root)
    always wins and is checked directly against the filesystem; nothing
    else here reads bm_store at all in that case. Otherwise
    bm_store.resolve_root() is borrowed, which returns a (path, source)
    TUPLE that must be unpacked."""
    if explicit_root:
        candidate = os.path.realpath(os.path.expanduser(explicit_root))
        if not os.path.isdir(candidate):
            return None, "no such directory: %s" % candidate
        return candidate, None
    mod, err = _load_bm_store()
    if mod is None:
        return None, ("could not load bm_store.py to resolve the project "
                      "root (%s); pass --root explicitly" % err)
    root, _source = mod.resolve_root()
    if not root:
        return None, ("nothing anchors a BrotherMode project here (no "
                      "BROTHERMODE_ROOT, no marker directory, no git repo "
                      "found); pass --root explicitly")
    return root, None


def _usage():
    """The command a reader can actually paste, in the layout they have.
    Resolved through bm_store.invocation(), the same resolver every other
    tool in this directory uses (tools/bm_passport.py's own _usage is the
    template). Degrades to this module's own absolute path when bm_store
    cannot be loaded: a usage string is not worth a traceback."""
    cmd = None
    mod, _err = _load_bm_store()
    if mod is not None:
        try:
            cmd = mod.invocation("bm_reality.py", __file__)
        except Exception:
            cmd = None
    if not cmd:
        cmd = "python3 %s" % shlex.quote(os.path.abspath(__file__))
    return (
        "Usage: %s accept --release ID --accountable NAME\n"
        "           [--passport SHA256] [--at ISO8601] [--detail TEXT]\n"
        "           [--root DIR]\n"
        "       %s enter --kind reopened|rolled-back|incident\n"
        "           --release-record ID [--at ISO8601] [--detail TEXT]\n"
        "           [--root DIR]\n"
        "       %s defect --release-record ID --title TEXT\n"
        "           [--files PATH ...] [--at ISO8601] [--queue PATH]\n"
        "           [--root DIR]\n"
        "       %s show [--release ID] [--root DIR]\n\n"
        "Appends ONE row to the BrotherMode store's reality_records table\n"
        "(schema 21), insert only: nothing this tool writes is ever "
        "updated or\ndeleted afterward. `defect` also appends a new "
        "docs/plan/QUEUE.json item\n(stage 'intent') BEFORE writing its "
        "reality row, so a queue write that\nfails leaves no reality row "
        "behind either.\n\n"
        "Exit 0 on a written row (or, for `show`, a completed read).\n"
        "Exit 1 when the store refused the write, could not be opened, "
        "or (for\n`defect`) the queue append failed. Exit 2 for a usage "
        "error, and for a\nNO-DATA verdict where no project root or no "
        "store could be resolved at\nall." % (cmd, cmd, cmd, cmd))


# ---------------------------------------------------------------------------
# Timestamp canonicalisation. Mirrors tools/bm_passport.py's own
# _canonical_now exactly: accepts a bare --at, a trailing Z, or a numeric
# offset, and always stores second-precision UTC, so two invocations
# naming the SAME instant in different spellings write the identical
# occurred_at.
# ---------------------------------------------------------------------------

def _canonical_at(at_arg):
    """(iso_string_or_None, error_or_None)."""
    if not at_arg:
        return None, None
    text = at_arg
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.datetime.fromisoformat(text)
    except ValueError as e:
        return None, "invalid --at value %r: %s" % (at_arg, e)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    parsed = parsed.astimezone(datetime.timezone.utc)
    return parsed.strftime("%Y-%m-%dT%H:%M:%SZ"), None


# ---------------------------------------------------------------------------
# Argument parsing. One hand-rolled loop per verb, the same shape every
# tool in this directory uses (no argparse anywhere in this project).
# `--files` is the one flag that takes more than one value: every token
# after it that does not itself start with "--" is collected, the same
# convention tools/bm_threads.py's own flag scan documents for its
# --files.
# ---------------------------------------------------------------------------

def _parse_accept(args):
    """(values_or_None, error_or_'help')."""
    values = {"release": None, "accountable": None, "passport": None,
             "at": None, "detail": None, "root": None}
    flags = {"--release": "release", "--accountable": "accountable",
             "--passport": "passport", "--at": "at", "--detail": "detail",
             "--root": "root"}
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ("--help", "-h"):
            return None, "help"
        if arg in flags:
            if i + 1 >= len(args):
                return None, "bm_reality accept: %s requires a value" % arg
            values[flags[arg]] = args[i + 1]
            i += 2
        else:
            return None, "bm_reality accept: unknown argument: %s" % arg
    missing = [f for f, k in (("--release", "release"),
                              ("--accountable", "accountable"))
              if not (values[k] or "").strip()]
    if missing:
        return None, ("bm_reality accept: missing required argument(s): "
                      "%s" % ", ".join(missing))
    return values, None


def _parse_enter(args):
    values = {"kind": None, "release_record": None, "at": None,
             "detail": None, "root": None}
    flags = {"--kind": "kind", "--release-record": "release_record",
             "--at": "at", "--detail": "detail", "--root": "root"}
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ("--help", "-h"):
            return None, "help"
        if arg in flags:
            if i + 1 >= len(args):
                return None, "bm_reality enter: %s requires a value" % arg
            values[flags[arg]] = args[i + 1]
            i += 2
        else:
            return None, "bm_reality enter: unknown argument: %s" % arg
    missing = [f for f, k in (("--kind", "kind"),
                              ("--release-record", "release_record"))
              if not (values[k] or "").strip()]
    if missing:
        return None, ("bm_reality enter: missing required argument(s): "
                      "%s" % ", ".join(missing))
    if values["kind"] not in _ENTER_KINDS:
        return None, ("bm_reality enter: --kind must be one of %s, got %r"
                      % (" | ".join(_ENTER_KINDS), values["kind"]))
    return values, None


def _parse_defect(args):
    values = {"release_record": None, "title": None, "files": [],
             "at": None, "queue": None, "root": None}
    single_flags = {"--release-record": "release_record",
                    "--title": "title", "--at": "at", "--queue": "queue",
                    "--root": "root"}
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ("--help", "-h"):
            return None, "help"
        if arg == "--files":
            i += 1
            n = 0
            while i < len(args) and not args[i].startswith("--"):
                values["files"].append(args[i])
                i += 1
                n += 1
            if n == 0:
                return None, "bm_reality defect: --files requires at least one path"
            continue
        if arg in single_flags:
            if i + 1 >= len(args):
                return None, "bm_reality defect: %s requires a value" % arg
            values[single_flags[arg]] = args[i + 1]
            i += 2
        else:
            return None, "bm_reality defect: unknown argument: %s" % arg
    missing = [f for f, k in (("--release-record", "release_record"),
                              ("--title", "title"))
              if not (values[k] or "").strip()]
    if missing:
        return None, ("bm_reality defect: missing required argument(s): "
                      "%s" % ", ".join(missing))
    return values, None


def _parse_show(args):
    values = {"release": None, "root": None}
    flags = {"--release": "release", "--root": "root"}
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ("--help", "-h"):
            return None, "help"
        if arg in flags:
            if i + 1 >= len(args):
                return None, "bm_reality show: %s requires a value" % arg
            values[flags[arg]] = args[i + 1]
            i += 2
        else:
            return None, "bm_reality show: unknown argument: %s" % arg
    return values, None


# ---------------------------------------------------------------------------
# The four verbs.
# ---------------------------------------------------------------------------

def _open_writable_store(mod, root):
    """(store_or_None, error_or_None). Matching bm_store.py's own CLI
    convention (its cmd_claim and every command besides init): only
    `bm_store.py init` may pass create=True. Every command here refuses
    'no-store' instead, naming the fix, the same discipline
    tools/bm_project.py's own _store() documents."""
    try:
        return mod.Store(root, create=False), None
    except mod.OwnershipRefused as e:
        return None, str(e)
    except mod.StoreCorrupt as e:
        return None, str(e)


def _run_accept(mod, root, args):
    values, err = _parse_accept(args)
    if err == "help":
        sys.stdout.write(_usage() + "\n")
        return 0
    if err:
        sys.stderr.write(err + "\n")
        sys.stderr.write(_usage() + "\n")
        return 2
    at_str, at_err = _canonical_at(values["at"])
    if at_err:
        sys.stderr.write("bm_reality accept: %s\n" % at_err)
        return 2
    store, open_err = _open_writable_store(mod, root)
    if store is None:
        sys.stderr.write("bm_reality accept: %s\n" % open_err)
        return 1
    try:
        result = store.add_reality_record({
            "kind": "accepted",
            "release_id": values["release"],
            "accountable": values["accountable"],
            "passport_sha256": values["passport"] or "",
            "occurred_at": at_str,
            "detail": values["detail"] or "",
        })
    except mod.OwnershipRefused as e:
        sys.stderr.write("bm_reality accept: refused (%s): %s\n"
                         % (e.reason, e))
        return 1
    finally:
        store.close()
    sys.stdout.write("accepted %s: release %s, record %s\n"
                     % (values["release"], values["release"],
                        result["record_id"]))
    return 0


def _run_enter(mod, root, args):
    values, err = _parse_enter(args)
    if err == "help":
        sys.stdout.write(_usage() + "\n")
        return 0
    if err:
        sys.stderr.write(err + "\n")
        sys.stderr.write(_usage() + "\n")
        return 2
    at_str, at_err = _canonical_at(values["at"])
    if at_err:
        sys.stderr.write("bm_reality enter: %s\n" % at_err)
        return 2
    store, open_err = _open_writable_store(mod, root)
    if store is None:
        sys.stderr.write("bm_reality enter: %s\n" % open_err)
        return 1
    try:
        result = store.add_reality_record({
            "kind": values["kind"],
            "links_to": values["release_record"],
            "occurred_at": at_str,
            "detail": values["detail"] or "",
        })
    except mod.OwnershipRefused as e:
        sys.stderr.write("bm_reality enter: refused (%s): %s\n"
                         % (e.reason, e))
        return 1
    finally:
        store.close()
    sys.stdout.write("%s recorded: release %s, record %s, linked to %s\n"
                     % (values["kind"], result["release_id"],
                        result["record_id"], values["release_record"]))
    return 0


# ---------------------------------------------------------------------------
# F1 (cross-family adversarial review, 2026-08-20): `defect` reads
# QUEUE.json, appends an item, and replaces the file, then commits a
# reality row naming that item. Two concurrent `defect` calls can each
# read the SAME queue, each replace it (last write wins), and each then
# commit a reality row whose intent_ref points at a queue item the other
# process's replace already erased. This tool's own domain is
# multi-writer safety, so a race here is a defect in the thing it exists
# to guard against.
#
# The lock mirrors tools/bm_autosave.py's _WorktreeLock mechanism exactly
# (os.open with O_CREAT|O_EXCL at mode 0600, writing pid and timestamp; a
# stale-age bound so a crashed holder cannot wedge this forever; a bounded
# poll, never an unbounded wait). It does NOT mirror that class's
# behaviour on contention: _WorktreeLock's acquire() returns False and the
# caller stands down silently, because autosave is advisory. A lost
# `defect` write is not advisory, so acquire() here instead RAISES, naming
# the holder's pid and how long it has held the lock, so the caller
# refuses visibly, never a silent overwrite and never a silent skip.
# ---------------------------------------------------------------------------

class _DefectQueueLockRefused(Exception):
    """Raised by _QueueAppendLock.acquire() when the lock is still held at
    the wait deadline. Carries the holder's pid and age (both best-effort;
    "?" / 0 when the lock file could not be read, which can happen if the
    holder released it in the instant between the last failed open and
    this read) so the caller's refusal names who to wait for."""

    def __init__(self, holder_pid, age_seconds):
        self.holder_pid = holder_pid
        self.age_seconds = age_seconds
        super(_DefectQueueLockRefused, self).__init__(
            "the queue append lock is held by pid %s (%d seconds); "
            "refusing rather than risk losing a concurrent defect write. "
            "Wait for that process to finish, or investigate whether it "
            "is still alive." % (holder_pid, age_seconds))


class _QueueAppendLock(object):
    """One lock per queue file (`<queue_path>.lock`), serializing the
    whole read-append-replace-then-record sequence `defect` runs. See the
    module comment above this class for why it mirrors, but does not
    reuse behaviourally, tools/bm_autosave.py's _WorktreeLock."""

    DEFAULT_WAIT = 10
    STALE_SECONDS = 600

    def __init__(self, queue_path, wait=None):
        self.path = queue_path + ".lock"
        self.wait = self.DEFAULT_WAIT if wait is None else wait
        self.held = False

    def _try_once(self):
        try:
            fd = os.open(self.path,
                        os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except OSError:
            return False
        try:
            os.write(fd, ("%d %f\n" % (os.getpid(), time.time()))
                     .encode("utf-8"))
        except OSError:  # sbe: allow-silent lock body is informational only; the O_EXCL create above is what holds the lock
            pass
        finally:
            os.close(fd)
        return True

    def _holder(self):
        """(pid_str, age_seconds), best-effort, for the refusal message."""
        pid = "?"
        try:
            with open(self.path, "r") as handle:
                parts = handle.read().split()
            if parts:
                pid = parts[0]
        except OSError:  # sbe: allow-silent best-effort per this method's own docstring, "?" pid stands in
            pass
        try:
            age = time.time() - os.stat(self.path).st_mtime
        except OSError:
            age = 0.0
        return pid, age

    def _clear_if_stale(self):
        try:
            age = time.time() - os.stat(self.path).st_mtime
        except OSError:
            return
        if age > self.STALE_SECONDS:
            try:
                os.remove(self.path)
            except OSError:  # sbe: allow-silent another process may have cleared the same stale lock first
                pass

    def acquire(self):
        """Raises _DefectQueueLockRefused, naming the holder, rather than
        returning False or waiting forever, if the lock is still held once
        `self.wait` seconds have passed."""
        deadline = time.time() + max(self.wait, 0)
        while True:
            if self._try_once():
                self.held = True
                return
            self._clear_if_stale()
            if time.time() >= deadline:
                pid, age = self._holder()
                raise _DefectQueueLockRefused(pid, int(age))
            time.sleep(0.05)

    def release(self):
        if not self.held:
            return
        self.held = False
        try:
            os.remove(self.path)
        except OSError:  # sbe: allow-silent lock file already gone is not a release failure
            pass


def _queue_append_defect(queue_path, record_id, release_record, title,
                         files, at_str):
    """(item_id_or_None, error_or_None). Appends ONE item to the queue
    file at `queue_path`, stage 'intent', state 'queued', carrying a
    `provenance` field naming `record_id` (the reality row this defect
    is ABOUT TO write, generated by the caller before either write
    happens; see this module's own docstring on write order). Never
    raises: every failure (missing file, malformed JSON, an unwritable
    path) is returned as a plain reason string so the caller can refuse
    to write the reality row rather than crash with one already
    committed by the OS's own traceback machinery.

    The item id is derived from `record_id` rather than generated
    separately (RR-<record_id>): this is exactly the id
    Store.add_reality_record's own intent_ref must equal, and deriving
    it rather than minting a second random id makes that equality
    structural instead of something two separate calls could drift on."""
    if not os.path.isfile(queue_path):
        return None, "missing queue file: %s" % queue_path
    try:
        with open(queue_path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        return None, "not valid JSON: %s (%s: %s)" % (
            queue_path, type(exc).__name__, exc)
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        return None, "%s is not a usable queue file (no items list)" % queue_path

    item_id = "RR-%s" % record_id
    item = {
        "id": item_id,
        "title": title,
        "state": "queued",
        "clock": "agent",
        "files": list(files),
        "stage": "intent",
        "provenance": record_id,
    }
    if at_str:
        item["defect_occurred_at"] = at_str
    data["items"].append(item)

    tmp_path = queue_path + ".tmp-%s" % uuid.uuid4().hex
    try:
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(tmp_path, queue_path)
    except OSError as exc:
        try:
            os.unlink(tmp_path)
        except OSError:  # sbe: allow-silent best-effort cleanup of the temp file; the real error is already returned below
            pass
        return None, "could not write %s (%s)" % (queue_path, exc)
    return item_id, None


def _run_defect(mod, root, args):
    values, err = _parse_defect(args)
    if err == "help":
        sys.stdout.write(_usage() + "\n")
        return 0
    if err:
        sys.stderr.write(err + "\n")
        sys.stderr.write(_usage() + "\n")
        return 2
    at_str, at_err = _canonical_at(values["at"])
    if at_err:
        sys.stderr.write("bm_reality defect: %s\n" % at_err)
        return 2
    queue_path = values["queue"] or os.path.join(root, _DEFAULT_QUEUE_REL)

    # F1: everything from the release-record check to the reality-row
    # write below runs under ONE cross-process lock on this queue file, so
    # a second concurrent `defect` cannot read the same queue this one is
    # about to append to and replace. See _QueueAppendLock's own module
    # comment for the mechanism and why it refuses instead of standing
    # down silently.
    queue_lock = _QueueAppendLock(queue_path)
    try:
        queue_lock.acquire()
    except _DefectQueueLockRefused as e:
        sys.stderr.write("bm_reality defect: refused (queue-locked): %s\n"
                         % e)
        return 1

    try:
        store, open_err = _open_writable_store(mod, root)
        if store is None:
            sys.stderr.write("bm_reality defect: %s\n" % open_err)
            return 1

        # The linked release record must genuinely exist and be 'accepted'
        # BEFORE the queue is touched: this is exactly what
        # Store.add_reality_record's own R2 refuses, checked here first (a
        # read, not a write) so a doomed defect never appends an orphaned
        # queue item that the reality write would then refuse to attach to.
        try:
            linked = store.get_reality_record(values["release_record"], raw=True)
            if linked is None or linked.get("kind") != "accepted":
                sys.stderr.write(
                    "bm_reality defect: refused (no-accepted-release): "
                    "release-record %r does not name an existing 'accepted' "
                    "reality record\n" % values["release_record"])
                store.close()
                return 1

            # Generated BEFORE either write, so the queue item's provenance
            # and the reality row's own primary key are the SAME id (see
            # _queue_append_defect's own docstring for why).
            record_id = uuid.uuid4().hex

            item_id, queue_err = _queue_append_defect(
                queue_path, record_id, values["release_record"],
                values["title"], values["files"], at_str)
            if queue_err:
                # THE WHOLE POINT: the queue append failed, so the reality
                # row is NOT written. A defect record pointing at a queue
                # item that does not exist would be worse than no record
                # at all.
                sys.stderr.write("bm_reality defect: queue append failed, "
                                 "no reality record written: %s\n" % queue_err)
                return 1

            try:
                result = store.add_reality_record({
                    "record_id": record_id,
                    "kind": "defect",
                    "links_to": values["release_record"],
                    "intent_ref": item_id,
                    "occurred_at": at_str,
                    "detail": values["title"],
                })
            except mod.OwnershipRefused as e:
                sys.stderr.write(
                    "bm_reality defect: queue item %s was written but the "
                    "reality record was refused (%s): %s\n"
                    % (item_id, e.reason, e))
                return 1
        finally:
            store.close()
    finally:
        queue_lock.release()

    sys.stdout.write(
        "defect recorded: release %s, record %s, queue item %s\n"
        % (result["release_id"], result["record_id"], item_id))
    return 0


def _format_record_line(record):
    return ("%-11s %-32s release=%s occurred_at=%s accountable=%s "
           "passport=%s links_to=%s intent_ref=%s"
           % (record.get("kind", ""), record.get("record_id", ""),
              record.get("release_id", ""), record.get("occurred_at", ""),
              record.get("accountable", "") or "-",
              record.get("passport_sha256", "") or "-",
              record.get("links_to", "") or "-",
              record.get("intent_ref", "") or "-"))


def _run_show(mod, root, args):
    values, err = _parse_show(args)
    if err == "help":
        sys.stdout.write(_usage() + "\n")
        return 0
    if err:
        sys.stderr.write(err + "\n")
        sys.stderr.write(_usage() + "\n")
        return 2
    try:
        store = mod.ReadOnlyStore(root)
    except mod.OwnershipRefused as e:
        sys.stdout.write("NO-DATA: %s\n" % e)
        return 2
    except mod.StoreCorrupt as e:
        sys.stdout.write("NO-DATA: %s\n" % e)
        return 2
    try:
        records = store.list_reality_records(
            release_id=values["release"], raw=True)
    finally:
        store.close()

    if not records:
        if values["release"]:
            sys.stdout.write("no reality records for release %s\n"
                             % values["release"])
        else:
            sys.stdout.write("no reality records recorded\n")
        return 0

    # Oldest first: this is a release's narrative (accepted, then
    # whatever happened to it), and the store's own list_reality_records
    # returns newest-first for the ordinary "what happened most
    # recently" read, so the display order here is a re-sort of what it
    # returned, not a second query.
    records = sorted(records, key=lambda r: (r.get("occurred_at", ""),
                                             r.get("recorded_at", "")))
    for record in records:
        sys.stdout.write(_format_record_line(record) + "\n")
    return 0


# ---------------------------------------------------------------------------
# CLI.
# ---------------------------------------------------------------------------

def _extract_root(args):
    """Pulls a bare --root value out of an already-parsed verb's args,
    for the one caller (main) that needs the root BEFORE the verb-specific
    parser runs, to resolve it once and pass it down rather than each verb
    parser re-resolving it. Tolerant of --root being entirely absent."""
    for i, arg in enumerate(args):
        if arg == "--root" and i + 1 < len(args):
            return args[i + 1]
    return None


def _run(argv):
    if not argv or argv[0] in ("--help", "-h"):
        sys.stdout.write(_usage() + "\n")
        return 0
    verb = argv[0]
    if verb not in _VERBS:
        sys.stderr.write("bm_reality: unknown verb: %s\n" % verb)
        sys.stderr.write(_usage() + "\n")
        return 2
    rest = argv[1:]

    mod, load_err = _load_bm_store()
    if mod is None:
        sys.stdout.write("NO-DATA: could not load bm_store.py (%s)\n"
                         % load_err)
        return 2

    root_arg = _extract_root(rest)
    root, reason = resolve_project_root(root_arg)
    if root is None:
        sys.stdout.write("NO-DATA: %s\n" % reason)
        return 2

    if verb == "accept":
        return _run_accept(mod, root, rest)
    if verb == "enter":
        return _run_enter(mod, root, rest)
    if verb == "defect":
        return _run_defect(mod, root, rest)
    return _run_show(mod, root, rest)


def main(argv):
    # R5 (release-blocking shortlist, 2026-08-23): a store write below
    # (accept, enter, defect) can succeed and then the CLI can still exit
    # nonzero if the write it prints afterward (a release id, a name, a
    # detail string) is not ASCII and the process locale is C or POSIX,
    # whose stdout/stderr encode as ASCII. That turned a genuinely saved
    # record into a reported failure, an evidence-corruption defect worse
    # than a plain failure would be. Fixed at the stream, not with a try
    # around each write: every non-ASCII byte this process ever prints
    # gets an escaped, still-readable stand-in instead of raising, so the
    # exit code always reflects the commit, never the printing.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="backslashreplace")
    try:
        return _run(argv)
    except Exception as exc:
        sys.stdout.write("NO-DATA: %s: %s\n" % (type(exc).__name__, exc))
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
