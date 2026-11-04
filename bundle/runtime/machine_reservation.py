"""machine_reservation: ONE reservation for the whole machine, with a dead-holder expiry.

WHY: thirteen agent processes from several sessions drove tonight's load, with
nothing admitting heavy work deliberately. This estate already throttles itself
to one writer when disk is low; the same shape belongs at the machine level so
one holder is admitted at a time and a crashed holder cannot block every later
battery behind a corpse. This is not a per-unit claim (that is claim_store.py's
job, keyed by unit_id): it is a single global slot, one holder or none.

REUSE, NOT A SECOND IMPLEMENTATION: dead-owner detection (a pid check that only
applies on the same host, permission-denied and other OSErrors read as alive
rather than dead) is claim_store.pid_alive, imported here rather than
re-derived. The atomic write (temp file in the same directory, fsync, then
os.replace, never edited in place) mirrors claim_store._write for the same
reason claim_store gives: a reservation written but not on disk when the power
goes is exactly the failure this module exists to survive.

CONTINGENCY AND EDGES, named here because the docstring is where the next
reader looks before trusting this module:

  TWO ACQUIRES RACING (same process, sequential calls). No same-holder
  exemption: a live holder blocks acquire() even when it is the caller's own
  earlier grant. A holder that wants to extend its own lease calls renew(),
  not acquire() again; acquire() answers "is the machine free", not "is it
  free for me".

  A HOLDER RENEWING AFTER EXPIRY. renew() refuses once the stored lease is no
  longer live (expired, or the pid is dead on this host), even when the
  `holder` string still matches the name on record: an expired lease is not
  yours to extend, it is available to acquire() (which will name the reclaim).

  A CLOCK THAT GOES BACKWARDS. Every reading comes from the injected `clock`
  callable, never wall-clock time.time() directly inside a comparison. All
  liveness math is `expires_at <= now`, a plain float comparison: if `now`
  moves backwards, the record simply reads as still-live again (the
  conservative direction), never raises, never manufactures a negative TTL.

  TTL OF ZERO OR NEGATIVE. Refused outright by acquire() and renew(): a
  reservation that does not protect any span of future time is not a
  reservation, and granting one would make every immediately-following
  acquire() see it as already expired, which is worse than refusing it.

  A STATE FILE THAT EXISTS BUT IS EMPTY. Not proof the machine is free: a
  write that died before its content landed looks identical on disk to a
  deliberately empty store. Read as NO-DATA, which REFUSES acquire() rather
  than treating the machine as free. A missing file (never written at all)
  is the one case that legitimately means free.

  A CORRUPT STATE FILE (unparseable JSON, or JSON that is not an object).
  Same NO-DATA refusal as the empty case, for the same reason: an unreadable
  claim to safety is not evidence of safety.

  LEASES (CUT2.e, 2026-11-02). Beside the one holder slot the same file carries a
  ledger of CAPACITY LEASES under data["leases"]: nested cut jobs (the perturbation
  workers, each running a whole suite that itself fans out) lease cores, memory
  and disk out of one stated capacity, so admission counts what the work will
  demand, not what a load average showed before it ramped up. The holder slot and
  the ledger never touch each other's keys. A lease dies by the SAME rule as the
  holder (expired, or its pid gone on this host: _dead_reason), so a crashed
  nested job frees its share by itself. lease() is read, check and write under
  one flock on <path>.lock, bounded: a lock held past LOCK_WAIT_S is NO-DATA, not
  a wait for ever. A refusal for want of capacity starts with CAPACITY: (the one
  recoverable stop: a lease ending frees it); NO-DATA: means the ledger could not
  be trusted; anything else is a bad request. A resource named in the demand but
  absent from the capacity, a negative or non finite number, or a name already
  leased by a live record is refused, never granted. A DEAD record under the same
  name is reclaimed (the actor restarted under the same name).

  AN ACQUIRE WHILE A REAP IS DUE. acquire() reclaims a dead holder inline
  (naming it in `reclaimed_from`) without needing reap() to have run first.
  reap() exists for a caller with nothing to acquire yet, that just wants
  the dead state cleared and recorded (a periodic sweep, or a report between
  batteries): it never runs implicitly inside acquire()/renew()/release(), so
  calling only reap() and never acquiring still leaves an accurate audit
  trail of what died and when.

Python 3, standard library only, plus claim_store.pid_alive from this same
estate. No network.
"""
import contextlib
import fcntl
import json
import math
import os
import socket
import tempfile
import time

import claim_store

NODATA = "NO-DATA"
CAPACITY = "CAPACITY"
#: How long lease() waits for the ledger's own lock before it answers NO-DATA.
LOCK_WAIT_S = 10.0


def _now(clock=None):
    return (clock or time.time)()


def _hostname():
    return socket.gethostname()


def _read(path):
    """(data, problem). data is None on anything that is not trustworthy
    evidence of the machine's real state: an unreadable file, one that
    exists but is empty, or content that is not a JSON object. Only a
    genuinely missing file reads as {} (free, nobody has ever reserved it).
    Callers that get None must refuse rather than treat the machine as
    free; only the acquire()/renew()/release()/reap() entry points below
    that use this are trusted to translate None into that refusal."""
    if not os.path.exists(path):
        return {}, ""
    try:
        with open(path, encoding="utf-8") as fh:
            content = fh.read()
    except (OSError, UnicodeDecodeError) as exc:   # bytes that are not text are an unreadable ledger, not a crash
        return None, "the reservation file could not be read: %s" % exc
    if content.strip() == "":
        return None, "the reservation file at %s exists but is empty" % path
    try:
        data = json.loads(content)
    except ValueError as exc:
        return None, "the reservation file could not be parsed: %s" % exc
    if not isinstance(data, dict):
        return None, "the reservation file did not contain a JSON object"
    return data, ""


def _write(path, data):
    """Atomic: write to a temp file in the same directory, fsync, then
    os.replace. Never edited in place, so a crash mid-write leaves whatever
    was there before intact rather than a half-written record."""
    d = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".machine-reservation-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=1, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _dead_reason(res, now):
    """Why the holder record `res` is not live, or None when it is still
    live. EITHER its lease expired OR its owning pid is gone on this host
    (never the other host's pid namespace: a foreign hostname falls back to
    pure time-based expiry). Time direction wins even against a live pid: a
    lease past its expiry is dead on schedule regardless of who is still
    running."""
    expires = float(res.get("expires_at", 0))
    if expires <= now:
        return "the lease expired %.0fs ago" % (now - expires)
    pid = res.get("pid")
    if pid and res.get("hostname") == _hostname() and not claim_store.pid_alive(pid):
        return ("holder pid %s is dead on this host, with %.0fs still left "
                "on the lease" % (pid, expires - now))
    return None


def _live(res, now):
    return _dead_reason(res, now) is None


def _check_ttl(ttl):
    """None on a usable ttl, else the refusal message. A ttl of zero or
    negative protects no span of future time, so it is refused rather than
    silently granted and read as already-expired by the next caller."""
    try:
        value = float(ttl)
    except (TypeError, ValueError):
        return "ttl must be a positive number of seconds, got %r" % (ttl,)
    if value <= 0:
        return "ttl must be a positive number of seconds, got %r" % (ttl,)
    return None


def acquire(path, holder, ttl, clock=None):
    """(reservation, problem). Grants the one machine reservation to `holder`
    only when it is free (never reserved, or the current holder is dead by
    _dead_reason above). A live holder, including `holder` itself calling
    again, is refused, naming who holds it and for how long: use renew() to
    extend a reservation you already hold."""
    bad_ttl = _check_ttl(ttl)
    if bad_ttl is not None:
        return None, bad_ttl
    now = _now(clock)
    data, problem = _read(path)
    if data is None:
        return None, "%s: %s" % (NODATA, problem)
    held = data.get("holder")
    if held and _live(held, now):
        return None, ("the machine is reserved by %s for %.0fs more"
                       % (held.get("holder"), float(held["expires_at"]) - now))
    reservation = {
        "holder": holder,
        "pid": os.getpid(),
        "hostname": _hostname(),
        "acquired_at": now,
        "expires_at": now + float(ttl),
        "reclaimed_from": held.get("holder") if held else None,
    }
    data["holder"] = reservation
    _write(path, data)
    return reservation, ""


def renew(path, holder, ttl, clock=None):
    """(reservation, problem). Extends the lease for `holder`, only when
    `holder` is the current, still-live holder. A non-holder renewing is
    refused. A holder whose own lease has already expired is also refused
    (dead means dead: it is available to acquire(), not to renew())."""
    bad_ttl = _check_ttl(ttl)
    if bad_ttl is not None:
        return None, bad_ttl
    now = _now(clock)
    data, problem = _read(path)
    if data is None:
        return None, "%s: %s" % (NODATA, problem)
    held = data.get("holder")
    if not held:
        return None, "the machine holds no reservation to renew"
    if held.get("holder") != holder:
        return None, ("the machine is reserved by %s, not %s"
                       % (held.get("holder"), holder))
    if not _live(held, now):
        return None, "%s: renew refused" % _dead_reason(held, now)
    held["expires_at"] = now + float(ttl)
    data["holder"] = held
    _write(path, data)
    return held, ""


def release(path, holder, clock=None):
    """(reservation, problem). Frees the machine, only when `holder` is the
    name on the current record. Releasing a reservation you do not hold
    (nothing reserved, or reserved by someone else) is refused rather than
    silently succeeding: a silent success there would let an unrelated actor
    free a live holder's lease out from under it."""
    now = _now(clock)
    data, problem = _read(path)
    if data is None:
        return None, "%s: %s" % (NODATA, problem)
    held = data.get("holder")
    if not held:
        return None, "the machine holds no reservation to release"
    if held.get("holder") != holder:
        return None, ("the machine is reserved by %s, so %s may not release it"
                       % (held.get("holder"), holder))
    held["released_at"] = now
    data["holder"] = None
    data["last_released"] = held
    _write(path, data)
    return held, ""


def reap(path, now_value=None, clock=None):
    """(detail, problem). Expires the current holder ONLY when it is
    genuinely dead (its lease has passed, or its pid is gone on this host),
    and records that the expiry happened by moving the record into
    `last_reaped` in the same atomic write. Never acts on a live holder,
    however close to expiry.

    `now_value` overrides the clock reading for this one call (a caller
    checking "is a reap due right now" without needing its own clock
    plumbing); `clock` is used when `now_value` is None, exactly like every
    other entry point here.

    Returns (None, "") when there was nothing to reap (no holder recorded,
    or the holder is still live): that is not an error, just nothing to do,
    so problem stays empty and the caller tells the two apart by whether
    detail is None."""
    now = now_value if now_value is not None else _now(clock)
    data, problem = _read(path)
    if data is None:
        return None, "%s: %s" % (NODATA, problem)
    held = data.get("holder")
    if not held:
        return None, ""
    reason = _dead_reason(held, now)
    if reason is None:
        return None, ""
    detail = dict(held)
    detail["reaped_at"] = now
    detail["reaped_reason"] = reason
    data["holder"] = None
    data["last_reaped"] = detail
    _write(path, data)
    return detail, ""


# ------------------------------------------------------------------ leases --

@contextlib.contextmanager
def _locked(path, wait_s=None):
    """flock(<path>.lock), exclusive, polled for at most wait_s seconds; TimeoutError past that. The lock file is
    never removed (removing it is the race it exists to close); it dies with the descriptor, so a crashed holder
    frees it."""
    wait_s = LOCK_WAIT_S if wait_s is None else wait_s
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    handle = open(path + ".lock", "a+")
    try:
        end = time.monotonic() + wait_s
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= end:
                    raise TimeoutError("the ledger lock %s.lock was not free within %.0fs" % (path, wait_s))
                time.sleep(0.02)
        yield
    finally:
        handle.close()


def _amounts(what, label):
    """(dict of float, problem): a non empty mapping of resource name to a finite, non negative number. Anything
    else (a bool, a string, NaN, an infinity, a negative) is a refusal, never read as zero."""
    if not isinstance(what, dict) or not what:
        return None, "%s must name at least one resource, got %r" % (label, what)
    out = {}
    for key, value in what.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            return None, "%s[%r] must be a finite number of at least 0, got %r" % (label, key, value)
        out[str(key)] = float(value)
    return out, ""


def _live_leases(data, now):
    """{name: record} of the leases still live. None when data["leases"] is present but is not a mapping of
    records (a corrupt ledger is not an empty one)."""
    raw = data.get("leases", {})
    if not isinstance(raw, dict) or not all(isinstance(v, dict) and isinstance(v.get("demand"), dict)
                                            for v in raw.values()):
        return None
    return {n: r for n, r in raw.items() if _live(r, now)}


def _sum(records):
    used = {}
    for rec in records.values():
        for key, value in rec["demand"].items():
            used[key] = used.get(key, 0.0) + float(value)
    return used


def leased(path, clock=None):
    """(used, problem): per resource, the sum of every live lease. A missing file is an empty ledger; an unreadable
    or corrupt one is (None, NO-DATA ...)."""
    now = _now(clock)
    data, problem = _read(path)
    if data is None:
        return None, "%s: %s" % (NODATA, problem)
    live = _live_leases(data, now)
    if live is None:
        return None, "%s: the leases in %s are not a mapping of records" % (NODATA, path)
    return _sum(live), ""


def lease(path, name, demand, capacity, ttl, clock=None):
    """(record, problem). Leases `demand` (resource to amount) to `name` when every resource still fits under
    `capacity` after the live leases are counted. The capacity is the caller's current reading (disk free changes),
    so the ledger holds only what was promised. Refused with CAPACITY: when one resource would pass its capacity
    (the message names which, the one recoverable stop), with NO-DATA: when the ledger cannot be trusted, and
    plainly otherwise for a bad request."""
    bad = _check_ttl(ttl)
    if bad is not None:
        return None, bad
    want, problem = _amounts(demand, "demand")
    if want is None:
        return None, problem
    room, problem = _amounts(capacity, "capacity")
    if room is None:
        return None, problem
    absent = sorted(k for k in want if k not in room)
    if absent:
        return None, "%s: no capacity is stated for %s, so it cannot be leased" % (NODATA, ", ".join(absent))
    if not isinstance(name, str) or not name:
        return None, "a lease needs a name, got %r" % (name,)
    now = _now(clock)
    try:
        with _locked(path):
            data, problem = _read(path)
            if data is None:
                return None, "%s: %s" % (NODATA, problem)
            live = _live_leases(data, now)
            if live is None:
                return None, "%s: the leases in %s are not a mapping of records" % (NODATA, path)
            if name in live:
                return None, "%s is already leased (pid %s)" % (name, live[name].get("pid"))
            used = _sum(live)
            for key in sorted(want):
                if used.get(key, 0.0) + want[key] > room[key] + 1e-9:
                    return None, ("%s: %s needs %g, %g of %g is already leased"
                                  % (CAPACITY, key, want[key], used.get(key, 0.0), room[key]))
            record = {"holder": name, "pid": os.getpid(), "hostname": _hostname(), "acquired_at": now,
                      "expires_at": now + float(ttl), "demand": want}
            live[name] = record
            data["leases"] = live
            _write(path, data)
            return record, ""
    except (TimeoutError, OSError) as exc:
        return None, "%s: the ledger %s could not be used: %s" % (NODATA, path, exc)


def end_lease(path, name, clock=None):
    """(record, problem). Ends the lease `name`, and only that one. A name with no live lease is (None, a plain
    message): ending twice is harmless to the caller, and never removes another actor's share."""
    if not os.path.exists(path):   # nothing to end: a read-shaped call creates no folder and no lock file
        return None, "no live lease named %s" % (name,)
    now = _now(clock)
    try:
        with _locked(path):
            data, problem = _read(path)
            if data is None:
                return None, "%s: %s" % (NODATA, problem)
            live = _live_leases(data, now)
            if live is None:
                return None, "%s: the leases in %s are not a mapping of records" % (NODATA, path)
            if name not in live:
                return None, "no live lease named %s" % (name,)
            gone = live.pop(name)
            data["leases"] = live
            _write(path, data)
            return gone, ""
    except (TimeoutError, OSError) as exc:
        return None, "%s: the ledger %s could not be used: %s" % (NODATA, path, exc)
