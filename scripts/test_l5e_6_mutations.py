"""L5e.6 mutation red runner tests.

Each named M-L5E mutation is applied inside a temporary copy of this
repository's scripts directory and its mapped sub unit check must exit
nonzero. An unknown mutation name, an already applied mutation, a missing
target, a concurrent mutation and every hostile argument block with
ValueError. A check module that cannot be imported, that raises while it is
imported, or that runs zero tests is red too, and a check module that
passes is green.

The registry and run_mutation live in the sibling module l5e_6_mutations.py,
so removing that module makes this suite fail rather than pass.

Done check: python3 -B scripts/test_l5e_6_mutations.py
"""

import os
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import l5e_6_mutations as M  # noqa: E402

REPO_ROOT = M.REPO_ROOT

EXPECTED_MUTATIONS = (
    "M-L5E1-ALWAYS-DIFF",
    "M-L5E2-EMPTY-CLAIMS",
    "M-L5E3-ALWAYS-OK",
    "M-L5E4-MISSING-SECTION",
    "M-L5E5-ALWAYS-TEN",
)


def _scripts_root(prefix):
    """A temporary repository root holding an empty scripts directory."""
    root = tempfile.mkdtemp(prefix=prefix)
    os.makedirs(os.path.join(root, "scripts"))
    return root


class MutationRedRunnerTests(unittest.TestCase):
    """RQ-9: every named mutation makes its mapped check go red."""

    def test_mutations_go_red(self):
        """Assert every M-L5E mutation makes its mapped check fail."""
        names = sorted(M.MUTATIONS)
        self.assertEqual(names, sorted(M.MUTATION_NAMES))
        self.assertEqual(names, sorted(EXPECTED_MUTATIONS))
        for name in names:
            code = M.run_mutation(name, REPO_ROOT)
            self.assertNotEqual(
                code, 0, "mutation %s did not turn its check red" % name)

    def test_unknown_mutation_name_blocks(self):
        with self.assertRaises(ValueError):
            M.run_mutation("M-L5E6-NOT-A-MUTATION", REPO_ROOT)

    def test_already_applied_mutation_blocks(self):
        root = _scripts_root("l5e6-already-")
        self.addCleanup(M._remove_tree, root)
        target = os.path.join(root, "scripts", "l5e_5_score_gate.py")
        with open(target, "w", encoding="utf-8") as handle:
            handle.write("# the find string is absent, so it is applied\n")
        with self.assertRaises(ValueError):
            M.run_mutation("M-L5E5-ALWAYS-TEN", root)

    def test_missing_target_blocks(self):
        root = tempfile.mkdtemp(prefix="l5e6-missing-")
        self.addCleanup(M._remove_tree, root)
        with self.assertRaises(ValueError):
            M.run_mutation("M-L5E5-ALWAYS-TEN", root)

    def test_concurrent_mutation_blocks(self):
        self.assertTrue(M._RUN_LOCK.acquire(False))
        try:
            with self.assertRaises(ValueError):
                M.run_mutation("M-L5E5-ALWAYS-TEN", REPO_ROOT)
        finally:
            M._RUN_LOCK.release()

    def test_check_that_cannot_be_imported_is_red(self):
        root = _scripts_root("l5e6-noimport-")
        self.addCleanup(M._remove_tree, root)
        self.assertNotEqual(M._run_check("zz_absent_check_module", root), 0)

    def test_check_that_raises_while_importing_is_red(self):
        root = _scripts_root("l5e6-broken-")
        self.addCleanup(M._remove_tree, root)
        target = os.path.join(root, "scripts", "zz_broken_check.py")
        with open(target, "w", encoding="utf-8") as handle:
            handle.write("def broken(:\n")
        self.assertNotEqual(M._run_check("zz_broken_check", root), 0)

    def test_check_that_runs_zero_tests_is_red(self):
        root = _scripts_root("l5e6-zero-")
        self.addCleanup(M._remove_tree, root)
        target = os.path.join(root, "scripts", "zz_empty_check.py")
        with open(target, "w", encoding="utf-8") as handle:
            handle.write('"""A module with no tests at all."""\n')
        self.assertNotEqual(M._run_check("zz_empty_check", root), 0)

    def test_a_passing_check_is_green(self):
        root = _scripts_root("l5e6-green-")
        self.addCleanup(M._remove_tree, root)
        target = os.path.join(root, "scripts", "zz_green_check.py")
        with open(target, "w", encoding="utf-8") as handle:
            handle.write(
                "import unittest\n"
                "\n"
                "\n"
                "class Green(unittest.TestCase):\n"
                "    def test_ok(self):\n"
                "        self.assertTrue(True)\n")
        self.assertEqual(M._run_check("zz_green_check", root), 0)

    def test_hostile_inputs_are_refused(self):
        for bad in (None, 123, 1.5, float("nan"), True, b"m", [], {}, ()):
            with self.assertRaises(ValueError):
                M.run_mutation(bad, REPO_ROOT)
        for bad in (None, 123, 1.5, True, b"/tmp", [], {}, ()):
            with self.assertRaises(ValueError):
                M.run_mutation("M-L5E5-ALWAYS-TEN", bad)
        with self.assertRaises(ValueError):
            M.run_mutation(
                "M-L5E5-ALWAYS-TEN",
                os.path.join(REPO_ROOT, "zz-absent-directory"))


if __name__ == "__main__":
    unittest.main()
