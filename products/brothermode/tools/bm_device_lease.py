#!/usr/bin/env python3
"""EPIC M4.01: per-device lease store, atomic claim/TTL/release/quarantine
for physical and simulator device identities (mobile testing broker).

WHY A SIBLING MODULE, NOT A NEW TABLE IN bm_store.py's OWN STORE. bm_store.py
already implements exactly this transactional discipline (sqlite3 WAL, one
BEGIN IMMEDIATE transaction per mutation, optimistic-concurrency UPDATE ...
WHERE version=? with the rowcount checked) for WORK UNITS, and the M4 unit
decomposition doc asks that be reused rather than reinvented. It genuinely
does not generalize to device identity, for one concrete reason: bm_store.py
resolves exactly ONE store per PROJECT ROOT (resolve_root() walks up from
cwd to the nearest .brothermode/ marker or .git, so every git checkout gets
its own <root>/.brothermode/store.sqlite3). A physical simulator UDID is a
MACHINE-scoped resource, not a project-scoped one: two unrelated repos, or
two worktrees of the same repo, checked out on the same Mac can each want the
same physical iPhone, and they resolve to two DIFFERENT bm_store.py roots, so
a table inside either one's own database could never see the other's claim.
This module therefore keeps its own single sqlite3 file at a fixed
machine-wide path (default_db_path(), override BM_DEVICE_LEASE_DB), visible
to every project and worktree on the machine alike.

THE DIRECTORY NAME IS DELIBERATELY NOT ".brothermode". bm_store.resolve_root()
treats ANY ancestor directory literally named ".brothermode" as a valid
project-root marker, even one holding no store.sqlite3 (its fallback-marker
branch: "a .brothermode/ with no store.sqlite3 ... still anchors a root when
NO ancestor is initialised"). A device-lease database living at
~/.brothermode/... would silently hijack every unrelated `bm` CLI invocation
run from anywhere under $HOME with no closer project marker, attaching it to
an empty, wrong "project". The directory here is ".brothermode-device-leases"
for exactly that reason; do not rename it to ".brothermode".

TTL IS A DELIBERATE, NEW DEPARTURE FROM bm_store.py's OWN WORK-UNIT CLAIMS.
bm_store.py's schema comment records that a ttl_hours column was tried and
then deleted for work-unit claims: "nothing anywhere expires anything: a
claim with a TTL of 0.36 seconds still blocked a second claim a second
later" (no consumer ever swept expired claims). Device leases are different
on purpose: a crashed or killed test session must not permanently strand a
physical device, so this module DOES enforce and sweep TTL, both implicitly
(claim() atomically reclaims an expired lease as part of granting a new one)
and explicitly (recover_stale() reclaims one without re-leasing it, list_stale()
reports every currently-expired lease for a caller that wants to act on all
of them). The lesson taken from bm_store.py's deletion is not "never do TTL",
it is "never claim a TTL exists unless something actually enforces it": this
module's enforcement is the claim()/recover_stale() code below, not a column
nobody reads.

Python 3.9, standard library only. No network, no subprocess.
No em or en dashes anywhere in this file, its comments, or its output.
"""
import contextlib
import datetime
import os
import shutil
import sqlite3
import uuid

SCHEMA_VERSION = 1
DB_ENV_OVERRIDE = "BM_DEVICE_LEASE_DB"
_DEFAULT_DB_DIRNAME = ".brothermode-device-leases"
_DEFAULT_DB_FILENAME = "leases.sqlite3"

_VALID_STATES = ("available", "leased", "dirty")
_ISO_STAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def now_iso():
    """UTC, second precision, ISO 8601, matching bm_store.now_iso()'s own
    format exactly: fixed width and zero-padded, so plain string comparison
    of two stamps is the same as comparing them chronologically (every
    staleness check below relies on this)."""
    return datetime.datetime.now(datetime.timezone.utc).strftime(_ISO_STAMP_FORMAT)


def _add_seconds(iso_ts, seconds):
    # ponytail: now_iso() truncates to whole seconds before this ever
    # runs, so a lease's real hold time is ttl_seconds minus up to 0.999s
    # of truncated fractional time (adversarial-review finding), and the
    # inclusive expires_at<=ts staleness check in claim()/recover_stale()
    # can call a lease stale at the instant expires_at arrives. Harmless
    # for the TTLs this store expects (test-run and device-boot scale,
    # minutes not sub-seconds); a caller relying on sub-second TTL
    # accuracy needs a finer clock than now_iso()'s second precision.
    dt = datetime.datetime.strptime(iso_ts, _ISO_STAMP_FORMAT).replace(
        tzinfo=datetime.timezone.utc)
    dt = dt + datetime.timedelta(seconds=seconds)
    return dt.strftime(_ISO_STAMP_FORMAT)


def default_db_path():
    """BM_DEVICE_LEASE_DB, or ~/.brothermode-device-leases/leases.sqlite3.
    See the module docstring for why the directory name is NOT
    ".brothermode"; that is load bearing, not a style choice."""
    override = os.environ.get(DB_ENV_OVERRIDE)
    if override:
        return override
    return os.path.join(
        os.path.expanduser("~"), _DEFAULT_DB_DIRNAME, _DEFAULT_DB_FILENAME)


class LeaseError(Exception):
    """Base for everything this module raises on purpose."""


class LeaseRefused(LeaseError):
    """A mutation was refused by explicit rule (fails closed), never a
    crash: `reason` is a short machine-checkable token, `details` a dict a
    caller can act on without parsing the message."""

    def __init__(self, reason, message, details=None):
        super().__init__(message)
        self.reason = reason
        self.details = details or {}


class LeaseStoreCorrupt(LeaseError):
    """The sqlite file itself is damaged (named evidence, see
    _is_corruption). quarantine_path is where it was moved aside at open
    time, or None either when the move failed or when this was raised
    mid-transaction by _exec, which never attempts a move (see its own
    docstring for why)."""

    def __init__(self, message, quarantine_path=None):
        super().__init__(message)
        self.quarantine_path = quarantine_path


_DDL = """
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT
);
CREATE TABLE IF NOT EXISTS device_leases (
  device_id TEXT PRIMARY KEY,
  state TEXT NOT NULL DEFAULT 'available'
    CHECK(state IN ('available','leased','dirty')),
  owner TEXT NOT NULL DEFAULT '',
  session_id TEXT NOT NULL DEFAULT '',
  lease_uuid TEXT NOT NULL DEFAULT '',
  leased_at TEXT,
  ttl_seconds INTEGER,
  expires_at TEXT,
  dirty_reason TEXT NOT NULL DEFAULT '',
  dirty_at TEXT,
  version INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  -- adversarial-review finding M3: this file's own Python never writes
  -- state='leased' with a NULL expires_at, but the database is a
  -- machine-wide sqlite file other processes can open directly (this
  -- module's own test suite does exactly that), so a row violating the
  -- invariant would be invisible to claim() (refused as already-leased)
  -- and to recover_stale()/list_stale() (not considered stale), stranding
  -- it permanently. Enforced at the schema level, not only in Python, so
  -- no writer (this module or another one) can create that row at all.
  CHECK (state != 'leased' OR expires_at IS NOT NULL)
);
"""

# Named evidence of a damaged FILE, not merely an unwelcome error (mirrors
# bm_store.py's own _CORRUPTION_MESSAGE_FRAGMENTS / _quarantine_is_warranted
# split): an OperationalError outside this list, or one naming "database is
# locked"/"database is busy", is reported and the file is left untouched.
# Quarantining on every DatabaseError would move a perfectly healthy file
# aside for a transient disk, permission, or network-volume hiccup.
_CORRUPTION_MESSAGE_FRAGMENTS = (
    "database disk image is malformed",
    "file is not a database",
    "file is encrypted or is not a database",
    "malformed database schema",
    "unsupported file format",
)


def _is_transient_busy(e):
    msg = str(e).lower()
    return "database is locked" in msg or "database is busy" in msg


def _is_corruption(e):
    if type(e) is sqlite3.DatabaseError:
        return True
    msg = str(e).lower()
    return any(frag in msg for frag in _CORRUPTION_MESSAGE_FRAGMENTS)


def _exec(store, sql, params=()):
    """Every SQL statement a mutation runs, including BEGIN IMMEDIATE and
    COMMIT, goes through here (mirrors bm_store.py's own _exec, GATE 4
    there): __init__ only probes the schema at OPEN time, so a busy or
    damaged database found MID-transaction would otherwise leak a raw
    sqlite3 exception out of claim()/release()/etc that a caller has to
    know sqlite3 to interpret, instead of the same LeaseRefused('db-busy')
    every other busy path in this module raises.

    A non-busy DatabaseError this late (the file was healthy at open, but
    sqlite hit damage on a later page, or something else replaced it under
    us) closes the connection and raises LeaseStoreCorrupt WITHOUT the
    move-aside __init__ does on a fresh open: this connection is already
    open and mid-transaction, and moving the file out from under our own
    handle is a needless second risk on top of whatever just went wrong.
    Only the init-time path quarantines a file; this path only reports and
    closes, naming the path so a caller can move it aside by hand.

    Refuses 'store-closed' up front (adversarial-review finding) rather
    than letting store.conn.execute raise a raw AttributeError on None: a
    caller that keeps a DeviceLeaseStore past its own close(), or past an
    earlier call that closed it via the corruption path above, gets a
    typed LeaseError either way, never a Python internals leak."""
    if store.conn is None:
        raise LeaseError(
            "device lease store at %s is closed; open a fresh "
            "DeviceLeaseStore to continue" % (store.path,))
    try:
        return store.conn.execute(sql, params)
    except sqlite3.IntegrityError:
        raise
    except sqlite3.OperationalError as e:
        if _is_transient_busy(e):
            raise LeaseRefused(
                "db-busy",
                "the device lease store at %s is busy or locked (%s); "
                "wait a moment and retry." % (store.path, e))
        store.close()
        raise LeaseStoreCorrupt(
            "device lease store at %s failed mid-operation (%s); the "
            "connection was closed rather than left in an unknown state. "
            "If this repeats, move %s aside by hand and let a fresh open "
            "recreate it." % (store.path, e, store.path))
    except sqlite3.DatabaseError as e:
        store.close()
        raise LeaseStoreCorrupt(
            "device lease store at %s failed mid-operation (%s); the "
            "connection was closed rather than left in an unknown state. "
            "If this repeats, move %s aside by hand and let a fresh open "
            "recreate it." % (store.path, e, store.path))


def _chmod_best_effort(path, mode):
    """Best-effort permission tightening; a failure (or a platform that
    will not honor a POSIX mode at all) is expected and never escalated."""
    try:
        os.chmod(path, mode)
    except OSError:  # sbe: allow-silent best-effort by design, not a bug swallowed
        pass


def _require_device_id(device_id):
    if not isinstance(device_id, str) or not device_id.strip():
        raise ValueError("device_id must be a non-empty string, got %r" % (device_id,))


# ponytail: 10 years is far past any real device-lease TTL (test-run and
# device-boot scale, minutes not years) and far short of what
# datetime.timedelta/strftime can represent, so this ceiling exists only
# to turn a wildly out-of-range caller value into this module's own typed
# error instead of a raw OverflowError leaking out of _add_seconds
# (adversarial-review finding, minor m1). Raise it if a real caller ever
# needs a longer-lived lease than this.
_MAX_TTL_SECONDS = 315360000  # 10 years


def _require_ttl(ttl_seconds):
    if (isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int)
            or ttl_seconds <= 0 or ttl_seconds > _MAX_TTL_SECONDS):
        raise ValueError(
            "ttl_seconds must be a positive whole number no greater than "
            "%d (10 years), got %r" % (_MAX_TTL_SECONDS, ttl_seconds))


def _require_iso_stamp(value, param_name):
    """Every staleness comparison in this module is a plain string compare
    against now_iso()'s own fixed-width format; a caller-supplied stamp in
    any other shape (a bare date, sub-second precision, a local time)
    would compare wrong without ever raising, silently mis-classifying
    which leases are stale. Refuse it here instead."""
    try:
        datetime.datetime.strptime(value, _ISO_STAMP_FORMAT)
    except (TypeError, ValueError):
        raise ValueError(
            "%s must match now_iso()'s format %r, got %r"
            % (param_name, _ISO_STAMP_FORMAT, value))


def _row_to_dict(row):
    return {k: row[k] for k in row.keys()}


class DeviceLeaseStore(object):
    """One open connection to the machine-wide device lease database. Every
    mutation runs inside exactly one BEGIN IMMEDIATE ... COMMIT (see
    _transaction), so two writers on the same file never interleave and a
    reader never observes a half-written row."""

    def __init__(self, db_path=None, busy_timeout_ms=5000):
        self.path = os.path.realpath(db_path or default_db_path())
        db_dir = os.path.dirname(self.path)
        os.makedirs(db_dir, exist_ok=True)
        _chmod_best_effort(db_dir, 0o700)
        self.conn = None
        # A pre-existing zero-byte file is a truncated write, never a
        # legitimate fresh database (sqlite3 would happily treat it as one
        # and silently lose every row already believed to exist).
        if os.path.isfile(self.path) and os.path.getsize(self.path) == 0:
            raise self._corrupt(
                "device lease store exists but is zero bytes "
                "(truncated, or never finished writing)")
        try:
            conn = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
            conn.row_factory = sqlite3.Row
            self.conn = conn
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=%d" % int(busy_timeout_ms))
            conn.executescript(_DDL)
            conn.execute(
                "INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),))
            # A read, not just a connect: a corrupt file can open fine and
            # only fail the instant something touches its b-tree pages.
            conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        except sqlite3.OperationalError as e:
            if not _is_transient_busy(e) and _is_corruption(e):
                # _corrupt() closes self.conn itself (see its own
                # docstring for why the order matters): do NOT close here
                # first.
                raise self._corrupt(str(e))
            if self.conn is not None:
                self.conn.close()
                self.conn = None
            if _is_transient_busy(e):
                raise LeaseRefused(
                    "db-busy",
                    "the device lease store at %s is busy or locked (%s), "
                    "most likely another process writing at the same "
                    "moment. The file was NOT touched: wait a moment and "
                    "retry." % (self.path, e))
            raise
        except sqlite3.DatabaseError as e:
            raise self._corrupt(str(e))
        _chmod_best_effort(self.path, 0o600)
        _chmod_best_effort(self.path + "-wal", 0o600)
        _chmod_best_effort(self.path + "-shm", 0o600)

    def _corrupt(self, reason):
        """Move the main file aside, plus copy its -wal/-shm sidecars if
        any (adversarial-review finding, reproduced: WAL mode leaves
        recent writes in those sidecars, and leaving them behind next to
        a fresh database at the same path lets sqlite try to apply the
        OLD wal onto the NEW file on its next open, reintroducing the
        very failure this was meant to clear).

        THE SIDECARS ARE COPIED BEFORE self.conn.close() RUNS, NOT AFTER,
        and this method closes the connection itself (callers must NOT
        close it first): reproduced here that a bundled sqlite3 deletes
        stale -wal/-shm files as part of its own close() cleanup, so a
        close-then-move order sometimes finds nothing left to move at
        all, silently keeping the corrupt evidence incomplete. Copying
        while the handle is still open (or before it was ever opened, for
        the zero-byte pre-check path, where self.conn is still None) is
        unaffected by whatever close() does to the originals; only the
        MAIN file needs an actual move; the copied sidecars' now-stale
        originals are removed best-effort afterward, since close() may
        already have removed them anyway."""
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime(
            "%Y%m%dT%H%M%S%f")
        qpath = "%s.corrupt-%s" % (self.path, stamp)
        for suffix in ("-wal", "-shm"):
            src = self.path + suffix
            try:
                if os.path.isfile(src):
                    shutil.copy2(src, qpath + suffix)
            except OSError:  # sbe: allow-silent best-effort sidecar copy, main file move below is still attempted
                pass
        if self.conn is not None:
            try:
                self.conn.close()
            except Exception:  # sbe: allow-silent, already on the way to a corruption verdict
                pass
            self.conn = None
        moved = None
        try:
            if os.path.isfile(self.path):
                os.replace(self.path, qpath)
                moved = qpath
        except OSError:  # ponytail: best-effort quarantine move, named in the message either way
            moved = None
        # Only clean up the live sidecars once the main file actually moved
        # (adversarial-review finding, minor m2): if os.replace above failed,
        # self.path is still the live database and these are its live
        # -wal/-shm, which can hold committed-but-uncheckpointed rows. The
        # copies made above already preserved the evidence either way; this
        # loop's job is deleting the ORIGINALS once they are sidecars of a
        # quarantined file, not of a file still in use.
        if moved:
            for suffix in ("-wal", "-shm"):
                try:
                    leftover = self.path + suffix
                    if os.path.isfile(leftover):
                        os.remove(leftover)
                except OSError:  # sbe: allow-silent best-effort cleanup, the copy above already preserved the evidence
                    pass
        return LeaseStoreCorrupt(
            "device lease store at %s is corrupt (%s).%s"
            % (self.path, reason,
               (" Moved aside to %s (and any -wal/-shm sidecars); a fresh "
                "store will be created next open." % moved) if moved else
               " Could not move it aside; nothing was deleted."),
            quarantine_path=moved)

    def close(self):
        if self.conn is not None:
            self.conn.close()
            self.conn = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False

    def _safe_rollback(self):
        # A raw call, not _exec: this runs while another exception is
        # already being handled (or right after one) and must never
        # replace or mask it, and self.conn can already be None here if
        # that exception was _exec's own busy/corrupt classification
        # closing the connection first.
        if self.conn is not None:
            try:
                self.conn.execute("ROLLBACK")
            except sqlite3.Error:  # sbe: allow-silent, already unwinding a real exception
                pass

    @contextlib.contextmanager
    def _transaction(self):
        _exec(self, "BEGIN IMMEDIATE")
        try:
            yield self.conn
        except Exception:
            self._safe_rollback()
            raise
        else:
            # adversarial-review finding: COMMIT itself can raise (most
            # plausibly LeaseRefused('db-busy') from _exec). Left
            # unhandled, that leaves the connection still inside the open
            # transaction, so the NEXT call's BEGIN IMMEDIATE fails with
            # sqlite3's "cannot start a transaction within a transaction",
            # a message _is_transient_busy does not recognize, which
            # _exec would then misclassify as corruption and quarantine a
            # perfectly healthy store over what was only transient
            # contention. Roll back here too, so a failed COMMIT leaves
            # the connection clean for the caller's retry.
            try:
                _exec(self, "COMMIT")
            except Exception:
                self._safe_rollback()
                raise

    # -- mutations ----------------------------------------------------

    def claim(self, device_id, owner, session_id, ttl_seconds):
        """Atomic claim. Raises LeaseRefused('device-dirty') for a
        quarantined device OR for one whose TTL expired without an explicit
        release() (adversarial-review finding M2, root-caused rather than
        patched: an expired lease with no release means the previous holder
        may have crashed mid-run without cleaning up the device, which is
        exactly the "no evidence collision" the M4 exit criterion is about,
        so this module refuses to silently hand a possibly-dirty device to
        the next claimant. The stale row is quarantined dirty in the same
        atomic write that discovers it; a caller must clear_dirty() after
        its own verification before anyone can claim the device again).
        Raises LeaseRefused('already-leased') for one another owner still
        holds within its TTL. Otherwise grants the lease to a new or
        available device. Returns {device_id, lease_uuid, state,
        expires_at, recovered_from_stale}; recovered_from_stale is always
        False now that a stale reclaim raises device-dirty instead of
        succeeding, kept in the return shape for callers already reading
        it."""
        _require_device_id(device_id)
        _require_ttl(ttl_seconds)
        owner = owner or ""
        session_id = session_id or ""
        ts = now_iso()
        lease_uuid = uuid.uuid4().hex
        expires_at = _add_seconds(ts, ttl_seconds)
        # `to_raise`: the M2 fix commits the dirty-quarantine UPDATE and
        # THEN reports device-dirty to the caller, so the quarantine must
        # survive even though this call itself is refused. Raising
        # immediately inside the `with self._transaction()` block below
        # would trigger _transaction()'s own rollback and discard that
        # exact UPDATE, silently reverting the quarantine, so the refusal
        # is deferred until after the transaction has already committed.
        to_raise = None
        with self._transaction():
            row = _exec(self,
                "SELECT * FROM device_leases WHERE device_id=?",
                (device_id,)).fetchone()
            if row is None:
                _exec(self,
                    "INSERT INTO device_leases (device_id, state, owner, "
                    "session_id, lease_uuid, leased_at, ttl_seconds, "
                    "expires_at, version, created_at, updated_at) "
                    "VALUES (?, 'leased', ?, ?, ?, ?, ?, ?, 1, ?, ?)",
                    (device_id, owner, session_id, lease_uuid, ts,
                     ttl_seconds, expires_at, ts, ts))
                result = {"device_id": device_id, "lease_uuid": lease_uuid,
                          "state": "leased", "expires_at": expires_at,
                          "recovered_from_stale": False}
            elif row["state"] == "dirty":
                # Nothing mutated yet on this path, so raising here (and
                # letting _transaction() roll back) is fine.
                raise LeaseRefused(
                    "device-dirty",
                    "device %r is quarantined dirty (%s) since %s; it "
                    "cannot be claimed until clear_dirty() remediates it"
                    % (device_id, row["dirty_reason"], row["dirty_at"]),
                    details={"device_id": device_id,
                             "dirty_reason": row["dirty_reason"]})
            else:
                stale = (row["state"] == "leased"
                         and row["expires_at"] is not None
                         and row["expires_at"] <= ts)
                if row["state"] == "leased" and not stale:
                    # Nothing mutated yet on this path either.
                    raise LeaseRefused(
                        "already-leased",
                        "device %r is already leased by owner=%r "
                        "session=%r until %s"
                        % (device_id, row["owner"], row["session_id"],
                           row["expires_at"]),
                        details={"device_id": device_id,
                                 "owner": row["owner"],
                                 "session_id": row["session_id"],
                                 "expires_at": row["expires_at"]})
                if stale:
                    # M2 root-cause fix: an unexplained TTL expiry (no
                    # release() call ever landed) is quarantined the same
                    # way mark_dirty() quarantines any other suspect
                    # device, not silently re-leased. Same version-guarded
                    # UPDATE shape as every other mutation here, so a
                    # concurrent writer still loses the race honestly
                    # instead of racing this one too.
                    dirty_reason = (
                        "lease expired at %s without release() (owner=%r "
                        "session=%r); the previous holder may have "
                        "crashed mid-run, so this device needs "
                        "clear_dirty() after verification before it can "
                        "be claimed again"
                        % (row["expires_at"], row["owner"],
                           row["session_id"]))
                    cur = _exec(self,
                        "UPDATE device_leases SET state='dirty', owner='', "
                        "session_id='', lease_uuid='', leased_at=NULL, "
                        "ttl_seconds=NULL, expires_at=NULL, dirty_reason=?, "
                        "dirty_at=?, version=version+1, updated_at=? "
                        "WHERE device_id=? AND version=?",
                        (dirty_reason, ts, ts, device_id, row["version"]))
                    if cur.rowcount != 1:
                        raise LeaseRefused(
                            "version-conflict",
                            "device %r changed under this claim (a "
                            "concurrent writer landed first); retry"
                            % (device_id,),
                            details={"device_id": device_id})
                    result = None
                    to_raise = LeaseRefused(
                        "device-dirty", dirty_reason,
                        details={"device_id": device_id,
                                 "dirty_reason": dirty_reason})
                else:
                    # row["state"] == "available": grant the lease,
                    # guarded by the row's own version so a concurrent
                    # writer that changed it between this SELECT and this
                    # UPDATE loses the race honestly.
                    cur = _exec(self,
                        "UPDATE device_leases SET state='leased', owner=?, "
                        "session_id=?, lease_uuid=?, leased_at=?, "
                        "ttl_seconds=?, expires_at=?, version=version+1, "
                        "updated_at=? WHERE device_id=? AND version=?",
                        (owner, session_id, lease_uuid, ts, ttl_seconds,
                         expires_at, ts, device_id, row["version"]))
                    if cur.rowcount != 1:
                        raise LeaseRefused(
                            "version-conflict",
                            "device %r changed under this claim (a "
                            "concurrent writer landed first); retry"
                            % (device_id,),
                            details={"device_id": device_id})
                    result = {"device_id": device_id,
                              "lease_uuid": lease_uuid, "state": "leased",
                              "expires_at": expires_at,
                              "recovered_from_stale": False}
        if to_raise is not None:
            raise to_raise
        return result

    def release(self, device_id, lease_uuid):
        """Refuses 'not-found', 'not-leased' (state is not 'leased'), or
        'not-lease-holder' (the stored lease_uuid does not match: this
        caller's lease may already have expired and been reclaimed by
        someone else). Otherwise atomically returns the device to
        'available'."""
        _require_device_id(device_id)
        if not isinstance(lease_uuid, str) or not lease_uuid.strip():
            raise ValueError("lease_uuid must be a non-empty string")
        ts = now_iso()
        with self._transaction():
            row = _exec(self,
                "SELECT * FROM device_leases WHERE device_id=?",
                (device_id,)).fetchone()
            if row is None:
                raise LeaseRefused(
                    "not-found", "no device %r in the lease store" % (device_id,))
            if row["state"] != "leased":
                raise LeaseRefused(
                    "not-leased",
                    "device %r is %s, not leased" % (device_id, row["state"]),
                    details={"device_id": device_id, "state": row["state"]})
            if row["lease_uuid"] != lease_uuid:
                raise LeaseRefused(
                    "not-lease-holder",
                    "device %r's current lease is %r, not %r; this caller "
                    "does not hold the active lease (it may have expired "
                    "and been reclaimed already)"
                    % (device_id, row["lease_uuid"], lease_uuid),
                    details={"device_id": device_id,
                             "current_lease_uuid": row["lease_uuid"]})
            cur = _exec(self,
                "UPDATE device_leases SET state='available', owner='', "
                "session_id='', lease_uuid='', leased_at=NULL, "
                "ttl_seconds=NULL, expires_at=NULL, version=version+1, "
                "updated_at=? WHERE device_id=? AND lease_uuid=?",
                (ts, device_id, lease_uuid))
            if cur.rowcount != 1:
                raise LeaseRefused(
                    "version-conflict",
                    "device %r changed under this release; retry" % (device_id,))
            return {"device_id": device_id, "state": "available"}

    def mark_dirty(self, device_id, reason):
        """Force state to 'dirty' regardless of current state, even
        overriding an active lease (a device whose cleanup failed mid-run
        must be quarantined immediately, not after its TTL happens to
        expire). Upserts: works even for a device_id never seen before."""
        _require_device_id(device_id)
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("reason must be a non-empty string")
        ts = now_iso()
        with self._transaction():
            row = _exec(self,
                "SELECT * FROM device_leases WHERE device_id=?",
                (device_id,)).fetchone()
            if row is None:
                _exec(self,
                    "INSERT INTO device_leases (device_id, state, "
                    "dirty_reason, dirty_at, version, created_at, updated_at) "
                    "VALUES (?, 'dirty', ?, ?, 1, ?, ?)",
                    (device_id, reason, ts, ts, ts))
            else:
                _exec(self,
                    "UPDATE device_leases SET state='dirty', owner='', "
                    "session_id='', lease_uuid='', leased_at=NULL, "
                    "ttl_seconds=NULL, expires_at=NULL, dirty_reason=?, "
                    "dirty_at=?, version=version+1, updated_at=? "
                    "WHERE device_id=?",
                    (reason, ts, ts, device_id))
            return {"device_id": device_id, "state": "dirty", "dirty_reason": reason}

    def clear_dirty(self, device_id):
        """Refuses 'not-found' or 'not-dirty'. Otherwise atomically
        returns the device to 'available', clearing dirty_reason/dirty_at:
        this is the explicit remediation step; nothing else in this module
        clears dirty on its own."""
        _require_device_id(device_id)
        ts = now_iso()
        with self._transaction():
            row = _exec(self,
                "SELECT * FROM device_leases WHERE device_id=?",
                (device_id,)).fetchone()
            if row is None:
                raise LeaseRefused(
                    "not-found", "no device %r in the lease store" % (device_id,))
            if row["state"] != "dirty":
                raise LeaseRefused(
                    "not-dirty",
                    "device %r is %s, not dirty; nothing to clear"
                    % (device_id, row["state"]),
                    details={"device_id": device_id, "state": row["state"]})
            cur = _exec(self,
                "UPDATE device_leases SET state='available', "
                "dirty_reason='', dirty_at=NULL, version=version+1, "
                "updated_at=? WHERE device_id=? AND version=?",
                (ts, device_id, row["version"]))
            if cur.rowcount != 1:
                raise LeaseRefused(
                    "version-conflict",
                    "device %r changed under this clear; retry" % (device_id,))
            return {"device_id": device_id, "state": "available"}

    def recover_stale(self, device_id):
        """Explicit sweep-one: reclaim an expired lease to 'dirty', WITHOUT
        re-leasing it to anyone (distinct from claim()'s implicit recovery,
        which quarantines dirty in the same atomic step as discovering the
        stale claim). 'dirty', not 'available', is the default on purpose
        (adversarial-review finding M2, root-caused the same way as
        claim()'s identical fix above): a lease that expired without an
        explicit release() carries no evidence the previous holder cleaned
        up, so this sweep assumes unclean rather than clean. A caller that
        has actually verified the device is fine calls clear_dirty()
        itself; recover_stale() never grants that verification for free.
        Refuses 'not-found', 'not-leased', or 'not-stale' (the lease has
        not actually expired yet; release() is the right call for that,
        this method never force-ends a still-valid lease)."""
        _require_device_id(device_id)
        ts = now_iso()
        with self._transaction():
            row = _exec(self,
                "SELECT * FROM device_leases WHERE device_id=?",
                (device_id,)).fetchone()
            if row is None:
                raise LeaseRefused(
                    "not-found", "no device %r in the lease store" % (device_id,))
            if row["state"] != "leased":
                raise LeaseRefused(
                    "not-leased",
                    "device %r is %s, not leased; nothing to recover"
                    % (device_id, row["state"]),
                    details={"device_id": device_id, "state": row["state"]})
            if row["expires_at"] is None or row["expires_at"] > ts:
                raise LeaseRefused(
                    "not-stale",
                    "device %r's lease has not expired (expires_at=%s, "
                    "now=%s); recover_stale() refuses to force-end a "
                    "still-valid lease, call release() instead"
                    % (device_id, row["expires_at"], ts),
                    details={"device_id": device_id,
                             "expires_at": row["expires_at"]})
            recovered_lease_uuid = row["lease_uuid"]
            dirty_reason = (
                "lease expired at %s without release() (owner=%r "
                "session=%r); recovered by an explicit recover_stale() "
                "sweep, previous holder may have crashed mid-run"
                % (row["expires_at"], row["owner"], row["session_id"]))
            cur = _exec(self,
                "UPDATE device_leases SET state='dirty', owner='', "
                "session_id='', lease_uuid='', leased_at=NULL, "
                "ttl_seconds=NULL, expires_at=NULL, dirty_reason=?, "
                "dirty_at=?, version=version+1, updated_at=? "
                "WHERE device_id=? AND version=?",
                (dirty_reason, ts, ts, device_id, row["version"]))
            if cur.rowcount != 1:
                raise LeaseRefused(
                    "version-conflict",
                    "device %r changed under this recovery; retry" % (device_id,))
            return {"device_id": device_id, "state": "dirty",
                    "dirty_reason": dirty_reason,
                    "recovered_lease_uuid": recovered_lease_uuid}

    # -- reads ----------------------------------------------------------

    def get(self, device_id):
        _require_device_id(device_id)
        row = _exec(self,
            "SELECT * FROM device_leases WHERE device_id=?",
            (device_id,)).fetchone()
        return _row_to_dict(row) if row is not None else None

    def list_all(self):
        rows = _exec(self,
            "SELECT * FROM device_leases ORDER BY device_id").fetchall()
        return [_row_to_dict(r) for r in rows]

    def list_stale(self, now=None):
        """Every row currently 'leased' whose TTL has already passed, as of
        `now` (defaults to now_iso()). Read-only: does not recover
        anything itself, callers sweep via recover_stale() per id.

        now is compared with `is None`, not truthiness (adversarial-review
        finding): `now or now_iso()` would silently swallow an explicit
        bad-but-falsy caller value like "" into "use current time"
        instead of raising, hiding exactly the malformed-input bug
        _require_iso_stamp exists to catch."""
        ts = now if now is not None else now_iso()
        _require_iso_stamp(ts, "now")
        rows = _exec(self,
            "SELECT * FROM device_leases WHERE state='leased' "
            "AND expires_at IS NOT NULL AND expires_at<=?",
            (ts,)).fetchall()
        return [_row_to_dict(r) for r in rows]
