"""The ONE test named in TASK.md's prompt. Make this pass."""
import unittest

from options import DEFAULTS, Options


class NestedOverrideTest(unittest.TestCase):
    def test_nested_override_does_not_change_defaults(self):
        opts = Options()
        opts["verbosity"] = 5
        self.assertEqual(DEFAULTS["verbosity"], 1)


if __name__ == "__main__":
    unittest.main()
