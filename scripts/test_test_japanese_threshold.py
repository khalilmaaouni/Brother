"""What test_japanese_threshold.py's own evaluate() must keep true.

Driven backwards, same discipline as test_readiness_gate.py: evaluate() is a
pure function (per_class, overall, floors, overall_threshold) -> (ok,
reasons), so this file doctors a floor and an overall threshold to PROVE a
FAIL is reachable, without needing a real BrotherModeUp checkout, a
subprocess, or any network to do it. Real parsing correctness (does the
regex actually read bm_vault_jbench.py's printed table) is exercised
separately in test_parse_output_reads_a_real_looking_table below, using a
literal string shaped exactly like that tool's own print statements.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_japanese_threshold as TJT  # noqa: E402


class ADoctoredFloorFlipsAPassingRunToFail(unittest.TestCase):
    """The backwards proof the task asked for: point the wrapper at a floor
    it cannot possibly meet and watch it report FAIL, not a fake PASS."""

    def _clean_per_class(self):
        # Every class comfortably clears TJT.CLASS_FLOORS as shipped.
        return {
            "lexical_only": (100, 108),
            "mixed": (60, 68),
            "kana_alias": (23, 25),
            "width_variant": (22, 24),
            "dictionary_dependent": (10, 10),
            "negative": (10, 10),
        }

    def test_the_real_floors_pass_a_clean_run(self):
        per_class = self._clean_per_class()
        overall = (sum(h for h, _t in per_class.values()),
                   sum(t for _h, t in per_class.values()))
        ok, reasons = TJT.evaluate(per_class, overall)
        self.assertTrue(ok, reasons)
        self.assertEqual(reasons, [])

    def test_a_doctored_impossible_floor_reports_fail(self):
        per_class = self._clean_per_class()
        overall = (sum(h for h, _t in per_class.values()),
                   sum(t for _h, t in per_class.values()))
        doctored_floors = dict(TJT.CLASS_FLOORS)
        doctored_floors["mixed"] = 1.01  # unmeetable: no run can score >100%
        ok, reasons = TJT.evaluate(per_class, overall, floors=doctored_floors)
        self.assertFalse(ok)
        self.assertTrue(any("mixed" in r for r in reasons), reasons)

    def test_a_doctored_overall_threshold_reports_fail_even_with_clean_classes(self):
        per_class = self._clean_per_class()
        overall = (sum(h for h, _t in per_class.values()),
                   sum(t for _h, t in per_class.values()))
        ok, reasons = TJT.evaluate(per_class, overall, overall_threshold=1.01)
        self.assertFalse(ok)
        self.assertTrue(any(r.startswith("overall:") for r in reasons), reasons)

    def test_a_no_data_class_in_jbench_output_is_reported_not_silently_passed(self):
        per_class = self._clean_per_class()
        per_class["negative"] = None  # jbench prints NO-DATA for a 0-case class
        overall = (90, 235)
        ok, reasons = TJT.evaluate(per_class, overall)
        self.assertFalse(ok)
        self.assertTrue(any("negative" in r and "NO-DATA" in r for r in reasons), reasons)


class TheOutputParserReadsBmVaultJbenchsRealShape(unittest.TestCase):
    """A literal string shaped like bm_vault_jbench.py's own cmd_run() print
    statements (see that file's per-class and overall format strings), so a
    change to this wrapper's regex is checked against the real shape rather
    than a shape convenient to the parser."""

    SAMPLE = (
        "per-class score table:\n"
        "  dictionary_dependent   9/10 (90%), floor 90%  OK\n"
        "  kana_alias             18/25 (72%), floor 70%  OK\n"
        "  lexical_only           102/108 (94%), floor 90%  OK\n"
        "  mixed                  50/68 (74%), floor 70%  OK\n"
        "  negative               NO-DATA (0 cases; floor 90%)\n"
        "  width_variant          18/24 (75%), floor 70%  OK\n"
        "overall: 197/235 (84%)\n"
    )

    def test_per_class_and_overall_parse_out_correctly(self):
        per_class, overall = TJT.parse_output(self.SAMPLE)
        self.assertEqual(per_class["dictionary_dependent"], (9, 10))
        self.assertEqual(per_class["kana_alias"], (18, 25))
        self.assertEqual(per_class["lexical_only"], (102, 108))
        self.assertEqual(per_class["mixed"], (50, 68))
        self.assertIsNone(per_class["negative"])
        self.assertEqual(per_class["width_variant"], (18, 24))
        self.assertEqual(overall, (197, 235))

    def test_a_no_data_class_still_fails_the_declared_floor(self):
        per_class, overall = TJT.parse_output(self.SAMPLE)
        ok, reasons = TJT.evaluate(per_class, overall)
        self.assertFalse(ok)
        self.assertTrue(any("negative" in r for r in reasons), reasons)


class FindBmuToolsNeverFabricatesAPath(unittest.TestCase):
    def test_an_empty_directory_is_honest_no_data(self):
        """V6: products/brothermode/tools resolves FIRST, unconditionally, so
        on a checkout where that in-tree directory is real (landed at M4),
        pointing BROTHERMODEUP_TOOLS at an empty override no longer reaches
        NO-DATA by itself, since the in-tree candidate is tried next and is
        still real. NO-DATA is honest only when the in-tree directory is ALSO
        genuinely absent, so this patches the module's in-tree base path (and
        the external-checkout base path, so the result does not depend on
        whatever else sits on this machine) to tmpdirs for the test, never by
        moving the real in-tree files."""
        import tempfile
        empty = tempfile.mkdtemp()
        absent_in_tree = tempfile.mkdtemp()
        absent_external = tempfile.mkdtemp()
        old_env = os.environ.pop("BROTHERMODEUP_TOOLS", None)
        old_in_tree = TJT.IN_TREE_TOOLS_DIR
        old_external = TJT.CONVENTIONAL_TOOLS_DIR
        TJT.IN_TREE_TOOLS_DIR = absent_in_tree
        TJT.CONVENTIONAL_TOOLS_DIR = absent_external
        try:
            os.environ["BROTHERMODEUP_TOOLS"] = empty
            tools_dir, jbench, err = TJT.find_bmu_tools()
        finally:
            TJT.IN_TREE_TOOLS_DIR = old_in_tree
            TJT.CONVENTIONAL_TOOLS_DIR = old_external
            if old_env is None:
                os.environ.pop("BROTHERMODEUP_TOOLS", None)
            else:
                os.environ["BROTHERMODEUP_TOOLS"] = old_env
        self.assertIsNone(tools_dir)
        self.assertIsNone(jbench)
        self.assertTrue(err.startswith("NO-DATA"), err)

    def test_the_in_tree_directory_resolves_first_unconditionally(self):
        """V6's new contract, pinned directly: with BROTHERMODEUP_TOOLS unset,
        the real in-tree products/brothermode/tools directory (landed at M4)
        is found without needing any other checkout present."""
        old_env = os.environ.pop("BROTHERMODEUP_TOOLS", None)
        try:
            tools_dir, jbench, fixture = TJT.find_bmu_tools()
        finally:
            if old_env is not None:
                os.environ["BROTHERMODEUP_TOOLS"] = old_env
        self.assertEqual(tools_dir, TJT.IN_TREE_TOOLS_DIR)
        self.assertTrue(os.path.isfile(jbench), jbench)
        self.assertTrue(os.path.isfile(fixture), fixture)


if __name__ == "__main__":
    unittest.main()
