#!/usr/bin/env python3
"""EPIC M4.02: simulator/emulator pool, layered over M4.01's real device
lease store (products/brothermode/tools/bm_device_lease.py).

WHY A THIN LAYER, NOT A SECOND LOCK. This module owns zero claim state of
its own. Every claim/release/quarantine decision goes through
bm_device_lease.DeviceLeaseStore, the same machine-wide sqlite lease store
M4.01 built (atomic BEGIN IMMEDIATE claim, TTL, stale recovery, dirty
quarantine). A second lock here would defeat the whole point of that store:
two pools on the same machine could then each think they own a device the
other one already granted. This module's only job is POOL SEMANTICS on top
of that lease: which simulator to pick, how to get it into a ready state,
and whether a warm (already booted) device may be reused as-is or must be
reset first.

WHY simctl CALLS REUSE scripts/mobile_workflow.py's EXACT VOCABULARY.
mobile_workflow.py's run_workflow() already shells out to `xcrun simctl
list/boot/bootstatus` for its own simulator test flow (verified end to end
by the M0.04 canary, PR #703). This module calls the same commands with the
same flags (list devices available -j, boot <udid>, bootstatus <udid> -b)
rather than inventing a second way to talk to simctl; it adds only what
mobile_workflow.py does not need for a one-shot test run: shutdown/erase
(reset) and a reusable pool API (mobile_workflow.py assumes an already
prepared simulator_id in its profile, it does not choose or prepare one).

"WAIT UNTIL GENUINELY READY" IS `simctl bootstatus -b`, NOT THE STATE FIELD.
`simctl list devices` can report a device as "Booted" the instant `simctl
boot` returns, well before the simulator's own boot sequence has actually
finished (SpringBoard, in Apple's own words, is not up yet at that point).
`simctl bootstatus -b` is Apple's own blocking wait for the real thing, and
it is what mobile_workflow.py already relies on (see its own boot-ready
stage). This module never treats state=="Booted" from `list` as proof of
readiness by itself; it either just ran bootstatus -b itself, or (when
warm-reusing a device this pool did not just boot) that is a caller-accepted
tradeoff of allow_warm_reuse, named in IsolationPolicy's own docstring.

Python 3.9, standard library only (subprocess is required here, unlike
bm_device_lease.py, to actually drive simctl). No em or en dashes anywhere
in this file, its comments, or its output.
"""
import dataclasses
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str((Path(__file__).resolve().parent.parent
                         / "products" / "brothermode" / "tools")))
import bm_device_lease as L  # noqa: E402  (path must be set up first)


DEFAULT_BOOT_TIMEOUT_SECONDS = 120
DEFAULT_READY_TIMEOUT_SECONDS = 240
DEFAULT_SHORT_TIMEOUT_SECONDS = 30

#: The one dirty_reason this pool wrote itself and knows how to undo: a
#: lease ended without an erase, so the device must be assumed used. It is
#: a PREFIX, matched at the start of the reason, because a reason string
#: can be truncated downstream and a marker at the end would be sliced off,
#: silently turning a recoverable quarantine into a permanent one.
#: Every other dirty reason means something nobody here diagnosed, and is
#: left to a human through the lease store's clear-dirty command.
DIRTY_USED_PREFIX = "pool-used:"


class SimulatorPoolError(Exception):
    """Base for everything this module raises on purpose (never a bare
    subprocess or JSON exception leaks past this module's own boundary)."""


class ToolMissing(SimulatorPoolError):
    """xcrun/simctl is not on PATH, or the call itself raised OSError."""


class NoSimulatorAvailable(SimulatorPoolError):
    """Raised by acquire(). `tried` is 0 when discover() itself returned no
    candidates at all (distinct from every discovered candidate having been
    refused by the lease store, `tried` > 0 for that case), matching the
    spec's request to distinguish the two."""

    def __init__(self, message, tried=0):
        super().__init__(message)
        self.tried = tried


class BootFailed(SimulatorPoolError):
    """simctl boot, or the bootstatus -b wait that follows it, failed or
    timed out. `udid` names the simulator; a caller of acquire() never sees
    this directly for a device the lease store already refused before boot
    was attempted, only for one this pool actually tried to prepare."""

    def __init__(self, message, udid):
        super().__init__(message)
        self.udid = udid


class ResetFailed(SimulatorPoolError):
    """shutdown, erase, or the reboot that follows either of them, failed.
    reset() always mark_dirty()s the device before raising this, so a
    caller never has to remember to quarantine it themselves."""

    def __init__(self, message, udid):
        super().__init__(message)
        self.udid = udid


@dataclasses.dataclass
class IsolationPolicy:
    """The one caller-facing knob this pool exposes for warm reuse.

    The input to this decision is the device's recorded PROVENANCE (did a
    previous lease use it), never its simctl power state. A device can be
    Shutdown and filthy, or Booted and freshly erased; power state answers
    neither question.

    allow_warm_reuse=False (the default): a simulator a previous lease used
    is reset (shutdown, erase, reboot, wait-ready) before being handed to
    the caller, so every acquire() returns a device that was actually
    erased, not one assumed clean. This is slower (a reset costs real wall
    time) but never leaks state between callers.

    allow_warm_reuse=True: such a device is handed back AS-IS, no erase.
    Faster, but whatever the previous holder installed, launched, or left
    in NSUserDefaults/the filesystem is still there, and the handle says so
    with warm_reused=True. This is a deliberate, explicit, per-call opt-in;
    it is never the default precisely because a caller who does not think
    about isolation should get isolation."""

    allow_warm_reuse: bool = False


@dataclasses.dataclass
class DeviceHandle:
    """What acquire() hands back: enough to use the device and to release
    it again through the same lease API (lease_uuid is required by
    release(), matching bm_device_lease.DeviceLeaseStore.release())."""

    device_id: str
    lease_uuid: str
    name: str
    runtime: str
    warm_reused: bool


def _run(argv, timeout, allowed_codes=(0,)):
    """Every simctl call in this module goes through here: one place that
    enforces an explicit timeout (never an open-ended subprocess call that
    can hang the whole pool on a wedged simulator) and turns a missing
    tool, a timeout, or an unexpected exit code into one of this module's
    own typed exceptions rather than a raw subprocess/OSError leaking out.
    Mirrors scripts/mobile_workflow.py's own invoke(): capture_output=True,
    an explicit timeout, stderr folded into the raised message."""
    try:
        proc = subprocess.run(argv, capture_output=True, timeout=timeout)
    except OSError as exc:
        # Covers a missing xcrun (FileNotFoundError) as well as any other
        # reason the OS refused to even start the process (permission
        # denied, etc), matching mobile_workflow.py's own invoke(), which
        # folds OSError and TimeoutExpired into one typed refusal rather
        # than letting a raw OS-level exception escape this module.
        raise ToolMissing("%s is not available: %s" % (argv[0], exc)) from exc
    except subprocess.TimeoutExpired as exc:
        raise SimulatorPoolError(
            "%s timed out after %ss" % (" ".join(argv), timeout)) from exc
    if proc.returncode not in allowed_codes:
        raise SimulatorPoolError(
            "%s exited %s: %s" % (" ".join(argv), proc.returncode,
                                   proc.stderr.decode("utf-8", "replace").strip()))
    return proc.stdout.decode("utf-8", "replace")


class SimulatorPool(object):
    """One pool over the machine-wide simulator fleet, backed by exactly
    one DeviceLeaseStore (M4.01's real lease API, never a second lock).

    NOT THREAD-SAFE, BY INHERITANCE FROM bm_device_lease.py. A
    DeviceLeaseStore opens one sqlite3 connection with the stdlib default
    check_same_thread=True: that connection, and therefore this pool, may
    only be used from the thread that created it. Two callers racing is
    the supported, tested case (two separate SimulatorPool/DeviceLeaseStore
    instances, i.e. two processes or two threads each with their own
    instance); sharing one SimulatorPool instance across threads is not."""

    def __init__(self, lease_store=None, boot_timeout_seconds=DEFAULT_BOOT_TIMEOUT_SECONDS,
                 ready_timeout_seconds=DEFAULT_READY_TIMEOUT_SECONDS):
        self._owns_store = lease_store is None
        self.lease_store = lease_store if lease_store is not None else L.DeviceLeaseStore()
        self.boot_timeout_seconds = boot_timeout_seconds
        self.ready_timeout_seconds = ready_timeout_seconds

    def close(self):
        # Never close a store the caller handed us; they own its lifecycle
        # (mirrors the spec's own instruction: "do not close a lease_store
        # the caller passed in").
        if self._owns_store:
            self.lease_store.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False

    # -- discovery --------------------------------------------------------

    def discover(self, runtime_filter=None, name_filter=None):
        """Every available simulator, as
        [{"device_id", "name", "runtime", "state"}, ...], sorted by
        device_id for a deterministic order: two callers racing over the
        same real fleet snapshot then try candidates in the same sequence,
        which is what makes the race behavior in acquire() testable and
        predictable rather than order-dependent on dict/set iteration."""
        raw = _run(["xcrun", "simctl", "list", "devices", "available", "-j"],
                   timeout=DEFAULT_SHORT_TIMEOUT_SECONDS)
        try:
            parsed = json.loads(raw)
        except ValueError as exc:
            raise SimulatorPoolError("simctl list returned malformed JSON: %s" % exc) from exc
        devices = parsed.get("devices") if isinstance(parsed, dict) else None
        if not isinstance(devices, dict):
            raise SimulatorPoolError("simctl list returned an unexpected shape")
        out = []
        for runtime, group in devices.items():
            if not isinstance(group, list):
                continue
            if runtime_filter and runtime_filter not in runtime:
                continue
            for d in group:
                if not isinstance(d, dict) or not d.get("udid"):
                    continue
                name = d.get("name", "")
                if name_filter and name_filter not in name:
                    continue
                out.append({"device_id": d["udid"], "name": name,
                            "runtime": runtime, "state": d.get("state", "")})
        out.sort(key=lambda d: d["device_id"])
        return out

    def _state(self, udid):
        for d in self.discover():
            if d["device_id"] == udid:
                return d["state"]
        return None

    # -- boot / ready / reset ---------------------------------------------

    def wait_ready(self, udid):
        """Block until `udid` has genuinely finished booting (simctl
        bootstatus -b), not merely accepted a boot command. Exposed on its
        own, in addition to being called by boot(), because the spec calls
        for a distinct wait-ready step (e.g. a caller booting a device by
        some other path still wants this pool's readiness wait)."""
        try:
            _run(["xcrun", "simctl", "bootstatus", udid, "-b"],
                 timeout=self.ready_timeout_seconds)
        except SimulatorPoolError as exc:
            raise BootFailed(
                "%s never became ready: %s" % (udid, exc), udid) from exc

    def _issue_boot(self, udid):
        """Send the boot command itself, tolerating a device that is
        already booted (simctl's own "Unable to boot device in current
        state: Booted" is not a real failure here, it is exactly the state
        we wanted). Split out from boot() so reset() can call this
        UNCONDITIONALLY right after erase, rather than trusting a fresh
        `simctl list` state read: a `list` snapshot taken immediately after
        shutdown/erase is not guaranteed to have caught up yet, and this
        module already knows for certain the device is not booted at that
        point, it just erased it itself.

        ponytail: only the "Booted" wording is tolerated; a device caught
        mid-transition (state "Booting"/"ShuttingDown" in a `list`
        snapshot, a narrow timing window) can still surface as a genuine
        BootFailed here even though simply waiting would have succeeded.
        Upgrade path if that is ever observed in practice: retry _run once
        after a short sleep instead of failing on the first non-"Booted"
        error text."""
        try:
            _run(["xcrun", "simctl", "boot", udid], timeout=self.boot_timeout_seconds)
        except SimulatorPoolError as exc:
            if "current state: Booted" not in str(exc):
                raise BootFailed("%s failed to boot: %s" % (udid, exc), udid) from exc

    def boot(self, udid):
        """Boot `udid` (if a fresh `simctl list` read shows it is not
        already Booted) and wait for it to be genuinely ready."""
        if self._state(udid) != "Booted":
            self._issue_boot(udid)
        self.wait_ready(udid)

    def reset(self, udid, lease_uuid=None):
        """Shutdown (tolerating already-shutdown), erase back to factory
        state, then unconditionally reboot and wait_ready, so a caller
        always gets the device back Booted and ready. Any step failing
        quarantines the device via lease_store.mark_dirty() BEFORE raising
        ResetFailed, so a device that failed reset is never silently
        handed to the next claimer in a broken state.

        lease_uuid, when the caller holds a lease on this device, is passed
        to mark_dirty as a fencing token: an erase that fails after this
        run's TTL already expired then refuses rather than revoking the
        live lease of whoever legitimately claimed the device since.

        Ownership is checked, read-only, BEFORE any destructive command
        runs: a stale or wrong lease_uuid must never erase a device somebody
        else currently holds, and a caller with NO token at all must never
        erase a device somebody else actively holds either. This is a
        pre-check, not a full fence: it narrows the window against a
        wrong/absent caller, but does not by itself make the read-check and
        the external erase atomic against a lease that expires and gets
        reclaimed DURING the destructive commands that follow. Closing that
        residual race needs a real coordination primitive (an atomic
        renew-or-refuse, or an explicit maintenance-hold state) in
        bm_device_lease.py itself, which does not exist yet; this precheck
        is deliberately not advertised as closing it. release_handle()'s
        own atomic compare-and-swap, later, still refuses a genuinely wrong
        final holder -- it just cannot undo an erase that already ran.
        Found by review: the previous order (reset, then release()) let
        release()'s real ownership check run only AFTER the erase had
        already happened; a later review reproduced that the precheck here
        narrows but does not eliminate the race, and that lease_uuid=None
        skipped this check even against a device someone else holds."""
        current = self.lease_store.get(udid)
        if lease_uuid is not None:
            if current is None:
                raise L.LeaseRefused(
                    "not-found", "no device %r in the lease store" % (udid,))
            if current["state"] != "leased":
                raise L.LeaseRefused(
                    "not-leased",
                    "device %r is %s, not leased" % (udid, current["state"]),
                    details={"device_id": udid, "state": current["state"]})
            if current["lease_uuid"] != lease_uuid:
                raise L.LeaseRefused(
                    "not-lease-holder",
                    "device %r's current lease is %r, not %r; this caller "
                    "does not hold the active lease (it may have expired "
                    "and been reclaimed already)"
                    % (udid, current["lease_uuid"], lease_uuid),
                    details={"device_id": udid,
                             "current_lease_uuid": current["lease_uuid"]})
        elif current is not None and current["state"] == "leased":
            raise L.LeaseRefused(
                "not-lease-holder",
                "device %r is actively leased by %r; a tokenless reset "
                "(maintenance path) must not erase a device someone else "
                "holds. Pass that lease's own lease_uuid, or reset a "
                "device that is available or dirty instead."
                % (udid, current["lease_uuid"]),
                details={"device_id": udid,
                         "current_lease_uuid": current["lease_uuid"]})
        try:
            try:
                _run(["xcrun", "simctl", "shutdown", udid], timeout=self.boot_timeout_seconds)
            except SimulatorPoolError as exc:
                if "current state: Shutdown" not in str(exc):
                    raise
            _run(["xcrun", "simctl", "erase", udid], timeout=self.boot_timeout_seconds)
            self._issue_boot(udid)
            self.wait_ready(udid)
        except SimulatorPoolError as exc:
            self.lease_store.mark_dirty(udid, "reset failed: %s" % exc,
                                        lease_uuid=lease_uuid)
            raise ResetFailed("%s failed to reset: %s" % (udid, exc), udid) from exc

    # -- provenance ---------------------------------------------------------

    def _pool_used_dirty(self, udid):
        """True when this device is quarantined for the one reason this
        pool itself knows how to undo: a previous lease used it and did not
        erase it. Matched on a prefix at the START of the reason, so a
        downstream slice of the message cannot turn a recoverable
        quarantine into an unrecognized one.

        Any OTHER dirty reason (a failed erase, a crash, a quarantine some
        other tool wrote) is deliberately NOT auto-recovered: erasing on
        top of an unknown fault would mask a genuinely broken simulator.
        Those are the operator's, through the lease store's clear-dirty
        command."""
        try:
            row = self.lease_store.get(udid)
        except L.LeaseError:
            return False
        if not row or row.get("state") != "dirty":
            return False
        return str(row.get("dirty_reason") or "").startswith(DIRTY_USED_PREFIX)

    def _rehabilitate(self, udid, owner, session_id, ttl_seconds):
        """Return a device quarantined as pool-used to the pool and claim
        it, or None if anything changed underneath (another session got
        there first). Without this the pool only ever shrinks: after
        M4.01's fixed store, a crashed holder's expired lease is
        quarantined rather than silently reclaimed, and a release with no
        reset is quarantined by design below, so dirty is the ORDINARY
        post-use state and nothing could return a device from it.

        The caller erases the device before handing it over, which is what
        actually makes the claim of cleanliness true; clearing the
        quarantine alone would only relabel it."""
        try:
            self.lease_store.clear_dirty(udid)
            return self.lease_store.claim(udid, owner, session_id, ttl_seconds)
        except L.LeaseRefused:
            return None

    # -- claim / release ----------------------------------------------------

    def acquire(self, owner, session_id, ttl_seconds, runtime_filter=None,
                name_filter=None, isolation_policy=None):
        """Claim one simulator through the real lease API and return it in
        a ready state. See the module docstring and IsolationPolicy for the
        warm-reuse tradeoff. Raises NoSimulatorAvailable if discover()
        found nothing at all (tried=0) or if every discovered candidate was
        refused by the lease store (tried=N)."""
        policy = isolation_policy if isolation_policy is not None else IsolationPolicy()
        candidates = self.discover(runtime_filter=runtime_filter, name_filter=name_filter)
        if not candidates:
            raise NoSimulatorAvailable(
                "no simulator matched runtime_filter=%r name_filter=%r"
                % (runtime_filter, name_filter), tried=0)
        tried = 0
        for candidate in candidates:
            udid = candidate["device_id"]
            tried += 1
            # used_before is this device's PROVENANCE, the only input to the
            # erase decision below. It used to be inferred from the simctl
            # power state in the discover() snapshot, which is not a record
            # of whether anybody used the device: a previous holder could
            # release a device it had installed an app on, leave it
            # Shutdown, and the next caller received it under a strict
            # no-warm-reuse policy with zero erase calls issued and
            # warm_reused=False, asserting clean. Power state also went
            # stale between the snapshot and the boot call, which read
            # fresh state, so the two could disagree.
            used_before = False
            try:
                claimed = self.lease_store.claim(udid, owner, session_id, ttl_seconds)
            except L.LeaseRefused as exc:
                if exc.reason == "device-dirty" and self._pool_used_dirty(udid):
                    claimed = self._rehabilitate(udid, owner, session_id, ttl_seconds)
                    if claimed is None:
                        continue
                    used_before = True
                elif exc.reason in ("already-leased", "device-dirty", "version-conflict"):
                    continue
                else:
                    raise
            # The lease is now ours. Every exception from here on MUST
            # release (or, via reset()'s own failure path, mark_dirty) this
            # lease before propagating: a caller that never got a usable
            # device must never be left holding a claim on the books.
            try:
                warm_reused = False
                if not used_before:
                    # Never used since its last erase: booting it is enough,
                    # and boot() tolerates a device already Booted.
                    self.boot(udid)
                elif policy.allow_warm_reuse:
                    # The explicit opt-in, and now it means what it says:
                    # a device a previous lease really did use, handed over
                    # without erasing.
                    warm_reused = True
                    self.boot(udid)
                else:
                    self.reset(udid, claimed["lease_uuid"])
                return DeviceHandle(
                    device_id=udid, lease_uuid=claimed["lease_uuid"],
                    name=candidate["name"], runtime=candidate["runtime"],
                    warm_reused=warm_reused)
            except Exception:
                if used_before:
                    # Found by review: this used to call
                    # self.lease_store.release() directly for EVERY failure,
                    # the raw primitive with no provenance awareness, instead
                    # of this pool's own release() (a few lines below, with
                    # the DIRTY_USED_PREFIX quarantine docstring). A failed
                    # warm-reuse boot on a device this pool already knew was
                    # used (used_before=True) then returned it 'available'
                    # with that provenance cleared, and the next STRICT
                    # acquire saw a clean-looking row, reported
                    # warm_reused=False, and issued no reset -- exactly the
                    # isolation gap used_before exists to prevent.
                    # reset_after=True here applies the same quarantine-or-
                    # reset logic a caller's own explicit release() would, so
                    # a device this pool once marked used never comes back
                    # silently unreset. A never-used device's own first-boot
                    # failure (used_before=False) is unchanged below: nothing
                    # about it says "dirty", only "retry", the same as
                    # before this fix.
                    try:
                        self.release(udid, claimed["lease_uuid"], reset_after=True)
                    except L.LeaseError:
                        # reset()'s own failure path already mark_dirty()d
                        # this device (which clears owner/session/lease_uuid
                        # and flips state to 'dirty', so a plain release()
                        # here correctly refuses 'not-leased'); either way
                        # the original preparation failure below is what
                        # matters, never mask it with this best-effort
                        # cleanup's own secondary error.
                        pass
                    raise
                try:
                    self.lease_store.release(udid, claimed["lease_uuid"])
                except L.LeaseError:
                    pass
                raise
        raise NoSimulatorAvailable(
            "every discovered simulator (%d) is leased or dirty" % tried, tried=tried)

    def release(self, device_id, lease_uuid, reset_after=False):
        """Hand `device_id` back, recording WHAT STATE IT IS IN rather than
        dropping the lease and leaving the next caller to guess.

        RESET THEN RELEASE, never release then reset. The device stays
        leased for the entire window it is being mutated. The old order
        dropped the lease first, so the erase ran against a device the
        store had already marked available: a second caller could acquire
        it mid-erase and receive it, flagged warm_reused=True. Reproduced
        directly.

        A failure in the release() call itself (wrong lease_uuid, already
        released, unknown device) is a real caller bug and is never
        swallowed.

        With reset_after=False the device is NOT returned as available. It
        was leased, so it must be assumed used, and that fact is recorded
        as a quarantine carrying DIRTY_USED_PREFIX. acquire() recognizes
        exactly that reason and rehabilitates the device by erasing it
        before the next caller sees it, so this costs the pool nothing
        while making the cleanliness claim true instead of assumed."""
        if reset_after:
            try:
                self.reset(device_id, lease_uuid)
            except ResetFailed:
                # reset() already quarantined the device, fenced on this
                # lease, so the lease is gone and the device is correctly
                # marked broken. Releasing on top of that would refuse
                # (not-leased) and would be wrong anyway: the device is not
                # available, it needs a human.
                return
            self.lease_store.release(device_id, lease_uuid)
            return
        self.lease_store.mark_dirty(
            device_id,
            DIRTY_USED_PREFIX + " released by %s with no reset" % lease_uuid,
            lease_uuid=lease_uuid)
