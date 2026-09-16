#!/usr/bin/env python3
"""Regression tests for tools/bm_device_lease.py (EPIC M4.01).
Standard library only. Run: python3 tools/test_bm_device_lease.py

Every test runs against its own tempfile.TemporaryDirectory() database path,
never the real default_db_path(), and never touches this repo's own files."""
import datetime
import importlib.util
import os
import sqlite3
import sys
import tempfile
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))

# E100: one sandbox for every temp tree this process makes, removed at exit
# (same convention test_bm_store.py uses).
sys.path.append(os.path.join(HERE, "../../../scripts"))
try:
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % os.path.basename(__file__))

_spec = importlib.util.spec_from_file_location(
    "bm_device_lease", os.path.join(HERE, "bm_device_lease.py"))
dl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dl)
sys.modules["bm_device_lease"] = dl


class StoreTestCase(unittest.TestCase):
    """Every test gets its own tempdir and its own DeviceLeaseStore at a
    path inside it, closed in tearDown even on failure."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self._tmp.name, "leases.sqlite3")
        self.store = dl.DeviceLeaseStore(self.db_path)

    def tearDown(self):
        self.store.close()
        self._tmp.cleanup()


class ClaimTests(StoreTestCase):
    def test_claim_new_device_creates_row(self):
        r = self.store.claim("sim-1", "owner-a", "sess-1", 60)
        self.assertEqual(r["state"], "leased")
        self.assertFalse(r["recovered_from_stale"])
        row = self.store.get("sim-1")
        self.assertEqual(row["owner"], "owner-a")
        self.assertEqual(row["lease_uuid"], r["lease_uuid"])
        self.assertEqual(row["version"], 1)

    def test_second_claim_refused_while_active(self):
        self.store.claim("sim-1", "owner-a", "sess-1", 60)
        with self.assertRaises(dl.LeaseRefused) as ctx:
            self.store.claim("sim-1", "owner-b", "sess-2", 60)
        self.assertEqual(ctx.exception.reason, "already-leased")

    def test_claim_rejects_bad_device_id(self):
        for bad in ("", "   ", None, 5):
            with self.assertRaises(ValueError):
                self.store.claim(bad, "owner-a", "sess-1", 60)

    def test_claim_rejects_bad_ttl(self):
        for bad in (0, -1, 0.5, True, "60", None):
            with self.assertRaises(ValueError):
                self.store.claim("sim-1", "owner-a", "sess-1", bad)

    def test_claim_rejects_ttl_beyond_the_bound_with_a_typed_error(self):
        """Minor m1 (adversarial review): an absurd ttl_seconds used to
        leak a raw OverflowError out of datetime arithmetic in
        _add_seconds instead of this module's own ValueError."""
        with self.assertRaises(ValueError):
            self.store.claim("sim-1", "owner-a", "sess-1", 10**12)

    def test_claim_after_release_succeeds(self):
        r1 = self.store.claim("sim-1", "owner-a", "sess-1", 60)
        self.store.release("sim-1", r1["lease_uuid"])
        r2 = self.store.claim("sim-1", "owner-b", "sess-2", 60)
        self.assertEqual(r2["state"], "leased")
        self.assertNotEqual(r1["lease_uuid"], r2["lease_uuid"])


class TTLAndStaleRecoveryTests(StoreTestCase):
    def _force_expiry(self, device_id):
        """Back-date expires_at directly (bypassing the API on purpose,
        this is the one place a test manufactures the passage of time)."""
        past = dl._add_seconds(dl.now_iso(), -1)
        self.store.conn.execute(
            "UPDATE device_leases SET expires_at=? WHERE device_id=?",
            (past, device_id))

    def test_claim_quarantines_expired_lease_instead_of_reclaiming(self):
        """Adversarial-review scenario (M2), reproduced exactly: claim as
        owner A with a short TTL, let it expire with no release() (a
        crashed session never calls release()), then claim again as a new
        owner B. The device must come out 'dirty', not silently 'leased'
        for B: a TTL expiry with no release is not evidence the device is
        clean, so it must not be handed to the next claimant for free."""
        self.store.claim("sim-1", "owner-a", "sess-1", 1)
        self._force_expiry("sim-1")
        with self.assertRaises(dl.LeaseRefused) as ctx:
            self.store.claim("sim-1", "owner-b", "sess-2", 60)
        self.assertEqual(ctx.exception.reason, "device-dirty")
        row = self.store.get("sim-1")
        self.assertEqual(row["state"], "dirty")
        self.assertEqual(row["owner"], "")
        self.assertNotEqual(row["dirty_reason"], "")
        # Only after explicit remediation can B actually claim it.
        self.store.clear_dirty("sim-1")
        r2 = self.store.claim("sim-1", "owner-b", "sess-2", 60)
        self.assertEqual(r2["state"], "leased")
        row = self.store.get("sim-1")
        self.assertEqual(row["owner"], "owner-b")

    def test_list_stale_reports_expired_only(self):
        self.store.claim("sim-1", "owner-a", "sess-1", 1)
        self.store.claim("sim-2", "owner-b", "sess-2", 3600)
        self._force_expiry("sim-1")
        stale = self.store.list_stale()
        ids = sorted(r["device_id"] for r in stale)
        self.assertEqual(ids, ["sim-1"])

    def test_recover_stale_refuses_still_valid_lease(self):
        self.store.claim("sim-1", "owner-a", "sess-1", 3600)
        with self.assertRaises(dl.LeaseRefused) as ctx:
            self.store.recover_stale("sim-1")
        self.assertEqual(ctx.exception.reason, "not-stale")

    def test_recover_stale_quarantines_dirty_without_leasing(self):
        """M2 root-cause fix applies here too: recover_stale() reclaims an
        expired, never-released lease to 'dirty', not 'available', since an
        unexplained expiry carries no evidence the device is clean. It
        still never re-leases the device to anyone (that stays claim()'s
        job), it only changes what 'reclaimed' defaults to."""
        self.store.claim("sim-1", "owner-a", "sess-1", 1)
        self._force_expiry("sim-1")
        result = self.store.recover_stale("sim-1")
        self.assertEqual(result["state"], "dirty")
        self.assertNotEqual(result["dirty_reason"], "")
        row = self.store.get("sim-1")
        self.assertEqual(row["state"], "dirty")
        self.assertEqual(row["owner"], "")
        self.assertEqual(row["lease_uuid"], "")
        self.assertNotEqual(row["dirty_reason"], "")

    def test_recover_stale_refuses_unknown_device(self):
        with self.assertRaises(dl.LeaseRefused) as ctx:
            self.store.recover_stale("ghost")
        self.assertEqual(ctx.exception.reason, "not-found")

    def test_recover_stale_refuses_available_device(self):
        self.store.claim("sim-1", "owner-a", "sess-1", 1)
        self.store.release(
            "sim-1", self.store.get("sim-1")["lease_uuid"])
        with self.assertRaises(dl.LeaseRefused) as ctx:
            self.store.recover_stale("sim-1")
        self.assertEqual(ctx.exception.reason, "not-leased")


class ReleaseTests(StoreTestCase):
    def test_release_unknown_device_refused(self):
        with self.assertRaises(dl.LeaseRefused) as ctx:
            self.store.release("ghost", "whatever")
        self.assertEqual(ctx.exception.reason, "not-found")

    def test_release_not_leased_refused(self):
        self.store.claim("sim-1", "owner-a", "sess-1", 60)
        row = self.store.get("sim-1")
        self.store.release("sim-1", row["lease_uuid"])
        # Already released: a second release with the same (now stale)
        # lease_uuid must refuse 'not-leased', not silently no-op.
        with self.assertRaises(dl.LeaseRefused) as ctx:
            self.store.release("sim-1", row["lease_uuid"])
        self.assertEqual(ctx.exception.reason, "not-leased")

    def test_release_wrong_lease_uuid_refused(self):
        self.store.claim("sim-1", "owner-a", "sess-1", 60)
        with self.assertRaises(dl.LeaseRefused) as ctx:
            self.store.release("sim-1", "not-the-real-uuid")
        self.assertEqual(ctx.exception.reason, "not-lease-holder")

    def test_release_after_stale_recovery_by_someone_else_refused(self):
        """The exact scenario the lease_uuid check exists for: A holds a
        lease that expires, the device is quarantined dirty (M2 fix), B
        clears it and claims fresh, then A's own release (holding its now-
        stale lease_uuid) must not tear down B's fresh lease."""
        r1 = self.store.claim("sim-1", "owner-a", "sess-1", 1)
        past = dl._add_seconds(dl.now_iso(), -1)
        self.store.conn.execute(
            "UPDATE device_leases SET expires_at=? WHERE device_id=?",
            (past, "sim-1"))
        with self.assertRaises(dl.LeaseRefused) as ctx:
            self.store.claim("sim-1", "owner-b", "sess-2", 60)
        self.assertEqual(ctx.exception.reason, "device-dirty")
        self.store.clear_dirty("sim-1")
        r2 = self.store.claim("sim-1", "owner-b", "sess-2", 60)
        with self.assertRaises(dl.LeaseRefused) as ctx:
            self.store.release("sim-1", r1["lease_uuid"])
        self.assertEqual(ctx.exception.reason, "not-lease-holder")
        # B's lease is untouched.
        row = self.store.get("sim-1")
        self.assertEqual(row["lease_uuid"], r2["lease_uuid"])
        self.assertEqual(row["state"], "leased")


class DirtyQuarantineTests(StoreTestCase):
    def test_mark_dirty_on_unknown_device_upserts(self):
        r = self.store.mark_dirty("sim-1", "cleanup failed: xcrun timeout")
        self.assertEqual(r["state"], "dirty")
        row = self.store.get("sim-1")
        self.assertEqual(row["state"], "dirty")
        self.assertEqual(row["dirty_reason"], "cleanup failed: xcrun timeout")

    def test_dirty_device_cannot_be_claimed(self):
        self.store.mark_dirty("sim-1", "left in a bad simulator state")
        with self.assertRaises(dl.LeaseRefused) as ctx:
            self.store.claim("sim-1", "owner-a", "sess-1", 60)
        self.assertEqual(ctx.exception.reason, "device-dirty")

    def test_mark_dirty_overrides_active_lease(self):
        self.store.claim("sim-1", "owner-a", "sess-1", 3600)
        r = self.store.mark_dirty("sim-1", "crashed mid-test")
        self.assertEqual(r["state"], "dirty")
        row = self.store.get("sim-1")
        self.assertEqual(row["state"], "dirty")
        self.assertEqual(row["owner"], "")
        self.assertEqual(row["lease_uuid"], "")

    def test_clear_dirty_returns_device_to_available(self):
        self.store.mark_dirty("sim-1", "bad state")
        r = self.store.clear_dirty("sim-1")
        self.assertEqual(r["state"], "available")
        row = self.store.get("sim-1")
        self.assertEqual(row["state"], "available")
        self.assertEqual(row["dirty_reason"], "")
        # Now claimable again.
        claimed = self.store.claim("sim-1", "owner-c", "sess-3", 60)
        self.assertEqual(claimed["state"], "leased")

    def test_clear_dirty_refuses_when_not_dirty(self):
        self.store.claim("sim-1", "owner-a", "sess-1", 60)
        with self.assertRaises(dl.LeaseRefused) as ctx:
            self.store.clear_dirty("sim-1")
        self.assertEqual(ctx.exception.reason, "not-dirty")

    def test_clear_dirty_refuses_unknown_device(self):
        with self.assertRaises(dl.LeaseRefused) as ctx:
            self.store.clear_dirty("ghost")
        self.assertEqual(ctx.exception.reason, "not-found")

    def test_mark_dirty_rejects_empty_reason(self):
        for bad in ("", "   ", None, 5):
            with self.assertRaises(ValueError):
                self.store.mark_dirty("sim-1", bad)


class ReadTests(StoreTestCase):
    def test_get_missing_device_returns_none(self):
        self.assertIsNone(self.store.get("ghost"))

    def test_list_all_orders_by_device_id(self):
        self.store.claim("sim-2", "owner-a", "sess-1", 60)
        self.store.claim("sim-1", "owner-b", "sess-2", 60)
        ids = [r["device_id"] for r in self.store.list_all()]
        self.assertEqual(ids, ["sim-1", "sim-2"])

    def test_list_stale_rejects_malformed_now(self):
        """A caller-supplied `now` in any shape other than now_iso()'s own
        fixed-width format must be refused, not silently compared wrong
        (adversarial-review finding: a bare date or sub-second timestamp
        would break the lexical staleness comparison without ever
        raising)."""
        for bad in ("2026-09-15", "2026-09-15T19:00:00.123456Z",
                    "not a timestamp", 12345, None):
            if bad is None:
                continue  # None means "use now_iso()", not a caller value
            with self.assertRaises(ValueError):
                self.store.list_stale(bad)

    def test_list_stale_accepts_now_iso_shaped_value(self):
        self.store.claim("sim-1", "owner-a", "sess-1", 1)
        future = dl._add_seconds(dl.now_iso(), 3600)
        self.assertEqual(self.store.list_stale(future)[0]["device_id"], "sim-1")


class SchemaInvariantTests(StoreTestCase):
    def test_raw_sql_cannot_insert_leased_with_null_expiry(self):
        """M3: the database is a machine-wide sqlite file other processes
        can open directly, not only through this module's Python, so the
        invariant "state='leased' implies expires_at IS NOT NULL" has to be
        enforced by the schema itself, not only by claim()/release(). A raw
        connection to the same file, bypassing every method on
        DeviceLeaseStore, must still be refused by sqlite's own CHECK."""
        raw = sqlite3.connect(self.db_path, timeout=5.0, isolation_level=None)
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                raw.execute(
                    "INSERT INTO device_leases (device_id, state, "
                    "expires_at, version, created_at, updated_at) VALUES "
                    "('ghost-device', 'leased', NULL, 1, 'x', 'x')")
        finally:
            raw.close()

    def test_raw_sql_cannot_update_leased_row_to_null_expiry(self):
        """Same invariant, reached by UPDATE instead of INSERT: a row that
        was legitimately leased must not be mutated into the stranded
        shape (leased, no expiry) by a second writer either."""
        self.store.claim("sim-1", "owner-a", "sess-1", 60)
        raw = sqlite3.connect(self.db_path, timeout=5.0, isolation_level=None)
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                raw.execute(
                    "UPDATE device_leases SET expires_at=NULL "
                    "WHERE device_id='sim-1'")
        finally:
            raw.close()

    def test_raw_sql_permits_leased_with_expiry_and_available_without(self):
        """The CHECK must not be so broad it refuses the legitimate shapes:
        'leased' with a real expiry, and 'available' with none."""
        raw = sqlite3.connect(self.db_path, timeout=5.0, isolation_level=None)
        try:
            raw.execute(
                "INSERT INTO device_leases (device_id, state, expires_at, "
                "version, created_at, updated_at) VALUES ('ok-leased', "
                "'leased', '2099-01-01T00:00:00Z', 1, 'x', 'x')")
            raw.execute(
                "INSERT INTO device_leases (device_id, state, expires_at, "
                "version, created_at, updated_at) VALUES ('ok-available', "
                "'available', NULL, 1, 'x', 'x')")
        finally:
            raw.close()


class BusyMidTransactionTests(StoreTestCase):
    def test_busy_during_begin_immediate_raises_lease_refused(self):
        """A second connection holding the write lock past busy_timeout_ms
        must surface as LeaseRefused('db-busy') from inside a mutation, not
        a raw sqlite3.OperationalError a caller has to know sqlite3 to
        catch (adversarial-review finding: the original _transaction()
        only wrapped __init__'s own open, not every later BEGIN
        IMMEDIATE)."""
        # Opened and closed BEFORE the competing writer grabs the lock, so
        # its own __init__ (a read-only schema probe) never contends.
        fast_store = dl.DeviceLeaseStore(self.db_path, busy_timeout_ms=200)
        blocker = sqlite3.connect(self.db_path, timeout=5.0, isolation_level=None)
        blocker.execute("BEGIN IMMEDIATE")
        blocker.execute(
            "INSERT INTO device_leases (device_id, state, version, "
            "created_at, updated_at) VALUES ('holder', 'available', 1, "
            "'x', 'x')")
        try:
            with self.assertRaises(dl.LeaseRefused) as ctx:
                fast_store.claim("sim-1", "owner-a", "sess-1", 60)
            self.assertEqual(ctx.exception.reason, "db-busy")
        finally:
            blocker.execute("ROLLBACK")
            blocker.close()
            fast_store.close()


class UseAfterCloseTests(StoreTestCase):
    def test_op_after_close_raises_lease_error_not_attribute_error(self):
        """Adversarial-review finding: self.conn is None after close(), so
        every call used to fall straight into store.conn.execute and leak
        a raw AttributeError instead of a typed LeaseError."""
        self.store.claim("sim-1", "owner-a", "sess-1", 60)
        self.store.close()
        with self.assertRaises(dl.LeaseError):
            self.store.get("sim-1")
        with self.assertRaises(dl.LeaseError):
            self.store.claim("sim-2", "owner-b", "sess-2", 60)

    def test_op_after_auto_close_on_mid_operation_error_raises_lease_error(self):
        """Same finding, the other trigger named in the review: _exec's
        own store.close() on a non-busy mid-operation error must leave the
        NEXT call on the same object raising LeaseError too, not
        AttributeError."""
        self.store.conn.close()
        self.store.conn = None
        with self.assertRaises(dl.LeaseError):
            self.store.get("sim-1")


class ConcurrencyTests(StoreTestCase):
    def test_concurrent_claims_only_one_wins(self):
        """Two threads racing claim() on a brand-new device_id: sqlite's
        own BEGIN IMMEDIATE serializes them, so exactly one INSERT lands
        and the other observes the row and is refused 'already-leased'
        (never a duplicate PRIMARY KEY crash, never two winners)."""
        results = []
        errors = []
        barrier = threading.Barrier(2)

        def worker(owner):
            store = dl.DeviceLeaseStore(self.db_path)
            try:
                barrier.wait(timeout=5)
                try:
                    results.append(store.claim("sim-race", owner, "s", 60))
                except dl.LeaseRefused as e:
                    errors.append(e)
            finally:
                store.close()

        threads = [threading.Thread(target=worker, args=("owner-%d" % i,))
                   for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        self.assertEqual(len(results), 1)
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].reason, "already-leased")


class CorruptionTests(unittest.TestCase):
    def test_zero_byte_file_quarantined_on_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "leases.sqlite3")
            open(path, "wb").close()
            self.assertEqual(os.path.getsize(path), 0)
            with self.assertRaises(dl.LeaseStoreCorrupt) as ctx:
                dl.DeviceLeaseStore(path)
            self.assertTrue(os.path.isfile(ctx.exception.quarantine_path))
            self.assertFalse(os.path.exists(path))

    def test_not_a_database_file_quarantined_on_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "leases.sqlite3")
            with open(path, "wb") as fh:
                fh.write(b"not a sqlite database at all, just some bytes")
            with self.assertRaises(dl.LeaseStoreCorrupt) as ctx:
                dl.DeviceLeaseStore(path)
            self.assertTrue(os.path.isfile(ctx.exception.quarantine_path))
            # A fresh open at the same path now succeeds (the bad file was
            # moved aside, not merely reported).
            store = dl.DeviceLeaseStore(path)
            store.close()

    def test_corrupt_quarantine_moves_wal_and_shm_sidecars(self):
        """Adversarial-review finding: quarantining only the main file
        left a stale -wal/-shm behind, which sqlite could try to apply
        onto the NEXT fresh database created at the same path, silently
        reintroducing the failure the quarantine was meant to clear."""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "leases.sqlite3")
            with open(path, "wb") as fh:
                fh.write(b"not a sqlite database at all, just some bytes")
            with open(path + "-wal", "wb") as fh:
                fh.write(b"stale wal bytes")
            with open(path + "-shm", "wb") as fh:
                fh.write(b"stale shm bytes")
            with self.assertRaises(dl.LeaseStoreCorrupt) as ctx:
                dl.DeviceLeaseStore(path)
            self.assertFalse(os.path.exists(path))
            self.assertFalse(os.path.exists(path + "-wal"))
            self.assertFalse(os.path.exists(path + "-shm"))
            self.assertTrue(os.path.isfile(ctx.exception.quarantine_path))
            self.assertTrue(
                os.path.isfile(ctx.exception.quarantine_path + "-wal"))
            self.assertTrue(
                os.path.isfile(ctx.exception.quarantine_path + "-shm"))

    def test_corrupt_keeps_live_sidecars_when_the_main_move_fails(self):
        """Minor m2 (adversarial review): if os.replace on the main file
        fails, self.path is still the LIVE database and its -wal/-shm can
        hold committed-but-uncheckpointed rows. The old code deleted them
        unconditionally; they must only be removed once the main file
        actually moved aside.

        Zero-byte trigger, not the not-a-database trigger the other
        corruption tests use: a not-a-database file is caught while
        self.conn is still open, and closing that connection is sqlite3's
        OWN cleanup path, which removes stale -wal/-shm sidecars as a side
        effect independent of anything this module does (see _corrupt's
        own docstring). That makes the not-a-database trigger unable to
        isolate this module's OWN deletion loop, the thing minor m2 is
        actually about. The zero-byte trigger raises before self.conn is
        ever opened (see __init__), so the explicit loop guarded by `if
        moved:` is the only code that can touch these sidecars here."""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "leases.sqlite3")
            open(path, "wb").close()  # zero bytes: __init__'s pre-check trigger
            with open(path + "-wal", "wb") as fh:
                fh.write(b"live wal bytes still needed")
            with open(path + "-shm", "wb") as fh:
                fh.write(b"live shm bytes still needed")
            real_replace = dl.os.replace

            def failing_replace(src, dst):
                # DeviceLeaseStore.__init__ resolves self.path through
                # os.path.realpath, which is not byte-identical to `path`
                # on macOS (/var/folders is a symlink to
                # /private/var/folders), so this stub raises unconditionally
                # rather than trying to match paths: the only os.replace
                # call in play during this test is _corrupt()'s own.
                raise OSError("simulated: cannot move the main file aside")

            dl.os.replace = failing_replace
            try:
                with self.assertRaises(dl.LeaseStoreCorrupt) as ctx:
                    dl.DeviceLeaseStore(path)
            finally:
                dl.os.replace = real_replace
            self.assertIsNone(ctx.exception.quarantine_path)
            # The move failed, so the originals are still live and must not
            # have been deleted out from under whatever still needs them.
            self.assertTrue(os.path.isfile(path))
            self.assertTrue(os.path.isfile(path + "-wal"))
            self.assertTrue(os.path.isfile(path + "-shm"))


class DefaultPathTests(unittest.TestCase):
    def test_env_override_wins(self):
        old = os.environ.get(dl.DB_ENV_OVERRIDE)
        os.environ[dl.DB_ENV_OVERRIDE] = "/tmp/some/explicit/path.sqlite3"
        try:
            self.assertEqual(dl.default_db_path(), "/tmp/some/explicit/path.sqlite3")
        finally:
            if old is None:
                del os.environ[dl.DB_ENV_OVERRIDE]
            else:
                os.environ[dl.DB_ENV_OVERRIDE] = old

    def test_default_directory_is_never_dot_brothermode(self):
        """Load-bearing per the module docstring: bm_store.resolve_root()
        treats ANY ancestor directory literally named ".brothermode" as a
        project marker, even with no store.sqlite3 inside it. A device
        lease database at that name under $HOME would silently hijack
        every unrelated brother CLI invocation."""
        old = os.environ.pop(dl.DB_ENV_OVERRIDE, None)
        try:
            path = dl.default_db_path()
            self.assertNotIn(os.sep + ".brothermode" + os.sep, path)
            self.assertFalse(path.endswith(os.sep + ".brothermode"))
        finally:
            if old is not None:
                os.environ[dl.DB_ENV_OVERRIDE] = old


if __name__ == "__main__":
    unittest.main(verbosity=2)
