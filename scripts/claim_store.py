"""claim_store: no worker starts before its claim exists, and two never own one unit.

PARITY BLOCKER P0.1, the heaviest cell on the board at 15 percent and the reason
the team adoption gate is shut. The dispatch machinery all exists: the scheduler
builds a conflict-free ready set, the bridge runs a batch concurrently in
isolated worktrees, the worker spawns a real process, the verifier gives three
verdicts and the repair loop is bounded. The product path stops before all of it
and prints, verbatim, "the live claim path is not wired yet".

WHAT WAS ACTUALLY MISSING is not a scheduler and not an executor. It is the one
piece of state that makes autonomous execution safe to leave alone: a DURABLE,
EXCLUSIVE claim. Without it, two sessions reading the same ready set both start
the same unit, and the first anybody knows is a conflict at integration.

THE THREE INVARIANTS, named by the directive and enforced here:

  CLAIM BEFORE SPAWN. A worker never starts before its claim exists. The claim
  is written and fsynced first, so a crash between claiming and spawning leaves
  a claim with no worker, which reconcile can see. The other order leaves a
  worker with no claim, which nothing can see.

  EXCLUSIVE LEASE. Two workers cannot own the same unit at once. Enforced by an
  atomic create of a lock file, which is the one primitive POSIX gives that two
  processes cannot both win.

  ATTEMPT IDENTITY. Every run is tied to work_id, unit_id, attempt and
  worker_id, so a result can be matched to the run that produced it rather than
  to whatever ran most recently.

LEASES EXPIRE, and that is what makes a crash survivable rather than permanent.
A session that dies holding claims does not hold them forever; a later
reconcile finds them expired and says so. This estate has a standing rule that a
claim which cannot retire itself will hold its paths until a human intervenes,
learned from a fence that did exactly that.

A DEAD OWNER NEEDS NO WAIT AT ALL, on the same host: the owning pid is on the
claim already, so `live()` also checks it (os.kill(pid, 0)) and calls a claim
dead the moment its owner is gone, rather than waiting out the full lease.
Measured cost of not doing this: crash recovery in the 2026-08-30 head-to-head
waited ~1200 seconds for a lease that could not possibly still be live. The
pid check runs only when the claim's own hostname matches this host, so a
claim genuinely held on another machine still falls back to pure time-based
expiry.

RECLAIMING IS NEVER SILENT. An expired claim is reported with its former owner
and how long it was dead before anything reuses the unit, because a claim
reclaimed quietly is indistinguishable from one that was never taken.

Python 3, standard library only. No network.

origin: claim_store.py is a library module with no CLI of its own (verified:
no `def main` and no `if __name__ == "__main__"` block in this file). The real
producer of a claim record is loop_bridge.py, the batch dispatcher: its main()
calls claim_store.acquire(store, node["id"], owner, work_id=args.work_id) at
scripts/loop_bridge.py line 611, once per dispatchable unit, before that
unit's worker is spawned, matching this module's own CLAIM BEFORE SPAWN
invariant stated above. release() and reconcile() are called from the same
loop (scripts/loop_bridge.py lines 578 and 676). Acceptance and test files
(scripts/acceptance_2.py, scripts/test_claim_store.py, and others found by
grep -rln claim_store scripts) call acquire()/release()/reconcile() directly
too, but only to exercise this module in isolation, not as a production path.

PRODUCER: this module is the sole producer of its own claim store file. Every
public mutator (acquire() at line 230, renew() at line 261, release() at line
283, reconcile() at line 310) goes through _write() (defined at line 85),
whose actual write is json.dump(data, fh, indent=1, sort_keys=True) at line 96
inside an fsync-then-os.replace atomic write.
"""
import errno
import json
import os
import socket
import sys
import tempfile
import time

NODATA = "NO-DATA"

#: How long a claim survives without being renewed. Twenty minutes matches the
#: progress deadline this estate already uses, deliberately: a worker that has
#: made no durable progress in twenty minutes is already the subject of a stall
#: verdict, so a lease that outlives that would keep a unit locked to a worker
#: everything else has given up on.
DEFAULT_TTL_SECONDS = 20 * 60


def _now(clock=None):
    return (clock or time.time)()


def _read(path):
    """The store, or {} when absent. A torn file is NOT silently emptied."""
    if not os.path.exists(path):
        return {}, ""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return (data if isinstance(data, dict) else {}), ""
    except (OSError, ValueError) as exc:
        return None, "the claim store could not be read: %s" % exc


def _write(path, data):
    """Atomic, and fsynced before the rename.

    A claim that is written but not on disk when the power goes is exactly the
    case this whole module exists to survive, so the durability is not
    decorative."""
    d = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".claims-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=1, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


class _Lock(object):
    """An exclusive lock across PROCESSES, not just threads.

    O_CREAT|O_EXCL is the one primitive POSIX gives where two processes cannot
    both succeed. A threading lock would be enough for one session and is
    exactly the wrong answer here, because the failure being prevented is two
    SESSIONS reading the same ready set.

    A holder that dies leaves this file behind forever unless something
    notices, which is the same dead-owner gap live() closes for claim
    records, so it reuses the same pieces: the owning pid and hostname are
    written into the lock file at acquire, and a waiter that hits EEXIST
    reads them back before it commits to sitting out the whole timeout."""

    def __init__(self, path, timeout=10.0, clock=None):
        self.path, self.timeout, self.clock = path + ".lock", timeout, clock
        self.fd = None

    def __enter__(self):
        deadline = _now(self.clock) + self.timeout
        while True:
            try:
                self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(self.fd, ("%d:%s" % (os.getpid(), _hostname())).encode())
                return self
            except OSError as exc:
                if exc.errno != errno.EEXIST:
                    raise
                if self._reclaim_if_dead():
                    continue  # a stale lock just got removed, try again at once
                if _now(self.clock) >= deadline:
                    raise TimeoutError(
                        "another process has held the claim store lock at %s for "
                        "more than %.0fs. That is not a reason to proceed without "
                        "it" % (self.path, self.timeout))
                time.sleep(0.02)

    def _reclaim_if_dead(self):
        """True when a stale lock was just removed and the caller should
        retry the acquire immediately, rather than sitting out the timeout.

        PID unreadable, malformed, or written by another host: never guess,
        return False so the existing timeout wait is unchanged. Only a pid
        that is both on THIS host and verifiably gone (pid_alive, the same
        check live() uses for claim records) gets reclaimed, and reclaiming
        is never silent: the dead pid and the reclaim are printed.

        THE KNOWN MICRO-RACE: two waiters can both read this same dead-owner
        content and both decide to reclaim. unlink-then-O_EXCL is still the
        only exclusion point, so at most one of their subsequent creates in
        __enter__ actually wins; the loser's create fails with EEXIST again,
        it reads back the winner's freshly written, live pid, and falls
        through to the ordinary wait. That never hands two processes the
        lock, so the race is tolerated by construction, not solved away."""
        try:
            with open(self.path, "rb") as fh:
                pid_s, _, hostname = fh.read().decode().partition(":")
            pid = int(pid_s)
        except (OSError, ValueError):
            return False
        if hostname != _hostname() or pid_alive(pid):
            return False
        try:
            os.unlink(self.path)
        except FileNotFoundError:
            return True  # another waiter already reclaimed it; try again
        print("claim_store: reclaiming lock %s, owning pid %d is dead"
              % (self.path, pid), file=sys.stderr)
        return True

    def __exit__(self, *exc):
        if self.fd is not None:
            os.close(self.fd)
            try:
                os.unlink(self.path)
            except OSError as e:
                # Never raise out of __exit__: that would replace whatever
                # exception the with-block was already raising. But this file's
                # own law is "reclaiming is never silent", and a lock that fails
                # to release is a stuck lock, so it is named on stderr rather
                # than only surfacing minutes later as an unexplained TimeoutError.
                print("claim_store: could not release lock %s: %s"
                      % (self.path, e), file=sys.stderr)
        return False


def _hostname():
    return socket.gethostname()


def pid_alive(pid):
    """Is a process with this pid running, as far as os.kill(pid, 0) can tell.

    No process (ProcessLookupError) means gone. Permission denied means the
    process exists but is owned by someone else, which counts as alive: we
    have no business deciding a claim is dead just because we lack the
    signal-sending permission to check it directly. Any other OSError is
    ambiguous, not proof of death, so it also reads alive."""
    if not pid:
        return True
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return True


def live(claim, now):
    """A claim is dead when EITHER its lease expired OR its owning pid is gone
    ON THIS HOST. The pid check never runs against a different host's claim
    (its pid namespace means nothing here), so a claim from elsewhere falls
    back to pure time-based expiry, unchanged. A live pid never overrides an
    expired lease: the time direction still reclaims on schedule regardless."""
    if float(claim.get("expires_at", 0)) <= now:
        return False
    pid = claim.get("pid")
    if pid and claim.get("hostname") == _hostname() and not pid_alive(pid):
        return False
    return True


def acquire(path, unit_id, owner, work_id="", ttl=DEFAULT_TTL_SECONDS,
            clock=None, attempt=None):
    """(claim, problem). Exclusive. Never returns a claim somebody else holds."""
    now = _now(clock)
    try:
        with _Lock(path, clock=clock):
            data, problem = _read(path)
            if data is None:
                return None, problem
            held = data.get(unit_id)
            if held and live(held, now) and held.get("owner") != owner:
                return None, ("unit %s is claimed by %s until %.0fs from now. A "
                              "second worker must not start on it"
                              % (unit_id, held.get("owner"),
                                 float(held["expires_at"]) - now))
            n = int(attempt if attempt is not None
                    else (held.get("attempt", 0) + 1 if held else 1))
            claim = {"unit_id": unit_id, "owner": owner, "work_id": work_id,
                     "attempt": n, "worker_id": "%s/%s/%d" % (owner, unit_id, n),
                     "pid": os.getpid(), "hostname": _hostname(), "claimed_at": now,
                     "expires_at": now + float(ttl), "state": "claimed",
                     "reclaimed_from": (held.get("owner")
                                        if held and not live(held, now)
                                        and held.get("owner") != owner else None)}
            data[unit_id] = claim
            _write(path, data)
            return claim, ""
    except (TimeoutError, OSError) as exc:
        return None, "could not take the claim store lock: %s" % exc


def renew(path, unit_id, owner, ttl=DEFAULT_TTL_SECONDS, clock=None):
    """Push the lease out. A long unit must not lose its claim mid-run."""
    now = _now(clock)
    try:
        with _Lock(path, clock=clock):
            data, problem = _read(path)
            if data is None:
                return None, problem
            held = data.get(unit_id)
            if not held:
                return None, "unit %s holds no claim to renew" % unit_id
            if held.get("owner") != owner:
                return None, ("unit %s is owned by %s, not %s"
                              % (unit_id, held.get("owner"), owner))
            held["expires_at"] = now + float(ttl)
            data[unit_id] = held
            _write(path, data)
            return held, ""
    except (TimeoutError, OSError) as exc:
        return None, "could not take the claim store lock: %s" % exc


def release(path, unit_id, owner, state="done", clock=None, evidence=None):
    """Close a claim with the state it ended in. Never deletes the record.

    The record is the only durable evidence that this unit was run, by whom, on
    which attempt. Deleting it on release would make a completed unit
    indistinguishable from one nobody ever took.

    `evidence`, row E1: what integrate.py actually observed for this unit (the
    check command, its captured exit code and output, and the canonical
    revision it ran against), threaded here so the claim's `state` string is
    never the only thing surviving to a delivery record. Omitted (None) when
    the caller has none to give, e.g. a unit that never reached integration;
    a caller must never invent one to fill this in."""
    try:
        with _Lock(path, clock=clock):
            data, problem = _read(path)
            if data is None:
                return None, problem
            held = data.get(unit_id)
            if not held:
                return None, "unit %s holds no claim to release" % unit_id
            if held.get("owner") != owner:
                return None, ("unit %s is owned by %s, so %s may not release it"
                              % (unit_id, held.get("owner"), owner))
            held["state"] = state
            held["released_at"] = _now(clock)
            held["expires_at"] = 0
            if evidence is not None:
                held["evidence"] = evidence
            data[unit_id] = held
            _write(path, data)
            return held, ""
    except (TimeoutError, OSError) as exc:
        return None, "could not take the claim store lock: %s" % exc


def reconcile(path, clock=None):
    """(findings, problem). What a restarting controller needs to know.

    This is the crash-recovery seam. It NEVER acts: it reports, because deciding
    that a dead session's unit may be retried is a judgement about whether that
    unit's side effects are safe to repeat, and this file cannot know that."""
    now = _now(clock)
    data, problem = _read(path)
    if data is None:
        return None, problem
    out = []
    for unit_id, claim in sorted(data.items()):
        state = claim.get("state")
        if state != "claimed":
            continue
        if live(claim, now):
            out.append({"unit_id": unit_id, "status": "in-flight",
                        "owner": claim.get("owner"),
                        "detail": "still leased for %.0fs"
                                  % (float(claim["expires_at"]) - now)})
        else:
            out.append({"unit_id": unit_id, "status": "abandoned",
                        "owner": claim.get("owner"),
                        "attempt": claim.get("attempt"),
                        "detail": ("the lease expired %.0fs ago while still in "
                                   "state claimed, so the owner did not finish "
                                   "and did not release. Whether this unit may "
                                   "be retried depends on whether its side "
                                   "effects are safe to repeat, which this "
                                   "cannot decide"
                                   % (now - float(claim["expires_at"])))})
    return out, ""
