"""HIDDEN: not named in TASK.md's prompt to the competitor. Exercises the
second, independent caller (report.py) on a SUCCESSFUL write. Catches a
superficial fix that stops evicting the snapshot early but never puts the
new value back afterward: it would pass the visible test (nothing runs on
a failed write) yet still fail here, since the cache would then never
learn about a real, successful change.
"""
import unittest

from report import update_and_reread
from store import FakeBackend, Store


class ReportRereadTest(unittest.TestCase):
    def test_successful_write_is_visible_on_reread(self):
        backend = FakeBackend()
        backend.set("price", 100)
        store = Store(backend=backend)
        store.load("price")

        new_value = update_and_reread(store, "price", 150)

        self.assertEqual(new_value, 150)
        # And a fresh read through the store's own get() must agree.
        self.assertEqual(store.get("price"), 150)


if __name__ == "__main__":
    unittest.main()
