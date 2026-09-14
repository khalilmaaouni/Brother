"""The ONE test named in TASK.md's prompt. Make this pass."""
import unittest

from store import FakeBackend, Store, make_failing_writer


class FailedUpdatePreservesCachedSnapshotTest(unittest.TestCase):
    def test_failed_update_preserves_cached_snapshot(self):
        backend = FakeBackend()
        backend.set("price", 100)
        store = Store(
            backend=backend,
            writer=make_failing_writer(backend, fail_keys={"price"}),
        )

        # Warm the cache with the known-good value.
        store.load("price")
        self.assertEqual(store.get("price"), 100)

        # A write that fails must not destroy the cached snapshot.
        with self.assertRaises(RuntimeError):
            store.update("price", 999)

        self.assertEqual(store.get("price"), 100)


if __name__ == "__main__":
    unittest.main()
