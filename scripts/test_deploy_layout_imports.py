#!/usr/bin/env python3
"""S5 harness: a deployed bin holds every module the grader imports by name from scripts/, and proves it imports.

grade_build.py imports prediction_ledger by name, and its own comment says the freeze ships prediction_ledger.py
beside it. It did not: deploy_stamped.stage_run copies scripts/loop into the bin through tool_stamp.copy_tools, and
prediction_ledger.py lives in scripts/, so it landed only under bin/candidate/scripts/ and every 1.1.7 grade carried
"prediction ledger could not be imported" (decision D10). The stage now places each such module beside the grader
and imports it from the staged folder alone before the canary; a module that does not import refuses the deploy.

The deploy cases run the real scripts/loop/deploy_stamped.sh on test_deploy_stamped's fixture (a fake source, target
and empty HOME; its own setUp and helpers, none of its tests).

Written first, red on the base by assertion (1.1.8 stability release, owner ruling C2, 2026-10-24; spec
docs/plan/specs/1.1.9/S5.md). Done check: python3 -B scripts/test_deploy_layout_imports.py DeployedImports
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "loop"))
import test_deploy_stamped as TDS  # noqa: E402
import deploy_stamped as DS  # noqa: E402

_F = TDS.DeployStamped


class DeployedImports(unittest.TestCase):
    setUp, git, deploy, success, unchanged = _F.setUp, _F.git, _F.deploy, _F.success, _F.unchanged

    def grader(self, ledger_text):
        # the quoted file name is how a real loop file names it (test_spec_score_no_claim.py), which puts it in
        # the parity check's referenced set: the post-swap parity then judges the placed copy, as in the real tree
        (self.loop / "grade_build.py").write_text('import json\nimport prediction_ledger\nLEDGER_FILE = "prediction_ledger.py"\n')
        (self.source / "scripts/prediction_ledger.py").write_text(ledger_text)
        self.git("add", ".")
        self.git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                 "-c", "core.hooksPath=/dev/null", "commit", "--quiet", "-m", "grader")

    def test_the_grader_imports_its_ledger(self):
        self.grader("LEDGER = 'fixture'\n")
        self.success(self.deploy())
        placed = self.target / "prediction_ledger.py"
        self.assertTrue(placed.is_file(), "the deployed bin has no prediction_ledger.py beside grade_build.py")
        self.assertEqual(placed.read_text(), "LEDGER = 'fixture'\n")

    def test_a_missing_import_refuses(self):
        # parses (kernel derivation reads it with ast) but raises when imported: only the staged import check sees it
        self.grader("raise RuntimeError('the fixture ledger cannot be imported')\n")
        result = self.deploy()
        self.assertNotEqual(result.returncode, 0, "a ledger that cannot be imported was deployed")
        self.assertIn("prediction_ledger", result.stdout + result.stderr)
        self.unchanged()

    def test_the_real_grader_names_its_ledger(self):
        self.assertTrue(hasattr(DS, "grader_by_name_imports"), "deploy_stamped has no grader_by_name_imports")
        names = DS.grader_by_name_imports(HERE / "loop", HERE)
        self.assertIn("prediction_ledger", names)
        self.assertFalse([n for n in names if (HERE / "loop" / (n + ".py")).exists()], names)

    def test_a_tree_without_a_grader_places_nothing(self):
        self.assertTrue(hasattr(DS, "grader_by_name_imports"), "deploy_stamped has no grader_by_name_imports")
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(DS.grader_by_name_imports(Path(d), Path(d)), [])

    def layout(self, d, grader, modules):
        loop = Path(d) / "loop"
        loop.mkdir()
        (loop / "grade_build.py").write_text(grader)
        for name, text in modules.items():
            (Path(d) / name).write_text(text)
        return loop, Path(d)

    def test_a_placed_module_brings_what_it_imports(self):
        # the ledger importing a second scripts/ module needs that one beside the grader too, or its import fails there
        with tempfile.TemporaryDirectory() as d:
            loop, scripts = self.layout(d, "import ledger_a\n", {"ledger_a.py": "import ledger_b\n", "ledger_b.py": "X = 1\n"})
            self.assertEqual(DS.grader_by_name_imports(loop, scripts), ["ledger_a", "ledger_b"])

    def test_a_function_scope_import_keeps_its_own_route(self):
        # grade_build._terms_scan imports private_terms_scan inside a function, from a folder it puts on sys.path itself
        with tempfile.TemporaryDirectory() as d:
            loop, scripts = self.layout(d, "import ledger_a\ndef scan():\n    import own_route\n",
                                        {"ledger_a.py": "X = 1\n", "own_route.py": "Y = 2\n"})
            self.assertEqual(DS.grader_by_name_imports(loop, scripts), ["ledger_a"])
        self.assertNotIn("private_terms_scan", DS.grader_by_name_imports(HERE / "loop", HERE))


if __name__ == "__main__":
    unittest.main()
