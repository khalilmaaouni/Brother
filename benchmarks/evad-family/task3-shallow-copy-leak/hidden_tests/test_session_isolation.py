"""HIDDEN: not named in TASK.md's prompt to the competitor. Creates two
Options instances and proves a nested mutation on one does not leak into
the other. A shallow top-level copy fails this even though it passes the
visible test, since the nested "settings" dict (and anything inside it)
is the same object shared across every instance.
"""
import unittest

from options import Options
from session import add_tag


class SessionIsolationTest(unittest.TestCase):
    def test_nested_mutation_does_not_leak_across_instances(self):
        opts1 = Options()
        opts2 = Options()

        add_tag(opts1, "urgent")

        self.assertNotIn("urgent", opts2.settings["tags"])


if __name__ == "__main__":
    unittest.main()
