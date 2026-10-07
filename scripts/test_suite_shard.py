"""test_suite_shard: ACC2.2's selector, driven through the real loader.

Every case builds a fake module with TestCase classes, adds a load_tests hook
that calls the selector, and loads the module with the real TestLoader, which
is how a real suite's opt-in hook is reached.
"""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_shard as S


_COUNTER = [0]


def _case_class(name, count):
    body = {}
    for index in range(count):
        def _run(self, _i=index):
            pass
        _run.__name__ = "test_%d" % index
        body[_run.__name__] = _run
    return type(name, (unittest.TestCase,), body)


def _fake_module(spec, names_counts):
    _COUNTER[0] = _COUNTER[0] + 1
    module = types.ModuleType("fake_shard_module_%d" % _COUNTER[0])
    for name, count in names_counts:
        module.__dict__[name] = _case_class(name, count)
    def load_tests(loader, tests, pattern):
        return S.select(tests, spec)
    module.__dict__["load_tests"] = load_tests
    return module


def _load(spec, names_counts):
    module = _fake_module(spec, names_counts)
    return unittest.TestLoader().loadTestsFromModule(module)


def _flatten(suite):
    out = []
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            out.extend(_flatten(item))
        else:
            out.append(item)
    return out


def _ids(suite):
    return [test.id() for test in _flatten(suite)]


def _classes(suite):
    return sorted(set(test_id.split(".")[-2] for test_id in _ids(suite)))


FIVE = [("AOne", 2), ("BTwo", 1), ("CThree", 1), ("DFour", 2), ("EFive", 1)]


class TheShardsPartitionTheSuite(unittest.TestCase):

    def test_unset_or_empty_spec_returns_the_suite_unchanged(self):
        whole = _load(None, FIVE)
        self.assertEqual(whole.countTestCases(), 7)
        self.assertIs(S.select(whole, None), whole)
        self.assertIs(S.select(whole, ""), whole)

    def test_two_shards_are_disjoint_and_complete(self):
        whole = set(_ids(_load(None, FIVE)))
        first = _ids(_load("1/2", FIVE))
        second = _ids(_load("2/2", FIVE))
        self.assertTrue(first)
        self.assertTrue(second)
        self.assertEqual(set(first) & set(second), set())
        self.assertEqual(set(first) | set(second), whole)

    def test_whole_classes_round_robin_in_loader_order(self):
        self.assertEqual(_classes(_load("1/2", FIVE)), ["AOne", "CThree", "EFive"])
        self.assertEqual(_classes(_load("2/2", FIVE)), ["BTwo", "DFour"])
        self.assertEqual(_classes(_load("1/3", FIVE)), ["AOne", "DFour"])
        self.assertEqual(_classes(_load("2/3", FIVE)), ["BTwo", "EFive"])
        self.assertEqual(_classes(_load("3/3", FIVE)), ["CThree"])

    def test_classes_without_tests_do_not_count(self):
        fixture = [("AEmpty", 0), ("BReal", 1), ("CReal", 1), ("DReal", 1)]
        self.assertEqual(_classes(_load("1/2", fixture)), ["BReal", "DReal"])

    def test_a_malformed_spec_is_refused(self):
        for bad in ("x", "0/2", "3/2", "1/0", "1/17", "1/2 ", " 1/2", "01/2",
                    "1/02", "1-2", "1/2/3"):
            with self.assertRaises(ValueError) as caught:
                S.parse_spec(bad)
            self.assertIsInstance(caught.exception, S.ShardError)
        self.assertEqual(S.parse_spec("16/16"), (16, 16))

    def test_a_non_string_spec_or_a_non_suite_is_refused(self):
        for bad in (0, True, b"1/2", [], ["1/2"], {}, object()):
            with self.assertRaises(ValueError) as caught:
                S.parse_spec(bad)
            self.assertIsInstance(caught.exception, S.ShardError)
        for bad in (None, [], "1/2", object()):
            with self.assertRaises(ValueError) as caught:
                S.select(bad, "1/2")
            self.assertIsInstance(caught.exception, S.ShardError)

    def test_an_empty_shard_fails_the_run_rather_than_passing(self):
        suite = _load("2/2", [("Only", 2)])
        result = unittest.TestResult()
        suite.run(result)
        self.assertFalse(result.wasSuccessful())
        self.assertEqual(result.testsRun, 1)
        self.assertEqual(len(result.errors), 1)
        self.assertIn("selects no test", result.errors[0][1])


if __name__ == "__main__":
    unittest.main(verbosity=2)
