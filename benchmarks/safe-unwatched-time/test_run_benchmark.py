"""Self-check for benchmarks/safe-unwatched-time/run_benchmark.py.

Two things this asserts, and nothing more:

1. A real workload family, backed by a real run this machine actually
   produced, is refused (NO-DATA, no duration) because at least one of the
   four preservation checks has no instrument behind it today. This is the
   CRITICAL property: the harness must never fill that gap with a guess.
2. The synthetic fixture under fixtures/synthetic-complete-example/, which is
   NOT a real run and is labeled as such in its own preservation.json, drives
   the same code path to a real computed duration with all four checks
   measured and passing. This proves the computation logic itself, isolated
   from the question of what evidence exists on this machine today.

Run directly: python3 benchmarks/safe-unwatched-time/test_run_benchmark.py
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import run_benchmark as rb  # noqa: E402

FIXTURE_DIR = os.path.join(HERE, "fixtures", "synthetic-complete-example")


class RealFamiliesRefuseADuration(unittest.TestCase):
    def test_every_named_family_is_no_data_today(self):
        """None of the twelve families may report a duration today: every one
        this harness can reach is missing at least one of the four
        preservation checks (see run_benchmark.py's module docstring for
        why), and the rest have no documented real run directory at all."""
        for family_id, name, run_name in rb.FAMILIES:
            result = rb.evaluate_family(family_id, name, run_name)
            self.assertEqual(
                result["verdict"], "NO-DATA",
                "family %d (%s) reported %r instead of refusing"
                % (family_id, name, result["verdict"]))
            self.assertNotIn("min over", result["verdict"])

    def test_families_6_and_7_are_backed_by_a_real_run_this_machine_has(self):
        """The two families with a documented run directory really do exist
        on disk and really do get a raw instrument reading computed, even
        though the family verdict still refuses (this is what proves the
        refusal is about the preservation checks, not about missing files)."""
        for family_id in (6, 7):
            entry = next(f for f in rb.FAMILIES if f[0] == family_id)
            run_dir = os.path.join(rb.RUNS_ROOT, entry[2])
            self.assertTrue(
                os.path.isdir(run_dir), "%s does not exist" % run_dir)
            result = rb.evaluate_family(*entry)
            self.assertIn("raw_instrument", result)
            self.assertEqual(result["verdict"], "NO-DATA")

    def test_an_unmapped_real_run_with_a_genuine_scope_violation_still_refuses(self):
        """scope-auditing-adversity-2026-09-04 really did trip a scope
        violation (measured=True, ok=False for scope_drift) and the harness
        still refuses a duration, because recoverability and repeated-mistake
        counting remain unmeasured regardless."""
        run_dir = os.path.join(
            rb.RUNS_ROOT, "scope-auditing-adversity-2026-09-04")
        self.assertTrue(os.path.isdir(run_dir))
        result = rb.evaluate_run(run_dir)
        self.assertEqual(result["verdict"], "NO-DATA")
        self.assertTrue(result["checks"]["scope_drift"]["measured"])
        self.assertFalse(result["checks"]["scope_drift"]["ok"])
        self.assertFalse(result["checks"]["unrecoverable_state"]["measured"])
        self.assertFalse(result["checks"]["repeated_mistakes"]["measured"])

    def test_a_missing_run_directory_is_no_data_not_a_crash(self):
        result = rb.evaluate_run(os.path.join(rb.RUNS_ROOT, "does-not-exist"))
        self.assertEqual(result["verdict"], "NO-DATA")
        self.assertIn("does not exist", result["reason"])


class TheSyntheticFixtureProvesTheComputation(unittest.TestCase):
    def test_the_fixture_is_labeled_as_a_fixture_not_a_real_run(self):
        import json
        with open(os.path.join(FIXTURE_DIR, "preservation.json"),
                   encoding="utf-8") as fh:
            body = json.load(fh)
        self.assertIn("_NOTE", body)
        self.assertIn("fixture", body["_NOTE"].lower())

    def test_the_fixture_computes_a_real_duration_with_all_four_checks_passing(self):
        result = rb.evaluate_run(FIXTURE_DIR)
        self.assertEqual(result["verdict"], "90.0 min over 2 units")
        self.assertAlmostEqual(result["minutes"], 90.0, places=3)
        self.assertEqual(result["units"], 2)
        self.assertTrue(result["all_preserved"])
        for name in ("false_greens", "scope_drift", "unrecoverable_state",
                     "repeated_mistakes"):
            check = result["checks"][name]
            self.assertTrue(check["measured"], "%s not measured" % name)
            self.assertTrue(check["ok"], "%s not ok" % name)

    def test_the_computation_actually_moves_not_just_the_label(self):
        """Backwards-drive: strip the fixture's preservation.json signal for
        recoverability and confirm the SAME fixture then refuses, so the
        pass above is not a property of the fixture directory's mere
        existence."""
        import json
        import shutil
        import tempfile
        tmp = tempfile.mkdtemp(prefix="sut-bench-")
        try:
            copy = os.path.join(tmp, "no-recoverability-signal")
            shutil.copytree(FIXTURE_DIR, copy)
            pres_path = os.path.join(copy, "preservation.json")
            with open(pres_path, encoding="utf-8") as fh:
                body = json.load(fh)
            del body["recoverability_ok"]
            with open(pres_path, "w", encoding="utf-8") as fh:
                json.dump(body, fh)
            result = rb.evaluate_run(copy)
            self.assertEqual(result["verdict"], "NO-DATA")
            self.assertFalse(result["checks"]["unrecoverable_state"]["measured"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
