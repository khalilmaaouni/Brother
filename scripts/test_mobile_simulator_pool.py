"""Discriminating simulator-pool guards with deterministic simctl fixtures.

Mirrors scripts/test_mobile_workflow.py's own pattern exactly: a fake xcrun
on PATH intercepts argv and returns canned, environment-controlled output,
so the whole suite runs in well under a second with zero real simulators
involved. Every test uses a real DeviceLeaseStore (bm_device_lease.py)
pointed at a fresh temp sqlite file, not a mock of it, so the lease
behavior under test is the real thing.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mobile_simulator_pool as P

sys.path.insert(0, str((Path(__file__).resolve().parent.parent
                         / "products" / "brothermode" / "tools")))
import bm_device_lease as L

UDID_A = "11111111-2222-3333-4444-555555555555"
UDID_B = "66666666-7777-8888-9999-000000000000"

# args = sys.argv[1:] since this script itself IS "xcrun" on PATH; every
# simctl subcommand is therefore args[0]=='simctl', args[1]==<verb>, ...
TOOL = r'''#!/usr/bin/env python3
import json,os,pathlib,sys
args=sys.argv[1:]
root=pathlib.Path(os.environ['FIXTURE_ROOT'])
log=root/'calls.log'
def record(line):
    with log.open('a') as fh: fh.write(line+'\n')
if args[:4]==['simctl','list','devices','available']:
    devices_path=root/'devices.json'
    if devices_path.exists():
        print(devices_path.read_text())
    else:
        print(json.dumps({'devices':{'iOS-fixture':[]}}))
    sys.exit(0)
if args[:2]==['simctl','boot']:
    udid=args[2]
    record('boot '+udid)
    if os.environ.get('FIXTURE_BOOT_ALREADY_BOOTED'):
        sys.stderr.write('Unable to boot device in current state: Booted\n'); sys.exit(149)
    if os.environ.get('FIXTURE_BOOT_FAIL'):
        sys.stderr.write('fixture boot failure\n'); sys.exit(1)
    sys.exit(0)
if args[:2]==['simctl','bootstatus']:
    udid=args[2]
    record('bootstatus '+udid)
    if os.environ.get('FIXTURE_BOOTSTATUS_FAIL'):
        sys.stderr.write('fixture bootstatus failure\n'); sys.exit(1)
    sys.exit(0)
if args[:2]==['simctl','shutdown']:
    udid=args[2]
    record('shutdown '+udid)
    if os.environ.get('FIXTURE_SHUTDOWN_ALREADY'):
        sys.stderr.write('Unable to shutdown device in current state: Shutdown\n'); sys.exit(149)
    if os.environ.get('FIXTURE_SHUTDOWN_FAIL'):
        sys.stderr.write('fixture shutdown failure\n'); sys.exit(1)
    sys.exit(0)
if args[:2]==['simctl','erase']:
    udid=args[2]
    record('erase '+udid)
    if os.environ.get('FIXTURE_ERASE_FAIL'):
        sys.stderr.write('fixture erase failure\n'); sys.exit(1)
    sys.exit(0)
sys.stderr.write('unexpected fixture command %r\n' % (args,)); sys.exit(99)
'''


def devices_json(entries):
    """entries: list of (udid, name, state). One fixture runtime bucket is
    enough; discover() only cares that it is a list under "devices"."""
    return json.dumps({"devices": {"iOS-fixture": [
        {"udid": u, "name": n, "state": s} for (u, n, s) in entries]}})


class SimulatorPoolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="brother-simpool-tests-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        tool_path = self.bin / "xcrun"
        tool_path.write_text(TOOL)
        tool_path.chmod(0o755)
        self.db_path = str(self.root / "leases.sqlite3")
        self.env = patch.dict(
            os.environ,
            {"PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
             "FIXTURE_ROOT": str(self.root)},
            clear=False)
        self.env.start()
        self.addCleanup(self.env.stop)
        for key in ("FIXTURE_BOOT_FAIL", "FIXTURE_BOOT_ALREADY_BOOTED",
                    "FIXTURE_BOOTSTATUS_FAIL", "FIXTURE_SHUTDOWN_FAIL",
                    "FIXTURE_SHUTDOWN_ALREADY", "FIXTURE_ERASE_FAIL"):
            os.environ.pop(key, None)

    def set_devices(self, entries):
        (self.root / "devices.json").write_text(devices_json(entries))

    def calls(self):
        log = self.root / "calls.log"
        return log.read_text().splitlines() if log.exists() else []

    def new_store(self):
        return L.DeviceLeaseStore(db_path=self.db_path)

    def new_pool(self, **kwargs):
        return P.SimulatorPool(lease_store=self.new_store(), **kwargs)

    # -- discover -----------------------------------------------------

    def test_discover_parses_fixture_devices_sorted_by_id(self):
        self.set_devices([(UDID_B, "Phone B", "Shutdown"), (UDID_A, "Phone A", "Booted")])
        pool = self.new_pool()
        self.addCleanup(pool.close)
        found = pool.discover()
        self.assertEqual([d["device_id"] for d in found], [UDID_A, UDID_B])
        self.assertEqual(found[0]["state"], "Booted")
        self.assertEqual(found[1]["name"], "Phone B")

    def test_discover_filters_by_name(self):
        self.set_devices([(UDID_A, "Phone A", "Shutdown"), (UDID_B, "Tablet B", "Shutdown")])
        pool = self.new_pool()
        self.addCleanup(pool.close)
        found = pool.discover(name_filter="Tablet")
        self.assertEqual([d["device_id"] for d in found], [UDID_B])

    # -- acquire: boot / warm reuse / reset ----------------------------

    def test_acquire_on_shutdown_device_boots_it(self):
        self.set_devices([(UDID_A, "Phone A", "Shutdown")])
        pool = self.new_pool()
        self.addCleanup(pool.close)
        handle = pool.acquire("owner-1", "session-1", 60)
        self.assertEqual(handle.device_id, UDID_A)
        self.assertFalse(handle.warm_reused)
        self.assertIn("boot " + UDID_A, self.calls())
        self.assertIn("bootstatus " + UDID_A, self.calls())
        self.assertNotIn("shutdown " + UDID_A, self.calls())
        self.assertNotIn("erase " + UDID_A, self.calls())

    def test_power_state_alone_never_triggers_an_erase(self):
        """A device nobody has used is not made cleaner by erasing it, and
        "Booted" is not a record that anybody used it. The erase decision
        reads provenance now, so a Booted but never-used device is simply
        handed over, under the STRICT policy."""
        self.set_devices([(UDID_A, "Phone A", "Booted")])
        pool = self.new_pool()
        self.addCleanup(pool.close)
        handle = pool.acquire("owner-1", "session-1", 60,
                               isolation_policy=P.IsolationPolicy(allow_warm_reuse=False))
        self.assertFalse(handle.warm_reused)
        self.assertNotIn("erase " + UDID_A, self.calls())

    def test_a_used_device_is_erased_before_the_next_caller_sees_it(self):
        """The strict policy's real promise, and the order it happens in."""
        self.set_devices([(UDID_A, "Phone A", "Booted")])
        pool = self.new_pool()
        self.addCleanup(pool.close)
        first = pool.acquire("owner-1", "session-1", 60)
        pool.release(first.device_id, first.lease_uuid, reset_after=False)
        (self.root / "calls.log").unlink(missing_ok=True)

        handle = pool.acquire("owner-2", "session-2", 60,
                               isolation_policy=P.IsolationPolicy(allow_warm_reuse=False))
        self.assertFalse(handle.warm_reused)
        calls = self.calls()
        self.assertIn("shutdown " + UDID_A, calls)
        self.assertIn("erase " + UDID_A, calls)
        self.assertIn("boot " + UDID_A, calls)
        self.assertIn("bootstatus " + UDID_A, calls)
        # reset order: shutdown and erase must both precede the reboot.
        self.assertLess(calls.index("shutdown " + UDID_A), calls.index("erase " + UDID_A))
        self.assertLess(calls.index("erase " + UDID_A), calls.index("boot " + UDID_A))

    def test_warm_reuse_hands_over_a_used_device_and_says_so(self):
        self.set_devices([(UDID_A, "Phone A", "Booted")])
        pool = self.new_pool()
        self.addCleanup(pool.close)
        first = pool.acquire("owner-1", "session-1", 60)
        pool.release(first.device_id, first.lease_uuid, reset_after=False)
        (self.root / "calls.log").unlink(missing_ok=True)

        handle = pool.acquire("owner-2", "session-2", 60,
                               isolation_policy=P.IsolationPolicy(allow_warm_reuse=True))
        self.assertTrue(handle.warm_reused)
        self.assertNotIn("erase " + UDID_A, self.calls())

    def test_a_device_dirty_for_an_unknown_reason_is_not_auto_rehabilitated(self):
        """Erasing on top of a fault nobody diagnosed would mask a broken
        simulator, so only this pool's own used-marker is recoverable
        automatically. Everything else waits for the clear-dirty command."""
        self.set_devices([(UDID_A, "Phone A", "Shutdown")])
        pool = self.new_pool()
        self.addCleanup(pool.close)
        pool.lease_store.mark_dirty(UDID_A, "something nobody here diagnosed")
        with self.assertRaises(P.NoSimulatorAvailable):
            pool.acquire("owner-1", "session-1", 60)
        self.assertEqual(pool.lease_store.get(UDID_A)["state"], "dirty")
        # The operator's hand, from the lease store's own CLI, brings it back.
        self.assertEqual(L.main(["--db", self.db_path, "clear-dirty", "--device", UDID_A]), 0)
        self.assertEqual(pool.acquire("owner-1", "session-1", 60).device_id, UDID_A)

    def test_boot_tolerates_already_booted_race(self):
        # discover() saw Shutdown, but simctl boot itself now reports
        # already-booted (another process's boot() call landed first);
        # this must not be treated as a failure.
        self.set_devices([(UDID_A, "Phone A", "Shutdown")])
        os.environ["FIXTURE_BOOT_ALREADY_BOOTED"] = "1"
        pool = self.new_pool()
        self.addCleanup(pool.close)
        handle = pool.acquire("owner-1", "session-1", 60)
        self.assertEqual(handle.device_id, UDID_A)
        self.assertIn("bootstatus " + UDID_A, self.calls())

    # -- claim races ----------------------------------------------------

    def test_second_acquire_on_sole_candidate_is_refused_then_raises(self):
        self.set_devices([(UDID_A, "Phone A", "Shutdown")])
        first_pool = self.new_pool()
        self.addCleanup(first_pool.close)
        first = first_pool.acquire("owner-1", "session-1", 300)
        second_pool = self.new_pool()
        self.addCleanup(second_pool.close)
        with self.assertRaises(P.NoSimulatorAvailable) as raised:
            second_pool.acquire("owner-2", "session-2", 60)
        self.assertEqual(raised.exception.tried, 1)
        # the winner's lease is untouched by the loser's attempt.
        row = first_pool.lease_store.get(UDID_A)
        self.assertEqual(row["state"], "leased")
        self.assertEqual(row["lease_uuid"], first.lease_uuid)

    def test_second_acquire_falls_through_to_second_candidate(self):
        self.set_devices([(UDID_A, "Phone A", "Shutdown"), (UDID_B, "Phone B", "Shutdown")])
        first_pool = self.new_pool()
        self.addCleanup(first_pool.close)
        first = first_pool.acquire("owner-1", "session-1", 300)
        self.assertEqual(first.device_id, UDID_A)
        second_pool = self.new_pool()
        self.addCleanup(second_pool.close)
        second = second_pool.acquire("owner-2", "session-2", 60)
        self.assertEqual(second.device_id, UDID_B)

    def test_all_candidates_refused_raises_with_tried_count(self):
        self.set_devices([(UDID_A, "Phone A", "Shutdown"), (UDID_B, "Phone B", "Shutdown")])
        blocker = self.new_store()
        self.addCleanup(blocker.close)
        blocker.claim(UDID_A, "owner-x", "session-x", 300)
        blocker.claim(UDID_B, "owner-x", "session-x", 300)
        pool = self.new_pool()
        self.addCleanup(pool.close)
        with self.assertRaises(P.NoSimulatorAvailable) as raised:
            pool.acquire("owner-2", "session-2", 60)
        self.assertEqual(raised.exception.tried, 2)

    def test_zero_candidates_raises_immediately_with_tried_zero(self):
        self.set_devices([])
        pool = self.new_pool()
        self.addCleanup(pool.close)
        with self.assertRaises(P.NoSimulatorAvailable) as raised:
            pool.acquire("owner-1", "session-1", 60)
        self.assertEqual(raised.exception.tried, 0)

    # -- failure paths never leak or strand a lease ----------------------

    def test_boot_failure_releases_the_lease_rather_than_leaking_it(self):
        self.set_devices([(UDID_A, "Phone A", "Shutdown")])
        os.environ["FIXTURE_BOOT_FAIL"] = "1"
        pool = self.new_pool()
        self.addCleanup(pool.close)
        with self.assertRaises(P.BootFailed):
            pool.acquire("owner-1", "session-1", 60)
        row = pool.lease_store.get(UDID_A)
        self.assertEqual(row["state"], "available")

    def test_a_failed_warm_reuse_boot_keeps_the_device_quarantined_not_available(self):
        """Data-review finding, reproduced then closed: the failure-cleanup
        path used to call the raw lease_store.release() directly, which has
        no provenance awareness, instead of this pool's own release(). A
        used device (used_before=True, warm reuse) whose boot then failed
        came back 'available' with its used-device provenance erased, so
        the NEXT strict acquire saw a clean-looking row, reported
        warm_reused=False, and issued no reset -- an isolation gap, not
        just a leaked lease. This proves the device stays quarantined
        (never silently 'available') and that a strict reacquire after it
        genuinely resets rather than trusting the stale row."""
        self.set_devices([(UDID_A, "Phone A", "Booted")])
        pool = self.new_pool()
        self.addCleanup(pool.close)
        first = pool.acquire("owner-1", "session-1", 60)
        pool.release(first.device_id, first.lease_uuid, reset_after=False)
        row = pool.lease_store.get(UDID_A)
        self.assertEqual(row["state"], "dirty", "fixture setup: expected a "
                         "pool-used quarantine before the warm-reuse probe")

        # The device is already physically Booted from the setup acquire
        # above, so a warm-reuse boot() call skips simctl boot itself (only
        # issued when state != Booted) and goes straight to wait_ready(),
        # which always runs regardless of prior state -- FIXTURE_BOOTSTATUS_
        # FAIL is what actually reaches that path here, not FIXTURE_BOOT_FAIL.
        # A real subprocess-fixture failure (FIXTURE_BOOTSTATUS_FAIL) stays
        # active for every simctl call, including reset()'s own internal
        # recovery boot inside the fix's cleanup path -- that would make
        # the cleanup's OWN reset fail too, quarantining the device for a
        # second, unrelated "reset failed" reason this test does not exist
        # to prove. Mocking pool.boot itself isolates the failure to
        # exactly the one warm-reuse call, leaving reset()'s real
        # _issue_boot/wait_ready machinery (and the real fixture) alone.
        (self.root / "calls.log").unlink(missing_ok=True)
        with patch.object(pool, "boot", side_effect=P.BootFailed("injected", UDID_A)):
            with self.assertRaises(P.BootFailed):
                pool.acquire("owner-2", "session-2", 60,
                            isolation_policy=P.IsolationPolicy(allow_warm_reuse=True))
        # The fix's real behavior, and the thing the old bug actually
        # skipped: cleanup for a USED device does not just drop the lease,
        # it erases for real before the device can be called available
        # again. This is what makes "available" afterward a true claim
        # instead of the old bug's false one -- the erase actually ran, not
        # merely a state label saying it was safe to believe so.
        self.assertIn("erase " + UDID_A, self.calls(),
                      "a used device whose warm-reuse boot failed was "
                      "returned without ever being erased: the old bug's "
                      "provenance loss, just without the crash")
        row = pool.lease_store.get(UDID_A)
        self.assertEqual(row["state"], "available")

        # A strict reacquire now gets the device straight away: it is
        # genuinely clean (really erased above), not falsely clean.
        (self.root / "calls.log").unlink(missing_ok=True)
        handle = pool.acquire("owner-3", "session-3", 60,
                              isolation_policy=P.IsolationPolicy(allow_warm_reuse=False))
        self.assertFalse(handle.warm_reused)

    def test_bootstatus_failure_releases_the_lease_rather_than_leaking_it(self):
        self.set_devices([(UDID_A, "Phone A", "Shutdown")])
        os.environ["FIXTURE_BOOTSTATUS_FAIL"] = "1"
        pool = self.new_pool()
        self.addCleanup(pool.close)
        with self.assertRaises(P.BootFailed):
            pool.acquire("owner-1", "session-1", 60)
        row = pool.lease_store.get(UDID_A)
        self.assertEqual(row["state"], "available")

    def test_reset_failure_quarantines_the_device_instead_of_releasing_it(self):
        self.set_devices([(UDID_A, "Phone A", "Booted")])
        pool = self.new_pool()
        self.addCleanup(pool.close)
        # The device has to have been USED for the strict policy to erase
        # it at all now, so give it a real prior holder first.
        first = pool.acquire("owner-1", "session-1", 60)
        pool.release(first.device_id, first.lease_uuid, reset_after=False)

        os.environ["FIXTURE_ERASE_FAIL"] = "1"
        with self.assertRaises(P.ResetFailed):
            pool.acquire("owner-2", "session-2", 60,
                          isolation_policy=P.IsolationPolicy(allow_warm_reuse=False))
        row = pool.lease_store.get(UDID_A)
        self.assertEqual(row["state"], "dirty")
        self.assertIn("reset failed", row["dirty_reason"])
        # A failed erase is NOT this pool's own recoverable used-marker, so
        # the next acquire leaves it alone rather than erasing over a fault.
        self.assertFalse(row["dirty_reason"].startswith(P.DIRTY_USED_PREFIX))
        with self.assertRaises(P.NoSimulatorAvailable):
            pool.acquire("owner-3", "session-3", 60)
        with self.assertRaises(L.LeaseRefused) as raised:
            pool.lease_store.claim(UDID_A, "owner-3", "session-3", 60)
        self.assertEqual(raised.exception.reason, "device-dirty")

    # -- release ----------------------------------------------------------

    def test_release_then_reacquire_actually_erases_between_holders(self):
        """THE defect this suite used to pass straight over. The old
        version of this test asserted only that the next caller got the
        device back, and never inspected the call log for an erase, so it
        passed identically whether the device was cleaned or merely
        relabelled. Proven scenario: owner-1 releases with
        reset_after=False, owner-2 acquires under a STRICT no-warm-reuse
        policy and used to receive the same device with zero erase calls
        issued, flagged warm_reused=False, asserting clean."""
        self.set_devices([(UDID_A, "Phone A", "Shutdown")])
        pool = self.new_pool()
        self.addCleanup(pool.close)
        handle = pool.acquire("owner-1", "session-1", 60)
        pool.release(handle.device_id, handle.lease_uuid, reset_after=False)

        # A used device is not "available": it is recorded as used.
        row = pool.lease_store.get(UDID_A)
        self.assertEqual(row["state"], "dirty")
        self.assertTrue(row["dirty_reason"].startswith(P.DIRTY_USED_PREFIX))

        (self.root / "calls.log").unlink(missing_ok=True)
        second = pool.acquire("owner-2", "session-2", 60,
                               isolation_policy=P.IsolationPolicy(allow_warm_reuse=False))
        self.assertEqual(second.device_id, UDID_A)
        self.assertFalse(second.warm_reused)
        self.assertIn("erase " + UDID_A, self.calls())
        self.assertEqual(pool.lease_store.get(UDID_A)["state"], "leased")

    def test_a_second_caller_cannot_acquire_a_device_mid_erase(self):
        """Proven scenario: while A is mid-erase, B acquired the same
        device and received it, flagged warm_reused=True, because release()
        dropped the lease BEFORE erasing. Reset-then-release keeps the
        device leased for the whole window it is being mutated.

        This drives the real acquire() path from inside the real erase
        call, rather than asserting the ordering from the outside."""
        self.set_devices([(UDID_A, "Phone A", "Shutdown")])
        pool = self.new_pool()
        self.addCleanup(pool.close)
        handle = pool.acquire("owner-1", "session-1", 60)

        seen = {}
        real_run = P._run

        def run_and_probe(argv, *args, **kwargs):
            if "erase" in argv:
                other = self.new_pool()
                try:
                    try:
                        seen["outcome"] = other.acquire("owner-B", "session-B", 60)
                    except (P.NoSimulatorAvailable, L.LeaseRefused) as exc:
                        seen["outcome"] = exc
                finally:
                    other.close()
            return real_run(argv, *args, **kwargs)

        with patch.object(P, "_run", run_and_probe):
            pool.release(handle.device_id, handle.lease_uuid, reset_after=True)

        self.assertIn("outcome", seen, "the probe never ran, so this proves nothing")
        self.assertIsInstance(seen["outcome"], P.NoSimulatorAvailable)
        self.assertEqual(pool.lease_store.get(UDID_A)["state"], "available")

    def test_release_wrong_lease_uuid_raises_not_swallowed(self):
        self.set_devices([(UDID_A, "Phone A", "Shutdown")])
        pool = self.new_pool()
        self.addCleanup(pool.close)
        handle = pool.acquire("owner-1", "session-1", 60)
        with self.assertRaises(L.LeaseRefused) as raised:
            pool.release(handle.device_id, "not-the-real-lease-uuid")
        self.assertEqual(raised.exception.reason, "not-lease-holder")

    def test_reset_refuses_a_wrong_lease_uuid_before_erasing(self):
        """Security-review finding, reproduced then closed: reset() used to
        run xcrun simctl erase unconditionally, with the real ownership
        check happening only later in release()'s atomic compare-and-swap.
        A stale or wrong lease_uuid could therefore destroy the CURRENT
        holder's device before that later check ever ran. This proves the
        erase command itself never fires for a wrong holder, not only that
        the call eventually raises."""
        self.set_devices([(UDID_A, "Phone A", "Shutdown")])
        pool = self.new_pool()
        self.addCleanup(pool.close)
        handle = pool.acquire("owner-1", "session-1", 60)
        with self.assertRaises(L.LeaseRefused) as raised:
            pool.reset(handle.device_id, "not-the-real-lease-uuid")
        self.assertEqual(raised.exception.reason, "not-lease-holder")
        self.assertNotIn("erase " + UDID_A, self.calls(),
                         "reset() erased the device before its own "
                         "ownership check refused the wrong lease_uuid")
        # The real holder's lease and device state are untouched.
        row = pool.lease_store.get(UDID_A)
        self.assertEqual(row["state"], "leased")
        self.assertEqual(row["lease_uuid"], handle.lease_uuid)

    def test_reset_with_no_lease_uuid_skips_the_ownership_check(self):
        """A direct maintenance reset of a device nobody holds (no lease
        row at all) is unchanged: nothing to validate, so it proceeds
        exactly as before this fix."""
        self.set_devices([(UDID_A, "Phone A", "Shutdown")])
        pool = self.new_pool()
        self.addCleanup(pool.close)
        pool.reset(UDID_A)
        self.assertIn("erase " + UDID_A, self.calls())

    def test_tokenless_reset_refuses_a_device_someone_else_actively_holds(self):
        """Second review finding, reproduced then closed: reset(udid) with
        no lease_uuid (the maintenance path) validated nothing at all, so
        it happily erased a device another caller was actively leasing.
        A missing token must refuse exactly like a wrong one, not bypass
        the check entirely."""
        self.set_devices([(UDID_A, "Phone A", "Shutdown")])
        pool = self.new_pool()
        self.addCleanup(pool.close)
        pool.acquire("owner-1", "session-1", 60)
        with self.assertRaises(L.LeaseRefused) as raised:
            pool.reset(UDID_A)
        self.assertEqual(raised.exception.reason, "not-lease-holder")
        self.assertNotIn("erase " + UDID_A, self.calls())

    def test_release_reset_after_is_best_effort_and_never_raises(self):
        self.set_devices([(UDID_A, "Phone A", "Shutdown")])
        pool = self.new_pool()
        self.addCleanup(pool.close)
        handle = pool.acquire("owner-1", "session-1", 60)
        os.environ["FIXTURE_ERASE_FAIL"] = "1"
        pool.release(handle.device_id, handle.lease_uuid, reset_after=True)
        row = pool.lease_store.get(UDID_A)
        # The erase now runs BEFORE the release, so its failure quarantines
        # the device and the release never happens: the device ends up
        # dirty rather than available, which is the honest outcome. Either
        # way the lease is gone, and release() still does not raise.
        self.assertEqual(row["state"], "dirty")
        self.assertIn("reset failed", row["dirty_reason"])

    # -- pool lifecycle ---------------------------------------------------

    def test_pool_does_not_close_a_store_it_did_not_open(self):
        store = self.new_store()
        pool = P.SimulatorPool(lease_store=store)
        pool.close()
        # still usable: close() must not have touched a caller-owned store.
        store.get(UDID_A)
        store.close()

    def test_pool_closes_a_store_it_opened_itself(self):
        db_path = str(self.root / "owned.sqlite3")
        pool = P.SimulatorPool(lease_store=L.DeviceLeaseStore(db_path=db_path))
        # simulate "pool opened its own store" by constructing with no
        # lease_store and an env override, matching real usage.
        with patch.dict(os.environ, {"BM_DEVICE_LEASE_DB": db_path}):
            owned_pool = P.SimulatorPool()
            self.addCleanup(lambda: None)
            owned_pool.close()
            self.assertIsNone(owned_pool.lease_store.conn)
        pool.close()

    def test_context_manager_closes_owned_store(self):
        db_path = str(self.root / "ctx.sqlite3")
        with patch.dict(os.environ, {"BM_DEVICE_LEASE_DB": db_path}):
            with P.SimulatorPool() as pool:
                pass
            self.assertIsNone(pool.lease_store.conn)


if __name__ == "__main__":
    unittest.main()
