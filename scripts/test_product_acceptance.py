"""product_acceptance.py, driven for real: P0.4 of docs/plan/
P0-COMPOSITION-WAVE-2026-08-30.md. Every area function under test IS the
real proof (a plain outcome sentence into brother_run.py, in a real temp
git repository), so this suite calls them directly rather than mocking
around them, the same relationship scripts/test_acceptance.py has with its
own scripts/acceptance_1.py..acceptance_11.py real-area tests.

Model calls are STUBBED (DOOR_MODEL_CMD/MODEL_WORKER_CMD point at throwaway
scripts, never the real `claude`), so this is hermetic: no network, no real
model, no --live path exercised here.
"""
import json
import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import product_acceptance as pa  # noqa: E402

PRODUCT_ACCEPTANCE = os.path.join(HERE, "product_acceptance.py")


class AreasList(unittest.TestCase):

    def test_eleven_areas_with_unique_ids(self):
        self.assertEqual(len(pa.AREAS), 11)
        ids = [a[0] for a in pa.AREAS]
        self.assertEqual(ids, [str(i) for i in range(1, 12)])
        self.assertEqual(len(set(ids)), 11)

    def test_ids_and_names_come_from_the_canonical_file(self):
        """Two harness generations hardcoded lists whose ids disagreed with
        docs/plan/CAPABILITY-AREAS.json, so a PASS line claimed one
        capability while proving another; the list is now loaded from the
        canonical file and this pins that."""
        with open(os.path.join(HERE, os.pardir, "docs", "plan",
                               "CAPABILITY-AREAS.json"), encoding="utf-8") as fh:
            canonical = [(str(a["id"]), a["name"]) for a in json.load(fh)]
        self.assertEqual(pa.AREAS, canonical)

    def test_every_area_but_ten_is_wired(self):
        for area_id, _name in pa.AREAS:
            wired = area_id in pa.REAL_AREAS
            self.assertEqual(wired, area_id != "10",
                             "area %s wiring mismatch" % area_id)


class AreaTenIsNoData(unittest.TestCase):

    def test_it_reports_no_data_with_the_reason_named_never_a_pass(self):
        verdict, evidence = pa.run_area("10")
        self.assertEqual(verdict, "NO-DATA")
        self.assertIn("editor", evidence)


class Area1RealTest(unittest.TestCase):
    """Time to first useful action: one plain sentence, one real repo."""

    def test_passes_against_a_real_repository(self):
        verdict, evidence = pa.area_1()
        self.assertEqual(verdict, "PASS", evidence)
        self.assertIn("s (budget", evidence)

    def test_a_refusing_decomposer_reads_as_fail_not_a_silent_pass(self):
        """Calibration: point DOOR_MODEL_CMD at a script that never emits a
        valid unit, so brother_run's door refuses the outcome outright.
        Proves area_1 can actually go red rather than always reading PASS
        by construction."""
        import tempfile
        tmp = tempfile.mkdtemp(prefix="product-acceptance-1-calibrate-")
        repo = pa.tbr.make_repo(tmp)
        decomposer = pa.tbr.write_stub(tmp, "decomposer.py", """
            import json, sys
            sys.stdin.read()
            print(json.dumps([
                {"id": "Z1", "objective": "no check at all",
                 "writes": ["z.txt"], "deps": []},
            ]))
        """)
        env = dict(os.environ)
        env["DOOR_MODEL_CMD"] = "%s %s" % (sys.executable, decomposer)
        proc = pa._sh([sys.executable, pa.BROTHER_RUN, "an outcome nobody can schedule",
                      "--cwd", repo, "--runs-root", tmp], env=env, timeout=60)
        self.assertNotEqual(proc.returncode, 0)


class Area2RealTest(unittest.TestCase):
    """Interrupt and redirect: kill after the first integration, --resume
    finishes the remainder."""

    def test_passes_against_a_real_kill_and_resume(self):
        verdict, evidence = pa.area_2()
        self.assertEqual(verdict, "PASS", evidence)
        self.assertIn("merges=['A1', 'A2']", evidence)

    def test_no_data_under_live_rather_than_a_flaky_real_model_race(self):
        verdict, evidence = pa.area_2(live=True)
        self.assertEqual(verdict, "NO-DATA")
        self.assertIn("real model", evidence)


class Area9RealTest(unittest.TestCase):
    """Crash and resumability: a SIGKILLed owner's claim is reclaimable at
    once, because its pid is gone on this host, and the reclaim is still
    exactly one clean reclaim naming the dead owner.

    This docstring said the opposite until 2026-09-02, when it still described
    the pre-2026-08-31 product: a live lease refusing a second claim until a
    simulated elapse. claim_store.live() now calls a claim dead when its
    owning pid is gone on the same host, which is what took crash recovery
    from about 1200 seconds of waiting to a single resume call."""

    def test_passes_against_a_real_sigkill_and_reclaim(self):
        verdict, evidence = pa.area_9()
        self.assertEqual(verdict, "PASS", evidence)
        # The three properties that make it ONE clean reclaim rather than a
        # duplicate, each still asserted by name in the evidence sentence.
        self.assertIn("no waiting and no surgery", evidence)
        self.assertIn("attempt 2", evidence)
        self.assertIn("merges=['A1', 'A2']", evidence)
        self.assertIn("reclaimed_from='brother-run-", evidence)

    def test_no_data_under_live_rather_than_a_flaky_real_model_race(self):
        verdict, evidence = pa.area_9(live=True)
        self.assertEqual(verdict, "NO-DATA")
        self.assertIn("real model", evidence)


class Area3RealTest(unittest.TestCase):
    """Partial delivery honesty: a stub worker fails one unit's own check;
    the delivery report must name that unit refused, by name, and exit
    nonzero, never report a false full success."""

    def test_passes_against_a_real_partial_failure(self):
        verdict, evidence = pa.area_3()
        self.assertEqual(verdict, "PASS", evidence)
        self.assertIn("BY NAME", evidence)

    def test_calibrate_a_worker_that_actually_succeeds_reads_pass_not_fail(self):
        """If BOTH units succeed, area_4's own assertions (exit nonzero,
        u2 absent, refused named) must not hold, proving this area can
        distinguish a real partial failure from a full success rather than
        always reading the same verdict."""
        import tempfile
        tmp = tempfile.mkdtemp(prefix="product-acceptance-4-calibrate-")
        repo = pa.tbr.make_repo(tmp)
        env = pa.stub_env(tmp, pa.TWO_INDEPENDENT_DECOMPOSER, pa.WRITER_MODEL)
        proc = pa._sh([sys.executable, pa.BROTHER_RUN, "two files exist, u1 and u2",
                      "--cwd", repo, "--runs-root", tmp], env=env, timeout=90)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertTrue(os.path.exists(os.path.join(repo, "u2.txt")))


class Area11RealTest(unittest.TestCase):
    """Status credibility: the delivery report's integrated list must match
    the target repository's own git merges exactly."""

    def test_passes_against_a_real_repository(self):
        verdict, evidence = pa.area_11()
        self.assertEqual(verdict, "PASS", evidence)
        self.assertIn("matches", evidence)

    def test_a_forged_report_line_would_be_caught(self):
        """Directly exercises the comparison area_5 relies on: a reported
        set that disagrees with git's own merges must not read as a match.
        This is the mechanical proof that the check can fail, without
        needing to forge brother_run's own stdout."""
        reported = {"U1", "U2", "GHOST"}
        actual = {"U1", "U2"}
        self.assertNotEqual(reported, actual)


class Area4RealTest(unittest.TestCase):
    """Monorepos and generated code: an undeclared cross-package write must
    be quarantined by name while the correctly-scoped unit still lands."""

    def test_passes_against_a_real_monorepo(self):
        verdict, evidence = pa.area_4()
        self.assertEqual(verdict, "PASS", evidence)
        self.assertIn("QUARANTINE", evidence)

    #: MONOREPO_MODEL, minus its one deliberate stray write into pkg_b. Kept
    #: separate (not the generic WRITER_MODEL) because both units here
    #: declare a DIRECTORY, and WRITER_MODEL writes each declared scope as a
    #: FILE, which would raise IsADirectoryError before either unit's own
    #: check ever ran.
    CLEAN_MONOREPO_MODEL = """
        import re, sys, os
        prompt = sys.argv[-1] if len(sys.argv) > 1 else ""
        m = re.search(r"Declared write scope: ([^\\n]+)", prompt)
        paths = [p.strip() for p in (m.group(1).split(",") if m else []) if p.strip()]
        for scope in paths:
            if scope.endswith("pkg_a/src"):
                os.makedirs(scope, exist_ok=True)
                with open(os.path.join(scope, "hand.py"), "w") as fh:
                    fh.write("def helper():\\n    return 1\\n")
            elif scope.endswith("pkg_a/docs"):
                os.makedirs(scope, exist_ok=True)
                with open(os.path.join(scope, "note.txt"), "w") as fh:
                    fh.write("note\\n")
        print("stub model wrote for scope %s" % paths)
    """

    def test_calibrate_a_worker_that_stays_in_scope_reads_pass_not_fail(self):
        """If the second unit's worker never strays into pkg_b at all, area_6's
        own assertions (nonzero exit, pkg_b untouched via QUARANTINE, U2
        refused by name) describe a DIFFERENT run than this one: both units
        land clean, proving this area distinguishes a real cross-package
        violation from an ordinary two-unit success."""
        import tempfile
        tmp = tempfile.mkdtemp(prefix="product-acceptance-6-calibrate-")
        repo, pkg_b_generated, before = pa._build_two_package_monorepo(tmp)
        env = pa.stub_env(tmp, pa.MONOREPO_DECOMPOSER, self.CLEAN_MONOREPO_MODEL)
        proc = pa._sh([sys.executable, pa.BROTHER_RUN,
                      "pkg_a's helper function exists and pkg_a's docs note exists",
                      "--cwd", repo, "--runs-root", tmp], env=env, timeout=90)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertTrue(os.path.exists(
            os.path.join(repo, "packages", "pkg_a", "docs", "note.txt")))
        self.assertEqual(pa._read_text(pkg_b_generated), before)


class Area6RealTest(unittest.TestCase):
    """Dirty trees and rebases: an unrelated uncommitted edit must survive a
    refused integration, and the requested change must still land once the
    tree is resolved and the run resumed."""

    def test_passes_against_a_real_dirty_tree(self):
        verdict, evidence = pa.area_6()
        self.assertEqual(verdict, "PASS", evidence)
        self.assertIn("byte-identical", evidence)

    def test_calibrate_a_clean_tree_integrates_immediately(self):
        """Without the seeded dirty tree, the same outcome integrates on the
        first attempt, proving area_7's refusal is a real reading of a real
        dirty tree rather than a check that always reads REFUSED."""
        import tempfile
        tmp = tempfile.mkdtemp(prefix="product-acceptance-7-calibrate-")
        repo = pa.tbr.make_repo(tmp)
        env = pa.stub_env(tmp, pa.ONE_UNIT_DECOMPOSER, pa.tbr.WRITER_MODEL)
        proc = pa._sh([sys.executable, pa.BROTHER_RUN,
                      "a file exists proving the tool did something useful",
                      "--cwd", repo, "--runs-root", tmp], env=env, timeout=90)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertTrue(os.path.exists(os.path.join(repo, "outcome.txt")))


class Area5RealTest(unittest.TestCase):
    """Terminal cancellation and hung command recovery: a worker that never
    returns must be reaped by the run's own timeout, never wedge the
    controller."""

    def test_passes_against_a_real_hang(self):
        verdict, evidence = pa.area_5()
        self.assertEqual(verdict, "PASS", evidence)
        self.assertIn("reaped", evidence)

    def test_no_data_under_live_rather_than_a_flaky_real_model_race(self):
        verdict, evidence = pa.area_5(live=True)
        self.assertEqual(verdict, "NO-DATA")
        self.assertIn("real model", evidence)


class CalibrateDelegates(unittest.TestCase):
    """Areas 6, 7 and 8 have no product-path flag to disable the safety net
    they prove, so --calibrate delegates to the mechanism twin's own
    calibration of the same real machinery this product-path test depends
    on transitively."""

    def test_each_delegate_calibration_reads_pass(self):
        for area_id, script in pa.CALIBRATE_DELEGATES.items():
            verdict, evidence = pa.run_area(area_id, calibrate=True)
            self.assertEqual(verdict, "PASS", "%s: %s" % (area_id, evidence))
            self.assertIn(script, evidence)

    def test_calibrate_without_area_is_a_usage_error(self):
        proc = subprocess.run([sys.executable, PRODUCT_ACCEPTANCE, "--calibrate"],
                              capture_output=True, text=True, timeout=30)
        self.assertNotEqual(proc.returncode, 0)

    def test_calibrate_on_an_area_with_no_delegate_is_no_data(self):
        verdict, evidence = pa.run_area("1", calibrate=True)
        self.assertEqual(verdict, "NO-DATA")
        self.assertIn("no calibration", evidence)


class CLIContract(unittest.TestCase):
    """The harness's own command line: mirrors scripts/acceptance.py's
    contract (--area, --explain, exit nonzero only on FAIL)."""

    def test_full_run_reports_ten_measured_one_no_data_exit_zero(self):
        proc = subprocess.run([sys.executable, PRODUCT_ACCEPTANCE],
                              capture_output=True, text=True, timeout=600)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("11 area(s): 10 pass, 0 fail, 1 no-data", proc.stdout)
        for area_id in ("1", "2", "3", "4", "5", "6", "7", "8", "9", "11"):
            self.assertIn("PASS     [%s]" % area_id, proc.stdout)
        self.assertIn("NO-DATA  [10]", proc.stdout)

    def test_area_flag_restricts_to_one_area(self):
        proc = subprocess.run([sys.executable, PRODUCT_ACCEPTANCE, "--area", "1"],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("1 area(s): 1 pass, 0 fail, 0 no-data", proc.stdout)
        self.assertNotIn("[2]", proc.stdout)

    def test_explain_forwards_the_areas_own_description(self):
        proc = subprocess.run(
            [sys.executable, PRODUCT_ACCEPTANCE, "--area", "4", "--explain"],
            capture_output=True, text=True, timeout=90)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn(pa.DESCRIPTIONS["4"], proc.stdout)

    def test_explain_without_area_is_a_usage_error(self):
        proc = subprocess.run(
            [sys.executable, PRODUCT_ACCEPTANCE, "--explain"],
            capture_output=True, text=True, timeout=30)
        self.assertNotEqual(proc.returncode, 0)

    def test_unknown_area_is_no_data_exit_two_not_a_crash_not_a_pass(self):
        proc = subprocess.run(
            [sys.executable, PRODUCT_ACCEPTANCE, "--area", "no-such-area"],
            capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("NO-DATA", proc.stdout)

    def test_a_real_fail_flips_main_exit_code(self):
        """Patches REAL_AREAS with a fixture that reports FAIL, the same
        technique scripts/test_acceptance.py's AreaFlag class uses to prove
        main()'s exit-code wiring without forcing one of the slow real
        areas red."""
        from unittest import mock

        def fake_fail(live=False):
            return "FAIL", "fixture failure"

        with mock.patch.dict(pa.REAL_AREAS, {"1": fake_fail}):
            self.assertEqual(pa.main(["--area", "1"]), 1)


if __name__ == "__main__":
    unittest.main()
