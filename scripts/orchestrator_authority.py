"""ORCH-03 of the 1.0.20 orchestration control plane: authority with epochs.

WHY THIS EXISTS. Two orchestrator processes (named by neutral roles such as
primary and secondary, never by a vendor or model family: this protocol is
provider neutral so any orchestrator can take over from any other)
cooperate on one run and must never both act with authority over the same
scope at once. A lease with a TTL alone is not enough: a process can stall
(a model call hangs, a network pause) and wake up believing it still owns
the scope. If its lease has since expired and been taken over, that belief
is wrong, and a wall-clock check alone cannot stop it from acting on it,
because the woken process is not asking the clock, it is asking its own
memory.

THE ANSWER IS AN EPOCH. Authority over a scope carries a monotonically
increasing integer. Every mutation states the epoch it believes it holds.
A mutation whose epoch is not the CURRENT one is refused, whatever the wall
clock says and whatever the caller believes: check() and renew() and
release() all compare the epoch on the call to the epoch on disk, not to
the time. A woken, stale-epoch caller cannot act through this module no
matter how confident it is, because its belief is not consulted, only its
epoch number is, and that number is wrong the instant someone else has
acquired, taken over or been handed the scope.

THE CENTRAL RULE THIS MODULE ENFORCES, same as claim_store's: a store file
that cannot be read is NEVER treated as a free scope. See AuthorityUnreadable
below. A corrupt store read as free would hand the same scope's authority to
two orchestrators at once, which is the one failure this module exists to
make impossible, so every reader (acquire, renew, release, takeover,
handoff, check, current, audit) raises rather than defaulting to "nobody
holds this".

EPOCH 1 IS THE FIRST EPOCH, NEVER 0. A falsy check (`if epoch:`) must never
confuse "no epoch recorded yet" with "the first epoch is held", so the
counter starts at 1 and only ever increments.

File handling mirrors scripts/claim_store.py deliberately: the same atomic
write (temp file, fsync, os.replace) and the same cross-process exclusive
lock (claim_store.Lock, its own O_CREAT|O_EXCL primitive, exported by that
module for exactly this kind of reuse) rather than a second implementation
of either. This module owns its own store file and its own record shape;
it does not read or write claim_store's claims.json.

`now` is an injectable clock: a float epoch-seconds, defaulting to
time.time() when omitted. It is a value, not a callable, so a test drives
expiry by passing a fixed number, never by sleeping.

Python 3.9 floor, standard library only, no network.
"""
import json
import os
import tempfile
import time
from collections import namedtuple

import claim_store

NODATA = "NO-DATA"


class AuthorityRefused(Exception):
    """A mutation could not proceed: a live lease is held by someone else,
    the caller's epoch is not the current one, or there is nothing to
    renew or release. Raised, never returned as a falsy value or a NODATA
    string, so a caller cannot accidentally treat a refusal as success by
    failing to check a return value."""


class AuthorityUnreadable(Exception):
    """The store file exists but could not be parsed as the JSON object
    this module writes. Never read as "the scope is free": see the module
    docstring. The caller's obligation is the same as for any other
    NO-DATA condition in this estate: block, do not proceed as if the
    scope were open."""


#: One acquired or taken-over authority. `epoch` is never 0: see the module
#: docstring. `expires_at` is a float epoch-seconds; a caller wanting
#: liveness asks check() or current(), not this tuple's fields directly,
#: since only those two apply the one liveness rule this module has.
Lease = namedtuple(
    "Lease",
    "run_id scope orchestrator instance epoch acquired_at expires_at",
)


def _read(path):
    """The store as a dict, or {} when the file does not exist yet. Raises
    AuthorityUnreadable on anything else: a truncated write, a file that
    is not a JSON object, permission trouble. There is no code path here
    that turns "could not read this" into "this is empty"."""
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        raise AuthorityUnreadable(
            "the authority store at %s could not be read: %s" % (path, exc)
        ) from exc
    if not isinstance(data, dict):
        raise AuthorityUnreadable(
            "the authority store at %s did not hold a JSON object" % path)
    _validate_shape(data, path)
    return data


def _write(path, data):
    """Atomic: temp file plus os.replace, the same technique
    claim_store._write uses, so a crash between write and rename leaves
    the prior version on disk rather than a half-written file that the
    next reader could mistake for corruption, or worse, read as empty."""
    d = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".authority-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=1, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _now(now):
    return time.time() if now is None else float(now)


def _validate_ttl(ttl_seconds):
    """A lease with ttl <= 0 is dead the instant it is minted: _is_live
    uses `expires_at > now`, so ttl=0 makes expires_at == now, already not
    live, and a second acquire at the same clock also succeeds while both
    callers hold a Lease object that reads like success. Negative ttl is
    worse. Refused here, in every function that mints a fresh lease."""
    if float(ttl_seconds) <= 0:
        raise ValueError("ttl_seconds must be positive, got %r" % (ttl_seconds,))


def _run_bucket(data, run_id):
    return data.setdefault(run_id, {"leases": {}, "audit": []})


def _is_live(record, now):
    """A record is live only strictly before its expiry: at now ==
    expires_at exactly, the lease is already treated as expired, not live
    for one more instant. The comparison is `>`, never `>=`; changing it
    would let a caller renew or act on a lease in the same tick it expires,
    which is the boundary CorruptStoreIsNeverFree's sibling test class
    checks for."""
    return (record is not None and record.get("state") == "active"
            and float(record["expires_at"]) > now)


_RECORD_KEYS = ("orchestrator", "instance", "epoch", "acquired_at",
                "expires_at", "state")


def _validate_shape(data, path):
    """Raise AuthorityUnreadable when the JSON parsed fine but is not the
    shape this module writes. Without this, a bucket missing "leases" or a
    record missing a required key reads as an empty (therefore free) scope
    from current()/check()/audit() while every mutator crashes with a bare
    KeyError instead of the documented AuthorityUnreadable, so a caller
    written to the documented contract (catch AuthorityUnreadable, block)
    either wrongly proceeds or takes an exception it was never told about."""
    for run_id, bucket in data.items():
        if not isinstance(bucket, dict) or "leases" not in bucket or "audit" not in bucket:
            raise AuthorityUnreadable(
                "the authority store at %s has a malformed bucket for run %s"
                % (path, run_id))
        if not isinstance(bucket["leases"], dict) or not isinstance(bucket["audit"], list):
            raise AuthorityUnreadable(
                "the authority store at %s has a malformed bucket for run %s"
                % (path, run_id))
        for scope, record in bucket["leases"].items():
            if not isinstance(record, dict) or any(k not in record for k in _RECORD_KEYS):
                raise AuthorityUnreadable(
                    "the authority store at %s has a malformed lease record "
                    "for run %s scope %s" % (path, run_id, scope))


def _to_lease(run_id, scope, record):
    return Lease(run_id=run_id, scope=scope, orchestrator=record["orchestrator"],
                 instance=record["instance"], epoch=record["epoch"],
                 acquired_at=record["acquired_at"], expires_at=record["expires_at"])


def _record_transition(data, run_id, kind, scope, old_owner, new_owner,
                        old_epoch, new_epoch, reason, now):
    """Append one entry to the run's audit trail. Called from inside the
    same lock and the same _write() as the mutation it describes, so the
    trail and the state it describes can never disagree about whether a
    transition happened."""
    bucket = _run_bucket(data, run_id)
    bucket["audit"].append({
        "type": kind, "scope": scope, "old_owner": old_owner,
        "new_owner": new_owner, "old_epoch": old_epoch, "new_epoch": new_epoch,
        "reason": reason, "at": now,
    })


def acquire(store_path, run_id, scope, orchestrator, instance, ttl_seconds,
            now=None):
    """Take authority over `scope`. Succeeds on a free or expired scope
    and returns a Lease at the next epoch (1, the first time). Raises
    AuthorityRefused when a live lease is held by someone else, whoever
    that is: acquire() is the entry to a scope nobody currently holds, not
    a way to re-confirm a lease already yours (that is renew())."""
    _validate_ttl(ttl_seconds)
    now = _now(now)
    with claim_store.Lock(store_path):
        data = _read(store_path)
        bucket = _run_bucket(data, run_id)
        record = bucket["leases"].get(scope)
        if _is_live(record, now):
            raise AuthorityRefused(
                "scope %s of run %s is held live by %s (orchestrator %s) "
                "for %.0fs more; acquire() never takes a live scope, use "
                "takeover() only once it has expired"
                % (scope, run_id, record["instance"], record["orchestrator"],
                   float(record["expires_at"]) - now))
        prev_epoch = record["epoch"] if record else 0
        old_epoch = prev_epoch if record is not None else None
        old_owner = record["instance"] if record else None
        epoch = prev_epoch + 1
        new_record = {"orchestrator": orchestrator, "instance": instance,
                      "epoch": epoch, "acquired_at": now,
                      "expires_at": now + float(ttl_seconds), "state": "active"}
        bucket["leases"][scope] = new_record
        _record_transition(data, run_id, "acquire", scope, old_owner, instance,
                            old_epoch, epoch, None, now)
        _write(store_path, data)
        return _to_lease(run_id, scope, new_record)


def renew(store_path, run_id, scope, instance, epoch, ttl_seconds, now=None):
    """Push the expiry out, only when `instance` and `epoch` both match
    the current record exactly AND that record is still live. A renew from
    a stale epoch (the caller held an earlier epoch that someone else has
    since taken over) is refused and changes nothing: the live lease's
    expiry is left exactly as it was before this call. The liveness check
    matters on its own: without it, a renew arriving after the lease has
    expired (and nobody has taken it over yet) would fully resurrect it,
    and a renew arriving after release() would return a live-looking Lease
    while the stored state stays "released", so current() still reports
    the scope free and a third party can acquire it while this caller
    holds an object that reads like success."""
    now = _now(now)
    with claim_store.Lock(store_path):
        data = _read(store_path)
        bucket = _run_bucket(data, run_id)
        record = bucket["leases"].get(scope)
        if (record is None or record["instance"] != instance
                or record["epoch"] != epoch or not _is_live(record, now)):
            raise AuthorityRefused(
                "scope %s of run %s: renew from %s at epoch %s does not "
                "match a live current record (%s); a stale, expired or "
                "released renew never extends a lease it no longer owns"
                % (scope, run_id, instance, epoch,
                   "no record" if record is None else
                   "%s at epoch %s, state %s" % (
                       record["instance"], record["epoch"], record["state"])))
        record["expires_at"] = now + float(ttl_seconds)
        bucket["leases"][scope] = record
        _record_transition(data, run_id, "renew", scope, instance, instance,
                            epoch, epoch, None, now)
        _write(store_path, data)
        return _to_lease(run_id, scope, record)


def release(store_path, run_id, scope, instance, epoch, now=None):
    """Close a lease. Refused when `instance`/`epoch` do not match the
    current record (you do not own what you are trying to release).
    Idempotent when the record already reads released under this same
    instance and epoch: a crash-retry re-sending its own release must
    succeed quietly, not turn a completed shutdown into an error."""
    now = _now(now)
    with claim_store.Lock(store_path):
        data = _read(store_path)
        bucket = _run_bucket(data, run_id)
        record = bucket["leases"].get(scope)
        if record is None:
            raise AuthorityRefused(
                "scope %s of run %s holds no lease to release" % (scope, run_id))
        if record["instance"] != instance or record["epoch"] != epoch:
            raise AuthorityRefused(
                "scope %s of run %s is held by %s at epoch %s, not %s at "
                "epoch %s; a stale release never touches a lease it no "
                "longer owns"
                % (scope, run_id, record["instance"], record["epoch"],
                   instance, epoch))
        if record["state"] == "released":
            return  # idempotent: a crash-retry must not become an error
        record["state"] = "released"
        record["released_at"] = now
        record["expires_at"] = now
        bucket["leases"][scope] = record
        _record_transition(data, run_id, "release", scope, instance, None,
                            epoch, epoch, None, now)
        _write(store_path, data)


def takeover(store_path, run_id, scope, orchestrator, instance, ttl_seconds,
             reason, now=None):
    """Reclaim a scope whose lease has expired. Raises AuthorityRefused
    when `now` (real time.time() by default) finds the current lease still
    live: there is no force ARGUMENT, so no caller passing this module's
    real, default clock can steal a live lease. That guarantee is about
    the argument list, not about the clock: `now` is deliberately an
    injectable value so tests can drive expiry without sleeping (see the
    module docstring), and a caller who injects a `now` far ahead of the
    real clock can make an actually-live lease read as expired, the same
    way it could feed a false clock to any other function here. This
    module trusts its caller's clock exactly as much as it trusts every
    other argument; production callers must pass the default (real
    time.time()), never a value from an untrusted source. Increments the
    epoch and records the former owner, the new owner and `reason` in the
    audit trail."""
    _validate_ttl(ttl_seconds)
    now = _now(now)
    with claim_store.Lock(store_path):
        data = _read(store_path)
        bucket = _run_bucket(data, run_id)
        record = bucket["leases"].get(scope)
        if _is_live(record, now):
            raise AuthorityRefused(
                "scope %s of run %s is held live by %s; a live lease cannot "
                "be taken over, there is no force path through this module"
                % (scope, run_id, record["instance"]))
        prev_epoch = record["epoch"] if record else 0
        old_epoch = prev_epoch if record is not None else None
        old_owner = record["instance"] if record else None
        epoch = prev_epoch + 1
        new_record = {"orchestrator": orchestrator, "instance": instance,
                      "epoch": epoch, "acquired_at": now,
                      "expires_at": now + float(ttl_seconds), "state": "active"}
        bucket["leases"][scope] = new_record
        _record_transition(data, run_id, "takeover", scope, old_owner, instance,
                            old_epoch, epoch, reason, now)
        _write(store_path, data)
        return _to_lease(run_id, scope, new_record)


def handoff(store_path, run_id, scope, from_instance, from_epoch,
            to_orchestrator, to_instance, ttl_seconds, reason, now=None):
    """A deliberate transfer, distinct from takeover(): the CURRENT owner
    is asking to hand the scope to a named successor, not reclaiming an
    abandoned one. Refused when `from_instance`/`from_epoch` do not match
    the current record, or when that record is not live: an expired or
    already-released lease has nothing left for its former holder to hand
    off, and without this check a stale handoff call could resurrect it
    the same way an unguarded renew could (see renew's docstring). The old
    lease is closed first, in the same write as the new one being opened,
    so no reader of the store ever sees the scope held by both sides at
    once; the epoch still increments, and the transition is recorded with
    `reason`."""
    _validate_ttl(ttl_seconds)
    now = _now(now)
    with claim_store.Lock(store_path):
        data = _read(store_path)
        bucket = _run_bucket(data, run_id)
        record = bucket["leases"].get(scope)
        if (record is None or record["instance"] != from_instance
                or record["epoch"] != from_epoch or not _is_live(record, now)):
            raise AuthorityRefused(
                "scope %s of run %s: handoff from %s at epoch %s does not "
                "match a live current record (%s); a stale, expired or "
                "released handoff never closes a lease it no longer owns"
                % (scope, run_id, from_instance, from_epoch,
                   "no record" if record is None else
                   "%s at epoch %s, state %s" % (
                       record["instance"], record["epoch"], record["state"])))
        # The old owner's lease is closed FIRST. Both the close and the new
        # lease land in the one _write() below, so there is no window on
        # disk where the scope reads as held by both instances.
        new_epoch = from_epoch + 1
        new_record = {"orchestrator": to_orchestrator, "instance": to_instance,
                      "epoch": new_epoch, "acquired_at": now,
                      "expires_at": now + float(ttl_seconds), "state": "active"}
        bucket["leases"][scope] = new_record
        _record_transition(data, run_id, "handoff", scope, from_instance,
                            to_instance, from_epoch, new_epoch, reason, now)
        _write(store_path, data)
        return _to_lease(run_id, scope, new_record)


def check(store_path, run_id, scope, instance, epoch, now=None):
    """The guard every caller uses before any mutation of its own outside
    this module: True only when `instance` holds `scope` at exactly
    `epoch` and the lease is currently live. Raises AuthorityUnreadable on
    a corrupt store rather than returning False, because "unreadable" and
    "you do not hold this" are different facts and a caller must not act
    on the wrong one."""
    now = _now(now)
    data = _read(store_path)
    record = data.get(run_id, {}).get("leases", {}).get(scope)
    return (record is not None and record["instance"] == instance
            and record["epoch"] == epoch and _is_live(record, now))


def current(store_path, run_id, scope, now=None):
    """The live Lease for `scope`, or None when it is free (never
    acquired, expired, or released). Raises AuthorityUnreadable on a
    corrupt store: see the module docstring, a caller must never read
    "could not parse this" as "free"."""
    now = _now(now)
    data = _read(store_path)
    record = data.get(run_id, {}).get("leases", {}).get(scope)
    if not _is_live(record, now):
        return None
    return _to_lease(run_id, scope, record)


def audit(store_path, run_id):
    """The append-only list of transitions recorded for this run, oldest
    first. Empty when the run has none yet. Raises AuthorityUnreadable on
    a corrupt store, same as every other reader here."""
    data = _read(store_path)
    return list(data.get(run_id, {}).get("audit", []))
